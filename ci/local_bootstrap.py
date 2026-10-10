"""Create persistent, checkout-local secrets and TLS material before Compose starts consumers."""

import base64
import json
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

SECRET_NAMES = (
    "api_db_password", "mysql_root_password", "keycloak_db_password", "keycloak_client_secret",
    "keycloak_admin_password", "bookstack_db_password", "mariadb_root_password", "bookstack_app_key",
    "bookstack_client_secret", "gitea_client_secret", "woodpecker_agent_secret", "gitea_admin_password",
)
WOODPECKER_GID = 10001
OPTIONAL_NATIVE_SECRETS = ("gitea_bootstrap_api_token", "woodpecker_gitea_client", "woodpecker_gitea_secret")


def _atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    _assert_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_no_symlink_components(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".local-bootstrap-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path: Path) -> bytes | None:
    _assert_no_symlink_components(path)
    if path.is_symlink():
        raise ValueError("Local secret or TLS material must not be a symlink.")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Local secret or TLS material is invalid.")
    return path.read_bytes()


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        if component.is_symlink():
            raise ValueError("Local bootstrap paths must not contain symlinks.")


def _prepare_directory(path: Path, mode: int) -> None:
    _assert_no_symlink_components(path)
    if path.exists() and not path.is_dir():
        raise ValueError("Local bootstrap path must be a directory.")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    _assert_no_symlink_components(path)
    if not path.is_dir():
        raise ValueError("Local bootstrap path must be a directory.")
    os.chmod(path, mode)


def _invalidate_route_markers(state_dir: Path) -> None:
    """Close routes from the prior generation before any mutable validation can fail."""
    ready = state_dir / "ready"
    _assert_no_symlink_components(ready)
    if not ready.exists():
        return
    if not ready.is_dir():
        raise ValueError("Local readiness path must be a directory.")
    unexpected_directory = False
    for marker in ready.iterdir():
        # Unlink direct symlinks without resolving their targets. Safe regular files
        # are invalidated even if an unrelated nested directory makes bootstrap fail.
        if marker.is_symlink() or marker.is_file():
            marker.unlink()
        else:
            unexpected_directory = True
    if unexpected_directory:
        raise ValueError("Unexpected directory in local readiness state.")


def _load_or_create_key(path: Path) -> rsa.RSAPrivateKey:
    current = _read(path)
    if current is not None:
        if not current:
            raise ValueError("Existing local TLS key is empty or corrupt.")
        try:
            key = serialization.load_pem_private_key(current, password=None)
        except (ValueError, TypeError):
            raise ValueError("Existing local TLS key is corrupt.") from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("Existing local TLS key has an unsupported type.")
        return key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _atomic(path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return key


def _certificate(path: Path, key: rsa.RSAPrivateKey, host: str, issuer: x509.Name,
                 signing_key: rsa.RSAPrivateKey, is_ca: bool) -> x509.Certificate:
    current = _read(path)
    if current is not None:
        if not current:
            raise ValueError("Existing local TLS certificate is empty or corrupt.")
        try:
            certificate = x509.load_pem_x509_certificate(current)
        except ValueError:
            raise ValueError("Existing local TLS certificate is corrupt or does not match its key.") from None
        if certificate.public_key().public_numbers() != key.public_key().public_numbers():
            raise ValueError("Existing local TLS certificate is corrupt or does not match its key.")
        if certificate.issuer != issuer:
            raise ValueError("Existing local TLS certificate has an invalid issuer.")
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise ValueError("Existing local TLS certificate is expired or not yet valid.")
        try:
            constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound:
            if is_ca:
                raise ValueError("Existing local CA certificate has no BasicConstraints.") from None
        else:
            if constraints.ca is not is_ca:
                raise ValueError("Existing local TLS certificate has invalid BasicConstraints.")
        if not is_ca:
            try:
                names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            except x509.ExtensionNotFound:
                raise ValueError("Existing local TLS certificate has no subject alternative name.") from None
            if host not in names.get_values_for_type(x509.DNSName):
                raise ValueError("Existing local TLS certificate does not match its host.")
        else:
            if certificate.subject != certificate.issuer:
                raise ValueError("Existing local CA certificate is not self-issued.")
        try:
            expected_authority = x509.AuthorityKeyIdentifier.from_issuer_public_key(signing_key.public_key())
            actual_authority = certificate.extensions.get_extension_for_class(
                x509.AuthorityKeyIdentifier).value
            certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
            usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
            if is_ca:
                if not usage.key_cert_sign:
                    raise ValueError("Existing local CA cannot issue certificates.")
            else:
                purpose = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                if (x509.oid.ExtendedKeyUsageOID.SERVER_AUTH not in purpose or not usage.digital_signature
                        or not usage.key_encipherment):
                    raise ValueError("Existing local server certificate has invalid key usage.")
        except x509.ExtensionNotFound:
            raise ValueError("Existing local TLS certificate is missing a required extension.") from None
        if actual_authority != expected_authority:
            raise ValueError("Existing local TLS certificate has an invalid authority identifier.")
        expected_subject = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        if certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value != expected_subject:
            raise ValueError("Existing local TLS certificate has an invalid subject identifier.")
        _verify_certificate_signature(certificate, signing_key.public_key())
        return certificate
    now = datetime.now(timezone.utc)
    common_name = "python-demo local CA" if is_ca else host
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
               .not_valid_after(now + timedelta(days=3650)))
    if is_ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=None, decipher_only=None,
        ), critical=True)
    else:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=None, decipher_only=None,
        ), critical=True)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(signing_key.public_key()), critical=False
    )
    certificate = builder.sign(signing_key, hashes.SHA256())
    _atomic(path, certificate.public_bytes(serialization.Encoding.PEM))
    return certificate


