"""Behavior tests for required Woodpecker control-plane configuration."""

import unittest

from ci.control_config import ControlConfigError, load_control_config


class ControlConfigTests(unittest.TestCase):
    def test_missing_required_secrets_fail_without_demo_fallbacks(self):
        # Arrange
        environment = {
            "WOODPECKER_HOST": "https://viniciusmenezes.com/ci",
            "WOODPECKER_ADMIN": "maintainer",
        }

        # Act / Assert
        with self.assertRaisesRegex(ControlConfigError, "required"):
            load_control_config(environment)

    def test_valid_configuration_requires_real_secrets_and_maintainer(self):
        # Arrange
        environment = {
            "WOODPECKER_HOST": "https://viniciusmenezes.com/ci",
            "WOODPECKER_ADMIN": "maintainer",
            "WOODPECKER_GITHUB_CLIENT": "oauth-client",
            "WOODPECKER_GITHUB_SECRET": "oauth-secret",
            "WOODPECKER_AGENT_SECRET": "agent-secret",
        }

        # Act
        result = load_control_config(environment)

        # Assert
        self.assertEqual(result.host, "https://viniciusmenezes.com/ci")
        self.assertEqual(result.maintainer, "maintainer")
        self.assertNotIn("oauth-secret", repr(result))

    def test_non_https_host_or_missing_prefix_is_rejected(self):
        # Arrange
        environment = {
            "WOODPECKER_HOST": "http://example.test",
            "WOODPECKER_ADMIN": "maintainer",
            "WOODPECKER_GITHUB_CLIENT": "oauth-client",
            "WOODPECKER_GITHUB_SECRET": "oauth-secret",
            "WOODPECKER_AGENT_SECRET": "agent-secret",
        }

        # Act / Assert
        with self.assertRaisesRegex(ControlConfigError, "https URL under /ci"):
            load_control_config(environment)

    def test_placeholder_credentials_are_rejected(self):
        # Arrange
        environment = {
            "WOODPECKER_HOST": "https://viniciusmenezes.com/ci",
            "WOODPECKER_ADMIN": "maintainer",
            "WOODPECKER_GITHUB_CLIENT": "replace-me",
            "WOODPECKER_GITHUB_SECRET": "oauth-secret",
            "WOODPECKER_AGENT_SECRET": "agent-secret",
        }

        # Act / Assert
        with self.assertRaisesRegex(ControlConfigError, "placeholder"):
            load_control_config(environment)

    def test_credentials_in_host_and_multiple_admins_are_rejected(self):
        for host, admin in (("https://user:password@example.test/ci", "maintainer"),
                            ("https://example.test/ci?x=1", "maintainer"),
                            ("https://example.test/ci", "maintainer,other"),
                            ("https://example.test/ci", "")):
            with self.subTest(host=host, admin=admin):
                # Arrange
                environment = {"WOODPECKER_HOST": host, "WOODPECKER_ADMIN": admin,
                               "WOODPECKER_GITHUB_CLIENT": "local-fake",
                               "WOODPECKER_GITHUB_SECRET": "local-fake",
                               "WOODPECKER_AGENT_SECRET": "local-fake"}
                # Act / Assert
                with self.assertRaises(ControlConfigError):
                    load_control_config(environment)


if __name__ == "__main__":
    unittest.main()
