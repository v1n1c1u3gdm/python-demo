import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


class RunnerContracts(unittest.TestCase):
    def test_bootstrap_allows_local_clone_only_for_the_checkout_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_fixture_repo(root)
            subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
            home = root / "home"
            home.mkdir()
            environment = {**os.environ, "HOME": str(home)}
            helper = REPOSITORY / "ci/allow_checkout_git.sh"

            # Act: apply the same scoped Git trust rule used by bootstrap, then clone.
            subprocess.run(["bash", str(helper), str(source)], env=environment, check=True)
            destination = root / "clone"
            clone = subprocess.run(
                ["git", "clone", "--quiet", str(source), str(destination)],
                env=environment, text=True, capture_output=True, check=False,
            )
            trusted = subprocess.run(
                ["git", "config", "--global", "--get-all", "safe.directory"],
                env=environment, text=True, capture_output=True, check=True,
            ).stdout.splitlines()

            # Assert: the clone succeeds and no wildcard or unrelated path is trusted.
            self.assertEqual(clone.returncode, 0, clone.stderr)
            self.assertEqual(trusted, [str(source), str(source / ".git")])
            self.assertTrue((destination / "ci/run-local.sh").exists())

    def make_docker(self, root, exit_code=0):
        bin_dir = root / "fake docker bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\"\n"
            "previous=\"\"\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$previous\" = -v ]; then\n"
            "    case \"$argument\" in /tmp/quality-workspace*:* ) workspace=\"${argument%%:*}\";; esac\n"
            "  fi\n"
            "  previous=\"$argument\"\n"
            "done\n"
            "printf called > \"$DOCKER_CALLED_FILE\"\n"
            "if [ -f \"$workspace/link/file\" ]; then cat \"$workspace/link/file\" > \"$DOCKER_EXPOSED_FILE\"; fi\n"
            "case \"$FAKE_DOCKER_MODE\" in\n"
            "  empty) ;;\n"
            "  root-symlink) ln -s \"$FAKE_REPORT_TARGET\" \"$workspace/.ci-reports\";;\n"
            "  nested-symlink) mkdir -p \"$workspace/.ci-reports/abc123-1728000000\"; ln -s \"$FAKE_REPORT_TARGET\" \"$workspace/.ci-reports/abc123-1728000000/external\";;\n"
            "  history) git -C \"$workspace\" rev-list --all --count > \"$FAKE_HISTORY_FILE\";;\n"
            "  *) mkdir -p \"$workspace/.ci-reports/abc123-1728000000\"; printf 'fresh report\\n' > \"$workspace/.ci-reports/abc123-1728000000/fake.xml\";;\n"
            "esac\n"
            f"exit {exit_code}\n"
        )
        docker.chmod(0o755)
        return bin_dir

    def run_local(self, root, bin_dir, repo=REPOSITORY, extra_env=None):
        args_file = root / "docker args.txt"
        env = {
            **os.environ,
            "CI_REPORTS_ROOT": str(root / "reports with spaces"),
            "CI_COMMIT_SHA": "abc123",
            "CI_RUN_ID": "1728000000",
            "DOCKER_ARGS_FILE": str(args_file),
            "DOCKER_CALLED_FILE": str(root / "docker-called"),
            "DOCKER_EXPOSED_FILE": str(root / "docker-exposed"),
            "FAKE_DOCKER_MODE": "report",
            "CI_DOCKER_BIN": "docker",
            "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            **(extra_env or {}),
        }
        result = subprocess.run(
            ["bash", str(repo / "ci/run-local.sh")], cwd=repo,
            env=env, text=True, capture_output=True, check=False,
        )
        return result, args_file

    def make_fixture_repo(self, root):
        repo = root / "fixture checkout"
        (repo / "ci").mkdir(parents=True)
        (repo / ".woodpecker").mkdir()
        shutil.copy2(REPOSITORY / "ci/run-local.sh", repo / "ci/run-local.sh")
        shutil.copy2(REPOSITORY / "ci/run-gate.sh", repo / "ci/run-gate.sh")
        (repo / "ci/gates.json").write_text("{}")
        (repo / ".woodpecker/quality.yaml").write_text("steps: {}\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        return repo

    def commit_fixture(self, repo, message):
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)

    def test_local_runner_executes_the_workflow_and_propagates_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange: a previous run exists and the fake CLI reports gate failure 23.
            root = Path(temporary)
            bin_dir = self.make_docker(root, exit_code=23)
            reports = root / "reports with spaces"
            old_report = reports / "abc123-1728000000"
            old_report.mkdir(parents=True)
            (old_report / "old.txt").write_text("old run")

            # Act
            result, args_file = self.run_local(root, bin_dir)

            # Assert
            self.assertEqual(result.returncode, 23)
            args = args_file.read_text().splitlines()
            self.assertIn("woodpeckerci/woodpecker-cli:v3.18.0@sha256:cba80a18e41e29500cc72b81f788986aee3fbe4c2c9c1b9a0112aa0cc019e215", args)
            self.assertIn("exec", args)
            self.assertIn(".woodpecker/quality.yaml", args)
            self.assertTrue((old_report / "old.txt").exists())
            self.assertEqual(len(list(reports.iterdir())), 2)
            report_dirs = [path for path in reports.iterdir() if path != old_report]
            self.assertEqual(len(report_dirs), 1)
            self.assertEqual(
                (report_dirs[0] / "abc123-1728000000" / "fake.xml").read_text(),
                "fresh report\n",
            )
            self.assertNotIn("/ci-reports", args)

    def test_local_runner_preserves_bootstrap_failure_without_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = self.make_docker(root, exit_code=37)

            # Act
            result, _ = self.run_local(
                root,
                bin_dir,
                extra_env={"FAKE_DOCKER_MODE": "empty"},
            )

            # Assert
            self.assertEqual(result.returncode, 37)
            self.assertIn("No reports were produced", result.stderr)

    def test_capacity_run_uses_only_its_validated_unique_cli_container_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = self.make_docker(root, exit_code=23)

            # Act
            result, args_file = self.run_local(
                root,
                bin_dir,
                extra_env={"CI_DOCKER_CLI_NAME": "python-demo-quality-17"},
            )

            # Assert
            args = args_file.read_text().splitlines()
            self.assertEqual(result.returncode, 23, result.stdout + result.stderr + args_file.read_text())
            self.assertEqual(args[args.index("--name") + 1], "python-demo-quality-17")

    def test_local_runner_rejects_unsafe_cli_container_name_before_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = self.make_docker(root)

            # Act
            result, _ = self.run_local(
                root,
                bin_dir,
                extra_env={"CI_DOCKER_CLI_NAME": "../unrelated"},
            )

            # Assert
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid Docker CLI container name", result.stderr)
            self.assertFalse((root / "docker-called").exists())

    def test_snapshot_rejects_tracked_descendants_through_external_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_fixture_repo(root)
            tracked_file = repo / "link/file"
            tracked_file.parent.mkdir()
            tracked_file.write_text("safe tracked file")
            self.commit_fixture(repo, "tracked descendant")
            outside = root / "outside"
            outside.mkdir()
            (outside / "file").write_text("temporary outside sentinel")
            shutil.rmtree(repo / "link")
            (repo / "link").symlink_to(outside, target_is_directory=True)
            bin_dir = self.make_docker(root)

            # Act
            result, _ = self.run_local(root, bin_dir, repo=repo)

            # Assert
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", (result.stdout + result.stderr).lower())
            self.assertFalse((root / "docker-called").exists())
            self.assertFalse((root / "docker-exposed").exists())
            self.assertNotIn("temporary outside sentinel", result.stdout + result.stderr)

    def test_local_runner_rejects_root_and_nested_report_symlinks(self):
        for mode in ("root-symlink", "nested-symlink"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outside = root / "outside report target"
                outside.mkdir()
                (outside / "sentinel.txt").write_text("temporary outside report sentinel")
                bin_dir = self.make_docker(root)
                result, _ = self.run_local(
                    root,
                    bin_dir,
                    extra_env={"FAKE_DOCKER_MODE": mode, "FAKE_REPORT_TARGET": str(outside)},
                )

                # Assert
                report_files = list((root / "reports with spaces").rglob("*"))
                self.assertFalse(any(path.is_symlink() for path in report_files))
                for path in report_files:
                    if path.is_file():
                        self.assertNotIn("temporary outside report sentinel", path.read_text())
                self.assertNotIn("temporary outside report sentinel", result.stdout + result.stderr)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", (result.stdout + result.stderr).lower())

    def test_snapshot_preserves_all_local_git_refs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_fixture_repo(root)
            (repo / "base.txt").write_text("base")
            self.commit_fixture(repo, "main base")
            subprocess.run(["git", "-C", str(repo), "switch", "-qc", "feature/unmerged"], check=True)
            (repo / "feature.txt").write_text("feature")
            self.commit_fixture(repo, "unmerged feature")
            subprocess.run(["git", "-C", str(repo), "switch", "-q", "-"], check=True)
            expected_count = subprocess.run(
                ["git", "-C", str(repo), "rev-list", "--all", "--count"],
                check=True, text=True, capture_output=True,
            ).stdout.strip()
            bin_dir = self.make_docker(root)
            history_file = root / "snapshot history count"

            # Act
            self.run_local(root, bin_dir, repo=repo, extra_env={
                "FAKE_DOCKER_MODE": "history", "FAKE_HISTORY_FILE": str(history_file),
            })

            # Assert
            self.assertEqual(history_file.read_text().strip(), expected_count)

    def test_gate_records_a_status_result_for_its_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            tool = bin_dir / "passing-gate"
            tool.write_text("#!/bin/sh\necho gate-output\nexit 0\n")
            tool.chmod(0o755)
            config = root / "gates.json"
            config.write_text('{"lint":{"implemented":true,"command":["passing-gate"]}}')
            env = {
                **os.environ,
                "CI_GATES_FILE": str(config),
                "CI_REPORTS_ROOT": str(root / "reports"),
                "CI_COMMIT_SHA": "abc123",
                "CI_RUN_ID": "run-1",
                "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            }

            # Act
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/run-gate.sh"), "lint"],
                cwd=REPOSITORY, env=env, text=True, capture_output=True, check=False,
            )
            status_file = root / "reports/abc123-run-1/lint.status.json"

            # Assert
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(status_file.is_file(), "run-gate must persist a per-gate status")

    def test_reusing_a_gate_identity_records_a_conflict_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            tool = bin_dir / "passing-gate"
            tool.write_text("#!/bin/sh\nexit 0\n")
            tool.chmod(0o755)
            config = root / "gates.json"
            config.write_text('{"lint":{"implemented":true,"command":["passing-gate"]}}')
            env = {
                **os.environ,
                "CI_GATES_FILE": str(config),
                "CI_REPORTS_ROOT": str(root / "reports"),
                "CI_COMMIT_SHA": "abc123",
                "CI_RUN_ID": "run-1",
                "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            }
            command = ["bash", str(REPOSITORY / "ci/run-gate.sh"), "lint"]
            subprocess.run(command, cwd=REPOSITORY, env=env, check=True)

            # Act
            result = subprocess.run(
                command, cwd=REPOSITORY, env=env, text=True, capture_output=True, check=False,
            )
            conflict = root / "reports/abc123-run-1/lint.conflict"

            # Assert
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(conflict.is_file())

    def test_gate_preserves_nonzero_exit_code_and_reports_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Arrange
            root = Path(temporary)
            bin_dir = root / "fake tool bin"
            bin_dir.mkdir()
            tool = bin_dir / "bad gate"
            tool.write_text("#!/bin/sh\necho fresh-output\nexit 23\n")
            tool.chmod(0o755)
            config = root / "gates.json"
            config.write_text('{"lint":{"implemented":true,"command":["bad gate"]}}')
            env = {
                **os.environ,
                "CI_GATES_FILE": str(config),
                "CI_REPORTS_ROOT": str(root / "reports with spaces"),
                "CI_COMMIT_SHA": "abc123",
                "CI_RUN_ID": "1728000000",
                "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            }

            # Act
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/run-gate.sh"), "lint"],
                cwd=REPOSITORY, env=env, text=True, capture_output=True, check=False,
            )

            # Assert
            self.assertEqual(result.returncode, 23)
            self.assertIn("lint", result.stdout)
            self.assertIn("fresh-output", result.stdout)

    def test_unimplemented_gate_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "gates.json"
            config.write_text('{"pending":{"implemented":false,"reason":"fixture pending"}}')
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/run-gate.sh"), "pending"],
                cwd=REPOSITORY, env={**os.environ, "CI_GATES_FILE": str(config)},
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 125)
        self.assertIn("pending", result.stderr)
        self.assertIn("not implemented", result.stderr)

    def test_unknown_gate_fails_with_name(self):
        result = subprocess.run(
            ["bash", str(REPOSITORY / "ci/run-gate.sh"), "missing"],
            cwd=REPOSITORY, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing", result.stderr)

    def test_missing_tool_fails_with_gate_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "gates.json"
            config.write_text('{"lint":{"implemented":true,"command":["absent-ci-tool"]}}')
            env = {**os.environ, "CI_GATES_FILE": str(config), "PATH": "/usr/local/bin:/usr/bin:/bin"}
            result = subprocess.run(
                ["bash", str(REPOSITORY / "ci/run-gate.sh"), "lint"],
                cwd=REPOSITORY, env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lint", result.stderr)
            self.assertIn("absent-ci-tool", result.stderr)


if __name__ == "__main__":
    unittest.main()
