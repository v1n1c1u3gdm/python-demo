from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

import requests
from flask import current_app
from jose import jwt


class KeycloakError(RuntimeError):
    """Raised when an interaction with Keycloak fails."""


class KeycloakClient:
    """Small helper around the Keycloak OpenID endpoints."""

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: Optional[str] = None,
        *,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer = issuer or f"{self.base_url}/realms/{realm}"
        self.audience = audience or client_id
        self._session = session or requests.Session()
        self._cache_ttl = cache_ttl_seconds
        self._well_known: Optional[Dict[str, Any]] = None
        self._well_known_expires_at: float = 0
        self._jwks: Optional[Dict[str, Any]] = None
        self._jwks_expires_at: float = 0

    def exchange_password(self, username: str, password: str) -> Dict[str, Any]:
        """Request access/refresh tokens using the Resource Owner Password grant."""
        metadata = self._get_well_known()
        token_endpoint = metadata.get("token_endpoint")
        if not token_endpoint:
            raise KeycloakError("Keycloak token endpoint not available.")

        data = {
            "grant_type": "password",
            "client_id": self.client_id,
            "username": username,
            "password": password,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        try:
            response = self._session.post(
                token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise KeycloakError("Authentication service unavailable.") from exc
        if not response.ok:
            raise KeycloakError("Invalid credentials.")

        try:
            payload = response.json()
        except (ValueError, requests.RequestException) as exc:
            raise KeycloakError("Invalid response from identity provider.") from exc
        if not isinstance(payload, dict):
            raise KeycloakError("Invalid response from identity provider.")
        return payload

    def decode_token(self, token: str) -> Dict[str, Any]:
        """Validate and decode a JWT access token."""
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:  # pragma: no cover - jose raises many subclasses
            raise KeycloakError("Invalid token header.") from exc

        if header.get("alg") != "RS256":
            raise KeycloakError("Token validation failed.")

        try:
            jwk = self._select_key(header.get("kid"))
        except KeycloakError:
            raise
        except Exception as exc:
            raise KeycloakError("Identity provider unavailable.") from exc
        if not jwk:
            raise KeycloakError("Unable to resolve signing key for token.")

        try:
            claims = jwt.decode(
                token,
                jwk,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require_aud": True,
                    "require_exp": True,
                    "require_iss": True,
                    "require_sub": True,
                },
            )
        except Exception as exc:  # pragma: no cover
            raise KeycloakError("Token validation failed.") from exc
        if not isinstance(claims, dict):
            raise KeycloakError("Token validation failed.")
        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            raise KeycloakError("Token validation failed.")
        if claims.get("typ") != "Bearer":
            raise KeycloakError("Token validation failed.")
        return claims

    def extract_roles(self, claims: Dict[str, Any]) -> List[str]:
        realm_access = claims.get("realm_access") or {}
        if not isinstance(realm_access, dict):
            return []
        roles = realm_access.get("roles") or []
        if not isinstance(roles, list):
            return []
        return sorted({role for role in roles if isinstance(role, str)})

    def require_roles(self, token: str, required_roles: Iterable[str]) -> Dict[str, Any]:
        claims = self.decode_token(token)
        granted = set(self.extract_roles(claims))
        missing = [role for role in required_roles if role not in granted]
        if missing:
            raise KeycloakError(f"Missing required role(s): {', '.join(missing)}")
        return claims

    def _get_well_known(self) -> Dict[str, Any]:
        if self._well_known and time.time() < self._well_known_expires_at:
            return self._well_known

        url = f"{self.base_url}/realms/{self.realm}/.well-known/openid-configuration"
        try:
            response = self._session.get(url, timeout=10)
        except requests.RequestException as exc:
            raise KeycloakError("Unable to fetch OpenID metadata.") from exc
        if not response.ok:
            raise KeycloakError("Unable to fetch OpenID metadata.")

        try:
            metadata = response.json()
        except (ValueError, requests.RequestException) as exc:
            raise KeycloakError("Invalid OpenID metadata response.") from exc
        if not isinstance(metadata, dict):
            raise KeycloakError("Invalid OpenID metadata response.")
        self._well_known = metadata
        self._well_known_expires_at = time.time() + self._cache_ttl
        return self._well_known

    def _get_jwks(self) -> Dict[str, Any]:
        if self._jwks and time.time() < self._jwks_expires_at:
            return self._jwks

        metadata = self._get_well_known()
        jwks_uri = metadata.get("jwks_uri")
        if not jwks_uri:
            raise KeycloakError("Keycloak JWKS endpoint not available.")

        try:
            response = self._session.get(jwks_uri, timeout=10)
        except requests.RequestException as exc:
            raise KeycloakError("Unable to fetch JWKS.") from exc
        if not response.ok:
            raise KeycloakError("Unable to fetch JWKS.")

        try:
            jwks = response.json()
        except (ValueError, requests.RequestException) as exc:
            raise KeycloakError("Invalid JWKS response.") from exc
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise KeycloakError("Invalid JWKS response.")
        self._jwks = jwks
        self._jwks_expires_at = time.time() + self._cache_ttl
        return self._jwks

    def _select_key(self, kid: Optional[str]) -> Optional[Dict[str, Any]]:
        if not kid:
            return None
        keys = self._get_jwks().get("keys", [])
        for jwk in keys:
            if jwk.get("kid") == kid:
                return jwk
        return None

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "KeycloakClient":
        return cls(
            config.get("KEYCLOAK_BASE_URL", "http://keycloak:8080"),
            config.get("KEYCLOAK_REALM", "python-demo"),
            config.get("KEYCLOAK_CLIENT_ID", "python-demo-api"),
            config.get("KEYCLOAK_CLIENT_SECRET"),
            issuer=config.get(
                "KEYCLOAK_ISSUER",
                f"{config.get('KEYCLOAK_BASE_URL', 'http://keycloak:8080').rstrip('/')}/realms/{config.get('KEYCLOAK_REALM', 'python-demo')}",
            ),
            audience=config.get("KEYCLOAK_AUDIENCE", config.get("KEYCLOAK_CLIENT_ID", "python-demo-api")),
        )


def init_keycloak_client(app) -> None:
    app.extensions["keycloak_client"] = KeycloakClient.from_config(app.config)


def get_keycloak_client():
    client = current_app.extensions.get("keycloak_client")
    if client is None:
        raise RuntimeError("Keycloak client is not configured.")
    return client
