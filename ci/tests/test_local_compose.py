"""Static behavioral contracts for the root local Compose entrypoint."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LocalComposeTests(unittest.TestCase):
    def test_root_compose_declares_local_dependency_graph_and_isolation(self) -> None:
        # Arrange
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        # Act
        services = compose.split("services:", 1)[1].rsplit("\nnetworks:\n", 1)[0]
        networks = compose.rsplit("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]

        # Assert
        for service in (
            "local-init", "app-mysql", "app-keycloak-db-init", "app-init", "app-keycloak",
            "keycloak-init", "bookstack-prepare", "legacy-mariadb", "legacy-bookstack", "bookstack-init",
            "gitea-prepare", "legacy-gitea", "gitea-init", "gitea-oauth-init", "ci-server", "ci-init",
            "legacy-share", "app-api", "app-ui", "gateway", "otel-collector",
        ):
            self.assertRegex(services, rf"(?m)^  {re.escape(service)}:")
        for network in ("app_web", "app_data", "legacy_web", "legacy_data", "ci_web", "ci_control"):
            self.assertRegex(networks, rf"(?m)^  {network}:")
        self.assertIn("python-demo-local", compose)
        self.assertRegex(compose, r"(?m)^\s+internal:\s+true\s*$")
        self.assertNotIn("/var/run/docker.sock", compose)
        self.assertNotRegex(services, r"(?m)^  (runner|exporter):")
        self.assertIn("WOODPECKER_OPEN: \"false\"", services)
        self.assertIn("WOODPECKER_DEFAULT_APPROVAL_MODE: all_events", services)
        self.assertIn("WOODPECKER_GITEA_URL: https://app.localhost/git", services)
        self.assertIn('WOODPECKER_AUTHENTICATE_PUBLIC_REPOS: "false"', services)
        self.assertIn('WOODPECKER_PLUGINS_PRIVILEGED: ""', services)
        self.assertIn('WOODPECKER_PLUGINS_TRUSTED_CLONE: ""', services)
        self.assertIn("WOODPECKER_DEFAULT_CLONE_PLUGIN:", services)
        self.assertNotIn("infra/compose/", compose)
        self.assertIn("127.0.0.1:443:443", compose)
        mysql_service = services.split("app-mysql:", 1)[1].split("app-keycloak-db-init:", 1)[0]
        self.assertNotRegex(mysql_service, r"(?m)^\s+ports:")

    def test_gateway_routes_are_gated_by_current_generation_and_auth_is_early(self) -> None:
        # Arrange
        config = (ROOT / "infra/local/gateway.conf").read_text(encoding="utf-8")
        watcher = (ROOT / "infra/local/watch-routes.sh").read_text(encoding="utf-8")

        # Act / Assert
        self.assertIn("location ^~ /auth/", config)
        for route in ("bookstack", "gitea", "ci"):
            self.assertIn(route, watcher)
        self.assertIn("$state/ready/$route", watcher)
        self.assertIn("/ready/{bookstack,gitea,ci}", config)
        self.assertIn("bootstrap-generation", watcher)
        self.assertIn("return 404", config)
        self.assertNotIn("docker.sock", watcher + config)
        self.assertNotRegex(config, r"(?i)allow\s+all")

    def test_gateway_owns_the_public_issuer_alias_on_web_networks(self) -> None:
        # Arrange
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        gateway = compose.split("\n  gateway:\n", 1)[1].split("\n  otel-collector:", 1)[0]

        # Act / Assert
        self.assertEqual(gateway.count("aliases: [app.localhost]"), 3)
        self.assertNotIn("app_data", gateway)
        self.assertNotIn("app-api:", gateway)
        self.assertNotIn("app-ui:", gateway)

    def test_gitea_proxy_strips_public_subpath_and_backend_does_not_reapply_it(self) -> None:
        # Arrange
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        watcher = (ROOT / "infra/local/watch-routes.sh").read_text(encoding="utf-8")
        service = compose.split("\n  legacy-gitea:\n", 1)[1].split("\n  gitea-init:", 1)[0]

        # Act / Assert
        self.assertIn("rewrite ^/git/?(.*)$ /$1 break", watcher)
        self.assertIn('GITEA__server__ROOT_URL: https://app.localhost/git/', service)
        self.assertIn('GITEA__server__SERVE_FROM_SUB_PATH: "false"', service)

    def test_bookstack_maps_claimed_admin_group_to_native_admin_role(self) -> None:
        # Arrange
        init = (ROOT / "infra/local/bookstack-init.sh").read_text(encoding="utf-8")

        # Act / Assert
        self.assertIn('Role::getSystemRole("admin")', init)
        self.assertIn("BOOKSTACK_ADMIN_GROUP", init)
        self.assertIn("external_auth_id", init)
        self.assertIn("$role->save()", init)

    def test_bookstack_uses_manual_oidc_and_strict_internal_backchannel(self) -> None:
        # Arrange
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        gateway = (ROOT / "infra/local/gateway.conf").read_text(encoding="utf-8")

        # Act / Assert
        self.assertIn('OIDC_ISSUER_DISCOVER: "false"', compose)
        self.assertIn("OIDC_PUBLIC_KEY: file:///run/local-public/bookstack-id-token.pem", compose)
        self.assertIn("https://gateway:8443/auth/realms/python-demo/protocol/openid-connect/token", compose)
        self.assertIn("listen 8443 ssl", gateway)
        self.assertIn("backchannel.crt", gateway)
        backchannel = gateway.rsplit("server {", 1)[1]
        self.assertIn("resolver 127.0.0.11", backchannel)
        self.assertIn("set $identity_backend app-keycloak:8080", backchannel)
        self.assertNotIn("ssl_verify off", gateway.lower())

    def test_gitea_init_calls_api_on_native_service_not_its_own_container(self) -> None:
        # Arrange
        init_script = (ROOT / "infra/local/gitea-init.sh").read_text(encoding="utf-8")

        # Act
        api_target = "http://legacy-gitea:3000/api/v1/admin/users/admin"

        # Assert
        self.assertIn(api_target, init_script)
        self.assertNotIn("http://127.0.0.1:3000/api/v1/admin/users/admin", init_script)

    def test_ci_ready_route_reuses_manual_cron_restart_write_guards(self) -> None:
        # Arrange
        dockerfile = (ROOT / "infra/local/gateway.Dockerfile").read_text(encoding="utf-8")
        watcher = (ROOT / "infra/local/watch-routes.sh").read_text(encoding="utf-8")

        # Act / Assert
        self.assertIn("ci/proxy/woodpecker.conf", dockerfile)
        self.assertIn("woodpecker.conf", watcher)
        self.assertIn("ci-server:8000", watcher)

    def test_ci_generated_prefix_does_not_shadow_guard_regex(self) -> None:
        # Arrange
        watcher = (ROOT / "infra/local/watch-routes.sh").read_text(encoding="utf-8")
        policy = (ROOT / "ci/proxy/woodpecker.conf").read_text(encoding="utf-8")

        # Act / Assert
        self.assertIn("location /ci/", policy)
        self.assertIn("location ~ ^/ci/api/repos/", policy)
        enabled_ci = watcher.split("ci:1)", 1)[1].split("bookstack:0)", 1)[0]
        self.assertIn("/usr/local/share/woodpecker.conf", enabled_ci)
        self.assertNotIn("location ^~ /ci/", policy)

    def test_gitea_native_process_imports_only_local_public_ca(self) -> None:
        # Arrange
        dockerfile = (ROOT / "infra/local/gitea.Dockerfile").read_text(encoding="utf-8")
        start = ROOT / "infra/local/gitea-start.sh"
        init = (ROOT / "infra/local/gitea-init.sh").read_text(encoding="utf-8")

        # Act / Assert
        self.assertTrue(start.is_file())
        self.assertIn("gitea-start.sh", dockerfile)
        self.assertIn("update-ca-certificates", start.read_text(encoding="utf-8"))
        self.assertIn("update-ca-certificates", init)
        self.assertNotIn("LOCALHOST_SKIP_TLS", dockerfile + init)

    def test_local_files_are_ignored_and_excluded_from_build_context(self) -> None:
        # Arrange / Act
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        # Assert
        self.assertIn("/infra/.local/", gitignore)
        self.assertIn("/infra/secrets/local/", gitignore)
        self.assertIn("infra/.local", dockerignore)
        self.assertIn("infra/secrets", dockerignore)

    def test_bootstrap_image_contains_the_native_integration_coordinators(self) -> None:
        # Arrange
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        # Act / Assert
        for module in ("local_bookstack.py", "local_gitea.py", "local_woodpecker.py"):
            self.assertIn(module, dockerfile)

    def test_default_share_container_is_a_denied_local_placeholder(self) -> None:
        # Arrange
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        service = compose.split("\n  legacy-share:\n", 1)[1].split("\n  app-api:", 1)[0]
        config = ROOT / "infra/share/default.conf"

        # Act / Assert
        self.assertTrue(config.is_file())
        self.assertIn("legacy-share", compose)
        self.assertIn("profiles: [share]", service)
        self.assertNotIn("share.Dockerfile", compose)
        self.assertIn("return 404", config.read_text(encoding="utf-8"))

    def test_task_two_native_images_are_registered_as_pinned_inputs(self) -> None:
        # Arrange
        import json

        images = json.loads((ROOT / "infra/images.json").read_text(encoding="utf-8"))["images"]
        references = {image["service"]: image["reference"] for image in images}

        # Act / Assert
        self.assertTrue(references["woodpecker-server"].endswith("sha256:5192aee400df23671de8ddffb906670e93d07ae8967c7f9e50efefca3a2deea5"))
        self.assertTrue(references["woodpecker-cli"].endswith("sha256:cba80a18e41e29500cc72b81f788986aee3fbe4c2c9b1a0112aa0cc019e215"))
        self.assertTrue(references["woodpecker-runtime"].endswith("sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8"))

    def test_operational_compose_contract_remains_explicit_and_unchanged(self) -> None:
        # Arrange
        from subprocess import run

        # Act
        diff = run(
            ["git", "diff", "--", "docker-compose.yml", "infra/compose"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        # Assert
        self.assertEqual(diff.returncode, 0)
        self.assertEqual(diff.stdout, "")


if __name__ == "__main__":
    unittest.main()
