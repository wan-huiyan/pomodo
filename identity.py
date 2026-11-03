"""Who is signed in behind Google's Identity-Aware Proxy: the email in IAP's signed header.

IAP signs every request it lets through (x-goog-iap-jwt-assertion, ES256). The email is trusted only after that
signature, the audience and the issuer check out; the unsigned x-goog-authenticated-user-email header is ignored.
"""
import logging
import os
import threading
import time

import requests
from google.auth import jwt

log = logging.getLogger(__name__)

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"

KEYS_MAX_AGE = 3600  # IAP rotates its keys now and then; download them again at least hourly
REFETCH_AFTER = 60   # a token signed with a key we don't have downloads them again, at most once a minute
NO_KEYS_RETRY = 10   # with no keys at all, a failed download is retried after this long, not by every page view
CLOCK_SKEW = 30      # seconds of clock difference allowed between IAP and this instance

_keys = {"certs": None, "at": None}  # at: time.monotonic() of the last download try
_keys_lock = threading.Lock()


def _fetch_keys():
    r = requests.get(IAP_CERTS, timeout=5)
    r.raise_for_status()
    certs = r.json()
    if not isinstance(certs, dict) or not certs:
        raise ValueError("IAP sent no keys")
    return certs


def _iap_keys(refetch=False):
    """IAP's public keys by key id, downloaded once and reused (a page view must not wait for gstatic.com)."""
    with _keys_lock:
        age = None if _keys["at"] is None else time.monotonic() - _keys["at"]
        if _keys["certs"] is None:
            due = age is None or age > NO_KEYS_RETRY
        else:
            due = age > KEYS_MAX_AGE or (refetch and age > REFETCH_AFTER)
        if due:
            _keys["at"] = time.monotonic()  # whatever happens, wait before trying again
            try:
                _keys["certs"] = _fetch_keys()
            except Exception as e:
                log.warning("Could not download IAP's keys (%s)", type(e).__name__)
        if _keys["certs"] is None:
            raise RuntimeError("IAP's keys are not available")
        return _keys["certs"]


def _verify(token, audience):
    """The token's claims, once its signature, audience, issue time and expiry check out."""
    keys = _iap_keys()
    if jwt.decode_header(token).get("kid") not in keys:
        keys = _iap_keys(refetch=True)
    return jwt.decode(token, certs=keys, audience=audience, clock_skew_in_seconds=CLOCK_SKEW)


def viewer_email(headers):
    """The signed-in email, or None when there is no valid IAP signature."""
    token, audience = headers.get("X-Goog-IAP-JWT-Assertion"), os.environ.get("IAP_AUDIENCE")
    if not token or not audience:  # never call decode without an audience: it would skip that check
        return None
    try:
        claims = _verify(token, audience)
    except Exception as e:  # bad signature, wrong audience, expired, or the keys could not be fetched
        log.warning("IAP signature did not verify (%s)", type(e).__name__)  # the message can contain the token
        return None
    if claims.get("iss") != IAP_ISSUER:
        log.warning("IAP signature has the wrong issuer")
        return None
    return (claims.get("email") or "").lower() or None
