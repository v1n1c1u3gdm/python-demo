"""Behavioral contracts for CI result collection and retention."""

import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from ci.collect_results import collect_results
from ci.collect_results import main as collect_main
from ci.gate_status import main as gate_status_main
from ci.gate_status import mark_gate_conflict, write_gate_status
from ci.retention import prune_reports

GATES = ("secrets", "lint", "security", "quality", "api", "ui", "build")


def successful_report(root: Path, *, run_id: str = "run-1", commit: str = "abc123") -> Path:
    report = root / f"{commit}-{run_id}"
    report.mkdir(parents=True)
    (report / "gitleaks-history.json").write_text("[]", encoding="utf-8")
    (report / "gitleaks-working-tree.json").write_text("[]", encoding="utf-8")
    (report / "jscpd-report.json").write_text(
        json.dumps({"duplicates": [], "statistics": {"total": {
            "clones": 0, "duplicatedLines": 0, "lines": 1, "sources": 1,
            "tokens": 1, "percentage": 0.0,
        }}}),
        encoding="utf-8",
    )
    for gate in GATES:
        (report / f"{gate}.log").write_text(f"{gate} passed\n", encoding="utf-8")
        status = {
            "schema": 1,
            "gate": gate,
            "commit": commit,
            "run_id": run_id,
            "status": "passed",
            "exit_code": 0,
            "started_at": "2026-10-04T10:00:00Z",
            "finished_at": "2026-10-04T10:01:00Z",
        }
        (report / f"{gate}.status.json").write_text(json.dumps(status), encoding="utf-8")
    (report / "api-coverage.xml").write_text(
        '<coverage lines-valid="100" lines-covered="90" line-rate="0.9"/>', encoding="utf-8"
    )
    ui = report / "ui"
    ui.mkdir()
    (ui / "coverage-summary.json").write_text(
        json.dumps({"total": {"lines": {"total": 100, "covered": 90}}}), encoding="utf-8"
    )
    (report / "ci-coverage.xml").write_text(
        '<coverage lines-valid="100" lines-covered="90" line-rate="0.9"/>', encoding="utf-8"
    )
    return report


