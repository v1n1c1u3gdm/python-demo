"""Compose rendering exercises required settings without starting services."""

import json
import os
import subprocess
import unittest


@unittest.skipUnless(os.environ.get("CI_CONTROL_COMPOSE_PROOF") == "1", "local Compose proof is opt-in")
class ControlComposeTests(unittest.TestCase):
    def render(self, settings, *, profiles=("runner", "logs")):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("WOODPECKER_")}
        environment.update(settings)
        profile_args = [argument for profile in profiles for argument in ("--profile", profile)]
        return subprocess.run(["docker", "compose", *profile_args, "--env-file", "/dev/null",
                               "-f", "ci/compose.yaml", "config", "--format", "json"], env=environment,
                              capture_output=True, text=True, check=False)

    def test_missing_credentials_fail_rendering(self):
        # Arrange / Act
        result = self.render({})
        # Assert
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required", result.stderr)

    def test_agent_and_log_exporter_are_opt_in_and_only_exporter_sees_api_token(self):
        # Arrange
        settings = {"WOODPECKER_HOST": "https://example.test/ci", "WOODPECKER_ADMIN": "maintainer",
                    "WOODPECKER_GITHUB_CLIENT": "local-fake", "WOODPECKER_GITHUB_SECRET": "local-fake",
                    "WOODPECKER_AGENT_SECRET": "local-fake", "CI_WOODPECKER_REPO_ID": "42",
                    "CI_WOODPECKER_TOKEN_FILE": "/etc/ci/api-token"}

        # Act
        result = self.render(settings)

        # Assert
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertEqual(services["agent"]["profiles"], ["runner"])
        self.assertEqual(services["exporter"]["profiles"], ["logs"])
        self.assertIn("/var/run/docker.sock", str(services["agent"]["volumes"]))
        self.assertNotIn("/var/run/docker.sock", str(services["exporter"].get("volumes", [])))
        token_mount = next(item for item in services["exporter"]["volumes"]
                           if item.get("target") == "/run/woodpecker-control/api-token")
        self.assertEqual(token_mount["source"], "/etc/ci/api-token")
        self.assertTrue(token_mount["read_only"])
        default = self.render(settings, profiles=())
        self.assertEqual(default.returncode, 0, default.stderr)
        default_services = json.loads(default.stdout)["services"]
        self.assertNotIn("agent", default_services)
        self.assertNotIn("exporter", default_services)

    def test_rendered_control_uses_tagged_safe_defaults(self):
        # Arrange
        settings = {"WOODPECKER_HOST": "https://example.test/ci", "WOODPECKER_ADMIN": "maintainer",
                    "WOODPECKER_GITHUB_CLIENT": "local-fake", "WOODPECKER_GITHUB_SECRET": "local-fake",
                    "WOODPECKER_AGENT_SECRET": "local-fake", "CI_WOODPECKER_REPO_ID": "42",
                    "CI_WOODPECKER_TOKEN_FILE": "/etc/ci/api-token"}
        # Act
        result = self.render(settings)
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        environment = services["server"]["environment"]
        # Assert
        self.assertEqual(environment["WOODPECKER_DATABASE_DRIVER"], "sqlite3")
        self.assertEqual(environment["WOODPECKER_DEFAULT_APPROVAL_MODE"], "all_events")
        self.assertEqual(environment["WOODPECKER_AUTHENTICATE_PUBLIC_REPOS"], "false")
        self.assertEqual(environment["WOODPECKER_PLUGINS_PRIVILEGED"], "")
        self.assertEqual(environment["WOODPECKER_PLUGINS_TRUSTED_CLONE"], "")
        self.assertEqual(environment["WOODPECKER_GITHUB"], "true")
        self.assertEqual(environment["WOODPECKER_GITHUB_PUBLIC_ONLY"], "true")
        self.assertEqual(services["server"]["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(services["agent"]["environment"]["WOODPECKER_MAX_WORKFLOWS"], "1")
