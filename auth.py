"""Who is signed in: a GitHub login, kept in Flask's signed session cookie after a GitHub OAuth sign-in.

The sign-in is GitHub's OAuth app flow, done by hand: a random state kept in the session, the code exchanged on the
server, no scopes asked for, and the login read from GitHub's /user. The user's token is used for that one read and
then dropped; only the login is kept. Whether that login is on the team is checked again on every request (main.py's
gate), so taking someone out of pomodo.toml takes effect at once.
"""
import hmac
import logging
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from flask import Blueprint, redirect, render_template, request, session, url_for

from config import CONFIG

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
TIMEOUT = 10  # seconds for each call to GitHub during sign-in
SESSION_DAYS = 30
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")  # what GitHub allows in a login
_TEAM = {p.login.lower(): p.login for p in CONFIG.team}  # GitHub logins ignore case; the config's spelling is kept
_IDS = {p.login: p.id for p in CONFIG.team}
MIN_SECRET = 32  # characters; a short key can be guessed offline from any cookie the board hands out

bp = Blueprint("auth", __name__)


def local_copy():
    """True on saved data (DEV_FIXTURE) or made-up data (DEMO=1). Nothing is ever written to GitHub then."""
    return bool(os.environ.get("DEV_FIXTURE")) or os.environ.get("DEMO") == "1"


def production():
    """True where the board is deployed: Cloud Run sets K_SERVICE; any other host can set POMODO_PRODUCTION."""
    return bool(os.environ.get("K_SERVICE") or os.environ.get("POMODO_PRODUCTION"))


def team_login(login):
    """The config's spelling of this login if they are on the team, else None."""
    if not isinstance(login, str) or not login.isascii():  # str.lower() folds some non-ASCII letters onto ASCII ones
        return None
    return _TEAM.get(login.lower())


def dev_user():
    """DEV_USER's login, only in local-copy mode and never where the board is deployed."""
    login = os.environ.get("DEV_USER", "").strip()
    return login if login and local_copy() and not production() else None


def current_login():
    """The signed-in GitHub login as GitHub spells it (on the team or not), or None."""
    login = dev_user() or session.get("login")
    return login if isinstance(login, str) and login else None


def secret_key():
    """SECRET_KEY from the environment. Local copies make a random one per start, which signs everyone out on restart."""
    key = os.environ.get("SECRET_KEY", "")
    if local_copy():
        return key or secrets.token_hex(32)
    if len(key.strip()) >= MIN_SECRET:
        return key
    if key:
        raise RuntimeError(f"SECRET_KEY is too short: use at least {MIN_SECRET} random characters, for example the "
                           "output of `python -c \"import secrets; print(secrets.token_hex(32))\"`.")
    raise RuntimeError("SECRET_KEY is not set. Set it to a long random string (for example the output of "
                       "`python -c \"import secrets; print(secrets.token_hex(32))\"`) and keep it the same across "
                       "restarts, or every sign-in ends when the board restarts.")


def cookie_secure():
    """Send the session cookie over HTTPS only, unless PUBLIC_URL says plain http (or a local copy has no PUBLIC_URL)."""
    public = os.environ.get("PUBLIC_URL", "")
    if not public:
        return not local_copy()
    return not public.lower().startswith("http://")


def configure(app):
    """Session cookie settings and the sign-in routes. Raises at start-up when SECRET_KEY is missing."""
    app.secret_key = secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",  # Lax, so GitHub's redirect back
                      SESSION_COOKIE_SECURE=cookie_secure(),                       # carries the state
                      PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
                      SESSION_REFRESH_EACH_REQUEST=False)  # the 30 days count from sign-in, not from the last visit
    app.register_blueprint(bp)


def _client():
    """The OAuth app's id and secret, or None when either is not set."""
    cid, secret = os.environ.get("GITHUB_CLIENT_ID", ""), os.environ.get("GITHUB_CLIENT_SECRET", "")
    return (cid, secret) if cid and secret else None


