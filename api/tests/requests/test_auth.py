import pytest

from services.keycloak_client import KeycloakError


@pytest.fixture()
def fake_keycloak(app):
    class FakeKeycloakClient:
        def __init__(self):
            self.tokens = {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "expires_in": 900,
            }
            self.claims = {
                "preferred_username": "admin",
                "email": "admin@example.com",
                "realm_access": {"roles": ["admin", "author"]},
            }

        def exchange_password(self, username, password):
            if password != "super-secret":
                raise KeycloakError("Invalid credentials: bad password")
            return self.tokens

        def decode_token(self, token):
            if token != self.tokens["access_token"]:
                raise KeycloakError("Token validation failed.")
            return self.claims

        def extract_roles(self, claims):
            return sorted(claims.get("realm_access", {}).get("roles", []))

        def require_roles(self, token, required_roles):
            claims = self.decode_token(token)
            granted = set(self.extract_roles(claims))
            missing = [role for role in required_roles if role not in granted]
            if missing:
                raise KeycloakError(f"Missing required role(s): {', '.join(missing)}")
            return claims

    fake = FakeKeycloakClient()
    app.extensions["keycloak_client"] = fake
    return fake


def test_login_success(client, fake_keycloak):
    response = client.post("/login", json={"username": "admin", "password": "super-secret"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["access_token"] == "access-token"
    assert payload["roles"] == ["admin", "author"]


def test_login_requires_credentials(client):
    response = client.post("/login", json={"username": "", "password": ""})

    assert response.status_code == 400
    assert "errors" in response.get_json()


def test_login_handles_keycloak_error(client, fake_keycloak):
    response = client.post("/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    payload = response.get_json()
    assert "Invalid credentials" in payload["errors"][0]


def test_admin_profile_requires_bearer_token(client):
    response = client.get("/admin/profile")

    assert response.status_code == 401


def test_admin_profile_succeeds_with_role(client, fake_keycloak):
    headers = {"Authorization": f"Bearer {fake_keycloak.tokens['access_token']}"}
    response = client.get("/admin/profile", headers=headers)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["username"] == "admin"
    assert payload["roles"] == ["admin", "author"]


def test_admin_profile_rejects_missing_role(client, fake_keycloak):
    fake_keycloak.claims["realm_access"]["roles"] = ["author"]
    headers = {"Authorization": f"Bearer {fake_keycloak.tokens['access_token']}"}
    response = client.get("/admin/profile", headers=headers)

    assert response.status_code == 403
    payload = response.get_json()
    assert "Missing required role" in payload["errors"][0]

