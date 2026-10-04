import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

DEVELOPMENT_DATABASE_URL = "mysql+pymysql://ruby-demo:2u8y-c0d3@db:3306/ruby_demo_development"
DEVELOPMENT_KEYCLOAK_SECRET = "python-demo-api-secret"


class ConfigurationError(RuntimeError):
    """Raised when runtime settings are missing or unsafe."""


class BaseConfig:
    """Default Flask configuration shared across environments."""

    SERVICE_NAME = "python-demo-api"
    API_TITLE = "Python Demo API"
    API_VERSION = "v1"

    SQLALCHEMY_DATABASE_URI = DEVELOPMENT_DATABASE_URL
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    READINESS_TIMEOUT_SECONDS = 3.0
    READINESS_MYSQL_CONNECT_TIMEOUT_SECONDS = 1.0
    READINESS_MYSQL_READ_TIMEOUT_SECONDS = 1.0
    READINESS_KEYCLOAK_CONNECT_TIMEOUT_SECONDS = 0.5
    READINESS_KEYCLOAK_READ_TIMEOUT_SECONDS = 1.0
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LOG_LEVEL = "INFO"
    LOG_DIR = Path(__file__).resolve().parent / "logs"

    SWAGGER_UI_ROUTE = "/api-docs"
    SWAGGER_SPEC_PATH = Path(__file__).parent / "swagger" / "v1" / "swagger.yaml"

    OPENAPI_SERVICE_NAME = SERVICE_NAME
    OPENAPI_SERVICE_NAMESPACE = "python-demo"

    OTEL_METRICS_ENABLED = False
    OTEL_EXPORTER_OTLP_METRICS_ENDPOINT = "http://otel-collector:4318/v1/metrics"
    OTEL_EXPORT_INTERVAL_MS = 10000
    OTEL_EXPORT_TIMEOUT_SECONDS = 2

    KEYCLOAK_BASE_URL = "http://keycloak:8080"
    KEYCLOAK_REALM = "python-demo"
    KEYCLOAK_CLIENT_ID = "python-demo-api"
    KEYCLOAK_CLIENT_SECRET = DEVELOPMENT_KEYCLOAK_SECRET
    KEYCLOAK_ADMIN_ROLE = "admin"
    KEYCLOAK_AUTHOR_ROLE = "author"
    KEYCLOAK_ISSUER = "http://keycloak:8080/realms/python-demo"
    KEYCLOAK_AUDIENCE = "python-demo-api"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> dict[str, object]:
        base_url = environ.get("KEYCLOAK_BASE_URL", cls.KEYCLOAK_BASE_URL).rstrip("/")
        realm = environ.get("KEYCLOAK_REALM", cls.KEYCLOAK_REALM)
        client_id = environ.get("KEYCLOAK_CLIENT_ID", cls.KEYCLOAK_CLIENT_ID)
        return {
            "SQLALCHEMY_DATABASE_URI": environ.get("DATABASE_URL", cls.SQLALCHEMY_DATABASE_URI),
            "LOG_LEVEL": environ.get("LOG_LEVEL", cls.LOG_LEVEL),
            "LOG_DIR": Path(environ.get("LOG_DIR", str(cls.LOG_DIR))),
            "OTEL_METRICS_ENABLED": environ.get("OTEL_METRICS_ENABLED", "false").lower() == "true",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": environ.get(
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", cls.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
            ),
            "OTEL_EXPORT_INTERVAL_MS": int(environ.get("OTEL_EXPORT_INTERVAL_MS", "10000")),
            "OTEL_EXPORT_TIMEOUT_SECONDS": int(environ.get("OTEL_EXPORT_TIMEOUT_SECONDS", "2")),
            "KEYCLOAK_BASE_URL": base_url,
            "KEYCLOAK_REALM": realm,
            "KEYCLOAK_CLIENT_ID": client_id,
            "KEYCLOAK_CLIENT_SECRET": environ.get(
                "KEYCLOAK_CLIENT_SECRET", cls.KEYCLOAK_CLIENT_SECRET
            ),
            "KEYCLOAK_ADMIN_ROLE": environ.get("KEYCLOAK_ADMIN_ROLE", cls.KEYCLOAK_ADMIN_ROLE),
            "KEYCLOAK_AUTHOR_ROLE": environ.get("KEYCLOAK_AUTHOR_ROLE", cls.KEYCLOAK_AUTHOR_ROLE),
            "KEYCLOAK_ISSUER": environ.get(
                "KEYCLOAK_ISSUER", f"{base_url}/realms/{realm}"
            ),
            "KEYCLOAK_AUDIENCE": environ.get("KEYCLOAK_AUDIENCE", client_id),
        }


