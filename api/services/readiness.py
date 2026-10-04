from __future__ import annotations

import atexit
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from services.keycloak_client import KeycloakClient

READINESS_TIMEOUT_SECONDS = 3.0
MAX_PROBE_JOBS = 2

_Probe = tuple[Future[bool], Callable[..., bool], tuple[Any, ...]]
_probe_queue: queue.Queue[_Probe] = queue.Queue(maxsize=MAX_PROBE_JOBS)
_probe_slots = threading.BoundedSemaphore(MAX_PROBE_JOBS)
_shutdown = threading.Event()


def _probe_worker() -> None:
    while not _shutdown.is_set():
        try:
            future, probe, args = _probe_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(probe(*args))
            except BaseException as error:  # keep a failed dependency from killing a worker
                future.set_exception(error)
        finally:
            _probe_slots.release()
            _probe_queue.task_done()


for _worker_index in range(MAX_PROBE_JOBS):
    threading.Thread(
        target=_probe_worker,
        name=f"readiness-probe-{_worker_index + 1}",
        daemon=True,
    ).start()


@atexit.register
def _stop_probe_workers() -> None:
    _shutdown.set()


def _run_probe(probe: Callable[..., bool], timeout: float, *args: Any) -> bool:
    if timeout <= 0 or not _probe_slots.acquire(blocking=False):
        return False
    future: Future[bool] = Future()
    try:
        _probe_queue.put_nowait((future, probe, args))
    except queue.Full:
        _probe_slots.release()
        return False
    try:
        return future.result(timeout=timeout)
    except BaseException:
        return False


def _probe_database(database_url: str, connect_timeout: float, read_timeout: float) -> bool:
    url = make_url(database_url)
    if url.get_backend_name() == "mysql":
        connect_args = {
            "connect_timeout": max(0.001, connect_timeout),
            "read_timeout": max(0.001, read_timeout),
        }
    else:
        connect_args = {"timeout": max(0.001, connect_timeout)}

    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def _probe_keycloak(config: dict[str, Any], connect_timeout: float, read_timeout: float) -> bool:
    session = requests.Session()
    client = KeycloakClient.from_config(config, session=session)
    timeout = (connect_timeout, read_timeout)
    try:
        metadata = client.fetch_fresh_discovery(timeout, session=session)
    except Exception:
        return False
    finally:
        session.close()
    return metadata.get("issuer") == config["KEYCLOAK_ISSUER"]


def check_readiness(app=None) -> bool:
    """Check MySQL and fresh Keycloak realm discovery within one fixed deadline."""
    if app is None:
        from flask import current_app

        app = current_app._get_current_object()

    config = {
        key: app.config[key]
        for key in (
            "SQLALCHEMY_DATABASE_URI",
            "KEYCLOAK_BASE_URL",
            "KEYCLOAK_REALM",
            "KEYCLOAK_CLIENT_ID",
            "KEYCLOAK_CLIENT_SECRET",
            "KEYCLOAK_ISSUER",
            "KEYCLOAK_AUDIENCE",
        )
    }
    deadline = time.monotonic() + min(
        READINESS_TIMEOUT_SECONDS,
        float(app.config.get("READINESS_TIMEOUT_SECONDS", READINESS_TIMEOUT_SECONDS)),
    )

    remaining = deadline - time.monotonic()
    mysql_connect = min(
        float(app.config.get("READINESS_MYSQL_CONNECT_TIMEOUT_SECONDS", 1.0)), remaining
    )
    mysql_read = min(
        float(app.config.get("READINESS_MYSQL_READ_TIMEOUT_SECONDS", 1.0)), remaining
    )
    if not _run_probe(
        _probe_database,
        remaining,
        config["SQLALCHEMY_DATABASE_URI"],
        mysql_connect,
        mysql_read,
    ):
        return False

    remaining = deadline - time.monotonic()
    keycloak_connect = min(
        float(app.config.get("READINESS_KEYCLOAK_CONNECT_TIMEOUT_SECONDS", 0.5)), remaining
    )
    keycloak_read = min(
        float(app.config.get("READINESS_KEYCLOAK_READ_TIMEOUT_SECONDS", 1.0)), remaining
    )
    if remaining <= 0:
        return False
    return _run_probe(
        _probe_keycloak,
        remaining,
        config,
        keycloak_connect,
        keycloak_read,
    )
