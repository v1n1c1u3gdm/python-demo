"""Forward completed Woodpecker step output to stdout with a private restart cursor."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

from ci.retention import DEFAULT_MAX_AGE_DAYS, DEFAULT_MAX_BYTES

WOODPECKER_STATUSES = {
    "skipped", "pending", "running", "success", "failure", "killed", "canceled",
    "error", "blocked", "declined", "created",
}
TERMINAL_PIPELINES = {"skipped", "success", "failure", "killed", "canceled", "error", "declined"}
TERMINAL_STEPS = TERMINAL_PIPELINES
SHA_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:token|password|secret|api[_-]?key|authorization)\b\s*[:=]\s*)([^\s,;]+)"
)
KNOWN_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PIPELINE_PAGES = 10_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ExporterConfig:
    base_url: str
    repo_id: int
    token_file: Path
    state_file: Path
    max_log_bytes: int = 16 * 1024 * 1024
    timeout_seconds: float = 10
    poll_interval_seconds: float = 10
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    max_total_bytes: int = DEFAULT_MAX_BYTES
    redact_values: tuple[str, ...] = ()


class WoodpeckerClient:
    """Small authenticated client for Woodpecker v3.18 API routes."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10):
        parts = urlsplit(base_url.rstrip("/"))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Woodpecker API URL must be an absolute HTTP(S) URL without credentials")
        if parts.query or parts.fragment:
            raise ValueError("Woodpecker API URL cannot contain a query or fragment")
        self.base_url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _request(self, path: str, *, max_bytes: int, method: str = "GET") -> bytes:
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json, text/plain"},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Woodpecker API returned HTTP {response.status}")
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        expected = int(declared_length)
                    except ValueError as error:
                        raise ValueError("Woodpecker API response length is malformed") from error
                    if expected < 0 or expected > max_bytes:
                        raise ValueError("Woodpecker API response exceeds the configured limit")
                else:
                    expected = None
                chunks = []
                received = 0
                while received <= max_bytes:
                    chunk = response.read(min(64 * 1024, max_bytes + 1 - received))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                body = b"".join(chunks)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                raise RuntimeError("Woodpecker API redirects are refused") from None
            raise RuntimeError(f"Woodpecker API returned HTTP {status}") from None
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, TimeoutError):
                raise TimeoutError("Woodpecker API request timed out") from None
            raise RuntimeError("Woodpecker API request failed") from None
        if len(body) > max_bytes:
            raise ValueError("Woodpecker API response exceeds the configured limit")
        if expected is not None and len(body) != expected:
            raise ValueError("Woodpecker API response is incomplete")
        return body

    def get_json(self, path: str):
        body = self._request(path, max_bytes=MAX_JSON_BYTES)
        try:
            return json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Woodpecker API returned malformed JSON schema") from error

    def list_pipelines(self, repo_id: int, page: int):
        payload = self.get_json(f"/api/repos/{repo_id}/pipelines?page={page}&perPage=50")
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError("Woodpecker API pipeline list schema is invalid")
        return payload

    def get_pipeline(self, repo_id: int, pipeline_number: int):
        payload = self.get_json(f"/api/repos/{repo_id}/pipelines/{pipeline_number}")
        if not isinstance(payload, dict):
            raise TypeError("Woodpecker API pipeline schema is invalid")
        return payload

    def download_step_log(self, repo_id: int, pipeline_number: int, step_id: int, *, max_bytes: int) -> bytes:
        return self._request(
            f"/api/repos/{repo_id}/logs/{pipeline_number}/{step_id}/download",
            max_bytes=max_bytes,
        )

    def delete_pipeline_logs(self, repo_id: int, pipeline_number: int) -> None:
        self._request(f"/api/repos/{repo_id}/logs/{pipeline_number}", max_bytes=1024, method="DELETE")


