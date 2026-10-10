"""Validate app-stack inputs and prepare secret-backed application commands."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, urlsplit


class ConfigurationError(Exception):
    """A safe, operator-facing configuration error without secret values."""


REQUIRED_ENVIRONMENT = (
    "STACK_PROJECT",
    "APP_WEB_NETWORK",
    "APP_DATA_NETWORK",
    "PUBLIC_HOST",
    "GATEWAY_TLS_CA_FILE",
    "KC_BOOTSTRAP_ADMIN_USERNAME",
    "MYSQL_ROOT_PASSWORD_FILE",
    "API_DB_PASSWORD_FILE",
    "KEYCLOAK_DB_PASSWORD_FILE",
    "KEYCLOAK_CLIENT_SECRET_FILE",
    "KEYCLOAK_ADMIN_PASSWORD_FILE",
)
NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
NETWORK_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
HOST_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?$")
DEVELOPMENT_API_DB_PASSWORD = "2u8y-c0d3"
DEVELOPMENT_KEYCLOAK_CLIENT_SECRET = "python-demo-api-secret"


def parse_environment_file(path: Path) -> dict[str, str]:
    """Read the deliberately simple KEY=VALUE form used by infra/.env."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigurationError("The app stack environment file is unavailable.") from error

    values: dict[str, str] = {}
    for line_number, source_line in enumerate(lines, start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Invalid environment assignment on line {line_number}.")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not key or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigurationError(f"Invalid environment variable name on line {line_number}.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _secret_value(path_value: str, variable: str) -> str:
    try:
        contents = Path(path_value).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Secret file for {variable} is unavailable or invalid.") from error
    if contents.endswith("\r\n"):
        contents = contents[:-2]
    elif contents.endswith("\n"):
        contents = contents[:-1]
    if not contents or "\n" in contents or "\r" in contents:
        raise ConfigurationError(f"Secret file for {variable} must contain one non-empty line.")
    return contents


def resolve_compose_environment(
    env_file: Path,
    repository_root: Path,
    compose_file: Path,
) -> dict[str, str]:
    """Ask Compose for its effective interpolation environment without starting services."""
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(repository_root),
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "config",
                "--environment",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ConfigurationError("Docker Compose could not resolve the app stack environment.") from error
    if result.returncode:
        raise ConfigurationError("Docker Compose could not resolve the app stack environment.")

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            values[key] = value
    return values


def validate_runtime_configuration(
    environment: Mapping[str, str], repository_root: Path
) -> None:
    """Fail before Compose starts if required config or external secret files are missing."""
    missing = [name for name in REQUIRED_ENVIRONMENT if not environment.get(name, "").strip()]
    if missing:
        raise ConfigurationError("Missing required app stack settings: " + ", ".join(missing))

    for variable in ("STACK_PROJECT", "APP_WEB_NETWORK", "APP_DATA_NETWORK"):
        if not NETWORK_PATTERN.fullmatch(environment[variable]):
            raise ConfigurationError(f"{variable} has an invalid name.")
    if not HOST_PATTERN.fullmatch(environment["PUBLIC_HOST"]):
        raise ConfigurationError("PUBLIC_HOST must be a hostname without scheme, path, or credentials.")
    try:
        ca_bundle = Path(environment["GATEWAY_TLS_CA_FILE"]).expanduser().resolve(strict=True)
    except OSError as error:
        raise ConfigurationError("GATEWAY_TLS_CA_FILE is unavailable or invalid.") from error
    if not ca_bundle.is_file():
        raise ConfigurationError("GATEWAY_TLS_CA_FILE must name a readable CA bundle file.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", environment["KC_BOOTSTRAP_ADMIN_USERNAME"]):
        raise ConfigurationError("KC_BOOTSTRAP_ADMIN_USERNAME has an invalid value.")

    secret_variables = (
        "MYSQL_ROOT_PASSWORD_FILE",
        "API_DB_PASSWORD_FILE",
        "KEYCLOAK_DB_PASSWORD_FILE",
        "KEYCLOAK_CLIENT_SECRET_FILE",
        "KEYCLOAK_ADMIN_PASSWORD_FILE",
    )
    root = repository_root.resolve()
    # Keep the allowlisted location lexical: resolving it would let a symlinked
    # infra/secrets directory bless files elsewhere inside the build context.
    excluded_secrets = root / "infra" / "secrets"
    secret_values: dict[str, str] = {}
    for variable in secret_variables:
        source = Path(environment[variable])
        if not source.is_absolute():
            source = root / source
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(f"Secret file for {variable} is unavailable or invalid.") from error
        try:
            resolved_source.relative_to(root)
        except ValueError:
            pass
        else:
            try:
                resolved_source.relative_to(excluded_secrets)
            except ValueError as error:
                raise ConfigurationError(
                    f"Secret file for {variable} must be outside the build context or under infra/secrets/."
                ) from error
        secret_values[variable] = _secret_value(str(resolved_source), variable)

    if secret_values["API_DB_PASSWORD_FILE"] == DEVELOPMENT_API_DB_PASSWORD:
        raise ConfigurationError("API_DB_PASSWORD_FILE must not use development demo credentials.")
    if secret_values["KEYCLOAK_CLIENT_SECRET_FILE"].strip() == DEVELOPMENT_KEYCLOAK_CLIENT_SECRET:
        raise ConfigurationError("KEYCLOAK_CLIENT_SECRET_FILE must not use the development demo secret.")


def validate_compose_contract(configuration: Mapping[str, object]) -> None:
    """Reject a rendered app stack that violates its network, startup, or mount boundaries."""
    services = configuration.get("services")
    if not isinstance(services, Mapping):
        raise ConfigurationError("Rendered app Compose configuration has no services.")
    required_services = (
        "app-mysql",
        "app-keycloak-db-init",
        "app-init",
        "app-api",
        "app-ui",
        "app-keycloak",
    )
    if any(name not in services for name in required_services):
        raise ConfigurationError("Rendered app Compose configuration is missing a required service.")
    for name in required_services:
        service = services[name]
        if not isinstance(service, Mapping):
            raise ConfigurationError(f"Rendered app Compose service {name} is invalid.")
        if service.get("ports"):
            raise ConfigurationError(f"App service {name} must not publish host ports.")
    provisioner_volumes = services["app-keycloak-db-init"].get("volumes", [])
    if any(
        isinstance(volume, Mapping) and volume.get("type") == "bind"
        for volume in provisioner_volumes
    ):
        raise ConfigurationError(
            "App service app-keycloak-db-init must keep its source and secrets outside bind mounts."
        )
    for name in ("app-init", "app-ui", "app-keycloak"):
        if services[name].get("volumes"):
            raise ConfigurationError(f"App service {name} must keep its source and secrets outside bind mounts.")
    api_volumes = services["app-api"].get("volumes", [])
    api_environment = services["app-api"].get("environment", {})
    if (
        not isinstance(api_environment, Mapping)
        or api_environment.get("REQUESTS_CA_BUNDLE") != "/run/certs/public-ca.pem"
        or not isinstance(api_volumes, list)
        or len(api_volumes) != 1
        or not isinstance(api_volumes[0], Mapping)
        or api_volumes[0].get("type") != "bind"
        or api_volumes[0].get("target") != "/run/certs/public-ca.pem"
        or api_volumes[0].get("read_only") is not True
    ):
        raise ConfigurationError(
            "App service app-api must keep its source and secrets outside bind mounts except for its read-only public CA bundle."
        )

    api_dependencies = services["app-api"].get("depends_on", {})
    if not isinstance(api_dependencies, Mapping) or api_dependencies.get("app-init", {}).get(
        "condition"
    ) != "service_completed_successfully":
        raise ConfigurationError("The API must wait for successful database initialization.")
    api_healthcheck = services["app-api"].get("healthcheck", {})
    api_probe = str(api_healthcheck.get("test", "")) if isinstance(api_healthcheck, Mapping) else ""
    if "/ready" not in api_probe or "/liveness" in api_probe:
        raise ConfigurationError("The API healthcheck must probe /ready for database and identity readiness.")
    keycloak_dependencies = services["app-keycloak"].get("depends_on", {})
    if not isinstance(keycloak_dependencies, Mapping) or keycloak_dependencies.get(
        "app-keycloak-db-init", {}
    ).get("condition") != "service_completed_successfully":
        raise ConfigurationError("Keycloak must wait for its schema provisioning service.")
    if "start-dev" in str(services["app-keycloak"].get("command", "")):
        raise ConfigurationError("Keycloak must use its production start command.")

    networks = configuration.get("networks")
    if not isinstance(networks, Mapping) or not isinstance(networks.get("app_data"), Mapping):
        raise ConfigurationError("The private app data network is missing.")
    if networks["app_data"].get("internal") is not True:
        raise ConfigurationError("The app data network must be internal.")

    legacy_service_names = {
        "legacy-mariadb",
        "legacy-bookstack",
        "legacy-gitea",
        "legacy-share",
    }
    if legacy_service_names.intersection(services):
        validate_legacy_compose_contract(configuration)

    if "gateway" in services:
        validate_gateway_compose_contract(configuration)


def validate_gateway_compose_contract(configuration: Mapping[str, object]) -> None:
    """Require the public gateway to be TLS-only, loopback-bound, and outside data networks."""
    services = configuration.get("services")
    if not isinstance(services, Mapping) or not isinstance(services.get("gateway"), Mapping):
        raise ConfigurationError("Rendered gateway Compose configuration has no gateway service.")
    gateway = services["gateway"]
    ports = gateway.get("ports", [])
    if not isinstance(ports, list) or len(ports) != 1:
        raise ConfigurationError("The gateway must expose one loopback-only TLS port.")
    port = ports[0]
    if not isinstance(port, Mapping) or port.get("target") != 443 or port.get("host_ip") != "127.0.0.1":
        raise ConfigurationError("The gateway TLS port must bind to loopback only.")
    if gateway.get("read_only") is not True or set(gateway.get("cap_drop", [])) != {"ALL"}:
        raise ConfigurationError("The gateway must use a read-only filesystem and drop all capabilities.")
    if set(gateway.get("cap_add", [])) != {
        "CHOWN",
        "DAC_READ_SEARCH",
        "NET_BIND_SERVICE",
        "SETGID",
        "SETUID",
    }:
        raise ConfigurationError(
            "The gateway may add only the required cache ownership, TLS key read, listener bind, and worker-drop capabilities."
        )

    networks = gateway.get("networks")
    required_networks = {"app_web", "legacy_web", "ci_web"}
    if not isinstance(networks, Mapping) or set(networks) != required_networks:
        raise ConfigurationError("The gateway must join only the app, legacy, and CI web networks.")

    environment = gateway.get("environment", {})
    if not isinstance(environment, Mapping) or environment.get("BOOKSTACK_BOOTSTRAP_CONFIRMED") not in {
        "true",
        "false",
    }:
        raise ConfigurationError("BOOKSTACK_BOOTSTRAP_CONFIRMED must be explicitly true or false.")


def validate_legacy_compose_contract(configuration: Mapping[str, object]) -> None:
    """Check persistent legacy services when the rendered file contains them."""
    services = configuration.get("services")
    if not isinstance(services, Mapping):
        raise ConfigurationError("Rendered legacy Compose configuration has no services.")
    required = {"legacy-mariadb", "legacy-bookstack", "legacy-gitea", "legacy-share"}
    if not required.issubset(services):
        raise ConfigurationError("The legacy stack is missing a required service.")

    for name in required:
        service = services[name]
        if not isinstance(service, Mapping):
            raise ConfigurationError(f"Legacy service {name} is invalid.")
        if service.get("ports"):
            raise ConfigurationError(f"Legacy service {name} must not publish host ports.")

    bookstack = services["legacy-bookstack"]
    environment = bookstack.get("environment", {})
    secrets = bookstack.get("secrets", [])
    secret_names = {
        item.get("source") if isinstance(item, Mapping) else item
        for item in secrets
    }
    volumes = bookstack.get("volumes", [])
    if not isinstance(environment, Mapping) or environment.get("FILE__APP_KEY") != "/run/secrets/bookstack_app_key":
        raise ConfigurationError("BookStack must read its explicit APP_KEY from a secret file.")
    if environment.get("FILE__DB_PASSWORD") != "/run/secrets/bookstack_db_password":
        raise ConfigurationError("BookStack must read its database password from a secret file.")
    if not {"bookstack_app_key", "bookstack_db_password"}.issubset(secret_names):
        raise ConfigurationError("BookStack key and database password secrets are required.")
    if not any(
        isinstance(volume, Mapping)
        and volume.get("type") == "volume"
        and volume.get("target") == "/config"
        for volume in volumes
    ):
        raise ConfigurationError("BookStack must persist its /config directory in a named volume.")

    mariadb = services["legacy-mariadb"]
    mariadb_environment = mariadb.get("environment", {})
    mariadb_volumes = mariadb.get("volumes", [])
    if not isinstance(mariadb_environment, Mapping) or not all(
        mariadb_environment.get(name)
        for name in ("MARIADB_ROOT_PASSWORD_FILE", "MARIADB_PASSWORD_FILE")
    ):
        raise ConfigurationError("MariaDB must read its passwords from secret files.")
    if not any(
        isinstance(volume, Mapping)
        and volume.get("type") == "volume"
        and volume.get("target") == "/var/lib/mysql"
        for volume in mariadb_volumes
    ):
        raise ConfigurationError("MariaDB must persist its data in a named volume.")

    gitea = services["legacy-gitea"]
    gitea_environment = gitea.get("environment", {})
    gitea_volumes = gitea.get("volumes", [])
    if not isinstance(gitea_environment, Mapping) or gitea_environment.get(
        "GITEA__database__DB_TYPE"
    ) != "sqlite3":
        raise ConfigurationError("The Gitea test fixture must use its documented fresh SQLite setup.")
    if not any(
        isinstance(volume, Mapping)
        and volume.get("type") == "volume"
        and volume.get("target") == "/data"
        for volume in gitea_volumes
    ):
        raise ConfigurationError("Gitea must persist its conventional /data directory in a named volume.")

    share = services["legacy-share"]
    share_volumes = share.get("volumes", [])
    if not any(
        isinstance(volume, Mapping)
        and volume.get("type") == "bind"
        and volume.get("target") == "/usr/share/nginx/html"
        and volume.get("read_only") is True
        for volume in share_volumes
    ):
        raise ConfigurationError("The share data directory must be mounted read-only.")
    networks = configuration.get("networks")
    if not isinstance(networks, Mapping) or not isinstance(networks.get("legacy_data"), Mapping):
        raise ConfigurationError("The private legacy data network is missing.")
    if networks["legacy_data"].get("internal") is not True:
        raise ConfigurationError("The legacy data network must be internal.")


def validate_legacy_runtime_configuration(
    environment: Mapping[str, str], repository_root: Path
) -> None:
    """Validate explicit legacy secrets and keep host data outside image build contexts."""
    required = (
        "STACK_PROJECT",
        "LEGACY_WEB_NETWORK",
        "LEGACY_DATA_NETWORK",
        "PUBLIC_HOST",
        "BOOKSTACK_URL",
        "GITEA_ROOT_URL",
        "BOOKSTACK_APP_KEY_FILE",
        "BOOKSTACK_DB_PASSWORD_FILE",
        "MARIADB_ROOT_PASSWORD_FILE",
        "SHARE_DATA_DIRECTORY",
    )
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise ConfigurationError("Missing required legacy stack settings: " + ", ".join(missing))
    for variable in ("STACK_PROJECT", "LEGACY_WEB_NETWORK", "LEGACY_DATA_NETWORK"):
        if not NETWORK_PATTERN.fullmatch(environment[variable]):
            raise ConfigurationError(f"{variable} has an invalid name.")
    if not HOST_PATTERN.fullmatch(environment["PUBLIC_HOST"]):
        raise ConfigurationError("PUBLIC_HOST must be a hostname without scheme, path, or credentials.")
    for variable in ("BOOKSTACK_URL", "GITEA_ROOT_URL"):
        parsed = urlsplit(environment[variable])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ConfigurationError(f"{variable} must be an explicit HTTP(S) URL without credentials.")

    root = repository_root.resolve()
    excluded_secrets = root / "infra" / "secrets"
    secret_values: dict[str, str] = {}
    for variable in (
        "BOOKSTACK_APP_KEY_FILE",
        "BOOKSTACK_DB_PASSWORD_FILE",
        "MARIADB_ROOT_PASSWORD_FILE",
    ):
        source = Path(environment[variable])
        if not source.is_absolute():
            source = root / source
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(f"Secret file for {variable} is unavailable or invalid.") from error
        try:
            resolved_source.relative_to(root)
        except ValueError:
            pass
        else:
            try:
                resolved_source.relative_to(excluded_secrets)
            except ValueError as error:
                raise ConfigurationError(
                    f"Secret file for {variable} must be outside the build context or under infra/secrets/."
                ) from error
        if variable == "BOOKSTACK_DB_PASSWORD_FILE":
            try:
                raw_secret = resolved_source.read_bytes()
            except OSError as error:
                raise ConfigurationError(f"Secret file for {variable} is unavailable or invalid.") from error
            if raw_secret.endswith((b"\n", b"\r")):
                raise ConfigurationError(
                    "BOOKSTACK_DB_PASSWORD_FILE must be byte-exact without a trailing newline "
                    "so MariaDB and BookStack read the same password."
                )
        secret_values[variable] = _secret_value(str(resolved_source), variable)

    app_key = secret_values["BOOKSTACK_APP_KEY_FILE"]
    if not app_key.startswith("base64:"):
        raise ConfigurationError("BOOKSTACK_APP_KEY_FILE must contain an explicit Laravel base64 key.")
    try:
        decoded_app_key = base64.b64decode(app_key.removeprefix("base64:"), validate=True)
    except ValueError as error:
        raise ConfigurationError("BOOKSTACK_APP_KEY_FILE must contain a valid Laravel base64 key.") from error
    if len(decoded_app_key) != 32:
        raise ConfigurationError("BOOKSTACK_APP_KEY_FILE must decode to a 32-byte Laravel key.")

    share_directory = Path(environment["SHARE_DATA_DIRECTORY"])
    if not share_directory.is_absolute():
        share_directory = root / share_directory
    try:
        resolved_share = share_directory.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError("SHARE_DATA_DIRECTORY must be an existing external directory.") from error
    if not resolved_share.is_dir():
        raise ConfigurationError("SHARE_DATA_DIRECTORY must be an existing external directory.")
    try:
        resolved_share.relative_to(root)
    except ValueError:
        return
    raise ConfigurationError("SHARE_DATA_DIRECTORY must remain outside the image build context.")


def build_api_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Load runtime secrets and build a correctly encoded MySQL URL in memory."""
    required = (
        "API_DB_HOST",
        "API_DB_NAME",
        "API_DB_USER",
        "API_DB_PASSWORD_FILE",
        "KEYCLOAK_BASE_URL",
        "KEYCLOAK_ISSUER",
        "KEYCLOAK_REALM",
        "KEYCLOAK_CLIENT_ID",
        "KEYCLOAK_CLIENT_SECRET_FILE",
        "KEYCLOAK_AUDIENCE",
    )
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise ConfigurationError("Missing required app runtime settings: " + ", ".join(missing))
    for variable in ("API_DB_NAME", "API_DB_USER"):
        if not NAME_PATTERN.fullmatch(environment[variable]):
            raise ConfigurationError(f"{variable} has an invalid value.")
    if not HOST_PATTERN.fullmatch(environment["API_DB_HOST"]):
        raise ConfigurationError("API_DB_HOST has an invalid value.")

    database_password = _secret_value(environment["API_DB_PASSWORD_FILE"], "API_DB_PASSWORD_FILE")
    client_secret = _secret_value(
        environment["KEYCLOAK_CLIENT_SECRET_FILE"], "KEYCLOAK_CLIENT_SECRET_FILE"
    )
    runtime = {key: value for key, value in environment.items() if not key.endswith("_FILE")}
    runtime["DATABASE_URL"] = (
        "mysql+pymysql://"
        f"{quote(environment['API_DB_USER'], safe='')}:{quote(database_password, safe='')}"
        f"@{environment['API_DB_HOST']}:3306/{environment['API_DB_NAME']}?charset=utf8mb4"
    )
    runtime["KEYCLOAK_CLIENT_SECRET"] = client_secret
    runtime["FLASK_ENV"] = "production"
    return runtime


def _load_pymysql():
    try:
        import pymysql
    except ImportError as error:
        raise ConfigurationError("The app image is missing its MySQL client dependency.") from error
    return pymysql


def provision_keycloak_database(environment: Mapping[str, str]) -> None:
    """Create or refresh only Keycloak's schema and user in the app MySQL instance."""
    required = ("MYSQL_ADMIN_SOCKET", "MYSQL_ROOT_PASSWORD_FILE", "KEYCLOAK_DB_PASSWORD_FILE")
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise ConfigurationError("Missing Keycloak database provisioning settings: " + ", ".join(missing))
    socket_path = Path(environment["MYSQL_ADMIN_SOCKET"])
    if not socket_path.is_absolute():
        raise ConfigurationError("MYSQL_ADMIN_SOCKET must be an absolute path.")
    root_password = _secret_value(environment["MYSQL_ROOT_PASSWORD_FILE"], "MYSQL_ROOT_PASSWORD_FILE")
    keycloak_password = _secret_value(
        environment["KEYCLOAK_DB_PASSWORD_FILE"], "KEYCLOAK_DB_PASSWORD_FILE"
    )
    client = _load_pymysql()
    try:
        connection = client.connect(
            unix_socket=str(socket_path),
            user="root",
            password=root_password,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            autocommit=True,
            charset="utf8mb4",
        )
        with connection, connection.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS `keycloak` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS 'keycloak'@'%%' IDENTIFIED BY %s",
                (keycloak_password,),
            )
            cursor.execute("ALTER USER 'keycloak'@'%%' IDENTIFIED BY %s", (keycloak_password,))
            cursor.execute("GRANT ALL PRIVILEGES ON `keycloak`.* TO 'keycloak'@'%'")
    except ConfigurationError:
        raise
    except client.MySQLError:
        raise ConfigurationError("Keycloak schema provisioning failed; inspect MySQL service health.") from None