class TestConfig(BaseConfig):
    TESTING = True

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> dict[str, object]:
        settings = super().from_environment(environ)
        settings["SQLALCHEMY_DATABASE_URI"] = environ.get(
            "DATABASE_URL_TEST", environ.get("DATABASE_URL", "sqlite+pysqlite:///:memory:")
        )
        settings["TESTING"] = True
        return settings


class DevelopmentConfig(BaseConfig):
    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> dict[str, object]:
        settings = super().from_environment(environ)
        settings["DEBUG"] = environ.get("FLASK_DEBUG", "0") == "1"
        return settings


class ProductionConfig(BaseConfig):
    REQUIRED_SETTINGS = (
        "DATABASE_URL",
        "KEYCLOAK_BASE_URL",
        "KEYCLOAK_ISSUER",
        "KEYCLOAK_REALM",
        "KEYCLOAK_CLIENT_ID",
        "KEYCLOAK_CLIENT_SECRET",
        "KEYCLOAK_AUDIENCE",
    )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> dict[str, object]:
        missing = [key for key in cls.REQUIRED_SETTINGS if not environ.get(key, "").strip()]
        if missing:
            raise ConfigurationError(
                "Missing required production settings: " + ", ".join(missing)
            )

        issuer = environ["KEYCLOAK_ISSUER"].strip()
        try:
            issuer_parts = urlsplit(issuer)
            issuer_parts.port
        except ValueError as exc:
            raise ConfigurationError("KEYCLOAK_ISSUER must be an HTTPS issuer URL.") from exc
        if (
            issuer_parts.scheme != "https"
            or not issuer_parts.hostname
            or issuer_parts.username
            or issuer_parts.password
            or issuer_parts.query
            or issuer_parts.fragment
        ):
            raise ConfigurationError("KEYCLOAK_ISSUER must be an HTTPS issuer URL.")
        base_url = environ["KEYCLOAK_BASE_URL"].strip()
        try:
            base_parts = urlsplit(base_url)
            base_parts.port
        except ValueError as exc:
            raise ConfigurationError(
                "KEYCLOAK_BASE_URL must be an absolute HTTP(S) URL without credentials."
            ) from exc
        if (
            base_parts.scheme not in {"http", "https"}
            or not base_parts.hostname
            or base_parts.username
            or base_parts.password
            or base_parts.query
            or base_parts.fragment
        ):
            raise ConfigurationError("KEYCLOAK_BASE_URL must be an absolute HTTP(S) URL without credentials.")

        database_url = environ["DATABASE_URL"].strip()
        try:
            parsed_database_url = make_url(database_url)
        except Exception as exc:
            raise ConfigurationError("DATABASE_URL must be a valid SQLAlchemy database URL.") from exc
        if not all(
            (
                parsed_database_url.drivername,
                parsed_database_url.host,
                parsed_database_url.database,
                parsed_database_url.username,
                parsed_database_url.password,
            )
        ):
            raise ConfigurationError("DATABASE_URL must include driver, host, database, and credentials.")
        if parsed_database_url.drivername != "mysql+pymysql":
            raise ConfigurationError("DATABASE_URL must use the configured MySQL/PyMySQL driver.")
        if parsed_database_url.password == "2u8y-c0d3":
            raise ConfigurationError("DATABASE_URL must not use the development demo credentials.")
        client_secret = environ["KEYCLOAK_CLIENT_SECRET"].strip()
        if client_secret == DEVELOPMENT_KEYCLOAK_SECRET:
            raise ConfigurationError("KEYCLOAK_CLIENT_SECRET must not use the development demo secret.")

        settings = super().from_environment(environ)
        settings["SQLALCHEMY_DATABASE_URI"] = database_url
        settings["KEYCLOAK_BASE_URL"] = base_url.rstrip("/")
        settings["KEYCLOAK_ISSUER"] = issuer
        settings["KEYCLOAK_REALM"] = environ["KEYCLOAK_REALM"].strip()
        settings["KEYCLOAK_CLIENT_ID"] = environ["KEYCLOAK_CLIENT_ID"].strip()
        settings["KEYCLOAK_CLIENT_SECRET"] = client_secret
        settings["KEYCLOAK_AUDIENCE"] = environ["KEYCLOAK_AUDIENCE"].strip()
        return settings


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestConfig,
    "default": BaseConfig,
    "production": ProductionConfig,
}


def get_config(env_name: str | None = None, environ: Mapping[str, str] | None = None):
    env = environ if environ is not None else os.environ
    name = (env_name if env_name is not None else env.get("FLASK_ENV", "default")).lower()
    try:
        return config_by_name[name]
    except KeyError as exc:
        raise ConfigurationError("Unsupported FLASK_ENV value.") from exc
