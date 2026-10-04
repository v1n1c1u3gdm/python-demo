import os

import pytest

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app import create_app
from extensions import db
from services.keycloak_client import KeycloakError


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config.update({"TESTING": True})

    with application.app_context():
        db.create_all()
        try:
            yield application
        finally:
            try:
                db.session.remove()
                db.drop_all()
            finally:
                db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def role_tokens(app):
    class RoleTokenVerifier:
        def decode_token(self, token):
            if token not in claims_by_token:
                raise KeycloakError("Invalid token.")
            return claims_by_token[token]

        @staticmethod
        def extract_roles(claims):
            realm_access = claims.get("realm_access", {})
            roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
            return sorted({role for role in roles if isinstance(role, str)}) if isinstance(roles, list) else []

    tokens = {
        "admin": "test-admin-token",
        "author": "test-author-token",
        "unlinked_author": "test-unlinked-author-token",
        "reader": "test-reader-token",
    }
    issuer = app.config["KEYCLOAK_ISSUER"]
    claims_by_token = {
        tokens["admin"]: {
            "iss": issuer,
            "sub": "admin-subject",
            "preferred_username": "admin",
            "email": "admin@example.test",
            "realm_access": {"roles": ["admin", "author"]},
        },
        tokens["author"]: {
            "iss": issuer,
            "sub": "linked-author-subject",
            "preferred_username": "author",
            "email": "author@example.test",
            "realm_access": {"roles": ["author"]},
        },
        tokens["unlinked_author"]: {
            "iss": issuer,
            "sub": "unlinked-author-subject",
            "preferred_username": "unlinked-author",
            "email": "unlinked@example.test",
            "realm_access": {"roles": ["author"]},
        },
        tokens["reader"]: {
            "iss": issuer,
            "sub": "reader-subject",
            "preferred_username": "reader",
            "email": "reader@example.test",
            "realm_access": {"roles": []},
        },
    }
    previous_client = app.extensions["keycloak_client"]
    app.extensions["keycloak_client"] = RoleTokenVerifier()
    try:
        yield tokens
    finally:
        app.extensions["keycloak_client"] = previous_client


@pytest.fixture()
def admin_headers(role_tokens):
    return {"Authorization": f"Bearer {role_tokens['admin']}"}


@pytest.fixture()
def author_headers(role_tokens):
    return {"Authorization": f"Bearer {role_tokens['author']}"}


@pytest.fixture()
def unlinked_author_headers(role_tokens):
    return {"Authorization": f"Bearer {role_tokens['unlinked_author']}"}


@pytest.fixture(autouse=True)
def clean_database(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        yield
        db.session.remove()
