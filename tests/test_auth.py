"""Sign in with GitHub, and the gate: only the team sees the board. GitHub is faked; nothing here reaches the network."""
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import auth
import main
from github import Cache

JSON = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
TOKEN = "gho_user-token-that-must-never-be-kept"


def sign_in(client, login):
    """Put this login in the client's session, as a finished GitHub sign-in would (None signs out)."""
    with client.session_transaction() as s:
        s.clear()
        if login:
            s["login"] = login


def _session(client):
    with client.session_transaction() as s:
        return dict(s)


def _raw():
    from test_board import _node
    return {"issues": [_node(2), _node(3, assignees=["ada-demo"])], "pullRequests": []}


class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def github(monkeypatch):
    """GitHub's token and user endpoints, faked. Set `token` or `user` to change what they answer; `sent` logs calls."""
    state = {"token": _Reply(200, {"access_token": TOKEN, "token_type": "bearer", "scope": ""}),
             "user": _Reply(200, {"login": "ada-demo", "id": 1}), "sent": []}

    def post(url, **kw):
        state["sent"].append(("POST", url, kw))
        if isinstance(state["token"], Exception):
            raise state["token"]
        return state["token"]

    def get(url, **kw):
        state["sent"].append(("GET", url, kw))
        if isinstance(state["user"], Exception):
            raise state["user"]
        return state["user"]

    monkeypatch.setattr(auth.requests, "post", post)
    monkeypatch.setattr(auth.requests, "get", get)
    return state


@pytest.fixture
def app(monkeypatch, github):
    monkeypatch.setattr(main, "cache", Cache(_raw, ttl=300, spawn=lambda f: f()))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PUBLIC_URL", "https://board.example.org/")
    writes = []
    monkeypatch.setattr(main, "set_assignee", lambda *a: writes.append(("assign", *a)) or [])
    monkeypatch.setattr(main, "set_label", lambda *a: writes.append(("label", *a)))
    monkeypatch.setattr(main, "add_comment", lambda *a: writes.append(("comment", *a)))
    return main.app.test_client(), writes


def _start(client):
    """Begin a sign-in; returns the state the board sent to GitHub."""
    r = client.get("/login/github")
    assert r.status_code == 302
    return parse_qs(urlsplit(r.headers["Location"]).query)["state"][0]


# ---------- the gate ----------

def test_a_team_member_sees_the_board(app):
    client, _ = app
    sign_in(client, "ada-demo")
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Hi Ada!</strong>" in html
    assert 'class="signout" method="post" action="/logout"' in html and "Signed in as <strong>Ada</strong>" in html
    assert client.get("/api/board.json").status_code == 200


def test_signed_out_pages_go_to_sign_in_and_the_api_says_401(app):
    client, writes = app
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"] == "/login"
    assert client.get("/?order=created").headers["Location"] == "/login"  # no next= to send anyone elsewhere later
    r = client.get("/api/board.json")
    assert r.status_code == 401 and r.get_json() == {"error": "Sign in to use the board."}
    assert "issues" not in r.get_data(as_text=True)
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 401
    assert client.post("/api/issues/3/checked", json={"checked": True}, headers=JSON).status_code == 401
    assert client.post("/api/issues/2/assignee", data={"assigned": "true"}).status_code == 401  # a cross-site form
    assert writes == []
    assert client.get("/no-such-page").status_code == 302  # an unknown address is not an open one


def test_every_route_but_the_open_ones_needs_a_team_member(app):
    """Walk every route the app has, so a route added later is covered without anyone remembering to add it here."""
    client, writes = app
    assert main.OPEN == {"auth.login", "auth.login_github", "auth.callback", "auth.logout", "static", "healthz"}
    closed = 0
    for rule in main.app.url_map.iter_rules():
        if rule.endpoint in main.OPEN:
            continue
        url = rule.build({a: 1 for a in rule.arguments}, append_unknown=False)[1]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            sign_in(client, None)
            r = client.open(url, method=method, json={}, headers=JSON)
            assert r.status_code in (302, 401), (rule.rule, method, r.status_code)
            sign_in(client, "someone-new")
            r = client.open(url, method=method, json={}, headers=JSON)
            assert r.status_code == 403, (rule.rule, method, r.status_code)
            closed += 1
    assert closed >= 4 and writes == []  # /, /api/board.json and the two write endpoints, at least


