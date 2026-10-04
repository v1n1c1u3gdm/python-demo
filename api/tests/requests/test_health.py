from blueprints import health as health_blueprint


def test_liveness_endpoint(client):
    response = client.get("/liveness")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"


def test_up_endpoint(client):
    response = client.get("/up")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"


def test_liveness_succeeds_when_database_and_keycloak_are_unavailable(client, monkeypatch):
    # Arrange
    def unavailable(*_args, **_kwargs):
        raise AssertionError("liveness must not probe dependencies")

    monkeypatch.setattr(health_blueprint, "check_readiness", unavailable, raising=False)

    # Act
    response = client.get("/liveness")

    # Assert
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_ready_checks_mysql_and_fresh_keycloak_discovery(client, app, monkeypatch):
    # Arrange
    checks = []

    def available(application):
        checks.append(application)
        return True

    monkeypatch.setattr(health_blueprint, "check_readiness", available)

    # Act
    response = client.get("/ready")

    # Assert
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert checks == [app]


def test_ready_returns_503_when_mysql_fails(client, monkeypatch):
    # Arrange
    monkeypatch.setattr(health_blueprint, "check_readiness", lambda _app: False)

    # Act
    response = client.get("/ready")

    # Assert
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}


def test_ready_returns_503_when_keycloak_discovery_fails(client, monkeypatch):
    # Arrange
    monkeypatch.setattr(health_blueprint, "check_readiness", lambda _app: False)

    # Act
    response = client.get("/ready")

    # Assert
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}


def test_ready_returns_503_within_global_deadline(client, monkeypatch):
    # Arrange
    import time

    monkeypatch.setattr(health_blueprint, "check_readiness", lambda _app: False)
    started = time.monotonic()

    # Act
    response = client.get("/ready")
    elapsed = time.monotonic() - started

    # Assert
    assert response.status_code == 503
    assert elapsed < 3


def test_ready_recovers_after_dependencies_recover(client, monkeypatch):
    # Arrange
    outcomes = iter((False, True))
    monkeypatch.setattr(health_blueprint, "check_readiness", lambda _app: next(outcomes))

    # Act
    unavailable = client.get("/ready")
    available = client.get("/ready")

    # Assert
    assert unavailable.status_code == 503
    assert available.status_code == 200
