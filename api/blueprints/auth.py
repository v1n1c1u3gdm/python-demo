from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from services.keycloak_client import KeycloakError, get_keycloak_client

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return _json_error("username and password are required.", 400)

    client = get_keycloak_client()
    try:
        tokens = client.exchange_password(username, password)
        access_token = tokens.get("access_token")
        if not access_token:
            raise KeycloakError("Keycloak response does not include an access token.")
        claims = client.decode_token(access_token)
        roles = client.extract_roles(claims)
    except KeycloakError as exc:
        return _json_error(str(exc), 401)

    return jsonify(
        {
            "token_type": tokens.get("token_type", "Bearer"),
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token"),
            "expires_in": tokens.get("expires_in"),
            "roles": roles,
            "username": claims.get("preferred_username", username),
        }
    )


@bp.get("/admin/profile")
def admin_profile():
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        return _json_error("Authorization header with Bearer token is required.", 401)

    client = get_keycloak_client()
    required_role = current_app.config.get("KEYCLOAK_ADMIN_ROLE", "admin")

    try:
        claims = client.require_roles(token, [required_role])
    except KeycloakError as exc:
        message = str(exc)
        status = 403 if "Missing required role" in message else 401
        return _json_error(message, status)

    return jsonify(
        {
            "username": claims.get("preferred_username"),
            "email": claims.get("email"),
            "roles": client.extract_roles(claims),
        }
    )


def _extract_bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _json_error(message: str, status: int):
    return jsonify({"errors": [message]}), status