def test_the_open_routes_work_signed_out(app):
    client, _ = app
    r = client.get("/login")
    assert r.status_code == 200 and 'href="/login/github"' in r.get_data(as_text=True)
    assert "Sign in with GitHub" in r.get_data(as_text=True)
    assert "test-org" not in r.get_data(as_text=True) and "Ada" not in r.get_data(as_text=True)  # names no repo or team
    assert client.get("/login/github").status_code == 302
    assert client.get("/auth/callback").status_code == 400  # a sign-in page, not a loop back to /login
    assert client.get("/static/board.css").status_code == 200
    assert client.get("/healthz").get_data(as_text=True) == "ok"
    assert client.post("/logout").status_code == 303


def test_signed_in_but_not_on_the_team(app):
    client, writes = app
    sign_in(client, "someone-new")
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 403 and "You're not on this team." in html and "<strong>someone-new</strong>" in html
    assert '<form class="hero-ctas" method="post" action="/logout">' in html and "Sign out</button>" in html
    assert "Item 2" not in html and "test-org" not in html  # nothing from the board
    r = client.get("/api/board.json")
    assert r.status_code == 403 and r.get_json() == {"error": "someone-new is not on this board's team."}
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 403
    assert client.post("/api/issues/3/checked", json={"checked": True}, headers=JSON).status_code == 403
    assert writes == []
    assert client.post("/logout", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 303  # the way out works
    assert _session(client) == {}


def test_a_login_matches_the_team_whatever_its_case(app):
    client, writes = app
    sign_in(client, "ADA-DEMO")
    html = client.get("/").get_data(as_text=True)
    assert "Hi Ada!</strong> 1 open issue is assigned to you" in html
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 200
    assert writes[0][4] == "ada-demo"  # GitHub is sent the config's spelling


# ---------- signing in ----------

def test_sign_in_goes_to_github_with_a_fresh_state_and_no_scope(app):
    client, _ = app
    r = client.get("/login/github")
    url = urlsplit(r.headers["Location"])
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://github.com/login/oauth/authorize"
    assert q == {"client_id": ["client-id"], "redirect_uri": ["https://board.example.org/auth/callback"],
                 "state": [q["state"][0]], "allow_signup": ["false"]}  # and no scope
    assert len(q["state"][0]) >= 40 and _session(client)["oauth_state"] == q["state"][0]
    assert _start(client) != q["state"][0]


def test_without_public_url_the_callback_is_on_the_requests_own_host(app, monkeypatch):
    client, _ = app
    monkeypatch.delenv("PUBLIC_URL")
    q = parse_qs(urlsplit(client.get("/login/github").headers["Location"]).query)
    assert q["redirect_uri"] == ["http://localhost/auth/callback"]


def test_a_good_callback_keeps_only_the_login(app, github, caplog):
    client, _ = app
    caplog.set_level(logging.DEBUG)
    state = _start(client)
    r = client.get(f"/auth/callback?code=the-code&state={state}")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert _session(client) == {"login": "ada-demo", "_permanent": True}  # no token, no state
    (m1, url1, kw1), (m2, url2, kw2) = github["sent"]
    assert (m1, url1) == ("POST", "https://github.com/login/oauth/access_token")
    assert kw1["headers"]["Accept"] == "application/json" and kw1["timeout"]
    assert kw1["data"] == {"client_id": "client-id", "client_secret": "client-secret", "code": "the-code",
                           "redirect_uri": "https://board.example.org/auth/callback"}
    assert (m2, url2) == ("GET", "https://api.github.com/user") and kw2["timeout"]
    assert kw2["headers"]["Authorization"] == f"Bearer {TOKEN}"
    cookie = r.headers["Set-Cookie"]
    assert TOKEN not in cookie and TOKEN not in caplog.text and "client-secret" not in caplog.text
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie and "Expires=" in cookie
    assert client.get("/").status_code == 200


def test_a_member_signing_in_with_other_capitals_is_kept_in_the_configs_spelling(app, github):
    client, _ = app
    github["user"] = _Reply(200, {"login": "Kofi-Demo"})
    client.get(f"/auth/callback?code=c&state={_start(client)}")
    assert _session(client) == {"login": "kofi-demo", "_permanent": True}


def test_someone_not_on_the_team_can_sign_in_but_sees_only_the_not_on_team_page(app, github):
    client, _ = app
    github["user"] = _Reply(200, {"login": "Someone-New"})
    client.get(f"/auth/callback?code=c&state={_start(client)}")
    assert _session(client)["login"] == "Someone-New"
    assert client.get("/").status_code == 403


def test_signing_in_replaces_whatever_the_session_held(app):
    client, _ = app
    state = _start(client)
    with client.session_transaction() as s:
        s["login"], s["other"] = "someone-new", "x"
    client.get(f"/auth/callback?code=c&state={state}")
    assert _session(client) == {"login": "ada-demo", "_permanent": True}


@pytest.mark.parametrize("query", [
    "code=c&state=wrong",          # someone else's state
    "code=c",                      # none sent back
    "code=c&state=",
    "code=c&state=%C3%A9t%C3%A9",  # not ASCII: must be a refusal, not a crash
])
def test_a_callback_with_the_wrong_state_is_refused_and_stores_nothing(app, github, query):
    client, _ = app
    _start(client)
    r = client.get(f"/auth/callback?{query}")
    assert r.status_code == 400 and "That sign-in didn't work." in r.get_data(as_text=True)
    assert _session(client) == {} and github["sent"] == []


def test_a_callback_with_no_sign_in_started_is_refused(app, github):
    client, _ = app
    r = client.get("/auth/callback?code=c&state=anything")
    assert r.status_code == 400 and _session(client) == {} and github["sent"] == []


def test_a_state_works_only_once(app, github):
    client, _ = app
    state = _start(client)
    assert client.get(f"/auth/callback?code=c&state={state}").status_code == 302
    sign_in(client, None)
    r = client.get(f"/auth/callback?code=c&state={state}")
    assert r.status_code == 400 and _session(client) == {} and len(github["sent"]) == 2  # no second exchange


def test_a_failed_check_still_uses_up_the_state(app):
    client, _ = app
    state = _start(client)
    client.get("/auth/callback?code=c&state=wrong")
    assert client.get(f"/auth/callback?code=c&state={state}").status_code == 400


def test_github_saying_no_is_a_plain_page(app, github):
    client, _ = app
    state = _start(client)
    r = client.get(f"/auth/callback?error=access_denied&state={state}")
    assert r.status_code == 400 and "Cancel" in r.get_data(as_text=True)
    assert _session(client) == {} and github["sent"] == []


@pytest.mark.parametrize("token, user", [
    (_Reply(200, {"error": "bad_verification_code"}), None),  # GitHub's "no" comes with HTTP 200
    (_Reply(200, {"access_token": ""}), None),
    (_Reply(200, []), None),                                   # JSON, but not the shape GitHub sends
    (None, _Reply(200, [])),
    (_Reply(500, {}), None),
    (_Reply(200, ValueError("not JSON")), None),
    (requests.ConnectionError("down"), None),
    (None, _Reply(401, {"message": "Bad credentials"})),
    (None, _Reply(200, {"login": None})),
    (None, _Reply(200, {"login": "<script>"})),
    (None, requests.Timeout("slow")),
])
def test_a_failed_exchange_is_a_plain_page_and_stores_nothing(app, github, caplog, token, user):
    client, _ = app
    if token is not None:
        github["token"] = token
    if user is not None:
        github["user"] = user
    r = client.get(f"/auth/callback?code=c&state={_start(client)}")
    assert r.status_code == 502 and "GitHub could not confirm who you are just now." in r.get_data(as_text=True)
    assert _session(client) == {} and TOKEN not in caplog.text
    assert client.get("/").status_code == 302


def test_a_callback_without_a_code_is_refused(app, github):
    client, _ = app
    r = client.get(f"/auth/callback?state={_start(client)}")
    assert r.status_code == 400 and _session(client) == {} and github["sent"] == []


def test_sign_in_not_set_up_says_which_settings_are_missing(app, monkeypatch):
    client, _ = app
    monkeypatch.delenv("GITHUB_CLIENT_SECRET")
    r = client.get("/login")
    html = r.get_data(as_text=True)
    assert r.status_code == 503 and "Sign-in isn't set up yet." in html
    assert "GITHUB_CLIENT_ID" in html and "GITHUB_CLIENT_SECRET" in html and "DEV_USER" not in html
    r = client.get("/login/github")
    assert r.status_code == 302 and r.headers["Location"] == "/login"
    monkeypatch.setenv("DEV_FIXTURE", "dev/raw.json")
    assert "DEV_USER" in client.get("/login").get_data(as_text=True)  # a local copy can skip it


def test_the_sign_in_page_sends_a_signed_in_member_to_the_board(app):
    client, _ = app
    sign_in(client, "mei-demo")
    assert client.get("/login").headers["Location"] == "/"


# ---------- signing out ----------

def test_sign_out_clears_the_session(app):
    client, _ = app
    sign_in(client, "ada-demo")
    r = client.post("/logout", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 303 and r.headers["Location"] == "/login"
    assert _session(client) == {}
    assert client.get("/").status_code == 302
    sign_in(client, "ada-demo")
    assert client.get("/logout").status_code == 405  # a link or an image cannot sign anyone out
    assert _session(client) == {"login": "ada-demo"}


def test_another_site_cannot_sign_you_out(app):
    client, _ = app
    sign_in(client, "ada-demo")
    assert client.post("/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _session(client) == {"login": "ada-demo"}


# ---------- local copies and DEV_USER ----------

@pytest.mark.parametrize("env, signed_in", [
    ({"DEV_FIXTURE": "dev/raw.json"}, True),
    ({"DEMO": "1"}, True),
    ({}, False),                                                           # live GitHub: real writes
    ({"DEMO": "yes"}, False),                                              # only DEMO=1 is a local copy
    ({"DEV_FIXTURE": "dev/raw.json", "K_SERVICE": "pomodo"}, False),       # deployed on Cloud Run
    ({"DEV_FIXTURE": "dev/raw.json", "POMODO_PRODUCTION": "1"}, False),    # deployed anywhere else
    ({"DEMO": "1", "POMODO_PRODUCTION": "1"}, False),
])
def test_dev_user_signs_in_only_on_a_local_copy_that_is_not_deployed(app, monkeypatch, env, signed_in):
    client, _ = app
    monkeypatch.setenv("DEV_USER", "Ada-Demo")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    r = client.get("/")
    if signed_in:
        assert r.status_code == 200 and "Hi Ada!</strong>" in r.get_data(as_text=True)
    else:
        assert r.status_code == 302 and r.headers["Location"] == "/login"
    assert auth.dev_user() == ("Ada-Demo" if signed_in else None)


def test_dev_user_is_still_checked_against_the_team(app, monkeypatch):
    client, _ = app
    monkeypatch.setenv("DEV_FIXTURE", "dev/raw.json")
    monkeypatch.setenv("DEV_USER", "someone-new")
    assert client.get("/").status_code == 403


def test_local_copy_mode_never_writes_to_github(app, monkeypatch):
    client, writes = app
    monkeypatch.setenv("DEMO", "1")
    sign_in(client, "ada-demo")
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 200
    assert writes == []


def test_demo_mode_never_reads_the_real_repo_either(monkeypatch):
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(main, "_DEMO", {"raw": None})
    monkeypatch.setattr(main, "fetch_repo", lambda *a: pytest.fail("read GitHub in DEMO mode"))
    raw = main._load()
    assert raw["issues"] and raw["pullRequests"]  # made-up data, in the shape of GitHub's
    assert main._load() is raw  # the same read every time, so the board's own changes stay in it


# ---------- the secret key and the cookie ----------

def test_secret_key_is_required_outside_a_local_copy(monkeypatch):
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        auth.secret_key()
    monkeypatch.setenv("DEMO", "1")
    assert len(auth.secret_key()) == 64 and auth.secret_key() != auth.secret_key()  # random per start
    monkeypatch.setenv("SECRET_KEY", "fixed")
    assert auth.secret_key() == "fixed"


@pytest.mark.parametrize("env, starts", [({}, False), ({"DEV_FIXTURE": "dev/raw.json"}, True),
                                         ({"SECRET_KEY": "set"}, False), ({"SECRET_KEY": "k" * 32}, True)])
def test_the_board_will_not_start_without_a_secret_key(env, starts):
    root = Path(main.__file__).parent
    clean = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DEV_FIXTURE", "DEMO", "K_SERVICE")}
    r = subprocess.run([sys.executable, "-c", "import main"], cwd=root, env={**clean, **env},
                       capture_output=True, text=True, timeout=60)
    assert (r.returncode == 0) == starts, r.stderr
    if not starts:
        assert "SECRET_KEY is not set" in r.stderr or "SECRET_KEY is too short" in r.stderr


@pytest.mark.parametrize("env, secure", [
    ({}, True),
    ({"PUBLIC_URL": "https://board.example.org"}, True),
    ({"PUBLIC_URL": "http://localhost:8080"}, False),
    ({"PUBLIC_URL": "HTTP://10.0.0.5"}, False),
    ({"DEV_FIXTURE": "dev/raw.json"}, False),                                  # a local copy on localhost
    ({"DEV_FIXTURE": "dev/raw.json", "PUBLIC_URL": "https://x.example"}, True),
])
def test_the_cookie_is_https_only_unless_the_board_is_on_plain_http(monkeypatch, env, secure):
    for k in ("PUBLIC_URL", "DEV_FIXTURE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert auth.cookie_secure() is secure


def test_the_session_lasts_thirty_days_and_scripts_cannot_read_it():
    c = main.app.config
    assert c["SESSION_COOKIE_HTTPONLY"] is True and c["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert c["SESSION_COOKIE_SECURE"] is True and c["PERMANENT_SESSION_LIFETIME"].days == 30


def test_a_short_or_blank_secret_key_is_refused_once_deployed(monkeypatch):
    for key in ("changeme", " " * 40, "x" * 31):
        monkeypatch.setenv("SECRET_KEY", key)
        with pytest.raises(RuntimeError, match="too short"):
            auth.secret_key()
    monkeypatch.setenv("SECRET_KEY", "k" * 32)
    assert auth.secret_key() == "k" * 32


def test_the_thirty_days_count_from_sign_in_not_from_each_visit(app):
    client, _ = app
    sign_in(client, "ada-demo")
    r = client.get("/")
    assert r.status_code == 200 and "Set-Cookie" not in r.headers
    assert main.app.config["SESSION_REFRESH_EACH_REQUEST"] is False


def test_icons_are_cached_by_the_browser_only(app):
    client, _ = app
    r = client.get("/static/favicon.svg")
    assert r.headers["Cache-Control"] == "private, max-age=86400"


# ---------- a login that changed hands ----------

def test_a_member_with_an_account_number_must_sign_in_with_that_account(app, github, monkeypatch):
    client, _ = app
    monkeypatch.setitem(auth._IDS, "ada-demo", 1)
    github["user"] = _Reply(200, {"login": "ada-demo", "id": 999})  # someone who registered Ada's old login
    r = client.get(f"/auth/callback?code=c&state={_start(client)}")
    assert r.status_code == 403 and "login" not in _session(client)
    github["user"] = _Reply(200, {"login": "ada-demo", "id": 1})
    client.get(f"/auth/callback?code=c&state={_start(client)}")
    assert _session(client)["login"] == "ada-demo"


def test_a_login_that_only_looks_like_a_member_is_not_one(app, github):
    client, _ = app
    assert auth.team_login("\u212aofi-demo") is None  # the Kelvin sign lower-cases to a plain k
    for login in ("\u212aofi-demo", "kofi-demo\n"):
        github["user"] = _Reply(200, {"login": login})
        client.get(f"/auth/callback?code=c&state={_start(client)}")
        assert "login" not in _session(client)
