"""Opt-in, real Compose checks for the checkout-local SSO environment."""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _unrelated_database_snapshot() -> str:
    ids = _docker(
        "ps", "--filter", "label=com.docker.compose.project=python-demo",
        "--filter", "label=com.docker.compose.service=db", "--format", "{{.ID}}",
    ).stdout.strip().splitlines()
    return "\n".join(
        _docker("inspect", "--format", "{{.Id}}|{{.State.Status}}|{{.RestartCount}}", item).stdout.strip()
        for item in ids
    )


_REQUIRED_INITIALIZERS = {
    "local-init", "app-keycloak-db-init", "app-init", "keycloak-init", "bookstack-prepare",
    "bookstack-init", "gitea-prepare", "gitea-init", "gitea-oauth-init", "ci-init",
}


def _startup_initializers_completed(containers: list[dict[str, object]]) -> bool:
    completed = {
        item.get("Service") for item in containers
        if item.get("State") == "exited" and item.get("ExitCode") == 0
    }
    return _REQUIRED_INITIALIZERS.issubset(completed)


class LocalStackReadinessUnitTests(unittest.TestCase):
    def test_ready_poll_requires_each_native_initializer_to_exit_successfully(self) -> None:
        # Arrange
        services = [
            {"Service": service, "State": "exited", "ExitCode": 0}
            for service in sorted(_REQUIRED_INITIALIZERS)
        ]
        services.append({"Service": "app-api", "State": "running", "Health": "healthy"})

        # Act
        complete = _startup_initializers_completed(services)
        services = [item for item in services if item.get("Service") != "ci-init"]
        pending = _startup_initializers_completed(services)

        # Assert
        self.assertTrue(complete)
        self.assertFalse(pending)


def _docker(*arguments: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", *arguments], cwd=ROOT, check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Docker command could not complete within its bounded runtime.") from None
    if check and result.returncode:
        raise RuntimeError("Docker command failed with a nonzero status.")
    return result


def _compose(project: str, secrets: Path, state: Path, override: Path, *arguments: str,
             check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": project,
        "LOCAL_SECRETS_DIR": str(secrets),
        "LOCAL_STATE_DIR": str(state),
    }
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", "compose.yaml", "-f", str(override), *arguments],
            cwd=ROOT, env=environment, check=False, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Compose command could not complete within its bounded runtime.") from None
    if check and result.returncode:
        raise RuntimeError("Compose command failed with a nonzero status.")
    return result


