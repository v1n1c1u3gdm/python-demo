"""Gateway route contracts and opt-in local TLS integration checks."""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import yaml

from ci.stack_config import (
    ConfigurationError,
    validate_compose_contract,
    validate_gateway_compose_contract,
    validate_legacy_compose_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_COMPOSE = REPOSITORY_ROOT / "infra" / "compose" / "gateway.yaml"
GATEWAY_CONFIG = REPOSITORY_ROOT / "infra" / "nginx" / "gateway.conf"


def valid_gateway_compose() -> dict[str, object]:
    return {
        "services": {
            "gateway": {
                "ports": [{"target": 443, "published": "8443", "host_ip": "127.0.0.1"}],
                "networks": {"app_web": {}, "legacy_web": {}, "ci_web": {}},
                "environment": {
                    "BOOKSTACK_BOOTSTRAP_CONFIRMED": "false"
                },
                "read_only": True,
                "cap_drop": ["ALL"],
                "cap_add": ["CHOWN", "DAC_READ_SEARCH", "NET_BIND_SERVICE", "SETGID", "SETUID"],
            }
        }
    }


class TestGatewayComposeContract(unittest.TestCase):
    def test_gateway_uses_only_web_networks_and_loopback_tls(self) -> None:
        # Arrange
        configuration = valid_gateway_compose()

        # Act / Assert
        validate_gateway_compose_contract(configuration)

    def test_rejects_gateway_on_a_database_or_ci_control_network(self) -> None:
        # Arrange
        configuration = valid_gateway_compose()
        configuration["services"]["gateway"]["networks"]["app_data"] = {}

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "only the app, legacy, and CI web"):
            validate_gateway_compose_contract(configuration)

    def test_rejects_unrecognized_bookstack_route_confirmation(self) -> None:
        # Arrange
        configuration = valid_gateway_compose()
        configuration["services"]["gateway"]["environment"]["BOOKSTACK_BOOTSTRAP_CONFIRMED"] = "yes"

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "must be explicitly true or false"):
            validate_gateway_compose_contract(configuration)

    def test_rejects_gateway_without_required_tls_key_read_capability(self) -> None:
        # Arrange
        configuration = valid_gateway_compose()
        configuration["services"]["gateway"]["cap_add"] = ["NET_BIND_SERVICE"]

        # Act / Assert
        with self.assertRaisesRegex(ConfigurationError, "worker-drop capabilities"):
            validate_gateway_compose_contract(configuration)

    def test_default_compose_keeps_share_closed_and_routes_ci_dynamically(self) -> None:
        # Arrange
        self.assertTrue(GATEWAY_COMPOSE.is_file())
        self.assertTrue(GATEWAY_CONFIG.is_file())
        compose = yaml.safe_load(GATEWAY_COMPOSE.read_text(encoding="utf-8"))
        proxy = GATEWAY_CONFIG.read_text(encoding="utf-8")

        # Act
        gateway = compose["services"]["gateway"]
        network_names = set(gateway["networks"])

        # Assert
        self.assertEqual(network_names, {"app_web", "legacy_web", "ci_web"})
        self.assertIn("resolver 127.0.0.11 valid=10s", proxy)
        self.assertIn("set $ci_backend ci-server:8000", proxy)
        self.assertIn("location ^~ /share/ {\n        return 404;", proxy)
        self.assertIn("$git_upstream_path$is_args$args", proxy)
        self.assertIn("$request_uri", proxy)
        self.assertEqual(gateway["environment"]["BOOKSTACK_BOOTSTRAP_CONFIRMED"], "${BOOKSTACK_BOOTSTRAP_CONFIRMED:-false}")
        disabled_route = (REPOSITORY_ROOT / "infra/nginx/bookstack-route-disabled.conf").read_text(
            encoding="utf-8"
        )
        disabled_root = (REPOSITORY_ROOT / "infra/nginx/bookstack-root-disabled.conf").read_text(
            encoding="utf-8"
        )
        self.assertIn("return 404", disabled_route)
        self.assertIn("return 404", disabled_root)
        self.assertIn("return 403", proxy)

