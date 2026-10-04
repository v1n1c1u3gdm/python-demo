import threading
import time
from concurrent.futures import ThreadPoolExecutor

from services import readiness
from services.keycloak_client import KeycloakClient


def _wait_for_probe_queue_idle(timeout=1.0):
    condition = readiness._probe_queue.all_tasks_done
    with condition:
        return condition.wait_for(
            lambda: readiness._probe_queue.unfinished_tasks == 0,
            timeout=timeout,
        )


def test_fresh_discovery_bypasses_cached_token_validation_metadata(monkeypatch):
    # Arrange
    class Response:
        ok = True

        @staticmethod
        def json():
            return {"issuer": "http://identity/realms/demo"}

        @staticmethod
        def close():
            return None

    class Session:
        calls = []
        was_closed = False

        def get(self, url, timeout):
            self.calls.append((url, timeout))
            return Response()

        def close(self):
            self.was_closed = True

    session = Session()
    monkeypatch.setattr("services.keycloak_client.requests.Session", lambda: session)
    client = KeycloakClient("http://identity", "demo", "api")
    client._well_known = {"issuer": "cached-invalid-value"}
    client._well_known_expires_at = time.time() + 100

    # Act
    result = client.fetch_fresh_discovery(0.5)

    # Assert
    assert result == {"issuer": "http://identity/realms/demo"}
    assert session.calls == [
        ("http://identity/realms/demo/.well-known/openid-configuration", 0.5)
    ]
    assert session.was_closed is True


def test_readiness_checks_both_dependencies_in_order_with_limited_timeouts(app, monkeypatch):
    # Arrange
    calls = []

    def database(url, connect_timeout, read_timeout):
        calls.append(("mysql", url, connect_timeout, read_timeout))
        return True

    def keycloak(config, connect_timeout, read_timeout):
        calls.append(("keycloak", config["KEYCLOAK_REALM"], connect_timeout, read_timeout))
        return True

    monkeypatch.setattr(readiness, "_probe_database", database)
    monkeypatch.setattr(readiness, "_probe_keycloak", keycloak)

    # Act
    result = readiness.check_readiness(app)

    # Assert
    assert result is True
    assert [call[0] for call in calls] == ["mysql", "keycloak"]
    assert 0 < calls[0][2] <= 1.0
    assert 0 < calls[0][3] <= 1.0
    assert 0 < calls[1][2] <= 0.5
    assert 0 < calls[1][3] <= 1.0


def test_database_probe_executes_select_one_with_a_fresh_connection():
    # Arrange
    database_url = "sqlite+pysqlite:///:memory:"

    # Act
    result = readiness._probe_database(database_url, 1.0, 1.0)

    # Assert
    assert result is True


def test_readiness_stops_when_mysql_probe_fails(app, monkeypatch):
    # Arrange
    calls = []

    def failed_database(*_args):
        calls.append("mysql")
        return False

    def unexpected_keycloak(*_args):
        calls.append("keycloak")
        return True

    monkeypatch.setattr(readiness, "_probe_database", failed_database)
    monkeypatch.setattr(readiness, "_probe_keycloak", unexpected_keycloak)

    # Act
    result = readiness.check_readiness(app)

    # Assert
    assert result is False
    assert calls == ["mysql"]


def test_readiness_fails_when_fresh_keycloak_discovery_fails(app, monkeypatch):
    # Arrange
    monkeypatch.setattr(readiness, "_probe_database", lambda *_args: True)
    monkeypatch.setattr(readiness, "_probe_keycloak", lambda *_args: False)

    # Act
    result = readiness.check_readiness(app)

    # Assert
    assert result is False


def test_keycloak_probe_requires_metadata_for_the_configured_issuer(app, monkeypatch):
    # Arrange
    config = {
        "KEYCLOAK_BASE_URL": app.config["KEYCLOAK_BASE_URL"],
        "KEYCLOAK_REALM": app.config["KEYCLOAK_REALM"],
        "KEYCLOAK_CLIENT_ID": app.config["KEYCLOAK_CLIENT_ID"],
        "KEYCLOAK_CLIENT_SECRET": app.config["KEYCLOAK_CLIENT_SECRET"],
        "KEYCLOAK_ISSUER": app.config["KEYCLOAK_ISSUER"],
        "KEYCLOAK_AUDIENCE": app.config["KEYCLOAK_AUDIENCE"],
    }
    monkeypatch.setattr(
        KeycloakClient,
        "fetch_fresh_discovery",
        lambda _self, _timeout, *, session: {"issuer": config["KEYCLOAK_ISSUER"]},
    )

    # Act
    result = readiness._probe_keycloak(config, 0.5, 1.0)

    # Assert
    assert result is True


def test_readiness_service_returns_before_stalled_probe_deadline(app, monkeypatch):
    # Arrange
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def stalled(*_args):
        started.set()
        try:
            release.wait()
            return True
        finally:
            completed.set()

    monkeypatch.setattr(readiness, "READINESS_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(readiness, "_probe_database", stalled)
    monkeypatch.setattr(readiness, "_probe_keycloak", lambda *_args: True)

    try:
        # Act
        began = time.monotonic()
        result = readiness.check_readiness(app)
        elapsed = time.monotonic() - began

        # Assert
        assert result is False
        assert elapsed < 0.1
        assert started.is_set()
    finally:
        release.set()
        assert completed.wait(timeout=1.0)
        assert _wait_for_probe_queue_idle(timeout=1.0)

    # Act: the completed probe releases its bounded worker slot.
    monkeypatch.setattr(readiness, "_probe_database", lambda *_args: True)
    assert readiness.check_readiness(app) is True


def test_repeated_stalled_probes_never_exceed_two_in_flight_jobs(app, monkeypatch):
    # Arrange
    started = []
    release = threading.Event()
    lock = threading.Lock()
    both_started = threading.Event()
    both_completed = threading.Event()
    completed_count = 0

    def stalled(*_args):
        nonlocal completed_count
        with lock:
            started.append(time.monotonic())
            if len(started) == 2:
                both_started.set()
        try:
            release.wait()
            return True
        finally:
            with lock:
                completed_count += 1
                if completed_count == 2:
                    both_completed.set()

    monkeypatch.setattr(readiness, "READINESS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(readiness, "_probe_database", stalled)
    monkeypatch.setattr(readiness, "_probe_keycloak", lambda *_args: True)

    try:
        # Act
        with ThreadPoolExecutor(max_workers=5) as callers:
            results = list(callers.map(lambda _index: readiness.check_readiness(app), range(5)))

        # Assert
        assert results == [False] * 5
        assert both_started.is_set()
        assert len(started) == 2
    finally:
        release.set()
        assert both_completed.wait(timeout=1.0)
        assert _wait_for_probe_queue_idle(timeout=1.0)

    # Act: completed probes make both bounded slots available again.
    monkeypatch.setattr(readiness, "_probe_database", lambda *_args: True)
    assert readiness.check_readiness(app) is True
