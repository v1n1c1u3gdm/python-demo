"""Socket-free regressions for Woodpecker exporter edge cases."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import patch

from ci.log_exporter import Exporter, ExporterConfig, WoodpeckerClient

SHA = "a" * 40


def pipeline(number, *, status="success", step_state="success", step_id=9, finished=1_790_000_000):
    return {
        "id": number + 100,
        "number": number,
        "commit": SHA,
        "status": status,
        "finished": finished,
        "created": finished,
        "updated": finished,
        "workflows": [{
            "id": number + 200,
            "pipeline_id": number + 100,
            "name": "default",
            "state": status,
            "children": [{
                "id": step_id,
                "pipeline_id": number + 100,
                "name": "quality",
                "state": step_state,
                "exit_code": 0,
                "pid": 0 if step_state == "pending" else 1,
            }],
        }],
    }


class MemoryClient:
    def __init__(self, pages, details, *, downloads=None):
        self.pages = pages
        self.details = details
        self.downloads = downloads or {}
        self.deleted = []

    def list_pipelines(self, _repo, page):
        return self.pages.get(page, [])

    def get_pipeline(self, _repo, number):
        return self.details[number]

    def download_step_log(self, _repo, number, _step, *, max_bytes):
        return self.downloads.get(number, b"")[:max_bytes]

    def delete_pipeline_logs(self, _repo, number):
        self.deleted.append(number)


class MemoryExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.token = root / "token"
        self.token.write_text("control-secret\n", encoding="utf-8")
        self.token.chmod(0o600)
        self.state = root / "state.json"

    def tearDown(self):
        self.temporary.cleanup()

    def exporter(self, *, output=None, total=1024):
        config = ExporterConfig(
            base_url="https://woodpecker.example/ci", repo_id=42,
            token_file=self.token, state_file=self.state,
            max_log_bytes=1024, timeout_seconds=1, poll_interval_seconds=1,
            max_total_bytes=total,
        )
        return Exporter(config, output=output or io.StringIO())

    def test_loaded_api_token_is_redacted_with_default_configuration(self):
        # Arrange
        exporter = self.exporter()

        # Act
        safe = exporter._sanitize("credential control-secret in message")

        # Assert
        self.assertNotIn("control-secret", safe)
        self.assertIn("[REDACTED]", safe)

    def test_download_rejects_eof_before_declared_content_length(self):
        # Arrange
        client = WoodpeckerClient("https://woodpecker.example/ci", "secret")
        class FakeSocket:
            def makefile(self, *_args, **_kwargs):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\npartial")

        response = HTTPResponse(FakeSocket())
        response.begin()
        client.opener = type("Opener", (), {"open": lambda *_args, **_kwargs: response})()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "incomplete"):
            client.download_step_log(42, 7, 9, max_bytes=1024)

    def test_success_with_pending_step_is_incomplete_and_retained(self):
        # Arrange
        output = io.StringIO()
        exporter = self.exporter(output=output, total=1)
        details = {7: pipeline(7, step_state="pending", finished=1)}
        exporter.client = MemoryClient(
            {1: [{"number": 7, "commit": SHA}]}, details,
        )

        # Act
        exporter.poll_once(now=datetime.fromtimestamp(1_800_000_000, timezone.utc))

        # Assert
        terminal = next(json.loads(line) for line in output.getvalue().splitlines()
                        if json.loads(line)["record_type"] == "pipeline-terminal")
        self.assertFalse(terminal["execution_complete"])
        self.assertEqual(exporter.client.deleted, [])

    def test_canceled_pipeline_with_pending_step_remains_incomplete(self):
        # Arrange
        output = io.StringIO()
        exporter = self.exporter(output=output, total=1)
        details = {7: pipeline(7, status="canceled", step_state="pending", finished=1_700_000_000)}
        exporter.client = MemoryClient({1: [{"number": 7, "commit": SHA}]}, details)

        # Act
        exporter.poll_once(now=datetime.fromtimestamp(1_800_000_000, timezone.utc))

        # Assert
        terminal = next(json.loads(line) for line in output.getvalue().splitlines()
                        if json.loads(line)["record_type"] == "pipeline-terminal")
        self.assertFalse(terminal["execution_complete"])
        self.assertEqual(exporter.client.deleted, [])

    def test_token_is_redacted_from_emitted_metadata(self):
        # Arrange
        output = io.StringIO()
        exporter = self.exporter(output=output)
        detail = pipeline(7)
        detail["workflows"][0]["children"][0]["name"] = "control-secret"
        exporter.client = MemoryClient({1: [{"number": 7, "commit": SHA}]}, {7: detail},
                                       downloads={7: b"message"})

        # Act
        exporter.poll_once()

        # Assert
        self.assertNotIn("control-secret", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_global_retention_waits_for_all_pages_and_removes_oldest_first(self):
        # Arrange
        exporter = self.exporter(total=49)
        details = {number: pipeline(number, finished=1_799_999_000 + number)
                   for number in range(1, 52)}
        pages = {
            1: [{"number": number, "commit": SHA} for number in range(51, 1, -1)],
            2: [{"number": 1, "commit": SHA}],
        }
        client = MemoryClient(pages, details, downloads={number: b"x" for number in details})
        exporter.client = client

        # Act
        exporter.poll_once(now=datetime.fromtimestamp(1_800_000_000, timezone.utc))

        # Assert
        self.assertEqual(client.deleted, [1, 2])

    def test_incomplete_page_inventory_fails_before_export_or_retention(self):
        # Arrange
        output = io.StringIO()
        exporter = self.exporter(output=output, total=49)
        details = {number: pipeline(number, finished=1_799_999_000 + number)
                   for number in range(1, 52)}
        pages = {
            1: [{"number": number, "commit": SHA} for number in range(51, 1, -1)],
            2: [{"number": 1, "commit": SHA}],
        }
        client = MemoryClient(pages, details, downloads={number: b"x" for number in details})
        exporter.client = client

        # Act / Assert
        with patch("ci.log_exporter.MAX_PIPELINE_PAGES", 1), self.assertRaisesRegex(
            RuntimeError, "inventory is incomplete"
        ):
            exporter.poll_once(now=datetime.fromtimestamp(1_800_000_000, timezone.utc))

        self.assertEqual(client.deleted, [])
        self.assertEqual(output.getvalue(), "")

    def test_retention_does_not_restore_processed_cursor_entries(self):
        # Arrange
        exporter = self.exporter(total=1)
        detail = pipeline(7, finished=1_700_000_000)
        exporter.client = MemoryClient({1: [{"number": 7, "commit": SHA}]}, {7: detail},
                                       downloads={7: b"log"})

        # Act
        exporter.poll_once(now=datetime.fromtimestamp(1_800_000_000, timezone.utc))
        state = exporter._load_state()

        # Assert
        self.assertEqual(state["processed"], [])

    def test_state_larger_than_load_limit_is_rejected_before_write(self):
        # Arrange
        exporter = self.exporter()
        state = {"schema": 1, "processed": ["x" * (16 * 1024 * 1024)], "pipelines": {}}

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "state.*limit"):
            exporter._save_state(state)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
