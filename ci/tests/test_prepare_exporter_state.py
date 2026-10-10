"""Ownership migration behavior for the portable log exporter cursor volume."""

from __future__ import annotations

import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch


class TestPrepareExporterState(unittest.TestCase):
    def test_module_entrypoint_returns_the_cli_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-exporter-entrypoint-") as directory:
            # Arrange
            arguments = ["prepare_exporter_state", "--directory", directory]
            module = Path(__file__).parents[1] / "prepare_exporter_state.py"

            # Act
            with (
                patch.object(sys, "argv", arguments),
                patch("ci.prepare_exporter_state.os.chown"),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(module), run_name="__main__")

            # Assert
            self.assertEqual(result.exception.code, 0)

    def test_cli_prepares_directory_and_reports_invalid_mounts(self) -> None:
        from ci.prepare_exporter_state import main

        with tempfile.TemporaryDirectory(prefix="python-demo-exporter-cli-") as directory:
            # Arrange
            root = Path(directory)

            # Act
            with patch("ci.prepare_exporter_state.os.chown"):
                result = main(["--directory", str(root)])

            # Assert
            self.assertEqual(result, 0)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            with redirect_stderr(StringIO()):
                self.assertEqual(main(["--directory", str(root / "missing")]), 2)

    def test_migrates_only_existing_state_to_the_exporter_uid_and_private_modes(self) -> None:
        from ci.prepare_exporter_state import prepare_state_directory

        with tempfile.TemporaryDirectory(prefix="python-demo-exporter-state-") as directory:
            # Arrange
            root = Path(directory)
            state = root / "state.json"
            state.write_text('{"schema":1}', encoding="utf-8")
            root.chmod(0o755)
            state.chmod(0o644)

            # Act
            with patch("ci.prepare_exporter_state.os.chown") as chown:
                prepare_state_directory(root)

            # Assert
            self.assertEqual(
                [call.args for call in chown.call_args_list],
                [(root, 0, 0), (state, 65532, 65532), (root, 65532, 65532)],
            )
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.read_text(encoding="utf-8"), '{"schema":1}')

    def test_refuses_symlinked_state_without_changing_its_target(self) -> None:
        from ci.prepare_exporter_state import (
            StatePreparationError,
            prepare_state_directory,
        )

        with tempfile.TemporaryDirectory(prefix="python-demo-exporter-link-") as directory:
            # Arrange
            root = Path(directory)
            target = root / "outside.json"
            target.write_text("preserve", encoding="utf-8")
            (root / "state.json").symlink_to(target)

            # Act / Assert
            with patch("ci.prepare_exporter_state.os.chown") as chown, self.assertRaisesRegex(
                StatePreparationError, "regular file"
            ):
                prepare_state_directory(root)
            self.assertEqual(
                [call.args for call in chown.call_args_list],
                [(root, 0, 0), (root, 65532, 65532)],
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
