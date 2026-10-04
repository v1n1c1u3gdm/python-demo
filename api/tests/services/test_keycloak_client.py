import base64
import time

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from services.keycloak_client import KeycloakClient, KeycloakError


class FakeResponse:
    def __init__(self, payload, ok=True, text=""):
        self.payload = payload
        self.ok = ok
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.responses.pop(0)


def encoded_integer(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def rsa_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
        "n": encoded_integer(numbers.n),
        "e": encoded_integer(numbers.e),
    }
    return private_key, jwk


def test_decode_token_fetches_and_caches_discovery_and_jwks():
    # Arrange
    private_key, jwk = rsa_pair()
    token = jwt.encode(
        {
            "sub": "alice",
            "iss": "https://id.example/realms/demo",
            "aud": "api",
            "exp": int(time.time()) + 300,
            "typ": "Bearer",
            "realm_access": {"roles": ["author"]},
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [jwk]}),
    ])
    client = KeycloakClient(
        "https://id.example/", "demo", "api", issuer="https://id.example/realms/demo",
        audience="api", session=session
    )

    # Act
    first_claims = client.decode_token(token)
    second_claims = client.decode_token(token)

    # Assert
    assert first_claims["sub"] == second_claims["sub"] == "alice"
    assert [call[1] for call in session.calls] == [
        "https://id.example/realms/demo/.well-known/openid-configuration",
        "https://id.example/keys",
    ]


def test_discovery_cache_expires_before_fetching_metadata_again(monkeypatch):
    # Arrange
    now = [10]
    monkeypatch.setattr("services.keycloak_client.time.time", lambda: now[0])
    session = FakeSession([
        FakeResponse({"token_endpoint": "https://id.example/token"}),
        FakeResponse({"token_endpoint": "https://id.example/token-2"}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", session=session, cache_ttl_seconds=5)

    # Act
    first = client._get_well_known()
    same = client._get_well_known()
    now[0] = 16
    refreshed = client._get_well_known()

    # Assert
    assert first is same
    assert refreshed["token_endpoint"].endswith("token-2")
    assert len(session.calls) == 2


def test_jwks_cache_expires_and_refetches_keys(monkeypatch):
    # Arrange
    now = [10]
    monkeypatch.setattr("services.keycloak_client.time.time", lambda: now[0])
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [{"kid": "first"}]}),
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [{"kid": "second"}]}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", session=session, cache_ttl_seconds=5)

    # Act
    first = client._get_jwks()
    cached = client._get_jwks()
    now[0] = 16
    refreshed = client._get_jwks()

    # Assert
    assert first is cached
    assert refreshed["keys"] == [{"kid": "second"}]
    assert [call[1] for call in session.calls].count("https://id.example/keys") == 2
    assert [call[1] for call in session.calls].count(
        "https://id.example/realms/demo/.well-known/openid-configuration"
    ) == 2


