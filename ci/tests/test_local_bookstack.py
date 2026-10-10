"""Behavior tests for explicit local BookStack OIDC provisioning."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.local_bookstack import main, prepare_bookstack_sso

ROOT = Path(__file__).resolve().parents[2]


class LocalBookStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.secrets = self.root / "secrets"
        self.state.mkdir()
        self.secrets.mkdir()
        (self.secrets / "bookstack_client_secret").write_text("client-secret")
        (self.state / "bootstrap-generation").write_text("a" * 32)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_admin_subject_is_explicitly_persisted_for_native_init(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-1", "username": "admin"}

        # Act
        config = prepare_bookstack_sso(identity, self.state, self.secrets)

        # Assert
        self.assertEqual(config["AUTH_METHOD"], "oidc")
        self.assertEqual(config["OIDC_GROUPS_CLAIM"], "groups")
        self.assertNotIn("groups", config["OIDC_ADDITIONAL_SCOPES"])
        self.assertEqual(config["OIDC_ISSUER_DISCOVER"], "false")
        self.assertEqual(config["OIDC_PUBLIC_KEY"], "file:///run/local-public/bookstack-id-token.pem")
        self.assertEqual(config["OIDC_AUTH_ENDPOINT"],
                         "https://app.localhost/auth/realms/python-demo/protocol/openid-connect/auth")
        self.assertEqual(config["OIDC_TOKEN_ENDPOINT"],
                         "https://gateway:8443/auth/realms/python-demo/protocol/openid-connect/token")
        self.assertEqual(config["OIDC_USERINFO_ENDPOINT"],
                         "https://gateway:8443/auth/realms/python-demo/protocol/openid-connect/userinfo")
        self.assertEqual(config["OIDC_ADDITIONAL_SCOPES"], "openid,profile,email")
        self.assertEqual(config["BOOKSTACK_ADMIN_SUBJECT"], "kc-subject-1")
        self.assertEqual(json.loads((self.state / "bookstack-oidc.json").read_text())["subject"],
                         "kc-subject-1")

    def test_prior_subject_change_requires_explicit_relink(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-1", "username": "admin"}
        prepare_bookstack_sso(identity, self.state, self.secrets)
        identity["subject"] = "different-subject"

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "refusing implicit relinking"):
            prepare_bookstack_sso(identity, self.state, self.secrets)

    def test_malformed_client_secret_fails_closed(self) -> None:
        # Arrange
        (self.secrets / "bookstack_client_secret").write_text("bad\nsecret")
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-1", "username": "admin"}

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "secret is malformed"):
            prepare_bookstack_sso(identity, self.state, self.secrets)
        self.assertFalse((self.state / "bookstack-oidc.json").exists())

    def test_native_intent_command_reads_identity_artifact(self) -> None:
        # Arrange
        identity_path = self.state / "keycloak-admin.json"
        identity_path.write_text(json.dumps({"issuer": "https://app.localhost/auth/realms/python-demo",
                                             "subject": "kc-subject-1", "username": "admin"}))
        argv = ["ci.local_bookstack", "--state-dir", str(self.state), "--secrets-dir", str(self.secrets)]

        # Act
        with patch.object(sys, "argv", argv):
            main()

        # Assert
        self.assertEqual(json.loads((self.state / "bookstack-oidc.json").read_text())["subject"], "kc-subject-1")

    def test_missing_subject_or_wrong_username_is_rejected(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo", "username": "admin"}

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "identity"):
            prepare_bookstack_sso(identity, self.state, self.secrets)
        self.assertFalse((self.state / "bookstack-oidc.json").exists())

    def test_pinned_image_bootstraps_dynamic_ca_before_native_lifecycle(self) -> None:
        # Arrange
        dockerfile = (ROOT / "infra/local/bookstack.Dockerfile").read_text()
        startup = ROOT / "infra/local/bookstack-ca.sh"

        # Act
        startup_script = startup.read_text() if startup.exists() else ""

        # Assert
        self.assertIn("COPY infra/local/bookstack-ca.sh /custom-cont-init.d/10-local-ca", dockerfile)
        self.assertIn("/usr/bin/with-contenv", startup_script.splitlines()[0])
        self.assertIn("update-ca-certificates", startup_script)
        self.assertIn("/run/local-secrets/tls/ca.crt", startup_script)


if __name__ == "__main__":
    unittest.main()
