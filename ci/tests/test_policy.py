"""Behavior tests for fail-closed Woodpecker repository and account policy."""

import json
import tempfile
import unittest
from pathlib import Path

from ci.policy import PolicyError, main, validate_policy


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "repository": {
                "require_approval": "all_events",
                "approval_allowed_users": [],
                "trusted": {"network": False, "volumes": False, "security": False},
                "allow_deploy": False,
                "private": False,
                "netrc_trusted": [],
                "config_extension_endpoint": "",
                "registry_extension_endpoint": "",
                "secret_extension_endpoint": "",
            },
            "users": [{"login": "maintainer", "admin": True}],
            "manual_cron_blocked": True,
            "crons": [],
        }

    def test_accepted_policy_requires_all_events_empty_author_bypass_and_one_admin(self):
        # Arrange / Act / Assert
        validate_policy(self.policy, "maintainer")

    def test_wrong_approval_mode_is_rejected(self):
        # Arrange
        self.policy["repository"]["require_approval"] = "forks"

        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "all_events"):
            validate_policy(self.policy, "maintainer")

    def test_nonempty_author_bypass_list_is_rejected(self):
        # Arrange
        self.policy["repository"]["approval_allowed_users"] = ["maintainer"]

        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "author bypass"):
            validate_policy(self.policy, "maintainer")

    def test_trusted_repository_is_rejected(self):
        # Arrange
        self.policy["repository"]["trusted"] = True

        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "trusted"):
            validate_policy(self.policy, "maintainer")

    def test_unexpected_ci_account_is_rejected(self):
        # Arrange
        self.policy["users"].append({"login": "old-contributor", "admin": False})

        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "unexpected CI accounts"):
            validate_policy(self.policy, "maintainer")

    def test_manual_and_cron_paths_must_be_blocked_outside_contributor_yaml(self):
        # Arrange
        self.policy["manual_cron_blocked"] = False

        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "manual and cron"):
            validate_policy(self.policy, "maintainer")

    def test_legacy_boolean_trust_is_rejected(self):
        # Arrange
        self.policy["repository"]["trusted"] = False
        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "trusted"):
            validate_policy(self.policy, "maintainer")

    def test_each_privilege_is_rejected(self):
        for privilege in ("network", "volumes", "security"):
            with self.subTest(privilege=privilege):
                # Arrange
                self.policy["repository"]["trusted"][privilege] = True
                # Act / Assert
                with self.assertRaisesRegex(PolicyError, "trusted"):
                    validate_policy(self.policy, "maintainer")
                self.policy["repository"]["trusted"][privilege] = False

    def test_credential_or_deploy_extensions_are_rejected(self):
        for field, value in (("allow_deploy", True), ("private", True),
                             ("netrc_trusted", ["plugin"]),
                             ("config_extension_endpoint", "https://extension.test"),
                             ("registry_extension_endpoint", "https://extension.test"),
                             ("secret_extension_endpoint", "https://extension.test")):
            with self.subTest(field=field):
                # Arrange
                original = self.policy["repository"][field]
                self.policy["repository"][field] = value
                # Act / Assert
                with self.assertRaises(PolicyError):
                    validate_policy(self.policy, "maintainer")
                self.policy["repository"][field] = original

    def test_existing_cron_is_rejected_even_with_creation_blocked(self):
        # Arrange
        self.policy["crons"] = [{"name": "old-daily"}]
        # Act / Assert
        with self.assertRaisesRegex(PolicyError, "cron"):
            validate_policy(self.policy, "maintainer")

    def test_cli_reports_valid_invalid_and_unreadable_snapshots(self):
        # Arrange
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(self.policy), encoding="utf-8")
            # Act / Assert
            self.assertEqual(main([str(path), "--maintainer", "maintainer"]), 0)
            path.write_text("null", encoding="utf-8")
            self.assertEqual(main([str(path), "--maintainer", "maintainer"]), 1)
            path.write_text("invalid-json", encoding="utf-8")
            self.assertEqual(main([str(path), "--maintainer", "maintainer"]), 1)
            path.unlink()
            self.assertEqual(main([str(path), "--maintainer", "maintainer"]), 1)


if __name__ == "__main__":
    unittest.main()
