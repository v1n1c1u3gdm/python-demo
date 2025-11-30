from datetime import datetime, timezone

from flask import Blueprint, current_app

from .utils import to_json


bp = Blueprint("health", __name__)


@bp.get("/liveness")
def liveness():
    return to_json(
        {
            "status": "ok",
            "service": current_app.config.get("SERVICE_NAME", "python-demo-api"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.get("/up")
def readiness():
    return to_json(
        {
            "status": "ok",
            "service": current_app.config.get("SERVICE_NAME", "python-demo-api"),
        }
    )

