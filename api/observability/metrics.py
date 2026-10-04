from __future__ import annotations

import os
import uuid

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics._internal.observation import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)


class ObservabilityMetrics:
    """Collects independent OpenTelemetry and Prometheus HTTP metrics."""

    def __init__(
        self,
        service_name: str,
        namespace: str = "python-demo",
        *,
        otel_metrics_enabled: bool = False,
        otel_exporter_endpoint: str = "http://otel-collector:4318/v1/metrics",
        export_interval_millis: int = 10000,
        export_timeout_seconds: int = 2,
    ):
        self.service_name = service_name
        self.namespace = namespace
        if not 0 < export_timeout_seconds <= 2:
            raise ValueError("OpenTelemetry export timeout must be between one and two seconds.")

        os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
        os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

        self.exporter = None
        self.export_reader = None
        self.reader = InMemoryMetricReader()
        metric_readers = [self.reader]
        if otel_metrics_enabled:
            self.exporter = OTLPMetricExporter(
                endpoint=otel_exporter_endpoint,
                timeout=export_timeout_seconds,
            )
            self.export_reader = PeriodicExportingMetricReader(
                self.exporter,
                export_interval_millis=export_interval_millis,
                export_timeout_millis=export_timeout_seconds * 1000,
            )
            metric_readers.append(self.export_reader)
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": namespace,
                "service.instance.id": f"{os.getpid()}-{uuid.uuid4().hex}",
            }
        )
        self._shutdown = False
        self.provider = MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
            shutdown_on_exit=False,
        )

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

        self.prometheus_registry = CollectorRegistry()
        self.prometheus_request_counter = Counter(
            "http_server_requests_total",
            "Total HTTP requests received by the API",
            ("method", "route", "status"),
            registry=self.prometheus_registry,
        )
        self.prometheus_request_duration = Histogram(
            "http_server_request_duration_seconds",
            "Time spent handling HTTP requests",
            ("method", "route", "status"),
            registry=self.prometheus_registry,
        )
        self.prometheus_liveness = Gauge(
            "service_liveness",
            "Indicates if the API process is alive",
            ("service", "state"),
            multiprocess_mode="livesum",
            registry=self.prometheus_registry,
        )
        self.prometheus_liveness.labels(service=service_name, state="alive").set(1)

    def _observe_liveness(self, options=None):
        yield Observation(
            1,
            attributes={"service": self.service_name, "state": "alive"},
        )

    def record_request(self, method: str, route: str, status: int, duration_seconds: float) -> None:
        attributes = {
            "service": self.service_name,
            "http.method": method,
            "http.route": route,
            "http.status_code": status,
        }
        self.request_counter.add(1, attributes=attributes)
        self.duration_sum_counter.add(duration_seconds, attributes=attributes)
        self.duration_count_counter.add(1, attributes=attributes)

        labels = {"method": method, "route": route, "status": str(status)}
        self.prometheus_request_counter.labels(**labels).inc()
        self.prometheus_request_duration.labels(**labels).observe(duration_seconds)

    def scrape(self):
        """Return the OpenTelemetry snapshot, kept separate from Prometheus output."""
        return self.reader.get_metrics_data()

    def shutdown(self) -> None:
        """Flush and close the provider and its background metric reader."""
        if not self._shutdown:
            self._shutdown = True
            self.provider.shutdown()

    def scrape_prometheus(self) -> str:
        """Render one app's metrics or aggregate all workers when multiprocess mode is active."""
        multiprocess_directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if multiprocess_directory:
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry, path=multiprocess_directory)
        else:
            registry = self.prometheus_registry
        return generate_latest(registry).decode("utf-8")


class MetricsFormatter:
    """Format OpenTelemetry metrics snapshots as Prometheus text."""

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
