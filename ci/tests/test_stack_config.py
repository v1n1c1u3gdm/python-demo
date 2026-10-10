"""Configuration and secret-handling contracts for the app stack."""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Self
from unittest.mock import patch
from urllib.parse import urlsplit

from ci.stack_config import (
    ConfigurationError,
    build_api_environment,
    parse_environment_file,
    provision_keycloak_database,
    resolve_compose_environment,
    validate_runtime_configuration,
)


def write_secret(path: Path, value: str) -> str:
    path.write_text(value + "\n", encoding="utf-8")
    return str(path)


def valid_environment(directory: Path) -> dict[str, str]:
    secret_directory = directory / "infra" / "secrets"
    secret_directory.mkdir(parents=True, exist_ok=True)
    ca_bundle = directory / "gateway-ca.pem"
    ca_bundle.write_text("synthetic public CA bundle", encoding="utf-8")
    return {
        "STACK_PROJECT": "contract-test",
        "APP_WEB_NETWORK": "contract-test-web",
        "APP_DATA_NETWORK": "contract-test-data",
        "PUBLIC_HOST": "identity.example.test",
        "GATEWAY_TLS_CA_FILE": str(ca_bundle),
        "KC_BOOTSTRAP_ADMIN_USERNAME": "stack-admin",
        "MYSQL_ROOT_PASSWORD_FILE": write_secret(secret_directory / "mysql-root", "root secret @/value"),
        "API_DB_PASSWORD_FILE": write_secret(secret_directory / "api-db", "api-secret:@/"),
        "KEYCLOAK_DB_PASSWORD_FILE": write_secret(secret_directory / "keycloak-db", "KC sql '; secret"),
        "KEYCLOAK_CLIENT_SECRET_FILE": write_secret(
            secret_directory / "keycloak-client", "client-secret-value"
        ),
        "KEYCLOAK_ADMIN_PASSWORD_FILE": write_secret(
            secret_directory / "keycloak-admin", "admin-secret-value"
        ),
    }


class TestEnvironmentFileParsing(unittest.TestCase):
    def test_ignores_comments_and_preserves_external_secret_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-env-") as directory:
            # Arrange
            env_file = Path(directory) / "stack.env"
            env_file.write_text(
                "# local-only\nPUBLIC_HOST=identity.example.test\n"
                "API_DB_PASSWORD_FILE=infra/secrets/api_db\n",
                encoding="utf-8",
            )

            # Act
            values = parse_environment_file(env_file)

            # Assert
            self.assertEqual(
                values,
                {
                    "PUBLIC_HOST": "identity.example.test",
                    "API_DB_PASSWORD_FILE": "infra/secrets/api_db",
                },
            )

    def test_uses_compose_effective_environment_including_shell_precedence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-compose-env-") as directory:
            # Arrange
            root = Path(directory)
            completed = subprocess.CompletedProcess(
                args=["docker", "compose"],
                returncode=0,
                stdout="API_DB_PASSWORD_FILE=/shell/override\nPUBLIC_HOST=resolved.example.test\n",
                stderr="",
            )

            # Act
            with patch("ci.stack_config.subprocess.run", return_value=completed) as run_compose:
                effective = resolve_compose_environment(
                    root / "stack.env", root, root / "infra" / "compose" / "app.yaml"
                )

            # Assert
            self.assertEqual(effective["API_DB_PASSWORD_FILE"], "/shell/override")
            self.assertEqual(effective["PUBLIC_HOST"], "resolved.example.test")
            self.assertIn("config", run_compose.call_args.args[0])
            self.assertIn("--environment", run_compose.call_args.args[0])