def _verify_certificate_signature(certificate: x509.Certificate, issuer_key: rsa.RSAPublicKey) -> None:
    try:
        issuer_key.verify(certificate.signature, certificate.tbs_certificate_bytes,
                          padding.PKCS1v15(), certificate.signature_hash_algorithm)
    except InvalidSignature:
        raise ValueError("Existing local TLS certificate has an invalid signature.") from None

def initialize_local_files(secrets_dir: Path, state_dir: Path, public_host: str) -> dict[str, str]:
    """Preserve valid secrets and keys and invalidate old route-ready markers."""
    secrets_dir, state_dir = Path(os.path.abspath(secrets_dir)), Path(os.path.abspath(state_dir))
    planned_directories = [
        secrets_dir, secrets_dir / "tls", secrets_dir / "runtime", state_dir,
        state_dir / "public", state_dir / "browser", state_dir / "ready",
        *(secrets_dir / "runtime" / consumer for consumer in (
            "api", "mysql", "keycloak-db-init", "keycloak", "keycloak-init", "gateway",
            "bookstack", "mariadb", "gitea", "gitea-oauth-init", "woodpecker",
        )),
        secrets_dir / "runtime/api/tls", secrets_dir / "runtime/gateway/tls",
        secrets_dir / "runtime/woodpecker/tls",
    ]
    for directory in planned_directories:
        _assert_no_symlink_components(directory)
    _invalidate_route_markers(state_dir)
    if not public_host or "/" in public_host or ":" in public_host or "@" in public_host:
        raise ValueError("Public host must be a hostname.")
    try:
        owner_uid = int(os.environ.get("LOCAL_OWNER_UID", str(os.getuid())))
        owner_gid = int(os.environ.get("LOCAL_OWNER_GID", str(os.getgid())))
        if owner_uid < 0 or owner_gid < 0:
            raise ValueError
    except ValueError:
        raise ValueError("Local state owner must use numeric non-negative identifiers.") from None
    if os.geteuid() != 0 and (os.geteuid(), os.getegid()) != (owner_uid, owner_gid):
        raise ValueError("Local state owner does not match the bootstrap process.")
    _prepare_directory(secrets_dir, 0o700)
    _prepare_directory(state_dir, 0o700)
    if os.geteuid() == 0:
        os.chown(state_dir, owner_uid, owner_gid)
    for name in SECRET_NAMES:
        path = secrets_dir / name
        current = _read(path)
        if current is None:
            value = "admin!123" if name in {"keycloak_admin_password", "gitea_admin_password"} else secrets.token_urlsafe(36)
            if name == "bookstack_app_key":
                value = "base64:" + base64.b64encode(secrets.token_bytes(32)).decode()
            _atomic(path, value.encode())
        elif not current or b"\n" in current or b"\r" in current:
            raise ValueError("Existing local secret is empty or malformed.")
        elif name in {"keycloak_admin_password", "gitea_admin_password"} and current != b"admin!123":
            raise ValueError("Local Keycloak/Gitea admin password must be admin!123.")
        os.chmod(path, 0o600)

    optional_contents = {name: _read(secrets_dir / name) for name in OPTIONAL_NATIVE_SECRETS}
    for name, content in optional_contents.items():
        if content is not None and (not content or b"\n" in content or b"\r" in content):
            raise ValueError("Existing canonical local secret is empty or malformed.")
    oauth_pair = (optional_contents["woodpecker_gitea_client"], optional_contents["woodpecker_gitea_secret"])
    if (oauth_pair[0] is None) != (oauth_pair[1] is None):
        raise ValueError("Canonical Woodpecker OAuth credential pair is incomplete; refusing rotation.")

    tls = secrets_dir / "tls"
    _prepare_directory(tls, 0o700)
    ca_key = _load_or_create_key(tls / "ca.key")
    ca_cert_path = tls / "ca.crt"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "python-demo local CA")])
    ca_cert = _certificate(ca_cert_path, ca_key, public_host, name, ca_key, True)
    server_key = _load_or_create_key(tls / "server.key")
    _certificate(tls / "server.crt", server_key, public_host, ca_cert.subject, ca_key, False)
    # BookStack's PHP/libcurl resolves *.localhost to loopback. A separate
    # internal listener/certificate avoids changing the persisted public cert.
    backchannel_key = _load_or_create_key(tls / "backchannel.key")
    _certificate(tls / "backchannel.crt", backchannel_key, "gateway", ca_cert.subject, ca_key, False)
    for name in ("ca.crt", "server.crt", "server.key", "backchannel.crt", "backchannel.key", "ca.key"):
        os.chmod(tls / name, 0o600)

    # The host browser needs only the public CA. Its profile/report directory is
    # writable by the checkout owner without making runtime state world-readable.
    public_dir = state_dir / "public"
    browser_dir = state_dir / "browser"
    for directory, mode in ((public_dir, 0o755), (browser_dir, 0o700)):
        _prepare_directory(directory, mode)
        if os.geteuid() == 0:
            os.chown(directory, owner_uid, owner_gid)
    public_ca = public_dir / "ca.crt"
    public_ca_content = (tls / "ca.crt").read_bytes()
    existing_public_ca = _read(public_ca)
    if existing_public_ca is not None and existing_public_ca != public_ca_content:
        raise ValueError("Published local CA does not match the canonical certificate.")
    if existing_public_ca is None:
        _atomic(public_ca, public_ca_content, 0o644)
    else:
        os.chmod(public_ca, 0o644)
    if os.geteuid() == 0:
        os.chown(public_ca, owner_uid, owner_gid)

    # Consumer mounts contain only the material each runtime needs. The CA private
    # key never leaves the source directory; Keycloak's UID 1000 reads group-only files.
    consumers = {
        "api": ("api_db_password", "keycloak_client_secret"),
        "mysql": ("api_db_password", "mysql_root_password"),
        "keycloak-db-init": ("mysql_root_password", "keycloak_db_password"),
        "keycloak": ("keycloak_db_password", "keycloak_admin_password"),
        "keycloak-init": ("keycloak_admin_password", "keycloak_client_secret",
                          "bookstack_client_secret", "gitea_client_secret"),
        "gateway": (),
        "bookstack": ("bookstack_db_password", "bookstack_app_key", "bookstack_client_secret"),
        "mariadb": ("bookstack_db_password", "mariadb_root_password"),
        "gitea": ("gitea_client_secret", "gitea_admin_password"),
        "gitea-oauth-init": ("gitea_bootstrap_api_token",),
        "woodpecker": ("woodpecker_agent_secret",),
    }
    _prepare_directory(secrets_dir / "runtime", 0o700)
    for consumer, names in consumers.items():
        target = secrets_dir / "runtime" / consumer
        _prepare_directory(target, 0o700)
        if consumer == "woodpecker":
            os.chmod(target, 0o750)
            if os.geteuid() == 0:
                os.chown(target, 0, WOODPECKER_GID)
        if consumer == "keycloak":
            try:
                if os.geteuid() == 0:
                    os.chown(target, 0, 0)
                elif os.geteuid() != 1000 or os.getegid() != 1000:
                    raise PermissionError
                os.chmod(target, 0o750)
            except PermissionError:
                raise ValueError("Could not set Keycloak secret directory ownership.") from None
        generated_files = {"woodpecker": {"gitea_client_id", "gitea_client_secret"}}.get(consumer, set())
        uses_ca = consumer in {"api", "bookstack", "gitea", "woodpecker"}
        expected = set(names) | generated_files | ({"tls"} if consumer == "gateway" or uses_ca else set())
        for existing in target.iterdir():
            if existing.name not in expected:
                if existing.is_file() or existing.is_symlink():
                    existing.unlink()
                else:
                    raise ValueError("Unexpected directory in local consumer secret state.")
        for name in names:
            mode = 0o640 if consumer in {"keycloak", "woodpecker"} else 0o600
            staged = target / name
            canonical_path = secrets_dir / name
            if name in OPTIONAL_NATIVE_SECRETS and not canonical_path.exists():
                if _read(staged) is not None:
                    raise ValueError("Derived local secret exists without canonical source.")
                continue
            canonical = _read(canonical_path)
            if canonical is None:
                continue
            derived = _read(staged)
            if derived is not None and derived != canonical:
                raise ValueError("Derived local secret does not match its canonical value.")
            if derived is None:
                _atomic(staged, canonical, mode)
            else:
                os.chmod(staged, mode)
            if consumer == "woodpecker" and os.geteuid() == 0:
                os.chown(staged, 0, WOODPECKER_GID)
            if consumer == "keycloak":
                try:
                    if os.geteuid() == 0:
                        os.chown(staged, 0, 0)
                    elif os.geteuid() != 1000 or os.getegid() != 1000:
                        raise PermissionError
                except PermissionError:
                    raise ValueError("Could not set Keycloak secret ownership.") from None
        if consumer == "gateway" or uses_ca:
            consumer_tls = target / "tls"
            tls_mode = 0o750 if consumer == "woodpecker" else 0o700
            _prepare_directory(consumer_tls, tls_mode)
            if consumer == "woodpecker" and os.geteuid() == 0:
                os.chown(consumer_tls, 0, WOODPECKER_GID)
            wanted_tls = (("server.crt", "server.key", "backchannel.crt", "backchannel.key")
                          if consumer == "gateway" else ("ca.crt",))
            for existing in consumer_tls.iterdir():
                if existing.name not in wanted_tls:
                    if existing.is_file() or existing.is_symlink():
                        existing.unlink()
                    else:
                        raise ValueError("Unexpected item in local consumer TLS state.")
            for name in wanted_tls:
                staged = consumer_tls / name
                canonical = (tls / name).read_bytes()
                derived = _read(staged)
                if derived is not None and derived != canonical:
                    raise ValueError("Derived local TLS file does not match its canonical value.")
                if derived is None:
                    _atomic(staged, canonical, 0o640 if consumer == "woodpecker" else 0o600)
                else:
                    os.chmod(staged, 0o640 if consumer == "woodpecker" else 0o600)
                if consumer == "woodpecker" and os.geteuid() == 0:
                    os.chown(staged, 0, WOODPECKER_GID)

    # Gitea creates these values once. Derived Woodpecker copies are refreshed
    # only from the canonical pair, and never prune or synthesize missing halves.
    if oauth_pair[0] is not None:
        target = secrets_dir / "runtime" / "woodpecker"
        for canonical_name, runtime_name, content in zip(
                ("woodpecker_gitea_client", "woodpecker_gitea_secret"),
                ("gitea_client_id", "gitea_client_secret"), oauth_pair):
            path = target / runtime_name
            derived = _read(path)
            if derived is not None and derived != content:
                raise ValueError("Derived local secret does not match its canonical value.")
            if derived is None:
                _atomic(path, content, 0o640)
            else:
                os.chmod(path, 0o640)
            if os.geteuid() == 0:
                os.chown(path, 0, WOODPECKER_GID)
    else:
        for name in ("gitea_client_id", "gitea_client_secret"):
            if _read(secrets_dir / "runtime" / "woodpecker" / name) is not None:
                raise ValueError("Derived Woodpecker OAuth secret exists without canonical source.")

    ready = state_dir / "ready"
    _prepare_directory(ready, 0o700)
    payload = {"generation": uuid.uuid4().hex,
               "issuer": f"https://{public_host}/auth/realms/python-demo"}
    _atomic(state_dir / "bootstrap-generation", (payload["generation"] + "\n").encode())
    _atomic(state_dir / "bootstrap.json", json.dumps(payload, sort_keys=True).encode())
    return payload


def main() -> None:
    """Initialize persistent local files from the Compose command line."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/local-secrets"))
    parser.add_argument("--state-dir", type=Path, default=Path("/run/local-state"))
    parser.add_argument("--public-host", default="app.localhost")
    arguments = parser.parse_args()
    result = initialize_local_files(arguments.secrets_dir, arguments.state_dir, arguments.public_host)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
