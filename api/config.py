import os
from pathlib import Path


class BaseConfig:
    """Default Flask configuration shared across environments."""

    SERVICE_NAME = "python-demo-api"
    API_TITLE = "Python Demo API"
    API_VERSION = "v1"

    SQLALCHEMY_DATABASE_URI = (
        os.getenv(
            "DATABASE_URL",
            "mysql+pymysql://ruby-demo:2u8y-c0d3@db:3306/ruby_demo_development",
        )
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parent / "logs"))

    SWAGGER_UI_ROUTE = "/api-docs"
    SWAGGER_SPEC_PATH = Path(__file__).parent / "swagger" / "v1" / "swagger.yaml"

    OPENAPI_SERVICE_NAME = SERVICE_NAME
    OPENAPI_SERVICE_NAMESPACE = "python-demo"

    OTEL_EXPORT_INTERVAL_MS = int(os.getenv("OTEL_EXPORT_INTERVAL_MS", "60000"))

    KEYCLOAK_BASE_URL = os.getenv("KEYCLOAK_BASE_URL", "http://keycloak:8080")
    KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "python-demo")
    KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "python-demo-api")
    KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "python-demo-api-secret")
    KEYCLOAK_ADMIN_ROLE = os.getenv("KEYCLOAK_ADMIN_ROLE", "admin")


class TestConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL_TEST",
        "sqlite+pysqlite:///:memory:",
    )
    TESTING = True


class DevelopmentConfig(BaseConfig):
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestConfig,
    "default": BaseConfig,
}


def get_config():
    env = os.getenv("FLASK_ENV", "default").lower()
    return config_by_name.get(env, BaseConfig)

