import json
from pathlib import Path


def test_realm_import_configures_john_doe_for_admin_login():
    # Arrange
    realm_path = Path(__file__).resolve().parents[2] / "keycloak" / "realm-python-demo.json"
    realm = json.loads(realm_path.read_text(encoding="utf-8"))

    # Act
    john_doe = next(
        (user for user in realm["users"] if user["username"] == "john.doe"),
        None,
    )

    # Assert
    assert john_doe is not None, "the realm must seed John Doe for local login tests"
    credentials = john_doe.get("credentials", [])
    has_test_password = any(
        credential.get("type") == "password"
        and credential.get("value") == "john.doe!123"
        and credential.get("temporary") is False
        for credential in credentials
    )
    profile_is_complete = (
        john_doe.get("firstName") == "John"
        and john_doe.get("lastName") == "Doe"
        and john_doe.get("email") == "john.doe@example.com"
        and john_doe.get("enabled") is True
        and john_doe.get("emailVerified") is True
    )
    has_admin_profile_roles = {"admin", "author"}.issubset(
        john_doe.get("realmRoles", [])
    )

    assert profile_is_complete, "John Doe must have a complete enabled, verified profile"
    assert has_test_password, "John Doe must have a permanent local test password"
    assert has_admin_profile_roles, "John Doe must be able to access the admin profile"
