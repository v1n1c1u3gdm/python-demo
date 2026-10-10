"""Small bounded HTTP client used by local bootstrap services."""

import json as json_library
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

MAX_RESPONSE_BYTES = 1024 * 1024
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


class _DeadlineExpired(Exception):
    """Internal signal used to interrupt blocking socket reads at the request deadline."""


@contextmanager
def _wallclock_deadline(deadline: float):
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        raise LocalHTTPError("Local service deadline enforcement requires the main thread on POSIX.")
    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        raise LocalHTTPError("Local service deadline enforcement cannot replace an active alarm.")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: object) -> None:
        raise _DeadlineExpired

    signal.signal(signal.SIGALRM, expire)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        signal.signal(signal.SIGALRM, previous_handler)
        raise LocalHTTPError("Local service request exceeded the retry deadline.")
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    except _DeadlineExpired:
        raise LocalHTTPError("Local service request exceeded the retry deadline.") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class LocalHTTPError(RuntimeError):
    """Safe failure without credentials or response bodies."""


class LocalHTTP:
    def __init__(self, base_url: str, ca_file: Path | None = None, timeout: float = 5) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.verify: bool | str = str(ca_file) if ca_file else True
        self.timeout = max(timeout, 0.1)

    def request(self, method: str, path: str, *, json: dict | None = None,
                headers: dict[str, str] | None = None, data: dict[str, str] | None = None,
                expected_statuses: tuple[int, ...] = (200,),
                retryable_statuses: tuple[int, ...] = ()) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        deadline = time.monotonic() + self.timeout
        last_error: Exception | None = None
        last_transient_status: int | None = None
        while time.monotonic() < deadline:
            response = None
            try:
                remaining = max(0.001, deadline - time.monotonic())
                with _wallclock_deadline(deadline):
                    response = requests.request(
                        method, url, json=json, headers=headers, data=data,
                        timeout=(min(1.0, remaining), min(1.0, remaining)),
                        verify=self.verify, allow_redirects=False, stream=True,
                    )
                if response.status_code not in expected_statuses:
                    if response.status_code in RETRYABLE_STATUSES or response.status_code in retryable_statuses:
                        last_transient_status = response.status_code
                        response.close()
                        response = None
                        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                        continue
                    raise LocalHTTPError(f"Local service returned HTTP {response.status_code}.")
                body = bytearray()
                with _wallclock_deadline(deadline):
                    for chunk in response.iter_content(chunk_size=1):
                        if time.monotonic() >= deadline:
                            raise LocalHTTPError("Local service response exceeded the retry deadline.")
                        if not chunk:
                            continue
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise LocalHTTPError("Local service response exceeded the allowed size.")
                        body.extend(chunk)
                if time.monotonic() >= deadline:
                    raise LocalHTTPError("Local service response exceeded the retry deadline.")
                if not body:
                    return None
                encoding = response.encoding or "utf-8"
                text = body.decode(encoding, errors="replace")
                try:
                    return json_library.loads(text)
                except json_library.JSONDecodeError:
                    return text
            except LocalHTTPError:
                raise
            except (requests.RequestException, OSError) as error:
                last_error = error
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            finally:
                if response is not None:
                    response.close()
        if isinstance(last_error, requests.exceptions.SSLError):
            kind = "TLS validation failed"
        elif last_transient_status is not None:
            kind = f"service remained unavailable after HTTP {last_transient_status}"
        else:
            kind = "service unavailable"
        raise LocalHTTPError(f"Local service {kind} before the retry deadline.") from None
