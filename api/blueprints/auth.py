from __future__ import annotations

from flask import Blueprint, jsonify, request

from services.authorization import AuthorizationError, require_admin
from services.keycloak_client import KeycloakError, get_keycloak_client

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("username and password are required.", 400)

    username_value = payload.get("username")
    password = payload.get("password")
    if not isinstance(username_value, str) or not isinstance(password, str):
        return _json_error("username and password are required.", 400)
    username = username_value.strip()

    if not username or not password:
        return _json_error("username and password are required.", 400)

    client = get_keycloak_client()
    try:
        tokens = client.exchange_password(username, password)
        if not isinstance(tokens, dict):
            raise KeycloakError("Invalid response from identity provider.")
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise KeycloakError("Invalid response from identity provider.")
        claims = client.decode_token(access_token)
        if not isinstance(claims, dict):
            raise KeycloakError("Invalid response from identity provider.")
        roles = client.extract_roles(claims)
    except KeycloakError:
        return _json_error("Authentication failed.", 401)

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
    try:
        claims = require_admin()
    except AuthorizationError as exc:
        return _json_error(str(exc), exc.status_code)

    client = get_keycloak_client()

    return jsonify(
        {
            "username": claims.get("preferred_username"),
            "email": claims.get("email"),
            "roles": client.extract_roles(claims),
        }
    )


def _json_error(message: str, status: int):
    return jsonify({"errors": [message]}), status
