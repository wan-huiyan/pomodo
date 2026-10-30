"""The "Board updated" pill: the page knows which GitHub read it was drawn from and asks whether there is a newer one."""
import pathlib
import re
import threading

import pytest

import main
from github import Cache
from test_auth import sign_in
from test_board import _node

SAME = {"Sec-Fetch-Site": "same-origin"}
ROOT = pathlib.Path(main.__file__).resolve().parent


def _raw():
    return {"issues": [_node(2), _node(3, assignees=["ada-demo"])], "pullRequests": [_node(30, kind="pr")]}


def _client(monkeypatch, cache):
    monkeypatch.setattr(main, "cache", cache)
    monkeypatch.delenv("DEV_FIXTURE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(main, "set_assignee", lambda token, repo, number, login, assigned: [login] if assigned else [])
    client = main.app.test_client()
    sign_in(client, "ada-demo")
    return client


@pytest.fixture
def app(monkeypatch):
    return _client(monkeypatch, Cache(_raw, ttl=300, spawn=lambda f: f()))


def _page_version(client):
    return re.search(r'<body data-version="([0-9a-f]+)"', client.get("/").get_data(as_text=True)).group(1)


def test_the_page_and_the_version_endpoint_agree_until_the_board_changes(app):
    drawn = _page_version(app)
    r = app.get("/api/version")
    assert r.status_code == 200 and r.get_json() == {"version": drawn}
    assert r.headers["Cache-Control"] == "no-store"
    taken = app.post("/api/issues/2/assignee", json={"assigned": True}, headers=SAME).get_json()
    # the reply carries the new version, so the page that made the change can adopt it and not tell itself
    assert taken["version"] != drawn and app.get("/api/version").get_json() == {"version": taken["version"]}
    assert _page_version(app) == taken["version"]


def test_only_the_team_can_ask_for_the_version(app):
    sign_in(app, None)
    assert app.get("/api/version").status_code == 401
    sign_in(app, "someone-else")
    assert app.get("/api/version").status_code == 403


def test_the_version_is_the_same_for_the_same_data_and_changes_with_it():
    assert main.fingerprint(_raw()) == main.fingerprint(_raw())
    changed = _raw()
    changed["issues"][0]["state"] = "CLOSED"
    assert main.fingerprint(changed) != main.fingerprint(_raw())


def test_your_own_take_still_matches_once_github_has_it(monkeypatch):
    """GitHub's copy of a take also moves updatedAt; the version must not change for that, or the page would offer
    to show the reader their own click a couple of minutes later."""
    t, github = [0.0], [_raw()]
    client = _client(monkeypatch, Cache(lambda: github[0], ttl=300, clock=lambda: t[0], spawn=lambda f: f()))
    client.get("/")
    adopted = client.post("/api/issues/2/assignee", json={"assigned": True}, headers=SAME).get_json()["version"]
    after = _raw()  # what GitHub returns on the next read: the assignee, a new updatedAt and a new comment
    after["issues"][0]["assignees"]["nodes"] = [{"login": "ada-demo", "avatarUrl": ""}]
    after["issues"][0]["updatedAt"] = "2026-10-30T12:00:00Z"
    after["issues"][0]["comments"] = {"totalCount": 1, "nodes": []}
    github[0], t[0] = after, 301
    client.get("/api/version")  # starts the read (inline here)
    assert client.get("/api/version").get_json() == {"version": adopted}
    # a change of status is still seen
    after["issues"][1]["state"] = "CLOSED"
    t[0] = 700
    client.get("/api/version")
    assert client.get("/api/version").get_json()["version"] != adopted


def test_the_version_endpoint_waits_for_a_read_under_way(monkeypatch):
    """Where a host gives a background read CPU only while a request is open, the poll holds one open for it."""
    reads, go = [], threading.Event()

    def loader():
        reads.append(1)
        if len(reads) > 1:
            go.wait(5)
            return {"issues": [_node(9)], "pullRequests": []}
        return _raw()

    t = [0.0]
    client = _client(monkeypatch, Cache(loader, ttl=300, clock=lambda: t[0]))
    first = client.get("/api/version").get_json()["version"]
    t[0] = 301  # the copy is old: this poll starts a read in the background and waits for it
    threading.Timer(0.2, go.set).start()
    after = client.get("/api/version").get_json()["version"]
    assert len(reads) == 2 and after != first


def test_the_page_polls_only_while_visible_and_redraws_only_on_a_click():
    js = (ROOT / "static" / "board.js").read_text(encoding="utf-8")
    live = js[js.index("function initLive"):]
    assert "doc.hidden" in live and "setInterval(check, 60000)" in live and '"visibilitychange"' in live
    assert live.count("location.reload()") == 1 and 'addEventListener("click"' in live
    html = (ROOT / "templates" / "board.html").read_text(encoding="utf-8")
    assert '<div class="updated" role="status" aria-live="polite"></div>' in html
