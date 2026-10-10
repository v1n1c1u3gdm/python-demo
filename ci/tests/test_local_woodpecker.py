"""Behavior tests for persistent, one-time Woodpecker OAuth credentials."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from ci.local_woodpecker import bootstrap_woodpecker_oauth, main, publish_ci_ready


class LocalWoodpeckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.secrets = self.root / "secrets"
        self.state.mkdir()
        (self.state / "bootstrap-generation").write_text("a" * 32)
        (self.secrets / "runtime" / "gitea-oauth-init").mkdir(parents=True)
        (self.secrets / "runtime" / "woodpecker").mkdir(parents=True)
        self.secrets.joinpath("gitea_bootstrap_api_token").write_text("api-token")
        self.created = []
        self.health_checks = []
        created = self.created
        health_checks = self.health_checks

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                return

            def do_POST(self) -> None:
                created.append(self.path)
                size = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(size)
                self.send_response(201)
                self.end_headers()
                self.wfile.write(json.dumps({"client_id": "stable-client", "client_secret": "one-time-secret"}).encode())

            def do_GET(self) -> None:
                if self.path == "/git/api/healthz":
                    health_checks.append(self.path)
                    self.send_response(404 if len(health_checks) == 1 else 200)
                    self.end_headers()
                    self.wfile.write(b"not-ready" if len(health_checks) == 1 else b"ok")
                    return
                if self.path == "/git/api/v1/user" and self.headers.get("Authorization") == "token api-token":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"login":"admin","is_admin":true}')
                else:
                    self.send_response(401)
                    self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    def test_oauth_secret_survives_repeat_without_second_registration(self) -> None:
        # Arrange / Act
        with patch("ci.local_woodpecker.GITEA_URL", self.url + "/git"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)
        first = (self.secrets / "runtime" / "woodpecker" / "gitea_client_secret").read_text()
        with patch("ci.local_woodpecker.GITEA_URL", self.url + "/git"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)

        # Assert
        self.assertEqual(first, "one-time-secret")
        self.assertEqual((self.secrets / "runtime" / "woodpecker" / "gitea_client_secret").read_text(), first)
        self.assertEqual(self.created, ["/git/api/v1/user/applications/oauth2"])
        self.assertGreaterEqual(len(self.health_checks), 2)
        self.assertFalse((self.state / "ready" / "ci").exists())
        self.assertEqual(json.loads((self.state / "woodpecker-gitea-oauth.json").read_text())["client_id"],
                         "stable-client")

    def test_registration_failure_does_not_publish_ci_readiness(self) -> None:
        # Arrange
        (self.state / "ready").mkdir()

        # Act / Assert
        # A missing auth token causes a fail-closed error before OAuth registration.
        (self.secrets / "gitea_bootstrap_api_token").unlink()
        with patch("ci.local_woodpecker.GITEA_URL", self.url + "/git"), self.assertRaisesRegex(
                ValueError, "token"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)
        self.assertFalse((self.state / "ready" / "ci").exists())

    def test_partial_canonical_oauth_pair_refuses_secret_rotation(self) -> None:
        # Arrange
        (self.secrets / "woodpecker_gitea_client").write_text("existing-client")
        (self.secrets / "woodpecker_gitea_secret").write_text("existing-secret")
        (self.secrets / "woodpecker_gitea_secret").unlink()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "incomplete"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)
        self.assertEqual(self.created, [])

    def test_repeated_bootstrap_checks_runtime_copy_against_canonical_pair(self) -> None:
        # Arrange
        with patch("ci.local_woodpecker.GITEA_URL", self.url + "/git"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)
        (self.secrets / "runtime" / "woodpecker" / "gitea_client_secret").write_text("drifted")

        # Act / Assert
        with patch("ci.local_woodpecker.GITEA_URL", self.url + "/git"), self.assertRaisesRegex(
                ValueError, "derived runtime copy"):
            bootstrap_woodpecker_oauth(self.state, self.secrets)
        self.assertEqual(self.created, ["/git/api/v1/user/applications/oauth2"])

    def test_ci_route_marker_is_exact_current_generation_bytes(self) -> None:
        # Arrange
        (self.state / "ready").mkdir()

        # Act
        publish_ci_ready(self.state)

        # Assert
        self.assertEqual((self.state / "ready" / "ci").read_bytes(), b"a" * 32)

    def test_compose_init_command_publishes_only_the_current_ci_generation(self) -> None:
        # Arrange
        (self.state / "ready").mkdir()
        arguments = ["ci.local_woodpecker", "--publish-ready", "--state-dir", str(self.state)]

        # Act
        with patch.object(sys, "argv", arguments):
            main()

        # Assert
        self.assertEqual((self.state / "ready/ci").read_bytes(), b"a" * 32)


if __name__ == "__main__":
    unittest.main()
