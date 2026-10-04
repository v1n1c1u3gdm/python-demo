from flask import Blueprint, current_app
from prometheus_client import CONTENT_TYPE_LATEST

from observability import ObservabilityMetrics
from services.authorization import require_admin

bp = Blueprint("metrics", __name__)


@bp.get("/metrics")
def metrics_endpoint():
    require_admin()
    metrics_service: ObservabilityMetrics = current_app.extensions["observability_metrics"]
    payload = metrics_service.scrape_prometheus()
    return current_app.response_class(payload, content_type=CONTENT_TYPE_LATEST)
