"""Behavior tests for the independent API and UI line coverage gate."""

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path

from ci.coverage_gate import _valid_counts, check_coverage_reports, main


class CoverageGateTests(unittest.TestCase):
    def write_report(self, directory: Path, module: str, line_rate: str) -> Path:
        report = directory / f"{module}-coverage.xml"
        report.write_text(
            f'<coverage line-rate="{line_rate}" branch-rate="0.01" '
            f'lines-covered="{int(Decimal(line_rate) * 100000)}" '
            'lines-valid="100000"></coverage>\n',
            encoding="utf-8",
        )
        return report

    def test_api_below_threshold_blocks_even_when_ui_is_above(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.8499")
            ui = self.write_report(reports, "ui", "0.99")

            result = check_coverage_reports(api, ui)

            self.assertFalse(result.passed)
            self.assertEqual(result.failures, ("api line coverage 84.9900% is below 85%",))

    def test_ui_below_threshold_blocks_even_when_api_is_above(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.99")
            ui = self.write_report(reports, "ui", "0.8499")

            result = check_coverage_reports(api, ui)

            self.assertFalse(result.passed)
            self.assertEqual(result.failures, ("ui line coverage 84.9900% is below 85%",))

    def test_both_modules_at_exact_threshold_pass(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.85")
            ui = self.write_report(reports, "ui", "0.85")

            result = check_coverage_reports(api, ui)

            self.assertTrue(result.passed)
            self.assertEqual(result.failures, ())

    def test_missing_or_malformed_reports_fail_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.90")
            ui = reports / "ui-coverage.xml"

            missing_result = check_coverage_reports(api, ui)
            ui.write_text("<coverage line-rate='NaN'>", encoding="utf-8")
            malformed_result = check_coverage_reports(api, ui)

            self.assertFalse(missing_result.passed)
            self.assertFalse(malformed_result.passed)
            self.assertTrue(any(message.startswith("ui coverage report is missing") for message in missing_result.failures))
            self.assertTrue(any(message.startswith("ui coverage report is invalid") for message in malformed_result.failures))

    def test_coverage_just_below_threshold_fails_even_when_percent_rounds_up(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.84999")
            ui = self.write_report(reports, "ui", "0.85")

            result = check_coverage_reports(api, ui)

            self.assertFalse(result.passed)
            self.assertIn("api line coverage 84.99900% is below 85%", result.failures)

    def test_zero_or_inconsistent_line_counts_fail_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.85")
            ui = self.write_report(reports, "ui", "0.85")
            api.write_text(
                '<coverage line-rate="0.85" lines-covered="0" lines-valid="0"></coverage>',
                encoding="utf-8",
            )
            ui.write_text(
                '<coverage line-rate="0.85" lines-covered="84" lines-valid="100"></coverage>',
                encoding="utf-8",
            )

            result = check_coverage_reports(api, ui)

            self.assertFalse(result.passed)
            self.assertEqual(len(result.failures), 2)

    def test_ci_python_coverage_has_its_own_independent_gate(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.99")
            ui = self.write_report(reports, "ui", "0.99")
            ci = self.write_report(reports, "ci", "0.8499")

            result = check_coverage_reports(api, ui, ci_report=ci)

            self.assertFalse(result.passed)
            self.assertTrue(any(message.startswith("ci line coverage") for message in result.failures))

    def test_cobertura_rounded_rate_is_accepted_when_line_counts_match(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = reports / "api-coverage.xml"
            ui = self.write_report(reports, "ui", "0.90")
            api.write_text(
                '<coverage line-rate="0.8978" lines-covered="1327" lines-valid="1478"></coverage>',
                encoding="utf-8",
            )

            result = check_coverage_reports(api, ui)

            self.assertTrue(result.passed)

    def test_json_fractional_line_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.90")
            ui = reports / "ui-summary.json"
            ui.write_text(
                '{"total":{"lines":{"covered":85.9,"total":100}}}', encoding="utf-8"
            )

            result = check_coverage_reports(api, ui)

            self.assertFalse(result.passed)
            self.assertTrue(any(message.startswith("ui coverage report is invalid") for message in result.failures))

    def test_non_line_metrics_do_not_override_line_coverage(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.85")
            ui = self.write_report(reports, "ui", "0.85")
            for report in (api, ui):
                report.write_text(
                    report.read_text(encoding="utf-8").replace(
                        'branch-rate="0.01"', 'branch-rate="0.00"'
                    ),
                    encoding="utf-8",
                )

            result = check_coverage_reports(api, ui)

            self.assertTrue(result.passed)

    def test_line_counts_require_whole_nonnegative_values_and_valid_total(self):
        # Arrange / Act / Assert
        for covered, total in ((True, 10), ("1.5", "10"), (-1, 10), (11, 10), (0, 0)):
            with self.subTest(covered=covered, total=total), self.assertRaises((TypeError, ValueError)):
                _valid_counts(covered, total)

    def test_json_summary_is_evaluated_by_exact_line_counts(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.90")
            ui = reports / "ui-summary.json"
            ui.write_text('{"total":{"lines":{"covered":85,"total":100},"branches":{"covered":0,"total":100}}}', encoding="utf-8")

            # Act
            result = check_coverage_reports(api, ui)

            # Assert
            self.assertTrue(result.passed)
            self.assertEqual(result.percentages[-1], ("ui", Decimal(85)))

    def test_cli_reports_each_module_and_returns_failure_for_invalid_xml_rate(self):
        with tempfile.TemporaryDirectory() as scratch:
            reports = Path(scratch)
            api = self.write_report(reports, "api", "0.90")
            ui = self.write_report(reports, "ui", "0.90")
            ci = self.write_report(reports, "ci", "0.90")

            # Act
            output = StringIO()
            with redirect_stdout(output):
                passed = main(["--api", str(api), "--ui", str(ui), "--ci", str(ci)])
            api.write_text('<coverage line-rate="1.2" lines-covered="90" lines-valid="100"></coverage>', encoding="utf-8")
            errors = StringIO()
            with redirect_stderr(errors):
                failed = main(["--api", str(api), "--ui", str(ui)])

            # Assert
            self.assertEqual(passed, 0)
            self.assertEqual(failed, 1)
            self.assertIn("ci line coverage: 90.0%", output.getvalue())
            self.assertIn("Coverage gate failed", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
