"""Contracts for joining the local stacks without starting a Docker daemon."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def exporter_state_initializer() -> dict[str, object]:
    return {
        "image": "python-demo-ci-exporter:portable",
        "user": "0:0",
        "entrypoint": ["python", "-m", "ci.prepare_exporter_state"],
        "network_mode": "none",
        "restart": "no",
        "profiles": ["logs"],
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "FOWNER"],
        "volumes": [{"type": "volume", "source": "exporter-state",
                     "target": "/var/lib/ci-log-exporter"}],
    }


class TestMetricsConfiguration(unittest.TestCase):
    def test_portable_api_accepts_an_explicit_otel_metrics_setting(self) -> None:
        # Arrange
        compose = (REPOSITORY_ROOT / "infra" / "compose" / "app.yaml").read_text(encoding="utf-8")

        # Act
        setting = next(line.strip() for line in compose.splitlines() if "OTEL_METRICS_ENABLED:" in line)

        # Assert
        self.assertIn("${OTEL_METRICS_ENABLED:-false}", setting)


class TestLocalStartSafety(unittest.TestCase):
    def test_runner_profile_is_rejected_before_preflight_or_compose(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-runner-block-") as directory:
            # Arrange
            root = Path(directory)
            docker_directory = root / "bin"
            docker_directory.mkdir()
            marker = root / "docker-called"
            docker = docker_directory / "docker"
            docker.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 0\n", encoding="utf-8")
            docker.chmod(0o755)
            environment = {
                "PATH": f"{docker_directory}:/usr/bin:/bin",
                "COMPOSE_PROFILES": "logs,runner",
            }

            # Act
            result = subprocess.run(
                [str(REPOSITORY_ROOT / "infra/scripts/up-local.sh")],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            # Assert
            self.assertEqual(result.returncode, 2)
            self.assertIn("runner is blocked", result.stderr)
            self.assertFalse(marker.exists())


class TestStackPreflightErrors(unittest.TestCase):
    def test_token_preflight_rejects_nonfiles_without_invoking_docker(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            _validate_exporter_token_file,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-token-not-file-") as directory:
            # Arrange
            token_directory = Path(directory)

            # Act / Assert
            with patch("ci.local_orchestration.subprocess.run") as docker, self.assertRaisesRegex(
                ConfigurationError, "regular file"
            ):
                _validate_exporter_token_file(token_directory)
            docker.assert_not_called()

    def test_exporter_token_preflight_requires_private_nonempty_runtime_readable_file(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            _validate_exporter_token_file,
        )

        def validate_synthetic_token(
            content: str, mode: int, owner: int = 65532, probe_status: int = 0
        ) -> Mock:
            with tempfile.TemporaryDirectory(prefix="python-demo-token-preflight-") as directory:
                token_file = Path(directory) / "credential"
                token_file.write_text(content, encoding="utf-8")
                token_file.chmod(mode)
                real_stat = Path.stat

                def synthetic_owner(path: Path, *args, **kwargs):
                    result = real_stat(path, *args, **kwargs)
                    if path == token_file:
                        return SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner)
                    return result

                with (
                    patch.object(Path, "stat", synthetic_owner),
                    patch(
                        "ci.local_orchestration.subprocess.run",
                        side_effect=[
                            subprocess.CompletedProcess([], 0, "", ""),
                            subprocess.CompletedProcess([], probe_status, "", ""),
                        ],
                    ) as docker,
                ):
                    _validate_exporter_token_file(token_file)
                return docker

        # Arrange / Act / Assert
        calls = validate_synthetic_token("synthetic-token\n", 0o600).call_args_list
        command = calls[1].args[0]
        self.assertIn("--pull=never", command)
        self.assertIn("--network", command)
        self.assertEqual(command[command.index("--user") + 1], "65532:65532")
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertNotIn("synthetic-token", " ".join(command))
        for content, mode, owner, probe_status, reason in (
            ("synthetic-secret-value", 0o644, 65532, 0, "private"),
            ("", 0o600, 65532, 13, "empty or malformed"),
            ("synthetic-token", 0o600, 1000, 0, "UID 65532"),
            ("synthetic\ttoken", 0o600, 65532, 13, "empty or malformed"),
        ):
            with self.subTest(mode=oct(mode), owner=owner), self.assertRaisesRegex(
                ConfigurationError, reason
            ) as raised:
                validate_synthetic_token(content, mode, owner, probe_status)
            self.assertNotIn("synthetic-secret-value", str(raised.exception))

    def test_token_probe_timeout_removes_only_its_unique_ephemeral_container(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            _validate_exporter_token_file,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-token-timeout-") as directory:
            # Arrange
            token_file = Path(directory) / "credential"
            token_file.write_text("synthetic-only", encoding="utf-8")
            token_file.chmod(0o600)
            real_stat = Path.stat

            def synthetic_owner(path: Path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == token_file:
                    return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=65532)
                return result

            with (
                patch.object(Path, "stat", synthetic_owner),
                patch(
                    "ci.local_orchestration.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.TimeoutExpired(["docker", "run"], 15),
                        subprocess.CompletedProcess([], 0, "", ""),
                    ],
                ) as docker,
                self.assertRaisesRegex(ConfigurationError, "timed out"),
            ):
                # Act / Assert
                _validate_exporter_token_file(token_file)

            probe_command = docker.call_args_list[1].args[0]
            probe_name = probe_command[probe_command.index("--name") + 1]
            self.assertRegex(probe_name, r"^python-demo-token-probe-[0-9a-f]{32}$")
            self.assertEqual(
                docker.call_args_list[2].args[0],
                ["docker", "rm", "--force", probe_name],
            )

    def test_token_probe_timeout_remains_clear_when_exact_container_cleanup_fails(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            _validate_exporter_token_file,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-token-cleanup-error-") as directory:
            # Arrange
            token_file = Path(directory) / "credential"
            token_file.write_text("synthetic-only", encoding="utf-8")
            token_file.chmod(0o600)
            real_stat = Path.stat

            def synthetic_owner(path: Path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == token_file:
                    return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=65532)
                return result

            with (
                patch.object(Path, "stat", synthetic_owner),
                patch(
                    "ci.local_orchestration.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.TimeoutExpired(["docker", "run"], 15),
                        OSError("synthetic cleanup failure"),
                    ],
                ) as docker,
                self.assertRaisesRegex(ConfigurationError, "readability check timed out"),
            ):
                # Act / Assert
                _validate_exporter_token_file(token_file)

            cleanup_command = docker.call_args_list[2].args[0]
            self.assertEqual(cleanup_command[:3], ["docker", "rm", "--force"])

    def test_token_probe_reports_image_and_runtime_failures_without_docker_output(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            _validate_exporter_token_file,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-token-probe-errors-") as directory:
            # Arrange
            token_file = Path(directory) / "credential"
            token_file.write_text("synthetic-only", encoding="utf-8")
            token_file.chmod(0o600)
            real_stat = Path.stat

            def synthetic_owner(path: Path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == token_file:
                    return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=65532)
                return result

            # Act / Assert: unavailable images and inspector failures stop before a token mount.
            image_failures = (
                (
                    [subprocess.CompletedProcess([], 1, "", "synthetic-docker-error")],
                    "image is unavailable",
                ),
                ([OSError("synthetic executable failure")], "verify the pinned"),
                ([subprocess.TimeoutExpired(["docker", "image", "inspect"], 15)], "verify the pinned"),
            )
            for outcomes, message in image_failures:
                with self.subTest(message=message), patch.object(Path, "stat", synthetic_owner), patch(
                    "ci.local_orchestration.subprocess.run", side_effect=outcomes
                ) as docker, self.assertRaisesRegex(ConfigurationError, message) as raised:
                    _validate_exporter_token_file(token_file)
                self.assertEqual(docker.call_count, 1)
                self.assertNotIn("synthetic-docker-error", str(raised.exception))

            # Probe failures distinguish runtime readability/encoding from daemon failures.
            probe_failures = (
                (11, "not readable by exporter UID"),
                (12, "readable UTF-8"),
                (99, "could not complete"),
            )
            for status, message in probe_failures:
                with (
                    self.subTest(status=status),
                    patch.object(Path, "stat", synthetic_owner),
                    patch(
                        "ci.local_orchestration.subprocess.run",
                        side_effect=[
                            subprocess.CompletedProcess([], 0, "", ""),
                            subprocess.CompletedProcess([], status, "", "synthetic-probe-error"),
                        ],
                    ) as docker,
                    self.assertRaisesRegex(ConfigurationError, message) as raised,
                ):
                    _validate_exporter_token_file(token_file)
                self.assertEqual(docker.call_count, 2)
                self.assertNotIn("synthetic-probe-error", str(raised.exception))

            with (
                patch.object(Path, "stat", synthetic_owner),
                patch(
                    "ci.local_orchestration.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "", ""),
                        OSError("synthetic docker execution failure"),
                    ],
                ),
                self.assertRaisesRegex(ConfigurationError, "could not run the isolated token readability check"),
            ):
                _validate_exporter_token_file(token_file)

    def test_preflight_rejects_public_token_before_rendering_any_stack(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_local_stacks

        with tempfile.TemporaryDirectory(prefix="python-demo-token-before-render-") as directory:
            # Arrange
            root = Path(directory) / "checkout"
            root.mkdir()
            app_env, ci_env = root / "app.env", root / "ci.env"
            app_env.write_text("APP=synthetic\n", encoding="utf-8")
            ci_env.write_text("CI=synthetic\n", encoding="utf-8")
            token_file = Path(directory) / "credential"
            token_file.write_text("synthetic-token\n", encoding="utf-8")
            token_file.chmod(0o644)
            with (
                patch("ci.local_orchestration.resolve_compose_environment", side_effect=[{}, {}, {
                    "GATEWAY_TLS_CERT_FILE": "cert", "GATEWAY_TLS_KEY_FILE": "key",
                }]),
                patch("ci.local_orchestration.validate_runtime_configuration"),
                patch("ci.local_orchestration.validate_legacy_runtime_configuration"),
                patch("ci.local_orchestration.validate_gateway_tls_files"),
                patch("ci.local_orchestration.load_control_config"),
                patch("ci.local_orchestration._compose_environment", return_value={
                    "CI_WOODPECKER_TOKEN_FILE": str(token_file),
                }),
                patch("ci.local_orchestration._rendered_compose") as render,
                self.assertRaisesRegex(ConfigurationError, "permissions must be private"),
            ):
                # Act
                validate_local_stacks(root, app_env, ci_env)

            # Assert
            render.assert_not_called()

    def test_missing_environment_file_returns_safe_configuration_error(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_local_stacks

        with tempfile.TemporaryDirectory(prefix="python-demo-stack-env-missing-") as directory:
            # Arrange
            root = REPOSITORY_ROOT
            missing_env = Path(directory) / "missing.env"

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "environment file is unavailable"):
                validate_local_stacks(root, missing_env, missing_env)


class TestComposePreflightHelpers(unittest.TestCase):
    @patch("ci.local_orchestration.subprocess.run")
    def test_compose_render_clears_inherited_profiles_and_builds_explicit_command(self, run: Mock) -> None:
        from ci.local_orchestration import _run_compose

        # Arrange
        run.return_value = subprocess.CompletedProcess([], 0, "rendered", "")
        environment = {**os.environ, "COMPOSE_PROFILES": "runner"}

        # Act
        with patch("ci.local_orchestration.os.environ", environment):
            result = _run_compose(
                REPOSITORY_ROOT,
                (Path("app.env"),),
                (Path("app.yaml"),),
                "config",
                "--format",
                "json",
            )

        # Assert
        self.assertEqual(result.stdout, "rendered")
        command, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(command[-5:], ["-f", "app.yaml", "config", "--format", "json"])
        self.assertNotIn("COMPOSE_PROFILES", options["env"])
        self.assertIn("--env-file", command)

    @patch("ci.local_orchestration.subprocess.run", side_effect=FileNotFoundError("docker"))
    def test_compose_executable_failure_is_reported_as_safe_preflight_error(self, _run: Mock) -> None:
        from ci.local_orchestration import ConfigurationError, _run_compose

        # Arrange / Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "Docker Compose is unavailable"):
            _run_compose(REPOSITORY_ROOT, (), (), "config")

    @patch("ci.local_orchestration.subprocess.run")
    def test_nonzero_compose_render_does_not_expose_command_output(self, run: Mock) -> None:
        from ci.local_orchestration import ConfigurationError, _run_compose

        # Arrange
        run.return_value = subprocess.CompletedProcess([], 1, "private render", "bad secret")

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "no services were started") as raised:
            _run_compose(REPOSITORY_ROOT, (), (), "config")
        self.assertNotIn("bad secret", str(raised.exception))

    @patch("ci.local_orchestration._run_compose")
    def test_effective_environment_parser_preserves_values_after_the_first_equals(self, run: Mock) -> None:
        from ci.local_orchestration import _compose_environment

        # Arrange
        run.return_value = subprocess.CompletedProcess(
            [], 0, "PUBLIC_HOST=demo.test\nSYNTHETIC_VALUE=one=two\nmalformed\n", ""
        )

        # Act
        values = _compose_environment(REPOSITORY_ROOT, (), ())

        # Assert
        self.assertEqual(values, {"PUBLIC_HOST": "demo.test", "SYNTHETIC_VALUE": "one=two"})

    @patch("ci.local_orchestration._run_compose")
    def test_invalid_rendered_json_and_nonobject_render_are_rejected(self, run: Mock) -> None:
        from ci.local_orchestration import ConfigurationError, _rendered_compose

        # Arrange / Act / Assert
        run.return_value = subprocess.CompletedProcess([], 0, "not-json", "")
        with self.assertRaisesRegex(ConfigurationError, "invalid local stack model"):
            _rendered_compose(REPOSITORY_ROOT, (), ())
        run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        with self.assertRaisesRegex(ConfigurationError, "invalid local stack model"):
            _rendered_compose(REPOSITORY_ROOT, (), ())

    def test_full_stack_preflight_validates_then_renders_every_profile_before_mutation(self) -> None:
        from ci.local_orchestration import validate_local_stacks

        with tempfile.TemporaryDirectory(prefix="python-demo-preflight-unit-") as directory:
            # Arrange
            root = Path(directory) / "checkout"
            root.mkdir()
            app_env, ci_env = root / "app.env", root / "ci.env"
            app_env.write_text("APP=synthetic\n", encoding="utf-8")
            ci_env.write_text("CI=synthetic\n", encoding="utf-8")
            token_file = Path(directory) / "ci-token"
            token_file.write_text("synthetic-token\n", encoding="utf-8")
            telemetry = {
                "services": {
                    "app-api": {
                        "environment": {
                            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://otel-collector:4318/v1/metrics",
                            "OTEL_METRICS_ENABLED": "true",
                        }
                    },
                    "otel-collector": {
                        "profiles": ["telemetry"],
                        "networks": {"app_web": {"aliases": ["otel-collector"]}},
                    },
                }
            }
            ci_default = {"services": {"server": {}}}
            ci_with_logs = {"services": {"server": {}, "exporter": {}}}

            with (
                patch("ci.local_orchestration.resolve_compose_environment", side_effect=[
                    {"application": "synthetic"},
                    {"legacy": "synthetic"},
                    {"GATEWAY_TLS_CERT_FILE": "cert.pem", "GATEWAY_TLS_KEY_FILE": "key.pem"},
                ]) as resolve,
                patch("ci.local_orchestration.validate_runtime_configuration") as validate_app,
                patch("ci.local_orchestration.validate_legacy_runtime_configuration") as validate_legacy,
                patch("ci.local_orchestration.validate_gateway_tls_files") as validate_tls,
                patch(
                    "ci.local_orchestration._compose_environment",
                    return_value={"CI_WOODPECKER_TOKEN_FILE": str(token_file)},
                ) as compose_env,
                patch("ci.local_orchestration.load_control_config") as load_control,
                patch("ci.local_orchestration._validate_exporter_token_file") as validate_token,
                patch(
                    "ci.local_orchestration._rendered_compose",
                    side_effect=[{}, telemetry, {}, {}, ci_default, ci_with_logs],
                ) as render,
                patch("ci.local_orchestration.validate_compose_contract") as validate_app_model,
                patch("ci.local_orchestration.validate_telemetry_compose_contract") as validate_metrics,
                patch("ci.local_orchestration.validate_legacy_compose_contract") as validate_legacy_model,
                patch("ci.local_orchestration.validate_gateway_contract") as validate_gateway,
                patch("ci.local_orchestration.validate_ci_compose_contract") as validate_ci,
            ):
                # Act
                validate_local_stacks(root, app_env, ci_env)

            # Assert
            self.assertEqual(resolve.call_count, 3)
            validate_app.assert_called_once()
            validate_legacy.assert_called_once()
            validate_tls.assert_called_once_with("cert.pem", "key.pem")
            compose_env.assert_called_once()
            load_control.assert_called_once()
            validate_token.assert_called_once_with(token_file)
            self.assertEqual(render.call_count, 6)
            for validator in (
                validate_app_model,
                validate_metrics,
                validate_legacy_model,
                validate_gateway,
                validate_ci,
            ):
                validator.assert_called_once()

    def test_full_stack_preflight_rejects_a_token_inside_the_checkout(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_local_stacks

        with tempfile.TemporaryDirectory(prefix="python-demo-preflight-token-") as directory:
            # Arrange
            root = Path(directory) / "checkout"
            root.mkdir()
            app_env, ci_env = root / "app.env", root / "ci.env"
            app_env.write_text("APP=synthetic\n", encoding="utf-8")
            ci_env.write_text("CI=synthetic\n", encoding="utf-8")
            token_file = root / "token"
            token_file.write_text("synthetic-token\n", encoding="utf-8")
            with (
                patch("ci.local_orchestration.resolve_compose_environment", side_effect=[{}, {}, {
                    "GATEWAY_TLS_CERT_FILE": "cert", "GATEWAY_TLS_KEY_FILE": "key",
                }]),
                patch("ci.local_orchestration.validate_runtime_configuration"),
                patch("ci.local_orchestration.validate_legacy_runtime_configuration"),
                patch("ci.local_orchestration.validate_gateway_tls_files"),
                patch("ci.local_orchestration.load_control_config"),
                patch(
                    "ci.local_orchestration._compose_environment",
                    return_value={"CI_WOODPECKER_TOKEN_FILE": str(token_file)},
                ),
                self.assertRaisesRegex(ConfigurationError, "outside the repository"),
            ):
                # Act
                validate_local_stacks(root, app_env, ci_env)

    def test_default_runner_profile_is_rejected_before_logs_profile_render(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_local_stacks

        with tempfile.TemporaryDirectory(prefix="python-demo-preflight-runner-") as directory:
            # Arrange
            root = Path(directory) / "checkout"
            root.mkdir()
            app_env, ci_env = root / "app.env", root / "ci.env"
            app_env.write_text("APP=synthetic\n", encoding="utf-8")
            ci_env.write_text("CI=synthetic\n", encoding="utf-8")
            token_file = Path(directory) / "token"
            token_file.write_text("synthetic-token\n", encoding="utf-8")
            renders = [{}, {"services": {}}, {}, {}, {"services": {"agent": {}}}]
            with (
                patch("ci.local_orchestration.resolve_compose_environment", side_effect=[{}, {}, {
                    "GATEWAY_TLS_CERT_FILE": "cert", "GATEWAY_TLS_KEY_FILE": "key",
                }]),
                patch("ci.local_orchestration.validate_runtime_configuration"),
                patch("ci.local_orchestration.validate_legacy_runtime_configuration"),
                patch("ci.local_orchestration.validate_gateway_tls_files"),
                patch("ci.local_orchestration.load_control_config"),
                patch("ci.local_orchestration._compose_environment", return_value={
                    "CI_WOODPECKER_TOKEN_FILE": str(token_file),
                }),
                patch("ci.local_orchestration._validate_exporter_token_file"),
                patch("ci.local_orchestration._rendered_compose", side_effect=renders) as render,
                patch("ci.local_orchestration.validate_compose_contract"),
                patch("ci.local_orchestration.validate_telemetry_compose_contract"),
                patch("ci.local_orchestration.validate_legacy_compose_contract"),
                patch("ci.local_orchestration.validate_gateway_contract"),
                self.assertRaisesRegex(ConfigurationError, "runner must remain disabled"),
            ):
                # Act
                validate_local_stacks(root, app_env, ci_env)
            self.assertEqual(render.call_count, 5)


class TestRenderedStackContracts(unittest.TestCase):
    def test_ci_contract_requires_nonnetworked_owner_setup_before_nonroot_exporter(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_ci_compose_contract,
        )

        # Arrange
        valid = {"services": {
            "server": {"environment": {"WOODPECKER_DEFAULT_APPROVAL_MODE": "all_events"},
                       "networks": {"ci-control": {}, "ci-web": {"aliases": ["ci-server"]}}},
            "exporter-state-init": exporter_state_initializer(),
            "exporter": {
                "build": {},
                "depends_on": {"exporter-state-init": {"condition": "service_completed_successfully"}},
                "volumes": [{"type": "bind", "target": "/run/woodpecker-control/api-token", "read_only": True}],
            },
        }, "networks": {"ci-web": {"external": True}}}

        # Act / Assert
        validate_ci_compose_contract(valid)
        invalid = json.loads(json.dumps(valid))
        invalid["services"]["exporter-state-init"]["network_mode"] = "host"
        with self.assertRaisesRegex(ConfigurationError, "state initializer must not join a network"):
            validate_ci_compose_contract(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["services"]["exporter"]["depends_on"] = {}
        with self.assertRaisesRegex(ConfigurationError, "wait for state ownership preparation"):
            validate_ci_compose_contract(invalid)

    def test_gateway_contract_rejects_incomplete_and_unapproved_network_models(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_gateway_contract

        # Arrange
        valid = {
            "services": {"gateway": {"ports": [{"host_ip": "127.0.0.1"}], "networks": {
                "app_web": {}, "legacy_web": {}, "ci_web": {},
            }}},
            "networks": {},
        }

        # Act / Assert
        for invalid in ({}, {"services": {}, "networks": {}}, {
            **valid, "services": {"gateway": {"ports": [{"host_ip": "127.0.0.1"}], "networks": {
                "app_web": {}, "legacy_web": {}, "ci_web": {}, "host": {},
            }}}
        }):
            with self.subTest(invalid=invalid), self.assertRaises(ConfigurationError):
                validate_gateway_contract(invalid)

    def test_telemetry_contract_rejects_missing_profile_endpoint_and_network_alias(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_telemetry_compose_contract,
        )

        # Arrange
        valid = {"services": {
            "app-api": {"environment": {
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://otel-collector:4318/v1/metrics",
                "OTEL_METRICS_ENABLED": "true",
            }},
            "otel-collector": {"profiles": ["telemetry"], "networks": {
                "app_web": {"aliases": ["otel-collector"]}
            }},
        }}
        invalid_models = [
            {}, {"services": {}},
            {"services": {**valid["services"], "otel-collector": {
                **valid["services"]["otel-collector"], "profiles": []
            }}},
            {"services": {**valid["services"], "app-api": {
                "environment": {"OTEL_METRICS_ENABLED": "false"}
            }}},
            {"services": {**valid["services"], "otel-collector": {
                **valid["services"]["otel-collector"], "networks": {"app_web": {"aliases": []}}
            }}},
        ]

        # Act / Assert
        for model in invalid_models:
            with self.subTest(model=model), self.assertRaises(ConfigurationError):
                validate_telemetry_compose_contract(model)

    def test_ci_contract_rejects_control_plane_drift_and_host_bind_mounts(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_ci_compose_contract,
        )

        # Arrange
        valid = {"services": {
            "server": {"environment": {"WOODPECKER_DEFAULT_APPROVAL_MODE": "all_events"},
                       "networks": {"ci-control": {}, "ci-web": {"aliases": ["ci-server"]}}},
            "exporter-state-init": exporter_state_initializer(),
            "exporter": {"build": {}, "depends_on": {
                "exporter-state-init": {"condition": "service_completed_successfully"}
            }, "volumes": [
                {"type": "bind", "target": "/run/woodpecker-control/api-token", "read_only": True}
            ]},
        }, "networks": {"ci-web": {"external": True}}}
        invalid_models = [
            {}, {"services": {}, "networks": {}},
            {"services": {**valid["services"], "server": {"networks": {}}}, "networks": valid["networks"]},
            {"services": {**valid["services"], "server": {
                **valid["services"]["server"], "networks": {"ci-control": {}, "ci-web": {"aliases": []}}
            }}, "networks": valid["networks"]},
            {"services": {**valid["services"], "server": {
                **valid["services"]["server"], "ports": ["8080:8000"]
            }}, "networks": valid["networks"]},
            {"services": valid["services"], "networks": {"ci-web": {"external": False}}},
            {"services": {**valid["services"], "agent": {"profiles": [], "volumes": []}}, "networks": valid["networks"]},
            {"services": {**valid["services"], "agent": {"profiles": ["runner"], "volumes": []}}, "networks": valid["networks"]},
            {"services": {**valid["services"], "exporter": {
                **valid["services"]["exporter"], "volumes": []
            }}, "networks": valid["networks"]},
            {"services": {**valid["services"], "exporter": {
                **valid["services"]["exporter"], "volumes": [
                    {"type": "bind", "target": "/run/woodpecker-control/api-token", "read_only": False}
                ]
            }}, "networks": valid["networks"]},
            {"services": {**valid["services"], "job": {"volumes": [{"type": "bind", "target": "/secret"}]}}, "networks": valid["networks"]},
        ]

        # Act / Assert
        validate_ci_compose_contract(valid)
        for model in invalid_models:
            with self.subTest(model=model), self.assertRaises(ConfigurationError):
                validate_ci_compose_contract(model)


class TestLocalOrchestrationCLI(unittest.TestCase):
    def test_cli_reports_success_after_preflight(self) -> None:
        from ci.local_orchestration import main

        # Arrange
        output = StringIO()
        arguments = ["--project-directory", "/tmp/project", "--app-env", "/tmp/app.env", "--ci-env", "/tmp/ci.env"]
        with patch("ci.local_orchestration.validate_local_stacks") as preflight, redirect_stdout(output):
            # Act
            result = main(arguments)

        # Assert
        self.assertEqual(result, 0)
        preflight.assert_called_once_with(Path("/tmp/project"), Path("/tmp/app.env"), Path("/tmp/ci.env"))
        self.assertIn("configuration validated", output.getvalue())

    def test_cli_reports_safe_configuration_error_without_success_output(self) -> None:
        from ci.local_orchestration import ConfigurationError, main

        # Arrange
        output, errors = StringIO(), StringIO()
        arguments = ["--project-directory", "/tmp/project", "--app-env", "/tmp/app.env", "--ci-env", "/tmp/ci.env"]
        with patch("ci.local_orchestration.validate_local_stacks", side_effect=ConfigurationError("synthetic failure")), \
                redirect_stdout(output), redirect_stderr(errors):
            # Act
            result = main(arguments)

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("synthetic failure", errors.getvalue())


    def test_gateway_must_bind_only_loopback_and_join_the_approved_web_networks(self) -> None:
        from ci.local_orchestration import ConfigurationError, validate_gateway_contract

        # Arrange
        gateway = {
            "services": {"gateway": {"ports": [{"host_ip": "127.0.0.1"}], "networks": {
                "app_web": {}, "legacy_web": {}, "ci_web": {},
            }}},
            "networks": {"app_web": {}, "legacy_web": {}, "ci_web": {}},
        }

        # Act / Assert
        validate_gateway_contract(gateway)
        gateway["services"]["gateway"]["ports"][0]["host_ip"] = "0.0.0.0"
        with self.assertRaisesRegex(ConfigurationError, "loopback"):
            validate_gateway_contract(gateway)

    def test_telemetry_requires_opt_in_internal_collector_and_explicit_endpoint(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_telemetry_compose_contract,
        )

        # Arrange
        configuration = {"services": {
            "app-api": {"environment": {
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://otel-collector:4318/v1/metrics",
                "OTEL_METRICS_ENABLED": "true",
            }},
            "otel-collector": {
                "profiles": ["telemetry"],
                "networks": {"app_web": {"aliases": ["otel-collector"]}},
            },
        }}

        # Act / Assert
        validate_telemetry_compose_contract(configuration)
        configuration["services"]["otel-collector"]["ports"] = ["4318:4318"]
        with self.assertRaisesRegex(ConfigurationError, "must not publish host ports"):
            validate_telemetry_compose_contract(configuration)

    def test_gateway_tls_pair_is_accepted_only_when_certificate_and_key_match(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_gateway_tls_files,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-tls-pair-") as directory:
            # Arrange
            root = Path(directory)
            certificate, key = root / "certificate.pem", root / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(certificate),
            ], check=True, capture_output=True, text=True)

            # Act / Assert
            validate_gateway_tls_files(str(certificate), str(key))
            mismatched_key = root / "mismatched-key.pem"
            mismatched_key.write_text("invalid key", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "TLS certificate or private key"):
                validate_gateway_tls_files(str(certificate), str(mismatched_key))


class TestGatewayTLSPreflight(unittest.TestCase):
    def test_missing_tls_key_fails_before_stack_startup(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_gateway_tls_files,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-local-tls-") as directory:
            # Arrange
            root = Path(directory)
            certificate = root / "fullchain.pem"
            certificate.write_text("synthetic certificate", encoding="utf-8")
            key = root / "missing-key.pem"

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "TLS certificate or private key"):
                validate_gateway_tls_files(str(certificate), str(key))

    def test_invalid_certificate_fails_without_echoing_file_contents(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_gateway_tls_files,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-local-tls-") as directory:
            # Arrange
            root = Path(directory)
            marker = "never-print-this-test-key"
            certificate = root / "fullchain.pem"
            certificate.write_text(marker, encoding="utf-8")
            key = root / "private-key.pem"
            key.write_text("not a private key", encoding="utf-8")

            # Act / Assert
            with self.assertRaises(ConfigurationError) as raised:
                validate_gateway_tls_files(str(certificate), str(key))

            self.assertNotIn(marker, str(raised.exception))


class TestPortableCIComposeContract(unittest.TestCase):
    def test_keeps_runner_opt_in_and_jobs_without_control_or_application_mounts(self) -> None:
        from ci.local_orchestration import validate_ci_compose_contract

        # Arrange
        configuration = {
            "services": {
                "server": {
                    "environment": {"WOODPECKER_DEFAULT_APPROVAL_MODE": "all_events"},
                    "networks": {
                        "ci-control": {},
                        "ci-web": {"aliases": ["ci-server"]},
                    }
                },
                "exporter-state-init": exporter_state_initializer(),
                "agent": {
                    "profiles": ["runner"],
                    "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                },
                "exporter": {
                    "build": {"context": "/repo", "dockerfile": "infra/ci-exporter.Dockerfile"},
                    "depends_on": {"exporter-state-init": {"condition": "service_completed_successfully"}},
                    "volumes": [
                        {"type": "bind", "target": "/run/woodpecker-control/api-token", "read_only": True},
                        {"type": "volume", "target": "/var/lib/ci-log-exporter"},
                    ],
                },
            },
            "networks": {"ci-web": {"external": True}},
        }

        # Act
        validate_ci_compose_contract(configuration)

        # Assert
        self.assertIn("runner", configuration["services"]["agent"]["profiles"])

    def test_rejects_exporter_source_mounts_or_jobs_with_production_access(self) -> None:
        from ci.local_orchestration import (
            ConfigurationError,
            validate_ci_compose_contract,
        )

        # Arrange
        configuration = {
            "services": {
                "server": {
                    "environment": {"WOODPECKER_DEFAULT_APPROVAL_MODE": "all_events"},
                    "networks": {"ci-control": {}, "ci-web": {"aliases": ["ci-server"]}},
                },
                "exporter-state-init": exporter_state_initializer(),
                "agent": {"profiles": ["runner"], "volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
                "exporter": {
                    "build": {"context": "/repo", "dockerfile": "infra/ci-exporter.Dockerfile"},
                    "depends_on": {"exporter-state-init": {"condition": "service_completed_successfully"}},
                    "volumes": [
                        {"type": "bind", "target": "/opt/ci/log_exporter.py", "read_only": True}
                    ],
                },
                "job": {"volumes": ["/var/run/docker.sock:/var/run/docker.sock", "/app-data:/data"]},
            },
            "networks": {"ci-web": {"external": True}},
        }

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "exporter.*code inside its image"):
            validate_ci_compose_contract(configuration)

        # Act / Assert
        configuration["services"]["exporter"]["volumes"] = [
            {"type": "bind", "target": "/run/woodpecker-control/api-token", "read_only": True}
        ]
        with self.assertRaisesRegex(ConfigurationError, "CI jobs must not mount"):
            validate_ci_compose_contract(configuration)


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required for the render-only proof")
class TestRenderedPortableCI(unittest.TestCase):
    def test_ci_overlay_renders_without_agent_and_uses_image_built_exporter(self) -> None:
        from ci.local_orchestration import validate_ci_compose_contract

        with tempfile.TemporaryDirectory(prefix="python-demo-ci-render-") as directory:
            # Arrange
            root = REPOSITORY_ROOT
            temporary = Path(directory)
            token = temporary / "api-token"
            token.write_text("synthetic-token", encoding="utf-8")
            env_file = temporary / "ci.env"
            values = {
                "WOODPECKER_HOST": "https://ci.example.test/ci",
                "WOODPECKER_GITHUB_CLIENT": "synthetic-client",
                "WOODPECKER_GITHUB_SECRET": "synthetic-secret",
                "WOODPECKER_AGENT_SECRET": "synthetic-agent-secret",
                "WOODPECKER_ADMIN": "synthetic-maintainer",
                "CI_WOODPECKER_REPO_ID": "42",
                "CI_WOODPECKER_TOKEN_FILE": str(token),
                "CI_WEB_NETWORK": "python-demo-ci-render-web",
                "CI_CONTROL_NETWORK": "python-demo-ci-render-control",
            }
            env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
            command = [
                "docker",
                "compose",
                "--project-directory",
                str(root),
                "--env-file",
                str(env_file),
                "-f",
                str(root / "ci" / "compose.yaml"),
                "-f",
                str(root / "infra" / "compose" / "ci.yaml"),
                "--profile",
                "logs",
                "config",
                "--format",
                "json",
            ]

            # Act
            result = subprocess.run(
                command,
                cwd=root,
                env={"PATH": os.environ["PATH"]},
                check=True,
                capture_output=True,
                text=True,
            )
            configuration = json.loads(result.stdout)
            validate_ci_compose_contract(configuration)

            # Assert
            server = configuration["services"]["server"]
            self.assertFalse(server.get("ports"))
            self.assertEqual(server["networks"]["ci-web"]["aliases"], ["ci-server"])
            self.assertNotIn("agent", configuration["services"])
            exporter = configuration["services"]["exporter"]
            initializer = configuration["services"]["exporter-state-init"]
            self.assertEqual(exporter["build"]["dockerfile"], "infra/ci-exporter.Dockerfile")
            self.assertEqual(initializer["user"], "0:0")
            self.assertEqual(initializer["network_mode"], "none")
            self.assertEqual(set(initializer["cap_add"]), {"CHOWN", "FOWNER"})
            self.assertEqual(
                exporter["depends_on"]["exporter-state-init"]["condition"],
                "service_completed_successfully",
            )
            self.assertFalse(any(volume.get("target") == "/opt/ci/log_exporter.py" for volume in exporter["volumes"]))
