import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


class StaticGateContracts(unittest.TestCase):
    def test_secret_scan_refuses_shallow_history_before_running_scanner(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            (repo / ".git/shallow").write_text("pretend-shallow\n")
            marker = repo / "scanner-called"
            fakebin = repo / "bin"
            fakebin.mkdir()
            scanner = fakebin / "gitleaks"
            scanner.write_text(f"#!/bin/sh\nif [ \"$1\" = version ]; then echo \"8.30.0\"; exit 0; fi\ntouch '{marker}'\nexit 0\n")
            scanner.chmod(0o755)
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/gitleaks-scan.sh")],
                cwd=repo, env={**os.environ, "PATH": f"{fakebin}:/usr/bin:/bin"},
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("shallow", (result.stdout + result.stderr).lower())
            self.assertFalse(marker.exists())

    def test_secret_scan_finds_unmerged_history_and_working_copy_with_redacted_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI"], check=True)
            (repo / "tracked.txt").write_text("safe\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-c", "unmerged-secret"], check=True)
            historical_value = "1234567890abcdef" * 2 + "12345678"
            historical_secret = 'api_key = "' + historical_value + '"\n'
            (repo / "historical.py").write_text(historical_secret)
            subprocess.run(["git", "-C", str(repo), "add", "historical.py"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "unmerged secret"], check=True)
            subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-"], check=True)
            working_value = "abcdef1234567890" * 2 + "abcdef12"
            working_secret = 'api_key = "' + working_value + '"\n'
            (repo / "working.py").write_text(working_secret)
            reports = root / "reports"
            scanner_path = shutil.which("gitleaks")
            if scanner_path is None:
                self.fail("The Gitleaks 8.30.0 executable is required for this integration test.")
            env = {
                **os.environ,
                "PATH": f"{Path(scanner_path).parent}:/usr/bin:/bin",
                "CI_REPORTS_ROOT": str(reports),
                "CI_RUN_ID": "static-gate-test",
            }

            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/gitleaks-scan.sh")],
                cwd=repo, env=env, text=True, capture_output=True, check=False,
            )
            saved_reports = [path for path in reports.rglob("*.json") if path.is_file()]
            report_text = "".join(path.read_text(errors="replace") for path in saved_reports)
            output = result.stdout + result.stderr + report_text

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(saved_reports), 2)
            self.assertNotIn(historical_value, output)
            self.assertNotIn(working_value, output)
            self.assertIn("REDACTED", report_text)


if __name__ == "__main__":
    unittest.main()
