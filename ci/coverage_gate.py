"""Fail closed unless API and UI each meet the line coverage threshold."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

THRESHOLD = Decimal("0.85")
RATE_TOLERANCE = Decimal("0.00005")


@dataclass(frozen=True)
class CoverageResult:
    passed: bool
    failures: tuple[str, ...]
    percentages: tuple[tuple[str, Decimal], ...]


def _valid_counts(covered: Any, total: Any) -> tuple[int, int]:
    if isinstance(covered, bool) or isinstance(total, bool):
        raise TypeError("line counts must be integers")
    if not isinstance(covered, (int, str)) or not isinstance(total, (int, str)):
        raise TypeError("line counts must be integers")
    if isinstance(covered, str) and not covered.isdecimal():
        raise TypeError("line counts must be integers")
    if isinstance(total, str) and not total.isdecimal():
        raise TypeError("line counts must be integers")
    covered_count = int(covered)
    total_count = int(total)
    if covered_count < 0 or total_count <= 0 or covered_count > total_count:
        raise ValueError("line counts are empty or inconsistent")
    return covered_count, total_count


def _read_counts(module: str, report: Path) -> tuple[int, int]:
    try:
        if report.suffix == ".json":
            data = json.loads(report.read_text(encoding="utf-8"))
            lines = data["total"]["lines"]
            return _valid_counts(lines["covered"], lines["total"])
        root = ET.parse(report).getroot()
        covered, total = _valid_counts(root.attrib["lines-covered"], root.attrib["lines-valid"])
        rate = Decimal(root.attrib["line-rate"])
        if not rate.is_finite() or rate < 0 or rate > 1:
            raise ValueError("line rate is outside the valid range")
        actual_rate = Decimal(covered) / Decimal(total)
        if abs(actual_rate - rate) > RATE_TOLERANCE:
            raise ValueError("line rate does not match covered and valid line counts")
        return covered, total
    except (OSError, ET.ParseError, KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as error:
        raise ValueError(f"{module} coverage report is invalid: {report}") from error


def check_coverage_reports(
    api_report: Path, ui_report: Path, ci_report: Path | None = None
) -> CoverageResult:
    failures: list[str] = []
    percentages: list[tuple[str, Decimal]] = []
    reports = [("api", api_report), ("ui", ui_report)]
    if ci_report is not None:
        reports.append(("ci", ci_report))
    for module, report in reports:
        if not report.is_file():
            failures.append(f"{module} coverage report is missing: {report}")
            continue
        try:
            covered, total = _read_counts(module, report)
        except ValueError as error:
            failures.append(str(error))
            continue
        rate = Decimal(covered) / Decimal(total)
        percentage = rate * 100
        percentages.append((module, percentage))
        if rate < THRESHOLD:
            failures.append(f"{module} line coverage {percentage}% is below 85%")
    return CoverageResult(not failures, tuple(failures), tuple(percentages))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", type=Path, required=True, help="API Cobertura XML report")
    parser.add_argument("--ui", type=Path, required=True, help="UI coverage-summary JSON report")
    parser.add_argument("--ci", type=Path, help="CI Python Cobertura XML report")
    arguments = parser.parse_args(argv)
    result = check_coverage_reports(arguments.api, arguments.ui, arguments.ci)
    for module, percentage in result.percentages:
        print(f"{module} line coverage: {percentage}% (required: 85%)")
    for failure in result.failures:
        print(f"Coverage gate failed: {failure}", file=sys.stderr)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