class ResultCollectionContracts(unittest.TestCase):
    def test_summary_cli_prints_success_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = successful_report(Path(temporary))
            output = StringIO()

            # Act
            with redirect_stdout(output):
                exit_code = collect_main([str(report)])

            # Assert
            self.assertEqual(exit_code, 0)
            self.assertIn("CI execution abc123/run-1: passed", output.getvalue())

    def test_summary_cli_prints_failure_and_returns_nonzero_for_missing_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = Path(temporary) / "abc123-run-1"
            report.mkdir()
            output = StringIO()
            errors = StringIO()

            # Act
            with redirect_stdout(output), redirect_stderr(errors):
                exit_code = collect_main([str(report)])

            # Assert
            self.assertNotEqual(exit_code, 0)
            self.assertIn("CI execution abc123/run-1: failed", output.getvalue())
            self.assertIn("[api] not_run", output.getvalue())
            self.assertIn("gate was not executed", errors.getvalue())

    def test_gate_status_writer_persists_terminal_identity_and_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            status_path = Path(temporary) / "api.status.json"

            # Act
            write_gate_status(
                status_path,
                gate="api",
                commit="abc123",
                run_id="run-1",
                status="passed",
                exit_code=0,
                started_at="2026-10-04T10:00:00Z",
                finished_at="2026-10-04T10:01:00Z",
            )

            # Assert
            status = json.loads(status_path.read_text())
            self.assertEqual(status["gate"], "api")
            self.assertEqual(status["run_id"], "run-1")
            self.assertEqual(status["status"], "passed")
            self.assertEqual(status["exit_code"], 0)
            self.assertIn("finished_at", status)
            self.assertEqual(stat.S_IMODE(status_path.stat().st_mode) & 0o044, 0o044)

    def test_gate_status_writer_rejects_symlinked_output_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            alias = root / "alias"
            alias.symlink_to(outside, target_is_directory=True)

            # Act / Assert
            with self.assertRaisesRegex(ValueError, "symlink"):
                write_gate_status(
                    alias / "api.status.json",
                    gate="api",
                    commit="abc123",
                    run_id="run-1",
                    status="running",
                    exit_code=125,
                    started_at="2026-10-04T10:00:00Z",
                )

    def test_gate_status_writer_records_running_and_failed_terminal_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            running_path = root / "api-running.status.json"
            failed_path = root / "api-failed.status.json"

            # Act
            write_gate_status(
                running_path,
                gate="api",
                commit="abc123",
                run_id="run-1",
                status="running",
                exit_code=125,
                started_at="2026-10-04T10:00:00Z",
            )
            write_gate_status(
                failed_path,
                gate="api",
                commit="abc123",
                run_id="run-1",
                status="failed",
                exit_code=124,
                started_at="2026-10-04T10:00:00Z",
                finished_at="2026-10-04T10:00:30Z",
            )

            # Assert
            self.assertNotIn("finished_at", json.loads(running_path.read_text()))
            failed = json.loads(failed_path.read_text())
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["exit_code"], 124)

    def test_gate_status_writer_rejects_invalid_identity_status_and_terminal_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            base = {
                "commit": "abc123",
                "run_id": "run-1",
                "status": "failed",
                "exit_code": 1,
                "started_at": "2026-10-04T10:00:00Z",
                "finished_at": "2026-10-04T10:01:00Z",
            }

            # Act / Assert
            for values, message in (
                ({**base, "gate": "../api"}, "tokens"),
                ({**base, "gate": "api", "status": "unknown"}, "invalid gate status"),
                ({**base, "gate": "api", "finished_at": None}, "finish time"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    write_gate_status(root / "gate.status.json", **values)

    def test_gate_status_cli_writes_valid_records_and_returns_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            status_path = Path(temporary) / "api.status.json"
            output = StringIO()

            # Act
            valid_code = gate_status_main([
                "write", str(status_path), "api", "abc123", "run-1", "running", "125",
                "2026-10-04T10:00:00Z",
            ])
            with redirect_stderr(output):
                invalid_code = gate_status_main([
                    "write", str(status_path), "api", "abc123", "run-1", "passed", "1",
                    "2026-10-04T10:00:00Z", "2026-10-04T10:01:00Z",
                ])

            # Assert
            self.assertEqual(valid_code, 0)
            self.assertEqual(json.loads(status_path.read_text())["status"], "running")
            self.assertEqual(invalid_code, 1)
            self.assertIn("invalid timestamps or exit code", output.getvalue())

    def test_conflict_marker_is_created_once_without_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            conflict = Path(temporary) / "api.conflict"

            # Act
            created = mark_gate_conflict(conflict, "api")
            duplicate = mark_gate_conflict(conflict, "api")

            # Assert
            self.assertTrue(created)
            self.assertFalse(duplicate)
            self.assertEqual(conflict.read_text(encoding="utf-8"), "gate output identity reused: api\n")

    def test_successful_execution_has_complete_per_gate_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = successful_report(Path(temporary))

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["identity"], {"commit": "abc123", "run_id": "run-1"})
            self.assertEqual(set(result["gates"]), set(GATES))
            self.assertTrue(all(item["status"] == "passed" for item in result["gates"].values()))
            self.assertTrue((report / "results.json").is_file())
            self.assertEqual(stat.S_IMODE((report / "results.json").stat().st_mode) & 0o044, 0o044)

    def test_missing_gates_are_explicit_and_fail_the_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = Path(temporary) / "abc123-run-1"
            report.mkdir()

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gates"]["api"]["status"], "not_run")
            self.assertNotEqual(result["exit_code"], 0)

    def test_failed_gate_never_becomes_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = successful_report(Path(temporary))
            status_file = report / "api.status.json"
            status = json.loads(status_file.read_text())
            status.update(status="failed", exit_code=1)
            status_file.write_text(json.dumps(status))

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gates"]["api"]["status"], "failed")
            self.assertEqual(json.loads((report / "results.json").read_text())["status"], "failed")

    def test_running_gate_stays_active_when_report_is_missing_and_retention_cannot_delete_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            report = successful_report(root)
            api_status = json.loads((report / "api.status.json").read_text())
            api_status.update(status="running", exit_code=125)
            api_status.pop("finished_at")
            (report / "api.status.json").write_text(json.dumps(api_status), encoding="utf-8")
            (report / "api-coverage.xml").unlink()
            expired = datetime.now(timezone.utc) - timedelta(days=15)
            os.utime(report, (expired.timestamp(), expired.timestamp()))

            # Act
            result = collect_results(report)
            removed = prune_reports(root, max_age_days=14, max_bytes=0)

            # Assert
            self.assertEqual(result["gates"]["api"]["status"], "running")
            self.assertNotEqual(result["exit_code"], 0)
            self.assertEqual(removed, [])
            self.assertTrue(report.exists())

    def test_invalid_analysis_report_schemas_fail_collection(self):
        cases = (
            ("gitleaks-history.json", "null"),
            ("gitleaks-working-tree.json", "{}"),
            ("gitleaks-history.json", '[{"RuleID":"demo"}]'),
            ("jscpd-report.json", "null"),
            ("jscpd-report.json", '{"duplicates":[]}'),
        )
        for report_name, invalid_payload in cases:
            with self.subTest(report=report_name, payload=invalid_payload), tempfile.TemporaryDirectory() as temporary:
                # Arrange
                report = successful_report(Path(temporary))
                (report / report_name).write_text(invalid_payload, encoding="utf-8")

                # Act
                result = collect_results(report)

                # Assert
                self.assertEqual(result["status"], "failed")
                self.assertNotEqual(result["exit_code"], 0)
                self.assertIn("incomplete", " ".join(result["errors"]).lower())
                self.assertTrue((report / "results.json").is_file())

    def test_real_shaped_gitleaks_and_jscpd_reports_are_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = successful_report(Path(temporary))
            finding = {
                "RuleID": "example-token",
                "File": "src/example.py",
                "StartLine": 4,
                "EndLine": 4,
            }
            (report / "gitleaks-history.json").write_text(json.dumps([finding]), encoding="utf-8")
            (report / "jscpd-report.json").write_text(json.dumps({
                "duplicates": [{
                    "format": "python",
                    "lines": 5,
                    "tokens": 50,
                    "firstFile": {"name": "one.py", "start": 1, "end": 5},
                    "secondFile": {"name": "two.py", "start": 1, "end": 5},
                }],
                "statistics": {"total": {
                    "clones": 1,
                    "duplicatedLines": 5,
                    "lines": 100,
                    "sources": 2,
                    "tokens": 500,
                    "percentage": 5.0,
                }},
            }), encoding="utf-8")

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["exit_code"], 0)

    def test_malformed_gate_and_coverage_fields_produce_persisted_failure_summaries(self):
        for malformed in ("gate-status", "ui-coverage"):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temporary:
                # Arrange
                report = successful_report(Path(temporary))
                if malformed == "gate-status":
                    path = report / "api.status.json"
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload.pop("status")
                else:
                    path = report / "ui" / "coverage-summary.json"
                    payload = {"total": []}
                path.write_text(json.dumps(payload), encoding="utf-8")

                # Act
                result = collect_results(report)

                # Assert
                self.assertEqual(result["status"], "failed")
                self.assertNotEqual(result["exit_code"], 0)
                self.assertTrue((report / "results.json").is_file())
                self.assertTrue(result["errors"])

    def test_non_object_status_records_produce_persisted_failure_summaries(self):
        for payload in ("[]", "null"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                # Arrange
                report = successful_report(Path(temporary))
                (report / "api.status.json").write_text(payload, encoding="utf-8")

                # Act
                result = collect_results(report)

                # Assert
                self.assertEqual(result["status"], "failed")
                self.assertNotEqual(result["exit_code"], 0)
                self.assertTrue((report / "results.json").is_file())
                self.assertIn("status record must be an object", " ".join(result["errors"]))

    def test_gate_output_conflict_makes_the_summary_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            report = successful_report(Path(temporary))
            (report / "lint.conflict").write_text("duplicate execution identity", encoding="utf-8")

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "failed")
            self.assertNotEqual(result["gates"]["lint"]["status"], "passed")

    def test_incomplete_report_and_cancelled_execution_fail(self):
        for status_name in ("incomplete", "cancelled", "timeout"):
            with self.subTest(status=status_name), tempfile.TemporaryDirectory() as temporary:
                # Arrange
                report = successful_report(Path(temporary))
                status_file = report / "ui.status.json"
                status = json.loads(status_file.read_text())
                status.update(status=status_name, exit_code=124 if status_name == "timeout" else 130)
                status_file.write_text(json.dumps(status))
                (report / "ui.log").unlink()

                # Act
                result = collect_results(report)

                # Assert
                self.assertEqual(result["status"], "failed")
                self.assertNotEqual(result["exit_code"], 0)

    def test_fresh_execution_identity_cannot_reuse_previous_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            successful_report(root, run_id="old-run")
            new_report = root / "abc123-new-run"
            new_report.mkdir()

            # Act
            result = collect_results(new_report)

            # Assert
            self.assertEqual(result["identity"]["run_id"], "new-run")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gates"]["build"]["status"], "not_run")

    def test_collector_rejects_symlinks_and_paths_outside_the_report_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            report = successful_report(root)
            outside = root / "outside.xml"
            outside.write_text("private report", encoding="utf-8")
            (report / "api-coverage.xml").unlink()
            (report / "api-coverage.xml").symlink_to(outside)

            # Act
            result = collect_results(report)

            # Assert
            self.assertEqual(result["status"], "failed")
            self.assertIn("symlink", " ".join(result["errors"]).lower())
            self.assertEqual(outside.read_text(), "private report")

    def test_collector_rejects_a_symlink_in_any_execution_path_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            report = successful_report(actual_parent)
            alias = root / "alias"
            alias.symlink_to(actual_parent, target_is_directory=True)

            # Act / Assert
            with self.assertRaisesRegex(ValueError, "symlink"):
                collect_results(alias / report.name)

    def test_retention_removes_expired_and_over_budget_runs_only_under_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary) / "reports"
            root.mkdir()
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            old = root / "deadbeef-old-run"
            recent = root / "deadbeef-recent-run"
            sentinel = Path(temporary) / "application"
            sentinel.mkdir()
            old.mkdir()
            recent.mkdir()
            (old / "report.json").write_bytes(b"a" * 8)
            (recent / "report.json").write_bytes(b"b" * 8)
            completed_result = lambda run_id: json.dumps({
                "schema": 1,
                "identity": {"commit": "deadbeef", "run_id": run_id},
                "status": "passed",
                "exit_code": 0,
                "gates": {gate: {"status": "passed"} for gate in GATES},
            })
            (old / "results.json").write_text(completed_result("old-run"), encoding="utf-8")
            (recent / "results.json").write_text(completed_result("recent-run"), encoding="utf-8")
            (sentinel / "keep.log").write_text("application log", encoding="utf-8")
            os.utime(old, (now.timestamp() - timedelta(days=15).total_seconds(),) * 2)
            os.utime(recent, (now.timestamp() - 60, now.timestamp() - 60))

            # Act
            removed = prune_reports(root, now=now, max_age_days=14, max_bytes=9)

            # Assert
            self.assertIn(old, removed)
            self.assertFalse(old.exists())
            self.assertFalse(recent.exists())
            self.assertEqual((sentinel / "keep.log").read_text(), "application log")

    def test_retention_preserves_arbitrary_markers_and_summaries_with_running_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary) / "reports"
            root.mkdir()
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            sentinel = root / "application-sentinel"
            active = root / "deadbeef-active-run"
            sentinel.mkdir()
            active.mkdir()
            (sentinel / "results.json").write_text("not an execution", encoding="utf-8")
            running = {
                "schema": 1,
                "identity": {"commit": "deadbeef", "run_id": "active-run"},
                "status": "failed",
                "exit_code": 1,
                "gates": {gate: {"status": "passed"} for gate in GATES},
            }
            running["gates"]["api"]["status"] = "running"
            (active / "results.json").write_text(json.dumps(running), encoding="utf-8")
            for path in (sentinel, active):
                os.utime(path, (now.timestamp() - timedelta(days=15).total_seconds(),) * 2)

            # Act
            removed = prune_reports(root, now=now, max_age_days=14, max_bytes=0)

            # Assert
            self.assertEqual(removed, [])
            self.assertTrue((sentinel / "results.json").exists())
            self.assertTrue((active / "results.json").exists())

    def test_retention_rejects_a_symlink_in_any_reports_root_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            actual_root = root / "actual reports"
            actual_root.mkdir()
            alias = root / "reports alias"
            alias.symlink_to(root, target_is_directory=True)

            # Act / Assert
            with self.assertRaisesRegex(ValueError, "symlink"):
                prune_reports(alias / actual_root.name)


if __name__ == "__main__":
    unittest.main()
