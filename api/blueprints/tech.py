from flask import Blueprint, make_response

from services.tech_report import TechReport


bp = Blueprint("tech", __name__)


@bp.get("/tech")
def tech_report():
    report = TechReport()
    html = report.render()
    response = make_response(html, 200)
    response.headers["Content-Type"] = "text/html"
    return response

