"""Contracts for authenticated Woodpecker log export and retention."""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch
from urllib.parse import urlparse

from ci.log_exporter import (
    Exporter,
    ExporterConfig,
    WoodpeckerClient,
    config_from_environment,
)

SHA = "a" * 40


def pipeline_payload(*, repo_pipeline=7, pipeline_id=77, commit=SHA, status="success", step_state="success"):
    return {
        "id": pipeline_id,
        "number": repo_pipeline,
        "commit": commit,
        "status": status,
        "finished": 1_790_000_000,
        "created": 1_790_000_000,
        "updated": 1_790_000_000,
        "workflows": [{
            "id": 8,
            "pipeline_id": pipeline_id,
            "pid": 1,
            "name": "default",
            "state": status,
            "finished": 1_790_000_000,
            "children": [{
                "id": 9,
                "pipeline_id": pipeline_id,
                "pid": 1,
                "name": "quality",
                "state": step_state,
                "exit_code": 0 if step_state == "success" else 1,
                "finished": 1_790_000_000,
            }],
        }],
    }


class FakeWoodpeckerHandler(BaseHTTPRequestHandler):
    expected_authorization: ClassVar[str] = "Bearer control-secret"
    requests: ClassVar[list] = []
    pipeline = pipeline_payload()
    deleted_logs: ClassVar[list] = []
    log_data = b"quality gate passed\nsecond line\n"
    redirect = False
    response_status = 200
    oversized = False
    response_delay = 0
    malformed_json = False
    bad_list_schema = False
    bad_pipeline_type = False

    def do_GET(self):
        if self.headers.get("Authorization") != type(self).expected_authorization:
            self.send_response(401)
            self.end_headers()
            return
        if type(self).response_delay:
            time.sleep(type(self).response_delay)
        type(self).requests.append((self.path, self.headers.get("Authorization")))
        path = urlparse(self.path).path
        if path.endswith("/pipelines"):
            payload = {} if type(self).bad_list_schema else [{"number": 7, "commit": type(self).listed_commit}]
            body = json.dumps(payload).encode()
        elif path.endswith("/pipelines/7"):
            payload = [] if type(self).bad_pipeline_type else type(self).pipeline
            body = b"{bad" if type(self).malformed_json else json.dumps(payload).encode()
        elif path.endswith("/logs/7/9/download"):
            if type(self).redirect:
                self.send_response(302)
                self.send_header("Location", "http://localhost:1/steal")
                self.end_headers()
                return
            body = type(self).log_data
        else:
            body = b"not found"
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json" if b"json" in body else "text/plain")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        if self.headers.get("Authorization") != type(self).expected_authorization:
            self.send_response(401)
            self.end_headers()
            return
        type(self).deleted_logs.append(self.path)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


