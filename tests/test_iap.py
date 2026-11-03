"""auth.mode = "iap": the board takes who is signed in from Google's Identity-Aware Proxy. Tokens are signed for real
(see test_identity.py); nothing here reaches the network."""
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

import auth
import main
from github import Cache
from test_auth import JSON, _raw
from test_identity import AUD, _token, keys  # noqa: F401  (keys is a fixture)

HEADER = "X-Goog-IAP-JWT-Assertion"


@pytest.fixture
def app(monkeypatch, keys):  # noqa: F811
    """The board in iap mode, with ada-demo reachable as ada@example.com. (The mode is read when the module loads, so
    the tests switch it on the loaded modules; start-up and routes are checked in a fresh process below.)"""
    monkeypatch.setattr(auth, "CONFIG", dataclasses.replace(auth.CONFIG, auth_mode="iap"))
    monkeypatch.setitem(main.app.jinja_env.globals, "settings", auth.CONFIG)
    monkeypatch.setattr(auth, "_EMAILS", {"ada@example.com": "ada-demo"})
    monkeypatch.setattr(main, "cache", Cache(_raw, ttl=300, spawn=lambda f: f()))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    writes = []
    monkeypatch.setattr(main, "set_assignee", lambda *a: writes.append(("assign", *a)) or [])
    monkeypatch.setattr(main, "set_label", lambda *a: writes.append(("label", *a)))
    monkeypatch.setattr(main, "add_comment", lambda *a: writes.append(("comment", *a)))
    return main.app.test_client(), writes


def test_the_team_member_with_the_verified_email_sees_the_board(app):
    client, _ = app
    r = client.get("/", headers={HEADER: _token(email="ADA@example.com")})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Hi Ada!</strong>" in html and "Signed in as <strong>Ada</strong>" in html
    assert "/logout" not in html and "Sign out" not in html
    assert client.get("/api/board.json", headers={HEADER: _token()}).status_code == 200


def test_an_email_not_on_the_team_gets_the_not_on_this_team_page(app):
    client, _ = app
    r = client.get("/", headers={HEADER: _token(email="stranger@example.com")})
    html = r.get_data(as_text=True)
    assert r.status_code == 403 and "You're not on this team." in html and "stranger@example.com" in html
    assert "/logout" not in html
    r = client.get("/api/board.json", headers={HEADER: _token(email="stranger@example.com")})
    assert r.status_code == 403 and "is not on this board's team" in r.get_json()["error"]


def test_no_token_the_wrong_audience_or_only_the_unsigned_header_gets_nobody_in(app):
    client, writes = app
    unsigned = {"X-Goog-Authenticated-User-Email": "accounts.google.com:ada@example.com"}
    for headers in ({}, unsigned, {HEADER: "not.a.token"}, {HEADER: _token(aud="/projects/1/other"), **unsigned}):
        r = client.get("/", headers=headers)
        assert r.status_code == 401 and "Open the board through Google sign-in." in r.get_data(as_text=True)
        assert "issues" not in client.get("/api/board.json", headers=headers).get_data(as_text=True)
        assert client.get("/api/board.json", headers=headers).status_code == 401
        assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers={**JSON, **headers}).status_code == 401
    assert writes == []


def test_its_mine_and_checked_in_the_app_act_as_the_mapped_login(app):
    client, writes = app
    h = {**JSON, HEADER: _token()}
    r = client.post("/api/issues/2/assignee", json={"assigned": True}, headers=h)
    assert r.status_code == 200 and writes == [("assign", "test-token", "test-org/test-repo", 2, "ada-demo", True)]


def test_dev_user_still_works_on_a_local_copy_and_never_where_deployed(app, monkeypatch):
    client, _ = app
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setenv("DEV_USER", "ada-demo")
    assert client.get("/").status_code == 200  # no IAP header at all
    monkeypatch.setenv("K_SERVICE", "pomodo")
    assert client.get("/").status_code == 401


def _fresh(env, code="import main"):
    root = Path(main.__file__).parent
    clean = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DEV_FIXTURE", "DEMO", "K_SERVICE",
                                                                "POMODO_PRODUCTION", "IAP_AUDIENCE")}
    return subprocess.run([sys.executable, "-c", code], cwd=root, env={**clean, **env}, capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def iap_toml(tmp_path):
    text = (Path(__file__).parent / "pomodo.test.toml").read_text()
    for n, who in enumerate(("ada", "kofi", "mei")):
        text = text.replace(f'login = "{who}-demo"', f'login = "{who}-demo"\nemail = "{who}@example.com"')
    path = tmp_path / "pomodo.toml"
    path.write_text(text + '\n[auth]\nmode = "iap"\n')
    return {"POMODO_CONFIG": str(path)}


@pytest.mark.parametrize("env, starts", [
    ({"K_SERVICE": "pomodo"}, False),
    ({"POMODO_PRODUCTION": "1"}, False),
    ({"K_SERVICE": "pomodo", "IAP_AUDIENCE": ""}, False),
    ({"K_SERVICE": "pomodo", "IAP_AUDIENCE": AUD}, True),   # no SECRET_KEY needed: there is no session
    ({"DEMO": "1"}, True),
    ({}, True),
])
def test_a_deployed_iap_board_will_not_start_without_its_audience(iap_toml, env, starts):
    r = _fresh({**iap_toml, **env})
    assert (r.returncode == 0) == starts, r.stderr
    if not starts:
        assert "IAP_AUDIENCE is not set" in r.stderr


def test_in_iap_mode_the_github_routes_are_not_offered(iap_toml):
    code = ("import main; r = sorted(x.rule for x in main.app.url_map.iter_rules()); print(r); "
            "c = main.app.test_client(); print([c.get(p).status_code for p in ('/login', '/login/github', '/auth/callback')])"
            "; print(c.post('/logout').status_code)")
    r = _fresh({**iap_toml, "DEMO": "1"}, code)
    assert r.returncode == 0, r.stderr
    rules, gets, logout = r.stdout.splitlines()
    assert "/login" not in rules and "/auth/callback" not in rules and "/logout" not in rules
    assert gets == "[401, 401, 401]" and logout == "401"  # never a sign-in page, and no 404 that would show the route exists


def test_in_github_mode_nothing_changes():
    assert auth.iap() is False and "/login/github" in {r.rule for r in main.app.url_map.iter_rules()}