def _exec_command(argv: Sequence[str], environment: Mapping[str, str]) -> None:
    runtime = build_api_environment(environment)
    os.execvpe(argv[0], list(argv), runtime)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate env and secret files before Compose")
    validate_parser.add_argument("--env-file", type=Path, required=True)
    validate_parser.add_argument("--compose-file", type=Path, required=True)
    validate_parser.add_argument("--project-directory", type=Path, required=True)
    legacy_parser = subparsers.add_parser(
        "validate-legacy", help="validate legacy stack inputs and its rendered Compose contract"
    )
    legacy_parser.add_argument("--env-file", type=Path, required=True)
    legacy_parser.add_argument("--compose-file", type=Path, required=True)
    legacy_parser.add_argument("--project-directory", type=Path, required=True)
    subparsers.add_parser("run-api", help="run the API with secret-file settings")
    subparsers.add_parser("run-init", help="run Flask's explicit database bootstrap")
    subparsers.add_parser("provision-keycloak-db", help="prepare Keycloak's separate MySQL schema/user")
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "validate":
            root = arguments.project_directory.resolve()
            env_file = arguments.env_file.resolve()
            compose_file = arguments.compose_file.resolve()
            environment = resolve_compose_environment(env_file, root, compose_file)
            validate_runtime_configuration(environment, repository_root=root)
            print("Application stack configuration validated.")
            return 0
        if arguments.command == "validate-legacy":
            root = arguments.project_directory.resolve()
            env_file = arguments.env_file.resolve()
            compose_file = arguments.compose_file.resolve()
            environment = resolve_compose_environment(env_file, root, compose_file)
            validate_legacy_runtime_configuration(environment, repository_root=root)
            rendered = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(root),
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(compose_file),
                    "config",
                    "--format",
                    "json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if rendered.returncode:
                raise ConfigurationError("Docker Compose could not render the legacy stack configuration.")
            validate_legacy_compose_contract(json.loads(rendered.stdout))
            print("Legacy stack configuration validated.")
            return 0
        if arguments.command == "provision-keycloak-db":
            provision_keycloak_database(os.environ)
            return 0
        command = (
            (
                "/app/api/scripts/start-gunicorn.sh",
                "--config",
                "/app/api/gunicorn.conf.py",
                "-b",
                "0.0.0.0:3000",
                "-w",
                "4",
                "--threads",
                "4",
                "app:app",
            )
            if arguments.command == "run-api"
            else ("flask", "--app", "app", "bootstrap-db")
        )
        _exec_command(command, os.environ)
        return 0
    except ConfigurationError as error:
        print(f"App stack configuration error: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("App stack command could not be started.", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
