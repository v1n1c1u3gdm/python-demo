"""Real Keycloak authorization checks in a disposable Compose project."""

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(
        os.getenv("RUN_KEYCLOAK_INTEGRATION") != "1",
        reason="set RUN_KEYCLOAK_INTEGRATION=1 to run isolated Keycloak authorization tests",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = ROOT / "docker-compose.yml"


class IsolatedKeycloakProject:
    def __init__(self, tmp_path: Path, image: str):
        self.name = f"python-demo-keycloak-test-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.override = tmp_path / "compose.override.yml"
        self.db_password = f"db-{uuid.uuid4().hex}"
        self.root_password = f"root-{uuid.uuid4().hex}"
        self.override.write_text(
            f"""services:
  api:
    image: {self.image}
    volumes: !reset []
    ports: !reset []
    environment:
      FLASK_ENV: development
      DATABASE_URL: mysql+pymysql://integration:{self.db_password}@db:3306/phase1b
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
      DATABASE_URL: mysql+pymysql://integration:{self.db_password}@db:3306/phase1b
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
      MYSQL_DATABASE: phase1b
      MYSQL_USER: integration
      MYSQL_PASSWORD: {self.db_password}
  keycloak:
    ports: !reset []
    environment:
      KC_HOSTNAME: https://identity.example.test
      KC_HOSTNAME_BACKCHANNEL_DYNAMIC: "true"
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

    def run(self, *args: str, input_text: str | None = None, timeout: int = 180):
        return subprocess.run(
            [*self.prefix, *args],
            cwd=ROOT,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def run_or_fail(self, *args: str, timeout: int = 180):
        result = self.run(*args, timeout=timeout)
        if result.returncode:
            pytest.fail(f"isolated Compose command failed with exit code {result.returncode}")
        return result

    def run_python(self, source: str, timeout: int = 120) -> dict[str, object]:
        result = self.run("exec", "-T", "api", "python", "-", input_text=source, timeout=timeout)
        if result.returncode:
            safe_stderr = result.stderr
            for secret in (self.db_password, self.root_password):
                safe_stderr = safe_stderr.replace(secret, "[redacted]")
            pytest.fail(
                f"isolated API scenario failed with exit code {result.returncode}: "
                f"{safe_stderr[-3000:]}"
            )
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            pytest.fail("isolated API scenario did not return its sanitized JSON result")


@pytest.fixture(scope="module")
def isolated_project(tmp_path_factory):
    project_name = f"python-demo-keycloak-image-{uuid.uuid4().hex[:12]}"
    build = subprocess.run(
        ["docker", "build", "--target", "api-app", "--tag", project_name, "--file", "Dockerfile", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if build.returncode:
        pytest.fail(f"isolated API image build failed with exit code {build.returncode}")

    project = IsolatedKeycloakProject(tmp_path_factory.mktemp("keycloak-project"), project_name)
    try:
        project.run_or_fail("up", "--detach", "--no-build", "api", "keycloak", timeout=360)
        ready = project.run_python(
            """import json
import time
import requests

deadline = time.monotonic() + 180
api_ready = False
keycloak_ready = False
while time.monotonic() < deadline and not (api_ready and keycloak_ready):
    try:
        api_ready = requests.get('http://127.0.0.1:3000/liveness', timeout=3).status_code == 200
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
        if not ready.get("api_ready") or not ready.get("keycloak_ready"):
            pytest.fail("isolated API and Keycloak did not become ready within 180 seconds")
        yield project
    finally:
        project.run("down", "--volumes", "--remove-orphans", timeout=180)
        subprocess.run(
            ["docker", "image", "rm", "--force", project_name],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def test_real_keycloak_access_token_obeys_phase_1b_policy(isolated_project):
    # Arrange
    scenario = """import base64
import json
import uuid
import requests

internal = 'http://keycloak:8080'
issuer = 'https://identity.example.test/realms/python-demo'
api = 'http://127.0.0.1:3000'
password = 'integration-only-password'

def token_claims(token):
    segment = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))

root_token_response = requests.post(
    f'{internal}/realms/master/protocol/openid-connect/token',
    data={'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': 'admin!123'},
    timeout=15,
)
assert root_token_response.status_code == 200
root_headers = {'Authorization': f"Bearer {root_token_response.json()['access_token']}"}
role_response = requests.get(f'{internal}/admin/realms/python-demo/roles/author', headers=root_headers, timeout=10)
assert role_response.status_code == 200
author_role = role_response.json()

def create_user(label, roles):
    username = f'phase1b-{label}-{uuid.uuid4().hex[:8]}'
    create_response = requests.post(
        f'{internal}/admin/realms/python-demo/users',
        headers=root_headers,
        json={
            'username': username,
            'firstName': 'Integration',
            'lastName': label.title(),
            'email': f'{username}@example.test',
            'enabled': True,
            'emailVerified': True,
            'requiredActions': [],
            'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
        },
        timeout=10,
    )
    assert create_response.status_code == 201
    lookup_response = requests.get(
        f'{internal}/admin/realms/python-demo/users',
        headers=root_headers,
        params={'username': username, 'exact': 'true'},
        timeout=10,
    )
    assert lookup_response.status_code == 200 and len(lookup_response.json()) == 1
    user_id = lookup_response.json()[0]['id']
    if roles:
        representations = []
        for role in roles:
            response = requests.get(f'{internal}/admin/realms/python-demo/roles/{role}', headers=root_headers, timeout=10)
            assert response.status_code == 200
            representations.append(response.json())
        mapping = requests.post(
            f'{internal}/admin/realms/python-demo/users/{user_id}/role-mappings/realm',
            headers=root_headers,
            json=representations,
            timeout=10,
        )
        assert mapping.status_code == 204
    return username

def login(username):
    response = requests.post(f'{api}/login', json={'username': username, 'password': password}, timeout=20)
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()['access_token']

admin_name = create_user('admin', ['admin', 'author'])
first_author_name = create_user('author-one', ['author'])
second_author_name = create_user('author-two', ['author'])
unlinked_name = create_user('unlinked', ['author'])

direct_response = requests.post(
    f'{internal}/realms/python-demo/protocol/openid-connect/token',
    data={
        'grant_type': 'password',
        'client_id': 'python-demo-api',
        'client_secret': 'python-demo-api-secret',
        'username': admin_name,
        'password': password,
    },
    timeout=15,
)
direct_claims = token_claims(direct_response.json()['access_token']) if direct_response.status_code == 200 else {}
admin_status, admin_token = login(admin_name)
first_status, first_token = login(first_author_name)
second_status, second_token = login(second_author_name)
unlinked_status, unlinked_token = login(unlinked_name)
summary = {
    'keycloak_token_status': direct_response.status_code,
    'issuer_matches': direct_claims.get('iss') == issuer,
    'api_audience_present': 'python-demo-api' in (direct_claims.get('aud', []) if isinstance(direct_claims.get('aud'), list) else [direct_claims.get('aud')]),
    'admin_login_status': admin_status,
    'first_author_login_status': first_status,
    'second_author_login_status': second_status,
    'unlinked_author_login_status': unlinked_status,
}
if not all((admin_token, first_token, second_token, unlinked_token)):
    print(json.dumps(summary))
    raise SystemExit(0)

admin_headers = {'Authorization': f'Bearer {admin_token}'}
first_headers = {'Authorization': f'Bearer {first_token}'}
second_headers = {'Authorization': f'Bearer {second_token}'}
unlinked_headers = {'Authorization': f'Bearer {unlinked_token}'}
first_subject = token_claims(first_token)['sub']
second_subject = token_claims(second_token)['sub']
unlinked_subject = token_claims(unlinked_token)['sub']
authors_response = requests.get(f'{api}/authors', timeout=10)
assert authors_response.status_code == 200
first_author_id = authors_response.json()[0]['id']
new_author = requests.post(
    f'{api}/authors',
    headers=admin_headers,
    json={'author': {
        'name': 'Phase 1B integration author',
        'birthdate': '1990-01-01',
        'photo_url': 'https://example.test/integration.png',
        'public_key': 'integration-test-key',
        'bio': 'Temporary integration author',
    }},
    timeout=10,
)
assert new_author.status_code == 201
second_author_id = new_author.json()['id']
first_link = requests.put(
    f'{api}/authors/{first_author_id}/identity',
    headers=admin_headers,
    json={'identity': {'issuer': issuer, 'sub': first_subject}},
    timeout=10,
)
second_link = requests.put(
    f'{api}/authors/{second_author_id}/identity',
    headers=admin_headers,
    json={'identity': {'issuer': issuer, 'sub': second_subject}},
    timeout=10,
)
assert first_link.status_code == 200 and second_link.status_code == 200

anonymous_articles = requests.get(f'{api}/articles', timeout=10)
admin_article = requests.post(
    f'{api}/articles',
    headers=admin_headers,
    json={'article': {
        'title': 'Admin integration article',
        'slug': f'phase1b-admin-{uuid.uuid4().hex[:8]}',
        'published_label': 'Today',
        'post_entry': 'Admin content',
        'tags': [],
        'author_id': first_author_id,
    }},
    timeout=10,
)
first_article = requests.post(
    f'{api}/articles',
    headers=first_headers,
    json={'article': {
        'title': 'First author article',
        'slug': f'phase1b-first-{uuid.uuid4().hex[:8]}',
        'published_label': 'Today',
        'post_entry': 'First author content',
        'tags': [],
    }},
    timeout=10,
)
second_article = requests.post(
    f'{api}/articles',
    headers=second_headers,
    json={'article': {
        'title': 'Second author article',
        'slug': f'phase1b-second-{uuid.uuid4().hex[:8]}',
        'published_label': 'Today',
        'post_entry': 'Second author content',
        'tags': [],
    }},
    timeout=10,
)
first_id = first_article.json().get('id') if first_article.status_code == 201 else -1
second_id = second_article.json().get('id') if second_article.status_code == 201 else -1
first_update = requests.patch(
    f'{api}/articles/{first_id}',
    headers=first_headers,
    json={'article': {'title': 'First author updated'}},
    timeout=10,
)
first_delete = requests.delete(
    f'{api}/articles/{first_id}', headers=first_headers, timeout=10,
)
cross_owner_update = requests.patch(
    f'{api}/articles/{second_id}',
    headers=first_headers,
    json={'article': {'title': 'Unauthorized update'}},
    timeout=10,
)
cross_owner_delete = requests.delete(f'{api}/articles/{second_id}', headers=first_headers, timeout=10)
spoofed_owner = requests.post(
    f'{api}/articles',
    headers=first_headers,
    json={'article': {
        'title': 'Spoofed author',
        'slug': f'phase1b-spoof-{uuid.uuid4().hex[:8]}',
        'published_label': 'Today',
        'post_entry': 'Spoofed content',
        'tags': [],
        'author_id': second_author_id,
    }},
    timeout=10,
)
unlinked_write = requests.post(
    f'{api}/articles',
    headers=unlinked_headers,
    json={'article': {
        'title': 'Unlinked author article',
        'slug': f'phase1b-unlinked-{uuid.uuid4().hex[:8]}',
        'published_label': 'Today',
        'post_entry': 'Unlinked content',
        'tags': [],
    }},
    timeout=10,
)
summary.update({
    'public_read_status': anonymous_articles.status_code,
    'admin_write_status': admin_article.status_code,
    'first_author_create_status': first_article.status_code,
    'first_author_id_assigned': first_article.json().get('author_id') == first_author_id if first_article.status_code == 201 else False,
    'second_author_create_status': second_article.status_code,
    'second_author_id_assigned': second_article.json().get('author_id') == second_author_id if second_article.status_code == 201 else False,
    'self_update_status': first_update.status_code,
    'self_delete_status': first_delete.status_code,
    'cross_owner_update_status': cross_owner_update.status_code,
    'cross_owner_delete_status': cross_owner_delete.status_code,
    'supplied_author_id_status': spoofed_owner.status_code,
    'unlinked_author_status': unlinked_write.status_code,
    'unlinked_sub_is_distinct': unlinked_subject not in {first_subject, second_subject},
})
print(json.dumps(summary))
"""

    # Act
    observed = isolated_project.run_python(scenario)

    # Assert
    assert observed["keycloak_token_status"] == 200
    assert observed["issuer_matches"] is True
    assert observed["api_audience_present"] is True
    assert observed["admin_login_status"] == 200
    assert observed["first_author_login_status"] == 200
    assert observed["second_author_login_status"] == 200
    assert observed["unlinked_author_login_status"] == 200
    assert observed["public_read_status"] == 200
    assert observed["admin_write_status"] == 201
    assert observed["first_author_create_status"] == 201
    assert observed["first_author_id_assigned"] is True
    assert observed["second_author_create_status"] == 201
    assert observed["second_author_id_assigned"] is True
    assert observed["self_update_status"] == 200
    assert observed["self_delete_status"] == 204
    assert observed["cross_owner_update_status"] == 403
    assert observed["cross_owner_delete_status"] == 403
    assert observed["supplied_author_id_status"] == 422
    assert observed["unlinked_author_status"] == 403
    assert observed["unlinked_sub_is_distinct"] is True


def test_real_keycloak_token_without_api_audience_is_rejected(isolated_project):
    # Arrange
    scenario = """import base64
import json
import uuid
import requests

internal = 'http://keycloak:8080'
issuer = 'https://identity.example.test/realms/python-demo'
api = 'http://127.0.0.1:3000'
password = 'integration-only-password'
root_response = requests.post(
    f'{internal}/realms/master/protocol/openid-connect/token',
    data={'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': 'admin!123'},
    timeout=15,
)
assert root_response.status_code == 200
root_headers = {'Authorization': f"Bearer {root_response.json()['access_token']}"}
suffix = uuid.uuid4().hex[:8]
username = f'phase1b-wrong-audience-{suffix}'
create_user = requests.post(
    f'{internal}/admin/realms/python-demo/users',
    headers=root_headers,
    json={
        'username': username,
        'firstName': 'Integration',
        'lastName': 'Wrong Audience',
        'email': f'{username}@example.test',
        'enabled': True,
        'emailVerified': True,
        'requiredActions': [],
        'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
    },
    timeout=10,
)
assert create_user.status_code == 201
client_id = f'phase1b-other-{suffix}'
create_client = requests.post(
    f'{internal}/admin/realms/python-demo/clients',
    headers=root_headers,
    json={
        'clientId': client_id,
        'enabled': True,
        'publicClient': True,
        'protocol': 'openid-connect',
        'standardFlowEnabled': False,
        'directAccessGrantsEnabled': True,
        'serviceAccountsEnabled': False,
    },
    timeout=10,
)
assert create_client.status_code == 201
token_response = requests.post(
    f'{internal}/realms/python-demo/protocol/openid-connect/token',
    data={'grant_type': 'password', 'client_id': client_id, 'username': username, 'password': password},
    timeout=15,
)
claims = {}
if token_response.status_code == 200:
    segment = token_response.json()['access_token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
api_response = requests.get(
    f'{api}/admin/profile',
    headers={'Authorization': f"Bearer {token_response.json()['access_token']}"} if token_response.status_code == 200 else {},
    timeout=10,
)
audiences = claims.get('aud', [])
if not isinstance(audiences, list):
    audiences = [audiences]
print(json.dumps({
    'token_status': token_response.status_code,
    'issuer_matches': claims.get('iss') == issuer,
    'api_audience_present': 'python-demo-api' in audiences,
    'protected_api_status': api_response.status_code,
}))
"""

    # Act
    observed = isolated_project.run_python(scenario)

    # Assert
    assert observed["token_status"] == 200
    assert observed["issuer_matches"] is True
    assert observed["api_audience_present"] is False
    assert observed["protected_api_status"] == 401


def test_mysql_identity_migration_preserves_legacy_rows_and_binary_constraints(isolated_project):
    # Arrange
    result = isolated_project.run_python(
        """import json
import os
import uuid

database = 'legacy_' + uuid.uuid4().hex[:10]
os.environ['PHASE1B_LEGACY_DATABASE'] = database
print(json.dumps({'database': database}))
"""
    )
    database = result["database"]
    create_database = isolated_project.run(
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        f'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root -e "CREATE DATABASE {database}; GRANT ALL PRIVILEGES ON {database}.* TO \'integration\'@\'%\'"',
    )
    if create_database.returncode:
        pytest.fail("could not create the isolated legacy migration database")

    # Act
    migration_probe = """import json
import os

os.environ['DATABASE_URL'] = 'mysql+pymysql://integration:__DB_PASSWORD__@db:3306/__DATABASE__'
from datetime import date
from flask_migrate import upgrade
from sqlalchemy.exc import IntegrityError
from app import create_app
from extensions import db
from models import Author, AuthorIdentity
from models.author_identity import find_author_id

app = create_app()
with app.app_context():
    upgrade(revision='20251130_0001')
    legacy_result = db.session.execute(
        db.text('''INSERT INTO authors
            (name, birthdate, photo_url, public_key, bio, created_at, updated_at)
            VALUES (:name, :birthdate, :photo_url, :public_key, :bio, NOW(), NOW())'''),
        {
            'name': 'Preserved legacy author',
            'birthdate': date(1990, 1, 1),
            'photo_url': 'https://example.test/legacy.png',
            'public_key': 'legacy-key',
            'bio': 'Existing row before private identity migration',
        },
    )
    legacy_id = legacy_result.lastrowid
    db.session.commit()
    db.session.remove()
    upgrade()
    preserved = db.session.get(Author, legacy_id)
    exact_author = Author(
        name='Binary match author', birthdate=date(1991, 1, 1),
        photo_url='https://example.test/exact.png', public_key='exact-key', bio='Exact match',
    )
    lower_author = Author(
        name='Binary lower author', birthdate=date(1992, 1, 1),
        photo_url='https://example.test/lower.png', public_key='lower-key', bio='Lower match',
    )
    db.session.add_all([exact_author, lower_author])
    db.session.flush()
    issuer = b'https://identity.example.test/realms/python-demo'
    exact_identity = AuthorIdentity(author_id=exact_author.id, issuer=issuer, subject=b'CaseSubject')
    lower_identity = AuthorIdentity(author_id=lower_author.id, issuer=issuer, subject=b'casesubject')
    db.session.add_all([exact_identity, lower_identity])
    db.session.commit()
    exact_lookup = find_author_id(issuer.decode(), 'CaseSubject')
    lower_lookup = find_author_id(issuer.decode(), 'casesubject')
    duplicate_identity = AuthorIdentity(
        author_id=legacy_id, issuer=issuer, subject=b'CaseSubject'
    )
    db.session.add(duplicate_identity)
    try:
        db.session.commit()
        duplicate_rejected = False
    except IntegrityError:
        db.session.rollback()
        duplicate_rejected = True
    db.session.delete(exact_author)
    db.session.commit()
    cascaded = db.session.query(AuthorIdentity).filter_by(author_id=exact_author.id).count() == 0
    print(json.dumps({
        'legacy_author_preserved': preserved.name == 'Preserved legacy author',
        'exact_case_lookup': exact_lookup == exact_author.id,
        'lower_case_lookup': lower_lookup == lower_author.id,
        'duplicate_identity_rejected': duplicate_rejected,
        'orm_delete_cascaded': cascaded,
    }))
"""
    migration_probe = migration_probe.replace("__DB_PASSWORD__", isolated_project.db_password)
    migration_probe = migration_probe.replace("__DATABASE__", database)
    probe = isolated_project.run_python(migration_probe)

    # Assert
    assert probe == {
        "legacy_author_preserved": True,
        "exact_case_lookup": True,
        "lower_case_lookup": True,
        "duplicate_identity_rejected": True,
        "orm_delete_cascaded": True,
    }
