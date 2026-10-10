"""Tests for the shared gate configuration parser."""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from ci.gate_config import gate_command, main


class GateConfigurationTests(unittest.TestCase):
    def write_config(self, directory: Path, document: object) -> Path:
        path = directory / "gates.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_configured_gate_returns_exact_command_arguments(self):
        with tempfile.TemporaryDirectory() as scratch:
            config = self.write_config(
                Path(scratch), {"api": {"implemented": True, "command": ["bash", "ci/api.sh"]}}
            )

            command = gate_command(config, "api")

            self.assertEqual(command, ("bash", "ci/api.sh"))

    def test_unknown_or_pending_gate_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            config = self.write_config(
                Path(scratch), {"api": {"implemented": False, "reason": "pending"}}
            )

            with self.assertRaisesRegex(ValueError, "is unknown"):
                gate_command(config, "ui")
            with self.assertRaisesRegex(ValueError, "not implemented"):
                gate_command(config, "api")

    def test_malformed_or_invalid_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            config = Path(scratch) / "gates.json"
            config.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid gate configuration"):
                gate_command(config, "api")
            config.write_text(json.dumps({"api": {"implemented": True, "command": []}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no valid command"):
                gate_command(config, "api")

    def test_command_rejects_non_object_and_malformed_gate_entries(self):
        with tempfile.TemporaryDirectory() as scratch:
            config = Path(scratch) / "gates.json"
            config.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(TypeError, "must be an object"):
                gate_command(config, "api")
            for entry in ("string", {"implemented": True, "command": ["echo", "line\nbreak"]}):
                config.write_text(json.dumps({"api": entry}), encoding="utf-8")
                with self.assertRaises((TypeError, ValueError)):
                    gate_command(config, "api")

    def test_command_line_returns_commands_or_documented_gate_errors(self):
        with tempfile.TemporaryDirectory() as scratch:
            config = self.write_config(Path(scratch), {
                "api": {"implemented": True, "command": ["bash", "ci/api.sh"]},
                "later": {"implemented": False},
            })

            # Act
            output = StringIO()
            with redirect_stdout(output):
                passed = main(["--config", str(config), "api"])
            errors = StringIO()
            with redirect_stderr(errors):
                pending = main(["--config", str(config), "later"])
                unknown = main(["--config", str(config), "missing"])

            # Assert
            self.assertEqual(passed, 0)
            self.assertEqual(output.getvalue().strip().splitlines(), ["bash", "ci/api.sh"])
            self.assertEqual(pending, 125)
            self.assertEqual(unknown, 2)
            self.assertIn("implementation is pending", errors.getvalue())
            self.assertIn("unknown", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
