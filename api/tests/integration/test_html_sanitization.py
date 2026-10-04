"""HTML sanitization checks across isolated MySQL, Flask, and Keycloak services."""

import json
import os
import subprocess
import sys
import uuid
import warnings
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "docker-compose.yml"
PRE_SANITIZATION_REVISION = "20261004_0002"
CURRENT_REVISION = "20261004_0003"
UNSAFE_HTML = '<p onclick="run()">Legacy text</p><script>legacy-secret()</script>'
SAFE_LEGACY_HTML = "<p>Legacy text</p>"

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(
        os.getenv("RUN_HTML_SANITIZATION_INTEGRATION") != "1",
        reason=(
            "set RUN_HTML_SANITIZATION_INTEGRATION=1 to run isolated MySQL, API, "
            "and Keycloak tests"
        ),
    ),
]


class IsolatedHtmlProject:
    """Own a uniquely named Compose project and its private resources."""

    def __init__(self, tmp_path: Path, image: str):
        self.name = f"python-demo-html-test-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.database_password = f"db-{uuid.uuid4().hex}"
        self.root_password = f"root-{uuid.uuid4().hex}"
        self.override = tmp_path / "compose.override.yml"
        self.override.write_text(
            f"""services:
  api:
    image: {self.image}
    volumes: !reset []
    ports: !reset []
    environment:
      FLASK_ENV: development
      DATABASE_URL: mysql+pymysql://html-test:{self.database_password}@db:3306/html_test
      KEYCLOAK_BASE_URL: http://keycloak:8080
      KEYCLOAK_ISSUER: https://identity.example.test/realms/python-demo
      KEYCLOAK_REALM: python-demo
      KEYCLOAK_CLIENT_ID: python-demo-api
      KEYCLOAK_CLIENT_SECRET: python-demo-api-secret
      KEYCLOAK_AUDIENCE: python-demo-api
  api-init:
    image: {self.image}
    environment:
      FLASK_ENV: development
      DATABASE_URL: mysql+pymysql://html-test:{self.database_password}@db:3306/html_test
      KEYCLOAK_BASE_URL: http://keycloak:8080
      KEYCLOAK_ISSUER: https://identity.example.test/realms/python-demo
      KEYCLOAK_REALM: python-demo
      KEYCLOAK_CLIENT_ID: python-demo-api
      KEYCLOAK_CLIENT_SECRET: python-demo-api-secret
      KEYCLOAK_AUDIENCE: python-demo-api
  db:
    ports: !reset []
    environment:
      MYSQL_ROOT_PASSWORD: {self.root_password}
      MYSQL_DATABASE: html_test
      MYSQL_USER: html-test
      MYSQL_PASSWORD: {self.database_password}
  keycloak:
    ports: !reset []
    environment:
      KC_HOSTNAME: https://identity.example.test
      KC_HOSTNAME_BACKCHANNEL_DYNAMIC: "true"
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

    def run(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: int = 180,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [*self.prefix, *args],
            cwd=REPOSITORY_ROOT,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            safe_stderr = result.stderr
            for secret in (self.database_password, self.root_password):
                safe_stderr = safe_stderr.replace(secret, "[redacted]")
            pytest.fail(
                f"isolated Compose command failed ({args[0]}), exit={result.returncode}: "
                f"{safe_stderr[-2000:]}"
            )
        return result

    def run_api_script(self, source: str, *, run_once: bool = False, timeout: int = 120) -> dict:
        command = (
            ["run", "--rm", "--no-deps", "api-init"]
            if run_once
            else ["exec", "-T", "api"]
        )
        result = self.run(*command, "python", "-", input_text=source, timeout=timeout)
        if result.returncode:
            pytest.fail(f"isolated API scenario failed, exit={result.returncode}")
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            pytest.fail("isolated API scenario did not return a JSON result")


@pytest.fixture(scope="module")
def html_project(tmp_path_factory):
    """Build an isolated image and exercise only this run's Compose resources."""
    image = f"python-demo-html-test:{uuid.uuid4().hex[:12]}"
    project = IsolatedHtmlProject(tmp_path_factory.mktemp("html-sanitization"), image)
    image_built = False
    try:
        build = subprocess.run(
            ["docker", "build", "--target", "api-app", "--tag", image, "--file", "Dockerfile", "."],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if build.returncode:
            pytest.fail(f"isolated API image build failed, exit={build.returncode}")
        image_built = True

        project.run("up", "--detach", "db", "keycloak", timeout=360)
        project.run("up", "--exit-code-from", "api-init", "api-init", timeout=240)
        project.run(
            "run",
            "--rm",
            "--no-deps",
            "api-init",
            "flask",
            "db",
            "downgrade",
            PRE_SANITIZATION_REVISION,
        )

        # Insert a row while the schema is at 0002, before the sanitization columns exist.
        legacy_row = project.run_api_script(
            """import json
from sqlalchemy import text
from app import create_app
from extensions import db

app = create_app()
with app.app_context():
    author_id = db.session.execute(text('SELECT id FROM authors ORDER BY id LIMIT 1')).scalar_one()
    result = db.session.execute(
        text('''INSERT INTO articles
            (title, slug, published_label, post_entry, tags, author_id, created_at, updated_at)
            VALUES (:title, :slug, :label, :body, :tags, :author_id, NOW(), NOW())'''),
        {'title': 'Legacy HTML integration', 'slug': 'legacy-html-integration',
         'label': 'Today', 'body': '<p onclick="run()">Legacy text</p><script>legacy-secret()</script>',
         'tags': '[]', 'author_id': author_id},
    )
    db.session.commit()
    print(json.dumps({'author_id': author_id, 'article_id': result.lastrowid}))
""",
            run_once=True,
        )
        project.legacy_article_id = legacy_row["article_id"]
        project.author_id = legacy_row["author_id"]

        project.run(
            "up",
            "--force-recreate",
            "--exit-code-from",
            "api-init",
            "api-init",
            timeout=240,
        )
        project.run("up", "--detach", "api", timeout=180)
        readiness = project.run_api_script(
            """import json
import time
import requests

deadline = time.monotonic() + 180
api_ready = False
keycloak_ready = False
while time.monotonic() < deadline and not (api_ready and keycloak_ready):
    try:
        api_ready = requests.get('http://api:3000/liveness', timeout=3).status_code == 200
        response = requests.post(
            'http://keycloak:8080/realms/master/protocol/openid-connect/token',
            data={'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': 'admin!123'},
            timeout=3,
        )
        keycloak_ready = response.status_code == 200
    except requests.RequestException:
        time.sleep(2)
print(json.dumps({'api_ready': api_ready, 'keycloak_ready': keycloak_ready}))
""",
            timeout=220,
        )
        if not readiness.get("api_ready") or not readiness.get("keycloak_ready"):
            pytest.fail("isolated API and Keycloak did not become ready")

        yield project
    finally:
        cleanup_failures = []
        down = project.run("down", "--volumes", "--remove-orphans", timeout=180, check=False)
        if down.returncode:
            cleanup_failures.append(_cleanup_failure("Compose project cleanup", down, project))
        if image_built:
            image_removal = subprocess.run(
                ["docker", "image", "rm", "--force", image],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if image_removal.returncode:
                cleanup_failures.append(_cleanup_failure("API image cleanup", image_removal, project))
        if cleanup_failures:
            message = "; ".join(cleanup_failures)
            if sys.exc_info()[0] is not None:
                warnings.warn(message, RuntimeWarning, stacklevel=1)
            else:
                pytest.fail(message, pytrace=False)


def _cleanup_failure(
    operation: str, result: subprocess.CompletedProcess, project: IsolatedHtmlProject
) -> str:
    """Describe an owned-resource cleanup failure without exposing credentials."""
    safe_stderr = result.stderr
    for secret in (project.database_password, project.root_password):
        safe_stderr = safe_stderr.replace(secret, "[redacted]")
    detail = f": {safe_stderr[-1000:]!r}" if safe_stderr else ""
    return f"{operation} failed for this test's isolated resources (exit={result.returncode}){detail}"


@pytest.fixture(scope="module")
def real_tokens(html_project):
    """Create a real Keycloak author and return admin/author Bearer headers."""
    result = html_project.run_api_script(
        """import json
import uuid
import requests

internal = 'http://keycloak:8080'
api = 'http://api:3000'
issuer = 'https://identity.example.test/realms/python-demo'
password = f'html-test-{uuid.uuid4().hex}'
root = requests.post(
    f'{internal}/realms/master/protocol/openid-connect/token',
    data={'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': 'admin!123'},
    timeout=15,
)
assert root.status_code == 200
root_headers = {'Authorization': f"Bearer {root.json()['access_token']}"}

username = f'html-author-{uuid.uuid4().hex[:10]}'
created = requests.post(
    f'{internal}/admin/realms/python-demo/users',
    headers=root_headers,
    json={'username': username, 'firstName': 'HTML', 'lastName': 'Author',
          'email': f'{username}@example.test', 'enabled': True, 'emailVerified': True,
          'requiredActions': [], 'credentials': [{'type': 'password', 'value': password, 'temporary': False}]},
    timeout=15,
)
assert created.status_code == 201
lookup = requests.get(
    f'{internal}/admin/realms/python-demo/users', headers=root_headers,
    params={'username': username, 'exact': 'true'}, timeout=15,
)
assert lookup.status_code == 200 and len(lookup.json()) == 1
role = requests.get(f'{internal}/admin/realms/python-demo/roles/author', headers=root_headers, timeout=15)
assert role.status_code == 200
user_id = lookup.json()[0]['id']
mapped = requests.post(
    f'{internal}/admin/realms/python-demo/users/{user_id}/role-mappings/realm',
    headers=root_headers, json=[role.json()], timeout=15,
)
assert mapped.status_code == 204

client_settings = {'grant_type': 'password', 'client_id': 'python-demo-api',
                   'client_secret': 'python-demo-api-secret'}
admin_login = requests.post(
    f'{internal}/realms/python-demo/protocol/openid-connect/token',
    data={**client_settings, 'username': 'john.doe', 'password': 'john.doe!123'}, timeout=15,
)
author_login = requests.post(
    f'{internal}/realms/python-demo/protocol/openid-connect/token',
    data={**client_settings, 'username': username, 'password': password}, timeout=15,
)
assert admin_login.status_code == 200 and author_login.status_code == 200
admin_token = admin_login.json()['access_token']
author_token = author_login.json()['access_token']
author_claims = json.loads(__import__('base64').urlsafe_b64decode(
    author_token.split('.')[1] + '=' * (-len(author_token.split('.')[1]) % 4)
))
link = requests.put(
    f"{api}/authors/{AUTHOR_ID}/identity", headers={'Authorization': f'Bearer {admin_token}'},
    json={'identity': {'issuer': issuer, 'sub': author_claims['sub']}}, timeout=15,
)
assert link.status_code == 200
print(json.dumps({'admin_token': admin_token, 'author_token': author_token}))
""".replace("{AUTHOR_ID}", str(html_project.author_id)),
    )
    return {
        "admin": {"Authorization": f"Bearer {result['admin_token']}"},
        "author": {"Authorization": f"Bearer {result['author_token']}"},
    }


def test_legacy_mysql_row_is_preserved_but_filtered_by_public_api(html_project):
    # Arrange
    article_id = html_project.legacy_article_id

    # Act
    result = html_project.run_api_script(
        f"""import json
import requests
from app import create_app
from extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    revision = db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    stored = db.session.execute(
        text('SELECT post_entry, bypass_sanitization FROM articles WHERE id = :id'), {{'id': {article_id}}}
    ).one()
response = requests.get('http://api:3000/articles/{article_id}', timeout=15)
print(json.dumps({{'revision': revision, 'stored_html': stored.post_entry,
                   'bypass': bool(stored.bypass_sanitization), 'status': response.status_code,
                   'public': response.json()}}))
""",
    )

    # Assert
    assert result["revision"] == CURRENT_REVISION
    assert result["stored_html"] == UNSAFE_HTML
    assert result["bypass"] is False
    assert result["status"] == 200
    assert result["public"]["post_entry"] == SAFE_LEGACY_HTML


def test_admin_bypass_preserves_raw_html_for_storage_and_public_output(html_project, real_tokens):
    # Arrange
    source = '<p onclick="run()">Trusted HTML</p><script>trusted-secret()</script>'

    # Act
    result = html_project.run_api_script(
        f"""import json
import uuid
import requests
from app import create_app
from extensions import db
from models import Article

headers = {json.dumps(real_tokens['admin'])}
author_id = {html_project.author_id}
created = requests.post('http://api:3000/articles', headers=headers, json={{'article': {{
    'title': 'Admin bypass integration', 'slug': f'admin-bypass-{{uuid.uuid4().hex}}',
    'published_label': 'Today', 'post_entry': {json.dumps(source)}, 'tags': [],
    'author_id': author_id, 'bypass_sanitization': True,
}}}}, timeout=15)
article_id = created.json().get('id')
public = requests.get(f'http://api:3000/articles/{{article_id}}', timeout=15)
app = create_app()
with app.app_context():
    stored = db.session.get(Article, article_id)
    stored_html, bypass = stored.post_entry, stored.bypass_sanitization
print(json.dumps({{'create_status': created.status_code, 'public_status': public.status_code,
                  'stored_html': stored_html, 'bypass': bypass,
                  'public_html': public.json().get('post_entry')}}))
""",
    )

    # Assert
    assert result["create_status"] == 201
    assert result["public_status"] == 200
    assert result["stored_html"] == source
    assert result["bypass"] is True
    assert result["public_html"] == source


def test_author_cannot_set_bypass_and_body_edit_clears_existing_admin_bypass(
    html_project, real_tokens
):
    # Arrange
    raw_admin_html = '<p onclick="run()">Approved raw body</p><script>approved-secret()</script>'
    author_html = '<p onclick="run()">Author replacement</p><script>author-secret()</script>'

    # Act
    result = html_project.run_api_script(
        f"""import json
import uuid
import requests
from app import create_app
from extensions import db
from models import Article

admin = {json.dumps(real_tokens['admin'])}
author = {json.dumps(real_tokens['author'])}
created = requests.post('http://api:3000/articles', headers=admin, json={{'article': {{
    'title': 'Author lifecycle integration', 'slug': f'author-lifecycle-{{uuid.uuid4().hex}}',
    'published_label': 'Today', 'post_entry': {json.dumps(raw_admin_html)}, 'tags': [],
    'author_id': {html_project.author_id}, 'bypass_sanitization': True,
}}}}, timeout=15)
article_id = created.json().get('id')
false_flag = requests.patch(f'http://api:3000/articles/{{article_id}}', headers=author,
                            json={{'article': {{'bypass_sanitization': False}}}}, timeout=15)
app = create_app()
with app.app_context():
    rejected_article = db.session.get(Article, article_id)
    rejected_stored_html = rejected_article.post_entry
    rejected_bypass = rejected_article.bypass_sanitization
rejected_public = requests.get(f'http://api:3000/articles/{{article_id}}', timeout=15)
body_edit = requests.patch(f'http://api:3000/articles/{{article_id}}', headers=author,
                           json={{'article': {{'post_entry': {json.dumps(author_html)}}}}}, timeout=15)
public = requests.get(f'http://api:3000/articles/{{article_id}}', timeout=15)
with app.app_context():
    db.session.expire_all()
with app.app_context():
    stored = db.session.get(Article, article_id)
    stored_html, bypass = stored.post_entry, stored.bypass_sanitization
print(json.dumps({{'create_status': created.status_code, 'false_flag_status': false_flag.status_code,
                  'rejected_stored_html': rejected_stored_html, 'rejected_bypass': rejected_bypass,
                  'rejected_public_html': rejected_public.json().get('post_entry'),
                  'rejected_public_bypass': rejected_public.json().get('bypass_sanitization'),
                  'edit_status': body_edit.status_code, 'public_html': public.json().get('post_entry'),
                  'stored_html': stored_html, 'bypass': bypass}}))
""",
    )

    # Assert
    assert result["create_status"] == 201
    assert result["false_flag_status"] == 422
    assert result["rejected_stored_html"] == raw_admin_html
    assert result["rejected_bypass"] is True
    assert result["rejected_public_html"] == raw_admin_html
    assert result["rejected_public_bypass"] is True
    assert result["edit_status"] == 200
    assert result["public_html"] == SAFE_LEGACY_HTML.replace("Legacy text", "Author replacement")
    assert result["stored_html"] == result["public_html"]
    assert result["bypass"] is False


def test_author_metadata_patch_preserves_admin_bypass(html_project, real_tokens):
    # Arrange
    raw_admin_html = '<p onclick="run()">Approved metadata body</p><script>metadata-secret()</script>'

    # Act
    result = html_project.run_api_script(
        f"""import json
import uuid
import requests
from app import create_app
from extensions import db
from models import Article

admin = {json.dumps(real_tokens['admin'])}
author = {json.dumps(real_tokens['author'])}
created = requests.post('http://api:3000/articles', headers=admin, json={{'article': {{
    'title': 'Metadata lifecycle integration', 'slug': f'metadata-lifecycle-{{uuid.uuid4().hex}}',
    'published_label': 'Today', 'post_entry': {json.dumps(raw_admin_html)}, 'tags': [],
    'author_id': {html_project.author_id}, 'bypass_sanitization': True,
}}}}, timeout=15)
article_id = created.json().get('id')
updated = requests.patch(f'http://api:3000/articles/{{article_id}}', headers=author,
                         json={{'article': {{'title': 'Metadata changed'}}}}, timeout=15)
public = requests.get(f'http://api:3000/articles/{{article_id}}', timeout=15)
app = create_app()
with app.app_context():
    stored = db.session.get(Article, article_id)
    stored_html, bypass, title = stored.post_entry, stored.bypass_sanitization, stored.title
print(json.dumps({{'create_status': created.status_code, 'update_status': updated.status_code,
                  'title': title, 'stored_html': stored_html, 'bypass': bypass,
                  'public_html': public.json().get('post_entry')}}))
""",
    )

    # Assert
    assert result["create_status"] == 201
    assert result["update_status"] == 200
    assert result["title"] == "Metadata changed"
    assert result["stored_html"] == raw_admin_html
    assert result["bypass"] is True
    assert result["public_html"] == raw_admin_html