class TestRuntimeConfiguration(unittest.TestCase):
    def test_preflight_demo_values_match_api_production_rejections(self) -> None:
        # Arrange
        repository_root = Path(__file__).resolve().parents[2]
        module = ast.parse((repository_root / "api" / "config.py").read_text(encoding="utf-8"))
        constants: dict[str, object] = {}
        for statement in module.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        try:
                            constants[target.id] = ast.literal_eval(statement.value)
                        except (ValueError, TypeError):
                            continue

        # Act
        database_password = urlsplit(str(constants["DEVELOPMENT_DATABASE_URL"])).password

        # Assert
        self.assertEqual(database_password, "2u8y-c0d3")
        self.assertEqual(constants["DEVELOPMENT_KEYCLOAK_SECRET"], "python-demo-api-secret")

    def test_missing_secret_rejects_configuration_without_revealing_other_secret_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-config-") as directory:
            # Arrange
            root = Path(directory)
            environment = valid_environment(root)
            secret = "must-never-appear-in-diagnostics"
            Path(environment["KEYCLOAK_CLIENT_SECRET_FILE"]).write_text(secret, encoding="utf-8")
            Path(environment["MYSQL_ROOT_PASSWORD_FILE"]).unlink()

            # Act / Assert
            with self.assertRaises(ConfigurationError) as raised:
                validate_runtime_configuration(environment, repository_root=root)

            # Assert
            self.assertIn("MYSQL_ROOT_PASSWORD_FILE", str(raised.exception))
            self.assertNotIn(secret, str(raised.exception))

    def test_rejects_secret_paths_that_resolve_to_unexcluded_build_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-secret-boundary-") as directory:
            # Arrange
            root = Path(directory) / "repository"
            root.mkdir()
            environment = valid_environment(root)
            unsafe_secret = root / "api" / "private" / "password"
            unsafe_secret.parent.mkdir(parents=True)
            write_secret(unsafe_secret, "synthetic-secret")
            outside_alias = Path(directory) / "external-alias"
            outside_alias.symlink_to(unsafe_secret)
            environment["API_DB_PASSWORD_FILE"] = str(outside_alias)

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "must be outside the build context"):
                validate_runtime_configuration(environment, repository_root=root)

    def test_rejects_excluded_directory_symlink_into_unexcluded_build_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-secret-directory-link-") as directory:
            # Arrange
            root = Path(directory) / "repository"
            root.mkdir()
            environment = valid_environment(root)
            excluded_directory = root / "infra" / "secrets"
            for path in excluded_directory.iterdir():
                path.unlink()
            excluded_directory.rmdir()
            unsafe_directory = root / "api" / "private"
            unsafe_directory.mkdir(parents=True)
            for secret_name in (
                "mysql-root",
                "api-db",
                "keycloak-db",
                "keycloak-client",
                "keycloak-admin",
            ):
                write_secret(unsafe_directory / secret_name, "synthetic-secret")
            excluded_directory.symlink_to(unsafe_directory, target_is_directory=True)
            environment["API_DB_PASSWORD_FILE"] = str(excluded_directory / "api-db")

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "must be outside the build context"):
                validate_runtime_configuration(environment, repository_root=root)

    def test_rejects_api_demo_database_password_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-demo-db-") as directory:
            # Arrange
            root = Path(directory)
            environment = valid_environment(root)
            Path(environment["API_DB_PASSWORD_FILE"]).write_text("2u8y-c0d3\n", encoding="utf-8")

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "API_DB_PASSWORD_FILE.*demo credentials"):
                validate_runtime_configuration(environment, repository_root=root)

    def test_rejects_api_demo_keycloak_client_secret_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-demo-client-") as directory:
            # Arrange
            root = Path(directory)
            environment = valid_environment(root)
            Path(environment["KEYCLOAK_CLIENT_SECRET_FILE"]).write_text(
                "python-demo-api-secret\n", encoding="utf-8"
            )

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "KEYCLOAK_CLIENT_SECRET_FILE.*demo secret"):
                validate_runtime_configuration(environment, repository_root=root)

    def test_api_runtime_encodes_database_password_and_loads_client_secret_in_memory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-runtime-") as directory:
            # Arrange
            environment = valid_environment(Path(directory))
            environment.update(
                {
                    "API_DB_HOST": "app-mysql",
                    "API_DB_NAME": "python_demo",
                    "API_DB_USER": "python_demo_api",
                    "KEYCLOAK_BASE_URL": "http://app-keycloak:8080/auth",
                    "KEYCLOAK_ISSUER": "https://identity.example.test/auth/realms/python-demo",
                    "KEYCLOAK_REALM": "python-demo",
                    "KEYCLOAK_CLIENT_ID": "python-demo-api",
                    "KEYCLOAK_AUDIENCE": "python-demo-api",
                }
            )
            raw_database_password = "api-secret:@/"

            # Act
            runtime = build_api_environment(environment)

            # Assert
            self.assertEqual(
                runtime["DATABASE_URL"],
                "mysql+pymysql://python_demo_api:api-secret%3A%40%2F@app-mysql:3306/"
                "python_demo?charset=utf8mb4",
            )
            self.assertNotIn(raw_database_password, runtime["DATABASE_URL"])
            self.assertEqual(runtime["KEYCLOAK_CLIENT_SECRET"], "client-secret-value")
            self.assertNotIn("KEYCLOAK_CLIENT_SECRET_FILE", runtime)

    def test_keycloak_password_is_a_bound_value_and_does_not_change_the_api_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-provision-") as directory:
            # Arrange
            environment = valid_environment(Path(directory))
            raw_password = "KC sql '; DROP DATABASE python_demo; --"
            Path(environment["KEYCLOAK_DB_PASSWORD_FILE"]).write_text(raw_password, encoding="utf-8")
            calls: list[tuple[str, tuple[str, ...], str]] = []
            connection_options: list[dict[str, object]] = []

            class Cursor:
                def __enter__(self) -> Self:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def execute(self, statement: str, parameters: tuple[str, ...] = ()) -> None:
                    calls.append((statement, parameters, "cursor"))

            class Connection:
                def __enter__(self) -> Self:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def cursor(self) -> Cursor:
                    return Cursor()

            class FakePyMySQL:
                @staticmethod
                def connect(**kwargs: object) -> Connection:
                    connection_options.append(kwargs)
                    calls.append(("connect", (str(kwargs["password"]),), "connection"))
                    return Connection()

            # Act
            with patch("ci.stack_config._load_pymysql", return_value=FakePyMySQL):
                provision_keycloak_database(
                    {
                        "MYSQL_ADMIN_SOCKET": "/var/run/mysqld/mysqld.sock",
                        "MYSQL_ROOT_PASSWORD_FILE": environment["MYSQL_ROOT_PASSWORD_FILE"],
                        "KEYCLOAK_DB_PASSWORD_FILE": environment["KEYCLOAK_DB_PASSWORD_FILE"],
                    }
                )

            # Assert
            statements = [statement for statement, _, kind in calls if kind == "cursor"]
            self.assertEqual(
                statements[0],
                "CREATE DATABASE IF NOT EXISTS `keycloak` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci",
            )
            self.assertTrue(any("CREATE USER IF NOT EXISTS 'keycloak'@'%%'" in sql for sql in statements))
            self.assertTrue(any("ALTER USER 'keycloak'@'%%'" in sql for sql in statements))
            self.assertTrue(any("GRANT ALL PRIVILEGES ON `keycloak`.*" in sql for sql in statements))
            self.assertTrue(all("python_demo" not in sql for sql in statements))
            self.assertEqual(connection_options[0].get("unix_socket"), "/var/run/mysqld/mysqld.sock")
            self.assertNotIn("host", connection_options[0])
            self.assertEqual(
                [parameters for _, parameters, kind in calls if kind == "cursor" and parameters],
                [(raw_password,), (raw_password,)],
            )
            self.assertTrue(all(raw_password not in sql for sql, _, _ in calls))


