"""Validated OIDC intent for the native BookStack bootstrap container."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ci.local_bootstrap import _assert_no_symlink_components

LOCAL_ISSUER = "https://app.localhost/auth/realms/python-demo"
_SUBJECT = re.compile(r"[A-Za-z0-9._:-]{1,255}\Z")


def _atomic(path: Path, payload: bytes) -> None:
    _assert_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".bookstack-sso-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _secret(secrets_dir: Path, name: str) -> str:
    path = secrets_dir / name
    _assert_no_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required BookStack OIDC secret is unavailable.")
    value = path.read_text(encoding="utf-8")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Required BookStack OIDC secret is malformed.")
    return value


def prepare_bookstack_sso(identity: dict[str, str], state_dir: Path,
                          secrets_dir: Path) -> dict[str, str]:
    """Persist the explicit Keycloak subject and return native BookStack settings."""
    if (identity.get("username") != "admin" or identity.get("issuer") != LOCAL_ISSUER
            or not _SUBJECT.fullmatch(identity.get("subject", ""))):
        raise ValueError("Keycloak administrator identity is incomplete or invalid.")
    state_dir, secrets_dir = Path(os.path.abspath(state_dir)), Path(os.path.abspath(secrets_dir))
    generation_path = state_dir / "bootstrap-generation"
    _assert_no_symlink_components(generation_path)
    generation = generation_path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("Local bootstrap generation is unavailable or invalid.")
    client_secret = _secret(secrets_dir, "bookstack_client_secret")
    subject = identity["subject"]
    prior_path = state_dir / "bookstack-oidc.json"
    if prior_path.exists():
        _assert_no_symlink_components(prior_path)
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        if prior.get("subject") != subject:
            raise ValueError("BookStack administrator subject changed; refusing implicit relinking.")
    _atomic(prior_path, json.dumps({"generation": generation, "subject": subject,
                                    "username": "admin"}, sort_keys=True).encode())
    return {
        "APP_URL": "https://app.localhost/bookstack",
        "AUTH_METHOD": "oidc",
        "AUTH_AUTO_INITIATE": "false",
        "OIDC_NAME": "Keycloak",
        "OIDC_CLIENT_ID": "bookstack",
        "OIDC_CLIENT_SECRET": client_secret,
        "OIDC_ISSUER": LOCAL_ISSUER,
        "OIDC_ISSUER_DISCOVER": "false",
        "OIDC_AUTH_ENDPOINT": f"{LOCAL_ISSUER}/protocol/openid-connect/auth",
        "OIDC_TOKEN_ENDPOINT": "https://gateway:8443/auth/realms/python-demo/protocol/openid-connect/token",
        "OIDC_USERINFO_ENDPOINT": "https://gateway:8443/auth/realms/python-demo/protocol/openid-connect/userinfo",
        "OIDC_PUBLIC_KEY": "file:///run/local-public/bookstack-id-token.pem",
        "OIDC_EXTERNAL_ID_CLAIM": "sub",
        "OIDC_USER_TO_GROUPS": "true",
        "OIDC_GROUPS_CLAIM": "groups",
        "OIDC_ADDITIONAL_SCOPES": "openid,profile,email",
        "OIDC_REMOVE_FROM_GROUPS": "true",
        "OIDC_DUMP_USER_DETAILS": "false",
        "BOOKSTACK_ADMIN_SUBJECT": subject,
        "BOOKSTACK_ADMIN_GROUP": "local-admins",
    }


def main() -> None:
    """Prepare the native BookStack intent from the Keycloak init artifact."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("/run/local-state"))
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/local-secrets"))
    args = parser.parse_args()
    identity = json.loads((args.state_dir / "keycloak-admin.json").read_text(encoding="utf-8"))
    prepare_bookstack_sso(identity, args.state_dir, args.secrets_dir)


if __name__ == "__main__":
    main()
