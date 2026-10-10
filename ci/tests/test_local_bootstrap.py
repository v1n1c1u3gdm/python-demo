"""AAA coverage for the repository-local runtime material bootstrap."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtensionOID

from ci.local_bootstrap import initialize_local_files, main


class LocalFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "checkout" / "infra"
        self.secrets = self.root / "secrets" / "local"
        self.state = self.root / ".local"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish_current_generation_routes(self) -> str:
        generation = (self.state / "bootstrap-generation").read_text(encoding="utf-8").strip()
        ready = self.state / "ready"
        for route in ("bookstack", "gitea", "ci"):
            (ready / route).write_text(generation, encoding="utf-8")
        return generation

    def test_command_line_bootstrap_creates_checkout_local_state(self) -> None:
        # Arrange
        root = Path(self.temporary.name) / "cli"
        secrets = root / "infra/secrets/local"
        state = root / "infra/.local"

        # Act
        argv = ["ci.local_bootstrap", "--secrets-dir", str(secrets), "--state-dir", str(state),
                "--public-host", "app.localhost"]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            main()

        # Assert
        payload = json.loads((state / "bootstrap.json").read_text())
        self.assertEqual(payload["issuer"], "https://app.localhost/auth/realms/python-demo")
        self.assertTrue((secrets / "tls/backchannel.crt").is_file())

    def test_invalid_owner_environment_is_rejected_before_file_creation(self) -> None:
        # Arrange / Act / Assert
        with patch.dict(os.environ, {"LOCAL_OWNER_UID": "-1"}), self.assertRaisesRegex(
                ValueError, "numeric non-negative"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertFalse(self.secrets.exists())

    def test_invalid_owner_environment_invalidates_current_route_markers(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        self._publish_current_generation_routes()

        # Act / Assert
        with patch.dict(os.environ, {"LOCAL_OWNER_UID": "-1"}), self.assertRaisesRegex(
                ValueError, "numeric non-negative"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        for route in ("bookstack", "gitea", "ci"):
            self.assertFalse((self.state / "ready" / route).exists())

    def test_invalid_existing_secret_invalidates_current_route_markers(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        self._publish_current_generation_routes()
        (self.secrets / "api_db_password").write_bytes(b"")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "secret is empty or malformed"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        for route in ("bookstack", "gitea", "ci"):
            self.assertFalse((self.state / "ready" / route).exists())

    def test_invalid_existing_tls_invalidates_current_route_markers(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        self._publish_current_generation_routes()
        (self.secrets / "tls/server.key").write_bytes(b"")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "TLS key is empty or corrupt"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        for route in ("bookstack", "gitea", "ci"):
            self.assertFalse((self.state / "ready" / route).exists())

    def test_invalid_public_host_is_rejected_before_writing_files(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "hostname"):
            initialize_local_files(self.secrets, self.state, "https://app.localhost")
        self.assertFalse(self.secrets.exists())

    def test_first_start_creates_private_valid_files(self) -> None:
        # Arrange
        # Act
        result = initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        self.assertTrue(result["generation"])
        self.assertEqual(result["issuer"], "https://app.localhost/auth/realms/python-demo")
        for path in self.secrets.iterdir():
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        certificate = x509.load_pem_x509_certificate((self.secrets / "tls/server.crt").read_bytes())
        sans = certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        self.assertIn("app.localhost", sans.get_values_for_type(x509.DNSName))
        internal = x509.load_pem_x509_certificate((self.secrets / "tls/backchannel.crt").read_bytes())
        internal_sans = internal.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        self.assertEqual(internal_sans.get_values_for_type(x509.DNSName), ["gateway"])
        for name in (
            "api_db_password", "mysql_root_password", "keycloak_db_password",
            "keycloak_client_secret", "keycloak_admin_password", "bookstack_db_password",
            "mariadb_root_password", "bookstack_app_key", "bookstack_client_secret",
            "gitea_client_secret", "woodpecker_agent_secret",
            "gitea_admin_password",
        ):
            self.assertTrue((self.secrets / name).read_bytes())
        self.assertEqual((self.secrets / "keycloak_admin_password").read_bytes(), b"admin!123")
        self.assertEqual((self.secrets / "gitea_admin_password").read_bytes(), b"admin!123")

    def test_tls_chain_has_issuer_identifiers_for_strict_clients(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        ca = x509.load_pem_x509_certificate((self.secrets / "tls/ca.crt").read_bytes())
        server = x509.load_pem_x509_certificate((self.secrets / "tls/server.crt").read_bytes())

        # Act
        ca_ski = ca.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER).value.digest
        ca_aki = ca.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_KEY_IDENTIFIER).value.key_identifier
        server_aki = server.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_KEY_IDENTIFIER).value.key_identifier
        ca_usage = ca.extensions.get_extension_for_class(x509.KeyUsage).value
        server_usage = server.extensions.get_extension_for_class(x509.KeyUsage).value
        server_purpose = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value

        # Assert
        self.assertEqual(ca_aki, ca_ski)
        self.assertEqual(server_aki, ca_ski)
        self.assertTrue(ca_usage.key_cert_sign)
        self.assertIn(x509.oid.ExtendedKeyUsageOID.SERVER_AUTH, server_purpose)
        self.assertTrue(server_usage.digital_signature)

    def test_repeated_start_preserves_keys_and_secrets(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        key_before = (self.secrets / "tls/server.key").read_bytes()
        secret_before = (self.secrets / "api_db_password").read_bytes()

        # Act
        second = initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        self.assertEqual((self.secrets / "tls/server.key").read_bytes(), key_before)
        self.assertEqual((self.secrets / "api_db_password").read_bytes(), secret_before)
        self.assertTrue(second["generation"])

    def test_corrupt_or_symlinked_secret_is_rejected_without_disclosing_contents(self) -> None:
        # Arrange
        self.secrets.mkdir(parents=True)
        victim = Path(self.temporary.name) / "outside-secret"
        victim.write_text("do-not-leak-THIS", encoding="utf-8")
        (self.secrets / "api_db_password").symlink_to(victim)

        # Act / Assert
        with self.assertRaises(ValueError) as caught:
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertNotIn("do-not-leak-THIS", str(caught.exception))
        self.assertEqual(victim.read_text(encoding="utf-8"), "do-not-leak-THIS")

    def test_new_generation_invalidates_old_ready_markers(self) -> None:
        # Arrange
        result = initialize_local_files(self.secrets, self.state, "app.localhost")
        ready = self.state / "ready"
        ready.mkdir(exist_ok=True)
        (ready / "bookstack").write_text(result["generation"], encoding="utf-8")

        # Act
        initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        current_generation = (self.state / "bootstrap-generation").read_text(encoding="utf-8").strip()
        self.assertNotEqual(current_generation, result["generation"])
        self.assertFalse((ready / "bookstack").exists())
        self.assertFalse((ready / "gitea").exists())
        self.assertFalse((ready / "ci").exists())
        self.assertTrue(json.loads((self.state / "bootstrap.json").read_text())["issuer"])

    def test_consumer_copies_are_scoped_private_and_match_canonical_material(self) -> None:
        # Arrange / Act
        initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        runtime = self.secrets / "runtime"
        self.assertEqual({path.name for path in (runtime / "gateway").iterdir()}, {"tls"})
        gateway_tls = runtime / "gateway" / "tls"
        self.assertEqual({path.name for path in gateway_tls.iterdir()},
                         {"server.crt", "server.key", "backchannel.crt", "backchannel.key"})
        self.assertEqual((gateway_tls / "server.key").read_bytes(), (self.secrets / "tls/server.key").read_bytes())
        self.assertEqual((gateway_tls / "backchannel.crt").read_bytes(),
                         (self.secrets / "tls/backchannel.crt").read_bytes())
        self.assertFalse((gateway_tls / "ca.key").exists())
        self.assertFalse((runtime / "api" / "mysql_root_password").exists())
        self.assertEqual((runtime / "api" / "api_db_password").read_bytes(),
                         (self.secrets / "api_db_password").read_bytes())
        self.assertEqual((runtime / "keycloak" / "keycloak_db_password").stat().st_mode & 0o777, 0o640)
        expected_group = 0 if os.geteuid() == 0 else os.getegid()
        self.assertEqual((runtime / "keycloak" / "keycloak_db_password").stat().st_gid, expected_group)
        public_ca = self.state / "public/ca.crt"
        self.assertEqual(public_ca.read_bytes(), (self.secrets / "tls/ca.crt").read_bytes())
        self.assertEqual(public_ca.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.state / "browser").stat().st_uid, os.getuid())
        woodpecker = runtime / "woodpecker"
        self.assertEqual(woodpecker.stat().st_mode & 0o777, 0o750)
        self.assertEqual((woodpecker / "tls").stat().st_mode & 0o777, 0o750)
        self.assertEqual((woodpecker / "tls/ca.crt").stat().st_mode & 0o777, 0o640)
        if os.geteuid() == 0:
            self.assertEqual(woodpecker.stat().st_gid, 10001)
            self.assertEqual((woodpecker / "tls/ca.crt").stat().st_gid, 10001)

    def test_native_oidc_consumers_receive_only_public_ca_and_oauth_helper_keeps_token(self) -> None:
        # Arrange
        self.secrets.mkdir(parents=True)
        (self.secrets / "gitea_bootstrap_api_token").write_text("persisted-native-token")

        # Act
        initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        expected_ca = (self.secrets / "tls/ca.crt").read_bytes()
        for consumer in ("bookstack", "gitea", "woodpecker"):
            tls = self.secrets / "runtime" / consumer / "tls"
            self.assertEqual({path.name for path in tls.iterdir()}, {"ca.crt"})
            self.assertEqual((tls / "ca.crt").read_bytes(), expected_ca)
        self.assertEqual((self.secrets / "runtime/bookstack/bookstack_db_password").read_bytes(),
                         (self.secrets / "bookstack_db_password").read_bytes())
        woodpecker_agent = self.secrets / "runtime/woodpecker/woodpecker_agent_secret"
        self.assertEqual(woodpecker_agent.stat().st_mode & 0o777, 0o640)
        if os.geteuid() == 0:
            self.assertEqual(woodpecker_agent.stat().st_gid, 10001)
        token_copy = self.secrets / "runtime/gitea-oauth-init/gitea_bootstrap_api_token"
        self.assertEqual(token_copy.read_text(), "persisted-native-token")
        self.assertEqual(token_copy.stat().st_mode & 0o777, 0o600)

    def test_local_init_does_not_invent_one_time_oauth_credentials(self) -> None:
        # Arrange / Act
        initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        for name in ("gitea_bootstrap_api_token", "woodpecker_gitea_client", "woodpecker_gitea_secret"):
            self.assertFalse((self.secrets / name).exists())
        self.assertFalse((self.secrets / "runtime/woodpecker/gitea_client_id").exists())
        self.assertFalse((self.secrets / "runtime/woodpecker/gitea_client_secret").exists())

    def test_local_init_rejects_empty_canonical_oauth_secret(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        self._publish_current_generation_routes()
        (self.secrets / "woodpecker_gitea_secret").write_bytes(b"")
        shutil.rmtree(self.secrets / "runtime")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "canonical local secret is empty or malformed"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        for route in ("bookstack", "gitea", "ci"):
            self.assertFalse((self.state / "ready" / route).exists())

    def test_local_init_refuses_to_prune_a_partial_canonical_oauth_pair(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        (self.secrets / "woodpecker_gitea_client").write_text("stable-id")
        (self.secrets / "woodpecker_gitea_secret").write_text("stable-secret")
        (self.secrets / "woodpecker_gitea_secret").unlink()
        shutil.rmtree(self.secrets / "runtime")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "OAuth credential pair is incomplete"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_local_init_rejects_runtime_token_when_canonical_was_removed(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        (self.secrets / "gitea_bootstrap_api_token").write_text("stable-token")
        initialize_local_files(self.secrets, self.state, "app.localhost")
        (self.secrets / "gitea_bootstrap_api_token").unlink()
        shutil.rmtree(self.secrets / "runtime")
        (self.secrets / "runtime/gitea-oauth-init").mkdir(parents=True)
        (self.secrets / "runtime/gitea-oauth-init/gitea_bootstrap_api_token").write_text("stale-token")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "without canonical source"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_modified_derived_secret_is_rejected(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        derived = self.secrets / "runtime/api/api_db_password"
        derived.write_text("different-secret", encoding="utf-8")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "does not match"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_symlinked_runtime_root_preserves_external_directory(self) -> None:
        # Arrange
        victim = Path(self.temporary.name) / "outside-runtime"
        victim.mkdir()
        (victim / "keep").write_text("untouched", encoding="utf-8")
        self.secrets.mkdir(parents=True)
        (self.secrets / "runtime").symlink_to(victim, target_is_directory=True)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlink"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual((victim / "keep").read_text(encoding="utf-8"), "untouched")
        self.assertEqual(list(victim.iterdir()), [victim / "keep"])
        self.assertFalse((self.secrets / "api_db_password").exists())

    def test_symlinked_consumer_tls_preserves_external_files(self) -> None:
        # Arrange
        victim = Path(self.temporary.name) / "outside-consumer-tls"
        victim.mkdir()
        (victim / "keep").write_text("untouched", encoding="utf-8")
        consumer = self.secrets / "runtime/api"
        consumer.mkdir(parents=True)
        (consumer / "tls").symlink_to(victim, target_is_directory=True)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlink"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual((victim / "keep").read_text(encoding="utf-8"), "untouched")
        self.assertEqual(list(victim.iterdir()), [victim / "keep"])

    def test_symlinked_ready_root_preserves_external_markers(self) -> None:
        # Arrange
        victim = Path(self.temporary.name) / "outside-ready"
        victim.mkdir()
        marker = victim / "bookstack"
        marker.write_text("generation", encoding="utf-8")
        self.state.mkdir(parents=True)
        (self.state / "ready").symlink_to(victim, target_is_directory=True)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlink"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(marker.read_text(encoding="utf-8"), "generation")

    def test_symlinked_input_root_is_rejected_before_external_writes(self) -> None:
        # Arrange
        outside = Path(self.temporary.name) / "outside-checkout"
        outside.mkdir()
        self.secrets.parent.parent.mkdir(parents=True)
        self.secrets.parent.symlink_to(outside, target_is_directory=True)

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "symlink"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(list(outside.iterdir()), [])

    def test_empty_tls_files_fail_without_replacing_canonical_material(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        for corrupt_path in (tls / "server.key", tls / "server.crt", tls / "ca.crt"):
            with self.subTest(path=corrupt_path.name):
                before = {path.name: path.read_bytes() for path in tls.iterdir() if path.is_file()}
                corrupt_path.write_bytes(b"")
                if (self.secrets / "runtime").exists():
                    shutil.rmtree(self.secrets / "runtime")
                if corrupt_path.name == "ca.crt":
                    (self.state / "public/ca.crt").unlink()

                # Act / Assert
                with self.assertRaisesRegex(ValueError, "empty|corrupt|invalid"):
                    initialize_local_files(self.secrets, self.state, "app.localhost")
                self.assertEqual(corrupt_path.read_bytes(), b"")
                for path in tls.iterdir():
                    if path != corrupt_path and path.is_file():
                        self.assertEqual(path.read_bytes(), before[path.name])
                corrupt_path.write_bytes(before[corrupt_path.name])

    def test_invalid_server_signature_is_rejected_without_rotation(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        original = x509.load_pem_x509_certificate((tls / "server.crt").read_bytes())
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        invalid = _resign(original, wrong_key)
        path = tls / "server.crt"
        path.write_bytes(invalid.public_bytes(serialization.Encoding.PEM))
        shutil.rmtree(self.secrets / "runtime")
        corrupt_bytes = path.read_bytes()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "signature|issuer|certificate"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_ca_without_basic_constraints_is_rejected_without_rotation(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        original = x509.load_pem_x509_certificate((tls / "ca.crt").read_bytes())
        ca_key = serialization.load_pem_private_key((tls / "ca.key").read_bytes(), password=None)
        invalid = _resign(original, ca_key, omit_extensions={ExtensionOID.BASIC_CONSTRAINTS})
        path = tls / "ca.crt"
        path.write_bytes(invalid.public_bytes(serialization.Encoding.PEM))
        shutil.rmtree(self.secrets / "runtime")
        (self.state / "public/ca.crt").unlink()
        corrupt_bytes = path.read_bytes()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "BasicConstraints|CA certificate|certificate"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_expired_server_certificate_is_rejected_without_rotation(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        original = x509.load_pem_x509_certificate((tls / "server.crt").read_bytes())
        ca_key = serialization.load_pem_private_key((tls / "ca.key").read_bytes(), password=None)
        invalid = _resign(
            original, ca_key, not_valid_before=datetime.now(timezone.utc) - timedelta(days=3),
            not_valid_after=datetime.now(timezone.utc) - timedelta(days=2),
        )
        path = tls / "server.crt"
        path.write_bytes(invalid.public_bytes(serialization.Encoding.PEM))
        shutil.rmtree(self.secrets / "runtime")
        corrupt_bytes = path.read_bytes()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "valid|expired|certificate"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_server_issuer_mismatch_is_rejected_without_rotation(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        original = x509.load_pem_x509_certificate((tls / "server.crt").read_bytes())
        ca_key = serialization.load_pem_private_key((tls / "ca.key").read_bytes(), password=None)
        invalid = _resign(original, ca_key, issuer=x509.Name([x509.NameAttribute(
            x509.NameOID.COMMON_NAME, "unexpected issuer"
        )]))
        path = tls / "server.crt"
        path.write_bytes(invalid.public_bytes(serialization.Encoding.PEM))
        shutil.rmtree(self.secrets / "runtime")
        corrupt_bytes = path.read_bytes()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "issuer|certificate"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_valid_server_certificate_without_basic_constraints_remains_usable(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        original = x509.load_pem_x509_certificate((tls / "server.crt").read_bytes())
        ca_key = serialization.load_pem_private_key((tls / "ca.key").read_bytes(), password=None)
        path = tls / "server.crt"
        path.write_bytes(_resign(original, ca_key, omit_extensions={ExtensionOID.BASIC_CONSTRAINTS})
                         .public_bytes(serialization.Encoding.PEM))

        # Act
        result = initialize_local_files(self.secrets, self.state, "app.localhost")

        # Assert
        self.assertTrue(result["generation"])
        self.assertNotIn(ExtensionOID.BASIC_CONSTRAINTS,
                         {extension.oid for extension in x509.load_pem_x509_certificate(path.read_bytes()).extensions})

    def test_existing_key_and_certificate_must_match_requested_host(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        key_path = self.secrets / "tls/server.key"
        original_key = key_path.read_bytes()
        key_path.write_bytes(rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "does not match its key"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        key_path.write_bytes(original_key)
        with self.assertRaisesRegex(ValueError, "does not match its host"):
            initialize_local_files(self.secrets, self.state, "other.localhost")

    def test_bootstrap_rejects_malformed_host_secret_key_and_directory(self) -> None:
        # Arrange
        with self.assertRaisesRegex(ValueError, "hostname"):
            initialize_local_files(self.secrets, self.state, "https://app.localhost")
        initialize_local_files(self.secrets, self.state, "app.localhost")
        password = self.secrets / "api_db_password"
        password_bytes = password.read_bytes()
        password.write_bytes(b"")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "secret is empty or malformed"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        password.write_bytes(password_bytes)
        key_path = self.secrets / "tls/ca.key"
        key_bytes = key_path.read_bytes()
        key_path.write_bytes(b"invalid PEM")
        with self.assertRaisesRegex(ValueError, "key is corrupt"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        key_path.write_bytes(key_bytes)

        # Act / Assert
        shutil.rmtree(self.secrets / "tls")
        (self.secrets / "tls").write_text("not-a-directory", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must be a directory"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_existing_keycloak_admin_password_must_match_local_login_contract(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        password = self.secrets / "keycloak_admin_password"
        password.write_text("different-valid-password", encoding="utf-8")
        shutil.rmtree(self.secrets / "runtime")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "admin password must be admin!123"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertEqual(password.read_text(encoding="utf-8"), "different-valid-password")

    def test_mismatched_published_and_consumer_tls_copies_are_rejected(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        public_ca = self.state / "public/ca.crt"
        canonical_ca = public_ca.read_bytes()
        public_ca.write_bytes(b"different certificate")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "does not match the canonical"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        public_ca.write_bytes(canonical_ca)
        derived_key = self.secrets / "runtime/gateway/tls/server.key"
        canonical_key = derived_key.read_bytes()
        derived_key.write_bytes(b"different private key")
        with self.assertRaisesRegex(ValueError, "Derived local TLS file does not match"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        self.assertNotEqual(derived_key.read_bytes(), canonical_key)

    def test_unexpected_runtime_and_ready_directories_are_rejected(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        unexpected_runtime = self.secrets / "runtime/gateway/unexpected"
        unexpected_runtime.mkdir()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "Unexpected directory"):
            initialize_local_files(self.secrets, self.state, "app.localhost")
        unexpected_runtime.rmdir()
        unexpected_ready = self.state / "ready/stale-directory"
        unexpected_ready.mkdir()
        with self.assertRaisesRegex(ValueError, "Unexpected directory"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_local_certificates_retain_required_usage_extensions(self) -> None:
        # Arrange
        initialize_local_files(self.secrets, self.state, "app.localhost")
        tls = self.secrets / "tls"
        ca_key = serialization.load_pem_private_key((tls / "ca.key").read_bytes(), password=None)
        ca_path = tls / "ca.crt"
        ca_original = x509.load_pem_x509_certificate(ca_path.read_bytes())
        no_signing_usage = x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=None, decipher_only=None,
        )
        ca_path.write_bytes(_resign(ca_original, ca_key,
                                    replace_extensions={ExtensionOID.KEY_USAGE: no_signing_usage})
                            .public_bytes(serialization.Encoding.PEM))
        shutil.rmtree(self.secrets / "runtime")
        (self.state / "public/ca.crt").unlink()

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "cannot issue certificates"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

        # Arrange
        if (self.secrets / "runtime").exists():
            shutil.rmtree(self.secrets / "runtime")
        ca_path.write_bytes(ca_original.public_bytes(serialization.Encoding.PEM))
        server_path = tls / "server.crt"
        server_original = x509.load_pem_x509_certificate(server_path.read_bytes())
        server_path.write_bytes(_resign(server_original, ca_key,
                                        omit_extensions={ExtensionOID.EXTENDED_KEY_USAGE})
                                .public_bytes(serialization.Encoding.PEM))
        if (self.secrets / "runtime").exists():
            shutil.rmtree(self.secrets / "runtime")

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "missing a required extension"):
            initialize_local_files(self.secrets, self.state, "app.localhost")

    def test_non_rsa_private_keys_are_rejected(self) -> None:
        # Arrange
        key_path = self.secrets / "unsupported.key"
        key_path.parent.mkdir(parents=True)
        key_path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))

        # Act / Assert
        from ci.local_bootstrap import _load_or_create_key

        with self.assertRaisesRegex(ValueError, "unsupported type"):
            _load_or_create_key(key_path)


def _resign(certificate: x509.Certificate, signing_key, *, issuer=None, not_valid_before=None, not_valid_after=None,
            omit_extensions: set = frozenset(), replace_extensions: dict | None = None) -> x509.Certificate:
    builder = (x509.CertificateBuilder().subject_name(certificate.subject).issuer_name(issuer or certificate.issuer)
               .public_key(certificate.public_key()).serial_number(certificate.serial_number)
               .not_valid_before(not_valid_before or certificate.not_valid_before_utc)
               .not_valid_after(not_valid_after or certificate.not_valid_after_utc))
    for extension in certificate.extensions:
        if extension.oid not in omit_extensions:
            replacement = (replace_extensions or {}).get(extension.oid, extension.value)
            builder = builder.add_extension(replacement, extension.critical)
    return builder.sign(signing_key, hashes.SHA256())


if __name__ == "__main__":
    unittest.main()
