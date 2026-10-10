"""Behavioral tests for safe capacity measurement around the local pipeline."""

from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from ci import stack_capacity


class TestContainerSelection(unittest.TestCase):
    @patch("ci.stack_capacity._docker")
    def test_ignores_malformed_and_non_object_docker_identity_rows(self, docker: Mock) -> None:
        # Arrange
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "fixture-api\n", ""),
            subprocess.CompletedProcess([], 0, "not-json\n[]\n", ""),
        ]

        # Act
        stack_ids, job_ids = stack_capacity._container_ids(("proof-app",), set(), "17")

        # Assert
        self.assertEqual(stack_ids, {"fixture-api"})
        self.assertEqual(job_ids, set())

    @patch("ci.stack_capacity._docker")
    def test_samples_only_fixture_projects_and_the_matching_pipeline_label(self, docker: Mock) -> None:
        # Arrange
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "stack-app\n", ""),
            subprocess.CompletedProcess([], 0, "stack-ci\n", ""),
            subprocess.CompletedProcess(
                [], 0,
                '{"ID":"old-job","Names":"wp_old","Labels":"wp_step=build,wp_uuid=old"}\n'
                '{"ID":"job-17","Names":"wp_current","Labels":"wp_step=build,wp_uuid=current"}\n'
                '{"ID":"other","Names":"wp_wrong","Labels":"wp_step=lint,wp_uuid=other"}\n',
                "",
            ),
            subprocess.CompletedProcess([], 0, "CI_PIPELINE_NUMBER=17\n", ""),
            subprocess.CompletedProcess([], 0, "CI_PIPELINE_NUMBER=8\n", ""),
        ]

        # Act
        stack_ids, job_ids = stack_capacity._container_ids(
            ("proof-app", "proof-ci"), {"old-job"}, "17"
        )

        # Assert
        self.assertEqual(stack_ids, {"stack-app", "stack-ci"})
        self.assertEqual(job_ids, {"job-17"})
        self.assertEqual(
            docker.call_args_list,
            [
                call(["ps", "-q", "--filter", "label=com.docker.compose.project=proof-app"]),
                call(["ps", "-q", "--filter", "label=com.docker.compose.project=proof-ci"]),
                call(["ps", "--format", "{{json .}}"]),
                call([
                    "inspect",
                    '--format={{range .Config.Env}}{{if eq . "CI_PIPELINE_NUMBER=17"}}{{println .}}{{end}}{{end}}',
                    "job-17",
                ]),
                call([
                    "inspect",
                    '--format={{range .Config.Env}}{{if eq . "CI_PIPELINE_NUMBER=17"}}{{println .}}{{end}}{{end}}',
                    "other",
                ]),
            ],
        )

    @patch("ci.stack_capacity._docker")
    def test_stats_are_decoded_and_malformed_rows_are_ignored(self, docker: Mock) -> None:
        # Arrange
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "fixture-api-full\n", ""),
            subprocess.CompletedProcess(
                [], 0, '{"ID":"fixture-api-full","Name":"api","CPUPerc":"12.0%"}\nnot-json\n', ""
            ),
        ]

        # Act
        stats = stack_capacity._stats({"fixture-api"})

        # Assert
        self.assertEqual(stats, [{"ID": "fixture-api-full", "Name": "api", "CPUPerc": "12.0%"}])
        self.assertEqual(
            docker.call_args_list,
            [
                call(["ps", "-q", "--no-trunc"]),
                call(["stats", "--no-stream", "--format", "{{json .}}", "fixture-api-full"]),
            ],
        )

    @patch("ci.stack_capacity._docker")
    def test_stats_ignore_a_container_that_exits_between_listing_and_sampling(self, docker: Mock) -> None:
        # Arrange
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "fixture-api-full\n", ""),
            subprocess.CompletedProcess([], 1, "", "container disappeared"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]

        # Act
        samples = stack_capacity._stats({"fixture-api"})

        # Assert
        self.assertEqual(samples, [])
        self.assertEqual(docker.call_count, 3)

    @patch("ci.stack_capacity._docker")
    def test_stats_raise_when_active_container_sampling_fails_after_retry(self, docker: Mock) -> None:
        # Arrange
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "fixture-api-full\n", ""),
            subprocess.CompletedProcess([], 1, "", "daemon unavailable"),
            subprocess.CompletedProcess([], 0, "fixture-api-full\n", ""),
            subprocess.CompletedProcess([], 1, "", "daemon unavailable"),
        ]

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "could not sample CPU, memory, and block I/O"):
            stack_capacity._stats({"fixture-api"})
        self.assertEqual(docker.call_count, 4)


