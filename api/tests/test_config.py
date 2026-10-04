import pytest

from app import create_app
from config import ConfigurationError, ProductionConfig, get_config


def test_production_config_requires_explicit_credentials():
    # Arrange
    environment = {
        "DATABASE_URL": "mysql+pymysql://api:db-secret@db:3306/articles",
        "KEYCLOAK_BASE_URL": "http://keycloak:8080",
        "KEYCLOAK_ISSUER": "https://identity.example/realms/articles",
        "KEYCLOAK_REALM": "articles",
        "KEYCLOAK_CLIENT_ID": "articles-api",
        "KEYCLOAK_CLIENT_SECRET": "client-secret",
        "KEYCLOAK_AUDIENCE": "articles-api",
    }

    # Act
    settings = ProductionConfig.from_environment(environment)

    # Assert
    assert settings["SQLALCHEMY_DATABASE_URI"] == environment["DATABASE_URL"]
    assert settings["KEYCLOAK_ISSUER"] == "https://identity.example/realms/articles"
    assert settings["KEYCLOAK_AUDIENCE"] == "articles-api"

    missing_secret = {key: value for key, value in environment.items() if key != "KEYCLOAK_CLIENT_SECRET"}
    with pytest.raises(ConfigurationError) as error:
        ProductionConfig.from_environment(missing_secret)
    assert "KEYCLOAK_CLIENT_SECRET" in str(error.value)
    assert "client-secret" not in str(error.value)


def test_unknown_environment_fails_closed():
    # Arrange / Act / Assert
    with pytest.raises(ConfigurationError, match="Unsupported FLASK_ENV"):
        get_config("prodution", {})


def test_configuration_reads_environment_values_when_requested():
    # Arrange
    environment = {
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "KEYCLOAK_BASE_URL": "http://internal-id:8080",
        "KEYCLOAK_ISSUER": "http://internal-id:8080/realms/python-demo",
        "KEYCLOAK_AUDIENCE": "configured-api",
    }

    # Act
    config_class = get_config("development", environment)
    settings = config_class.from_environment(environment)

    # Assert
    assert settings["SQLALCHEMY_DATABASE_URI"] == environment["DATABASE_URL"]
    assert settings["KEYCLOAK_AUDIENCE"] == "configured-api"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("KEYCLOAK_BASE_URL", "http://user:password@keycloak:8080"),
        ("KEYCLOAK_BASE_URL", "http://keycloak:8080?token=secret"),
        ("KEYCLOAK_BASE_URL", "http://[malformed-host"),
        ("KEYCLOAK_ISSUER", "https://user:password@identity.example/realms/articles"),
        ("DATABASE_URL", "not-a-database-url"),
        ("DATABASE_URL", "postgresql://svc:safe-db-secret@db:5432/articles"),
        ("DATABASE_URL", "mysql+pymysql://svc:2u8y-c0d3@other-db:3306/articles"),
        ("KEYCLOAK_CLIENT_SECRET", " python-demo-api-secret "),
    ],
)
def test_production_config_rejects_invalid_or_demo_credentials(setting, value):
    # Arrange
    environment = {
        "DATABASE_URL": "mysql+pymysql://svc:safe-db-secret@db:3306/articles",
        "KEYCLOAK_BASE_URL": "http://keycloak:8080",
        "KEYCLOAK_ISSUER": "https://identity.example/realms/articles",
        "KEYCLOAK_REALM": "articles",
        "KEYCLOAK_CLIENT_ID": "articles-api",
        "KEYCLOAK_CLIENT_SECRET": "safe-client-secret",
        "KEYCLOAK_AUDIENCE": "articles-api",
    }
    environment[setting] = value

    # Act / Assert
    with pytest.raises(ConfigurationError) as error:
        ProductionConfig.from_environment(environment)
    assert "safe-db-secret" not in str(error.value)
    assert "safe-client-secret" not in str(error.value)


def test_app_creation_fails_before_database_access_when_production_settings_are_missing(
    monkeypatch,
):
    # Arrange
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://svc:db-password-secret@db:3306/articles")
    monkeypatch.setenv("KEYCLOAK_BASE_URL", "http://keycloak:8080")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://identity.example/realms/articles")
    monkeypatch.setenv("KEYCLOAK_REALM", "articles")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "articles-api")
    monkeypatch.delenv("KEYCLOAK_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEYCLOAK_AUDIENCE", "articles-api")

    # Act / Assert
    with pytest.raises(ConfigurationError) as error:
        create_app()
    assert "KEYCLOAK_CLIENT_SECRET" in str(error.value)
    assert "db-password-secret" not in str(error.value)
