"""Validate and summarize reports produced by one isolated CI execution."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

GATES = ("secrets", "lint", "security", "quality", "api", "ui", "build")
MAX_REPORT_BYTES = 100 * 1024 * 1024
IDENTITY_PATTERN = re.compile(r"^(?P<commit>[A-Za-z0-9._-]+)-(?P<run_id>[A-Za-z0-9._-]+)$")


def _safe_file(root: Path, relative: str) -> Path:
    """Resolve a fixed report path without following symlinks or escaping root."""
    candidate = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink is not allowed in report path: {relative}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError(f"report path escapes execution directory: {relative}")
    if not resolved.is_file():
        raise ValueError(f"required report is not a regular file: {relative}")
    if resolved.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"report exceeds the size limit: {relative}")
    return resolved


def _reject_symlink_ancestors(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("execution paths must not contain parent traversal")
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink is not allowed in execution path: {current}")
    return absolute


def _load_json(root: Path, relative: str) -> Any:
    return json.loads(_safe_file(root, relative).read_text(encoding="utf-8"))


def _validate_coverage(root: Path, relative: str, *, ui: bool = False) -> None:
    payload = _load_json(root, relative) if ui else None
    if ui:
        total = payload.get("total") if isinstance(payload, dict) else None
        lines = total.get("lines") if isinstance(total, dict) else None
        if not isinstance(lines, dict) or not isinstance(lines.get("total"), int) or not isinstance(lines.get("covered"), int):
            raise ValueError(f"incomplete line coverage report: {relative}")
        if lines["total"] <= 0 or not 0 <= lines["covered"] <= lines["total"]:
            raise ValueError(f"invalid line coverage counts: {relative}")
        return
    coverage_path = _safe_file(root, relative)
    document = ET.parse(coverage_path).getroot()
    try:
        total = int(document.attrib["lines-valid"])
        covered = int(document.attrib["lines-covered"])
    except (KeyError, ValueError) as error:
        raise ValueError(f"incomplete line coverage report: {relative}") from error
    if total <= 0 or covered < 0 or covered > total:
        raise ValueError(f"invalid line coverage counts: {relative}")


def _validate_gitleaks(root: Path, relative: str) -> None:
    findings = _load_json(root, relative)
    if not isinstance(findings, list):
        raise TypeError(f"incomplete Gitleaks report: {relative} must contain a list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise TypeError(f"incomplete Gitleaks finding: {relative}")
        if any(not isinstance(finding.get(key), str) or not finding[key] for key in ("RuleID", "File")):
            raise ValueError(f"incomplete Gitleaks finding fields: {relative}")
        if any(type(finding.get(key)) is not int or finding[key] < 1 for key in ("StartLine", "EndLine")):
            raise ValueError(f"invalid Gitleaks finding line numbers: {relative}")


def _validate_jscpd(root: Path, relative: str) -> None:
    report = _load_json(root, relative)
    if not isinstance(report, dict) or not isinstance(report.get("duplicates"), list):
        raise TypeError(f"incomplete jscpd report: {relative}")
    statistics = report.get("statistics")
    total = statistics.get("total") if isinstance(statistics, dict) else None
    count_fields = ("clones", "duplicatedLines", "lines", "sources", "tokens")
    if not isinstance(total, dict) or any(type(total.get(key)) is not int or total[key] < 0 for key in count_fields):
        raise ValueError(f"incomplete jscpd total statistics: {relative}")
    percentage = total.get("percentage")
    if type(percentage) not in (int, float) or not 0 <= percentage <= 100:
        raise ValueError(f"invalid jscpd total percentage: {relative}")
    for duplicate in report["duplicates"]:
        if not isinstance(duplicate, dict) or not isinstance(duplicate.get("format"), str):
            raise TypeError(f"incomplete jscpd duplicate: {relative}")
        if any(type(duplicate.get(key)) is not int or duplicate[key] < 1 for key in ("lines", "tokens")):
            raise ValueError(f"invalid jscpd duplicate counts: {relative}")
        for side in ("firstFile", "secondFile"):
            file_report = duplicate.get(side)
            if not isinstance(file_report, dict) or not isinstance(file_report.get("name"), str):
                raise TypeError(f"incomplete jscpd duplicate file: {relative}")
            if any(type(file_report.get(key)) is not int or file_report[key] < 1 for key in ("start", "end")):
                raise ValueError(f"invalid jscpd duplicate line range: {relative}")


def _read_gate(root: Path, gate: str, identity: dict[str, str]) -> dict[str, Any]:
    conflict_file = root / f"{gate}.conflict"
    if conflict_file.exists() or conflict_file.is_symlink():
        return {
            "status": "incomplete",
            "exit_code": None,
            "errors": [f"gate output identity conflict: {gate}"],
        }
    status_file = root / f"{gate}.status.json"
    if not status_file.exists() and not status_file.is_symlink():
        return {"status": "not_run", "exit_code": None, "errors": ["gate was not executed"]}
    errors: list[str] = []
    status: dict[str, Any] = {}
    gate_state = "incomplete"
    try:
        status_payload = _load_json(root, status_file.name)
        if not isinstance(status_payload, dict):
            raise TypeError("status record must be an object")
        status = status_payload
        state = status.get("status")
        if state not in {"running", "passed", "failed", "incomplete", "cancelled", "timeout"}:
            errors.append("missing or invalid gate status")
        else:
            gate_state = state
        identity_valid = True
        for key, expected in (("schema", 1), ("gate", gate), ("commit", identity["commit"]), ("run_id", identity["run_id"])):
            if status.get(key) != expected:
                errors.append(f"status identity mismatch for {key}")
                identity_valid = False
        started_valid = isinstance(status.get("started_at"), str) and bool(status["started_at"])
        if not started_valid:
            errors.append("missing start time")
        exit_code_valid = type(status.get("exit_code")) is int
        if not exit_code_valid:
            errors.append("missing exit code")
        elif state == "passed" and status["exit_code"] != 0:
            errors.append("passing status has a nonzero exit code")
        if state != "passed" or status.get("exit_code") != 0:
            errors.append(f"gate ended with status {state if state is not None else 'unknown'}")
        if state == "passed" and not status.get("finished_at"):
            errors.append("missing finish time")
        if state == "running" and not (identity_valid and started_valid and exit_code_valid):
            gate_state = "incomplete"
        _safe_file(root, f"{gate}.log")
        if gate == "secrets":
            _validate_gitleaks(root, "gitleaks-history.json")
            _validate_gitleaks(root, "gitleaks-working-tree.json")
        elif gate == "quality":
            _validate_jscpd(root, "jscpd-report.json")
        elif gate == "api":
            _validate_coverage(root, "api-coverage.xml")
        elif gate == "ui":
            _validate_coverage(root, "ui/coverage-summary.json", ui=True)
        elif gate == "build":
            _validate_coverage(root, "ci-coverage.xml")
            _safe_file(root, "ui/coverage-summary.json")
            _safe_file(root, "api-coverage.xml")
    except (OSError, UnicodeError, json.JSONDecodeError, ET.ParseError, ValueError, TypeError) as error:
        errors.append(str(error))
    if errors and gate_state == "passed":
        gate_state = "incomplete"
    return {
        "status": "passed" if not errors else gate_state,
        "exit_code": status.get("exit_code"),
        "errors": errors,
    }


def collect_results(run_dir: Path) -> dict[str, Any]:
    """Collect all gate states, persist a JSON summary, and print a readable result."""
    run_dir = _reject_symlink_ancestors(Path(run_dir))
    if not run_dir.is_dir():
        raise ValueError("execution directory must be a real directory")
    match = IDENTITY_PATTERN.fullmatch(run_dir.name)
    if not match:
        raise ValueError("execution directory must be named <commit>-<run_id>")
    root = run_dir.resolve(strict=True)
    # Commit IDs are supplied by Git/Woodpecker and do not contain hyphens;
    # execution IDs may, so retain the complete suffix as the run identity.
    commit, run_id = run_dir.name.split("-", 1)
    identity = {"commit": commit, "run_id": run_id}
    gates = {gate: _read_gate(root, gate, identity) for gate in GATES}
    errors = [f"{gate}: {error}" for gate, result in gates.items() for error in result["errors"]]
    result: dict[str, Any] = {
        "schema": 1,
        "identity": identity,
        "status": "passed" if not errors else "failed",
        "exit_code": 0 if not errors else 1,
        "gates": gates,
        "errors": errors,
    }
    destination = root / "results.json"
    if destination.is_symlink():
        result["status"] = "failed"
        result["exit_code"] = 1
        result["errors"].append("results.json is a symlink")
    else:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False) as temporary:
            json.dump(result, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary_name = temporary.name
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, destination)
    for gate, gate_result in gates.items():
        print(f"[{gate}] {gate_result['status']}")
    print(f"CI execution {identity['commit']}/{identity['run_id']}: {result['status']}")
    for error in result["errors"]:
        print(f"ERROR: {error}", file=sys.stderr)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        return int(collect_results(args.run_dir)["exit_code"])
    except (OSError, ValueError) as error:
        print(f"Cannot collect CI results: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