class TestMeasuredPipeline(unittest.TestCase):
    @patch("ci.stack_capacity.time.sleep")
    @patch("ci.stack_capacity.shutil.disk_usage")
    @patch(
        "ci.stack_capacity._stats",
        return_value=[{"ID": "job-17", "Name": "api", "MemUsage": "100MiB"}],
    )
    @patch("ci.stack_capacity._container_ids", return_value=({"fixture-api"}, {"job-17"}))
    @patch("ci.stack_capacity._docker")
    @patch("ci.stack_capacity.subprocess.Popen")
    def test_records_scoped_stack_and_job_stats_and_local_run_limits(
        self,
        popen: Mock,
        docker: Mock,
        _ids: Mock,
        _stats: Mock,
        disk_usage: Mock,
        _sleep: Mock,
    ) -> None:
        # Arrange
        process = Mock()
        process.pid = 4321
        process.poll.side_effect = [None, 0]
        process.wait.return_value = 0
        popen.return_value = process
        docker.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "df-row", ""),
        ]
        disk_usage.return_value = type("Usage", (), {"total": 1000, "used": 400, "free": 600})()
        with tempfile.TemporaryDirectory(prefix="python-demo-capacity-") as directory:
            report_file = Path(directory) / "report.json"

            # Act
            report = stack_capacity.run_pipeline_with_measurements(
                Path(directory), ("fixture-app", "fixture-ci"), report_file, "17"
            )

            # Assert
            saved = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["pipeline_exit_code"], 0)
            self.assertEqual(report["duration_seconds"], saved["duration_seconds"])
            self.assertEqual(report["pipeline_job_container_ids_observed"], ["job-17"])
            self.assertTrue(report["workflow_job_usage_observed"])
            self.assertEqual(report["container_samples"][0]["stack_container_ids"], ["fixture-api"])
            self.assertEqual(report["container_samples"][0]["pipeline_job_container_ids"], ["job-17"])
            self.assertTrue(any("not a measurement" in limit for limit in report["measurement_limits"]))
            self.assertEqual(report["workspace_filesystem_bytes_after_pipeline"]["free"], 600)
            self.assertEqual(report["workspace_filesystem_peak_used_bytes_during_pipeline"], 400)
            self.assertEqual(report["workspace_filesystem_used_delta_bytes_during_pipeline"], 0)
            self.assertEqual(report["container_samples"][0]["workspace_filesystem_bytes"]["total"], 1000)
            self.assertEqual(popen.call_args.kwargs["stdout"].name, report["pipeline_log_file"])
            self.assertEqual(
                popen.call_args.kwargs["env"]["CI_DOCKER_CLI_NAME"],
                "python-demo-quality-17",
            )

    @patch("ci.stack_capacity._docker", return_value=subprocess.CompletedProcess([], 0, "", ""))
    @patch(
        "ci.stack_capacity._container_ids",
        side_effect=[
            RuntimeError("sample failed"),
            ({"fixture-app"}, {"pipeline-owned-job"}),
            ({"fixture-app"}, set()),
        ],
    )
    @patch("ci.stack_capacity.subprocess.Popen")
    def test_sampling_failure_terminates_the_pipeline_child(
        self, popen: Mock, ids: Mock, docker: Mock
    ) -> None:
        # Arrange
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        with tempfile.TemporaryDirectory(prefix="python-demo-capacity-failure-") as directory, patch(
            "ci.stack_capacity.os.killpg"
        ) as kill_group:
            # Act / Assert
            with self.assertRaisesRegex(RuntimeError, "sample failed"):
                stack_capacity.run_pipeline_with_measurements(
                    Path(directory), ("fixture-app",), Path(directory) / "report.json", "17"
                )

            kill_group.assert_called_once_with(popen.return_value.pid, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=10)
            self.assertEqual(ids.call_args_list[1].args, (("fixture-app",), set(), "17"))
            self.assertTrue(ids.call_args_list[1].kwargs["include_stopped"])
            self.assertEqual(ids.call_args_list[2].args, (("fixture-app",), set(), "17"))
            self.assertTrue(ids.call_args_list[2].kwargs["include_stopped"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            stop_commands = [
                entry for entry in docker.call_args_list if entry.args[0][:2] == ["rm", "--force"]
            ]
            self.assertEqual(
                stop_commands,
                [
                    call(["rm", "--force", "python-demo-quality-17"]),
                    call(["rm", "--force", "pipeline-owned-job"]),
                ],
            )

    @patch("ci.stack_capacity._docker", side_effect=subprocess.TimeoutExpired("docker ps", 10))
    def test_container_listing_has_a_finite_docker_timeout(self, docker: Mock) -> None:
        # Arrange / Act / Assert
        with self.assertRaises(subprocess.TimeoutExpired):
            stack_capacity._container_ids(("fixture-app",), set(), "17")
        self.assertEqual(docker.call_count, 1)

    @patch("ci.stack_capacity.time.monotonic", side_effect=[0.0, 3601.0])
    @patch("ci.stack_capacity._container_ids", return_value=(set(), set()))
    @patch("ci.stack_capacity._docker", return_value=subprocess.CompletedProcess([], 0, "", ""))
    @patch("ci.stack_capacity.subprocess.Popen")
    def test_measurement_timeout_terminates_pipeline_and_removes_only_its_cli(
        self, popen: Mock, docker: Mock, _ids: Mock, _monotonic: Mock
    ) -> None:
        # Arrange
        process = Mock()
        process.pid = 9876
        process.poll.return_value = None
        popen.return_value = process

        with tempfile.TemporaryDirectory(prefix="python-demo-capacity-timeout-") as directory, patch(
            "ci.stack_capacity.os.killpg"
        ) as kill_group:
            # Act / Assert
            with self.assertRaisesRegex(TimeoutError, "one-hour measurement window"):
                stack_capacity.run_pipeline_with_measurements(
                    Path(directory), ("fixture-app",), Path(directory) / "report.json", "19"
                )

            kill_group.assert_called_once_with(9876, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=10)
            self.assertIn(
                call(["rm", "--force", "python-demo-quality-19"]),
                docker.call_args_list,
            )

    @patch("ci.stack_capacity._docker", return_value=subprocess.CompletedProcess([], 0, "old-id\n", ""))
    @patch("ci.stack_capacity.subprocess.Popen")
    def test_existing_cli_container_name_is_never_stopped_or_reused(
        self, popen: Mock, _docker: Mock
    ) -> None:
        # Arrange
        with tempfile.TemporaryDirectory(prefix="python-demo-capacity-name-") as directory, self.assertRaisesRegex(
            RuntimeError, "name already exists"
        ):
            # Act / Assert
            stack_capacity.run_pipeline_with_measurements(
                Path(directory), ("fixture-app",), Path(directory) / "report.json", "17"
            )
        popen.assert_not_called()
