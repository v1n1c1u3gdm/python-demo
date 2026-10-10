"""Compose contract checks that do not require a Docker daemon in CI jobs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.stack_config import (
    ConfigurationError,
    resolve_compose_environment,
    validate_compose_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose" / "app.yaml"


def valid_compose_model() -> dict[str, object]:
    return {
        "services": {
            "app-mysql": {"volumes": [{"type": "volume", "source": "app_mysql_data"}]},
            "app-keycloak-db-init": {
                "volumes": [{"type": "volume", "source": "app_mysql_socket"}],
                "depends_on": {"app-mysql": {"condition": "service_healthy"}}
            },
            "app-init": {"depends_on": {"app-mysql": {"condition": "service_healthy"}}},
            "app-api": {
                "environment": {"REQUESTS_CA_BUNDLE": "/run/certs/public-ca.pem"},
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/tmp/test-ca.pem",
                        "target": "/run/certs/public-ca.pem",
                        "read_only": True,
                    }
                ],
                "healthcheck": {
                    "test": ["CMD", "python", "-c", "urllib.request.urlopen('/ready')"]
                },
                "depends_on": {"app-init": {"condition": "service_completed_successfully"}},
            },
            "app-ui": {"depends_on": {"app-api": {"condition": "service_started"}}},
            "app-keycloak": {
                "command": ["start"],
                "depends_on": {
                    "app-keycloak-db-init": {"condition": "service_completed_successfully"}
                },
            },
        },
        "networks": {"app_data": {"internal": True}},
    }


def write_compose_env(directory: Path, project_name: str = "contract-test") -> Path:
    values = {
        "STACK_PROJECT": project_name,
        "APP_WEB_NETWORK": f"{project_name}-web",
        "APP_DATA_NETWORK": f"{project_name}-data",
        "PUBLIC_HOST": "identity.example.test",
        "GATEWAY_TLS_CA_FILE": str(directory / "gateway-ca.pem"),
        "KC_BOOTSTRAP_ADMIN_USERNAME": "stack-admin",
        "MYSQL_ROOT_PASSWORD_FILE": str(directory / "mysql-root"),
        "API_DB_PASSWORD_FILE": str(directory / "api-db"),
        "KEYCLOAK_DB_PASSWORD_FILE": str(directory / "keycloak-db"),
        "KEYCLOAK_CLIENT_SECRET_FILE": str(directory / "keycloak-client"),
        "KEYCLOAK_ADMIN_PASSWORD_FILE": str(directory / "keycloak-admin"),
    }
    for variable, value in values.items():
        if variable.endswith("_FILE"):
            Path(value).write_text("synthetic-test-value\n", encoding="utf-8")
    env_file = directory / "stack.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return env_file


class TestRenderedComposeContract(unittest.TestCase):
    def test_keeps_databases_private_and_initializes_before_serving(self) -> None:
        # Arrange
        configuration = valid_compose_model()

        # Act / Assert
        validate_compose_contract(configuration)

    def test_rejects_host_ports_for_private_application_services(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"]["app-mysql"]["ports"] = ["3306:3306"]

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "must not publish host ports"):
            validate_compose_contract(configuration)

    def test_rejects_api_healthcheck_that_skips_identity_readiness(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"]["app-api"]["healthcheck"]["test"] = [
            "CMD",
            "python",
            "-c",
            "urllib.request.urlopen('/liveness')",
        ]

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "must probe /ready"):
            validate_compose_contract(configuration)

    def test_rejects_source_bind_mounts_for_application_containers(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"]["app-api"]["volumes"] = ["../../api:/app/api"]

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "must keep its source"):
            validate_compose_contract(configuration)

    def test_rejects_bind_mounts_for_privileged_database_provisioner(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"]["app-keycloak-db-init"]["volumes"] = [
            {"type": "bind", "source": "./scripts", "target": "/scripts"}
        ]

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "must keep its source"):
            validate_compose_contract(configuration)

    def test_rejects_gateway_without_loopback_tls_binding(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"]["gateway"] = {
            "ports": [{"target": 443, "published": "8443", "host_ip": "127.0.0.1"}],
            "networks": {"app_web": {}, "legacy_web": {}, "ci_web": {}},
            "environment": {
                "BOOKSTACK_BOOTSTRAP_CONFIRMED": "false"
            },
        }
        configuration["services"]["gateway"]["ports"][0]["host_ip"] = "0.0.0.0"

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "gateway.*TLS.*loopback"):
            validate_compose_contract(configuration)

    def test_rejects_legacy_bookstack_without_explicit_key_secret(self) -> None:
        # Arrange
        configuration = valid_compose_model()
        configuration["services"].update(
            {
                "legacy-mariadb": {"volumes": [{"type": "volume", "source": "legacy_mariadb_data"}]},
                "legacy-bookstack": {
                    "environment": {"APP_URL": "https://docs.example.test/bookstack/"},
                    "secrets": ["bookstack_db_password"],
                    "volumes": [{"type": "volume", "source": "legacy_bookstack_config"}],
                    "depends_on": {"legacy-mariadb": {"condition": "service_healthy"}},
                },
                "legacy-gitea": {"volumes": [{"type": "volume", "source": "legacy_gitea_data"}]},
                "legacy-share": {
                    "volumes": [{"type": "bind", "source": "${SHARE_DATA_DIRECTORY}", "read_only": True}]
                },
            }
        )

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "BookStack.*APP_KEY"):
            validate_compose_contract(configuration)


@unittest.skipUnless(
    os.environ.get("RUN_STACK_COMPOSE_PROOF") == "1" and shutil.which("docker"),
    "Set RUN_STACK_COMPOSE_PROOF=1 to render the real Compose file.",
)
class TestRealComposeFile(unittest.TestCase):
    def test_real_compose_file_matches_the_stack_contract(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory(prefix="python-demo-compose-contract-") as directory:
            env_file = write_compose_env(Path(directory))

            # Act
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "--project-directory",
                    str(REPOSITORY_ROOT),
                    "-f",
                    str(COMPOSE_FILE),
                    "config",
                    "--format",
                    "json",
                ],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

        # Assert
        configuration = json.loads(completed.stdout)
        validate_compose_contract(configuration)
        for service_name in ("app-keycloak-db-init", "app-init", "app-api", "app-ui", "app-keycloak"):
            build = configuration["services"][service_name]["build"]
            self.assertTrue(
                (Path(build["context"]) / build["dockerfile"]).is_file(),
                f"{service_name} has an invalid rendered build context",
            )

    def test_compose_resolves_dotenv_interpolation_and_shell_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-compose-precedence-") as directory:
            # Arrange
            temporary_root = Path(directory)
            env_file = write_compose_env(temporary_root)
            secret_directory = temporary_root / "secrets-without-values"
            secret_directory.mkdir()
            (secret_directory / "api-db").write_text("synthetic-test-value\n", encoding="utf-8")
            env_lines = env_file.read_text(encoding="utf-8").splitlines()
            env_lines = [line for line in env_lines if not line.startswith("API_DB_PASSWORD_FILE=")]
            env_lines.extend(
                (
                    f"STACK_SECRET_DIRECTORY={secret_directory}",
                    "API_DB_PASSWORD_FILE=${STACK_SECRET_DIRECTORY}/api-db",
                )
            )
            env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
            shell_override = str(temporary_root / "shell-override")
            isolated_environment = os.environ.copy()
            isolated_environment.pop("API_DB_PASSWORD_FILE", None)
            isolated_environment.pop("STACK_SECRET_DIRECTORY", None)

            # Act: with no variable override, Compose expands the dotenv reference.
            with patch.dict(os.environ, isolated_environment, clear=True):
                dotenv_values = resolve_compose_environment(env_file, REPOSITORY_ROOT, COMPOSE_FILE)

            # Act: the shell value wins over the dotenv value according to Compose precedence.
            with patch.dict(
                os.environ,
                {**isolated_environment, "API_DB_PASSWORD_FILE": shell_override},
                clear=True,
            ):
                shell_values = resolve_compose_environment(env_file, REPOSITORY_ROOT, COMPOSE_FILE)

            # Assert
            self.assertEqual(
                dotenv_values["API_DB_PASSWORD_FILE"], str(secret_directory / "api-db")
            )
            self.assertEqual(shell_values["API_DB_PASSWORD_FILE"], shell_override)


@unittest.skipUnless(
    os.environ.get("RUN_STACK_COMPOSE_PROOF") == "1" and shutil.which("docker"),
    "Set RUN_STACK_COMPOSE_PROOF=1 to run the isolated Docker recreation proof.",
)
class TestStackPersistenceRecreation(unittest.TestCase):
    def test_api_and_keycloak_records_survive_mysql_recreation_and_init_repeat(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-persist-") as directory:
            # Arrange
            temporary_root = Path(directory)
            project_name = f"task1-persist-{temporary_root.name[-10:]}"
            env_file = write_compose_env(temporary_root, project_name=project_name)
            environment = os.environ.copy()
            for name in (
                "STACK_PROJECT",
                "APP_WEB_NETWORK",
                "APP_DATA_NETWORK",
                "PUBLIC_HOST",
                "KC_BOOTSTRAP_ADMIN_USERNAME",
                "MYSQL_ROOT_PASSWORD_FILE",
                "API_DB_PASSWORD_FILE",
                "KEYCLOAK_DB_PASSWORD_FILE",
                "KEYCLOAK_CLIENT_SECRET_FILE",
                "KEYCLOAK_ADMIN_PASSWORD_FILE",
            ):
                environment.pop(name, None)
            environment["APP_ENV_FILE"] = str(env_file)

            def compose(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--project-directory",
                        str(REPOSITORY_ROOT),
                        "--env-file",
                        str(env_file),
                        "-f",
                        str(COMPOSE_FILE),
                        *arguments,
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )

            def assert_command_succeeded(result: subprocess.CompletedProcess[str]) -> None:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            api_record_script = (
                'import os, sys; sys.path.insert(0,"/opt/python-demo"); '
                'from stack_config import build_api_environment; '
                'from sqlalchemy import create_engine, text; '
                'engine=create_engine(build_api_environment(os.environ)["DATABASE_URL"]); '
                'connection=engine.connect(); transaction=connection.begin(); '
                'connection.execute(text("CREATE TABLE IF NOT EXISTS task1_recreation_probe '
                '(id INT PRIMARY KEY, marker VARCHAR(80) NOT NULL) ENGINE=InnoDB")); '
                'connection.execute(text("INSERT INTO task1_recreation_probe (id,marker) '
                'VALUES (1,:marker) ON DUPLICATE KEY UPDATE marker=VALUES(marker)"), '
                '{"marker":"api-record"}); transaction.commit(); '
                'print(connection.execute(text("SELECT marker FROM task1_recreation_probe '
                'WHERE id=1")).scalar_one())'
            )
            keycloak_record_script = (
                'import pymysql; from pathlib import Path; '
                'password=Path("/run/secrets/mysql_root_password").read_text().rstrip("\\n"); '
                'connection=pymysql.connect(unix_socket="/var/run/mysqld/mysqld.sock", '
                'user="root",password=password,database="keycloak",autocommit=True); '
                'cursor=connection.cursor(); '
                'cursor.execute("CREATE TABLE IF NOT EXISTS task1_recreation_probe '
                '(id INT PRIMARY KEY, marker VARCHAR(80) NOT NULL) ENGINE=InnoDB"); '
                'cursor.execute("INSERT INTO task1_recreation_probe (id,marker) VALUES (1,%s) '
                'ON DUPLICATE KEY UPDATE marker=VALUES(marker)",("keycloak-record",)); '
                'cursor.execute("SELECT marker FROM task1_recreation_probe WHERE id=1"); '
                'print(cursor.fetchone()[0])'
            )
            api_query_script = (
                'import os, sys; sys.path.insert(0,"/opt/python-demo"); '
                'from stack_config import build_api_environment; '
                'from sqlalchemy import create_engine, text; '
                'engine=create_engine(build_api_environment(os.environ)["DATABASE_URL"]); '
                'print(engine.connect().execute(text("SELECT marker FROM task1_recreation_probe '
                'WHERE id=1")).scalar_one())'
            )
            keycloak_query_script = (
                'import pymysql; from pathlib import Path; '
                'password=Path("/run/secrets/mysql_root_password").read_text().rstrip("\\n"); '
                'connection=pymysql.connect(unix_socket="/var/run/mysqld/mysqld.sock", '
                'user="root",password=password,database="keycloak"); '
                'cursor=connection.cursor(); '
                'cursor.execute("SELECT marker FROM task1_recreation_probe WHERE id=1"); '
                'print(cursor.fetchone()[0])'
            )

            try:
                # Act: initialize both databases and write independent synthetic records.
                started = subprocess.run(
                    ["bash", str(REPOSITORY_ROOT / "infra" / "scripts" / "up-app.sh"), "--build"],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )
                assert_command_succeeded(started)
                api_written = compose(
                    "run", "--rm", "--no-deps", "--entrypoint", "python", "app-api", "-c", api_record_script
                )
                assert_command_succeeded(api_written)
                self.assertIn("api-record", api_written.stdout)
                keycloak_written = compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "app-keycloak-db-init",
                    "-c",
                    keycloak_record_script,
                )
                assert_command_succeeded(keycloak_written)
                self.assertIn("keycloak-record", keycloak_written.stdout)

                # Act: remove and recreate only the MySQL container, retaining project volumes.
                removed = compose("rm", "--stop", "--force", "app-mysql")
                assert_command_succeeded(removed)
                recreated = compose("up", "--detach", "--wait", "app-mysql")
                assert_command_succeeded(recreated)

                # Act: repeat the explicit API migration/seed init and Keycloak provisioning.
                for service in ("app-init", "app-keycloak-db-init"):
                    repeated_init = compose("run", "--rm", "--no-deps", service)
                    assert_command_succeeded(repeated_init)

                api_after = compose(
                    "run", "--rm", "--no-deps", "--entrypoint", "python", "app-api", "-c", api_query_script
                )
                keycloak_after = compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "app-keycloak-db-init",
                    "-c",
                    keycloak_query_script,
                )

                # Assert
                assert_command_succeeded(api_after)
                assert_command_succeeded(keycloak_after)
                self.assertIn("api-record", api_after.stdout)
                self.assertIn("keycloak-record", keycloak_after.stdout)
            finally:
                # Cleanup only the uniquely named synthetic project and its own volumes.
                compose("down", "--volumes")
