"""Write atomic per-gate execution records without following symlinks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
GATE_STATES = {"running", "passed", "failed", "incomplete", "cancelled", "timeout"}


def _safe_path(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("status paths must not contain parent traversal")
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink is not allowed in status path: {current}")
    if not absolute.parent.is_dir():
        raise ValueError("status directory must already exist")
    return absolute


def write_gate_status(
    path: Path,
    *,
    gate: str,
    commit: str,
    run_id: str,
    status: str,
    exit_code: int,
    started_at: str,
    finished_at: str | None = None,
) -> None:
    """Atomically persist a validated status record for one gate invocation."""
    destination = _safe_path(Path(path))
    if not all(TOKEN.fullmatch(value) for value in (gate, commit, run_id)):
        raise ValueError("gate and execution identity must be simple tokens")
    if status not in GATE_STATES:
        raise ValueError(f"invalid gate status: {status}")
    if type(exit_code) is not int:
        raise TypeError("gate exit code must be an integer")
    if not started_at or (status == "passed" and exit_code != 0):
        raise ValueError("gate status has invalid timestamps or exit code")
    if status != "running" and not finished_at:
        raise ValueError("terminal gate status requires a finish time")
    record = {
        "schema": 1,
        "gate": gate,
        "commit": commit,
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "started_at": started_at,
    }
    if finished_at:
        record["finished_at"] = finished_at
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as temporary:
        json.dump(record, temporary, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o644)
    os.replace(temporary_path, destination)


def mark_gate_conflict(path: Path, gate: str) -> bool:
    """Create a collision marker once without following or replacing a link."""
    destination = _safe_path(Path(path))
    if not TOKEN.fullmatch(gate):
        raise ValueError("gate name must be a simple token")
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(f"gate output identity reused: {gate}\n")
    os.chmod(destination, 0o644)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    write = commands.add_parser("write")
    write.add_argument("path", type=Path)
    write.add_argument("gate")
    write.add_argument("commit")
    write.add_argument("run_id")
    write.add_argument("status", choices=sorted(GATE_STATES))
    write.add_argument("exit_code", type=int)
    write.add_argument("started_at")
    write.add_argument("finished_at", nargs="?")
    conflict = commands.add_parser("conflict")
    conflict.add_argument("path", type=Path)
    conflict.add_argument("gate")
    args = parser.parse_args(argv)
    try:
        if args.operation == "conflict":
            mark_gate_conflict(args.path, args.gate)
        else:
            write_gate_status(
                args.path,
                gate=args.gate,
                commit=args.commit,
                run_id=args.run_id,
                status=args.status,
                exit_code=args.exit_code,
                started_at=args.started_at,
                finished_at=args.finished_at,
            )
    except (OSError, TypeError, ValueError) as error:
        print(f"Cannot write gate status: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
