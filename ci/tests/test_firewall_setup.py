"""Behavior tests for the administrative firewall preparation command."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ci import firewall_setup


def valid_inventory():
    return {
        "schema_version": 1,
        "inventory_complete": True,
        "trusted_bridges": ["br-0123456789ab"],
        "protected_bridge_cidrs": ["172.30.0.0/16"],
    }


class FirewallSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.config = root / "inventory.json"
        self.config.write_text(json.dumps(valid_inventory()), encoding="utf-8")
        self.output = root / "prepared.nft"

    def test_prepares_private_rules_file_after_namespace_syntax_check(self):
        # Arrange
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "ip":
                return firewall_setup.CompletedProcess(command, 0, "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n", "")
            return firewall_setup.CompletedProcess(command, 0, "", "")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 0)
        self.assertIn("table inet python_demo_ci", self.output.read_text(encoding="utf-8"))
        self.assertEqual(os.stat(self.output).st_mode & 0o777, 0o600)
        nft_call = next(call for call in calls if "nft" in call[0])
        self.assertIn("--net", nft_call[0])
        self.assertIn("-c", nft_call[0])
        self.assertIn("destroy table inet python_demo_ci", nft_call[1]["input"])

    def test_rejects_incomplete_inventory_without_replacing_existing_output(self):
        # Arrange
        self.config.write_text(json.dumps({**valid_inventory(), "inventory_complete": False}), encoding="utf-8")
        self.output.write_text("keep this", encoding="utf-8")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(
                self.config, self.output,
                runner=lambda *args, **kwargs: firewall_setup.CompletedProcess(args[0], 0, "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n", ""),
            )

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep this")

    def test_requires_namespace_syntax_check_to_succeed_before_writing(self):
        # Arrange
        calls = []
        self.output.write_text("previous policy", encoding="utf-8")

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ip":
                return firewall_setup.CompletedProcess(command, 0, "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n", "")
            return firewall_setup.CompletedProcess(command, 1, "", "nft syntax unsupported")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous policy")

    def test_rejects_unaccounted_bridge_before_running_nft_check(self):
        # Arrange
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ip":
                return firewall_setup.CompletedProcess(
                    command, 0,
                    "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n4: virbr0: <UP>\n", "",
                )
            return firewall_setup.CompletedProcess(command, 0, "", "")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 2)
        self.assertFalse(any("nft" in command for command in calls))
        self.assertFalse(self.output.exists())

    def test_rejects_output_that_is_the_inventory_before_running_tools(self):
        # Arrange
        original = self.config.read_text(encoding="utf-8")
        runner_calls = []

        def runner(command, **kwargs):
            runner_calls.append(command)
            stdout = "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n" if command[0] == "ip" else ""
            return firewall_setup.CompletedProcess(command, 0, stdout, "")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(
                self.config,
                self.config,
                runner=runner,
            )

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(runner_calls, [])
        self.assertEqual(self.config.read_text(encoding="utf-8"), original)

    def test_rejects_directory_symlink_swap_during_preflight_without_touching_foreign_output(self):
        # Arrange
        root = Path(self.temp_dir.name)
        slot = root / "slot"
        saved_slot = root / "slot-original"
        foreign = root / "foreign"
        slot.mkdir()
        foreign.mkdir()
        self.output = slot / "prepared.nft"
        self.output.write_text("old output", encoding="utf-8")
        foreign_output = foreign / "prepared.nft"
        foreign_output.write_text("foreign sentinel", encoding="utf-8")
        foreign_config = foreign / "inventory.json"
        foreign_config.write_text("foreign config sentinel", encoding="utf-8")

        def runner(command, **kwargs):
            if command[0] == "ip":
                slot.rename(saved_slot)
                slot.symlink_to(foreign, target_is_directory=True)
                stdout = "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n"
            else:
                stdout = ""
            return firewall_setup.CompletedProcess(command, 0, stdout, "")

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(foreign_output.read_text(encoding="utf-8"), "foreign sentinel")
        self.assertEqual(foreign_config.read_text(encoding="utf-8"), "foreign config sentinel")
        self.assertEqual((saved_slot / "prepared.nft").read_text(encoding="utf-8"), "old output")

    def test_times_out_external_command_and_preserves_existing_output(self):
        # Arrange
        self.output.write_text("previous rules", encoding="utf-8")

        def runner(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 15)
            raise subprocess.TimeoutExpired(command, timeout=15)

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous rules")

    def test_reports_directory_sync_failure_after_committed_private_output(self):
        # Arrange
        self.output.write_text("previous rules", encoding="utf-8")
        calls = []
        error_output = StringIO()
        original_fsync = os.fsync

        def fsync_with_directory_failure(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError("simulated directory sync failure")
            return original_fsync(descriptor)

        def runner(command, **kwargs):
            stdout = "2: docker0: <UP>\n3: br-0123456789ab: <UP>\n" if command[0] == "ip" else ""
            return firewall_setup.CompletedProcess(command, 0, stdout, "")

        # Act
        with (
            patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(firewall_setup.os, "fsync", side_effect=fsync_with_directory_failure),
            redirect_stderr(error_output),
        ):
            result = firewall_setup.prepare(self.config, self.output, runner=runner)

        # Assert
        self.assertEqual(result, 0)
        self.assertIn("table inet python_demo_ci", self.output.read_text(encoding="utf-8"))
        self.assertEqual(os.stat(self.output).st_mode & 0o777, 0o600)
        self.assertIn("directory fsync failed", error_output.getvalue())

    def test_refuses_relative_output_path(self):
        # Arrange / Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, Path("prepared.nft"))

        # Assert
        self.assertEqual(result, 2)
        self.assertFalse(Path("prepared.nft").exists())

    def test_refuses_relative_inventory_path(self):
        # Arrange / Act
        with patch.object(firewall_setup.shutil, "which") as which:
            result = firewall_setup.prepare(Path("inventory.json"), self.output)

        # Assert
        self.assertEqual(result, 2)
        which.assert_not_called()

    def test_rejects_symlink_output_without_following_it(self):
        # Arrange
        target = Path(self.temp_dir.name) / "outside"
        target.write_text("untouched", encoding="utf-8")
        self.output.symlink_to(target)

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, self.output, runner=lambda *a, **k: None)

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_rejects_symlink_ancestor_of_output(self):
        # Arrange
        parent = Path(self.temp_dir.name) / "linked-dir"
        real_parent = Path(self.temp_dir.name) / "real-dir"
        real_parent.mkdir()
        parent.symlink_to(real_parent, target_is_directory=True)

        # Act
        with patch.object(firewall_setup.shutil, "which", return_value="/usr/bin/tool"):
            result = firewall_setup.prepare(self.config, parent / "prepared.nft")

        # Assert
        self.assertEqual(result, 2)
        self.assertFalse((real_parent / "prepared.nft").exists())

    def test_reports_missing_tools_without_installing_them(self):
        # Arrange / Act
        with patch.object(firewall_setup.shutil, "which", return_value=None), patch.object(
            firewall_setup, "subprocess"
        ) as subprocess:
            result = firewall_setup.prepare(self.config, self.output)

        # Assert
        self.assertEqual(result, 2)
        subprocess.run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_help_does_not_probe_tools_or_touch_output(self):
        # Arrange / Act
        with (
            patch.object(firewall_setup.shutil, "which") as which,
            self.assertRaises(SystemExit) as exit_result,
        ):
            firewall_setup.main(["--help"])

        # Assert
        self.assertEqual(exit_result.exception.code, 0)
        which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_module_entrypoint_displays_help(self):
        # Arrange
        repository_root = Path(__file__).resolve().parents[2]

        # Act
        result = subprocess.run(
            [sys.executable, "-m", "ci.firewall_setup", "--help"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        self.assertEqual(result.returncode, 0)
        self.assertIn("--output", result.stdout)
        self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
