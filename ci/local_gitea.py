"""Validated OIDC source intent for the native Gitea image."""

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
    descriptor, temporary = tempfile.mkstemp(prefix=".gitea-sso-", dir=path.parent)
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
        raise ValueError("Required Gitea OIDC secret is unavailable.")
    value = path.read_text(encoding="utf-8")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Required Gitea OIDC secret is malformed.")
    return value


def prepare_gitea_sso(identity: dict[str, str], state_dir: Path,
                      secrets_dir: Path) -> dict[str, str]:
    """Persist a stable OIDC source and explicit Keycloak subject association intent."""
    if (identity.get("username") != "admin" or identity.get("issuer") != LOCAL_ISSUER
            or not _SUBJECT.fullmatch(identity.get("subject", ""))):
        raise ValueError("Keycloak administrator identity is incomplete or invalid.")
    state_dir, secrets_dir = Path(os.path.abspath(state_dir)), Path(os.path.abspath(secrets_dir))
    generation_path = state_dir / "bootstrap-generation"
    _assert_no_symlink_components(generation_path)
    generation = generation_path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("Local bootstrap generation is unavailable or invalid.")
    client_secret = _secret(secrets_dir, "gitea_client_secret")
    subject = identity["subject"]
    state_path = state_dir / "gitea-oidc.json"
    if state_path.exists():
        _assert_no_symlink_components(state_path)
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if previous.get("subject") != subject:
            raise ValueError("Gitea administrator subject changed; refusing implicit relinking.")
    result = {"generation": generation, "issuer": LOCAL_ISSUER, "source_name": "keycloak",
              "subject": subject, "username": "admin", "admin_group": "local-admins",
              "group_claim": "groups"}
    _atomic(state_path, json.dumps(result, sort_keys=True).encode())
    native_intent = f"generation={generation}\nissuer={LOCAL_ISSUER}\nsubject={subject}\n"
    _atomic(state_dir / "gitea-oidc.env", native_intent.encode())
    return {
        "GITEA_OIDC_ISSUER": LOCAL_ISSUER,
        "GITEA_OIDC_CLIENT_ID": "gitea",
        "GITEA_OIDC_CLIENT_SECRET": client_secret,
        "GITEA_OIDC_SOURCE_NAME": "keycloak",
        "GITEA_OIDC_SUBJECT": subject,
        "GITEA_OIDC_GROUP_CLAIM": "groups",
        "GITEA_OIDC_ADMIN_GROUP": "local-admins",
        "GITEA_OIDC_SCOPES": "openid,email,profile",
        "GITEA_OIDC_DISCOVERY_URL": f"{LOCAL_ISSUER}/.well-known/openid-configuration",
    }


def main() -> None:
    """Prepare the native Gitea intent from the Keycloak init artifact."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("/run/local-state"))
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/local-secrets"))
    args = parser.parse_args()
    identity = json.loads((args.state_dir / "keycloak-admin.json").read_text(encoding="utf-8"))
    prepare_gitea_sso(identity, args.state_dir, args.secrets_dir)


if __name__ == "__main__":
    main()
