"""Idempotently provision the local Keycloak realm, clients and administrator."""

import base64
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ci.local_bootstrap import _assert_no_symlink_components
from ci.local_http import LocalHTTP

REALM = "python-demo"
CLIENTS = {
    "python-demo-api": {"public": False, "standard": False, "direct": True, "redirectUris": [], "webOrigins": []},
    "python-demo-ui": {
        "public": True, "standard": True, "direct": False,
        "redirectUris": ["https://app.localhost/admin"], "webOrigins": ["https://app.localhost"],
        "pkceMethod": "S256", "audience": "python-demo-api",
    },
    "bookstack": {"public": False, "standard": True, "direct": False,
                   "redirectUris": ["https://app.localhost/bookstack/oidc/callback"],
                   "webOrigins": ["https://app.localhost"]},
    "gitea": {"public": False, "standard": True, "direct": False,
              "redirectUris": ["https://app.localhost/git/user/oauth2/keycloak/callback"],
              "webOrigins": ["https://app.localhost"]},
}


def _group_mapper() -> dict[str, object]:
    return {
        "name": "groups", "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper", "consentRequired": False,
        "config": {"claim.name": "groups", "full.path": "false",
                   "id.token.claim": "true", "access.token.claim": "true",
                   "userinfo.token.claim": "true"},
    }


def _api_audience_mapper() -> dict[str, object]:
    return {
        "name": "python-demo-api audience", "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper", "consentRequired": False,
        "config": {"included.client.audience": "python-demo-api", "access.token.claim": "true"},
    }


