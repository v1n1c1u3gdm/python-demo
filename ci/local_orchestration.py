"""Preflight contracts for the local multi-stack Compose deployment."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import stat
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from ci.control_config import ControlConfigError, load_control_config
from ci.stack_config import (
    ConfigurationError as StackConfigurationError,
)
from ci.stack_config import (
    resolve_compose_environment,
    validate_compose_contract,
    validate_legacy_compose_contract,
    validate_legacy_runtime_configuration,
    validate_runtime_configuration,
)


class ConfigurationError(ValueError):
    """Raised when a stack cannot be safely rendered or started."""


_TOKEN_PROBE_IMAGE = (
    "docker.io/library/python:3.14.7-slim@sha256:"
    "51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d"
)
_TOKEN_PROBE_TIMEOUT_SECONDS = 15


def _validate_exporter_token_file(token_file: Path) -> None:
    """Validate token metadata locally and its content as the exporter runtime user."""
    if not token_file.is_file():
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE must name a regular file.")
    metadata = token_file.stat()
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077:
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE permissions must be private (0600 or stricter).")
    if metadata.st_uid != 65532 or not mode & 0o400:
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE must be readable by UID 65532 and owned by that UID.")

    try:
        image = subprocess.run(
            ["docker", "image", "inspect", _TOKEN_PROBE_IMAGE],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TOKEN_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConfigurationError("Docker could not verify the pinned token-validation image.") from error
    if image.returncode:
        raise ConfigurationError(
            "The pinned Python token-validation image is unavailable; pull it before retrying local preflight."
        )

    probe_name = f"python-demo-token-probe-{uuid.uuid4().hex}"
    probe_script = (
        "from pathlib import Path\n"
        "try:\n    token=Path('/run/token').read_text(encoding='utf-8').strip()\n"
        "except PermissionError:\n    raise SystemExit(11)\n"
        "except (OSError, UnicodeError):\n    raise SystemExit(12)\n"
        "raise SystemExit(0 if token and not any(ord(char)<32 for char in token) else 13)\n"
    )
    command = [
        "docker", "run", "--pull=never", "--rm", "--name", probe_name,
        "--network", "none", "--read-only", "--user", "65532:65532",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--volume", f"{token_file}:/run/token:ro",
        _TOKEN_PROBE_IMAGE, "python", "-c", probe_script,
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_TOKEN_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        try:
            subprocess.run(
                ["docker", "rm", "--force", probe_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=_TOKEN_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise ConfigurationError("The isolated token readability check timed out.") from error
    except OSError as error:
        raise ConfigurationError("Docker could not run the isolated token readability check.") from error

    if result.returncode == 11:
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE is not readable by exporter UID 65532.")
    if result.returncode == 12:
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE must contain readable UTF-8 text.")
    if result.returncode == 13:
        raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE is empty or malformed.")
    if result.returncode:
        raise ConfigurationError("Docker could not complete the isolated token readability check.")


def _run_compose(
    repository_root: Path,
    environment_files: tuple[Path, ...],
    compose_files: tuple[Path, ...],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--project-directory", str(repository_root)]
    for environment_file in environment_files:
        command.extend(("--env-file", str(environment_file)))
    for compose_file in compose_files:
        command.extend(("-f", str(compose_file)))
    command.extend(arguments)
    try:
        environment = os.environ.copy()
        environment.pop("COMPOSE_PROFILES", None)
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, cwd=repository_root, env=environment
        )
    except OSError as error:
        raise ConfigurationError("Docker Compose is unavailable for the local stack preflight.") from error
    if result.returncode:
        raise ConfigurationError("A local Compose stack could not be rendered; no services were started.")
    return result


def _compose_environment(
    repository_root: Path, environment_files: tuple[Path, ...], compose_files: tuple[Path, ...]
) -> dict[str, str]:
    result = _run_compose(
        repository_root, environment_files, compose_files, "config", "--environment"
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name] = value
    return values


def _rendered_compose(
    repository_root: Path,
    environment_files: tuple[Path, ...],
    compose_files: tuple[Path, ...],
    *,
    profiles: tuple[str, ...] = (),
) -> dict[str, object]:
    profile_arguments = tuple(argument for profile in profiles for argument in ("--profile", profile))
    result = _run_compose(
        repository_root,
        environment_files,
        compose_files,
        *profile_arguments,
        "config",
        "--format",
        "json",
    )
    try:
        rendered = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ConfigurationError("Docker Compose returned an invalid local stack model.") from error
    if not isinstance(rendered, dict):
        raise ConfigurationError("Docker Compose returned an invalid local stack model.")
    return rendered


def validate_local_stacks(repository_root: Path, app_env_file: Path, ci_env_file: Path) -> None:
    """Validate every stack, secret, route, and TLS pair before the first container mutation."""
    root = repository_root.resolve()
    try:
        app_env = app_env_file.resolve(strict=True)
        ci_env = ci_env_file.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError("Local application or CI environment file is unavailable.") from error
    app_compose = root / "infra" / "compose" / "app.yaml"
    legacy_compose = root / "infra" / "compose" / "legacy.yaml"
    gateway_compose = root / "infra" / "compose" / "gateway.yaml"
    ci_compose = (root / "ci" / "compose.yaml", root / "infra" / "compose" / "ci.yaml")

    try:
        app_environment = resolve_compose_environment(app_env, root, app_compose)
        validate_runtime_configuration(app_environment, repository_root=root)
        legacy_environment = resolve_compose_environment(app_env, root, legacy_compose)
        validate_legacy_runtime_configuration(legacy_environment, repository_root=root)
        gateway_environment = resolve_compose_environment(app_env, root, gateway_compose)
        validate_gateway_tls_files(
            gateway_environment["GATEWAY_TLS_CERT_FILE"],
            gateway_environment["GATEWAY_TLS_KEY_FILE"],
        )
        ci_environment = _compose_environment(root, (app_env, ci_env), ci_compose)
        load_control_config(ci_environment)
        try:
            token_file = Path(ci_environment["CI_WOODPECKER_TOKEN_FILE"]).expanduser().resolve(strict=True)
        except OSError as error:
            raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE is unavailable or invalid.") from error
        try:
            token_file.relative_to(root)
        except ValueError:
            pass
        else:
            raise ConfigurationError("CI_WOODPECKER_TOKEN_FILE must be outside the repository checkout.")
        _validate_exporter_token_file(token_file)
    except (StackConfigurationError, ControlConfigError, KeyError, OSError) as error:
        raise ConfigurationError(str(error)) from error

    # Render every model and check its contract before any command can run `up`.
    app = _rendered_compose(root, (app_env,), (app_compose,))
    validate_compose_contract(app)
    telemetry = _rendered_compose(root, (app_env,), (app_compose,), profiles=("telemetry",))
    validate_telemetry_compose_contract(telemetry)
    legacy = _rendered_compose(root, (app_env,), (legacy_compose,))
    validate_legacy_compose_contract(legacy)
    gateway = _rendered_compose(root, (app_env,), (gateway_compose,))
    validate_gateway_contract(gateway)
    ci_default = _rendered_compose(root, (app_env, ci_env), ci_compose)
    default_services = ci_default.get("services", {})
    if not isinstance(default_services, Mapping) or "agent" in default_services:
        raise ConfigurationError("The Woodpecker runner must remain disabled in the default stack.")
    ci = _rendered_compose(root, (app_env, ci_env), ci_compose, profiles=("logs",))
    validate_ci_compose_contract(ci)


def validate_gateway_contract(configuration: Mapping[str, object]) -> None:
    """Require rendered gateway configuration to preserve the app/gateway boundary."""
    services = configuration.get("services")
    networks = configuration.get("networks")
    gateway = services.get("gateway") if isinstance(services, Mapping) else None
    if not isinstance(gateway, Mapping) or not isinstance(networks, Mapping):
        raise ConfigurationError("Rendered gateway Compose configuration is incomplete.")
    ports = gateway.get("ports", [])
    if len(ports) != 1 or not isinstance(ports[0], Mapping) or ports[0].get("host_ip") != "127.0.0.1":
        raise ConfigurationError("The gateway must bind its TLS port to loopback.")
    if set(gateway.get("networks", {})) != {"app_web", "legacy_web", "ci_web"}:
        raise ConfigurationError("The gateway must join only the three approved web networks.")


def validate_telemetry_compose_contract(configuration: Mapping[str, object]) -> None:
    """Keep the existing collector opt-in, internal, and addressable by the API alias."""
    services = configuration.get("services")
    if not isinstance(services, Mapping):
        raise ConfigurationError("Rendered telemetry Compose configuration has no services.")
    api = services.get("app-api")
    collector = services.get("otel-collector")
    if not isinstance(api, Mapping) or not isinstance(collector, Mapping):
        raise ConfigurationError("The telemetry profile must include the API and existing collector.")
    api_environment = api.get("environment", {})
    if (
        not isinstance(api_environment, Mapping)
        or api_environment.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
        != "http://otel-collector:4318/v1/metrics"
        or api_environment.get("OTEL_METRICS_ENABLED") not in {"true", "false"}
    ):
        raise ConfigurationError("The API must use an explicit internal OTLP endpoint and enabled setting.")
    if "telemetry" not in collector.get("profiles", []):
        raise ConfigurationError("The OpenTelemetry collector must remain opt-in behind telemetry.")
    if collector.get("ports"):
        raise ConfigurationError("The OpenTelemetry collector must not publish host ports.")
    networks = collector.get("networks", {})
    aliases = networks.get("app_web", {}).get("aliases", []) if isinstance(networks, Mapping) else []
    if set(networks) != {"app_web"} or "otel-collector" not in aliases:
        raise ConfigurationError("The collector must join only app_web under the existing OTLP alias.")


def validate_gateway_tls_files(certificate_file: str, private_key_file: str) -> None:
    """Check certificate/key files and pair compatibility without exposing contents."""
    try:
        certificate = Path(certificate_file).expanduser().resolve(strict=True)
        private_key = Path(private_key_file).expanduser().resolve(strict=True)
        if not certificate.is_file() or not private_key.is_file():
            raise OSError("not regular files")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(certificate), keyfile=str(private_key))
    except (OSError, ssl.SSLError, ValueError) as error:
        raise ConfigurationError("Gateway TLS certificate or private key is missing or invalid.") from error


def validate_ci_compose_contract(configuration: Mapping[str, object]) -> None:
    """Keep CI control access separate and jobs free of host/application resources."""
    services = configuration.get("services")
    networks = configuration.get("networks")
    if not isinstance(services, Mapping) or not isinstance(networks, Mapping):
        raise ConfigurationError("Rendered CI Compose configuration is incomplete.")
    required = {"server", "exporter", "exporter-state-init"}
    if not required.issubset(services):
        raise ConfigurationError("Rendered CI Compose configuration is missing a required service.")

    server = services["server"]
    server_networks = server.get("networks", {}) if isinstance(server, Mapping) else {}
    if not isinstance(server_networks, Mapping) or not {"ci-control", "ci-web"}.issubset(server_networks):
        raise ConfigurationError("The CI server must join its control and external web networks.")
    aliases = server_networks.get("ci-web", {}).get("aliases", [])
    if "ci-server" not in aliases:
        raise ConfigurationError("The CI server must publish the gateway alias ci-server.")
    server_environment = server.get("environment", {})
    if (
        not isinstance(server_environment, Mapping)
        or server_environment.get("WOODPECKER_DEFAULT_APPROVAL_MODE") != "all_events"
        or server.get("ports")
    ):
        raise ConfigurationError("The CI server must retain all_events approval and use the gateway for ingress.")
    if not isinstance(networks.get("ci-web"), Mapping) or networks["ci-web"].get("external") is not True:
        raise ConfigurationError("The CI web network must be externally owned by the gateway stack.")

    initializer = services["exporter-state-init"]
    initializer_volumes = initializer.get("volumes", []) if isinstance(initializer, Mapping) else []
    if (
        not isinstance(initializer, Mapping)
        or initializer.get("user") != "0:0"
        or initializer.get("entrypoint") != ["python", "-m", "ci.prepare_exporter_state"]
        or initializer.get("network_mode") != "none"
        or initializer.get("restart") != "no"
        or set(initializer.get("profiles", [])) != {"logs"}
        or set(initializer.get("cap_drop", [])) != {"ALL"}
        or set(initializer.get("cap_add", [])) != {"CHOWN", "FOWNER"}
        or initializer.get("ports")
        or not isinstance(initializer_volumes, list)
        or len(initializer_volumes) != 1
        or not isinstance(initializer_volumes[0], Mapping)
        or initializer_volumes[0].get("type") != "volume"
        or initializer_volumes[0].get("source") != "exporter-state"
        or initializer_volumes[0].get("target") != "/var/lib/ci-log-exporter"
    ):
        raise ConfigurationError("The exporter state initializer must not join a network or access other resources.")
    exporter = services["exporter"]
    dependencies = exporter.get("depends_on", {}) if isinstance(exporter, Mapping) else {}
    state_dependency = dependencies.get("exporter-state-init") if isinstance(dependencies, Mapping) else None
    if (
        not isinstance(state_dependency, Mapping)
        or state_dependency.get("condition") != "service_completed_successfully"
    ):
        raise ConfigurationError("The exporter must wait for state ownership preparation.")

    agent = services.get("agent")
    if agent is not None:
        if not isinstance(agent, Mapping) or "runner" not in agent.get("profiles", []):
            raise ConfigurationError("The CI agent must remain opt-in behind the runner profile.")
        socket_mounts = [
            volume
            for volume in agent.get("volumes", [])
            if isinstance(volume, str) and "/var/run/docker.sock" in volume
        ]
        if len(socket_mounts) != 1:
            raise ConfigurationError("Only the opt-in CI agent may mount the Docker control socket.")

    if not isinstance(exporter, Mapping) or not isinstance(exporter.get("build"), Mapping):
        raise ConfigurationError("The CI exporter must be built from the repository image definition.")
    exporter_volumes = exporter.get("volumes", [])
    exporter_binds = [
        volume
        for volume in exporter_volumes
        if isinstance(volume, Mapping) and volume.get("type") == "bind"
    ]
    if any(
        volume.get("target") != "/run/woodpecker-control/api-token"
        for volume in exporter_binds
    ):
        raise ConfigurationError("The CI exporter must keep its code inside its image.")
    if len(exporter_binds) != 1 or exporter_binds[0].get("read_only") is not True:
        raise ConfigurationError("The CI exporter must mount only its read-only API token file.")
    if any(
        isinstance(volume, str) and ("docker.sock" in volume or "/app" in volume)
        for volume in exporter_volumes
    ):
        raise ConfigurationError("The CI exporter must not mount host control or application data.")

    for name, service in services.items():
        if name in {"agent", "server", "exporter", "exporter-state-init"} or not isinstance(service, Mapping):
            continue
        for volume in service.get("volumes", []):
            if isinstance(volume, str) and ("docker.sock" in volume or "/app" in volume):
                raise ConfigurationError("CI jobs must not mount Docker control or application data.")
            if isinstance(volume, Mapping) and volume.get("type") == "bind":
                raise ConfigurationError("CI jobs must not bind mount host credentials or application data.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-directory", type=Path, required=True)
    parser.add_argument("--app-env", type=Path, required=True)
    parser.add_argument("--ci-env", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        validate_local_stacks(arguments.project_directory, arguments.app_env, arguments.ci_env)
    except (ConfigurationError, StackConfigurationError) as error:
        print(f"Local stack configuration error: {error}", file=sys.stderr)
        return 2
    print("Local application, legacy, gateway, and CI stack configuration validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
