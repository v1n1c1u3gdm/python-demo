"""Download and verify the pinned CI tool archives before extraction."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ARCHIVES = {
    "node": {
        "amd64": (
            "https://nodejs.org/dist/v24.11.1/node-v24.11.1-linux-x64.tar.xz",
            "60e3b0a8500819514aca603487c254298cd776de0698d3cd08f11dba5b8289a8",
            "node-v24.11.1-linux-x64.tar.xz",
            "node-v24.11.1-linux-x64",
        ),
        "arm64": (
            "https://nodejs.org/dist/v24.11.1/node-v24.11.1-linux-arm64.tar.xz",
            "6b0863fb9f627bf4a6c5948dce1de4398174a2e05dbe717503d828e211ca01f0",
            "node-v24.11.1-linux-arm64.tar.xz",
            "node-v24.11.1-linux-arm64",
        ),
    },
    "gitleaks": {
        "amd64": (
            "https://github.com/gitleaks/gitleaks/releases/download/v8.30.0/gitleaks_8.30.0_linux_x64.tar.gz",
            "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e",
            "gitleaks_8.30.0_linux_x64.tar.gz",
            "gitleaks",
        ),
        "arm64": (
            "https://github.com/gitleaks/gitleaks/releases/download/v8.30.0/gitleaks_8.30.0_linux_arm64.tar.gz",
            "b4cbbb6ddf7d1b2a603088cd03a4e3f7ce48ee7fd449b51f7de6ee2906f5fa2f",
            "gitleaks_8.30.0_linux_arm64.tar.gz",
            "gitleaks",
        ),
    },
    "uv": {
        "amd64": (
            "https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-unknown-linux-gnu.tar.gz",
            "6590717592ace991ff83a63fef799e3ad9d33ecc8f96c5d6bdd732496e79337f",
            "uv-x86_64-unknown-linux-gnu.tar.gz",
            "uv-x86_64-unknown-linux-gnu/uv",
        ),
        "arm64": (
            "https://github.com/astral-sh/uv/releases/download/0.12.20/uv-aarch64-unknown-linux-gnu.tar.gz",
            "8a7aad7bc76a2fae5151566ff3e43eacce0b2a113d5e4de3e4afe3e58fa2441e",
            "uv-aarch64-unknown-linux-gnu.tar.gz",
            "uv-aarch64-unknown-linux-gnu/uv",
        ),
    },
}


def architecture_key(machine: str) -> str:
    try:
        return {"x86_64": "amd64", "aarch64": "arm64"}[machine]
    except KeyError as error:
        raise ValueError(f"unsupported CI architecture: {machine}") from error


def verify_archive(archive: Path, expected_sha256: str) -> bool:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"SHA-256 verification failed for {archive.name}")
    return True


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "python-demo-ci-bootstrap/1"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _extract_file(archive: Path, member_name: str, destination: Path) -> None:
    member_path = PurePosixPath(member_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"unsafe archive member: {member_name}")
    with tarfile.open(archive, "r:*") as source:
        member = source.getmember(member_name)
        if not member.isfile():
            raise ValueError(f"expected regular archive file: {member_name}")
        stream = source.extractfile(member)
        if stream is None:
            raise ValueError(f"archive member is unreadable: {member_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with stream, destination.open("wb") as output:
            shutil.copyfileobj(stream, output)
    destination.chmod(0o755)


def extract_node_archive(archive: Path, directory_name: str, destination: Path) -> Path:
    parent = PurePosixPath(directory_name)
    if parent.is_absolute() or len(parent.parts) != 1 or parent.name in {".", ".."}:
        raise ValueError(f"unsafe Node archive directory: {directory_name}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:xz") as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe Node archive path: {member.name}")
        source.extractall(destination, filter="data")
    return destination / directory_name


def install_tools(destination: Path, machine: str | None = None) -> Path:
    architecture = architecture_key(machine or platform.machine())
    destination.mkdir(parents=True, exist_ok=True)
    bin_dir = destination / "bin"
    required_tools = (bin_dir / "node", bin_dir / "npm", bin_dir / "npx", bin_dir / "gitleaks", bin_dir / "uv")
    if all(tool.exists() for tool in required_tools):
        return bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool, options in ARCHIVES.items():
        url, digest, filename, member = options[architecture]
        archive = destination / filename
        _download(url, archive)
        verify_archive(archive, digest)
        if tool == "node":
            node_dir = extract_node_archive(archive, member, destination)
            for executable in ("node", "npm", "npx"):
                (bin_dir / executable).symlink_to(node_dir / "bin" / executable)
        else:
            _extract_file(archive, member, bin_dir / tool)
        archive.unlink()
    return bin_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    bin_dir = install_tools(arguments.install_dir)
    print(f"Verified CI tools installed in {bin_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
