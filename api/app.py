import atexit
import os
import time
from pathlib import Path

from flask import Flask, current_app, g, jsonify, redirect, request, send_file
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
from marshmallow import ValidationError
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import models  # noqa: F401  # Ensure models are registered before migrations
from blueprints import register_blueprints
from bootstrap import register_bootstrap_command
from config import get_config
from extensions import db, migrate
from logging_config import configure_logging
from observability import ObservabilityMetrics
from services.authorization import AuthorizationError
from services.keycloak_client import init_keycloak_client


def create_app() -> Flask:
    config_class = get_config()
    settings = config_class.from_environment(os.environ)
    configure_logging(settings.get("LOG_DIR", config_class.LOG_DIR), settings.get("LOG_LEVEL", config_class.LOG_LEVEL))

    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config.update(settings)
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
        service_name=app.config["SERVICE_NAME"],
        namespace=app.config.get("OPENAPI_SERVICE_NAMESPACE", "python-demo"),
        otel_metrics_enabled=app.config["OTEL_METRICS_ENABLED"],
        otel_exporter_endpoint=app.config["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"],
        export_interval_millis=app.config["OTEL_EXPORT_INTERVAL_MS"],
        export_timeout_seconds=app.config["OTEL_EXPORT_TIMEOUT_SECONDS"],
    )
    atexit.register(observability.shutdown)
    app.extensions["observability_metrics"] = observability

    register_swagger(app)
    register_error_handlers(app)
    register_request_hooks(app, observability)
    register_blueprints(app)
    init_keycloak_client(app)
    register_bootstrap_command(app)

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
        g.metrics_recorded = False

    @app.after_request
    def record_metrics(response):
        if getattr(g, "metrics_recorded", False):
            return response
        started = getattr(g, "request_started_at", None)
        duration = time.perf_counter() - started if started is not None else 0.0
        metrics.record_request(
            method=request.method,
            route=request.url_rule.rule if request.url_rule is not None else "404",
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


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        messages = _flatten_errors(error.messages)
        return jsonify({"errors": messages}), 422

    @app.errorhandler(AuthorizationError)
    def handle_authorization_error(error: AuthorizationError):
        return jsonify({"errors": [str(error)]}), error.status_code

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        return jsonify({"errors": [error.description]}), error.code

    @app.errorhandler(Exception)
    def handle_exception(error):
        current_app.logger.error("Unhandled exception type=%s", type(error).__name__)
        return jsonify({"errors": ["Internal server error."]}), 500


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


def run_development_server(flask_app: Flask) -> None:
    flask_app.run(host="127.0.0.1", port=5000)


app = create_app()


if __name__ == "__main__":
    run_development_server(app)
