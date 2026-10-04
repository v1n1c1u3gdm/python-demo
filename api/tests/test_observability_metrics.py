import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from prometheus_client.parser import text_string_to_metric_families

from observability import ObservabilityMetrics


def _samples(body, name):
    return [
        sample
        for family in text_string_to_metric_families(body)
        for sample in family.samples
        if sample.name == name
    ]


def test_each_metrics_service_scrapes_only_its_own_prometheus_registry():
    # Arrange
    first = ObservabilityMetrics("first-api")
    second = ObservabilityMetrics("second-api")

    # Act
    first.record_request("GET", "/first", 200, 0.125)
    second.record_request("POST", "/second", 503, 0.25)
    first_scrape = first.scrape_prometheus()
    second_scrape = second.scrape_prometheus()
    otel_data = first.scrape()

    # Assert
    first_counter = _samples(first_scrape, "http_server_requests_total")
    second_counter = _samples(second_scrape, "http_server_requests_total")
    assert [(sample.labels["route"], sample.value) for sample in first_counter] == [("/first", 1)]
    assert [(sample.labels["route"], sample.value) for sample in second_counter] == [("/second", 1)]
    assert "http_server_request_duration_seconds_sum" in first_scrape
    assert "http_server_request_duration_seconds_sum" in second_scrape
    otel_names = {
        metric.name
        for resource_metrics in otel_data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    assert "http_server_requests_total" in otel_names
    assert "http_server_request_duration_seconds_sum" in otel_names
    assert "http_server_request_duration_seconds_count" in otel_names
    assert 'service.name="first-api"' not in first_scrape


def test_dead_worker_cleanup_preserves_prometheus_counter_and_histogram(tmp_path):
    # Arrange
    source_root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import multiprocessing
import os

from prometheus_client import multiprocess
from observability import ObservabilityMetrics

def record_in_worker():
    worker_metrics = ObservabilityMetrics("multiprocess-api")
    worker_metrics.record_request("GET", "/worker", 200, 0.25)

master_metrics = ObservabilityMetrics("multiprocess-api")
master_metrics.record_request("GET", "/master", 200, 0.125)
if "fork" in multiprocessing.get_all_start_methods():
    multiprocessing.set_start_method("fork")
worker = multiprocessing.Process(target=record_in_worker)
worker.start()
worker.join(timeout=10)
assert worker.exitcode == 0
before = {name for name in os.listdir(os.environ["PROMETHEUS_MULTIPROC_DIR"]) if name.startswith("gauge_")}
multiprocess.mark_process_dead(worker.pid, path=os.environ["PROMETHEUS_MULTIPROC_DIR"])
after = {name for name in os.listdir(os.environ["PROMETHEUS_MULTIPROC_DIR"]) if name.startswith("gauge_")}
payload = master_metrics.scrape_prometheus()
assert any(name.endswith(f"_{worker.pid}.db") for name in before)
assert not any(name.endswith(f"_{worker.pid}.db") for name in after)
assert any(name.endswith(f"_{os.getpid()}.db") for name in after)
assert 'route="/worker"' in payload
assert 'route="/master"' in payload
assert "http_server_request_duration_seconds_count" in payload
print(json.dumps({"dead_worker_gauges_removed": True, "live_gauges_preserved": True}))
'''
    environment = {
        **os.environ,
        "PROMETHEUS_MULTIPROC_DIR": str(tmp_path),
        "PYTHONPATH": str(source_root),
    }

    # Act
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert '"dead_worker_gauges_removed": true' in result.stdout


def _run_startup_script(tmp_path, private_directory=None, hostname=None):
    source_root = Path(__file__).resolve().parents[1]
    capture = tmp_path / "gunicorn-args.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_gunicorn = fake_bin / "gunicorn"
    fake_gunicorn.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PROMETHEUS_MULTIPROC_DIR\" \"$@\" > \"$START_GUNICORN_CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_gunicorn.chmod(0o755)
    script_path = source_root / "scripts" / "start-gunicorn.sh"
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "START_GUNICORN_CAPTURE": str(capture)}
    if private_directory is None:
        environment.pop("PROMETHEUS_MULTIPROC_DIR", None)
    else:
        environment["PROMETHEUS_MULTIPROC_DIR"] = str(private_directory)
    if hostname is not None:
        environment["HOSTNAME"] = hostname
    result = subprocess.run(
        [str(script_path), "--config", "/app/api/gunicorn.conf.py", "app:app"],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, capture


def test_startup_script_reuses_private_owned_directory_and_cleans_only_metric_files(tmp_path):
    # Arrange
    private_directory = Path(f"/tmp/python-demo-prometheus-test-{uuid.uuid4().hex}")
    assert not private_directory.exists()

    try:
        # Act
        first_result, capture = _run_startup_script(tmp_path, private_directory)
        owner_marker = private_directory / ".python-demo-prometheus-owner"
        stale_counter = private_directory / "counter_123.db"
        stale_histogram = private_directory / "histogram_123.db"
        unrelated_file = private_directory / "unrelated.txt"
        unrelated_directory = private_directory / "unrelated"
        unrelated_directory.mkdir()
        unrelated_child = unrelated_directory / "keep.txt"
        stale_counter.write_text("stale counter", encoding="utf-8")
        stale_histogram.write_text("stale histogram", encoding="utf-8")
        unrelated_file.write_text("keep this file", encoding="utf-8")
        unrelated_child.write_text("keep this nested file", encoding="utf-8")
        second_result, second_capture = _run_startup_script(tmp_path, private_directory)

        # Assert
        assert first_result.returncode == 0, first_result.stderr
        assert owner_marker.is_file()
        assert str(private_directory) in owner_marker.read_text(encoding="utf-8")
        assert str(os.getuid()) in owner_marker.read_text(encoding="utf-8")
        assert stat.S_IMODE(private_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(owner_marker.stat().st_mode) == 0o600
        assert second_result.returncode == 0, second_result.stderr
        assert not stale_counter.exists()
        assert not stale_histogram.exists()
        assert unrelated_file.read_text(encoding="utf-8") == "keep this file"
        assert unrelated_child.read_text(encoding="utf-8") == "keep this nested file"
        assert capture.read_text(encoding="utf-8").splitlines() == [
            str(private_directory),
            "--config",
            "/app/api/gunicorn.conf.py",
            "app:app",
        ]
        assert second_capture.read_text(encoding="utf-8").splitlines() == capture.read_text(encoding="utf-8").splitlines()
    finally:
        shutil.rmtree(private_directory, ignore_errors=True)


def test_startup_script_rejects_non_private_directory_without_deleting_files(tmp_path):
    # Arrange
    private_directory = Path(f"/tmp/python-demo-prometheus-test-{uuid.uuid4().hex}")
    private_directory.mkdir(mode=0o755)
    os.chmod(private_directory, 0o755)
    unrelated_file = private_directory / "important.txt"
    unrelated_file.write_text("must survive", encoding="utf-8")

    try:
        # Act
        result, capture = _run_startup_script(tmp_path, private_directory)

        # Assert
        assert result.returncode != 0
        assert "mode 0700" in result.stderr.lower()
        assert unrelated_file.read_text(encoding="utf-8") == "must survive"
        assert not capture.exists()
    finally:
        shutil.rmtree(private_directory, ignore_errors=True)


def test_startup_script_rejects_mismatched_owner_marker_without_deleting_files(tmp_path):
    # Arrange
    private_directory = Path(f"/tmp/python-demo-prometheus-test-{uuid.uuid4().hex}")
    private_directory.mkdir(mode=0o700)
    owner_marker = private_directory / ".python-demo-prometheus-owner"
    owner_marker.write_text("owned by another instance", encoding="utf-8")
    owner_marker.chmod(0o600)
    stale_metric = private_directory / "counter_123.db"
    stale_metric.write_text("must survive", encoding="utf-8")

    try:
        # Act
        result, capture = _run_startup_script(tmp_path, private_directory)

        # Assert
        assert result.returncode != 0
        assert "owner" in result.stderr.lower()
        assert stale_metric.read_text(encoding="utf-8") == "must survive"
        assert not capture.exists()
    finally:
        shutil.rmtree(private_directory, ignore_errors=True)


def test_startup_script_exports_generated_directory_when_environment_is_unset(tmp_path):
    # Arrange
    hostname = f"metrics-default-{uuid.uuid4().hex}"
    generated_directory = Path(f"/tmp/python-demo-prometheus-{hostname}")
    assert not generated_directory.exists()

    try:
        # Act
        result, capture = _run_startup_script(tmp_path, hostname=hostname)

        # Assert
        assert result.returncode == 0, result.stderr
        assert capture.read_text(encoding="utf-8").splitlines()[0] == str(generated_directory)
        assert stat.S_IMODE(generated_directory.stat().st_mode) == 0o700
        assert (generated_directory / ".python-demo-prometheus-owner").is_file()
    finally:
        shutil.rmtree(generated_directory, ignore_errors=True)
