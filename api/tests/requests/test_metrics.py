def test_metrics_endpoint_returns_openmetrics(client):
    client.get("/liveness")

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "http_server_requests_total" in body
    assert "service_liveness" in body
    assert "text/plain" in response.content_type

