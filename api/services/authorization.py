from typing import Any

from flask import current_app, request

from models.author_identity import find_author_id
from services.keycloak_client import KeycloakError, get_keycloak_client


class AuthorizationError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def get_bearer_claims() -> dict[str, Any]:
    cached_claims = request.environ.get("python_demo.keycloak_claims")
    if isinstance(cached_claims, dict):
        return cached_claims

    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthorizationError("A valid Bearer token is required.", 401)

    try:
        claims = get_keycloak_client().decode_token(parts[1])
    except KeycloakError as exc:
        raise AuthorizationError("A valid Bearer token is required.", 401) from exc
    if not isinstance(claims, dict):
        raise AuthorizationError("A valid Bearer token is required.", 401)
    request.environ["python_demo.keycloak_claims"] = claims
    return claims


def has_role(claims: dict[str, Any], role: str) -> bool:
    realm_access = claims.get("realm_access")
    roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    return isinstance(roles, list) and role in {value for value in roles if isinstance(value, str)}


def require_admin() -> dict[str, Any]:
    claims = get_bearer_claims()
    required_role = current_app.config.get("KEYCLOAK_ADMIN_ROLE", "admin")
    if not has_role(claims, required_role):
        raise AuthorizationError("Insufficient permissions.", 403)
    return claims


def require_author_identity() -> tuple[dict[str, Any], int]:
    claims = get_bearer_claims()
    required_role = current_app.config.get("KEYCLOAK_AUTHOR_ROLE", "author")
    if not has_role(claims, required_role):
        raise AuthorizationError("Insufficient permissions.", 403)

    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or not isinstance(subject, str):
        raise AuthorizationError("Insufficient permissions.", 403)
    author_id = find_author_id(issuer, subject)
    if author_id is None:
        raise AuthorizationError("Insufficient permissions.", 403)
    return claims, author_id
