"""Prepare an inactive nftables rules file after host capability checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

CompletedProcess = subprocess.CompletedProcess
_BRIDGE_LINE = re.compile(r"^\d+: ([^:@]+)(?:@[^:]+)?:")
_REQUIRED_TOOLS = ("ip", "nft", "unshare")
COMMAND_TIMEOUT_SECONDS = 15


class SafeOutputTarget:
    """Pin a symlink-free output directory chain and commit relative to its fd."""

    def __init__(self, path: Path):
        original_path = Path(path)
        if not original_path.is_absolute():
            raise ValueError("output path must be absolute")
        self.path = Path(os.path.abspath(original_path))
        if not self.path.name:
            raise ValueError("output path must name a file")
        self.name = self.path.name
        self._fds = [os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)]
        self._links = []
        try:
            parent_fd = self._fds[0]
            for component in self.path.parent.parts[1:]:
                try:
                    child_fd = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                except OSError as error:
                    raise ValueError(
                        f"output path has a symlink or non-directory ancestor: {component}"
                    ) from error
                self._fds.append(child_fd)
                self._links.append((parent_fd, component, child_fd))
                parent_fd = child_fd
            self.parent_fd = parent_fd
            self._initial_destination = self._destination_identity()
            self.validate()
        except BaseException:
            self.close()
            raise

    def _destination_identity(self):
        try:
            info = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("output path is a symlink; choose a regular file path")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("output path exists and is not a regular file")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def validate(self) -> None:
        """Confirm every path component still names the pinned directory."""
        for parent_fd, component, child_fd in self._links:
            try:
                named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                pinned = os.fstat(child_fd)
            except OSError as error:
                raise ValueError("output directory changed during preparation") from error
            if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (
                pinned.st_dev,
                pinned.st_ino,
            ):
                raise ValueError("output directory changed during preparation")
        if self._destination_identity() != self._initial_destination:
            raise ValueError("output file changed during preparation; refusing to replace it")

    def close(self) -> None:
        """Close pinned directory descriptors after preparation completes."""
        for descriptor in reversed(getattr(self, "_fds", [])):
            os.close(descriptor)
        self._fds = []


def _bridge_names(output: str) -> list[str]:
    names = []
    for line in output.splitlines():
        match = _BRIDGE_LINE.match(line)
        if match:
            names.append(match.group(1))
    return names


def _load_inventory(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("inventory must be an existing regular file")
        return json.load(stream)


def _run_checked(runner, command: list[str], *, input_text: str | None = None):
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            input=input_text,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"{command[0]} timed out after {COMMAND_TIMEOUT_SECONDS} seconds; "
            "no firewall file was written. Check host tool and namespace support, then retry."
        ) from error
    except OSError as error:
        raise RuntimeError(f"could not execute {command[0]}: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "no diagnostic provided").strip()
        raise RuntimeError(f"{command[0]} failed: {detail}")
    return result


def _write_private_atomic(target: SafeOutputTarget, content: str) -> None:
    target.validate()
    descriptor = None
    temporary_name = None
    for _ in range(10):
        candidate = f".{target.name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=target.parent_fd,
            )
            temporary_name = candidate
            break
        except FileExistsError:
            continue
    if descriptor is None or temporary_name is None:
        raise OSError("could not allocate a private temporary output file")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        target.validate()
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
        )
        temporary_name = None
        try:
            os.fsync(target.parent_fd)
        except OSError as error:
            print(
                "Warning: output was replaced, but directory fsync failed; "
                f"the rename may not survive a sudden power loss: {error}",
                file=sys.stderr,
            )
    finally:
        if temporary_name is not None:
            os.unlink(temporary_name, dir_fd=target.parent_fd)


def prepare(config_path: Path, output_path: Path, *, runner=subprocess.run) -> int:
    """Prepare validated firewall rules without applying them to the host."""
    from ci.firewall_policy import (
        FirewallPolicyError,
        load_policy,
        render_policy,
        validate_bridges,
    )

    target = None
    try:
        config_path = Path(config_path)
        if not config_path.is_absolute():
            raise ValueError("inventory path must be absolute")
        config_path = Path(os.path.abspath(config_path))
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("inventory must be an existing regular file, not a symlink")
        raw_output_path = Path(output_path)
        if not raw_output_path.is_absolute():
            raise ValueError("output path must be absolute")
        output_path = Path(os.path.abspath(raw_output_path))
        if config_path == output_path:
            raise ValueError("inventory and output paths must be different")
        try:
            if not output_path.is_symlink() and os.path.samefile(config_path, output_path):
                raise ValueError("inventory and output must not reference the same file")
        except FileNotFoundError:
            pass
        target = SafeOutputTarget(output_path)
        missing = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(
                f"required tools are missing: {names}. Install iproute2, nftables, and util-linux, "
                "then rerun; this command does not install packages."
            )
        policy = load_policy(_load_inventory(config_path))
        ip_result = _run_checked(runner, ["ip", "-o", "link", "show", "type", "bridge"])
        target.validate()
        bridges = _bridge_names(ip_result.stdout)
        validate_bridges(policy, bridges)
        rules = render_policy(policy)
        syntax_command = [
            "unshare", "--user", "--map-root-user", "--net", "nft", "-c", "-f", "-"
        ]
        _run_checked(runner, syntax_command, input_text=rules)
        target.validate()
        _write_private_atomic(target, rules)
    except (OSError, ValueError, RuntimeError, FirewallPolicyError) as error:
        print(f"Firewall preparation failed: {error}", file=sys.stderr)
        return 2
    finally:
        if target is not None:
            target.close()
    print(f"Prepared inactive firewall rules at {output_path}; no host rules were changed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the explicit output path and run the safe preparation flow."""
    parser = argparse.ArgumentParser(prog="ci/setup-firewall.sh", description=__doc__)
    parser.add_argument(
        "--config", required=True, type=Path,
        help="absolute path to the operator-reviewed complete inventory JSON",
    )
    parser.add_argument("--output", required=True, type=Path, help="absolute path for the private inactive rules file")
    arguments = parser.parse_args(argv)
    return prepare(arguments.config, arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