@unittest.skipUnless(
    os.environ.get("LOCAL_STACK_LOG_PROOF") == "1" and shutil.which("docker"),
    "Set LOCAL_STACK_LOG_PROOF=1 with the local gateway running to verify its access log.",
)
class GatewayAccessLogIntegrationTests(unittest.TestCase):
    def test_gateway_keeps_request_shape_without_logging_query_values(self) -> None:
        # Arrange
        project = os.environ.get("LOCAL_STACK_PROJECT", "python-demo-local")
        port = int(os.environ.get("LOCAL_GATEWAY_PORT", "443"))
        state_dir = Path(os.environ.get("LOCAL_STATE_DIR", ROOT / "infra/.local"))
        ca_file = state_dir / "public/ca.crt"
        container = _docker(
            "ps", "--filter", f"label=com.docker.compose.project={project}",
            "--filter", "label=com.docker.compose.service=gateway", "--format", "{{.ID}}",
        ).stdout.strip().splitlines()
        self.assertEqual(len(container), 1, "Expected exactly one gateway owned by the selected Compose project.")
        marker = f"synthetic-query-{uuid.uuid4().hex}"
        request_path = f"/auth/realms/python-demo/.well-known/openid-configuration/log-probe-{uuid.uuid4().hex}"
        context = ssl.create_default_context(cafile=str(ca_file))
        request = urllib.request.Request(
            f"https://app.localhost:{port}{request_path}?access_token={marker}",
            method="GET",
        )

        # Act
        request_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with urllib.request.urlopen(request, context=context, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as response:
            status = response.code
            response.close()
        deadline = time.monotonic() + 5
        logs = ""
        while time.monotonic() < deadline:
            logs = _docker("logs", "--since", request_started, container[0], check=False, timeout=10).stdout
            if f"GET {request_path}" in logs:
                break
            time.sleep(0.1)

        # Assert
        self.assertEqual(status, 404)
        if marker in logs:
            self.fail("Gateway log included the synthetic query marker.")
        self.assertTrue(
            f'"GET {request_path} HTTP/1.1"' in logs,
            "Gateway log omitted the request method and path.",
        )
        self.assertTrue(f'" {status} ' in logs, "Gateway log omitted the response status.")


@unittest.skipUnless(
    os.environ.get("LOCAL_STACK_LIFECYCLE_PROOF") == "1" and shutil.which("docker"),
    "Set LOCAL_STACK_LIFECYCLE_PROOF=1 to build and exercise an isolated Compose project.",
)
class LocalStackLifecycleIntegrationTests(unittest.TestCase):
    maxDiff = None

    def _owned_ids(self, resource: str, project: str) -> list[str]:
        result = _docker(resource, "ls", "-q", "--filter", f"label=com.docker.compose.project={project}")
        return [item for item in result.stdout.splitlines() if item]

    def _wait_ready(self, project: str, secrets: Path, state: Path, override: Path) -> None:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            status = _compose(project, secrets, state, override, "ps", "--all", "--format", "json", check=False)
            try:
                containers = [json.loads(line) for line in status.stdout.splitlines() if line.strip()]
            except json.JSONDecodeError:
                containers = []
            api = next((item for item in containers if item.get("Service") == "app-api"), None)
            failed = [item for item in containers if item.get("State") == "exited" and item.get("ExitCode") not in (0, None)]
            if failed:
                self.fail("An isolated local Compose service exited unsuccessfully during startup.")
            if api and api.get("Health") == "healthy" and _startup_initializers_completed(containers):
                snapshot = self._local_snapshot(project, secrets, state)
                if all(snapshot["ready"].get(route) == snapshot["generation"]
                       for route in ("bookstack", "gitea", "ci")):
                    return
            time.sleep(3)
        self.fail("The isolated local API did not become healthy before the timeout.")

    def _local_snapshot(self, project: str, secrets: Path, state: Path) -> dict[str, object]:
        source = (
            "import hashlib,json\nfrom pathlib import Path\n"
            "s=Path('/secrets'); t=Path('/state'); dynamic={'bootstrap-generation','bootstrap.json'}\n"
            "def stable(p):\n"
            " data=p.read_bytes()\n"
            " if p.suffix=='.json':\n"
            "  try:\n"
            "   value=json.loads(data)\n"
            "   if isinstance(value,dict): value.pop('generation',None); data=json.dumps(value,sort_keys=True).encode()\n"
            "  except (ValueError,UnicodeDecodeError): pass\n"
            " if p.name=='gitea-oidc.env': data=b'\\n'.join(line for line in data.splitlines() if not line.startswith(b'generation='))\n"
            " return data\n"
            "files={str(p.relative_to(s)):'secret:'+hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in sorted(s.rglob('*')) if p.is_file()}\n"
            "files.update({str(p.relative_to(t)):'state:'+hashlib.sha256(stable(p)).hexdigest() "
            "for p in sorted(t.rglob('*')) if p.is_file() and p.name not in dynamic "
            "and 'ready' not in p.relative_to(t).parts})\n"
            "generation=(t/'bootstrap-generation').read_text().strip()\n"
            "ready={p.name:p.read_text().strip() for p in sorted((t/'ready').glob('*')) if p.is_file()}\n"
            "print(json.dumps({'files':files,'generation':generation,'ready':ready},sort_keys=True))"
        )
        result = _docker(
            "run", "--rm", "--network", "none", "--read-only",
            "--mount", f"type=bind,src={secrets},dst=/secrets,readonly",
            "--mount", f"type=bind,src={state},dst=/state,readonly",
            "python-demo-local-bootstrap:local", "python", "-c", source,
        )
        try:
            snapshot = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError("The scoped local state reader returned an invalid summary.") from None
        expected_state = {
            "keycloak-admin.json", "keycloak-admin-subject", "keycloak-issuer", "keycloak-clients.json",
            "bookstack-oidc.json", "gitea-oidc.json", "gitea-oidc.env", "woodpecker-gitea-oauth.json", "public/ca.crt",
            "public/bookstack-id-token.pem",
        }
        expected_secrets = {
            "api_db_password", "mysql_root_password", "keycloak_db_password", "keycloak_client_secret",
            "keycloak_admin_password", "bookstack_db_password", "mariadb_root_password", "bookstack_app_key",
            "bookstack_client_secret", "gitea_client_secret", "woodpecker_agent_secret", "gitea_admin_password",
            "tls/ca.key", "tls/server.crt", "tls/server.key", "tls/backchannel.key", "tls/backchannel.crt",
            "gitea_bootstrap_api_token", "woodpecker_gitea_client", "woodpecker_gitea_secret",
        }
        files = snapshot.get("files", {})
        self.assertTrue(
            expected_state.issubset(files),
            f"Persistent identity state is missing expected files: {sorted(expected_state - files.keys())}.",
        )
        self.assertTrue(
            expected_secrets.issubset(files),
            f"Persistent secret and TLS state is missing expected files: {sorted(expected_secrets - files.keys())}.",
        )
        return snapshot

    def _ready_marker_files(self, state: Path) -> list[str]:
        source = (
            "from pathlib import Path\n"
            "print('\\n'.join(sorted(p.name for p in (Path('/state')/'ready').glob('*') if p.is_file())))"
        )
        result = _docker(
            "run", "--rm", "--network", "none", "--read-only",
            "--mount", f"type=bind,src={state},dst=/state,readonly",
            "python-demo-local-bootstrap:local", "python", "-c", source,
        )
        return [line for line in result.stdout.splitlines() if line]

    def _assert_initializers_succeeded(self, project: str) -> None:
        rows = _docker(
            "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
            "--format", '{{.Label "com.docker.compose.service"}}|{{.Status}}',
        ).stdout.splitlines()
        statuses = {row.split("|", 1)[0]: row.split("|", 1)[1] for row in rows if "|" in row}
        initializers = {
            "local-init", "app-keycloak-db-init", "app-init", "keycloak-init", "bookstack-prepare",
            "bookstack-init", "gitea-prepare", "gitea-init", "gitea-oauth-init", "ci-init",
        }
        self.assertTrue(initializers.issubset(statuses), "A required local initializer did not run.")
        self.assertTrue(
            all(statuses[name].startswith("Exited (0)") for name in initializers),
            "A local initializer did not complete successfully.",
        )

    def _remove_owned_resources(self, resource: str, project: str) -> None:
        resource_type = {"container": "container", "network": "network", "volume": "volume"}[resource]
        ids = self._owned_ids(resource, project)
        for resource_id in ids:
            label_template = (
                '{{index .Config.Labels "com.docker.compose.project"}}' if resource == "container"
                else '{{index .Labels "com.docker.compose.project"}}'
            )
            owner = _docker(resource_type, "inspect", "--format", label_template, resource_id).stdout.strip()
            if owner != project:
                raise RuntimeError("Refusing cleanup because a resource does not carry the isolated project label.")
        if not ids:
            return
        if resource == "container":
            _docker("rm", "-f", *ids)
        else:
            _docker(resource_type, "rm", *ids)

    def _assert_project_ownership(self, project: str) -> None:
        for resource in ("container", "network", "volume"):
            resource_type = resource
            ids = self._owned_ids(resource, project)
            for resource_id in ids:
                label_template = (
                    '{{index .Config.Labels "com.docker.compose.project"}}' if resource == "container"
                    else '{{index .Labels "com.docker.compose.project"}}'
                )
                owner = _docker(resource_type, "inspect", "--format", label_template, resource_id).stdout.strip()
                if owner != project:
                    raise RuntimeError("Refusing Compose teardown without verified isolated-project ownership.")

    def _down_owned_project(self, project: str, secrets: Path, state: Path, override: Path) -> None:
        self._assert_project_ownership(project)
        _compose(project, secrets, state, override, "down", timeout=180)

    def test_first_up_recreation_failure_gating_and_scoped_cleanup(self) -> None:
        # Arrange
        project = f"local-sso-lifecycle-{uuid.uuid4().hex[:8]}"
        secrets = ROOT / "infra/secrets/local" / project
        workspace = ROOT / "infra/.local/playwright" / project
        state = workspace / "state"
        override = workspace / "compose.override.yaml"
        workspace.mkdir(parents=True, exist_ok=False)
        state.mkdir(exist_ok=False)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            gateway_port = probe.getsockname()[1]
        override.write_text(
            f'services:\n  gateway:\n    ports: !override ["127.0.0.1:{gateway_port}:443"]\n', encoding="utf-8"
        )
        unrelated = _unrelated_database_snapshot()
        self.assertEqual(self._owned_ids("volume", project), [])
        self.assertEqual(self._owned_ids("network", project), [])

        try:
            # Act: a bare Compose up owns the first local file generation.
            _compose(project, secrets, state, override, "up", "--detach", "--build", timeout=2400)
            self._wait_ready(project, secrets, state, override)
            self._assert_initializers_succeeded(project)
            first_snapshot = self._local_snapshot(project, secrets, state)
            for route in ("bookstack", "gitea", "ci"):
                self.assertEqual(
                    first_snapshot["ready"].get(route), first_snapshot["generation"],
                    "A native integration route did not match the active bootstrap generation.",
                )
            init_containers = _docker(
                "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", "label=com.docker.compose.service=app-init", "--format", "{{.ID}}",
            ).stdout.strip().splitlines()
            self.assertEqual(len(init_containers), 1, "Migrations must run through one app-init service.")

            # A failed upstream must retain useful path/status logs without echoing query values.
            api_containers = _docker(
                "ps", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", "label=com.docker.compose.service=app-api", "--format", "{{.ID}}",
            ).stdout.strip().splitlines()
            gateway_containers = _docker(
                "ps", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", "label=com.docker.compose.service=gateway", "--format", "{{.ID}}",
            ).stdout.strip().splitlines()
            self.assertEqual(len(api_containers), 1, "Expected one API owned by the isolated project.")
            self.assertEqual(len(gateway_containers), 1, "Expected one gateway owned by the isolated project.")
            for resource_id in (*api_containers, *gateway_containers):
                owner = _docker(
                    "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', resource_id
                ).stdout.strip()
                self.assertEqual(owner, project, "Refusing a failure probe outside the isolated project.")
            marker = f"synthetic-upstream-query-{uuid.uuid4().hex}"
            tls_context = ssl.create_default_context(cafile=str(state / "public/ca.crt"))
            _docker("stop", api_containers[0])
            try:
                failure_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
                request = urllib.request.Request(
                    f"https://app.localhost:{gateway_port}/api/ready?access_token={marker}", method="GET"
                )
                failure_status = None
                try:
                    with urllib.request.urlopen(request, context=tls_context, timeout=10) as response:
                        failure_status = response.status
                except urllib.error.HTTPError as response:
                    failure_status = response.code
                    response.close()
                self.assertEqual(
                    failure_status, 502,
                    "Stopping the isolated API did not exercise the gateway error path.",
                )
                deadline = time.monotonic() + 5
                error_logs = ""
                while time.monotonic() < deadline:
                    result = _docker(
                        "logs", "--since", failure_started, gateway_containers[0],
                        check=False, timeout=10,
                    )
                    error_logs = result.stdout + result.stderr
                    if "GET /api/ready" in error_logs:
                        break
                    time.sleep(0.1)
                self.assertIn(
                    '"GET /api/ready HTTP/1.1"', error_logs,
                    "The gateway omitted the method/path for the failed upstream request.",
                )
                self.assertIn('" 502 ', error_logs,
                              "The gateway omitted the failed upstream status from its access record.")
                if marker in error_logs:
                    self.fail("Gateway error output included the synthetic query marker.")
            finally:
                _docker("start", api_containers[0])

            # A failed generation must close the routes before returning failure.
            failure = _compose(
                project, secrets, state, override, "run", "--rm", "--no-deps",
                "-e", "LOCAL_OWNER_UID=invalid", "local-init", check=False,
            )
            self.assertNotEqual(failure.returncode, 0, "Invalid owner data unexpectedly passed local-init.")
            self.assertEqual(
                self._ready_marker_files(state), [],
                "A failed generation left public routes ready.",
            )
            try:
                with urllib.request.urlopen(
                    f"https://app.localhost:{gateway_port}/bookstack/login",
                    context=tls_context, timeout=10,
                ) as response:
                    closed_route_status = response.status
            except urllib.error.HTTPError as response:
                closed_route_status = response.code
                response.close()
            self.assertEqual(
                closed_route_status, 404,
                "A native service route remained open after its bootstrap generation failed.",
            )
            self.assertEqual(
                _unrelated_database_snapshot(),
                unrelated,
                "The unrelated database container changed during the isolated proof.",
            )

            self._down_owned_project(project, secrets, state, override)
            _compose(project, secrets, state, override, "up", "--detach", "--build", timeout=2400)
            self._wait_ready(project, secrets, state, override)
            self._assert_initializers_succeeded(project)
            second_snapshot = self._local_snapshot(project, secrets, state)
            self.assertEqual(first_snapshot["files"], second_snapshot["files"])
            self.assertNotEqual(first_snapshot["generation"], second_snapshot["generation"])
            for route in ("bookstack", "gitea", "ci"):
                self.assertEqual(
                    second_snapshot["ready"].get(route), second_snapshot["generation"],
                    "A recreated native integration route did not match the new generation.",
                )

            # Assert
            init_containers = _docker(
                "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", "label=com.docker.compose.service=app-init", "--format", "{{.ID}}",
            ).stdout.strip().splitlines()
            self.assertEqual(len(init_containers), 1, "Recreation must retain a single successful migrations init.")
        finally:
            self._assert_project_ownership(project)
            _compose(project, secrets, state, override, "down", check=False, timeout=180)
            for resource in ("container", "network", "volume"):
                self._remove_owned_resources(resource, project)
            if secrets.name == project and secrets.parent == ROOT / "infra/secrets/local":
                cleanup = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--mount", f"type=bind,src={secrets.parent},dst=/parent",
                    "python-demo-local-bootstrap:local", "python", "-c",
                    "from pathlib import Path; import shutil; p=Path('/parent')/'" + project
                    + "'; shutil.rmtree(p) if p.exists() and not p.is_symlink() else None",
                    check=True,
                    timeout=30,
                )
                self.assertFalse(cleanup.stdout.strip(), "The scoped secret cleanup emitted unexpected output.")
                self.assertFalse(secrets.exists(), "The scoped local secret directory was not removed.")
            if workspace.exists():
                _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--mount", f"type=bind,src={workspace.parent},dst=/parent",
                    "python-demo-local-bootstrap:local", "python", "-c",
                    "from pathlib import Path;import shutil;p=Path('/parent')/'" + project
                    + "';assert p.exists() and not p.is_symlink();shutil.rmtree(p);assert not p.exists()",
                    timeout=30,
                )
            self.assertFalse(workspace.exists(), "The isolated Playwright state directory was not removed.")


if __name__ == "__main__":
    unittest.main()
