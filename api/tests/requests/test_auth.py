import pytest

from services.authorization import AuthorizationError
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


@pytest.mark.parametrize("payload", [["admin", "password"], {"username": 123, "password": "secret"}])
def test_login_rejects_malformed_json_fields(client, payload):
    # Arrange / Act
    response = client.post("/login", json=payload)

    # Assert
    assert response.status_code == 400
    assert "errors" in response.get_json()


def test_login_handles_keycloak_error(client, fake_keycloak):
    response = client.post("/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    payload = response.get_json()
    assert "wrong" not in payload["errors"][0]


def test_login_does_not_expose_keycloak_error_details(client, fake_keycloak, monkeypatch):
    # Arrange
    monkeypatch.setattr(
        fake_keycloak,
        "exchange_password",
        lambda username, password: (_ for _ in ()).throw(
            KeycloakError("upstream echoed password client-secret-123")
        ),
    )

    # Act
    response = client.post("/login", json={"username": "admin", "password": "client-secret-123"})

    # Assert
    assert response.status_code == 401
    assert "client-secret-123" not in response.get_data(as_text=True)


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
    assert "Missing required role" not in payload["errors"][0]


def test_admin_profile_treats_malformed_role_claims_as_forbidden(client, fake_keycloak):
    # Arrange
    fake_keycloak.claims["realm_access"] = {"roles": {"admin": True}}
    headers = {"Authorization": f"Bearer {fake_keycloak.tokens['access_token']}"}

    # Act
    response = client.get("/admin/profile", headers=headers)

    # Assert
    assert response.status_code == 403


def test_authorization_errors_use_their_safe_http_status_and_json_body():
    # Arrange
    from app import create_app
    from extensions import db

    isolated_app = create_app()

    def protected_resource():
        raise AuthorizationError("Insufficient permissions.", 403)

    isolated_app.add_url_rule("/test-protected-resource", view_func=protected_resource)

    try:
        # Act
        response = isolated_app.test_client().get("/test-protected-resource")

        # Assert
        assert response.status_code == 403
        assert response.get_json() == {"errors": ["Insufficient permissions."]}
    finally:
        with isolated_app.app_context():
            db.session.remove()
            db.engine.dispose()
