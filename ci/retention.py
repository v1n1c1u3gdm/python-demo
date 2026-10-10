"""Apply local age and byte-budget retention to completed report directories."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
GATES = ("secrets", "lint", "security", "quality", "api", "ui", "build")
TERMINAL_GATE_STATES = {"passed", "failed", "not_run", "cancelled", "timeout"}
RUN_NAME = re.compile(r"^(?P<commit>[A-Za-z0-9._]+)-(?P<run_id>[A-Za-z0-9._-]+)$")


def _size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"refusing to inspect symlink in report directory: {item}")
        if item.is_file():
            total += item.stat().st_size
    return total


def _reject_symlink_ancestors(path: Path) -> Path:
    """Return an absolute path only after checking every path component."""
    if ".." in path.parts:
        raise ValueError("retention paths must not contain parent traversal")
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlink in retention path: {current}")
    return absolute


def _completed_summary(entry: Path, marker: Path) -> bool:
    """Accept only a terminal result summary matching a recognized run path."""
    match = RUN_NAME.fullmatch(entry.name)
    if not match:
        return False
    try:
        if marker.stat().st_size > 1024 * 1024:
            return False
        result = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict) or result.get("schema") != 1:
        return False
    identity = result.get("identity")
    if identity != match.groupdict():
        return False
    status = result.get("status")
    exit_code = result.get("exit_code")
    if status not in {"passed", "failed"} or not isinstance(exit_code, int):
        return False
    if (status == "passed") != (exit_code == 0):
        return False
    gates = result.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(GATES):
        return False
    states = []
    for gate_result in gates.values():
        if not isinstance(gate_result, dict):
            return False
        gate_state = gate_result.get("status")
        if gate_state not in TERMINAL_GATE_STATES:
            return False
        states.append(gate_state)
    return not (status == "passed" and any(state != "passed" for state in states))


def _result_markers(root: Path):
    """Walk without following symlinks and refuse symlink entries outright."""
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            child = current_path / name
            if child.is_symlink():
                raise ValueError(f"refusing to inspect symlink in reports root: {child}")
        if "results.json" in files:
            yield current_path / "results.json"


def prune_reports(
    reports_root: Path,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[Path]:
    """Delete expired completed runs, then oldest completed runs over budget."""
    root = _reject_symlink_ancestors(Path(reports_root))
    if not root.is_dir():
        raise ValueError("reports root must be a real directory")
    if max_age_days < 0 or max_bytes < 0:
        raise ValueError("retention limits must not be negative")
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - timedelta(days=max_age_days).total_seconds()
    completed: list[tuple[Path, float, int]] = []
    for marker in _result_markers(root):
        entry = marker.parent
        if not entry.is_dir() or not _completed_summary(entry, marker):
            continue
        size = _size(entry)
        completed.append((entry, entry.stat().st_mtime, size))
    removed: list[Path] = []
    kept = []
    for entry, modified, size in completed:
        if modified < cutoff:
            shutil.rmtree(entry)
            removed.append(entry)
            _remove_empty_parents(entry.parent, root)
        else:
            kept.append((entry, modified, size))
    total = sum(size for _, _, size in kept)
    for entry, _, size in sorted(kept, key=lambda item: item[1]):
        if total <= max_bytes:
            break
        shutil.rmtree(entry)
        removed.append(entry)
        total -= size
        _remove_empty_parents(entry.parent, root)
    return removed


def _remove_empty_parents(path: Path, root: Path) -> None:
    """Remove only empty report containers between a completed run and root."""
    current = path
    while current != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_root", type=Path)
    args = parser.parse_args(argv)
    try:
        for removed in prune_reports(args.reports_root):
            print(f"Removed completed CI reports: {removed}")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