def test_exchange_password_sends_grant_and_optional_secret():
    # Arrange
    session = FakeSession([
        FakeResponse({"token_endpoint": "https://id.example/token"}),
        FakeResponse({"access_token": "issued-token"}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", "secret", session=session)

    # Act
    result = client.exchange_password("alice", "password")

    # Assert
    assert result == {"access_token": "issued-token"}
    posted = session.calls[-1][2]
    assert posted["data"] == {
        "grant_type": "password",
        "client_id": "api",
        "username": "alice",
        "password": "password",
        "client_secret": "secret",
    }
    assert posted["timeout"] == 15


def test_exchange_password_omits_secret_for_public_client():
    # Arrange
    session = FakeSession([
        FakeResponse({"token_endpoint": "https://id.example/token"}),
        FakeResponse({"access_token": "issued-token"}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", session=session)

    # Act
    client.exchange_password("alice", "password")

    # Assert
    assert "client_secret" not in session.calls[-1][2]["data"]


def test_exchange_password_hides_transport_exception_details():
    # Arrange
    class FailingSession(FakeSession):
        def post(self, url, **kwargs):
            raise requests.ConnectionError("request contained client-secret-123")

    session = FailingSession([FakeResponse({"token_endpoint": "https://id.example/token"})])
    client = KeycloakClient("https://id.example", "demo", "api", session=session)

    # Act / Assert
    with pytest.raises(KeycloakError, match="Authentication service unavailable") as error:
        client.exchange_password("alice", "password")
    assert "client-secret-123" not in str(error.value)


@pytest.mark.parametrize(
    ("responses", "message"),
    [
    ([FakeResponse({"error": "offline"}, ok=False)], "Unable to fetch OpenID metadata."),
        ([FakeResponse({})], "Keycloak token endpoint not available."),
    ([FakeResponse({"token_endpoint": "/token"}), FakeResponse({"error_description": "denied"}, ok=False)], "Invalid credentials."),
    ([FakeResponse({"token_endpoint": "/token"}), FakeResponse(ValueError(), ok=False, text="bad response")], "Invalid credentials."),
    ],
)
def test_exchange_password_reports_http_and_metadata_errors(responses, message):
    # Arrange
    client = KeycloakClient("https://id.example", "demo", "api", session=FakeSession(responses))

    # Act / Assert
    with pytest.raises(KeycloakError, match=message.replace(".", r"\.")):
        client.exchange_password("alice", "wrong")


def test_decode_token_rejects_missing_key_and_invalid_jwt():
    # Arrange
    missing_key_session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": []}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", session=missing_key_session)

    # Act / Assert
    with pytest.raises(KeycloakError, match="Invalid token header"):
        client.decode_token("not-a-jwt")
    with pytest.raises(KeycloakError, match="Token validation failed"):
        client.decode_token(jwt.encode({"sub": "alice"}, "secret", algorithm="HS256", headers={"kid": "missing"}))


def test_decode_token_without_key_identifier_fails_without_http_request():
    # Arrange
    session = FakeSession([])
    client = KeycloakClient("https://id.example", "demo", "api", session=session)
    token = jwt.encode({"sub": "alice"}, "secret", algorithm="HS256")

    # Act / Assert
    with pytest.raises(KeycloakError, match="Token validation failed"):
        client.decode_token(token)
    assert session.calls == []


def test_decode_token_reports_missing_jwks_and_failed_jwks_request():
    # Arrange
    missing_uri = KeycloakClient("https://id.example", "demo", "api", session=FakeSession([FakeResponse({})]))
    failed_fetch = KeycloakClient("https://id.example", "demo", "api", session=FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"error": "unavailable"}, ok=False),
    ]))

    # Act / Assert
    with pytest.raises(KeycloakError, match="JWKS endpoint not available"):
        missing_uri._get_jwks()
    with pytest.raises(KeycloakError, match="Unable to fetch JWKS"):
        failed_fetch._get_jwks()


def test_decode_token_rejects_signature_validation_failure():
    # Arrange
    private_key, jwk = rsa_pair()
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"sub": "alice"}, wrong_key, algorithm="RS256", headers={"kid": "test-key"})
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [jwk]}),
    ])
    client = KeycloakClient("https://id.example", "demo", "api", session=session)

    # Act / Assert
    with pytest.raises(KeycloakError, match="Token validation failed"):
        client.decode_token(token)


