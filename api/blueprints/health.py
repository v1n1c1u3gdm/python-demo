from flask import Blueprint, current_app

from services.readiness import check_readiness

from .utils import to_json

bp = Blueprint("health", __name__)


@bp.get("/liveness")
def liveness():
    return to_json({"status": "ok"})


@bp.get("/up")
def readiness():
    return to_json({"status": "ok"})


@bp.get("/ready")
def ready():
    if not check_readiness(current_app._get_current_object()):
        return to_json({"status": "unavailable"}, 503)
    return to_json({"status": "ok"})
