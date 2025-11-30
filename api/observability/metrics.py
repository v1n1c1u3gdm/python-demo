from __future__ import annotations

import os
from typing import Iterable

from opentelemetry import metrics
from opentelemetry.metrics._internal.observation import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource


class ObservabilityMetrics:
    """Wraps OpenTelemetry metrics collection for HTTP requests and service liveness."""

    def __init__(self, service_name: str, namespace: str = "python-demo"):
        self.service_name = service_name
        self.namespace = namespace

        os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
        os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

        self.reader = InMemoryMetricReader()
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": namespace,
            }
        )

        self.provider = MeterProvider(resource=resource, metric_readers=[self.reader])
        metrics.set_meter_provider(self.provider)

        meter = self.provider.get_meter(service_name, version="0.1.0")
        self.request_counter = meter.create_counter(
            "http_server_requests_total",
            description="Total HTTP requests received by the API",
        )
        self.duration_sum_counter = meter.create_counter(
            "http_server_request_duration_seconds_sum",
            description="Total time spent handling HTTP requests",
            unit="s",
        )
        self.duration_count_counter = meter.create_counter(
            "http_server_request_duration_seconds_count",
            description="Number of HTTP requests observed for duration",
        )
        meter.create_observable_gauge(
            "service_liveness",
            callbacks=[self._observe_liveness],
            description="Indicates if the API process is alive",
        )

    def _observe_liveness(self, options=None):
        yield Observation(
            1,
            attributes={"service": self.service_name, "state": "alive"},
        )

    def record_request(self, method: str, path: str, status: int, duration_seconds: float):
        attributes = {
            "service": self.service_name,
            "http.method": method,
            "http.route": self._normalize_path(path),
            "http.status_code": status,
        }
        self.request_counter.add(1, attributes=attributes)
        self.duration_sum_counter.add(duration_seconds, attributes=attributes)
        self.duration_count_counter.add(1, attributes=attributes)

    def scrape(self):
        return self.reader.get_metrics_data()

    @staticmethod
    def _normalize_path(path: str) -> str:
        # Replace numeric path segments with :id to keep cardinality low
        parts = []
        for part in filter(None, path.split("/")):
            parts.append(":id" if part.isdigit() else part)
        return "/" + "/".join(parts)


class MetricsFormatter:
    """Format OpenTelemetry metrics snapshots into Prometheus text exposition format."""

    TYPE_MAPPING = {
        "Counter": "counter",
        "ObservableCounter": "counter",
        "UpDownCounter": "gauge",
        "ObservableUpDownCounter": "gauge",
        "Histogram": "histogram",
        "ObservableGauge": "gauge",
        "Gauge": "gauge",
        "Sum": "counter",
    }

    def __init__(self, metrics_data):
        self.metrics_data = metrics_data

    def to_text(self) -> str:
        if not self.metrics_data:
            return ""

        lines = []
        for resource_metrics in self.metrics_data.resource_metrics:
            resource_attrs = dict(resource_metrics.resource.attributes)
            for scope_metric in resource_metrics.scope_metrics:
                for metric in scope_metric.metrics:
                    metric_name = metric.name
                    data_class = metric.data.__class__.__name__
                    metric_type = self.TYPE_MAPPING.get(data_class, "gauge")
                    description = metric.description or "Metric emitted via OpenTelemetry"
                    lines.append(f"# HELP {metric_name} {description}")
                    lines.append(f"# TYPE {metric_name} {metric_type}")
                    for data_point in metric.data.data_points:
                        value = getattr(data_point, "value", getattr(data_point, "sum", None))
                        if value is None:
                            continue
                        labels = self._format_labels(resource_attrs, data_point.attributes)
                        lines.append(f"{metric_name}{labels} {self._format_value(value)}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_labels(resource_attrs: dict, point_attrs: dict) -> str:
        labels = {**resource_attrs, **point_attrs}
        if not labels:
            return ""
        encoded = ",".join(
            f'{MetricsFormatter._sanitize_label(k)}="{MetricsFormatter._escape(v)}"'
            for k, v in sorted(labels.items())
            if v is not None
        )
        return f"{{{encoded}}}" if encoded else ""

    @staticmethod
    def _sanitize_label(key) -> str:
        return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(key))

    @staticmethod
    def _escape(value) -> str:
        return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    @staticmethod
    def _format_value(value) -> str:
        if isinstance(value, float):
            return f"{value:.6f}"
        return str(value)

