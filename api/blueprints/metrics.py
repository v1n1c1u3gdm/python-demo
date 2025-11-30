from flask import Blueprint, current_app

from observability import MetricsFormatter, ObservabilityMetrics


bp = Blueprint("metrics", __name__)


@bp.get("/metrics")
def metrics_endpoint():
    metrics_service: ObservabilityMetrics = current_app.extensions["observability_metrics"]
    data = metrics_service.scrape()
    formatter = MetricsFormatter(data)
    payload = formatter.to_text()
    return current_app.response_class(payload, mimetype="text/plain; version=0.0.4")