class TestAppUpPreflight(unittest.TestCase):
    def test_missing_configuration_stops_before_compose_up_is_called(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-preflight-") as directory:
            # Arrange
            root = Path(__file__).resolve().parents[2]
            root_path = Path(directory)
            docker_marker = root_path / "docker-was-called"
            fake_docker = root_path / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                f"  *\" up \"*) touch '{docker_marker}' ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            env_file = root_path / "empty.env"
            env_file.write_text("", encoding="utf-8")
            process_environment = {
                "PATH": f"{root_path}:{os.environ.get('PATH', '')}",
                "APP_ENV_FILE": str(env_file),
            }

            # Act
            completed = subprocess.run(
                ["bash", "infra/scripts/up-app.sh"],
                cwd=root,
                env=process_environment,
                capture_output=True,
                text=True,
                check=False,
            )

            # Assert
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("STACK_PROJECT", completed.stderr)
            self.assertFalse(docker_marker.exists())

    def test_shell_secret_override_is_validated_before_compose_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-override-") as directory:
            # Arrange
            root = Path(__file__).resolve().parents[2]
            temporary_root = Path(directory)
            environment = valid_environment(temporary_root)
            env_file = temporary_root / "stack.env"
            env_file.write_text(
                "".join(f"{name}={value}\n" for name, value in environment.items()),
                encoding="utf-8",
            )
            invalid_override = write_secret(temporary_root / "shell-override", "")
            docker_marker = temporary_root / "docker-up-was-called"
            docker_stub = temporary_root / "docker"
            docker_stub.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                "  *\" config --environment \"*) cat \"$COMPOSE_ENVIRONMENT_OUTPUT\" ;;\n"
                "  *\" up \"*) touch \"$DOCKER_UP_MARKER\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            docker_stub.chmod(0o755)
            effective_environment = dict(environment)
            effective_environment["API_DB_PASSWORD_FILE"] = invalid_override
            compose_environment_output = temporary_root / "compose-environment"
            compose_environment_output.write_text(
                "".join(f"{name}={value}\n" for name, value in effective_environment.items()),
                encoding="utf-8",
            )
            process_environment = {
                "PATH": f"{temporary_root}:{os.environ.get('PATH', '')}",
                "APP_ENV_FILE": str(env_file),
                "API_DB_PASSWORD_FILE": invalid_override,
                "COMPOSE_ENVIRONMENT_OUTPUT": str(compose_environment_output),
                "DOCKER_UP_MARKER": str(docker_marker),
            }

            # Act
            completed = subprocess.run(
                ["bash", "infra/scripts/up-app.sh"],
                cwd=root,
                env=process_environment,
                capture_output=True,
                text=True,
                check=False,
            )

            # Assert
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(docker_marker.exists())

    def test_known_demo_credential_is_rejected_before_compose_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-demo-preflight-") as directory:
            # Arrange
            root = Path(__file__).resolve().parents[2]
            temporary_root = Path(directory)
            environment = valid_environment(temporary_root)
            Path(environment["API_DB_PASSWORD_FILE"]).write_text("2u8y-c0d3\n", encoding="utf-8")
            env_file = temporary_root / "stack.env"
            env_file.write_text(
                "".join(f"{name}={value}\n" for name, value in environment.items()),
                encoding="utf-8",
            )
            docker_marker = temporary_root / "docker-up-was-called"
            docker_stub = temporary_root / "docker"
            effective_environment = temporary_root / "compose-environment"
            effective_environment.write_text(
                "".join(f"{name}={value}\n" for name, value in environment.items()),
                encoding="utf-8",
            )
            docker_stub.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                "  *\" config --environment \"*) cat \"$COMPOSE_ENVIRONMENT_OUTPUT\" ;;\n"
                f"  *\" up \"*) touch '{docker_marker}' ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            docker_stub.chmod(0o755)
            process_environment = {
                "PATH": f"{temporary_root}:{os.environ.get('PATH', '')}",
                "APP_ENV_FILE": str(env_file),
                "COMPOSE_ENVIRONMENT_OUTPUT": str(effective_environment),
            }

            # Act
            completed = subprocess.run(
                ["bash", "infra/scripts/up-app.sh"],
                cwd=root,
                env=process_environment,
                capture_output=True,
                text=True,
                check=False,
            )

            # Assert
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("demo credentials", completed.stderr)
            self.assertNotIn("2u8y-c0d3", completed.stderr)
            self.assertFalse(docker_marker.exists())


class TestContainerCommandDispatch(unittest.TestCase):
    def test_run_api_and_run_init_dispatch_with_secret_file_environment(self) -> None:
        from ci.stack_config import main

        # Arrange
        runtime = {"DATABASE_URL": "mysql+pymysql://synthetic", "KEYCLOAK_CLIENT_SECRET": "synthetic"}
        with patch("ci.stack_config.build_api_environment", return_value=runtime), patch(
            "ci.stack_config.os.execvpe"
        ) as exec_command:
            # Act
            api_result = main(["run-api"])
            init_result = main(["run-init"])

        # Assert
        self.assertEqual((api_result, init_result), (0, 0))
        self.assertEqual(exec_command.call_args_list[0].args[0], "/app/api/scripts/start-gunicorn.sh")
        self.assertIn("app:app", exec_command.call_args_list[0].args[1])
        self.assertEqual(exec_command.call_args_list[1].args[0], "flask")
        self.assertEqual(exec_command.call_args_list[1].args[1][-1], "bootstrap-db")
        self.assertEqual(exec_command.call_args_list[1].args[2], runtime)

    def test_run_commands_keep_configuration_errors_safe_and_exec_errors_bounded(self) -> None:
        from contextlib import redirect_stderr
        from io import StringIO

        from ci.stack_config import ConfigurationError, main

        # Arrange / Act
        errors = StringIO()
        with patch("ci.stack_config.build_api_environment", side_effect=ConfigurationError("missing setting")), \
                redirect_stderr(errors):
            configuration_result = main(["run-api"])
        self.assertEqual(configuration_result, 2)
        self.assertIn("missing setting", errors.getvalue())

        errors = StringIO()
        with patch("ci.stack_config.build_api_environment", return_value={}), patch(
            "ci.stack_config.os.execvpe", side_effect=OSError("synthetic")
        ), redirect_stderr(errors):
            exec_result = main(["run-init"])

        # Assert
        self.assertEqual(exec_result, 126)
        self.assertIn("could not be started", errors.getvalue())


class TestComposeEnvironmentFailures(unittest.TestCase):
    def test_parser_rejects_bad_assignments_and_variable_names(self) -> None:
        for contents, expected in (("not-an-assignment\n", "assignment"), ("9BAD=value\n", "variable name")):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                # Arrange
                env_file = Path(directory) / "stack.env"
                env_file.write_text(contents, encoding="utf-8")

                # Act / Assert
                with self.assertRaisesRegex(ConfigurationError, expected):
                    parse_environment_file(env_file)

    def test_missing_required_runtime_values_fail_before_secret_reads(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "Missing required app stack settings"):
            validate_runtime_configuration({}, repository_root=Path("/tmp/no-secrets-read"))

    def test_compose_resolution_failure_does_not_expose_process_output(self) -> None:
        with patch(
            "ci.stack_config.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "synthetic token", "private error"),
        ):
            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "could not resolve") as raised:
                resolve_compose_environment(Path("app.env"), Path("/tmp/project"), Path("app.yaml"))
            self.assertNotIn("private error", str(raised.exception))
