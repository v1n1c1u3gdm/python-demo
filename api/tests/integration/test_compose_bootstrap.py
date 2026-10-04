"""End-to-end bootstrap checks against isolated Compose MySQL projects."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "docker-compose.yml"
INVALID_DATABASE_PASSWORD = "fake-invalid-bootstrap-secret-91"

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(
        os.getenv("RUN_COMPOSE_INTEGRATION") != "1",
        reason="set RUN_COMPOSE_INTEGRATION=1 to run isolated Docker Compose tests",
    ),
]


@pytest.fixture(scope="session")
def test_api_image():
    """Build one uniquely tagged API image from the current source for this run."""
    image = f"python-demo-bootstrap-test:{uuid.uuid4().hex[:12]}"
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


class ComposeProject:
    """Run Compose commands in a uniquely named project with private resources."""

    def __init__(self, tmp_path: Path, image: str, invalid_init_credentials: bool = False):
        self.name = f"python-demo-bootstrap-test-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.invalid_init_credentials = invalid_init_credentials
        self.override = tmp_path / "compose.override.yml"
        init_database_url = (
            "mysql+pymysql://missing-bootstrap-user:fake-invalid-bootstrap-secret-91@db:3306/bootstrap_test"
            if invalid_init_credentials
            else "mysql+pymysql://bootstrap-test:bootstrap-test-password@db:3306/bootstrap_test"
        )
        self.override.write_text(
            f"""services:
  api:
    image: {self.image}
    ports: !reset []
    volumes: !reset []
    environment:
      FLASK_ENV: development
      DATABASE_URL: mysql+pymysql://bootstrap-test:bootstrap-test-password@db:3306/bootstrap_test
      VINICIUS_PUBLIC_KEY: ssh-ed25519 AAAATESTONLY
  api-init:
    image: {self.image}
    environment:
      FLASK_ENV: development
      DATABASE_URL: {init_database_url}
      VINICIUS_PUBLIC_KEY: ssh-ed25519 AAAATESTONLY
  db:
    environment:
      MYSQL_ROOT_PASSWORD: bootstrap-test-root-password
      MYSQL_DATABASE: bootstrap_test
      MYSQL_USER: bootstrap-test
      MYSQL_PASSWORD: bootstrap-test-password
    ports: !reset []
  ui:
    ports: !reset []
  keycloak:
    ports: !reset []
volumes:
  mysql_data:
""",
            encoding="utf-8",
        )
        self.command_prefix = [
            "docker",
            "compose",
            "--project-name",
            self.name,
            "--file",
            str(COMPOSE_FILE),
            "--file",
            str(self.override),
        ]

    def run(self, *args: str, timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [*self.command_prefix, *args],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            safe_output = result.stderr.replace(INVALID_DATABASE_PASSWORD, "[redacted]")
            safe_output = safe_output.replace("bootstrap-test-password", "[redacted]")
            safe_output = safe_output.replace("bootstrap-test-root-password", "[redacted]")
            pytest.fail(f"Compose command failed ({args[0]}): {safe_output[-3000:]}")
        return result

    def up(self, *services: str, force_recreate: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        options = ["up", "--detach", "--no-build"]
        if force_recreate:
            options.append("--force-recreate")
        return self.run(*options, *services, check=check)

    def container_id(self, service: str) -> str | None:
        result = self.run("ps", "--all", "--quiet", service, check=False)
        container_ids = result.stdout.strip().splitlines()
        return container_ids[0] if container_ids else None

    def container_state(self, service: str) -> tuple[str, str, str] | None:
        container_id = self.container_id(service)
        if container_id is None:
            return None
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}|{{.State.ExitCode}}|{{.State.FinishedAt}}", container_id],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            return None
        return tuple(result.stdout.strip().split("|", maxsplit=2))

    def wait_for_exit(self, service: str, timeout: int = 120) -> tuple[str, str, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.container_state(service)
            if state is not None and state[0] in {"exited", "dead"}:
                return state
            time.sleep(2)
        pytest.fail(f"{service} did not exit within {timeout} seconds")

    def wait_for_database(self, timeout: int = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            probe = self.run(
                "exec", "-T", "db", "mysqladmin", "ping", "-h", "127.0.0.1", "-ubootstrap-test",
                "-pbootstrap-test-password", check=False,
            )
            if probe.returncode == 0:
                return
            time.sleep(2)
        pytest.fail("isolated MySQL did not become healthy within 120 seconds")

    def http_get_articles(self) -> tuple[int, object] | None:
        script = """import json
import urllib.error
import urllib.request

request = urllib.request.Request("http://127.0.0.1:3000/articles")
try:
    response = urllib.request.urlopen(request, timeout=120)
    status = response.status
    body = response.read()
except urllib.error.HTTPError as error:
    status = error.code
    body = error.read()
print(json.dumps({"status": status, "body": body.decode()}))
"""
        result = self.run("exec", "-T", "api", "python", "-c", script, check=False)
        if result.returncode:
            return None
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        try:
            body = json.loads(payload["body"])
        except json.JSONDecodeError:
            body = payload["body"]
        return payload["status"], body

    def wait_for_api(self, timeout: int = 120) -> tuple[int, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.http_get_articles()
            if response and response[0] == 200:
                return response
            time.sleep(2)
        pytest.fail("API did not return HTTP 200 within 120 seconds")

    def database_state(self) -> dict[str, object]:
        script = """import json
from app import create_app
from extensions import db
from models import Author, SeedRun

app = create_app()
with app.app_context():
    revision = db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar_one()
    seed_count = SeedRun.query.count()
    author = Author.query.first()
    print(json.dumps({"revision": revision, "seed_count": seed_count, "author_name": author.name, "author_bio": author.bio}))
