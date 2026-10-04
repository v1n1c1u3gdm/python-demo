from flask import abort
from prometheus_client.parser import text_string_to_metric_families

from app import create_app
from tests.factories import ArticleFactory


def _counter_samples(body):
    return {
        (sample.labels["method"], sample.labels["route"], sample.labels["status"]): sample.value
        for family in text_string_to_metric_families(body)
        for sample in family.samples
        if sample.name == "http_server_requests_total"
    }


def test_metrics_route_requires_admin(client, role_tokens):
    # Arrange
    reader_headers = {"Authorization": f"Bearer {role_tokens['reader']}"}

    # Act
    anonymous = client.get("/metrics")
    reader = client.get("/metrics", headers=reader_headers)

    # Assert
    assert anonymous.status_code == 401
    assert reader.status_code == 403


def test_metrics_uses_route_templates_and_stable_not_found_label(client, admin_headers):
    # Arrange
    article = ArticleFactory()

    # Act
    client.get(f"/articles/{article.id}")
    client.get("/private-user-path")
    response = client.get("/metrics", headers=admin_headers)

    # Assert
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'route="/articles/<int:article_id>"' in body
    assert 'route="404"' in body
    assert f'route="/articles/{article.id}"' not in body
    assert "private-user-path" not in body
    assert "test-admin-token" not in body


def test_error_request_is_counted_once(admin_headers):
    # Arrange
    application = create_app()
    application.add_url_rule(
        "/test-unhandled-metrics-error",
        endpoint="test_unhandled_metrics_error",
        view_func=lambda: (_ for _ in ()).throw(RuntimeError("private failure")),
    )
    application.add_url_rule(
        "/test-handled-metrics-error",
        endpoint="test_handled_metrics_error",
        view_func=lambda: abort(400),
    )
    isolated_client = application.test_client()
    verified_admin = {
        "python_demo.keycloak_claims": {"realm_access": {"roles": ["admin"]}},
    }
    isolated_client.get("/test-unhandled-metrics-error")
    isolated_client.get("/test-handled-metrics-error")

    # Act
    response = isolated_client.get("/metrics", headers=admin_headers, environ_overrides=verified_admin)

    # Assert
    assert response.status_code == 200
    samples = _counter_samples(response.get_data(as_text=True))
    assert samples[("GET", "/test-unhandled-metrics-error", "500")] == 1
    assert samples[("GET", "/test-handled-metrics-error", "400")] == 1


def test_request_recording_flag_resets_between_requests_in_a_held_client_context(client, admin_headers):
    # Arrange
    before_response = client.get("/metrics", headers=admin_headers)
    before = _counter_samples(before_response.get_data(as_text=True))

    # Act
    with client:
        liveness = client.get("/liveness")
        readiness = client.get("/up")
    after_response = client.get("/metrics", headers=admin_headers)
    after = _counter_samples(after_response.get_data(as_text=True))

    # Assert
    assert liveness.status_code == 200
    assert readiness.status_code == 200
    assert after[("GET", "/liveness", "200")] - before.get(("GET", "/liveness", "200"), 0) == 1
    assert after[("GET", "/up", "200")] - before.get(("GET", "/up", "200"), 0) == 1


def test_metrics_registry_scrape_includes_request_counter_and_duration(client, admin_headers):
    # Arrange
    client.get("/liveness")

    # Act
    response = client.get("/metrics", headers=admin_headers)

    # Assert
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "# TYPE http_server_requests_total counter" in body
    assert "http_server_request_duration_seconds_bucket" in body
    assert "http_server_request_duration_seconds_count" in body
    assert "service_liveness" in body
    assert "text/plain" in response.content_type
