"""Prepare only the persistent Woodpecker exporter cursor for its non-root runtime."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

EXPORTER_UID = 65532
EXPORTER_GID = 65532


class StatePreparationError(ValueError):
    """Raised when the mounted cursor volume is not a safe state directory."""


def prepare_state_directory(directory: Path) -> None:
    """Migrate an existing cursor's ownership without deleting or following links."""
    state = directory / "state.json"
    if directory.is_symlink() or not directory.is_dir():
        raise StatePreparationError("exporter state volume must be a real directory")
    os.chown(directory, 0, 0, follow_symlinks=False)
    try:
        os.chmod(directory, 0o700)
        if state.is_symlink() or (state.exists() and not state.is_file()):
            raise StatePreparationError("exporter state must be a regular file")
        if state.exists():
            os.chown(state, EXPORTER_UID, EXPORTER_GID, follow_symlinks=False)
            os.chmod(state, 0o600)
    finally:
        os.chown(directory, EXPORTER_UID, EXPORTER_GID, follow_symlinks=False)
        os.chmod(directory, 0o700)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/var/lib/ci-log-exporter"))
    arguments = parser.parse_args(argv)
    try:
        prepare_state_directory(arguments.directory)
    except (OSError, StatePreparationError) as error:
        print(f"Exporter state volume could not be prepared: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
