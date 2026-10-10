"""Fail-closed validation for a read-only Woodpecker repo/account policy snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class PolicyError(ValueError):
    """Raised when the instance or repository policy drifts from accepted controls."""


def validate_policy(snapshot: Any, maintainer: str) -> None:
    """Reject policy snapshots that bypass PR approval or allow unexpected CI users."""
    if not isinstance(snapshot, dict):
        raise PolicyError("policy snapshot must be an object")
    repository = snapshot.get("repository")
    if not isinstance(repository, dict):
        raise PolicyError("repository settings are missing")
    if repository.get("require_approval") != "all_events":
        raise PolicyError("repository must require approval for all_events")
    if repository.get("approval_allowed_users") != []:
        raise PolicyError("approval_allowed_users must be empty; it is an author bypass")
    trusted = repository.get("trusted")
    if not isinstance(trusted, dict) or set(trusted) != {"network", "volumes", "security"}:
        raise PolicyError("repository trusted settings must use the tagged API object schema")
    if any(value is not False for value in trusted.values()):
        raise PolicyError("repository must remain untrusted")
    for field in ("allow_deploy", "private"):
        if repository.get(field) is not False:
            raise PolicyError(f"repository {field} must be false")
    if repository.get("netrc_trusted") != []:
        raise PolicyError("repository must not share clone credentials with steps")
    for field in ("config_extension_endpoint", "registry_extension_endpoint", "secret_extension_endpoint"):
        if repository.get(field) != "":
            raise PolicyError(f"repository {field} must be disabled")
    if snapshot.get("manual_cron_blocked") is not True:
        raise PolicyError("manual and cron routes must be blocked at the reverse proxy")

    if snapshot.get("crons") != []:
        raise PolicyError("existing cron schedules must be removed before activation")

    users = snapshot.get("users")
    if not isinstance(users, list) or len(users) != 1:
        raise PolicyError("unexpected CI accounts: exactly the maintainer account must exist")
    user = users[0]
    if not isinstance(user, dict) or user.get("login") != maintainer or user.get("admin") is not True:
        raise PolicyError("unexpected CI accounts: exactly the maintainer admin must exist")


def load_snapshot(path: Path) -> Any:
    """Read a local export of repository settings and instance accounts."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read policy snapshot: {path}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="read-only repo/account JSON export")
    parser.add_argument("--maintainer", required=True, help="the single configured Woodpecker admin")
    arguments = parser.parse_args(argv)
    try:
        validate_policy(load_snapshot(arguments.snapshot), arguments.maintainer)
    except PolicyError as error:
        print(f"Woodpecker policy check failed: {error}", file=sys.stderr)
        return 1
    print("Woodpecker policy snapshot passed: single maintainer, all_events, no author bypass, untrusted repo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
