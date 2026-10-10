"""Behavior tests for stable source and subject-based local Gitea OIDC."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.local_gitea import main, prepare_gitea_sso


class LocalGiteaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.secrets = self.root / "secrets"
        self.state.mkdir()
        self.secrets.mkdir()
        (self.secrets / "gitea_client_secret").write_text("client-secret")
        (self.state / "bootstrap-generation").write_text("b" * 32)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identity_config_requires_explicit_subject_and_group_admin_claim(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-2", "username": "admin"}

        # Act
        config = prepare_gitea_sso(identity, self.state, self.secrets)

        # Assert
        self.assertEqual(config["GITEA_OIDC_ADMIN_GROUP"], "local-admins")
        self.assertEqual(config["GITEA_OIDC_GROUP_CLAIM"], "groups")
        self.assertEqual(config["GITEA_OIDC_SCOPES"], "openid,email,profile")
        native_intent = (self.state / "gitea-oidc.env").read_text(encoding="utf-8")
        self.assertIn("subject=kc-subject-2\n", native_intent)
        self.assertNotIn("client-secret", native_intent)
        self.assertEqual(json.loads((self.state / "gitea-oidc.json").read_text())["subject"],
                         "kc-subject-2")

    def test_missing_identity_is_rejected_before_source_state_is_written(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo", "username": "admin"}

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "identity"):
            prepare_gitea_sso(identity, self.state, self.secrets)
        self.assertFalse((self.state / "gitea-oidc.json").exists())

    def test_prior_subject_change_requires_explicit_relink(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-2", "username": "admin"}
        prepare_gitea_sso(identity, self.state, self.secrets)
        identity["subject"] = "different-subject"

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "refusing implicit relinking"):
            prepare_gitea_sso(identity, self.state, self.secrets)

    def test_missing_secret_fails_closed(self) -> None:
        # Arrange
        (self.secrets / "gitea_client_secret").unlink()
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-2", "username": "admin"}

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "secret is unavailable"):
            prepare_gitea_sso(identity, self.state, self.secrets)
        self.assertFalse((self.state / "gitea-oidc.json").exists())

    def test_native_intent_command_reads_identity_artifact(self) -> None:
        # Arrange
        identity_path = self.state / "keycloak-admin.json"
        identity_path.write_text(json.dumps({"issuer": "https://app.localhost/auth/realms/python-demo",
                                             "subject": "kc-subject-2", "username": "admin"}))
        argv = ["ci.local_gitea", "--state-dir", str(self.state), "--secrets-dir", str(self.secrets)]

        # Act
        with patch.object(sys, "argv", argv):
            main()

        # Assert
        self.assertEqual(json.loads((self.state / "gitea-oidc.json").read_text())["subject"], "kc-subject-2")

    def test_existing_oidc_source_and_subject_are_not_duplicated(self) -> None:
        # Arrange
        identity = {"issuer": "https://app.localhost/auth/realms/python-demo",
                    "subject": "kc-subject-2", "username": "admin"}

        # Act
        prepare_gitea_sso(identity, self.state, self.secrets)
        prepare_gitea_sso(identity, self.state, self.secrets)

        # Assert
        stored = json.loads((self.state / "gitea-oidc.json").read_text())
        self.assertEqual(stored["subject"], "kc-subject-2")
        self.assertEqual(stored["source_name"], "keycloak")
        self.assertEqual(stored["admin_group"], "local-admins")


if __name__ == "__main__":
    unittest.main()