"""
        result = self.run("run", "--rm", "--no-deps", "api", "python", "-c", script)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def set_author_bio(self, bio: str) -> None:
        script = """from app import create_app
from extensions import db
from models import Author

app = create_app()
with app.app_context():
    author = Author.query.first()
    author.bio = """ + repr(bio) + "\n    db.session.commit()\n"
        self.run("run", "--rm", "--no-deps", "api", "python", "-c", script)

    def worker_count(self) -> int:
        container_id = self.container_id("api")
        assert container_id is not None
        result = subprocess.run(
            ["docker", "top", container_id, "-eo", "pid,ppid,args"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        processes = []
        for line in result.stdout.splitlines()[1:]:
            fields = line.strip().split(maxsplit=2)
            if len(fields) == 3:
                process_id, parent_id, command = fields
                processes.append((process_id, parent_id, command))
        return max(
            (
                sum(child[1] == process[0] for child in processes)
                for process in processes
                if "gunicorn" in process[2]
            ),
            default=0,
        )

    def init_logs(self) -> str:
        result = self.run("logs", "api-init", check=False)
        return result.stdout + result.stderr


@pytest.fixture
def compose_project(tmp_path, test_api_image):
    """Arrange a unique project, then clean up only its named resources."""
    project = ComposeProject(tmp_path, test_api_image)
    try:
        yield project
    finally:
        project.run("down", "--volumes", "--remove-orphans", check=False)


def start_ready_api(project: ComposeProject) -> tuple[dict[str, object], tuple[int, object]]:
    """Start the completed init job first, then start and probe the API."""
    project.up("api-init")
    init_state = project.wait_for_exit("api-init")
    assert init_state[1] == "0"
    project.up("api")
    response = project.wait_for_api()
    return project.database_state(), response


def test_fresh_mysql_is_seeded_before_four_workers_serve_articles(compose_project):
    """A clean MySQL is bootstrapped before four API workers serve seeded articles."""
    # Arrange
    compose_project.run("up", "--detach", "--build", "api")

    # Act
    status, articles = compose_project.wait_for_api()

    # Assert
    assert status == 200
    assert articles
    init_state = compose_project.wait_for_exit("api-init")
    database = compose_project.database_state()
    assert init_state[1] == "0"
    assert compose_project.worker_count() == 4
    assert database["revision"]
    assert database["seed_count"] == 1

    # Act: verify the regular build-and-start command after init already succeeded.
    compose_project.run("up", "--detach", "--build", "api")
    repeated_status, repeated_articles = compose_project.wait_for_api()
    state_after_repeat = compose_project.database_state()

    # Assert
    assert repeated_status == 200
    assert repeated_articles
    assert state_after_repeat["revision"] == database["revision"]
    assert state_after_repeat["seed_count"] == 1


def test_repeated_init_preserves_mysql_edits_and_single_seed_record(compose_project):
    """Recreating init leaves applied migrations, edits, and the seed record intact."""
    # Arrange
    initial_state, _ = start_ready_api(compose_project)
    compose_project.run("stop", "api")
    compose_project.set_author_bio("Integration edit preserved across bootstrap")

    # Act
    compose_project.up("api-init", force_recreate=True)
    repeated_init_state = compose_project.wait_for_exit("api-init")
    state_after_init = compose_project.database_state()

    # Assert
    assert repeated_init_state[1] == "0"
    assert state_after_init["revision"] == initial_state["revision"]
    assert state_after_init["author_bio"] == "Integration edit preserved across bootstrap"
    assert state_after_init["seed_count"] == 1


def test_failed_init_prevents_api_start_and_does_not_log_invalid_password(tmp_path, test_api_image):
    """An init with invalid database credentials blocks API and hides its password."""
    # Arrange
    override_directory = tmp_path / "failed-init"
    override_directory.mkdir()
    failed_project = ComposeProject(override_directory, test_api_image, invalid_init_credentials=True)
    try:
        # Act
        failed_project.run("up", "--detach", "--no-build", "api", check=False)
        init_state = failed_project.wait_for_exit("api-init")
        logs = failed_project.init_logs()
        api_state = failed_project.container_state("api")

        # Assert
        assert init_state[1] != "0"
        assert api_state is None or api_state[0] != "running"
        assert INVALID_DATABASE_PASSWORD not in logs, "init logs must hide the invalid database password"
    finally:
        failed_project.run("down", "--volumes", "--remove-orphans", check=False)


def test_restarting_api_does_not_rerun_init(compose_project):
    """Restarting API workers leaves the init completion timestamp and database unchanged."""
    # Arrange
    initial_state, _ = start_ready_api(compose_project)
    init_before = compose_project.wait_for_exit("api-init")

    # Act
    compose_project.run("restart", "api")
    status, articles = compose_project.wait_for_api()
    init_after = compose_project.wait_for_exit("api-init")
    state_after = compose_project.database_state()

    # Assert
    assert status == 200
    assert articles
    assert init_after[2] == init_before[2]
    assert state_after == initial_state


def test_recreated_completed_init_runs_again_before_api_starts(compose_project):
    """Force-recreating an exited init runs it again before the API starts."""
    # Arrange
    initial_state, _ = start_ready_api(compose_project)
    initial_init_state = compose_project.wait_for_exit("api-init")
    compose_project.run("stop", "api")

    # Act
    compose_project.up("api-init", force_recreate=True)
    recreated_init_state = compose_project.wait_for_exit("api-init")
    compose_project.up("api")
    status, articles = compose_project.wait_for_api()
    final_state = compose_project.database_state()

    # Assert
    assert recreated_init_state[1] == "0"
    assert recreated_init_state[2] != initial_init_state[2]
    assert status == 200
    assert articles
    assert final_state["revision"] == initial_state["revision"]
    assert final_state["seed_count"] == 1