class ExporterBehavior(unittest.TestCase):
    def setUp(self):
        FakeWoodpeckerHandler.requests = []
        FakeWoodpeckerHandler.pipeline = pipeline_payload()
        FakeWoodpeckerHandler.deleted_logs = []
        FakeWoodpeckerHandler.listed_commit = SHA
        FakeWoodpeckerHandler.log_data = b"quality gate passed\nsecond line\n"
        FakeWoodpeckerHandler.redirect = False
        FakeWoodpeckerHandler.response_status = 200
        FakeWoodpeckerHandler.oversized = False
        FakeWoodpeckerHandler.response_delay = 0
        FakeWoodpeckerHandler.malformed_json = False
        FakeWoodpeckerHandler.bad_list_schema = False
        FakeWoodpeckerHandler.bad_pipeline_type = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWoodpeckerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.token_path = root / "token"
        self.token_path.write_text("control-secret\n", encoding="utf-8")
        self.token_path.chmod(0o600)
        self.state_path = root / "state.json"
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/ci"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def make_exporter(self, *, max_log_bytes=1024, timeout=1, output=None):
        config = ExporterConfig(
            base_url=self.base_url,
            repo_id=42,
            token_file=self.token_path,
            state_file=self.state_path,
            max_log_bytes=max_log_bytes,
            timeout_seconds=timeout,
            poll_interval_seconds=1,
            redact_values=("control-secret",),
        )
        return Exporter(config, output=output)

    def test_client_refuses_invalid_urls_json_lists_and_pipeline_payload_types(self):
        # Arrange
        with self.assertRaisesRegex(ValueError, "URL"):
            WoodpeckerClient("http://user:password@localhost", "secret")
        with self.assertRaisesRegex(ValueError, "query"):
            WoodpeckerClient(self.base_url + "?token=secret", "secret")
        client = WoodpeckerClient(self.base_url, "control-secret", timeout=1)

        # Act / Assert
        FakeWoodpeckerHandler.malformed_json = True
        with self.assertRaisesRegex(ValueError, "malformed JSON"):
            client.get_pipeline(42, 7)
        FakeWoodpeckerHandler.malformed_json = False
        FakeWoodpeckerHandler.bad_list_schema = True
        with self.assertRaisesRegex(ValueError, "list schema"):
            client.list_pipelines(42, 1)
        FakeWoodpeckerHandler.bad_list_schema = False
        FakeWoodpeckerHandler.bad_pipeline_type = True
        with self.assertRaisesRegex(TypeError, "pipeline schema"):
            client.get_pipeline(42, 7)
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            WoodpeckerClient("http://127.0.0.1:1", "token", timeout=1).get_pipeline(42, 7)

    def test_forwards_real_step_output_with_repo_pipeline_sha_and_step_identity(self):
        # Arrange
        from io import StringIO
        output = StringIO()
        exporter = self.make_exporter(output=output)

        # Act
        count = exporter.poll_once()

        # Assert
        self.assertEqual(count, 1)
        record = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(record["repo_id"], 42)
        self.assertEqual(record["pipeline_number"], 7)
        self.assertEqual(record["commit"], SHA)
        self.assertEqual(record["step_id"], 9)
        self.assertIn("quality gate passed", record["line"])
        self.assertIn("second line", output.getvalue())
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        completion = next(record for record in records if record["record_type"] == "completion")
        pipeline_result = next(record for record in records if record["record_type"] == "pipeline-terminal")
        self.assertTrue(completion["log_downloaded"])
        self.assertEqual(completion["log_bytes"], len(FakeWoodpeckerHandler.log_data))
        self.assertEqual(pipeline_result["pipeline_status"], "success")
        self.assertTrue(pipeline_result["execution_complete"])
        self.assertTrue(all(auth == "Bearer control-secret" for _, auth in FakeWoodpeckerHandler.requests))

    def test_fake_control_api_rejects_incorrect_bearer_on_get_and_delete(self):
        # Arrange
        missing = WoodpeckerClient(self.base_url, "wrong-token", timeout=1)

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            missing.list_pipelines(42, 1)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            missing.delete_pipeline_logs(42, 7)
        self.assertEqual(FakeWoodpeckerHandler.deleted_logs, [])

    def test_refuses_pipeline_and_step_identity_mismatches(self):
        # Arrange
        FakeWoodpeckerHandler.pipeline = pipeline_payload(commit="b" * 40)
        exporter = self.make_exporter()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "commit"):
            exporter.poll_once()

    def test_refuses_a_step_attached_to_another_pipeline(self):
        # Arrange
        FakeWoodpeckerHandler.pipeline["workflows"][0]["children"][0]["pipeline_id"] = 999
        exporter = self.make_exporter()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "step identity"):
            exporter.poll_once()

    def test_skips_incomplete_execution_and_retries_after_restart(self):
        # Arrange
        from io import StringIO
        FakeWoodpeckerHandler.pipeline = pipeline_payload(status="running", step_state="running")
        first = self.make_exporter(output=StringIO())

        # Act
        first_output = first.output
        self.assertEqual(first.poll_once(), 0)
        self.assertEqual(first_output.getvalue(), "")
        FakeWoodpeckerHandler.pipeline = pipeline_payload()
        restarted = self.make_exporter(output=StringIO())
        forwarded = restarted.poll_once()
        duplicate = restarted.poll_once()

        # Assert
        self.assertEqual(forwarded, 1)
        self.assertEqual(duplicate, 0)

    def test_failure_timeout_error_and_cancellation_are_preserved_without_success_claim(self):
        # Arrange
        from io import StringIO
        payload = pipeline_payload(status="failure", step_state="failure")
        payload["workflows"][0]["children"][0]["error"] = "step timeout: password=hunter2"
        payload["errors"] = [{"message": "timeout while running quality",
                              "data": {"details": ["token=control-secret"]}}]
        FakeWoodpeckerHandler.pipeline = payload
        output = StringIO()
        exporter = self.make_exporter(output=output)

        # Act
        exporter.poll_once()

        # Assert
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        result = next(event for event in events if event["record_type"] == "pipeline-terminal")
        completion = next(event for event in events if event["record_type"] == "completion")
        self.assertEqual(result["pipeline_status"], "failure")
        self.assertIn("timeout", result["pipeline_errors"][0]["message"])
        self.assertEqual(result["pipeline_errors"][0]["data"]["details"], ["token=[REDACTED]"])
        self.assertEqual(completion["step_state"], "failure")
        self.assertIn("timeout", completion["error"])
        self.assertIn("[REDACTED]", completion["error"])

    def test_terminal_pipeline_error_without_steps_preserves_errors(self):
        # Arrange
        from io import StringIO
        FakeWoodpeckerHandler.pipeline = {
            "id": 77, "number": 7, "commit": SHA, "status": "error", "finished": 0,
            "updated": 1_790_000_000, "created": 1_790_000_000, "errors": [{"message": "step timeout"}],
        }
        output = StringIO()
        exporter = self.make_exporter(output=output)

        # Act
        exporter.poll_once()

        # Assert
        result = json.loads(output.getvalue())
        self.assertEqual(result["pipeline_status"], "error")
        self.assertEqual(result["pipeline_errors"][0]["message"], "step timeout")
        self.assertTrue(result["execution_complete"])
        self.assertEqual(result["steps"], [])

    def test_terminal_pipeline_with_running_step_is_marked_incomplete(self):
        # Arrange
        from io import StringIO
        FakeWoodpeckerHandler.pipeline = pipeline_payload(status="success", step_state="running")
        output = StringIO()
        exporter = self.make_exporter(output=output)

        # Act
        exporter.poll_once()

        # Assert
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        result = next(event for event in events if event["record_type"] == "pipeline-terminal")
        self.assertFalse(result["execution_complete"])
        self.assertEqual(result["steps"][0]["state"], "running")
        self.assertFalse(any(event["record_type"] == "completion" for event in events))

    def test_escapes_control_characters_and_redacts_api_credential(self):
        # Arrange
        from io import StringIO
        FakeWoodpeckerHandler.log_data = b"pass=abc\x1b[31mred\x00\nAuthorization: Bearer top-secret\ncontrol-secret\n"
        output = StringIO()
        exporter = self.make_exporter(output=output)

        # Act
        exporter.poll_once()

        # Assert
        self.assertNotIn("\x1b", output.getvalue())
        self.assertNotIn("\x00", output.getvalue())
        self.assertNotIn("control-secret", output.getvalue())
        self.assertNotIn("top-secret", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())
        self.assertIn("\\x1b", output.getvalue())

    def test_api_timeouts_are_reported_without_retrying_an_unbounded_read(self):
        # Arrange
        FakeWoodpeckerHandler.response_delay = 0.1
        client = WoodpeckerClient(self.base_url, "control-secret", timeout=0.01)

        # Act / Assert
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            client.get_pipeline(42, 7)

    def test_rejects_redirect_without_forwarding_authorization(self):
        # Arrange
        FakeWoodpeckerHandler.redirect = True
        client = WoodpeckerClient(self.base_url, "control-secret", timeout=1)

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            client.download_step_log(42, 7, 9, max_bytes=100)
        self.assertEqual(len(FakeWoodpeckerHandler.requests), 1)
        self.assertEqual(FakeWoodpeckerHandler.requests[0][1], "Bearer control-secret")

    def test_bounds_download_size_and_rejects_api_errors_and_malformed_json(self):
        # Arrange
        client = WoodpeckerClient(self.base_url, "control-secret", timeout=1)
        FakeWoodpeckerHandler.log_data = b"x" * 200

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "limit"):
            client.download_step_log(42, 7, 9, max_bytes=100)
        FakeWoodpeckerHandler.response_status = 500
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            client.get_pipeline(42, 7)
        FakeWoodpeckerHandler.response_status = 200
        FakeWoodpeckerHandler.pipeline = {"number": 7, "commit": SHA, "status": "success", "finished": 1}
        with self.assertRaisesRegex(ValueError, "schema"):
            self.make_exporter().poll_once()

    def test_state_reader_rejects_symlinked_insecure_and_malformed_state(self):
        # Arrange
        exporter = self.make_exporter()
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        alias = Path(self.temporary.name) / "alias.json"
        alias.symlink_to(outside)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlinks"):
            Exporter._validate_state_path(alias)
        self.state_path.write_text("{}", encoding="utf-8")
        self.state_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "schema"):
            exporter._load_state()
        self.state_path.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            exporter._load_state()
        self.state_path.write_text('{"schema": 1, "processed": "bad", "pipelines": {}}', encoding="utf-8")
        with self.assertRaisesRegex(TypeError, "schema"):
            exporter._load_state()
        self.state_path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions"):
            exporter._load_state()

    def test_private_state_is_atomic_and_deduplicates_after_process_restart(self):
        # Arrange
        from io import StringIO
        output = StringIO()
        first = self.make_exporter(output=output)

        # Act
        first.poll_once()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        second_output = StringIO()
        second = self.make_exporter(output=second_output)
        second.poll_once()

        # Assert
        self.assertEqual(state["schema"], 1)
        self.assertEqual(self.state_path.stat().st_mode & 0o077, 0)
        self.assertEqual(second_output.getvalue(), "")
        self.assertFalse(list(self.state_path.parent.glob("*.tmp")))

    def test_retention_deletes_only_terminal_logs_and_keeps_pipeline_metadata(self):
        # Arrange
        from io import StringIO
        old_finish = 1_700_000_000
        FakeWoodpeckerHandler.pipeline = pipeline_payload()
        FakeWoodpeckerHandler.pipeline["finished"] = old_finish
        state = {
            "schema": 1,
            "processed": ["42:7:9"],
            "pipelines": {"7": {"status": "success", "finished": old_finish, "bytes": 10, "complete": True}},
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.state_path.chmod(0o600)
        exporter = self.make_exporter(output=StringIO())

        # Act
        exporter.poll_once(now=datetime(2026, 10, 5, tzinfo=timezone.utc))

        # Assert
        self.assertEqual(len(FakeWoodpeckerHandler.deleted_logs), 1)
        self.assertIn("/api/repos/42/logs/7", FakeWoodpeckerHandler.deleted_logs[0])
        self.assertNotIn("/pipelines/7", FakeWoodpeckerHandler.deleted_logs[0])
        self.assertIn("7", json.loads(self.state_path.read_text())["pipelines"])

    def test_retention_enforces_logical_byte_budget_by_oldest_terminal_logs(self):
        # Arrange
        from io import StringIO
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        FakeWoodpeckerHandler.pipeline["finished"] = int(now.timestamp())
        state = {
            "schema": 1,
            "processed": ["42:7:9"],
            "pipelines": {"7": {"status": "success", "finished": int(now.timestamp()), "bytes": 10, "complete": True}},
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.state_path.chmod(0o600)
        exporter = self.make_exporter(output=StringIO())
        exporter.config = ExporterConfig(**{**exporter.config.__dict__, "max_total_bytes": 5})

        # Act
        exporter.poll_once(now=now)

        # Assert
        self.assertEqual(len(FakeWoodpeckerHandler.deleted_logs), 1)
        item = json.loads(self.state_path.read_text())["pipelines"]["7"]
        self.assertFalse(item["retained"])
        self.assertEqual(item["bytes"], 0)

    def test_retention_preserves_active_execution_logs_even_over_budget(self):
        # Arrange
        from io import StringIO
        active = pipeline_payload(status="running", step_state="running")
        FakeWoodpeckerHandler.pipeline = active
        state = {
            "schema": 1,
            "processed": [],
            "pipelines": {"7": {"status": "running", "finished": 1, "bytes": 50, "complete": False}},
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.state_path.chmod(0o600)
        exporter = self.make_exporter(output=StringIO())
        exporter.config = ExporterConfig(**{**exporter.config.__dict__, "max_total_bytes": 1})

        # Act
        exporter.poll_once(now=datetime(2026, 10, 5, tzinfo=timezone.utc))

        # Assert
        self.assertEqual(FakeWoodpeckerHandler.deleted_logs, [])
        self.assertEqual(json.loads(self.state_path.read_text())["pipelines"]["7"]["bytes"], 50)

    def test_exporter_configuration_requires_explicit_api_identity_and_private_paths(self):
        # Arrange
        environment = {
            "CI_WOODPECKER_URL": "http://127.0.0.1:8000/ci",
            "CI_WOODPECKER_REPO_ID": "42",
            "CI_WOODPECKER_TOKEN_FILE": str(self.token_path),
            "CI_EXPORTER_STATE_FILE": str(self.state_path),
        }

        # Act
        config = config_from_environment(environment)

        # Assert
        self.assertEqual(config.repo_id, 42)
        self.assertEqual(config.max_age_days, 14)
        self.assertEqual(config.max_total_bytes, 1024 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "missing required"):
            config_from_environment({})

    def test_exporter_loop_reports_errors_and_waits_between_polls(self):
        # Arrange
        from io import StringIO
        output = StringIO()
        exporter = self.make_exporter(output=output)
        exporter.poll_once = unittest.mock.Mock(side_effect=[1, RuntimeError("temporary failure"), KeyboardInterrupt])

        # Act / Assert
        with patch("ci.log_exporter.time.sleep") as pause, self.assertRaises(KeyboardInterrupt):
            exporter.run_forever()
        self.assertIn("temporary failure", output.getvalue())
        self.assertEqual(pause.call_count, 2)

    def test_exporter_rejects_nonpositive_limits(self):
        # Arrange
        config = ExporterConfig(
            base_url=self.base_url, repo_id=42, token_file=self.token_path, state_file=self.state_path,
            max_log_bytes=0,
        )

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "limits"):
            Exporter(config)

    def test_token_file_must_be_private(self):
        # Arrange
        self.token_path.chmod(0o644)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "permissions"):
            self.make_exporter().poll_once()


if __name__ == "__main__":
    unittest.main()