def _callback_url():
    public = os.environ.get("PUBLIC_URL", "").rstrip("/")
    return public + "/auth/callback" if public else url_for("auth.callback", _external=True)


def _failed(why, status=400):
    """The sign-in did not work: a plain page with a way to try again. Nothing is stored."""
    return render_template("signin.html", kind="failed", why=why), status


@bp.get("/login")
def login():
    """The sign-in page, or a note saying sign-in is not set up yet."""
    if team_login(current_login()):
        return redirect(url_for("index"))
    if not _client():
        return render_template("signin.html", kind="setup", local=local_copy()), 503
    return render_template("signin.html", kind="login")


@bp.get("/login/github")
def login_github():
    """Off to GitHub to sign in, with a fresh state to check when GitHub sends the person back."""
    client = _client()
    if not client:
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    query = urlencode({"client_id": client[0], "redirect_uri": _callback_url(), "state": state,
                       "allow_signup": "false"})  # no scope: the public profile is all the board reads
    return redirect(f"{AUTHORIZE_URL}?{query}")


def _github_login(client, code):
    """Swap the code for a token, read the login with it, and drop the token. Raises on anything unexpected."""
    r = requests.post(TOKEN_URL, headers={"Accept": "application/json"}, timeout=TIMEOUT,
                      data={"client_id": client[0], "client_secret": client[1], "code": code,
                            "redirect_uri": _callback_url()})
    r.raise_for_status()
    body = r.json()
    token = body.get("access_token") if isinstance(body, dict) else None  # a bad code is HTTP 200 with an "error"
    if not isinstance(token, str) or not token:
        raise ValueError("no access token")
    try:
        u = requests.get(USER_URL, timeout=TIMEOUT, headers={"Authorization": f"Bearer {token}",
                                                             "Accept": "application/vnd.github+json"})
        u.raise_for_status()
        body = u.json()
        login = body.get("login") if isinstance(body, dict) else None
        gid = body.get("id") if isinstance(body, dict) else None
    finally:
        del token  # used for this one read only; never stored, never logged
    if not isinstance(login, str) or not _LOGIN.fullmatch(login):
        raise ValueError("no login")
    return login, gid


@bp.get("/auth/callback")
def callback():
    """GitHub sends the person back here. Check the state (once only), find their login, and keep just that."""
    expected = session.pop("oauth_state", None)  # used up whatever happens next, so a state works once
    given = request.args.get("state", "")
    if request.args.get("error"):
        return _failed("GitHub did not sign you in (you may have pressed Cancel).")
    if not isinstance(expected, str) or not expected or not given \
            or not hmac.compare_digest(expected.encode(), given.encode()):  # bytes: a str with non-ASCII would raise
        return _failed("This sign-in link has expired or was already used.")
    client, code = _client(), request.args.get("code", "")
    if not client or not code:
        return _failed("GitHub did not send a sign-in code.")
    try:
        login, gid = _github_login(client, code)
    except (requests.RequestException, ValueError) as e:
        log.warning("GitHub sign-in failed (%s)", type(e).__name__)  # the message could hold a code or token
        return _failed("GitHub could not confirm who you are just now.", 502)
    member = team_login(login)
    if member and _IDS.get(member) is not None and gid != _IDS[member]:
        # Someone took over a login a team member gave up: same name, different GitHub account.
        log.warning("Sign-in as %s refused: GitHub account number does not match pomodo.toml", member)
        return _failed("This GitHub account is not the one pomodo.toml names for that login.", 403)
    session.clear()
    session["login"] = member or login  # the config's spelling for the team; GitHub's for anyone else
    session.permanent = True  # 30 days from now, then sign in again
    log.info("Signed in: %s", session["login"])
    return redirect(url_for("index"))


@bp.post("/logout")
def logout():
    """Sign out: forget the session and go back to the sign-in page."""
    if request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":  # another site cannot sign you out
        return render_template("signin.html", kind="failed", why="That sign-out came from another site."), 403
    session.clear()
    return redirect(url_for("auth.login"), 303)
