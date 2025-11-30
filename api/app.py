import logging
import time
from pathlib import Path

from flask import Flask, current_app, g, jsonify, redirect, request, send_file
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
from marshmallow import ValidationError
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_config
from extensions import db, migrate
from logging_config import configure_logging
from observability import MetricsFormatter, ObservabilityMetrics
from blueprints import register_blueprints
from seeds import bootstrap_seed_data
import models  # noqa: F401  # Ensure models are registered before migrations


def create_app() -> Flask:
    config_class = get_config()
    configure_logging(config_class.LOG_DIR, config_class.LOG_LEVEL)

    app = Flask(__name__)
    app.config.from_object(config_class)
    app.wsgi_app = ProxyFix(app.wsgi_app)  # type: ignore

    CORS(
        app,
        resources={r"/api-docs*": {"origins": "*"}, r"/*": {"origins": "*"}},
        supports_credentials=False,
    )

    db.init_app(app)
    migrate.init_app(app, db)
    FlaskInstrumentor().instrument_app(app)

    observability = ObservabilityMetrics(
        service_name=config_class.SERVICE_NAME,
        namespace=getattr(config_class, "OPENAPI_SERVICE_NAMESPACE", "python-demo"),
    )
    app.extensions["observability_metrics"] = observability

    register_swagger(app)
    register_error_handlers(app, observability)
    register_request_hooks(app, observability)
    register_blueprints(app)

    if not app.config.get("TESTING"):
        with app.app_context():
            run_database_bootstrap()

    return app


def register_swagger(app: Flask) -> None:
    spec_path: Path = app.config["SWAGGER_SPEC_PATH"]
    swagger_ui_blueprint = get_swaggerui_blueprint(
        app.config["SWAGGER_UI_ROUTE"],
        "/openapi.yaml",
        config={"app_name": app.config.get("API_TITLE", "Python Demo API")},
    )
    app.register_blueprint(swagger_ui_blueprint, url_prefix=app.config["SWAGGER_UI_ROUTE"])

    @app.route("/")
    def root():
        return redirect(app.config["SWAGGER_UI_ROUTE"])

    @app.route("/openapi.yaml")
    def openapi_definition():
        return send_file(spec_path, mimetype="application/yaml")


def register_request_hooks(app: Flask, metrics: ObservabilityMetrics) -> None:
    @app.before_request
    def start_timer():
        g.request_started_at = time.perf_counter()

    @app.after_request
    def record_metrics(response):
        if getattr(g, "metrics_recorded", False):
            return response
        started = getattr(g, "request_started_at", None)
        duration = time.perf_counter() - started if started is not None else 0.0
        metrics.record_request(
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_seconds=duration,
        )
        g.metrics_recorded = True
        current_app.logger.info(
            "HTTP %s %s -> %s (%.4fs)",
            request.method,
            request.path,
            response.status_code,
            duration,
        )
        return response


def register_error_handlers(app: Flask, metrics: ObservabilityMetrics) -> None:
    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        messages = _flatten_errors(error.messages)
        return jsonify({"errors": messages}), 422

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        return jsonify({"errors": [error.description]}), error.code

    @app.errorhandler(Exception)
    def handle_exception(error):
        current_app.logger.exception("Unhandled exception: %s", error)
        started = getattr(g, "request_started_at", None)
        duration = time.perf_counter() - started if started is not None else 0.0
        metrics.record_request(
            method=request.method,
            path=request.path,
            status=500,
            duration_seconds=duration,
        )
        g.metrics_recorded = True
        return jsonify({"errors": [str(error)]}), 500


def _flatten_errors(messages):
    if isinstance(messages, dict):
        errors = []
        for value in messages.values():
            if isinstance(value, (list, tuple)):
                errors.extend(value)
            elif isinstance(value, dict):
                errors.extend(_flatten_errors(value))
            else:
                errors.append(str(value))
        return errors or ["Invalid payload."]
    if isinstance(messages, (list, tuple)):
        return [str(item) for item in messages]
    return [str(messages)]


def run_database_bootstrap():
    from flask_migrate import upgrade

    upgrade()
    bootstrap_seed_data()


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

