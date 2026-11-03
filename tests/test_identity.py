"""Who is signed in behind IAP, checked with real ES256 tokens signed like IAP's."""
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.auth import jwt
from google.auth.crypt import es

import identity

AUD = "/projects/1/locations/europe-north1/services/pomodo"
KEY, OTHER = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())


def _pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _token(key=KEY, kid="k1", **claims):
    now = int(time.time())
    body = {"iss": identity.IAP_ISSUER, "aud": AUD, "email": "Ada@Example.com", "sub": "x",
            "iat": now, "exp": now + 600, **claims}
    body = {k: v for k, v in body.items() if v is not None}
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return jwt.encode(es.EsSigner.from_string(pem, key_id=kid), body).decode()


@pytest.fixture
def keys(monkeypatch):
    """IAP's key list as the app would download it; `served` can change, `fetches` counts downloads."""
    state = {"served": {"k1": _pem(KEY)}, "fetches": 0}

    def fetch():
        state["fetches"] += 1
        return dict(state["served"])

    monkeypatch.setattr(identity, "_fetch_keys", fetch)
    monkeypatch.setattr(identity, "_keys", {"certs": None, "at": None})
    monkeypatch.setenv("IAP_AUDIENCE", AUD)
    return state


def _email(token):
    return identity.viewer_email({"X-Goog-IAP-JWT-Assertion": token})


def test_a_valid_iap_token_names_the_email_in_lower_case(keys):
    assert _email(_token()) == "ada@example.com"
    assert _email(_token(email="KOFI@EXAMPLE.COM")) == "kofi@example.com"
    assert _email(_token(iat=int(time.time()) + 5)) == "ada@example.com"  # a few seconds of clock difference is fine


@pytest.mark.parametrize("token", [
    _token(aud="/projects/1/locations/europe-north1/services/other"),
    _token(aud=None),
    _token(iss="https://evil.example"),
    _token(iat=int(time.time()) - 1200, exp=int(time.time()) - 600),
    _token(key=OTHER),
    _token(email=None),
    "not.a.token",
    "",
    None,
])
def test_anything_else_names_nobody(keys, token):
    assert _email(token) is None


def test_the_unsigned_email_header_is_ignored(keys):
    assert identity.viewer_email({"X-Goog-Authenticated-User-Email": "accounts.google.com:ada@example.com"}) is None
    assert identity.viewer_email({}) is None


@pytest.mark.parametrize("audience", ["", None])
def test_without_an_audience_nobody_is_trusted(keys, monkeypatch, audience):
    if audience is None:
        monkeypatch.delenv("IAP_AUDIENCE")
    else:
        monkeypatch.setenv("IAP_AUDIENCE", audience)
    assert _email(_token()) is None


def test_keys_are_downloaded_once_not_on_every_page_view(keys):
    for _ in range(10):
        assert _email(_token()) == "ada@example.com"
    assert keys["fetches"] == 1


def test_a_new_iap_key_is_fetched_once(keys, monkeypatch):
    assert _email(_token()) == "ada@example.com"
    keys["served"] = {"k1": _pem(KEY), "k2": _pem(OTHER)}  # IAP rotated in a new key
    monkeypatch.setattr(identity, "_keys", {"certs": identity._keys["certs"], "at": time.monotonic() - 120})
    assert _email(_token(key=OTHER, kid="k2")) == "ada@example.com"
    assert keys["fetches"] == 2
    assert _email(_token(key=OTHER, kid="unknown")) is None  # an unknown key within a minute does not fetch again
    assert keys["fetches"] == 2


def test_when_iap_keys_cannot_be_downloaded_page_views_do_not_queue_for_them(keys, monkeypatch):
    def down():
        keys["fetches"] += 1
        raise OSError("gstatic.com unreachable")

    monkeypatch.setattr(identity, "_fetch_keys", down)
    assert _email(_token()) is None
    assert _email(_token()) is None  # within the back-off: no second download
    assert keys["fetches"] == 1
    monkeypatch.setattr(identity, "_keys", {"certs": None, "at": time.monotonic() - identity.NO_KEYS_RETRY - 1})
    assert _email(_token()) is None
    assert keys["fetches"] == 2
