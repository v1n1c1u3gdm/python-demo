"""Register and persist the local Woodpecker OAuth application in Gitea."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ci.local_bootstrap import _assert_no_symlink_components
from ci.local_http import LocalHTTP

GITEA_URL = "https://app.localhost/git"
CALLBACK = "https://app.localhost/ci/authorize"
WOODPECKER_GID = 10001


def _atomic(path: Path, payload: bytes, mode: int = 0o600) -> None:
    _assert_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_no_symlink_components(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".woodpecker-oauth-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_secret(path: Path) -> str:
    _assert_no_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Gitea bootstrap API token is unavailable.")
    value = path.read_text(encoding="utf-8")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Gitea bootstrap API token is malformed.")
    return value


def bootstrap_woodpecker_oauth(state_dir: Path, secrets_dir: Path) -> None:
    """Create once, preserve the returned OAuth secret, and open CI only for its admin."""
    state_dir, secrets_dir = Path(os.path.abspath(state_dir)), Path(os.path.abspath(secrets_dir))
    generation_path = state_dir / "bootstrap-generation"
    _assert_no_symlink_components(generation_path)
    generation = generation_path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("Local bootstrap generation is unavailable or invalid.")

    token = _read_secret(secrets_dir / "gitea_bootstrap_api_token")
    output_dir = secrets_dir / "runtime" / "woodpecker"
    output_dir.chmod(0o750)
    if os.geteuid() == 0:
        os.chown(output_dir, 0, WOODPECKER_GID)
    client_path = output_dir / "gitea_client_id"
    secret_path = output_dir / "gitea_client_secret"
    canonical_client = secrets_dir / "woodpecker_gitea_client"
    canonical_secret = secrets_dir / "woodpecker_gitea_secret"
    state_path = state_dir / "woodpecker-gitea-oauth.json"
    http = LocalHTTP(GITEA_URL, secrets_dir / "runtime" / "woodpecker" / "tls" / "ca.crt", timeout=60)
    headers = {"Authorization": f"token {token}"}

    # The create endpoint returns its secret exactly once. Persist it before any
    # later operation can fail, and reuse the pair without calling POST again.
    canonical_exists = (canonical_client.exists(), canonical_secret.exists())
    if canonical_exists[0] != canonical_exists[1]:
        raise ValueError("Canonical Woodpecker OAuth credential pair is incomplete; refusing rotation.")
    # The gateway publishes /git only after the native Gitea identity marker is
    # written. Its watcher is asynchronous, so retry this readiness probe over
    # strict TLS before making any authenticated API request.
    http.request("GET", "api/healthz", retryable_statuses=(404,))
    if state_path.exists() or client_path.exists() or secret_path.exists() or canonical_exists[0]:
        if not (state_path.is_file() and client_path.is_file() and secret_path.is_file()
                and canonical_client.is_file() and canonical_secret.is_file()):
            raise ValueError("Persisted Woodpecker OAuth client is incomplete; refusing rotation.")
        for path in (state_path, client_path, secret_path, canonical_client, canonical_secret):
            _assert_no_symlink_components(path)
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        client_id = client_path.read_text(encoding="utf-8")
        client_secret = secret_path.read_text(encoding="utf-8")
        if (canonical_client.read_text(encoding="utf-8") != client_id
                or canonical_secret.read_text(encoding="utf-8") != client_secret):
            raise ValueError("Woodpecker derived runtime copy differs from canonical OAuth credentials.")
        if (stored.get("callback") != CALLBACK or stored.get("client_id") != client_id
                or not client_id or not client_secret or "\n" in client_secret or "\r" in client_secret
                ):
            raise ValueError("Persisted Woodpecker OAuth client is invalid; refusing rotation.")
    else:
        admin = http.request("GET", "api/v1/user", headers=headers)
        if admin.get("login") != "admin" or admin.get("is_admin") is not True:
            raise ValueError("Gitea bootstrap token is not the verified local administrator.")
        created = http.request("POST", "api/v1/user/applications/oauth2", headers=headers, json={
            "name": "Woodpecker Local",
            "confidential_client": True,
            "redirect_uris": [CALLBACK],
            "skip_secondary_authorization": False,
        }, expected_statuses=(201,))
        client_id, client_secret = created.get("client_id", ""), created.get("client_secret", "")
        if not client_id or not client_secret:
            raise ValueError("Gitea did not return a complete Woodpecker OAuth client.")
        _atomic(canonical_client, client_id.encode())
        _atomic(canonical_secret, client_secret.encode())
        _atomic(client_path, client_id.encode(), 0o640)
        _atomic(secret_path, client_secret.encode(), 0o640)
        if os.geteuid() == 0:
            os.chown(client_path, 0, WOODPECKER_GID)
            os.chown(secret_path, 0, WOODPECKER_GID)
        _atomic(state_path, json.dumps({"callback": CALLBACK, "client_id": client_id,
                                        "generation": generation}, sort_keys=True).encode())

    # Verify the persisted forge administrator on every run before publishing
    # a generation-only marker consumed by the gateway watcher.
    admin = http.request("GET", "api/v1/user", headers=headers)
    if admin.get("login") != "admin" or admin.get("is_admin") is not True:
        raise ValueError("Gitea bootstrap token is not the verified local administrator.")


def publish_ci_ready(state_dir: Path) -> None:
    """Publish the current route generation after Compose has observed CI health."""
    state_dir = Path(os.path.abspath(state_dir))
    generation_path = state_dir / "bootstrap-generation"
    _assert_no_symlink_components(generation_path)
    generation = generation_path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("Local bootstrap generation is unavailable or invalid.")
    ready_dir = state_dir / "ready"
    _assert_no_symlink_components(ready_dir)
    ready_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = ready_dir / "ci"
    _atomic(marker, generation.encode(), 0o600)


def main() -> None:
    """Run OAuth provisioning or publish CI readiness for the Compose init."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("/run/local-state"))
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/local-secrets"))
    parser.add_argument("--publish-ready", action="store_true")
    args = parser.parse_args()
    if args.publish_ready:
        publish_ci_ready(args.state_dir)
    else:
        bootstrap_woodpecker_oauth(args.state_dir, args.secrets_dir)


if __name__ == "__main__":
    main()
