"""Behavior tests for the prepared, inactive host firewall policy."""

import ipaddress
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from ci.firewall_policy import (
    FirewallPolicyError,
    load_policy,
    main,
    render_policy,
    validate_bridges,
)


def valid_inventory():
    return {
        "schema_version": 1,
        "inventory_complete": True,
        "trusted_bridges": ["br-0123456789ab"],
        "protected_bridge_cidrs": ["172.30.0.0/16", "fd00:30::/64"],
    }


class FirewallPolicyTests(unittest.TestCase):
    def test_policy_blocks_host_private_and_l2_paths_before_public_egress(self):
        # Arrange
        policy = load_policy(valid_inventory())

        # Act
        rules = render_policy(policy)

        # Assert
        self.assertIn("table inet python_demo_ci", rules)
        self.assertIn('iifname "br-*"', rules)
        self.assertIn('iifname "docker0"', rules)
        self.assertIn("fib daddr type local drop", rules)
        self.assertIn("ip daddr 169.254.0.0/16 drop", rules)
        self.assertIn("ip6 daddr fc00::/7 drop", rules)
        self.assertIn("ip6 daddr 100:0:0:1::/64 drop", rules)
        self.assertIn("ip6 daddr 3fff::/20 drop", rules)
        self.assertIn("ip6 daddr 5f00::/16 drop", rules)
        self.assertIn("ip6 daddr fec0::/10 drop", rules)
        self.assertIn("ip6 daddr 2002::/16 drop", rules)
        self.assertIn("ip6 daddr != 2000::/3 drop", rules)
        self.assertIn("ip daddr 172.30.0.0/16 drop", rules)
        self.assertIn("ip6 daddr fd00:30::/64 drop", rules)
        self.assertIn('meta ibrname "br-*" iifname "veth*" oifname "veth*" counter drop', rules)
        self.assertIn("destroy table inet python_demo_ci", rules)
        self.assertIn("destroy table bridge python_demo_ci_l2", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertLess(rules.index("fib daddr type local drop"), rules.index("udp dport 53 accept"))
        self.assertIn("tcp dport 53 accept", rules)
        self.assertLess(rules.index("tcp dport 443 accept"), rules.index("counter drop"))
        self.assertNotIn("ct state established", rules)

    def test_ipv6_egress_is_limited_to_global_unicast_before_port_allows(self):
        # Arrange
        policy = load_policy(valid_inventory())

        # Act
        rules = render_policy(policy)

        # Assert
        allowed_public = ipaddress.ip_address("2606:4700:4700::1111")
        non_global = ipaddress.ip_address("::2")
        global_unicast = ipaddress.ip_network("2000::/3")
        self.assertIn(allowed_public, global_unicast)
        self.assertNotIn(non_global, global_unicast)
        self.assertLess(rules.index("ip6 daddr != 2000::/3 drop"), rules.index("udp dport 53 accept"))
        self.assertLess(rules.index("ip6 daddr != 2000::/3 drop"), rules.index("tcp dport 443 accept"))

    def test_policy_rejects_incomplete_bridge_inventory(self):
        # Arrange
        inventory = valid_inventory()
        inventory["inventory_complete"] = False

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "complete"):
            load_policy(inventory)

    def test_policy_rejects_missing_trusted_bridge_exceptions(self):
        # Arrange
        inventory = valid_inventory()
        inventory["trusted_bridges"] = []

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "at least one"):
            load_policy(inventory)

    def test_policy_rejects_wildcards_in_trusted_bridge_names(self):
        # Arrange
        inventory = valid_inventory()
        inventory["trusted_bridges"] = ["br-*"]

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "exact interface"):
            load_policy(inventory)

    def test_policy_rejects_injection_and_overlong_interface_names(self):
        for interface_name in ('br-good; flush ruleset', "br-1234567890123"):
            with self.subTest(interface_name=interface_name):
                # Arrange
                inventory = valid_inventory()
                inventory["trusted_bridges"] = [interface_name]

                # Act / Assert
                with self.assertRaises(FirewallPolicyError):
                    load_policy(inventory)

    def test_default_docker_bridge_cannot_be_trusted(self):
        # Arrange
        inventory = valid_inventory()
        inventory["trusted_bridges"] = ["docker0"]

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "docker0"):
            load_policy(inventory)

    def test_bridge_check_requires_every_trusted_bridge_and_supported_docker_names(self):
        # Arrange
        policy = load_policy(valid_inventory())

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "missing trusted"):
            validate_bridges(policy, ["docker0", "br-aaaaaaaaaaaa"])
        with self.assertRaisesRegex(FirewallPolicyError, "unsupported bridge"):
            validate_bridges(policy, ["br-0123456789ab", "custom0"])

    def test_bridge_check_accepts_dynamic_docker_bridges_and_explicit_exceptions(self):
        # Arrange
        policy = load_policy(valid_inventory())

        # Act
        validate_bridges(policy, ["docker0", "br-0123456789ab", "br-aaaaaaaaaaaa"])

        # Assert
        self.assertEqual(policy.trusted_bridges, ("br-0123456789ab",))

    def test_bridge_check_rejects_duplicate_observed_interfaces(self):
        # Arrange
        policy = load_policy(valid_inventory())

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "duplicate"):
            validate_bridges(policy, ["br-0123456789ab", "br-0123456789ab"])

    def test_policy_rejects_unknown_schema_fields(self):
        # Arrange
        inventory = valid_inventory()
        inventory["allow_all"] = True

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "unexpected"):
            load_policy(inventory)

    def test_policy_rejects_boolean_schema_version(self):
        # Arrange
        inventory = valid_inventory()
        inventory["schema_version"] = True

        # Act / Assert
        with self.assertRaisesRegex(FirewallPolicyError, "schema version"):
            load_policy(inventory)

    def test_policy_rejects_missing_or_invalid_protected_bridge_cidrs(self):
        for cidrs in ([], ["not-a-cidr"]):
            with self.subTest(cidrs=cidrs):
                # Arrange
                inventory = valid_inventory()
                inventory["protected_bridge_cidrs"] = cidrs

                # Act / Assert
                with self.assertRaises(FirewallPolicyError):
                    load_policy(inventory)

    def test_policy_requires_explicit_text_cidrs_and_normalizes_host_bits(self):
        for cidrs in ([True], [0], [1234], [{}], ["172.30.0.1"]):
            with self.subTest(cidrs=cidrs):
                # Arrange
                inventory = valid_inventory()
                inventory["protected_bridge_cidrs"] = cidrs

                # Act / Assert
                with self.assertRaises(FirewallPolicyError):
                    load_policy(inventory)

        # Arrange
        inventory = valid_inventory()
        inventory["protected_bridge_cidrs"] = ["172.30.5.9/16"]

        # Act
        policy = load_policy(inventory)

        # Assert
        self.assertEqual(policy.protected_bridge_cidrs, ("172.30.0.0/16",))

    def test_cli_preflights_then_renders_without_applying_host_rules(self):
        # Arrange
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "inventory.json"
            config_path.write_text(json.dumps(valid_inventory()), encoding="utf-8")
            output = StringIO()

            # Act
            with redirect_stdout(output):
                result = main([
                    "--config", str(config_path), "--bridges", "br-0123456789ab", "--render"
                ])

        # Assert
        self.assertEqual(result, 0)
        self.assertIn("table inet python_demo_ci", output.getvalue())

    def test_cli_fails_closed_for_missing_or_invalid_config(self):
        # Arrange
        errors = StringIO()

        # Act
        with redirect_stderr(errors):
            result = main(["--config", "/missing/firewall.json", "--bridges", "docker0"])

        # Assert
        self.assertEqual(result, 2)
        self.assertIn("Firewall preflight failed", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
