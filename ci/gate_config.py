"""Read the shared gate command map and reject incomplete definitions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class GateNotImplemented(ValueError):
    """Raised when a configured gate is intentionally pending."""


def gate_command(config_path: Path, name: str) -> tuple[str, ...]:
    try:
        gates = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid gate configuration: {error}") from error
    if not isinstance(gates, dict):
        raise TypeError("gate configuration must be an object")
    gate = gates.get(name)
    if gate is None:
        raise ValueError(f"Gate {name} is unknown")
    if not isinstance(gate, dict):
        raise TypeError(f"Gate {name} has an invalid configuration")
    if gate.get("implemented") is not True:
        reason = gate.get("reason", "implementation is pending")
        raise GateNotImplemented(f"Gate {name} is not implemented: {reason}")
    command = gate.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item and "\n" not in item for item in command)
    ):
        raise ValueError(f"Gate {name} has no valid command")
    return tuple(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("gate")
    arguments = parser.parse_args(argv)
    try:
        command = gate_command(arguments.config, arguments.gate)
    except GateNotImplemented as error:
        print(error, file=sys.stderr)
        return 125
    except (ValueError, TypeError) as error:
        print(error, file=sys.stderr)
        return 2
    print("\n".join(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
