"""Isolated Compose checks for aggregated Gunicorn metrics."""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(
        os.getenv("RUN_COMPOSE_METRICS_INTEGRATION") != "1",
        reason="set RUN_COMPOSE_METRICS_INTEGRATION=1 to run isolated Compose metrics tests",
    ),
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def api_image():
    """Build a uniquely named API image and remove only that image afterward."""
    image = f"python-demo-metrics-test:{uuid.uuid4().hex[:12]}"
    result = subprocess.run(
        ["docker", "build", "--target", "api-app", "--tag", image, "--file", "Dockerfile", "."],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        pytest.fail(f"isolated API image build failed: {result.stderr[-3000:]}")
    try:
        yield image
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


class MetricsComposeProject:
    def __init__(self, tmp_path: Path, image: str, *, otel_enabled: bool = False):
        self.name = f"python-demo-metrics-test-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.database_password = f"metrics-db-{uuid.uuid4().hex}"
        self.root_password = f"metrics-root-{uuid.uuid4().hex}"
        self.database_url = (
            "mysql+pymysql://metrics-test:"
            f"{self.database_password}@db:3306/metrics_test"
        )
        self.override = tmp_path / "compose.metrics.override.yml"
        self.override.write_text(
            f"""services:
  api:
    image: {self.image}
    ports: !reset []
    volumes: !reset []
    environment:
      FLASK_ENV: development
      DATABASE_URL: {self.database_url}
      KEYCLOAK_BASE_URL: http://keycloak:8080
      KEYCLOAK_ISSUER: https://identity.example.test/realms/python-demo
      KEYCLOAK_REALM: python-demo
      KEYCLOAK_CLIENT_ID: python-demo-api
      KEYCLOAK_CLIENT_SECRET: python-demo-api-secret
      KEYCLOAK_AUDIENCE: python-demo-api
      VINICIUS_PUBLIC_KEY: ssh-ed25519 AAAATESTONLY
      OTEL_METRICS_ENABLED: {str(otel_enabled).lower()}
      OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: http://otel-collector:4318/v1/metrics
  api-init:
    image: {self.image}
    environment:
      FLASK_ENV: development
      DATABASE_URL: {self.database_url}
      VINICIUS_PUBLIC_KEY: ssh-ed25519 AAAATESTONLY
  db:
    ports: !reset []
    environment:
      MYSQL_ROOT_PASSWORD: {self.root_password}
      MYSQL_DATABASE: metrics_test
      MYSQL_USER: metrics-test
      MYSQL_PASSWORD: {self.database_password}
  keycloak:
    ports: !reset []
    environment:
      KC_HOSTNAME: https://identity.example.test
      KC_HOSTNAME_BACKCHANNEL_DYNAMIC: "true"
  ui:
    ports: !reset []
volumes:
  mysql_data:
""",
            encoding="utf-8",
        )
        self.prefix = [
            "docker",
            "compose",
            "--project-name",
            self.name,
            "--file",
            str(COMPOSE_FILE),
            "--file",
            str(self.override),
        ]

    def run(self, *args: str, timeout: int = 180, check: bool = True):
        result = subprocess.run(
            [*self.prefix, *args],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            pytest.fail(f"isolated Compose command failed ({args[0]}): {result.stderr[-3000:]}")
        return result

    def api_python(self, source: str, timeout: int = 120):
        result = self.run("exec", "-T", "api", "python", "-c", source, timeout=timeout, check=False)
        if result.returncode:
            pytest.fail(f"isolated API scenario failed: {result.stderr[-3000:]}")
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            pytest.fail("isolated API scenario did not return JSON")

    def wait_for_services(self, timeout: int = 240) -> None:
        probe = """import json, time, requests
deadline = time.monotonic() + 200
api_ready = keycloak_ready = False
while time.monotonic() < deadline and not (api_ready and keycloak_ready):
    try:
        api_ready = requests.get('http://127.0.0.1:3000/liveness', timeout=2).status_code == 200
        token = requests.post('http://keycloak:8080/realms/master/protocol/openid-connect/token', data={
            'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': 'admin!123'
        }, timeout=2)
        keycloak_ready = token.status_code == 200
    except requests.RequestException:
        time.sleep(2)
print(json.dumps({'api_ready': api_ready, 'keycloak_ready': keycloak_ready}))
"""
        status = self.api_python(probe, timeout=timeout)
        if not status["api_ready"] or not status["keycloak_ready"]:
            pytest.fail("isolated API/Keycloak project did not become ready")

    def admin_access_token(self) -> str:
        token_request = """import json, requests
response = requests.post('http://keycloak:8080/realms/python-demo/protocol/openid-connect/token', data={
    'grant_type': 'password', 'client_id': 'python-demo-api', 'client_secret': 'python-demo-api-secret',
    'username': 'john.doe', 'password': 'john.doe!123'
}, timeout=15)
assert response.status_code == 200, response.status_code
print(json.dumps({'access_token': response.json()['access_token']}))
"""
        return self.api_python(token_request)["access_token"]

    def metrics_for_liveness(self, access_token: str) -> int:
        source = f"""import json, requests
from prometheus_client.parser import text_string_to_metric_families
response = requests.get('http://127.0.0.1:3000/metrics', headers={{'Authorization': 'Bearer {access_token}'}}, timeout=15)
assert response.status_code == 200, response.status_code
count = next(sample.value for family in text_string_to_metric_families(response.text) for sample in family.samples
    if sample.name == 'http_server_requests_total' and sample.labels == {{'method': 'GET', 'route': '/liveness', 'status': '200'}})
print(json.dumps({{'count': count}}))
"""
        return int(self.api_python(source)["count"])


@pytest.fixture(scope="module")
def metrics_project(tmp_path_factory, api_image):
    project = MetricsComposeProject(tmp_path_factory.mktemp("metrics-project"), api_image)
    try:
        project.run("up", "--detach", "api", "keycloak", timeout=360)
        project.wait_for_services()
        yield project
    finally:
        project.run("down", "--volumes", "--remove-orphans", timeout=180, check=False)


@pytest.fixture(scope="module")
def otel_metrics_project(tmp_path_factory, api_image):
    project = MetricsComposeProject(
        tmp_path_factory.mktemp("otel-metrics-project"), api_image, otel_enabled=True
    )
    try:
        project.run(
            "--profile", "telemetry", "up", "--detach", "api", "keycloak", "otel-collector",
            timeout=360,
        )
        project.wait_for_services()
        yield project
    finally:
        project.run(
            "--profile", "telemetry", "down", "--volumes", "--remove-orphans",
            timeout=180, check=False,
        )


def test_four_gunicorn_workers_contribute_to_single_scrape(metrics_project):
    # Arrange
    access_token = metrics_project.admin_access_token()
    before = metrics_project.metrics_for_liveness(access_token)
    exercise_workers = """import concurrent.futures, json, requests
def request_liveness(_):
    response = requests.get('http://127.0.0.1:3000/liveness', headers={'Connection': 'close'}, timeout=15)
    assert response.status_code == 200
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
    list(pool.map(request_liveness, range(128)))
from pathlib import Path
import os
directory = Path(os.environ['PROMETHEUS_MULTIPROC_DIR'])
active_counter_files = [path for path in directory.glob('counter_*.db') if path.stat().st_size > 0]
print(json.dumps({'worker_counter_files': len(active_counter_files)}))
"""

    # Act
    exercise = metrics_project.api_python(exercise_workers, timeout=60)
    after = metrics_project.metrics_for_liveness(access_token)

    # Assert
    assert exercise["worker_counter_files"] == 4
    assert after - before == 128


def test_metrics_directory_is_unique_per_compose_project(tmp_path):
    # Arrange
    project_names = [f"metrics-path-{uuid.uuid4().hex[:10]}" for _ in range(2)]

    # Act
    directories = []
    api_environments = []
    init_environments = []
    init_commands = []
    for name in project_names:
        result = subprocess.run(
            ["docker", "compose", "--project-name", name, "--file", str(COMPOSE_FILE), "config", "--format", "json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        services = json.loads(result.stdout)["services"]
        api_environments.append(services["api"]["environment"])
        directories.append(api_environments[-1]["PROMETHEUS_MULTIPROC_DIR"])
        init_environments.append(services["api-init"]["environment"])
        init_commands.append(services["api-init"]["command"])

    # Assert
    assert directories[0] != directories[1]
    assert all(path.startswith("/tmp/python-demo-prometheus-") for path in directories)
    assert all(environment["FLASK_ENV"] == "development" for environment in api_environments)
    assert all(environment["DATABASE_URL"] == init_environment["DATABASE_URL"] for environment, init_environment in zip(api_environments, init_environments))
    assert all("VINICIUS_PUBLIC_KEY" in environment and "LOG_DIR" in environment for environment in api_environments)
    assert all("PROMETHEUS_MULTIPROC_DIR" not in environment for environment in init_environments)
    assert init_commands == [["flask", "bootstrap-db"], ["flask", "bootstrap-db"]]


def test_dead_worker_is_marked_without_clearing_live_workers(metrics_project):
    # Arrange
    access_token = metrics_project.admin_access_token()
    warm_workers = """import concurrent.futures, json, requests
from pathlib import Path
import os
def request_liveness(_):
    response = requests.get('http://127.0.0.1:3000/liveness', headers={'Connection': 'close'}, timeout=15)
    assert response.status_code == 200
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
    list(pool.map(request_liveness, range(128)))
directory = Path(os.environ['PROMETHEUS_MULTIPROC_DIR'])
active = [path for path in directory.glob('counter_*.db') if path.stat().st_size > 0]
print(json.dumps({'worker_counter_files': len(active)}))
"""
    assert metrics_project.api_python(warm_workers, timeout=60)["worker_counter_files"] == 4
    before_count = metrics_project.metrics_for_liveness(access_token)
    terminate_one_worker = """import json, os, signal, time
from pathlib import Path

def workers():
    return sorted(
        int(pid)
        for pid in Path('/proc/1/task/1/children').read_text().split()
    )

initial = workers()
assert len(initial) == 4, initial
directory = Path(os.environ['PROMETHEUS_MULTIPROC_DIR'])
dead_pid = initial[0]
dead_gauge = directory / f'gauge_livesum_{dead_pid}.db'
dead_counter = directory / f'counter_{dead_pid}.db'
dead_histogram = directory / f'histogram_{dead_pid}.db'
assert dead_gauge.exists()
assert dead_counter.exists()
assert dead_histogram.exists()
for pid in initial[1:]:
    assert (directory / f'gauge_livesum_{pid}.db').exists()
os.kill(dead_pid, signal.SIGTERM)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    active = workers()
    if not dead_gauge.exists() and len(active) == 4 and dead_pid not in active:
        break
    time.sleep(0.25)
assert not dead_gauge.exists(), 'Gunicorn child_exit did not remove the dead worker live gauge'
assert dead_counter.exists(), 'dead worker counter accumulation was cleared'
assert dead_histogram.exists(), 'dead worker histogram accumulation was cleared'
assert all((directory / f'gauge_livesum_{pid}.db').exists() for pid in initial[1:])
print(json.dumps({'dead_worker': dead_pid, 'remaining_live_workers': 3}))
"""

    # Act
    stopped = metrics_project.api_python(terminate_one_worker, timeout=80)
    after_count = metrics_project.metrics_for_liveness(access_token)

    # Assert
    assert stopped["remaining_live_workers"] == 3
    assert after_count == before_count


def test_docker_image_default_command_runs_the_startup_script(metrics_project, api_image):
    # Arrange
    container = f"python-demo-metrics-cmd-{uuid.uuid4().hex[:12]}"
    network = f"{metrics_project.name}_default"
    run = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            network,
            "--env",
            "FLASK_ENV=development",
            "--env",
            f"DATABASE_URL={metrics_project.database_url}",
            "--env",
            "KEYCLOAK_BASE_URL=http://keycloak:8080",
            "--env",
            "KEYCLOAK_ISSUER=https://identity.example.test/realms/python-demo",
            "--env",
            "KEYCLOAK_REALM=python-demo",
            "--env",
            "KEYCLOAK_CLIENT_ID=python-demo-api",
            "--env",
            "KEYCLOAK_CLIENT_SECRET=python-demo-api-secret",
            "--env",
            "KEYCLOAK_AUDIENCE=python-demo-api",
            api_image,
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    try:
        # Act
        deadline = time.monotonic() + 60
        response = None
        while time.monotonic() < deadline:
            probe = subprocess.run(
                ["docker", "exec", container, "python", "-c", "import requests; print(requests.get('http://127.0.0.1:3000/liveness', timeout=2).status_code)"],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "200":
                response = probe.stdout.strip()
                break
            time.sleep(1)
        environment = subprocess.run(
            ["docker", "exec", container, "python", "-c", "import json; data=open('/proc/1/environ','rb').read().split(b'\\0'); value=next(item.split(b'=',1)[1].decode() for item in data if item.startswith(b'PROMETHEUS_MULTIPROC_DIR=')); print(json.dumps({'directory': value}))"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        # Assert
        assert response == "200"
        assert environment.returncode == 0, environment.stderr
        directory = json.loads(environment.stdout.strip().splitlines()[-1])["directory"]
        assert directory.startswith("/tmp/python-demo-prometheus-")
        assert directory != "/tmp/python-demo-prometheus-" + metrics_project.name
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def test_otel_collector_receives_distinct_worker_metrics_and_outage_is_nonfatal(otel_metrics_project):
    # Arrange
    generate_worker_metrics = """import concurrent.futures, json, requests
def hit_api(_):
    response = requests.get('http://127.0.0.1:3000/liveness', headers={'Connection': 'close'}, timeout=5)
    assert response.status_code == 200
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
    list(pool.map(hit_api, range(128)))
print(json.dumps({'requests': 128}))
"""
    assert otel_metrics_project.api_python(generate_worker_metrics, timeout=60)["requests"] == 128
    ready_before_stop = otel_metrics_project.api_python(
        "import requests; print(requests.get('http://127.0.0.1:3000/ready', timeout=3).status_code)"
    )
    assert ready_before_stop == 200, ready_before_stop

    # Act
    deadline = time.monotonic() + 45
    collector_logs = ""
    worker_ids = set()
    while time.monotonic() < deadline:
        logs = otel_metrics_project.run("logs", "otel-collector", check=False)
        collector_logs = logs.stdout + logs.stderr
        worker_ids = set(re.findall(r"service\.instance\.id: Str\(([^)\r\n]+)\)", collector_logs))
        if "http_server_requests_total" in collector_logs and len(worker_ids) >= 4:
            break
        time.sleep(1)
    otel_metrics_project.run("stop", "otel-collector", timeout=60)
    outage_probe = otel_metrics_project.api_python(
        """import json, time, requests
results = {}
for name, path in [('liveness', '/liveness'), ('ready', '/ready'), ('articles', '/articles')]:
    started = time.monotonic()
    response = requests.get(f'http://127.0.0.1:3000{path}', timeout=3)
    result = {'status': response.status_code, 'elapsed': time.monotonic() - started}
    if name == 'articles':
        result['json_is_list'] = isinstance(response.json(), list)
    results[name] = result
print(json.dumps(results))
""",
        timeout=10,
    )

    # Assert
    assert "http_server_requests_total" in collector_logs, collector_logs[-4000:]
    assert len(worker_ids) >= 4, sorted(worker_ids)
    assert outage_probe["liveness"]["status"] == 200, outage_probe
    assert outage_probe["liveness"]["elapsed"] < 3, outage_probe
    assert outage_probe["ready"]["status"] == 200, outage_probe
    assert outage_probe["ready"]["elapsed"] < 3, outage_probe
    assert outage_probe["articles"]["status"] == 200, outage_probe
    assert outage_probe["articles"]["json_is_list"] is True, outage_probe
    assert outage_probe["articles"]["elapsed"] < 3, outage_probe
