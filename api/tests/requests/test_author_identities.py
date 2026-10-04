from datetime import date

import pytest

from extensions import db
from models import Author


@pytest.fixture()
def fake_keycloak(app):
    class FakeKeycloakClient:
        claims = {
            "sub": "keycloak-subject",
            "realm_access": {"roles": ["admin"]},
        }

        def decode_token(self, token):
            if token != "trusted-token":
                raise ValueError("invalid token")
            return self.claims

    client = FakeKeycloakClient()
    previous_client = app.extensions.get("keycloak_client")
    app.extensions["keycloak_client"] = client
    yield client
    if previous_client is None:
        app.extensions.pop("keycloak_client", None)
    else:
        app.extensions["keycloak_client"] = previous_client


def author(name):
    value = Author(
        name=name,
        birthdate=date(2000, 1, 1),
        photo_url="https://example.test/photo.png",
        public_key="public-key",
        bio="Author bio",
    )
    db.session.add(value)
    db.session.commit()
    return value


def admin_headers():
    return {"Authorization": "Bearer trusted-token"}


def test_admin_can_link_and_revoke_identity(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        value = author("Linked author")
        author_id = value.id
    payload = {
        "identity": {
            "issuer": app.config["KEYCLOAK_ISSUER"],
            "sub": "account-123",
        }
    }

    # Act
    linked = client.put(f"/authors/{author_id}/identity", json=payload, headers=admin_headers())
    revoked = client.delete(f"/authors/{author_id}/identity", headers=admin_headers())
    repeated_revoke = client.delete(f"/authors/{author_id}/identity", headers=admin_headers())

    # Assert
    assert linked.status_code == 200
    assert linked.get_json() == {"author_id": author_id, "linked": True}
    assert revoked.status_code == 204
    assert repeated_revoke.status_code == 204
    from models.author_identity import find_author_id

    with app.app_context():
        assert find_author_id(app.config["KEYCLOAK_ISSUER"], "account-123") is None


def test_identity_link_is_unique_and_replacement_is_atomic(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        first = author("First author")
        second = author("Second author")
        first_id, second_id = first.id, second.id
    issuer = app.config["KEYCLOAK_ISSUER"]
    original = {"identity": {"issuer": issuer, "sub": "first-subject"}}
    competing = {"identity": {"issuer": issuer, "sub": "second-subject"}}
    headers = admin_headers()

    # Act
    first_link = client.put(f"/authors/{first_id}/identity", json=original, headers=headers)
    second_link = client.put(f"/authors/{second_id}/identity", json=competing, headers=headers)
    replacement = client.put(
        f"/authors/{first_id}/identity",
        json={"identity": {"issuer": issuer, "sub": "replacement-subject"}},
        headers=headers,
    )
    replacement_conflict = client.put(f"/authors/{first_id}/identity", json=competing, headers=headers)

    # Assert
    assert first_link.status_code == 200
    assert second_link.status_code == 200
    assert replacement.status_code == 200
    assert replacement_conflict.status_code == 409

    from models.author_identity import find_author_id

    with app.app_context():
        assert find_author_id(issuer, "first-subject") is None
        assert find_author_id(issuer, "replacement-subject") == first_id
        assert find_author_id(issuer, "second-subject") == second_id


def test_identity_match_is_case_sensitive(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        upper = author("Upper case author")
        lower = author("Lower case author")
        upper_id, lower_id = upper.id, lower.id
    issuer = app.config["KEYCLOAK_ISSUER"]
    headers = admin_headers()

    # Act
    upper_response = client.put(
        f"/authors/{upper_id}/identity",
        json={"identity": {"issuer": issuer, "sub": "Alice"}},
        headers=headers,
    )
    lower_response = client.put(
        f"/authors/{lower_id}/identity",
        json={"identity": {"issuer": issuer, "sub": "alice"}},
        headers=headers,
    )
    # Assert
    assert upper_response.status_code == 200
    assert lower_response.status_code == 200

    from models.author_identity import find_author_id

    with app.app_context():
        assert find_author_id(issuer, "Alice") == upper_id
        assert find_author_id(issuer, "alice") == lower_id


def test_non_configured_issuer_is_rejected(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        value = author("Configured issuer author")
        author_id = value.id
    payload = {"identity": {"issuer": "https://other.example/realm", "sub": "account"}}

    # Act
    response = client.put(f"/authors/{author_id}/identity", json=payload, headers=admin_headers())

    # Assert
    assert response.status_code == 400
    assert response.get_json() == {"errors": ["Identity issuer is not configured."]}


def test_public_author_payload_does_not_expose_identity(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        value = author("Private identity author")
        author_id = value.id
    client.put(
        f"/authors/{author_id}/identity",
        json={"identity": {"issuer": app.config["KEYCLOAK_ISSUER"], "sub": "private-sub"}},
        headers=admin_headers(),
    )

    # Act
    response = client.get(f"/authors/{author_id}")

    # Assert
    assert response.status_code == 200
    payload = response.get_json()
    assert "identity" not in payload
    assert "issuer" not in payload
    assert "sub" not in payload


def test_identity_management_requires_admin(client, fake_keycloak):
    # Arrange
    fake_keycloak.claims["realm_access"]["roles"] = ["author"]

    # Act
    unauthenticated = client.delete("/authors/1/identity")
    authenticated_without_admin = client.delete(
        "/authors/1/identity", headers=admin_headers()
    )

    # Assert
    assert unauthenticated.status_code == 401
    assert authenticated_without_admin.status_code == 403


def test_identity_link_rejects_values_over_binary_column_limits(
    client, app, fake_keycloak, monkeypatch
):
    # Arrange
    with app.app_context():
        value = author("Bounded identity author")
        author_id = value.id
    oversized_issuer = "é" * 257
    oversized_subject = "é" * 128
    monkeypatch.setitem(app.config, "KEYCLOAK_ISSUER", oversized_issuer)

    # Act
    issuer_response = client.put(
        f"/authors/{author_id}/identity",
        json={"identity": {"issuer": oversized_issuer, "sub": "subject"}},
        headers=admin_headers(),
    )
    subject_response = client.put(
        f"/authors/{author_id}/identity",
        json={"identity": {"issuer": app.config["KEYCLOAK_ISSUER"], "sub": oversized_subject}},
        headers=admin_headers(),
    )

    # Assert
    assert issuer_response.status_code == 400
    assert issuer_response.get_json() == {"errors": ["A valid identity is required."]}
    assert subject_response.status_code == 400


def test_author_deletion_cascades_private_identity(client, app, fake_keycloak):
    # Arrange
    with app.app_context():
        value = author("Cascade identity author")
        author_id = value.id
    issuer = app.config["KEYCLOAK_ISSUER"]
    linked = client.put(
        f"/authors/{author_id}/identity",
        json={"identity": {"issuer": issuer, "sub": "cascade-subject"}},
        headers=admin_headers(),
    )

    # Act
    deleted = client.delete(f"/authors/{author_id}", headers=admin_headers())

    # Assert
    assert linked.status_code == 200
    assert deleted.status_code == 204
    from models.author_identity import find_author_id

    with app.app_context():
        assert find_author_id(issuer, "cascade-subject") is None


def test_identity_lookup_rejects_empty_and_oversized_values(app):
    # Arrange
    from models.author_identity import find_author_id

    # Act
    with app.app_context():
        empty_values = (find_author_id("", "subject"), find_author_id("issuer", ""))
        oversized_values = (
            find_author_id("é" * 257, "subject"),
            find_author_id("issuer", "é" * 128),
        )

    # Assert
    assert empty_values == (None, None)
    assert oversized_values == (None, None)


@pytest.mark.parametrize(
    "identity",
    [
        None,
        {},
        {"issuer": "issuer", "sub": 3},
        {"issuer": "issuer", "sub": ""},
        {"issuer": "issuer", "sub": "\ud800"},
        {"issuer": "issuer", "sub": "subject", "username": "not accepted"},
    ],
)
def test_identity_link_rejects_malformed_identity_fields(client, app, fake_keycloak, identity):
    # Arrange
    with app.app_context():
        value = author("Malformed identity author")
        author_id = value.id
    payload = {"identity": identity}
    if isinstance(identity, dict):
        payload["identity"]["issuer"] = app.config["KEYCLOAK_ISSUER"]

    # Act
    response = client.put(
        f"/authors/{author_id}/identity", json=payload, headers=admin_headers()
    )

    # Assert
    assert response.status_code == 400
