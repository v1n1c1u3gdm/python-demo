"""Contracts and opt-in persistence proof for legacy services."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from shutil import copyfile

import yaml

from ci.stack_config import (
    ConfigurationError,
    validate_legacy_compose_contract,
    validate_legacy_runtime_configuration,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose" / "legacy.yaml"


def _legacy_fixture_process_environment(
    fixture_values: dict[str, str], ambient: dict[str, str] | None = None
) -> dict[str, str]:
    """Keep Compose interpolation fixture-owned while retaining local Docker CLI basics."""
    source = os.environ if ambient is None else ambient
    allowed = ("PATH", "HOME", "DOCKER_CONFIG", "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "TMPDIR")
    process_environment = {name: source[name] for name in allowed if name in source}
    process_environment.update(fixture_values)
    return process_environment


class TestLegacyComposeContract(unittest.TestCase):
    def test_legacy_compose_requires_explicit_app_key_and_separates_persistent_data(self) -> None:
        # Arrange
        self.assertTrue(COMPOSE_FILE.is_file(), "the legacy Compose definition must exist")
        configuration = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        services = configuration["services"]

        # Act
        bookstack = services["legacy-bookstack"]
        mariadb = services["legacy-mariadb"]
        gitea = services["legacy-gitea"]
        share = services["legacy-share"]

        # Assert
        self.assertEqual(bookstack["environment"]["FILE__APP_KEY"], "/run/secrets/bookstack_app_key")
        self.assertIn("legacy-mariadb", bookstack["depends_on"])
        self.assertIn("legacy_bookstack_config", [volume["source"] for volume in bookstack["volumes"]])
        self.assertIn("legacy_mariadb_data", [volume["source"] for volume in mariadb["volumes"]])
        self.assertEqual(gitea["environment"]["GITEA__database__DB_TYPE"], "sqlite3")
        self.assertIn("/data", [volume["target"] for volume in gitea["volumes"]])
        self.assertTrue(any(volume["read_only"] for volume in share["volumes"]))
        self.assertFalse(any(service.get("ports") for service in services.values()))

    def test_legacy_compose_closes_share_and_keeps_all_data_out_of_build_context(self) -> None:
        # Arrange
        self.assertTrue(COMPOSE_FILE.is_file(), "the legacy Compose definition must exist")
        configuration = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        services = configuration["services"]
        share_config = (REPOSITORY_ROOT / "infra" / "share" / "default.conf").read_text(
            encoding="utf-8"
        )

        # Act
        host_mounts = [
            volume["source"]
            for service in services.values()
            for volume in service.get("volumes", [])
            if volume.get("type") == "bind"
        ]

        # Assert
        self.assertIn("autoindex off", share_config)
        self.assertIn("deny all", share_config)
        self.assertTrue(any(source.startswith("${SHARE_DATA_DIRECTORY:") for source in host_mounts))
        self.assertNotIn("/var/www", "\n".join(host_mounts))


class TestLegacyRuntimeConfiguration(unittest.TestCase):
    def test_rejects_missing_app_key_without_revealing_database_password(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-preflight-") as directory:
            # Arrange
            root = Path(directory)
            secrets = root / "infra" / "secrets"
            secrets.mkdir(parents=True)
            environment = {
                "STACK_PROJECT": "legacy-test",
                "LEGACY_WEB_NETWORK": "legacy-test-web",
                "LEGACY_DATA_NETWORK": "legacy-test-data",
                "PUBLIC_HOST": "git.example.test",
                "BOOKSTACK_URL": "https://docs.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://docs.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(secrets / "missing-key"),
                "BOOKSTACK_DB_PASSWORD_FILE": str(secrets / "bookstack-db"),
                "MARIADB_ROOT_PASSWORD_FILE": str(secrets / "mariadb-root"),
                "SHARE_DATA_DIRECTORY": str(root.parent),
            }
            (secrets / "bookstack-db").write_text("synthetic-db-password\n", encoding="utf-8")
            (secrets / "mariadb-root").write_text("synthetic-root-password\n", encoding="utf-8")

            # Act / Assert
            with self.assertRaises(ConfigurationError) as raised:
                validate_legacy_runtime_configuration(environment, repository_root=root)

            self.assertIn("BOOKSTACK_APP_KEY_FILE", str(raised.exception))
            self.assertNotIn("synthetic-db-password", str(raised.exception))

    def test_rejects_trailing_newline_in_shared_bookstack_database_secret(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-secret-bytes-") as directory:
            # Arrange
            root = Path(directory)
            secrets = root / "infra" / "secrets"
            secrets.mkdir(parents=True)
            app_key = secrets / "bookstack-key"
            app_key.write_text("base64:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=", encoding="utf-8")
            db_password = secrets / "bookstack-db"
            db_password.write_text("synthetic-password\n", encoding="utf-8")
            root_password = secrets / "mariadb-root"
            root_password.write_text("synthetic-root-password\n", encoding="utf-8")
            share_directory = root.parent
            environment = {
                "STACK_PROJECT": "legacy-test",
                "LEGACY_WEB_NETWORK": "legacy-test-web",
                "LEGACY_DATA_NETWORK": "legacy-test-data",
                "PUBLIC_HOST": "git.example.test",
                "BOOKSTACK_URL": "https://docs.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://docs.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(app_key),
                "BOOKSTACK_DB_PASSWORD_FILE": str(db_password),
                "MARIADB_ROOT_PASSWORD_FILE": str(root_password),
                "SHARE_DATA_DIRECTORY": str(share_directory),
            }

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "BOOKSTACK_DB_PASSWORD_FILE must be byte-exact"):
                validate_legacy_runtime_configuration(environment, repository_root=root)

    def test_legacy_up_preflight_blocks_compose_up_when_bookstack_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-up-preflight-") as directory:
            # Arrange
            temporary_root = Path(directory)
            secrets = temporary_root / "secrets"
            secrets.mkdir()
            db_password = secrets / "bookstack-db"
            root_password = secrets / "mariadb-root"
            db_password.write_text("synthetic-db-password", encoding="utf-8")
            root_password.write_text("synthetic-root-password\n", encoding="utf-8")
            share_directory = temporary_root / "share"
            share_directory.mkdir()
            environment = {
                "STACK_PROJECT": "legacy-preflight-test",
                "LEGACY_WEB_NETWORK": "legacy-preflight-test-web",
                "LEGACY_DATA_NETWORK": "legacy-preflight-test-data",
                "PUBLIC_HOST": "git.example.test",
                "BOOKSTACK_URL": "https://docs.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://docs.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(secrets / "missing-key"),
                "BOOKSTACK_DB_PASSWORD_FILE": str(db_password),
                "MARIADB_ROOT_PASSWORD_FILE": str(root_password),
                "SHARE_DATA_DIRECTORY": str(share_directory),
            }
            compose_environment = temporary_root / "compose-environment"
            compose_environment.write_text(
                "".join(f"{key}={value}\n" for key, value in environment.items()), encoding="utf-8"
            )
            env_file = temporary_root / "legacy.env"
            env_file.write_text("placeholder\n", encoding="utf-8")
            compose_calls = temporary_root / "compose-calls"
            docker = temporary_root / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$COMPOSE_CALLS\"\n"
                "case \" $* \" in\n"
                "  *\" config --environment \"*) cat \"$COMPOSE_ENVIRONMENT\" ;;\n"
                "  *\" up \"*) touch \"$COMPOSE_START_MARKER\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            process_environment = {
                "PATH": f"{temporary_root}:{os.environ.get('PATH', '')}",
                "LEGACY_ENV_FILE": str(env_file),
                "COMPOSE_ENVIRONMENT": str(compose_environment),
                "COMPOSE_CALLS": str(compose_calls),
                "COMPOSE_START_MARKER": str(temporary_root / "compose-started"),
            }

            # Act
            completed = subprocess.run(
                ["bash", str(REPOSITORY_ROOT / "infra" / "scripts" / "up-legacy.sh")],
                cwd=REPOSITORY_ROOT,
                env=process_environment,
                capture_output=True,
                text=True,
                check=False,
            )

            # Assert
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("BOOKSTACK_APP_KEY_FILE is unavailable", completed.stderr)
            self.assertIn("config --environment", compose_calls.read_text(encoding="utf-8"))
            self.assertNotIn("config --format json", compose_calls.read_text(encoding="utf-8"))
            self.assertFalse((temporary_root / "compose-started").exists())

    def test_rejects_share_directory_inside_the_build_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-share-boundary-") as directory:
            # Arrange
            root = Path(directory)
            secrets = root / "infra" / "secrets"
            secrets.mkdir(parents=True)
            key_file = secrets / "bookstack-key"
            key_file.write_text("base64:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=\n")
            db_password = secrets / "bookstack-db"
            db_password.write_text("synthetic-db-password")
            root_password = secrets / "mariadb-root"
            root_password.write_text("synthetic-root-password\n")
            share_directory = root / "share"
            share_directory.mkdir()
            environment = {
                "STACK_PROJECT": "legacy-test",
                "LEGACY_WEB_NETWORK": "legacy-test-web",
                "LEGACY_DATA_NETWORK": "legacy-test-data",
                "PUBLIC_HOST": "git.example.test",
                "BOOKSTACK_URL": "https://docs.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://docs.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(key_file),
                "BOOKSTACK_DB_PASSWORD_FILE": str(db_password),
                "MARIADB_ROOT_PASSWORD_FILE": str(root_password),
                "SHARE_DATA_DIRECTORY": str(share_directory),
            }

            # Act / Assert
            with self.assertRaisesRegex(ConfigurationError, "outside the image build context"):
                validate_legacy_runtime_configuration(environment, repository_root=root)


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is needed to render the legacy file.")
class TestRealLegacyComposeFile(unittest.TestCase):
    def test_legacy_compose_renders_with_explicit_secrets_and_closed_share(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-render-") as directory:
            # Arrange
            temporary_root = Path(directory)
            secrets = temporary_root / "secrets"
            secrets.mkdir()
            app_key = secrets / "bookstack-key"
            db_password = secrets / "bookstack-db"
            root_password = secrets / "mariadb-root"
            app_key.write_text("base64:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=\n")
            db_password.write_text("synthetic-db-password")
            root_password.write_text("synthetic-root-password\n")
            share_directory = temporary_root / "share"
            share_directory.mkdir()
            environment = {
                "STACK_PROJECT": "legacy-render-test",
                "LEGACY_WEB_NETWORK": "legacy-render-test-web",
                "LEGACY_DATA_NETWORK": "legacy-render-test-data",
                "PUBLIC_HOST": "git.example.test",
                "BOOKSTACK_URL": "https://docs.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://docs.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(app_key),
                "BOOKSTACK_DB_PASSWORD_FILE": str(db_password),
                "MARIADB_ROOT_PASSWORD_FILE": str(root_password),
                "SHARE_DATA_DIRECTORY": str(share_directory),
            }
            env_file = temporary_root / "legacy.env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
            hostile_share_directory = temporary_root / "ambient-share"
            hostile_share_directory.mkdir()
            hostile_secret_directory = temporary_root / "ambient-secrets"
            hostile_secret_directory.mkdir()
            hostile_environment = {
                **os.environ,
                "STACK_PROJECT": "ambient-override-project",
                "LEGACY_WEB_NETWORK": "ambient-override-web",
                "LEGACY_DATA_NETWORK": "ambient-override-data",
                "PUBLIC_HOST": "ambient.example.test",
                "BOOKSTACK_URL": "https://ambient.example.test/bookstack/",
                "GITEA_ROOT_URL": "https://ambient.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(hostile_secret_directory / "ambient-key"),
                "BOOKSTACK_DB_PASSWORD_FILE": str(hostile_secret_directory / "ambient-bookstack-db"),
                "MARIADB_ROOT_PASSWORD_FILE": str(hostile_secret_directory / "ambient-mariadb-root"),
                "SHARE_DATA_DIRECTORY": str(hostile_share_directory),
            }

            # Act
            validate_legacy_runtime_configuration(environment, repository_root=REPOSITORY_ROOT)
            rendered = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    environment["STACK_PROJECT"],
                    "--project-directory",
                    str(REPOSITORY_ROOT),
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(COMPOSE_FILE),
                    "config",
                    "--format",
                    "json",
                ],
                cwd=REPOSITORY_ROOT,
                env=_legacy_fixture_process_environment(environment, hostile_environment),
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            configuration = json.loads(rendered.stdout)
            validate_legacy_compose_contract(configuration)

            # Assert
            self.assertTrue(configuration["networks"]["legacy_data"]["internal"])
            self.assertEqual(
                configuration["services"]["legacy-gitea"]["volumes"][0]["target"], "/data"
            )
            share_volume = configuration["services"]["legacy-share"]["volumes"][1]
            self.assertTrue(share_volume["read_only"])
            self.assertEqual(share_volume["source"], str(share_directory))
            self.assertEqual(configuration["name"], environment["STACK_PROJECT"])
            self.assertEqual(
                configuration["volumes"]["legacy_mariadb_data"]["name"],
                f"{environment['STACK_PROJECT']}_mariadb",
            )
            self.assertEqual(
                configuration["secrets"]["bookstack_app_key"]["file"], str(app_key)
            )
            self.assertEqual(
                configuration["secrets"]["bookstack_db_password"]["file"], str(db_password)
            )
            self.assertEqual(
                configuration["secrets"]["mariadb_root_password"]["file"], str(root_password)
            )
            self.assertEqual(
                configuration["volumes"]["legacy_bookstack_config"]["name"],
                f"{environment['STACK_PROJECT']}_bookstack",
            )
            self.assertEqual(
                configuration["volumes"]["legacy_gitea_data"]["name"],
                f"{environment['STACK_PROJECT']}_gitea",
            )
            self.assertEqual(
                configuration["networks"]["legacy_data"]["name"],
                environment["LEGACY_DATA_NETWORK"],
            )


@unittest.skipUnless(
    os.environ.get("RUN_LEGACY_COMPOSE_PROOF") == "1" and shutil.which("docker"),
    "Set RUN_LEGACY_COMPOSE_PROOF=1 to run isolated legacy persistence proof.",
)
class TestLegacyPersistenceRecreation(unittest.TestCase):
    def test_synthetic_bookstack_gitea_and_share_data_survive_container_recreation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-demo-legacy-persist-") as directory:
            # Arrange
            temporary_root = Path(directory)
            project_name = f"legacy-{temporary_root.name[-12:]}"
            secret_directory = temporary_root / "secrets"
            share_directory = temporary_root / "share"
            secret_directory.mkdir()
            share_directory.mkdir()
            secret_values = {
                "BOOKSTACK_APP_KEY_FILE": (
                    secret_directory / "bookstack-key",
                    "base64:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=",
                ),
                "BOOKSTACK_DB_PASSWORD_FILE": (secret_directory / "bookstack-db", "bookstack-synthetic-db"),
                "MARIADB_ROOT_PASSWORD_FILE": (secret_directory / "mariadb-root", "mariadb-synthetic-root"),
            }
            for path, value in secret_values.values():
                # These files are mounted verbatim into both MariaDB and the BookStack FILE__ handler.
                path.write_text(value, encoding="utf-8")
            app_key_digest = hashlib.sha256(
                secret_values["BOOKSTACK_APP_KEY_FILE"][0].read_bytes()
            ).hexdigest()
            share_fixture = REPOSITORY_ROOT / "infra" / "tests" / "fixtures" / "share-fixture.txt"
            copyfile(share_fixture, share_directory / "fixture.txt")
            env_values = {
                "STACK_PROJECT": project_name,
                "LEGACY_WEB_NETWORK": f"{project_name}-web",
                "LEGACY_DATA_NETWORK": f"{project_name}-data",
                "PUBLIC_HOST": "legacy.example.test",
                "BOOKSTACK_URL": "http://legacy.example.test/bookstack/",
                "GITEA_ROOT_URL": "http://legacy.example.test/git/",
                "BOOKSTACK_APP_KEY_FILE": str(secret_values["BOOKSTACK_APP_KEY_FILE"][0]),
                "BOOKSTACK_DB_PASSWORD_FILE": str(secret_values["BOOKSTACK_DB_PASSWORD_FILE"][0]),
                "MARIADB_ROOT_PASSWORD_FILE": str(secret_values["MARIADB_ROOT_PASSWORD_FILE"][0]),
                "SHARE_DATA_DIRECTORY": str(share_directory),
            }
            env_file = temporary_root / "legacy.env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in env_values.items()))
            compose_environment = _legacy_fixture_process_environment(env_values)

            def compose(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-p",
                        project_name,
                        "--project-directory",
                        str(REPOSITORY_ROOT),
                        "--env-file",
                        str(env_file),
                        "-f",
                        str(COMPOSE_FILE),
                        *arguments,
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=compose_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )

            def assert_command_succeeded(result: subprocess.CompletedProcess[str]) -> None:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def effective_app_key_digest() -> str:
                result = compose(
                    "exec", "-T", "legacy-bookstack", "/command/with-contenv", "php",
                    "/app/www/artisan", "tinker", "--execute",
                    'echo "APP_KEY_SHA256=".hash("sha256",config("app.key"));',
                )
                assert_command_succeeded(result)
                marker = "APP_KEY_SHA256="
                self.assertIn(marker, result.stdout)
                return result.stdout.rsplit(marker, 1)[1].strip()

            # Validate the fully rendered fixture before creating any containers, networks or volumes.
            validate_legacy_runtime_configuration(env_values, repository_root=REPOSITORY_ROOT)
            rendered = compose("config", "--format", "json")
            assert_command_succeeded(rendered)
            configuration = json.loads(rendered.stdout)
            validate_legacy_compose_contract(configuration)
            self.assertEqual(configuration["name"], project_name)
            self.assertEqual(
                configuration["volumes"]["legacy_mariadb_data"]["name"],
                f"{project_name}_mariadb",
            )
            self.assertEqual(
                configuration["volumes"]["legacy_bookstack_config"]["name"],
                f"{project_name}_bookstack",
            )
            self.assertEqual(
                configuration["volumes"]["legacy_gitea_data"]["name"],
                f"{project_name}_gitea",
            )
            for secret_name, environment_name in (
                ("bookstack_app_key", "BOOKSTACK_APP_KEY_FILE"),
                ("bookstack_db_password", "BOOKSTACK_DB_PASSWORD_FILE"),
                ("mariadb_root_password", "MARIADB_ROOT_PASSWORD_FILE"),
            ):
                self.assertEqual(
                    configuration["secrets"][secret_name]["file"], env_values[environment_name]
                )
            share_volume = configuration["services"]["legacy-share"]["volumes"][1]
            self.assertEqual(share_volume["source"], str(share_directory))

            try:
                # Act: create fresh synthetic databases and persistent BookStack/Gitea/share records.
                started = compose("up", "--detach", "--wait")
                assert_command_succeeded(started)
                gitea_user = compose(
                    "exec", "-T", "--user", "git", "legacy-gitea", "gitea", "admin", "user", "create",
                    "--username", "synthetic-fixture-admin", "--password", "synthetic-fixture-password",
                    "--email", "fixture@example.test", "--admin",
                )
                assert_command_succeeded(gitea_user)
                gitea_created = compose(
                    "exec", "-T", "legacy-bookstack", "php", "-r",
                    '$context=stream_context_create(["http"=>["method"=>"POST",'
                    '"header"=>"Authorization: Basic ".base64_encode("synthetic-fixture-admin:synthetic-fixture-password")'
                    '."\\r\\nContent-Type: application/json\\r\\n",'
                    '"content"=>json_encode(["name"=>"synthetic-persistent-repository","auto_init"=>true]),'
                    '"ignore_errors"=>true]]);'
                    '$body=file_get_contents("http://legacy-gitea:3000/api/v1/user/repos",false,$context);'
                    'if ($body===false || ($http_response_header[0]??"")!=="HTTP/1.1 201 Created") '
                    '{fwrite(STDERR,(string)$body." ".($http_response_header[0]??"no response"));exit(1);} echo $body;',
                )
                assert_command_succeeded(gitea_created)
                self.assertIn("synthetic-fixture-admin/synthetic-persistent-repository", gitea_created.stdout)

                token_provision = compose(
                    "exec", "-T", "legacy-bookstack", "/command/with-contenv", "php",
                    "/app/www/artisan", "tinker", "--execute",
                    '$user=\\BookStack\\Users\\Models\\User::query()->where("email","admin@admin.com")->first();'
                    '$token=new \\BookStack\\Api\\ApiToken();'
                    '$token->forceFill(["name"=>"synthetic persistence fixture",'
                    '"token_id"=>"legacyfixturetokenid0123456789012345",'
                    '"secret"=>\\Illuminate\\Support\\Facades\\Hash::make("legacyfixturesyntheticsecret012345678901"),'
                    '"user_id"=>$user->id,"expires_at"=>\\Illuminate\\Support\\Carbon::now()->addDay()->format("Y-m-d")])->save();'
                    '$token->refresh();echo "fixture token provisioned ".$token->token_id." user=".$token->user_id;',
                )
                assert_command_succeeded(token_provision)

                bookstack_created = compose(
                    "exec", "-T", "legacy-bookstack", "php", "-r",
                    '$headers="Authorization: Token legacyfixturetokenid0123456789012345:legacyfixturesyntheticsecret012345678901\\r\\n";'
                    '$headers.="Content-Type: application/json\\r\\n";'
                    '$context=stream_context_create(["http"=>["method"=>"POST","header"=>$headers,'
                    '"content"=>json_encode(["name"=>"synthetic-persistence-book"]),"ignore_errors"=>true]]);'
                    '$bookBody=file_get_contents("http://127.0.0.1/api/books",false,$context);$book=json_decode($bookBody,true);'
                    'if (!isset($book["id"])) {fwrite(STDERR,(string)($http_response_header[0]??"no response")." ".$bookBody);exit(1);}'
                    '$context=stream_context_create(["http"=>["method"=>"POST","header"=>$headers,'
                    '"content"=>json_encode(["book_id"=>$book["id"],"name"=>"synthetic-persistence-page",'
                    '"markdown"=>"Synthetic page content that must survive recreation." ]),"ignore_errors"=>true]]);'
                    '$page=json_decode(file_get_contents("http://127.0.0.1/api/pages",false,$context),true);'
                    'if (!isset($page["id"])) {fwrite(STDERR,(string)($http_response_header[0]??"no response"));exit(1);}'
                    '$boundary="legacyfixtureboundary012345";'
                    '$body="--$boundary\\r\\nContent-Disposition: form-data; name=\\"uploaded_to\\"\\r\\n\\r\\n".$page["id"]."\\r\\n";'
                    '$body.="--$boundary\\r\\nContent-Disposition: form-data; name=\\"name\\"\\r\\n\\r\\nfixture-upload.txt\\r\\n";'
                    '$body.="--$boundary\\r\\nContent-Disposition: form-data; name=\\"file\\"; filename=\\"fixture-upload.txt\\"\\r\\nContent-Type: text/plain\\r\\n\\r\\nsynthetic-upload-payload\\r\\n--$boundary--\\r\\n";'
                    '$uploadHeaders="Authorization: Token legacyfixturetokenid0123456789012345:legacyfixturesyntheticsecret012345678901\\r\\n".'
                    '"Content-Type: multipart/form-data; boundary=$boundary\\r\\n";'
                    '$context=stream_context_create(["http"=>["method"=>"POST","header"=>$uploadHeaders,"content"=>$body,"ignore_errors"=>true]]);'
                    '$upload=json_decode(file_get_contents("http://127.0.0.1/api/attachments",false,$context),true);'
                    'if (!isset($upload["id"])) {fwrite(STDERR,(string)($http_response_header[0]??"no response"));exit(1);}'
                    'file_put_contents("/config/www/files/fixture-records.json",json_encode(["page"=>$page["id"],"upload"=>$upload["id"]]));'
                    'echo json_encode(["page"=>$page["id"],"upload"=>$upload["id"]]);',
                )
                self.assertEqual(
                    bookstack_created.returncode,
                    0,
                    token_provision.stdout + bookstack_created.stdout + bookstack_created.stderr,
                )
                effective_key_before_recreation = effective_app_key_digest()
                attachment_bytes_before = compose(
                    "exec", "-T", "legacy-bookstack", "sh", "-ec",
                    "grep -R -F -q synthetic-upload-payload /config",
                )
                assert_command_succeeded(attachment_bytes_before)

                # Act: recreate every service container without deleting any named volume.
                removed = compose(
                    "rm", "--stop", "--force", "legacy-bookstack", "legacy-mariadb",
                    "legacy-gitea", "legacy-share",
                )
                assert_command_succeeded(removed)
                recreated = compose("up", "--detach", "--wait")
                assert_command_succeeded(recreated)
                self.assertEqual(effective_app_key_digest(), effective_key_before_recreation)
                bookstack_query = compose(
                    "exec", "-T", "legacy-bookstack", "php", "-r",
                    '$records=json_decode(file_get_contents("/config/www/files/fixture-records.json"),true);'
                    '$headers="Authorization: Token legacyfixturetokenid0123456789012345:legacyfixturesyntheticsecret012345678901\\r\\n";'
                    '$context=stream_context_create(["http"=>["header"=>$headers,"ignore_errors"=>true]]);'
                    '$page=file_get_contents("http://127.0.0.1/api/pages/".$records["page"],false,$context);'
                    '$upload=file_get_contents("http://127.0.0.1/api/attachments/".$records["upload"],false,$context);'
                    'if (strpos((string)$page,"Synthetic page content that must survive recreation.")===false '
                    '|| strpos((string)$upload,"fixture-upload.txt")===false) {fwrite(STDERR,"synthetic BookStack content missing");exit(1);}'
                    'echo "BookStack page and upload persisted";',
                )
                gitea_data = compose(
                    "exec", "-T", "legacy-gitea", "sh", "-ec",
                    "test -f /data/gitea/gitea.db "
                    "&& test -f /data/git/repositories/synthetic-fixture-admin/"
                    "synthetic-persistent-repository.git/HEAD",
                )
                share_data = compose("exec", "-T", "legacy-share", "cat", "/usr/share/nginx/html/fixture.txt")
                gitea_repo_data = compose(
                    "exec", "-T", "legacy-bookstack", "php", "-r",
                    '$context=stream_context_create(["http"=>["header"=>"Authorization: Basic ".base64_encode('
                    '"synthetic-fixture-admin:synthetic-fixture-password"),"ignore_errors"=>true]]);'
                    '$body=file_get_contents("http://legacy-gitea:3000/api/v1/repos/synthetic-fixture-admin/"'
                    '."synthetic-persistent-repository",false,$context);'
                    'if (strpos((string)$body,"synthetic-persistent-repository")===false) {fwrite(STDERR,"repository missing");exit(1);}'
                    'echo "Gitea repository persisted";',
                )

                # Assert
                assert_command_succeeded(bookstack_query)
                assert_command_succeeded(gitea_data)
                assert_command_succeeded(gitea_repo_data)
                assert_command_succeeded(share_data)
                self.assertIn("BookStack page and upload persisted", bookstack_query.stdout)
                self.assertIn("Gitea repository persisted", gitea_repo_data.stdout)
                self.assertIn("synthetic share fixture", share_data.stdout)
                attachment_bytes_after = compose(
                    "exec", "-T", "legacy-bookstack", "sh", "-ec",
                    "grep -R -F -q synthetic-upload-payload /config",
                )
                assert_command_succeeded(attachment_bytes_after)
                self.assertEqual(
                    app_key_digest,
                    hashlib.sha256(secret_values["BOOKSTACK_APP_KEY_FILE"][0].read_bytes()).hexdigest(),
                )

                # Act: verify a database outage and recovery without touching the named volumes.
                stopped_database = compose("stop", "legacy-mariadb")
                assert_command_succeeded(stopped_database)
                effective_key_during_outage = effective_app_key_digest()
                self.assertEqual(effective_key_during_outage, effective_key_before_recreation)
                page_id = str(json.loads(bookstack_created.stdout)["page"])
                failed_page_request = compose(
                    "exec", "-T", "legacy-bookstack", "/command/with-contenv", "php", "-r",
                    '$headers="Authorization: Token legacyfixturetokenid0123456789012345:legacyfixturesyntheticsecret012345678901\\r\\n";'
                    '$context=stream_context_create(["http"=>["header"=>$headers,"ignore_errors"=>true]]);'
                    '$page=file_get_contents("http://127.0.0.1/api/pages/".$argv[1],false,$context);'
                    'if (($http_response_header[0]??"")==="HTTP/1.1 200 OK" '
                    '|| strpos((string)$page,"Synthetic page content that must survive recreation.")!==false) '
                    '{fwrite(STDERR,(string)($http_response_header[0]??"no response"));exit(1);} echo "database failure observed";',
                    page_id,
                )
                assert_command_succeeded(failed_page_request)
                self.assertIn("database failure observed", failed_page_request.stdout)
                recovered_stack = compose("up", "--detach", "--wait")
                assert_command_succeeded(recovered_stack)
                recovered_page = None
                for _ in range(15):
                    recovered_page = compose(
                        "exec", "-T", "legacy-bookstack", "/command/with-contenv", "php", "-r",
                        '$headers="Authorization: Token legacyfixturetokenid0123456789012345:legacyfixturesyntheticsecret012345678901\\r\\n";'
                        '$context=stream_context_create(["http"=>["header"=>$headers,"ignore_errors"=>true]]);'
                        '$page=file_get_contents("http://127.0.0.1/api/pages/".$argv[1],false,$context);'
                        'if (strpos((string)$page,"Synthetic page content that must survive recreation.")===false) '
                        '{fwrite(STDERR,(string)($http_response_header[0]??"no response")." ".(string)$page);exit(1);} '
                        'echo "database recovery verified";',
                        page_id,
                    )
                    if recovered_page.returncode == 0 and "database recovery verified" in recovered_page.stdout:
                        break
                    time.sleep(2)
                self.assertIsNotNone(recovered_page)
                assert_command_succeeded(recovered_page)
                self.assertIn("database recovery verified", recovered_page.stdout)
                self.assertEqual(effective_app_key_digest(), effective_key_before_recreation)
                self.assertEqual(
                    app_key_digest,
                    hashlib.sha256(secret_values["BOOKSTACK_APP_KEY_FILE"][0].read_bytes()).hexdigest(),
                )
            finally:
                # Remove only the unique project resources created by this test.
                cleanup = compose("down", "--volumes")
                if cleanup.returncode:
                    if sys.exc_info()[0] is None:
                        assert_command_succeeded(cleanup)
                    else:
                        sys.stderr.write(
                            "Isolated legacy fixture cleanup failed: "
                            + cleanup.stdout
                            + cleanup.stderr
                        )