def _atomic(path: Path, payload: bytes, mode: int = 0o600) -> None:
    _assert_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_no_symlink_components(path)
    fd, temporary = tempfile.mkstemp(prefix=".keycloak-init-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _b64url_integer(value: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        number = int.from_bytes(decoded, "big")
    except (ValueError, TypeError):
        number = 0
    if number <= 0:
        raise ValueError("Keycloak did not publish a valid RSA signing key.")
    return number


def _persist_bookstack_public_key(client: LocalHTTP, state_dir: Path, issuer: str) -> None:
    discovery = client.request("GET", f"realms/{REALM}/.well-known/openid-configuration")
    expected = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "userinfo_endpoint": f"{issuer}/protocol/openid-connect/userinfo",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
    }
    if any(discovery.get(field) != value for field, value in expected.items()):
        raise ValueError("Keycloak OIDC discovery does not match the configured public issuer.")
    parsed = urlparse(discovery["jwks_uri"])
    if parsed.scheme != "https" or parsed.netloc != "app.localhost":
        raise ValueError("Keycloak JWKS endpoint is outside the local public issuer.")
    jwks = client.request("GET", f"realms/{REALM}/protocol/openid-connect/certs")
    signing_keys = [key for key in jwks.get("keys", [])
                    if key.get("kty") == "RSA" and key.get("use") == "sig" and key.get("alg") == "RS256"]
    if len(signing_keys) != 1:
        raise ValueError("Keycloak must publish exactly one active RSA/RS256 signing key for BookStack.")
    key = signing_keys[0]
    public_key = rsa.RSAPublicNumbers(_b64url_integer(key.get("e", "")),
                                      _b64url_integer(key.get("n", ""))).public_key()
    pem = public_key.public_bytes(serialization.Encoding.PEM,
                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    public_dir = state_dir / "public"
    _assert_no_symlink_components(public_dir)
    if not public_dir.is_dir():
        raise ValueError("Local public state directory is unavailable.")
    _atomic(public_dir / "bookstack-id-token.pem", pem, mode=0o644)


def _secret(secrets_dir: Path, name: str) -> str:
    path = secrets_dir / name
    _assert_no_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required local Keycloak secret is unavailable or invalid.")
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("Required local Keycloak secret is unavailable or invalid.") from None
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Required local Keycloak secret is malformed.")
    return value


def _ensure_groups(client: LocalHTTP, headers: dict[str, str]) -> list[str]:
    group_ids = []
    for name in ("local-admins", "Admin"):
        groups = client.request("GET", f"admin/realms/{REALM}/groups", headers=headers)
        found = next((item for item in groups if item.get("name") == name), None)
        if not found:
            client.request("POST", f"admin/realms/{REALM}/groups", json={"name": name},
                           headers=headers, expected_statuses=(201, 409))
            groups = client.request("GET", f"admin/realms/{REALM}/groups", headers=headers)
            found = next((item for item in groups if item.get("name") == name), None)
        if not found or not found.get("id"):
            raise ValueError(f"Keycloak did not return the {name} administrator group.")
        group_ids.append(str(found["id"]))
    return group_ids


def bootstrap_keycloak(base_url: str, secrets_dir: Path, state_dir: Path) -> dict[str, str]:
    """Configure realm and stable admin subject, publishing state only after success."""
    secrets_dir, state_dir = Path(os.path.abspath(secrets_dir)), Path(os.path.abspath(state_dir))
    ready = state_dir / "ready"
    _assert_no_symlink_components(secrets_dir)
    _assert_no_symlink_components(state_dir)
    _assert_no_symlink_components(ready)
    if ready.exists():
        if not ready.is_dir():
            raise ValueError("Local readiness path must be a directory.")
        for marker in ready.iterdir():
            _assert_no_symlink_components(marker)
            if marker.is_file():
                marker.unlink()
            else:
                raise ValueError("Local readiness state contains an unexpected item.")
    http = LocalHTTP(base_url, secrets_dir / "tls" / "ca.crt", timeout=180)
    admin_password = _secret(secrets_dir, "keycloak_admin_password")
    api_secret = _secret(secrets_dir, "keycloak_client_secret")
    bookstack_secret = _secret(secrets_dir, "bookstack_client_secret")
    gitea_secret = _secret(secrets_dir, "gitea_client_secret")
    token = http.request("POST", "realms/master/protocol/openid-connect/token",
                         data={"grant_type": "password", "client_id": "admin-cli",
                               "username": "admin", "password": admin_password})
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    realms = http.request("GET", "admin/realms", headers=headers)
    if not any(realm.get("realm") == REALM for realm in realms):
        http.request("POST", "admin/realms", json={"realm": REALM, "enabled": True}, headers=headers,
                     expected_statuses=(201, 409))

    for client_id, options in CLIENTS.items():
        clients = http.request("GET", f"admin/realms/{REALM}/clients?clientId={quote(client_id)}", headers=headers)
        public = bool(options["public"])
        config = {
            "clientId": client_id, "enabled": True, "protocol": "openid-connect",
            "publicClient": public, "clientAuthenticatorType": "client-secret",
            "standardFlowEnabled": bool(options["standard"]),
            "directAccessGrantsEnabled": bool(options["direct"]),
            "redirectUris": options["redirectUris"], "webOrigins": options["webOrigins"],
        }
        if client_id == "python-demo-api":
            config["secret"] = api_secret
            config["protocolMappers"] = [_api_audience_mapper()]
        elif client_id == "bookstack":
            config["secret"] = bookstack_secret
        elif client_id == "gitea":
            config["secret"] = gitea_secret
        if client_id == "python-demo-ui":
            config["attributes"] = {
                "pkce.code.challenge.method": "S256",
                "post.logout.redirect.uris": "+",
            }
            config["protocolMappers"] = [_api_audience_mapper(), _group_mapper()]
        elif client_id in {"bookstack", "gitea"}:
            config["protocolMappers"] = [_group_mapper()]
        if clients:
            client_uuid = clients[0]["id"]
            http.request("PUT", f"admin/realms/{REALM}/clients/{client_uuid}", json=config,
                         headers=headers, expected_statuses=(204,))
        else:
            http.request("POST", f"admin/realms/{REALM}/clients", json=config,
                         headers=headers, expected_statuses=(201, 409))

    group_ids = _ensure_groups(http, headers)
    users = http.request("GET", f"admin/realms/{REALM}/users?username=admin&exact=true", headers=headers)
    user = next((item for item in users if item.get("username") == "admin"), None)
    if not user:
        http.request("POST", f"admin/realms/{REALM}/users", json={
            "username": "admin", "enabled": True, "email": "admin@app.localhost",
            "emailVerified": True, "firstName": "Local", "lastName": "Administrator",
            "attributes": {"profileComplete": ["true"]},
        }, headers=headers, expected_statuses=(201, 409))
        users = http.request("GET", f"admin/realms/{REALM}/users?username=admin&exact=true", headers=headers)
        user = next((item for item in users if item.get("username") == "admin"), None)
    if not user or not user.get("id"):
        raise ValueError("Keycloak did not return the local administrator identity.")
    user_id = str(user["id"])
    http.request("PUT", f"admin/realms/{REALM}/users/{user_id}", json={
        **user, "enabled": True, "email": "admin@app.localhost", "emailVerified": True,
        "firstName": "Local", "lastName": "Administrator",
        "attributes": {**user.get("attributes", {}), "profileComplete": ["true"]},
    }, headers=headers, expected_statuses=(204,))
    http.request("PUT", f"admin/realms/{REALM}/users/{user_id}/reset-password",
                 json={"type": "password", "value": admin_password, "temporary": False},
                 headers=headers, expected_statuses=(204,))
    for group_id in group_ids:
        http.request("PUT", f"admin/realms/{REALM}/users/{user_id}/groups/{group_id}", headers=headers,
                     expected_statuses=(204,))
    role = http.request("GET", f"admin/realms/{REALM}/roles/admin", headers=headers,
                        expected_statuses=(200, 404))
    if not role or role.get("error"):
        http.request("POST", f"admin/realms/{REALM}/roles", json={"name": "admin"},
                     headers=headers, expected_statuses=(201, 409))
        role = http.request("GET", f"admin/realms/{REALM}/roles/admin", headers=headers)
    http.request("POST", f"admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
                 json=[role], headers=headers, expected_statuses=(204, 409))

    issuer = f"https://app.localhost/auth/realms/{REALM}"
    _persist_bookstack_public_key(http, state_dir, issuer)
    result = {"issuer": issuer, "subject": user_id, "username": "admin"}
    _atomic(state_dir / "keycloak-admin.json", json.dumps(result, sort_keys=True).encode())
    _atomic(state_dir / "keycloak-admin-subject", f"{user_id}\n".encode())
    _atomic(state_dir / "keycloak-issuer", f"{issuer}\n".encode())
    public_client_settings = {
        "python-demo-ui": {"redirectUris": CLIENTS["python-demo-ui"]["redirectUris"],
                           "pkceMethod": "S256", "audience": "python-demo-api"},
    }
    _atomic(state_dir / "keycloak-clients.json", json.dumps(public_client_settings, sort_keys=True).encode())
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://app-keycloak:8080/auth")
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/local-secrets"))
    parser.add_argument("--state-dir", type=Path, default=Path("/run/local-state"))
    arguments = parser.parse_args()
    print(json.dumps(bootstrap_keycloak(arguments.base_url, arguments.secrets_dir, arguments.state_dir), sort_keys=True))