def test_decode_token_rejects_untrusted_algorithm_and_missing_claims():
    # Arrange
    secret = "shared-secret-that-must-not-be-trusted"
    jwk = {
        "kty": "oct",
        "kid": "test-key",
        "use": "sig",
        "alg": "HS256",
        "k": base64.urlsafe_b64encode(secret.encode()).rstrip(b"=").decode(),
    }
    token = jwt.encode(
        {"sub": "alice", "iss": "https://id.example/realms/demo", "aud": "api", "exp": int(time.time()) + 300, "typ": "Bearer"},
        secret,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [jwk]}),
    ])
    client = KeycloakClient(
        "https://id.example", "demo", "api", issuer="https://id.example/realms/demo",
        audience="api", session=session
    )

    # Act / Assert
    with pytest.raises(KeycloakError, match="Token validation failed"):
        client.decode_token(token)


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://id.example/realms/demo", "aud": "api", "exp": int(time.time()) + 300, "typ": "Bearer"},
        {"sub": "alice", "aud": "api", "exp": int(time.time()) + 300, "typ": "Bearer"},
        {"sub": "alice", "iss": "https://id.example/realms/demo", "exp": int(time.time()) + 300, "typ": "Bearer"},
        {"sub": "alice", "iss": "https://id.example/realms/demo", "aud": "api", "typ": "Bearer"},
    ],
)
def test_decode_token_rejects_missing_required_claims(claims):
    # Arrange
    private_key, jwk = rsa_pair()
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [jwk]}),
    ])
    client = KeycloakClient(
        "https://id.example", "demo", "api", issuer="https://id.example/realms/demo",
        audience="api", session=session
    )

    # Act / Assert
    with pytest.raises(KeycloakError, match="Token validation failed"):
        client.decode_token(token)


def test_decode_token_requires_expected_issuer_audience_and_access_token():
    # Arrange
    private_key, jwk = rsa_pair()
    session = FakeSession([
        FakeResponse({"jwks_uri": "https://id.example/keys"}),
        FakeResponse({"keys": [jwk]}),
    ])
    client = KeycloakClient(
        "https://id.example", "demo", "api", issuer="https://id.example/realms/demo",
        audience="api", session=session
    )

    def signed(claims):
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})

    valid = {
        "sub": "alice", "iss": "https://id.example/realms/demo", "aud": "api",
        "exp": int(time.time()) + 300, "typ": "Bearer"
    }

    # Act / Assert
    for invalid in (
        {**valid, "iss": "https://attacker.example/realms/demo"},
        {**valid, "aud": "another-client"},
        {**valid, "typ": "ID"},
        {**valid, "sub": ""},
    ):
        with pytest.raises(KeycloakError, match="Token validation failed"):
            client.decode_token(signed(invalid))
    assert client.decode_token(signed(valid))["sub"] == "alice"


def test_extract_and_require_roles_preserve_sorted_unique_string_roles():
    # Arrange
    client = KeycloakClient("https://id.example", "demo", "api")
    claims = {"sub": "alice", "realm_access": {"roles": ["author", "admin", "author", 42]}}

    # Act
    roles = client.extract_roles(claims)
    client.decode_token = lambda token: claims
    granted = client.require_roles("token", ["author"])

    # Assert
    assert roles == ["admin", "author"]
    assert granted == claims
    assert client.extract_roles({}) == []
    with pytest.raises(KeycloakError, match=r"Missing required role\(s\): reader"):
        client.require_roles("token", ["reader"])


def test_require_roles_returns_claims_when_every_role_is_present(monkeypatch):
    # Arrange
    client = KeycloakClient("https://id.example", "demo", "api")
    claims = {"sub": "alice", "realm_access": {"roles": ["admin"]}}
    monkeypatch.setattr(client, "decode_token", lambda token: claims)

    # Act
    result = client.require_roles("token", ["admin"])

    # Assert
    assert result == claims


def test_from_config_and_client_lookup_use_expected_defaults(app):
    # Arrange
    from services.keycloak_client import get_keycloak_client

    # Act
    client = KeycloakClient.from_config({})

    # Assert
    assert client.base_url == "http://keycloak:8080"
    assert client.realm == "python-demo"
    assert client.client_id == "python-demo-api"
    assert app.extensions["keycloak_client"] is not None
    with app.app_context():
        assert get_keycloak_client() is app.extensions["keycloak_client"]


def test_client_lookup_reports_missing_configuration(app):
    # Arrange
    from services.keycloak_client import get_keycloak_client
    existing_client = app.extensions.pop("keycloak_client")

    # Act / Assert
    with app.app_context(), pytest.raises(RuntimeError, match="not configured"):
        get_keycloak_client()
    app.extensions["keycloak_client"] = existing_client
