import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


class ToolPolicyContracts(unittest.TestCase):
    def run_jscpd(self, root, source, format_name, filler_lines):
        fixture = root / "sources"
        fixture.mkdir()
        repeated = "\n".join(
            f"shared_{index} = (alpha + beta + charlie + delta + echo + foxtrot + golf + hotel + india + juliet)"
            for index in range(5)
        )
        for name, prefix in (("one", "left"), ("two", "right")):
            body = repeated + "\n" + "\n".join(
                f"{prefix}_{index} = {index}" for index in range(filler_lines)
            ) + "\n"
            (fixture / f"{name}.{source}").write_text(body)
        report = root / "report"
        result = subprocess.run(
            [
                "npx", "--no-install", "jscpd", "--min-lines", "5", "--min-tokens", "50",
                "--format", format_name, "--reporters", "json", "--output", str(report),
                "--threshold", "5", str(fixture),
            ],
            cwd=REPOSITORY, text=True, capture_output=True, check=False,
        )
        report_file = report / "jscpd-report.json"
        data = json.loads(report_file.read_text()) if report_file.exists() else None
        return result, data

    def run_configured_quality_gate(self, filler_lines):
        with tempfile.TemporaryDirectory(prefix=".quality-fixture-", dir=REPOSITORY / "ci") as temporary:
            fixture = Path(temporary)
            repeated = "\n".join(
                f"shared_{index} = (alpha + beta + charlie + delta + echo + foxtrot + golf + hotel + india + juliet)"
                for index in range(5)
            )
            for name, prefix in (("one", "left"), ("two", "right")):
                body = repeated + "\n" + "\n".join(
                    f"{prefix}_{index} = {index}" for index in range(filler_lines)
                ) + "\n"
                (fixture / f"{name}.py").write_text(body)
            reports = fixture / "reports"
            run_id = f"fixture-{filler_lines}"
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/quality.sh"), str(fixture.relative_to(REPOSITORY))],
                cwd=REPOSITORY,
                env={**os.environ, "CI_REPORTS_ROOT": str(reports), "CI_COMMIT_SHA": "fixture", "CI_RUN_ID": run_id},
                text=True, capture_output=True, check=False,
            )
            report_path = reports / f"fixture-{run_id}" / "jscpd-report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else None
            return result, report

    def test_configured_quality_gate_accepts_exactly_five_percent(self):
        result, report = self.run_configured_quality_gate(55)

        self.assertEqual(report["statistics"]["total"]["percentage"], 5.0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_configured_quality_gate_rejects_above_five_percent(self):
        result, report = self.run_configured_quality_gate(54)

        self.assertGreater(report["statistics"]["total"]["percentage"], 5.0)
        self.assertNotEqual(result.returncode, 0)

    def test_jscpd_detects_javascript_clones(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, report = self.run_jscpd(Path(temporary), "js", "javascript", 55)

        self.assertGreater(report["statistics"]["total"]["clones"], 0)
        self.assertEqual(result.returncode, 0)

    def test_jscpd_detects_style_clones_inside_vue_single_file_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "sources"
            fixture.mkdir()
            shared_style = "\n".join(
                f".shared-{index} {{ color: var(--alpha-{index}); background: var(--beta-{index}); margin: var(--gamma-{index}); padding: var(--delta-{index}); border: 1px solid var(--epsilon-{index}); }}"
                for index in range(6)
            )
            for component in ("First", "Second"):
                (fixture / f"{component}.vue").write_text(
                    f"<template><main>{component}</main></template>\n<style>\n{shared_style}\n</style>\n"
                )
            report = root / "report"
            result = subprocess.run(
                [
                    "npx", "--no-install", "jscpd", "--min-lines", "5", "--min-tokens", "50",
                    "--format", "vue,css", "--reporters", "json", "--output", str(report),
                    "--threshold", "100", str(fixture),
                ],
                cwd=REPOSITORY, text=True, capture_output=True, check=False,
            )
            data = json.loads((report / "jscpd-report.json").read_text())
            self.assertGreater(data["statistics"]["total"]["clones"], 0)
            self.assertIn(".vue:css", " ".join(clone["firstFile"]["name"] for clone in data["duplicates"]))
            self.assertEqual(result.returncode, 0)

    def test_ruff_complexity_accepts_ten_and_rejects_eleven(self):
        ruff = shutil.which("ruff")
        self.assertIsNotNone(ruff, "Ruff is required for the complexity policy test.")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "complexity.py"
            for branch_count, expected in ((9, 0), (10, 1)):
                conditions = "\n".join(
                    f"    if value == {index}:\n        return {index}"
                    for index in range(branch_count)
                )
                path.write_text(f"def sample(value):\n{conditions}\n    return -1\n")
                result = subprocess.run(
                    [ruff, "check", "--config", str(REPOSITORY / "api/pyproject.toml"), str(path)],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_eslint_complexity_accepts_ten_and_rejects_eleven(self):
        eslint = REPOSITORY / "ui/node_modules/.bin/eslint"
        self.assertTrue(eslint.exists(), "ESLint is required for the complexity policy test.")
        for branch_count, expected in ((9, 0), (10, 1)):
            conditions = "\n".join(f"  if (value === {index}) return {index}" for index in range(branch_count))
            source = f"export function sample(value) {{\n{conditions}\n  return -1\n}}\n"
            result = subprocess.run(
                [str(eslint), "--no-fix", "--stdin", "--stdin-filename", "src/gate-fixture.js"],
                input=source, cwd=REPOSITORY / "ui", text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def run_configured_security_gate(self, source):
        bandit = os.environ.get("BANDIT_BIN") or shutil.which("bandit")
        self.assertIsNotNone(bandit, "Bandit 1.9.4 is required for the security policy test.")
        with tempfile.TemporaryDirectory(prefix="task3-security-fixture-", dir=REPOSITORY / "api") as temporary:
            fixture = Path(temporary) / "security_fixture.py"
            fixture.write_text(source)
            bandit_path = str(Path(bandit).parent)
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/security.sh")],
                cwd=REPOSITORY,
                env={**os.environ, "PATH": f"{bandit_path}:{os.environ['PATH']}"},
                text=True, capture_output=True, check=False,
            )
        return result

    def test_configured_security_gate_ignores_low_severity_findings(self):
        result = self.run_configured_security_gate('PASSWORD = "short"\n')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("B105", result.stdout)

    def test_configured_security_gate_blocks_accepted_severity_confidence_pairs(self):
        source = (
            "import os\nimport tempfile\n\nfrom flask import Flask\n\n"
            'tempfile.mktemp(dir="/tmp")\n'
            'os.chmod("/tmp/sample", 0o777)\n'
            '\n'
            "Flask(__name__).run(debug=True)\n"
            'PASSWORD = "short"\n'
        )
        result = self.run_configured_security_gate(source)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("B108", result.stdout)  # Medium severity, medium confidence.
        self.assertIn("B201", result.stdout)  # High severity, medium confidence.
        self.assertIn("B103", result.stdout)  # Medium severity, high confidence.
        self.assertNotIn("B105", result.stdout)



if __name__ == "__main__":
    unittest.main()