class Exporter:
    def __init__(self, config: ExporterConfig, *, output: TextIO | None = None):
        self.config = config
        if (
            config.repo_id < 1
            or config.max_log_bytes < 1
            or config.max_age_days < 0
            or config.max_total_bytes < 1
            or config.timeout_seconds <= 0
            or config.poll_interval_seconds <= 0
        ):
            raise ValueError("exporter limits and intervals are invalid")
        self.output = output or sys.stdout
        self.state_path = self._validate_state_path(config.state_file)
        self.token = self._read_token(config.token_file)
        self.client = WoodpeckerClient(config.base_url, self.token, timeout=config.timeout_seconds)
        self.redact_values = tuple(dict.fromkeys(
            value for value in (*config.redact_values, self.token) if value
        ))

    @staticmethod
    def _validate_state_path(path: Path) -> Path:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("state file must be an absolute path without traversal")
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError("state path cannot contain symlinks")
        if path.exists() and not path.is_file():
            raise ValueError("state file must be a regular file")
        return path

    @staticmethod
    def _read_token(path: Path) -> str:
        if not path.is_absolute() or ".." in path.parts or path.is_symlink() or not path.is_file():
            raise ValueError("API token must be a regular file outside the checkout")
        try:
            path.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            pass
        else:
            raise ValueError("API token must be stored outside the checkout")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ValueError("API token file permissions must be private (0600 or stricter)")
        token = path.read_text(encoding="utf-8").strip()
        if not token or any(ord(char) < 32 for char in token):
            raise ValueError("API token file is empty or malformed")
        return token

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"schema": 1, "processed": [], "pipelines": {}}
        if self.state_path.is_symlink() or self.state_path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError("exporter state is unsafe or too large")
        if stat.S_IMODE(self.state_path.stat().st_mode) & 0o077:
            raise ValueError("exporter state permissions must be private")
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("exporter state is malformed") from error
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise ValueError("exporter state schema is invalid")
        if not isinstance(state.get("processed"), list) or not isinstance(state.get("pipelines"), dict):
            raise TypeError("exporter state schema is invalid")
        return state

    def _save_state(self, state: dict) -> None:
        serialized = json.dumps(state, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("exporter state exceeds the configured size limit")
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=".cursor-", suffix=".tmp", dir=self.state_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            directory_fd = os.open(self.state_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _integer(value, field: str, *, minimum: int = 0) -> int:
        if type(value) is not int or value < minimum:
            raise ValueError(f"Woodpecker API pipeline schema is invalid ({field})")
        return value

    def _steps(self, pipeline: dict) -> list[dict]:
        pipeline_id = self._integer(pipeline.get("id"), "id", minimum=1)
        workflows = pipeline.get("workflows", [])
        if workflows is None:
            workflows = []
        if not isinstance(workflows, list):
            raise TypeError("Woodpecker API pipeline schema is invalid (workflows)")
        steps = []
        seen = set()
        for workflow in workflows:
            if (
                not isinstance(workflow, dict)
                or type(workflow.get("id")) is not int
                or workflow.get("id") < 1
                or type(workflow.get("pipeline_id")) is not int
                or workflow.get("pipeline_id") != pipeline_id
                or workflow.get("state") not in WOODPECKER_STATUSES
                or ("error" in workflow and not isinstance(workflow["error"], str))
            ):
                raise ValueError("Woodpecker API workflow identity or state schema is invalid")
            children = workflow.get("children")
            if not isinstance(children, list):
                raise TypeError("Woodpecker API workflow children schema is invalid")
            for step in children:
                if (
                    not isinstance(step, dict)
                    or type(step.get("pipeline_id")) is not int
                    or step.get("pipeline_id") != pipeline_id
                    or step.get("state") not in WOODPECKER_STATUSES
                    or ("error" in step and not isinstance(step["error"], str))
                ):
                    raise ValueError("Woodpecker API step identity or state schema is invalid")
                step_id = self._integer(step.get("id"), "step id", minimum=1)
                if (
                    step_id in seen
                    or not isinstance(step.get("name"), str)
                    or not step["name"]
                    or type(step.get("exit_code")) is not int
                ):
                    raise ValueError("Woodpecker API step schema is invalid")
                seen.add(step_id)
                steps.append(step)
        return steps

    def _sanitize(self, text: str) -> str:
        for secret in self.redact_values:
            text = text.replace(secret, "[REDACTED]")
        text = BEARER_VALUE.sub("Bearer [REDACTED]", text)
        text = SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", text)
        text = KNOWN_TOKEN.sub("[REDACTED]", text)
        clean = []
        for char in text:
            code = ord(char)
            if char == "\n":
                clean.append("\\n")
            elif char == "\r":
                clean.append("\\r")
            elif code < 32 or 0x7F <= code <= 0x9F:
                clean.append(f"\\x{code:02x}")
            else:
                clean.append(char)
        return "".join(clean)

    def _safe_api_value(self, value):
        if isinstance(value, str):
            return self._sanitize(value)
        if isinstance(value, list):
            return [self._safe_api_value(item) for item in value]
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return {key: self._safe_api_value(item) for key, item in value.items()}
        if value is None or type(value) in {bool, int, float}:
            return value
        raise ValueError("Woodpecker API pipeline errors schema is invalid")

    def _safe_errors(self, errors):
        if errors is None:
            return []
        if not isinstance(errors, list) or any(not isinstance(error, dict) for error in errors):
            raise TypeError("Woodpecker API pipeline errors schema is invalid")
        return [self._safe_api_value(error) for error in errors]

    def _emit_pipeline_terminal(self, repo_id: int, number: int, pipeline: dict, steps: list[dict], complete: bool) -> None:
        event = {
            "source": "woodpecker-step",
            "record_type": "pipeline-terminal",
            "repo_id": repo_id,
            "pipeline_number": number,
            "commit": pipeline["commit"],
            "pipeline_status": pipeline["status"],
            "pipeline_errors": self._safe_errors(pipeline.get("errors")),
            "execution_complete": complete,
            "steps": [
                {"id": step["id"], "name": self._sanitize(step["name"]), "state": step["state"],
                 "exit_code": step.get("exit_code")}
                for step in steps
            ],
        }
        self.output.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.output.flush()

    def _emit_step(self, repo_id: int, pipeline_number: int, pipeline: dict, step: dict) -> int:
        raw = self.client.download_step_log(
            repo_id,
            pipeline_number,
            step["id"],
            max_bytes=self.config.max_log_bytes,
        )
        text = raw.decode("utf-8", errors="replace")
        emitted_lines = text.splitlines()
        for line in emitted_lines:
            event = {
                "source": "woodpecker-step",
                "record_type": "output",
                "repo_id": repo_id,
                "pipeline_number": pipeline_number,
                "commit": pipeline["commit"],
                "pipeline_status": pipeline["status"],
                "step_id": step["id"],
                "step": self._sanitize(step["name"]),
                "step_state": step["state"],
                "line": self._sanitize(line),
            }
            self.output.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        metadata = {
            "source": "woodpecker-step",
            "record_type": "completion",
            "repo_id": repo_id,
            "pipeline_number": pipeline_number,
            "commit": pipeline["commit"],
            "pipeline_status": pipeline["status"],
            "step_id": step["id"],
            "step": self._sanitize(step["name"]),
            "step_state": step["state"],
            "exit_code": step.get("exit_code"),
            "error": self._sanitize(step.get("error", "")) if isinstance(step.get("error", ""), str) else "",
            "log_downloaded": True,
            "log_bytes": len(raw),
        }
        self.output.write(json.dumps(metadata, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.output.flush()
        return len(raw)

    def _validate_pipeline(self, number: int, pipeline: dict) -> tuple[str, list[dict], int]:
        if self._integer(pipeline.get("number"), "number", minimum=1) != number:
            raise ValueError("Woodpecker API pipeline number does not match requested pipeline")
        commit = pipeline.get("commit")
        if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit):
            raise ValueError("Woodpecker API pipeline commit SHA is invalid")
        status = pipeline.get("status")
        if status not in WOODPECKER_STATUSES:
            raise ValueError("Woodpecker API pipeline status is invalid")
        finished = self._integer(pipeline.get("finished"), "finished")
        updated = self._integer(pipeline.get("updated"), "updated")
        created = self._integer(pipeline.get("created"), "created")
        steps = self._steps(pipeline)
        return status, steps, finished or updated or created

    def _apply_retention(self, repo_id: int, state: dict, now: datetime) -> None:
        items = state["pipelines"]
        cutoff = int((now - timedelta(days=self.config.max_age_days)).timestamp())
        total = sum(item["bytes"] for item in items.values() if isinstance(item, dict))
        ordered = sorted(items.items(), key=lambda pair: pair[1].get("finished", 0))
        for number, item in ordered:
            if (
                item.get("status") not in TERMINAL_PIPELINES
                or not item.get("finished")
                or item.get("complete") is not True
            ):
                continue
            if item.get("retained", True) and (item["finished"] < cutoff or total > self.config.max_total_bytes):
                # v3.18 permits deleting terminal pipeline logs separately, preserving pipeline metadata.
                self.client.delete_pipeline_logs(repo_id, int(number))
                total -= item.get("bytes", 0)
                item["bytes"] = 0
                item["retained"] = False
                prefix = f"{repo_id}:{number}:"
                state["processed"] = [key for key in state["processed"] if not key.startswith(prefix)]

    def poll_once(self, *, now: datetime | None = None) -> int:
        repo_id = self._integer(self.config.repo_id, "repo id", minimum=1)
        current = now or datetime.now(timezone.utc)
        state = self._load_state()
        processed = set(state["processed"])
        exported = 0
        inventory = []
        seen_numbers = set()
        inventory_complete = False
        for page in range(1, MAX_PIPELINE_PAGES + 1):
            summaries = self.client.list_pipelines(repo_id, page)
            if not summaries:
                inventory_complete = True
                break
            summary_by_number = {}
            for summary in summaries:
                number = self._integer(summary.get("number"), "pipeline number", minimum=1)
                if number in summary_by_number or number in seen_numbers:
                    raise ValueError("Woodpecker API pipeline list contains duplicate numbers")
                summary_by_number[number] = summary
                seen_numbers.add(number)
            for number in sorted(summary_by_number):
                details = self.client.get_pipeline(repo_id, number)
                summary_commit = summary_by_number[number].get("commit")
                if not isinstance(summary_commit, str) or not SHA_PATTERN.fullmatch(summary_commit):
                    raise ValueError("Woodpecker API pipeline list commit SHA is invalid")
                if details.get("commit") != summary_commit:
                    raise ValueError("Woodpecker API pipeline commit differs from list entry")
                status, steps, finished = self._validate_pipeline(number, details)
                inventory.append((number, details, status, steps, finished))
            if len(summaries) < 50:
                inventory_complete = True
                break
        if not inventory_complete:
            raise RuntimeError("Woodpecker API pipeline inventory is incomplete at the page limit")

        for number, details, status, steps, finished in inventory:
            if status not in TERMINAL_PIPELINES:
                continue
            if not steps and status not in {"skipped", "canceled", "error", "declined"}:
                raise ValueError("Woodpecker API terminal pipeline has no workflow steps")
            key_prefix = f"{repo_id}:{number}:"
            candidates = [step for step in steps if step.get("state") in TERMINAL_STEPS]
            pipeline_state = state["pipelines"].setdefault(
                str(number), {"status": status, "finished": finished, "bytes": 0, "retained": True}
            )
            pipeline_state.update({"status": status, "finished": finished})
            if pipeline_state.get("retained") is False:
                continue
            for step in candidates:
                step_key = key_prefix + str(step["id"])
                if step_key in processed:
                    continue
                pipeline_state["bytes"] += self._emit_step(repo_id, number, details, step)
                exported += 1
                processed.add(step_key)
                state["processed"] = sorted(processed)
                # Persist after each flushed step: a crash may repeat the whole step, never silently skip it.
                self._save_state(state)
            complete = all(step.get("state") in TERMINAL_STEPS for step in steps)
            pipeline_state["complete"] = complete
            if (
                pipeline_state.get("reported_status") != status
                or pipeline_state.get("reported_complete") != complete
            ):
                self._emit_pipeline_terminal(repo_id, number, details, steps, complete)
                pipeline_state["reported_status"] = status
                pipeline_state["reported_complete"] = complete
        self._apply_retention(repo_id, state, current)
        processed = set(state["processed"])
        state["processed"] = sorted(processed)
        self._save_state(state)
        return exported

    def run_forever(self) -> None:
        while True:
            try:
                count = self.poll_once()
                if count:
                    print(json.dumps({"source": "woodpecker-exporter", "forwarded_steps": count}), file=self.output, flush=True)
            except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as error:
                message = str(error).replace("\n", " ").replace("\r", " ")
                print(json.dumps({"source": "woodpecker-exporter", "error": message}), file=self.output, flush=True)
            time.sleep(self.config.poll_interval_seconds)


def config_from_environment(environment: dict[str, str] | None = None) -> ExporterConfig:
    values = os.environ if environment is None else environment
    required = ("CI_WOODPECKER_URL", "CI_WOODPECKER_REPO_ID", "CI_WOODPECKER_TOKEN_FILE", "CI_EXPORTER_STATE_FILE")
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError("missing required exporter settings: " + ", ".join(missing))
    try:
        repo_id = int(values["CI_WOODPECKER_REPO_ID"])
        timeout = float(values.get("CI_EXPORTER_TIMEOUT", "10"))
        interval = float(values.get("CI_EXPORTER_POLL_SECONDS", "10"))
        max_log = int(values.get("CI_EXPORTER_MAX_STEP_BYTES", str(16 * 1024 * 1024)))
        max_total = int(values.get("CI_EXPORTER_MAX_TOTAL_BYTES", str(DEFAULT_MAX_BYTES)))
        max_age = int(values.get("CI_EXPORTER_MAX_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS)))
    except ValueError as error:
        raise ValueError("exporter numeric settings are malformed") from error
    if min(repo_id, timeout, interval, max_log, max_total) <= 0 or max_age < 0:
        raise ValueError("exporter limits and intervals must be positive")
    return ExporterConfig(
        base_url=values["CI_WOODPECKER_URL"],
        repo_id=repo_id,
        token_file=Path(values["CI_WOODPECKER_TOKEN_FILE"]),
        state_file=Path(values["CI_EXPORTER_STATE_FILE"]),
        max_log_bytes=max_log,
        timeout_seconds=timeout,
        poll_interval_seconds=interval,
        max_age_days=max_age,
        max_total_bytes=max_total,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)
    try:
        exporter = Exporter(config_from_environment())
        if args.once:
            exporter.poll_once()
        else:
            exporter.run_forever()
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as error:
        print(f"woodpecker log exporter: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
