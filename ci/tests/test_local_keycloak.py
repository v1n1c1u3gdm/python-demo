"""AAA coverage for idempotent local Keycloak identity provisioning."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ci.local_http import LocalHTTP, LocalHTTPError
from ci.local_keycloak import _b64url_integer, bootstrap_keycloak


class KeycloakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.secrets = self.root / "secrets"
        self.state = self.root / "state"
        self.secrets.mkdir()
        self.state.mkdir()
        (self.state / "public").mkdir()
        for name, value in {
            "keycloak_admin_password": "admin!123",
            "keycloak_client_secret": "client-secret",
            "bookstack_client_secret": "bookstack-secret",
            "gitea_client_secret": "gitea-secret",
            "api_db_password": "database-secret",
            "keycloak_db_password": "keycloak-database-secret",
        }.items():
            (self.secrets / name).write_text(value, encoding="utf-8")
        self.users: dict[str, dict] = {}
        self.clients: dict[str, dict] = {}
        self.roles: dict[str, dict] = {}
        self.role_assignments: list[dict] = []
        self.group_assignments: list[str] = []
        self.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.discovery_issuer = "https://app.localhost/auth/realms/python-demo"

        users = self.users
        signing_key = self.signing_key
        discovery_issuer = self
        clients = self.clients
        roles = self.roles
        role_assignments = self.role_assignments
        group_assignments = self.group_assignments

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                return

            def do_GET(self) -> None:
                if self.path.endswith("/.well-known/openid-configuration"):
                    issuer = discovery_issuer.discovery_issuer
                    response = {
                        "issuer": issuer,
                        "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
                        "token_endpoint": issuer + "/protocol/openid-connect/token",
                        "userinfo_endpoint": issuer + "/protocol/openid-connect/userinfo",
                        "jwks_uri": issuer + "/protocol/openid-connect/certs",
                    }
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path.endswith("/protocol/openid-connect/certs"):
                    numbers = signing_key.public_key().public_numbers()
                    encode = lambda number: base64.urlsafe_b64encode(
                        number.to_bytes((number.bit_length() + 7) // 8, "big")).decode().rstrip("=")
                    response = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256",
                                          "kid": "keycloak-signing-key", "e": encode(numbers.e),
                                          "n": encode(numbers.n)}]}
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path.endswith("/openid-connect/userinfo"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({"sub": "stable-subject"}).encode())
                elif "/clients?" in self.path:
                    self.send_response(200)
                    self.end_headers()
                    client_id = self.path.split("clientId=", 1)[1]
                    values = [clients[client_id]] if client_id in clients else []
                    self.wfile.write(json.dumps(values).encode())
                elif self.path.endswith("/clients"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(list(clients.values())).encode())
                elif self.path.endswith("/groups"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps([
                        {"id": "local-admins-id", "name": "local-admins"},
                        {"id": "bookstack-admin-id", "name": "Admin"},
                    ]).encode())
                elif self.path.endswith("/roles/admin"):
                    self.send_response(200 if "admin" in roles else 404)
                    self.end_headers()
                    self.wfile.write(json.dumps(roles.get("admin", {"error": "Role not found"})).encode())
                elif self.path.endswith("/realms"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps([{"realm": "python-demo"}]).encode())
                elif self.path.endswith("/users") or "/users?" in self.path:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(list(users.values())).encode())
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({}).encode())

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
                if self.path.endswith("/protocol/openid-connect/token"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({"access_token": "opaque-admin-token"}).encode())
                    return
                if self.path.endswith("/users"):
                    value = json.loads(body)
                    users[value["username"]] = {**value, "id": "stable-subject"}
                    self.send_response(201)
                    self.end_headers()
                    return
                if self.path.endswith("/clients"):
                    value = json.loads(body)
                    clients[value["clientId"]] = {**value, "id": value["clientId"] + "-id"}
                    self.send_response(201)
                    self.end_headers()
                    return
                if self.path.endswith("/role-mappings/realm"):
                    role_assignments.extend(json.loads(body))
                    self.send_response(204)
                    self.end_headers()
                    return
                if self.path.endswith("/roles"):
                    value = json.loads(body)
                    roles[value["name"]] = {"id": "admin-role", **value}
                    self.send_response(201)
                    self.end_headers()
                    return
                self.send_response(201)
                self.end_headers()

            def do_PUT(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(size)
                if self.path.endswith("/reset-password"):
                    self.send_response(204)
                    self.end_headers()
                    return
                if "/groups/" in self.path:
                    group_assignments.append(self.path.rsplit("/", 1)[1])
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/auth"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    def test_bootstrap_admin_has_complete_profile(self) -> None:
        # Arrange / Act
        result = bootstrap_keycloak(self.base_url, self.secrets, self.state)

        # Assert
        self.assertEqual(result["username"], "admin")
        self.assertEqual(result["subject"], "stable-subject")
        persisted = json.loads((self.state / "keycloak-admin.json").read_text())
        self.assertEqual(persisted, result)
        created = self.users["admin"]
        self.assertTrue(created["firstName"])
        self.assertTrue(created["lastName"])
        self.assertTrue(created["email"])
        self.assertEqual(self.role_assignments[0]["name"], "admin")
        self.assertEqual(self.group_assignments[0], "local-admins-id")
        self.assertIn("bookstack-admin-id", self.group_assignments)
        pem = (self.state / "public/bookstack-id-token.pem").read_bytes()
        loaded = serialization.load_pem_public_key(pem)
        self.assertEqual(loaded.public_numbers(), self.signing_key.public_key().public_numbers())
        self.assertEqual((self.state / "public/bookstack-id-token.pem").stat().st_mode & 0o777, 0o644)

    def test_malformed_jwks_rsa_component_is_rejected(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "valid RSA signing key"):
            _b64url_integer(None)  # type: ignore[arg-type]

    def test_ui_client_requires_pkce_and_api_audience(self) -> None:
        # Arrange / Act
        bootstrap_keycloak(self.base_url, self.secrets, self.state)

        # Assert: exercise the provisioner through recorded resources in state.
        ui = self.clients["python-demo-ui"]
        self.assertEqual(ui["redirectUris"], ["https://app.localhost/admin"])
        self.assertEqual(ui["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(ui["attributes"]["post.logout.redirect.uris"], "+")
        mappers = {mapper["name"]: mapper for mapper in ui["protocolMappers"]}
        self.assertEqual(mappers["python-demo-api audience"]["config"]["included.client.audience"],
                         "python-demo-api")
        self.assertEqual(mappers["groups"]["config"]["claim.name"], "groups")
        self.assertEqual(self.clients["bookstack"]["protocolMappers"][0]["config"]["claim.name"], "groups")
        self.assertEqual(self.clients["gitea"]["protocolMappers"][0]["config"]["claim.name"], "groups")

    def test_api_password_grant_token_has_api_audience(self) -> None:
        # Arrange / Act
        bootstrap_keycloak(self.base_url, self.secrets, self.state)

        # Assert
        api_mappers = {mapper["name"]: mapper for mapper in self.clients["python-demo-api"]["protocolMappers"]}
        self.assertEqual(api_mappers["python-demo-api audience"]["config"]["included.client.audience"],
                         "python-demo-api")

    def test_repeated_provisioning_preserves_subject(self) -> None:
        # Arrange / Act
        first = bootstrap_keycloak(self.base_url, self.secrets, self.state)
        second = bootstrap_keycloak(self.base_url, self.secrets, self.state)

        # Assert
        self.assertEqual(first["subject"], second["subject"])
        self.assertEqual((self.state / "keycloak-admin-subject").read_text().strip(), first["subject"])

    def test_discovery_must_match_the_persisted_public_issuer(self) -> None:
        # Arrange
        self.discovery_issuer = "https://attacker.invalid/auth/realms/python-demo"

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "discovery does not match"):
            bootstrap_keycloak(self.base_url, self.secrets, self.state)
        self.assertFalse((self.state / "public/bookstack-id-token.pem").exists())

    def test_failure_never_publishes_identity_ready(self) -> None:
        # Arrange
        ready = self.state / "ready"
        ready.mkdir()
        marker = ready / "bookstack"
        marker.write_text("stale-generation")

        # Act
        (self.secrets / "bookstack_client_secret").unlink()
        with self.assertRaisesRegex(ValueError, "secret is unavailable"):
            bootstrap_keycloak(self.base_url, self.secrets, self.state)

        # Assert
        self.assertFalse(marker.exists())
        self.assertFalse((self.state / "keycloak-admin.json").exists())

    def test_symlinked_ready_root_preserves_external_markers(self) -> None:
        # Arrange
        outside = self.root / "outside-ready"
        outside.mkdir()
        marker = outside / "bookstack"
        marker.write_text("keep-this-marker", encoding="utf-8")
        (self.state / "ready").symlink_to(outside, target_is_directory=True)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlink"):
            bootstrap_keycloak(self.base_url, self.secrets, self.state)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep-this-marker")
        self.assertFalse((self.state / "keycloak-admin.json").exists())

    def test_local_http_supports_form_posts_and_safe_errors(self) -> None:
        # Arrange / Act
        response = LocalHTTP(self.base_url).request(
            "POST", "/protocol/openid-connect/token", data={"grant_type": "password"}
        )

        # Assert
        self.assertEqual(response["access_token"], "opaque-admin-token")

    def test_local_http_uses_verified_tls_and_preserves_form_fields(self) -> None:
        # Arrange
        response = Mock(status_code=200, headers={"Content-Type": "application/json"}, encoding="utf-8")
        response.iter_content.return_value = [b'{"ok":true}']
        with patch("ci.local_http.requests.request", return_value=response) as request:
            # Act
            result = LocalHTTP("https://app.localhost", Path("/tmp/local-ca.pem")).request(
                "POST", "/token", data={"password": "human-secret"}
            )

        # Assert
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_args.kwargs["data"], {"password": "human-secret"})
        self.assertEqual(request.call_args.kwargs["verify"], "/tmp/local-ca.pem")
        self.assertTrue(request.call_args.kwargs["stream"])
        response.close.assert_called_once()
        self.assertTrue(LocalHTTP("http://internal").verify)

    def test_local_http_rejects_unexpected_status_without_response_body(self) -> None:
        # Arrange
        response = Mock(status_code=401)
        with patch("ci.local_http.requests.request", return_value=response), self.assertRaisesRegex(
            LocalHTTPError, "HTTP 401"
        ) as caught:
            # Act
            LocalHTTP("http://internal").request("GET", "/private")
        self.assertNotIn("must-not-appear", str(caught.exception))
        response.close.assert_called_once()

    def test_local_http_bounds_retries_and_hides_transport_errors(self) -> None:
        # Arrange / Act
        with patch("ci.local_http.requests.request", side_effect=OSError("token=must-not-appear")), self.assertRaisesRegex(
            LocalHTTPError, "retry deadline"
        ) as caught:
            LocalHTTP("http://internal", timeout=0.15).request("GET", "/ready")

        # Assert
        self.assertNotIn("must-not-appear", str(caught.exception))

    def test_local_http_rejects_oversized_response(self) -> None:
        # Arrange
        response = Mock(status_code=200)
        consumed = []

        def endless_chunks():
            while True:
                consumed.append(len(consumed))
                yield b"x" * 65536

        response.iter_content.side_effect = lambda chunk_size: endless_chunks()
        with patch("ci.local_http.requests.request", return_value=response), self.assertRaisesRegex(
            LocalHTTPError, "exceeded the allowed size"
        ):
            # Act
            LocalHTTP("http://internal").request("GET", "/large")
        response.close.assert_called_once()
        self.assertEqual(len(consumed), 17)

    def test_local_http_rejects_calls_outside_deadline_execution_context(self) -> None:
        # Arrange
        errors = []

        def request_from_worker() -> None:
            try:
                LocalHTTP("http://internal").request("GET", "/ready")
            except LocalHTTPError as error:
                errors.append(str(error))

        worker = Thread(target=request_from_worker)

        # Act
        worker.start()
        worker.join()

        # Assert
        self.assertEqual(len(errors), 1)
        self.assertIn("main thread on POSIX", errors[0])

    def test_local_http_stops_continuous_slow_drip_at_wall_clock_deadline(self) -> None:
        # Arrange
        response = Mock(status_code=200)

        def continuous_chunks():
            while True:
                time.sleep(0.02)
                yield b"x"

        response.iter_content.side_effect = lambda chunk_size: continuous_chunks()
        started = time.monotonic()

        # Act / Assert
        with patch("ci.local_http.requests.request", return_value=response), self.assertRaisesRegex(
            LocalHTTPError, "deadline"
        ):
            LocalHTTP("http://internal", timeout=0.12).request("GET", "/slow")
        self.assertLess(time.monotonic() - started, 0.75)
        response.close.assert_called_once()

    def test_local_http_deadline_closes_real_slow_header_and_body_streams(self) -> None:
        # Arrange
        peer_closed = {"headers": threading.Event(), "body": threading.Event()}

        class DrippingHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                kind = self.path.rsplit("/", 1)[-1]
                try:
                    if kind == "headers":
                        self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        for byte in b"abcdefghijklmnopqrstuvwxyz0123456789":
                            self.connection.sendall(bytes([byte]))
                            time.sleep(0.01)
                        self.connection.sendall(b"\r\nContent-Length: 0\r\n\r\n")
                    else:
                        self.send_response(200)
                        self.send_header("Content-Length", "100")
                        self.end_headers()
                        for _ in range(100):
                            self.connection.sendall(b"x")
                            time.sleep(0.01)
                    self.connection.settimeout(0.5)
                    if self.connection.recv(1) == b"":
                        peer_closed[kind].set()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    peer_closed[kind].set()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), DrippingHandler)
        server.daemon_threads = True
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Act / Assert
        try:
            for kind in ("headers", "body"):
                started = time.monotonic()
                with self.subTest(kind=kind), self.assertRaisesRegex(LocalHTTPError, "deadline"):
                    LocalHTTP(f"http://127.0.0.1:{server.server_port}", timeout=0.12).request("GET", f"/{kind}")
                self.assertLess(time.monotonic() - started, 0.25)
                self.assertTrue(peer_closed[kind].wait(0.5))
        finally:
            server.shutdown()
            server_thread.join()
            server.server_close()

    def test_local_http_retries_transient_503_until_ready(self) -> None:
        # Arrange
        busy = Mock(status_code=503)
        ready = Mock(status_code=200, headers={"Content-Type": "application/json"}, encoding="utf-8")
        ready.iter_content.return_value = [b'{"ready":true}']

        # Act
        with patch("ci.local_http.requests.request", side_effect=[busy, ready]) as request, \
                patch("ci.local_http.time.sleep"):
            result = LocalHTTP("http://internal", timeout=0.5).request("GET", "/ready")

        # Assert
        self.assertEqual(result, {"ready": True})
        self.assertEqual(request.call_count, 2)
        busy.close.assert_called_once()
        ready.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
