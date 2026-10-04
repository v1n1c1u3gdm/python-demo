"""Unit tests for optional, per-worker OpenTelemetry metrics export."""

import os

import pytest
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    MetricExportResult,
    PeriodicExportingMetricReader,
)

from config import BaseConfig
from observability import ObservabilityMetrics


def _instance_id(metrics: ObservabilityMetrics) -> str:
    data = metrics.reader.get_metrics_data()
    return str(data.resource_metrics[0].resource.attributes["service.instance.id"])


def test_exporter_is_disabled_by_default():
    # Arrange / Act
    metrics = ObservabilityMetrics("python-demo-api")

    # Assert
    assert isinstance(metrics.reader, InMemoryMetricReader)
    assert metrics.export_reader is None
    assert metrics.exporter is None


def test_app_configuration_disables_export_and_uses_bounded_defaults():
    # Arrange / Act
    settings = BaseConfig.from_environment({})

    # Assert
    assert settings["OTEL_METRICS_ENABLED"] is False
    assert settings["OTEL_EXPORT_INTERVAL_MS"] == 10_000
    assert settings["OTEL_EXPORT_TIMEOUT_SECONDS"] == 2


def test_app_configuration_can_enable_otlp_metrics_export():
    # Arrange / Act
    settings = BaseConfig.from_environment({"OTEL_METRICS_ENABLED": "true"})

    # Assert
    assert settings["OTEL_METRICS_ENABLED"] is True


def test_export_timeout_cannot_exceed_two_seconds():
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="between one and two seconds"):
        ObservabilityMetrics(
            "python-demo-api", otel_metrics_enabled=True, export_timeout_seconds=3
        )


def test_enabled_workers_have_unique_resource_instance_ids():
    # Arrange
    first = ObservabilityMetrics("python-demo-api", otel_metrics_enabled=True)
    second = ObservabilityMetrics("python-demo-api", otel_metrics_enabled=True)
    first.exporter.export = lambda data, timeout_millis=10_000: MetricExportResult.SUCCESS
    second.exporter.export = lambda data, timeout_millis=10_000: MetricExportResult.SUCCESS

    try:
        # Act
        first_id = _instance_id(first)
        second_id = _instance_id(second)

        # Assert
        assert isinstance(first.export_reader, PeriodicExportingMetricReader)
        assert first_id != second_id
        assert first_id.startswith(f"{os.getpid()}-")
    finally:
        first.shutdown()
        second.shutdown()


def test_recording_a_request_does_not_export_synchronously(monkeypatch):
    # Arrange
    metrics = ObservabilityMetrics(
        "python-demo-api", otel_metrics_enabled=True, export_interval_millis=60_000
    )
    exports = []
    monkeypatch.setattr(
        metrics.exporter,
        "export",
        lambda data, timeout_millis=10_000: exports.append(data) or MetricExportResult.SUCCESS,
    )

    try:
        # Act
        metrics.record_request("GET", "/up", 200, 0.01)

        # Assert
        assert exports == []
    finally:
        metrics.shutdown()