class _LoopbackHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        raw_socket = socket.create_connection(("127.0.0.1", self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class _FormInputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        values = dict(attrs)
        name = values.get("name")
        if name:
            self.values[name] = values.get("value", "") or ""


def _gateway_request(
    hostname: str,
    port: int,
    ca_bundle: Path,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    context = ssl.create_default_context(cafile=str(ca_bundle))
    connection = _LoopbackHTTPSConnection(hostname, port, context=context, timeout=20)
    request_headers = {"Host": hostname, "Connection": "close", **(headers or {})}
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def _compose_environment(values: dict[str, str]) -> dict[str, str]:
    allowed = ("PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "TMPDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(values)
    return environment


def _write_environment_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")


def _run_cleanup_actions(
    actions: list[tuple[str, Callable[[], subprocess.CompletedProcess[str]]]],
) -> list[str]:
    errors: list[str] = []
    for name, action in actions:
        try:
            result = action()
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{name}: raised {type(error).__name__}: {error}")
        else:
            if result.returncode:
                errors.append(f"{name}: {result.stdout}{result.stderr}")
    return errors


def _jwt_claims_without_token(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AssertionError("The synthetic identity provider returned a malformed JWT.")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    if not isinstance(claims, dict):
        raise TypeError("The synthetic identity provider returned invalid JWT claims.")
    return claims


def _test_certificate_files(directory: Path, hostname: str) -> tuple[Path, Path, Path]:
    ca_key = directory / "test-ca.key"
    ca_certificate = directory / "test-ca.pem"
    server_key = directory / "server.key"
    request_file = directory / "server.csr"
    server_certificate = directory / "server.pem"
    extensions = directory / "server.ext"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
            "-out", str(ca_certificate), "-sha256", "-days", "2", "-subj", "/CN=python-demo-task3-test-ca",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(server_key),
            "-out", str(request_file), "-subj", f"/CN={hostname}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    extensions.write_text(f"subjectAltName=DNS:{hostname}\n", encoding="utf-8")
    subprocess.run(
        [
            "openssl", "x509", "-req", "-in", str(request_file), "-CA", str(ca_certificate),
            "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(server_certificate), "-days", "2",
            "-sha256", "-extfile", str(extensions),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if shutil.which("chcon"):
        subprocess.run(
            ["chcon", "-R", "-t", "container_file_t", str(directory)],
            check=True,
            capture_output=True,
            text=True,
        )
    return ca_certificate, server_certificate, server_key


@unittest.skipUnless(
    os.environ.get("RUN_STACK_GATEWAY_PROOF") == "1"
    and shutil.which("docker")
    and shutil.which("openssl"),
    "Set RUN_STACK_GATEWAY_PROOF=1 to start isolated application, legacy, and gateway Compose projects.",
)
class TestGatewayHTTPSIntegration(unittest.TestCase):
    def assert_gateway_redirect(
        self, location: str | None, path: str, hostname: str, query: str = ""
    ) -> None:
        # NGINX may emit an absolute HTTPS Location by default; validate its URL semantics.
        parsed = urlsplit(location or "")
        self.assertEqual(parsed.path, path)
        self.assertEqual(parsed.query, query)
        if parsed.scheme:
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, hostname)
        else:
            self.assertEqual(parsed.netloc, "")

    def test_nginx_preserves_encoded_git_path_query_and_optional_ci_dns(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-gateway-proxy-") as directory:
            temporary_root = Path(directory)
            suffix = "".join(
                character for character in temporary_root.name[-8:].lower() if character.isalnum()
            )
            hostname = f"proxy-{suffix}.test"
            gateway_project = f"task3proxy{suffix}"
            upstream_project = f"task3mock{suffix}"
            app_web = f"t3proxyapp{suffix}"
            legacy_web = f"t3proxylegacy{suffix}"
            ci_web = f"t3proxyci{suffix}"
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                https_port = listener.getsockname()[1]
            ca_bundle, tls_certificate, tls_key = _test_certificate_files(temporary_root, hostname)
            capture_script = temporary_root / "capture.py"
            capture_script.write_text(
                "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "import json\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self): self.respond()\n"
                "    def do_POST(self): self.respond()\n"
                "    def respond(self):\n"
                "        body=json.dumps({'path':self.path,'method':self.command,'host':self.headers.get('Host'),"
                "'proto':self.headers.get('X-Forwarded-Proto')}).encode()\n"
                "        self.send_response(200); self.send_header('Content-Type','application/json'); "
                "self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n"
                "    def log_message(self,*args): pass\n"
                "from threading import Thread\n"
                "servers=[HTTPServer(('0.0.0.0',port),Handler) for port in (80,3000,8080,8000)]\n"
                "[Thread(target=server.serve_forever,daemon=True).start() for server in servers]\n"
                "from threading import Event\n"
                "Event().wait()\n",
                encoding="utf-8",
            )
            upstream_compose = temporary_root / "upstreams.yaml"
            upstream_values = {
                "APP_WEB_NETWORK": app_web,
                "LEGACY_WEB_NETWORK": legacy_web,
                "CI_WEB_NETWORK": ci_web,
            }
            capture_service = {
                "image": "python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d",
                "command": ["python", "/fixture/capture.py"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(capture_script),
                        "target": "/fixture/capture.py",
                        "read_only": True,
                    }
                ],
            }
            upstream_compose.write_text(
                yaml.safe_dump(
                    {
                        "services": {
                            "capture": {
                                **capture_service,
                                "networks": {
                                    "app_web": {"aliases": ["app-api", "app-ui", "app-keycloak"]},
                                    "legacy_web": {
                                        "aliases": ["legacy-gitea", "legacy-bookstack", "legacy-share"]
                                    },
                                },
                            },
                            "capture_ci": {
                                **capture_service,
                                "networks": {"ci_web": {"aliases": ["ci-server"]}},
                            },
                        },
                        "networks": {
                            "app_web": {"name": app_web},
                            "legacy_web": {"name": legacy_web},
                            "ci_web": {"name": ci_web, "external": True},
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            gateway_values = {
                "GATEWAY_PROJECT": gateway_project,
                "GATEWAY_HTTPS_PORT": str(https_port),
                "GATEWAY_BIND_ADDRESS": "127.0.0.1",
                "GATEWAY_TLS_CERT_FILE": str(tls_certificate),
                "GATEWAY_TLS_KEY_FILE": str(tls_key),
                "GATEWAY_TLS_CA_FILE": str(ca_bundle),
                "BOOKSTACK_BOOTSTRAP_CONFIRMED": "false",
                "PUBLIC_HOST": hostname,
                "APP_WEB_NETWORK": app_web,
                "LEGACY_WEB_NETWORK": legacy_web,
                "CI_WEB_NETWORK": ci_web,
            }
            gateway_env = temporary_root / "gateway.env"
            upstream_env = temporary_root / "upstreams.env"
            _write_environment_file(gateway_env, gateway_values)
            _write_environment_file(upstream_env, upstream_values)
            gateway_environment = _compose_environment(gateway_values)
            upstream_environment = _compose_environment(upstream_values)

            def compose(
                project: str,
                env_file: Path,
                process_environment: dict[str, str],
                compose_file: Path,
                *arguments: str,
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "docker", "compose", "--project-directory", str(REPOSITORY_ROOT), "-p", project,
                        "--env-file", str(env_file), "-f", str(compose_file), *arguments,
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=process_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=300,
                )

            app_yaml = REPOSITORY_ROOT / "infra/compose/gateway.yaml"
            rendered_gateway = compose(
                gateway_project, gateway_env, gateway_environment, app_yaml, "config", "--format", "json"
            )
            rendered_upstreams = compose(
                upstream_project, upstream_env, upstream_environment, upstream_compose, "config", "--format", "json"
            )
            self.assertEqual(rendered_gateway.returncode, 0, rendered_gateway.stdout + rendered_gateway.stderr)
            self.assertEqual(
                rendered_upstreams.returncode, 0, rendered_upstreams.stdout + rendered_upstreams.stderr
            )
            validate_gateway_compose_contract(json.loads(rendered_gateway.stdout))
            upstream_configuration = json.loads(rendered_upstreams.stdout)
            self.assertEqual(upstream_configuration["name"], upstream_project)
            self.assertEqual(
                upstream_configuration["services"]["capture"]["volumes"][0]["source"],
                str(capture_script),
            )

            gateway_started = False
            upstream_started = False
            ci_started = False
            failure: BaseException | None = None
            try:
                upstream_started = True
                started = compose(
                    upstream_project, upstream_env, upstream_environment, upstream_compose,
                    "up", "--detach", "capture",
                )
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                gateway_started = True
                gateway_up = compose(
                    gateway_project, gateway_env, gateway_environment, app_yaml, "up", "--detach", "--build"
                )
                self.assertEqual(gateway_up.returncode, 0, gateway_up.stdout + gateway_up.stderr)

                tls_ready = False
                tls_deadline = time.monotonic() + 30
                last_tls_error: BaseException | None = None
                while time.monotonic() < tls_deadline:
                    try:
                        _gateway_request(hostname, https_port, ca_bundle, "/")
                        tls_ready = True
                        break
                    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                        last_tls_error = error
                        time.sleep(1)
                if not tls_ready:
                    logs = compose(
                        gateway_project, gateway_env, gateway_environment, app_yaml, "logs", "gateway"
                    )
                    self.fail(f"Gateway TLS did not become ready: {last_tls_error}\n{logs.stdout}{logs.stderr}")

                # Optional CI is offline at gateway startup and must not hold the app routes open.
                offline_status, _, _ = _gateway_request(
                    hostname, https_port, ca_bundle, "/ci/status?continue=a%2Fb"
                )
                self.assertEqual(offline_status, 502)
                status, _, body = _gateway_request(hostname, https_port, ca_bundle, "/api/health")
                self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
                closed_bookstack_status, _, _ = _gateway_request(
                    hostname, https_port, ca_bundle, "/bookstack/login?continue=a%2Fb"
                )
                self.assertEqual(closed_bookstack_status, 404)

                # The gateway-owned CI network accepts a separately started optional producer.
                ci_started = True
                ci_up = compose(
                    upstream_project, upstream_env, upstream_environment, upstream_compose,
                    "up", "--detach", "capture_ci",
                )
                self.assertEqual(ci_up.returncode, 0, ci_up.stdout + ci_up.stderr)

                def read_capture(path: str) -> dict[str, str]:
                    status, _, body = _gateway_request(hostname, https_port, ca_bundle, path)
                    self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
                    return json.loads(body)

                encoded_git = read_capture("/git/owner/repo%2Fpart?continue=a%2Fb")
                self.assertEqual(encoded_git["path"], "/owner/repo%2Fpart?continue=a%2Fb")
                self.assertEqual(encoded_git["proto"], "https")
                self.assertEqual(encoded_git["host"], hostname)
                encoded_api = read_capture("/api/health?continue=a%2Fb")
                self.assertEqual(encoded_api["path"], "/health?continue=a%2Fb")
                preserved_auth = read_capture("/auth/realms/example?continue=a%2Fb")
                self.assertEqual(preserved_auth["path"], "/auth/realms/example?continue=a%2Fb")
                self.assertEqual(read_capture("/")["path"], "/")
                ci_capture: dict[str, str] | None = None
                ci_deadline = time.monotonic() + 20
                while time.monotonic() < ci_deadline:
                    try:
                        ci_capture = read_capture("/ci/status?continue=a%2Fb")
                        break
                    except AssertionError:
                        time.sleep(1)
                self.assertIsNotNone(ci_capture, "the gateway should resolve a CI producer added after startup")
                self.assertEqual(ci_capture["path"], "/ci/status?continue=a%2Fb")

                # Enabling the fixture route strips its external prefix while preserving raw URI/query bytes.
                gateway_values["BOOKSTACK_BOOTSTRAP_CONFIRMED"] = "true"
                _write_environment_file(gateway_env, gateway_values)
                gateway_environment = _compose_environment(gateway_values)
                recreated = compose(
                    gateway_project, gateway_env, gateway_environment, app_yaml,
                    "up", "--detach", "--force-recreate",
                )
                self.assertEqual(recreated.returncode, 0, recreated.stdout + recreated.stderr)
                gateway_ready = False
                readiness_deadline = time.monotonic() + 30
                while time.monotonic() < readiness_deadline:
                    try:
                        read_capture("/api/health")
                        gateway_ready = True
                        break
                    except (AssertionError, OSError, ssl.SSLError, http.client.HTTPException):
                        time.sleep(1)
                self.assertTrue(gateway_ready, "the gateway should restart with the confirmed BookStack route")
                status, headers, _ = _gateway_request(hostname, https_port, ca_bundle, "/bookstack")
                self.assertEqual(status, 308)
                self.assert_gateway_redirect(headers.get("Location"), "/bookstack/", hostname)
                for path in ("/api", "/auth", "/git", "/ci"):
                    status, headers, _ = _gateway_request(
                        hostname, https_port, ca_bundle, f"{path}?continue=a%2Fb"
                    )
                    self.assertEqual(status, 308, f"{path} should redirect with its original query")
                    self.assert_gateway_redirect(
                        headers.get("Location"), f"{path}/", hostname, "continue=a%2Fb"
                    )
                bookstack_capture = read_capture("/bookstack/images/logo%2Fpart.svg?continue=a%2Fb")
                self.assertEqual(bookstack_capture["path"], "/images/logo%2Fpart.svg?continue=a%2Fb")

                # A CI path write is denied before any upstream lookup; other routes remain usable.
                status, _, _ = _gateway_request(
                    hostname,
                    https_port,
                    ca_bundle,
                    "/ci/api/repos/fixture/pipelines/7/restart?continue=a%2Fb",
                    method="POST",
                    headers={"Content-Length": "0"},
                    body=b"",
                )
                self.assertEqual(status, 403)
                for blocked_path in (
                    "/ci/api/repos/fixture/pipelines%2F",
                    "/ci/api/repos/fixture/pipelines%2F7%2Frestart",
                    "/ci/api/repos/fixture/cr%6Fn%2F7",
                ):
                    status, _, _ = _gateway_request(
                        hostname,
                        https_port,
                        ca_bundle,
                        blocked_path,
                        method="POST",
                        headers={"Content-Length": "0"},
                        body=b"",
                    )
                    self.assertEqual(status, 403, f"encoded CI write path {blocked_path} must be blocked")
                self.assertEqual(read_capture("/api/health")["path"], "/health")
            except BaseException as error:
                failure = error
                try:
                    logs = compose(
                        gateway_project, gateway_env, gateway_environment, app_yaml, "logs", "gateway"
                    )
                except (OSError, subprocess.TimeoutExpired) as log_error:
                    error.add_note(f"Could not collect gateway logs: {type(log_error).__name__}: {log_error}")
                else:
                    error.add_note(f"Gateway logs:\n{logs.stdout}{logs.stderr}")
                raise
            finally:
                cleanup_actions: list[tuple[str, Callable[[], subprocess.CompletedProcess[str]]]] = []
                if ci_started:
                    cleanup_actions.append((
                        "CI stub",
                        lambda: compose(
                            upstream_project, upstream_env, upstream_environment, upstream_compose,
                            "rm", "--stop", "--force", "capture_ci",
                        ),
                    ))
                if gateway_started:
                    cleanup_actions.append((
                        "gateway",
                        lambda: compose(
                            gateway_project, gateway_env, gateway_environment, app_yaml,
                            "down", "--volumes", "--remove-orphans",
                        ),
                    ))
                if upstream_started:
                    cleanup_actions.append((
                        "upstreams",
                        lambda: compose(
                            upstream_project, upstream_env, upstream_environment, upstream_compose,
                            "down", "--volumes", "--remove-orphans",
                        ),
                    ))
                cleanup_errors = _run_cleanup_actions(cleanup_actions)
                if cleanup_errors:
                    message = "Controlled gateway cleanup failed: " + "\n".join(cleanup_errors)
                    if failure is None:
                        self.fail(message)
                    failure.add_note(message)

    def test_public_routes_tls_login_prefixes_and_closed_services(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-stack-gateway-") as directory:
            temporary_root = Path(directory)
            suffix = "".join(
                character for character in temporary_root.name[-8:].lower() if character.isalnum()
            )
            hostname = f"gateway-{suffix}.test"
            app_project = f"task3app{suffix}"
            legacy_project = f"task3legacy{suffix}"
            gateway_project = f"task3gate{suffix}"
            app_web = f"t3appweb{suffix}"
            app_data = f"t3appdata{suffix}"
            legacy_web = f"t3legacyweb{suffix}"
            legacy_data = f"t3legacydata{suffix}"
            ci_web = f"t3ciweb{suffix}"
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                https_port = listener.getsockname()[1]

            ca_bundle, tls_certificate, tls_key = _test_certificate_files(temporary_root, hostname)
            share_directory = temporary_root / "share"
            share_directory.mkdir()
            (share_directory / "private-fixture.txt").write_text("synthetic share data\n", encoding="utf-8")
            secrets_directory = temporary_root / "secrets"
            secrets_directory.mkdir()
            secret_values = {
                "api-db": f"api-fixture-{suffix}",
                "mysql-root": f"mysql-root-fixture-{suffix}",
                "keycloak-db": f"keycloak-db-fixture-{suffix}",
                "keycloak-client": f"keycloak-client-fixture-{suffix}",
                "keycloak-admin": f"keycloak-admin-fixture-{suffix}",
                "bookstack-app-key": "base64:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=",
                "bookstack-db": f"bookstack-db-fixture-{suffix}",
                "mariadb-root": f"mariadb-root-fixture-{suffix}",
            }
            secret_files: dict[str, Path] = {}
            for name, value in secret_values.items():
                path = secrets_directory / name
                ending = "" if name == "bookstack-db" else "\n"
                path.write_text(value + ending, encoding="utf-8")
                secret_files[name] = path

            app_values = {
                "STACK_PROJECT": app_project,
                "APP_WEB_NETWORK": app_web,
                "APP_DATA_NETWORK": app_data,
                "PUBLIC_HOST": hostname,
                "GATEWAY_TLS_CA_FILE": str(ca_bundle),
                "KC_BOOTSTRAP_ADMIN_USERNAME": "synthetic-stack-admin",
                "MYSQL_ROOT_PASSWORD_FILE": str(secret_files["mysql-root"]),
                "API_DB_PASSWORD_FILE": str(secret_files["api-db"]),
                "KEYCLOAK_DB_PASSWORD_FILE": str(secret_files["keycloak-db"]),
                "KEYCLOAK_CLIENT_SECRET_FILE": str(secret_files["keycloak-client"]),
                "KEYCLOAK_ADMIN_PASSWORD_FILE": str(secret_files["keycloak-admin"]),
                "OTEL_METRICS_ENABLED": "true" if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1" else "false",
            }
            legacy_values = {
                "STACK_PROJECT": legacy_project,
                "LEGACY_WEB_NETWORK": legacy_web,
                "LEGACY_DATA_NETWORK": legacy_data,
                "PUBLIC_HOST": hostname,
                "BOOKSTACK_URL": f"https://{hostname}/bookstack/",
                "GITEA_ROOT_URL": f"https://{hostname}/git/",
                "SHARE_DATA_DIRECTORY": str(share_directory),
                "BOOKSTACK_APP_KEY_FILE": str(secret_files["bookstack-app-key"]),
                "BOOKSTACK_DB_PASSWORD_FILE": str(secret_files["bookstack-db"]),
                "MARIADB_ROOT_PASSWORD_FILE": str(secret_files["mariadb-root"]),
            }
            gateway_values = {
                "GATEWAY_PROJECT": gateway_project,
                "GATEWAY_HTTPS_PORT": str(https_port),
                "GATEWAY_BIND_ADDRESS": "127.0.0.1",
                "GATEWAY_TLS_CERT_FILE": str(tls_certificate),
                "GATEWAY_TLS_KEY_FILE": str(tls_key),
                "GATEWAY_TLS_CA_FILE": str(ca_bundle),
                "BOOKSTACK_BOOTSTRAP_CONFIRMED": "false",
                "PUBLIC_HOST": hostname,
                "APP_WEB_NETWORK": app_web,
                "LEGACY_WEB_NETWORK": legacy_web,
                "CI_WEB_NETWORK": ci_web,
            }
            app_env = temporary_root / "app.env"
            legacy_env = temporary_root / "legacy.env"
            gateway_env = temporary_root / "gateway.env"
            _write_environment_file(app_env, app_values)
            _write_environment_file(legacy_env, legacy_values)
            _write_environment_file(gateway_env, gateway_values)

            app_environment = _compose_environment(app_values)
            legacy_environment = _compose_environment(legacy_values)
            gateway_environment = _compose_environment(gateway_values)

            def compose(
                project: str,
                env_file: Path,
                process_environment: dict[str, str],
                compose_file: Path,
                *arguments: str,
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "docker", "compose", "--project-directory", str(REPOSITORY_ROOT), "-p", project,
                        "--env-file", str(env_file), "-f", str(compose_file), *arguments,
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=process_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=900,
                )

            def assert_succeeded(result: subprocess.CompletedProcess[str]) -> None:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            app_compose_file = REPOSITORY_ROOT / "infra/compose/app.yaml"
            legacy_compose_file = REPOSITORY_ROOT / "infra/compose/legacy.yaml"
            gateway_compose_file = REPOSITORY_ROOT / "infra/compose/gateway.yaml"
            # Render and check every project before the first container, network, or volume mutation.
            app_rendered = compose(app_project, app_env, app_environment, app_compose_file, "config", "--format", "json")
            legacy_rendered = compose(
                legacy_project, legacy_env, legacy_environment, legacy_compose_file, "config", "--format", "json"
            )
            gateway_rendered = compose(
                gateway_project, gateway_env, gateway_environment, gateway_compose_file, "config", "--format", "json"
            )
            assert_succeeded(app_rendered)
            assert_succeeded(legacy_rendered)
            assert_succeeded(gateway_rendered)
            app_configuration = json.loads(app_rendered.stdout)
            legacy_configuration = json.loads(legacy_rendered.stdout)
            gateway_configuration = json.loads(gateway_rendered.stdout)
            validate_compose_contract(app_configuration)
            validate_legacy_compose_contract(legacy_configuration)
            validate_gateway_compose_contract(gateway_configuration)
            self.assertEqual(app_configuration["name"], app_project)
            self.assertEqual(legacy_configuration["name"], legacy_project)
            self.assertEqual(gateway_configuration["name"], gateway_project)
            self.assertEqual(app_configuration["networks"]["app_web"]["name"], app_web)
            self.assertEqual(app_configuration["networks"]["app_data"]["name"], app_data)
            self.assertIs(app_configuration["networks"]["app_data"]["internal"], True)
            self.assertEqual(legacy_configuration["networks"]["legacy_web"]["name"], legacy_web)
            self.assertEqual(legacy_configuration["networks"]["legacy_data"]["name"], legacy_data)
            self.assertIs(legacy_configuration["networks"]["legacy_data"]["internal"], True)
            self.assertEqual(
                gateway_configuration["networks"]["ci_web"]["name"], ci_web
            )
            self.assertEqual(
                {name: volume["name"] for name, volume in app_configuration["volumes"].items()},
                {
                    "app_mysql_data": f"{app_project}_mysql",
                    "app_mysql_socket": f"{app_project}_mysql_socket",
                },
            )
            self.assertEqual(
                {name: volume["name"] for name, volume in legacy_configuration["volumes"].items()},
                {
                    "legacy_mariadb_data": f"{legacy_project}_mariadb",
                    "legacy_bookstack_config": f"{legacy_project}_bookstack",
                    "legacy_gitea_data": f"{legacy_project}_gitea",
                },
            )
            self.assertEqual(
                {name: secret["file"] for name, secret in app_configuration["secrets"].items()},
                {name: str(path) for name, path in {
                    "api_db_password": secret_files["api-db"],
                    "mysql_root_password": secret_files["mysql-root"],
                    "keycloak_db_password": secret_files["keycloak-db"],
                    "keycloak_client_secret": secret_files["keycloak-client"],
                    "keycloak_admin_password": secret_files["keycloak-admin"],
                }.items()},
            )
            self.assertEqual(
                {name: secret["file"] for name, secret in legacy_configuration["secrets"].items()},
                {name: str(path) for name, path in {
                    "bookstack_app_key": secret_files["bookstack-app-key"],
                    "bookstack_db_password": secret_files["bookstack-db"],
                    "mariadb_root_password": secret_files["mariadb-root"],
                }.items()},
            )
            self.assertEqual(
                {
                    (volume["source"], volume["target"], volume["read_only"])
                    for volume in legacy_configuration["services"]["legacy-share"]["volumes"]
                    if volume["target"] == "/usr/share/nginx/html"
                },
                {(str(share_directory), "/usr/share/nginx/html", True)},
            )
            self.assertEqual(
                {volume["source"] for volume in gateway_configuration["services"]["gateway"]["volumes"]},
                {str(tls_certificate), str(tls_key)},
            )
            self.assertEqual(
                app_configuration["services"]["app-api"]["volumes"][0]["source"], str(ca_bundle)
            )
            ui_build_args = app_configuration["services"]["app-ui"]["build"]["args"]
            self.assertEqual(app_configuration["services"]["app-ui"]["build"]["context"], str(REPOSITORY_ROOT))
            self.assertEqual(
                gateway_configuration["services"]["gateway"]["build"]["context"], str(REPOSITORY_ROOT)
            )
            self.assertEqual(ui_build_args["VITE_API_BASE_URL"], "/api")
            self.assertEqual(ui_build_args["VITE_ARTICLES_URL"], "/api/articles")
            self.assertEqual(ui_build_args["VITE_AUTHORS_URL"], "/api/authors")
            self.assertEqual(ui_build_args["VITE_ARTICLES_COUNT_URL"], "/api/articles/count_by_author")
            self.assertEqual(ui_build_args["VITE_SOCIALS_URL"], "/api/socials")
            self.assertEqual(ui_build_args["VITE_ARTICLE_PUBLIC_BASE_URL"], f"https://{hostname}")

            app_started = False
            legacy_started = False
            gateway_started = False
            ci_started = False
            ci_project = f"task4ci{suffix}"
            ci_token_file = temporary_root / "synthetic-ci-api-token"
            ci_token_file.write_text("synthetic-token-not-used\n", encoding="utf-8")
            ci_token_file.chmod(0o600)
            ci_values = {
                "WOODPECKER_HOST": f"https://{hostname}/ci",
                "WOODPECKER_GITHUB_CLIENT": f"synthetic-client-{suffix}",
                "WOODPECKER_GITHUB_SECRET": f"synthetic-github-secret-{suffix}",
                "WOODPECKER_AGENT_SECRET": f"synthetic-agent-secret-{suffix}",
                "WOODPECKER_ADMIN": "fixture-admin",
                "CI_WOODPECKER_REPO_ID": "42",
                "CI_WOODPECKER_TOKEN_FILE": str(ci_token_file),
                "CI_WEB_NETWORK": ci_web,
                "CI_CONTROL_NETWORK": f"t4cicontrol{suffix}",
            }
            ci_env = temporary_root / "ci.env"
            _write_environment_file(ci_env, ci_values)
            ci_environment = _compose_environment(ci_values)
            ci_compose_file = REPOSITORY_ROOT / "ci/compose.yaml"
            ci_overlay_file = REPOSITORY_ROOT / "infra/compose/ci.yaml"
            preflight_values = dict(app_values)
            preflight_values.update(legacy_values)
            preflight_values.update(gateway_values)
            preflight_env = temporary_root / "complete-stack.env"
            _write_environment_file(preflight_env, preflight_values)
            ci_rendered = None
            if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1":
                token_permissions = subprocess.run(
                    [
                        "docker", "run", "--rm", "--pull=never", "--network", "none",
                        "--user", "0:0", "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                        "--cap-add", "FOWNER", "-v", f"{ci_token_file}:/run/token",
                        "--entrypoint", "python",
                        (
                            "docker.io/library/python:3.14.7-slim@sha256:"
                            "51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d"
                        ),
                        "-c", "import os; os.chown('/run/token', 65532, 65532); os.chmod('/run/token', 0o600)",
                    ],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    token_permissions.returncode,
                    0,
                    token_permissions.stdout + token_permissions.stderr,
                )
                ci_rendered = compose(
                    ci_project, ci_env, ci_environment, ci_compose_file,
                    "-f", str(ci_overlay_file), "config", "--format", "json",
                )
                assert_succeeded(ci_rendered)
                ci_model = json.loads(ci_rendered.stdout)
                self.assertEqual(ci_model["name"], ci_project)
                self.assertNotIn("agent", ci_model["services"])
                self.assertFalse(ci_model["services"]["server"].get("ports"))
                from ci.local_orchestration import validate_local_stacks

                validate_local_stacks(REPOSITORY_ROOT, preflight_env, ci_env)
            failure: BaseException | None = None
            try:
                app_started = True
                app_up_arguments = ["--profile", "telemetry"] if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1" else []
                app_up_arguments.extend(("up", "--detach", "--build"))
                app_up = compose(app_project, app_env, app_environment, app_compose_file, *app_up_arguments)
                assert_succeeded(app_up)
                legacy_started = True
                legacy_up = compose(
                    legacy_project, legacy_env, legacy_environment, legacy_compose_file, "up", "--detach"
                )
                assert_succeeded(legacy_up)
                gateway_started = True
                gateway_up = compose(
                    gateway_project, gateway_env, gateway_environment, gateway_compose_file,
                    "up", "--detach", "--build",
                )
                assert_succeeded(gateway_up)
                if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1":
                    ci_started = True
                    ci_up = compose(
                        ci_project, ci_env, ci_environment, ci_compose_file,
                        "-f", str(ci_overlay_file), "up", "--detach", "server",
                    )
                    assert_succeeded(ci_up)
                    state_init = compose(
                        ci_project,
                        ci_env,
                        ci_environment,
                        ci_compose_file,
                        "-f",
                        str(ci_overlay_file),
                        "--profile",
                        "logs",
                        "run",
                        "--build",
                        "--rm",
                        "--no-deps",
                        "exporter-state-init",
                    )
                    assert_succeeded(state_init)
                    state_write = compose(
                        ci_project,
                        ci_env,
                        ci_environment,
                        ci_compose_file,
                        "-f",
                        str(ci_overlay_file),
                        "--profile",
                        "logs",
                        "run",
                        "--rm",
                        "--no-deps",
                        "--entrypoint",
                        "python",
                        "exporter",
                        "-c",
                        (
                            "import io,os; from pathlib import Path; from ci.log_exporter import "
                            "Exporter,ExporterConfig; state=Path('/var/lib/ci-log-exporter/state.json'); "
                            "exporter=Exporter(ExporterConfig('http://ci-server:8000/ci',42, "
                            "Path('/run/woodpecker-control/api-token'),state),output=io.StringIO()); "
                            "exporter._save_state({'schema':1,'processed':['synthetic-pipeline'],"
                            "'pipelines':{'1':{'status':'success'}}}); "
                            "print(f'{state.stat().st_uid}:{state.stat().st_mode & 0o777:o}')"
                        ),
                    )
                    assert_succeeded(state_write)
                    self.assertIn("65532:600", state_write.stdout)

                def request_when_ready(path: str, expected_status: int = 200) -> tuple[http.client.HTTPMessage, bytes]:
                    deadline = time.monotonic() + 240
                    last_error: BaseException | None = None
                    while time.monotonic() < deadline:
                        try:
                            status, headers, response_body = _gateway_request(
                                hostname, https_port, ca_bundle, path
                            )
                            if status == expected_status:
                                return headers, response_body
                            last_error = AssertionError(f"{path} returned HTTP {status}: {response_body[:500]!r}")
                        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                            last_error = error
                        time.sleep(2)
                    raise AssertionError(f"Gateway route {path} did not become ready: {last_error}")

                # Act / Assert: the host trusts the synthetic CA, and the browser entry serves real UI assets.
                _, ui_html = request_when_ready("/")
                self.assertIn(b"<html", ui_html.lower())
                asset_match = re.search(rb'<script[^>]+src="([^"]+\.js)"', ui_html)
                self.assertIsNotNone(asset_match, "the UI document must reference a built JavaScript asset")
                _, ui_asset = request_when_ready(asset_match.group(1).decode("ascii"))
                self.assertIn(b"/api", ui_asset)
                request_when_ready("/api/liveness")

                for path in ("/api", "/auth", "/git", "/ci"):
                    status, headers, _ = _gateway_request(hostname, https_port, ca_bundle, path)
                    self.assertEqual(status, 308, f"{path} should redirect to its slash form")
                    self.assert_gateway_redirect(headers.get("Location"), f"{path}/", hostname)

                # Keycloak starts without a realm. Use only synthetic bootstrap and realm credentials.
                request_when_ready("/auth/realms/master/.well-known/openid-configuration")
                admin_password = secret_files["keycloak-admin"].read_text(encoding="utf-8").strip()
                kcadm = ["/opt/keycloak/bin/kcadm.sh"]
                admin_login = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "config", "credentials", "--server", "http://localhost:8080/auth", "--realm",
                    "master", "--user", "synthetic-stack-admin", "--password", admin_password,
                )
                assert_succeeded(admin_login)
                realm_created = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "create", "realms", "-s", "realm=python-demo", "-s", "enabled=true",
                )
                assert_succeeded(realm_created)
                client_secret = secret_files["keycloak-client"].read_text(encoding="utf-8").strip()
                client_created = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "create", "clients", "-r", "python-demo", "-s", "clientId=python-demo-api",
                    "-s", "enabled=true", "-s", "protocol=openid-connect", "-s", "publicClient=false",
                    "-s", "directAccessGrantsEnabled=true", "-s", f"secret={client_secret}",
                )
                assert_succeeded(client_created)
                clients = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "get", "clients", "-r", "python-demo", "-q", "clientId=python-demo-api",
                )
                assert_succeeded(clients)
                client_id = json.loads(clients.stdout)[0]["id"]
                mapper = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "create", f"clients/{client_id}/protocol-mappers/models", "-r", "python-demo",
                    "-s", "name=python-demo-api-audience", "-s", "protocol=openid-connect",
                    "-s", "protocolMapper=oidc-audience-mapper",
                    "-s", 'config."included.client.audience"=python-demo-api',
                    "-s", 'config."access.token.claim"=true',
                )
                assert_succeeded(mapper)
                user_created = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "create", "users", "-r", "python-demo", "-s", "username=fixture-admin",
                    "-s", "enabled=true", "-s", "firstName=Fixture", "-s", "lastName=Admin",
                    "-s", "email=fixture-admin@example.test", "-s", "emailVerified=true",
                )
                assert_succeeded(user_created)
                password_set = compose(
                    app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                    *kcadm, "set-password", "-r", "python-demo", "--username", "fixture-admin",
                    "--new-password", f"kc-fixture-{suffix}",
                )
                assert_succeeded(password_set)
                synthetic_password = f"kc-fixture-{suffix}"
                token_status, _, token_body = _gateway_request(
                    hostname,
                    https_port,
                    ca_bundle,
                    "/auth/realms/python-demo/protocol/openid-connect/token",
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    body=urlencode(
                        {
                            "grant_type": "password",
                            "client_id": "python-demo-api",
                            "client_secret": client_secret,
                            "username": "fixture-admin",
                            "password": synthetic_password,
                        }
                    ).encode("utf-8"),
                )
                token_payload = json.loads(token_body)
                if token_status != 200:
                    self.fail(
                        "Synthetic Keycloak token request failed: "
                        f"status={token_status}, error={token_payload.get('error')!r}, "
                        f"error_description={token_payload.get('error_description')!r}"
                    )
                claims = _jwt_claims_without_token(token_payload["access_token"])
                self.assertEqual(claims.get("iss"), f"https://{hostname}/auth/realms/python-demo")
                audience = claims.get("aud", [])
                if isinstance(audience, str):
                    audience = [audience]
                self.assertIn("python-demo-api", audience)
                _, discovery_body = request_when_ready(
                    "/auth/realms/python-demo/.well-known/openid-configuration"
                )
                discovery = json.loads(discovery_body)
                self.assertEqual(
                    discovery["issuer"], f"https://{hostname}/auth/realms/python-demo"
                )
                self.assertTrue(discovery["jwks_uri"].startswith(f"https://{hostname}/auth/"))
                login_status = 0
                login_body = b""
                login_deadline = time.monotonic() + (30 if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1" else 0)
                while True:
                    login_status, _, login_body = _gateway_request(
                        hostname,
                        https_port,
                        ca_bundle,
                        "/api/login",
                        method="POST",
                        headers={"Content-Type": "application/json"},
                        body=json.dumps(
                            {"username": "fixture-admin", "password": synthetic_password}
                        ).encode("utf-8"),
                    )
                    if login_status == 200 or time.monotonic() >= login_deadline:
                        break
                    time.sleep(2)
                if login_status != 200:
                    try:
                        login_error = json.loads(login_body)
                    except json.JSONDecodeError:
                        login_error = {}
                    self.fail(
                        "API login failed after the synthetic token endpoint succeeded: "
                        f"status={login_status}, errors={login_error.get('errors')!r}"
                    )
                self.assertEqual(json.loads(login_body)["username"], "fixture-admin")
                if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1":
                    admin_role = compose(
                        app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                        *kcadm, "create", "roles", "-r", "python-demo", "-s", "name=admin",
                    )
                    assert_succeeded(admin_role)
                    role_mapping = compose(
                        app_project, app_env, app_environment, app_compose_file, "exec", "-T", "app-keycloak",
                        *kcadm, "add-roles", "-r", "python-demo", "--uusername", "fixture-admin",
                        "--rolename", "admin",
                    )
                    assert_succeeded(role_mapping)
                    metrics_login_status = 0
                    metrics_login_body = b""
                    metrics_login_deadline = time.monotonic() + 30
                    while True:
                        metrics_login_status, _, metrics_login_body = _gateway_request(
                            hostname,
                            https_port,
                            ca_bundle,
                            "/api/login",
                            method="POST",
                            headers={"Content-Type": "application/json"},
                            body=json.dumps(
                                {"username": "fixture-admin", "password": synthetic_password}
                            ).encode("utf-8"),
                        )
                        if metrics_login_status == 200 or time.monotonic() >= metrics_login_deadline:
                            break
                        time.sleep(2)
                    self.assertEqual(metrics_login_status, 200, metrics_login_body[:500])
                    access_token = json.loads(metrics_login_body)["access_token"]
                    metrics_status, _, metrics_body = _gateway_request(
                        hostname, https_port, ca_bundle, "/api/metrics",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    self.assertEqual(metrics_status, 200, metrics_body[:500].decode("utf-8", errors="replace"))
                    self.assertIn(b"http_server_requests_total", metrics_body)
                    collector_logs = ""
                    collector_deadline = time.monotonic() + 30
                    while time.monotonic() < collector_deadline:
                        logs = compose(
                            app_project, app_env, app_environment, app_compose_file,
                            "--profile", "telemetry", "logs", "otel-collector",
                        )
                        assert_succeeded(logs)
                        collector_logs = logs.stdout + logs.stderr
                        if "http_server_requests_total" in collector_logs and "service.name" in collector_logs:
                            break
                        time.sleep(1)
                    self.assertIn("http_server_requests_total", collector_logs)
                    self.assertIn("service.name", collector_logs)

                # Share and BookStack remain closed until a fixture-only password reset is complete.
                for path in ("/share/", "/share/private-fixture.txt", "/share/../share/private-fixture.txt"):
                    status, _, _ = _gateway_request(hostname, https_port, ca_bundle, path)
                    self.assertEqual(status, 404, f"share path {path} must remain closed")
                status, _, _ = _gateway_request(hostname, https_port, ca_bundle, "/bookstack/login")
                self.assertEqual(status, 404)

                gitea_password = f"gitea-fixture-{suffix}"
                gitea_user = compose(
                    legacy_project, legacy_env, legacy_environment, legacy_compose_file,
                    "exec", "-T", "--user", "git", "legacy-gitea", "gitea", "admin", "user", "create",
                    "--username", "fixture-admin", "--password", gitea_password,
                    "--email", "fixture-admin@example.test", "--admin",
                )
                assert_succeeded(gitea_user)
                gitea_login_status, _, gitea_login_page = _gateway_request(
                    hostname, https_port, ca_bundle, "/git/user/login"
                )
                self.assertEqual(gitea_login_status, 200)
                self.assertIn(b"login", gitea_login_page.lower())
                authorization = "Basic " + base64.b64encode(
                    f"fixture-admin:{gitea_password}".encode()
                ).decode("ascii")
                repo_status, _, repo_body = _gateway_request(
                    hostname,
                    https_port,
                    ca_bundle,
                    "/git/api/v1/user/repos?fixture=query%2Fvalue",
                    method="POST",
                    headers={"Authorization": authorization, "Content-Type": "application/json"},
                    body=json.dumps({"name": "synthetic-gateway-repository", "private": True, "auto_init": True}).encode("utf-8"),
                )
                self.assertEqual(repo_status, 201, repo_body.decode("utf-8", errors="replace"))
                self.assertIn(b"synthetic-gateway-repository", repo_body)
                clone_directory = temporary_root / "cloned-repository"
                git_environment = _compose_environment({"GIT_SSL_CAINFO": str(ca_bundle)})
                clone = subprocess.run(
                    [
                        "git", "-c", f"http.extraHeader=Authorization: {authorization}",
                        "-c", f"http.curloptResolve={hostname}:{https_port}:127.0.0.1", "clone", "--quiet",
                        f"https://{hostname}:{https_port}/git/fixture-admin/synthetic-gateway-repository.git",
                        str(clone_directory),
                    ],
                    cwd=temporary_root,
                    env=git_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertEqual(clone.returncode, 0, clone.stdout + clone.stderr)
                self.assertTrue((clone_directory / ".git").is_dir())

                # Change the fresh BookStack admin password before explicitly enabling its route.
                reset_command = (
                    '$user=\\BookStack\\Users\\Models\\User::query()->where("email","admin@admin.com")->first();'
                    f'$user->password=\\Illuminate\\Support\\Facades\\Hash::make("bookstack-fixture-{suffix}");'
                    '$user->save();echo "synthetic bootstrap password replaced";'
                )
                password_reset = compose(
                    legacy_project, legacy_env, legacy_environment, legacy_compose_file, "exec", "-T",
                    "legacy-bookstack", "/command/with-contenv", "php", "/app/www/artisan", "tinker",
                    "--execute", reset_command,
                )
                assert_succeeded(password_reset)
                self.assertIn("synthetic bootstrap password replaced", password_reset.stdout)
                password_verification = compose(
                    legacy_project, legacy_env, legacy_environment, legacy_compose_file, "exec", "-T",
                    "legacy-bookstack", "/command/with-contenv", "php", "/app/www/artisan", "tinker",
                    "--execute",
                    (
                        '$user=\\BookStack\\Users\\Models\\User::query()->where("email","admin@admin.com")->first();'
                        f'echo \\Illuminate\\Support\\Facades\\Hash::check("bookstack-fixture-{suffix}",$user->password) ? "new-password-accepted\\n" : "new-password-rejected\\n";'
                        'echo \\Illuminate\\Support\\Facades\\Hash::check("password",$user->password) ? "bootstrap-password-accepted\\n" : "bootstrap-password-rejected\\n";'
                    ),
                )
                assert_succeeded(password_verification)
                self.assertIn("new-password-accepted", password_verification.stdout)
                self.assertIn("bootstrap-password-rejected", password_verification.stdout)
                gateway_values["BOOKSTACK_BOOTSTRAP_CONFIRMED"] = "true"
                gateway_environment = _compose_environment(gateway_values)
                _write_environment_file(gateway_env, gateway_values)
                gateway_recreated = compose(
                    gateway_project, gateway_env, gateway_environment, gateway_compose_file,
                    "up", "--detach", "--force-recreate",
                )
                assert_succeeded(gateway_recreated)
                request_when_ready("/api/liveness")

                bookstack_status, bookstack_headers, bookstack_login = _gateway_request(
                    hostname, https_port, ca_bundle, "/bookstack/login"
                )
                self.assertEqual(bookstack_status, 200, bookstack_login[:500])
                self.assertIn(b"bookstack", bookstack_login.lower())
                redirect_status, redirect_headers, _ = _gateway_request(
                    hostname, https_port, ca_bundle, "/bookstack"
                )
                self.assertEqual(redirect_status, 308)
                self.assert_gateway_redirect(redirect_headers.get("Location"), "/bookstack/", hostname)
                css_path = re.search(rb'(?:href|src)="([^"]+\.(?:css|js))', bookstack_login)
                self.assertIsNotNone(css_path)
                asset_reference = css_path.group(1).decode("utf-8")
                asset_url = urlsplit(urljoin(f"https://{hostname}/bookstack/login", asset_reference))
                self.assertEqual(asset_url.scheme, "https")
                self.assertEqual(asset_url.netloc, hostname)
                asset_request_path = asset_url.path
                if asset_url.query:
                    asset_request_path += f"?{asset_url.query}"
                asset_status, _, asset_body = _gateway_request(
                    hostname, https_port, ca_bundle, asset_request_path
                )
                self.assertEqual(asset_status, 200)
                self.assertTrue(asset_body)
                form = _FormInputs()
                form.feed(bookstack_login.decode("utf-8", errors="replace"))
                self.assertIn("_token", form.values)
                cookies = "; ".join(
                    cookie.split(";", maxsplit=1)[0]
                    for cookie in bookstack_headers.get_all("Set-Cookie", [])
                )
                self.assertTrue(cookies)
                login_status, login_headers, _ = _gateway_request(
                    hostname,
                    https_port,
                    ca_bundle,
                    "/bookstack/login",
                    method="POST",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Cookie": cookies,
                        "Referer": f"https://{hostname}/bookstack/login",
                    },
                    body=urlencode(
                        {
                            "email": "admin@admin.com",
                            "password": f"bookstack-fixture-{suffix}",
                            "_token": form.values["_token"],
                        }
                    ).encode("utf-8"),
                )
                self.assertIn(login_status, {302, 303}, str(login_headers))
                self.assertIn("/bookstack", login_headers.get("Location", ""))

                # Regression guard: nested manual/cron/restart writes are rejected before optional CI DNS lookup.
                for path in (
                    "/ci/api/repos/fixture/pipelines",
                    "/ci/api/repos/fixture/pipelines/1/restart",
                    "/ci/api/repos/fixture/cron/1",
                    "/ci/api/repos/fixture/pipelines%2F",
                    "/ci/api/repos/fixture/pipelines%2F1%2Frestart",
                    "/ci/api/repos/fixture/cr%6Fn%2F1",
                ):
                    status, _, _ = _gateway_request(
                        hostname,
                        https_port,
                        ca_bundle,
                        path,
                        method="POST",
                        headers={"Content-Length": "0"},
                        body=b"",
                    )
                    self.assertEqual(status, 403, f"CI write route {path} must be blocked")
                ci_offline_status, _, _ = _gateway_request(
                    hostname, https_port, ca_bundle, "/ci/api/healthz"
                )
                expected_ci_status = 200 if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1" else 502
                self.assertEqual(ci_offline_status, expected_ci_status)
                healthy_status, _, _ = _gateway_request(hostname, https_port, ca_bundle, "/api/liveness")
                self.assertEqual(healthy_status, 200)
                if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1":
                    from ci.stack_capacity import run_pipeline_with_measurements

                    report_path = Path(os.environ.get("TASK4_CAPACITY_REPORT", "/tmp/python-demo-task4-capacity.json"))
                    run_id = str(time.time_ns())
                    run_pipeline_with_measurements(
                        REPOSITORY_ROOT,
                        (app_project, legacy_project, gateway_project, ci_project),
                        report_path,
                        run_id,
                    )
            except BaseException as error:
                failure = error
                runtime_logs: list[str] = []
                for name, attempted, project, env_file, process_environment, compose_file in (
                    ("ci", ci_started, ci_project, ci_env, ci_environment, ci_compose_file),
                    ("gateway", gateway_started, gateway_project, gateway_env, gateway_environment, gateway_compose_file),
                    ("legacy", legacy_started, legacy_project, legacy_env, legacy_environment, legacy_compose_file),
                    ("app", app_started, app_project, app_env, app_environment, app_compose_file),
                ):
                    if attempted:
                        try:
                            logs = compose(project, env_file, process_environment, compose_file, "logs")
                        except (OSError, subprocess.TimeoutExpired) as log_error:
                            runtime_logs.append(
                                f"Could not collect {name} logs: {type(log_error).__name__}: {log_error}"
                            )
                        else:
                            runtime_logs.append(f"{name} logs:\n{logs.stdout}{logs.stderr}")
                if runtime_logs:
                    error.add_note("\n".join(runtime_logs))
                raise
            finally:
                cleanup_actions: list[tuple[str, Callable[[], subprocess.CompletedProcess[str]]]] = []
                if ci_started:
                    cleanup_actions.append((
                        "ci",
                        lambda: compose(
                            ci_project, ci_env, ci_environment, ci_compose_file,
                            "-f", str(ci_overlay_file), "down", "--volumes", "--remove-orphans",
                        ),
                    ))
                if gateway_started:
                    cleanup_actions.append((
                        "gateway",
                        lambda: compose(
                            gateway_project, gateway_env, gateway_environment, gateway_compose_file,
                            "down", "--volumes", "--remove-orphans",
                        ),
                    ))
                if legacy_started:
                    cleanup_actions.append((
                        "legacy",
                        lambda: compose(
                            legacy_project, legacy_env, legacy_environment, legacy_compose_file,
                            "down", "--volumes", "--remove-orphans",
                        ),
                    ))
                if app_started:
                    app_down_arguments = ["--profile", "telemetry"] if os.environ.get("RUN_TASK4_COMPLETE_PROOF") == "1" else []
                    app_down_arguments.extend(("down", "--volumes", "--remove-orphans"))
                    cleanup_actions.append((
                        "app",
                        lambda: compose(
                            app_project, app_env, app_environment, app_compose_file,
                            *app_down_arguments,
                        ),
                    ))
                cleanup_failures = _run_cleanup_actions(cleanup_actions)
                if cleanup_failures and failure is None:
                    self.fail("Isolated Compose cleanup failed: " + "\n".join(cleanup_failures))
                if cleanup_failures and failure is not None:
                    failure.add_note("Isolated Compose cleanup failed: " + "\n".join(cleanup_failures))


if __name__ == "__main__":
    unittest.main()
