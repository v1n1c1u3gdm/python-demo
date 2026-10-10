"""Validate required Woodpecker control-plane settings without exposing credentials."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

REQUIRED_SECRETS = (
    "WOODPECKER_GITHUB_CLIENT",
    "WOODPECKER_GITHUB_SECRET",
    "WOODPECKER_AGENT_SECRET",
)
PLACEHOLDERS = {"change-me", "changeme", "replace-me", "replace_me", "your-secret"}


class ControlConfigError(ValueError):
    """Raised when the control plane lacks safe required configuration."""


@dataclass(frozen=True)
class ControlConfig:
    host: str
    maintainer: str


def load_control_config(environment: Mapping[str, str]) -> ControlConfig:
    """Validate required secrets and public routing configuration, returning no secrets."""
    missing = [name for name in REQUIRED_SECRETS if not environment.get(name, "").strip()]
    if missing:
        raise ControlConfigError(f"required control-plane settings are missing: {', '.join(missing)}")
    for name in REQUIRED_SECRETS:
        value = environment[name].strip()
        if value.casefold() in PLACEHOLDERS:
            raise ControlConfigError(f"{name} contains a placeholder credential")

    host = environment.get("WOODPECKER_HOST", "").strip()
    parsed = urlsplit(host)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path.rstrip("/") != "/ci":
        raise ControlConfigError("WOODPECKER_HOST must be an https URL under /ci")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ControlConfigError("WOODPECKER_HOST must not contain credentials, query, or fragment")

    maintainer = environment.get("WOODPECKER_ADMIN", "").strip()
    if not maintainer or "," in maintainer:
        raise ControlConfigError("WOODPECKER_ADMIN must name exactly one maintainer login")
    return ControlConfig(host=host.rstrip("/"), maintainer=maintainer)
