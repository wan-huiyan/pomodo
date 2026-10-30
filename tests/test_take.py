"""The tick: assign or unassign the signed-in person on GitHub."""
import json

import pytest

import main
from github import Cache, GitHubError, set_assignee
from test_auth import sign_in
from test_board import _node

JSON = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def _raw():
    return {"issues": [_node(2), _node(3, assignees=["ada-demo"]),
                       _node(4, state="CLOSED", closed="2026-10-27T09:00:00Z")],
            "pullRequests": [_node(30, kind="pr")]}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(main, "cache", Cache(_raw, ttl=300, spawn=lambda f: f()))
    monkeypatch.delenv("DEV_FIXTURE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    calls = []

    def fake_set(token, repo, number, login, assigned):
        calls.append((number, login, assigned))
        return [login] if assigned else []

    monkeypatch.setattr(main, "set_assignee", fake_set)
    client = main.app.test_client()
    sign_in(client, "ada-demo")
    return client, calls, monkeypatch


def test_tick_assigns_on_github_and_the_board_shows_it_at_once(app):
    client, calls, _ = app
    r = client.post("/api/issues/2/assignee", json={"assigned": True}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.get_json() == {"number": 2, "assigned": True, "assignees": ["ada-demo"]}
    assert calls == [(2, "ada-demo", True)]
    board = main.current_board("ada-demo")
    mine = next(p for p in board["people"] if p["login"] == "ada-demo")["items"]
    assert 2 in [i["number"] for i in mine]  # moved into Ada's card without waiting for a refresh


def test_untick_unassigns(app):
    client, calls, _ = app
    r = client.post("/api/issues/3/assignee", json={"assigned": False}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and calls == [(3, "ada-demo", False)]


@pytest.mark.parametrize("headers, body", [
    ({"Sec-Fetch-Site": "cross-site"}, {"assigned": True}),  # another site's page
    ({"Sec-Fetch-Site": "same-site"}, {"assigned": True}),
])
def test_requests_from_other_pages_are_refused(app, headers, body):
    client, calls, _ = app
    assert client.post("/api/issues/2/assignee", json=body, headers=headers).status_code == 403
    assert calls == []


def test_a_form_post_is_refused(app):
    client, calls, _ = app
    r = client.post("/api/issues/2/assignee", data={"assigned": "true"})  # what a cross-site HTML form can send
    assert r.status_code == 403 and calls == []


def test_only_a_team_member_can_tick(app):
    client, calls, _ = app
    sign_in(client, None)
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 401
    sign_in(client, "someone-new")
    assert client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON).status_code == 403
    assert calls == []


@pytest.mark.parametrize("number", [4, 30, 999])  # closed issue, pull request, unknown
def test_only_open_issues_can_be_ticked(app, number):
    client, calls, _ = app
    assert client.post(f"/api/issues/{number}/assignee", json={"assigned": True}, headers=JSON).status_code == 404
    assert calls == []


@pytest.mark.parametrize("body", [{"assigned": "yes"}, {}, [1], "x", None])
def test_body_must_say_true_or_false(app, body):
    client, calls, _ = app
    r = client.post("/api/issues/2/assignee", data=json.dumps(body), headers=JSON)
    assert r.status_code == 400 and calls == []


def test_github_refusal_is_reported_and_the_board_is_unchanged(app):
    client, _, monkeypatch = app

    def refuse(*a):
        raise GitHubError("GitHub refused the change (HTTP 403)")

    monkeypatch.setattr(main, "set_assignee", refuse)
    r = client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON)
    assert r.status_code == 502 and "HTTP 403" in r.get_json()["error"]
    unclaimed = [i["number"] for i in main.current_board("ada-demo")["unclaimed"]]
    assert 2 in unclaimed


def test_ticks_render_only_for_open_issues_and_only_for_a_known_viewer(app):
    client, _, monkeypatch = app
    html = client.get("/").get_data(as_text=True)
    assert 'class="take" data-number="2"' in html and 'data-number="3" data-on="true"' in html
    assert 'class="take" data-number="4"' not in html and 'class="take" data-number="30"' not in html
    # The spoken name starts with the words on the button, so speech control finds it.
    assert 'aria-label="It&#39;s mine! Assign me #2"' in html and '<span class="take-off">It\'s mine!</span>' in html
    assert 'aria-label="All mine. Give back #3"' in html and 'All mine</span>' in html
    sign_in(client, "someone-new")  # only a team member sees the board, so only they get ticks
    r = client.get("/")
    assert r.status_code == 403 and 'class="take"' not in r.get_data(as_text=True)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Session:
    def __init__(self, resp):
        self.resp, self.sent = resp, []

    def request(self, method, url, **kw):
        self.sent.append((method, url, kw["json"]))
        return self.resp


def test_set_assignee_sends_one_login_and_checks_github_kept_it():
    s = _Session(_Resp(201, {"assignees": [{"login": "ada-demo"}]}))
    assert set_assignee("t", "o/r", 7, "ada-demo", True, session=s) == ["ada-demo"]
    assert s.sent == [("POST", "https://api.github.com/repos/o/r/issues/7/assignees", {"assignees": ["ada-demo"]})]
    with pytest.raises(GitHubError, match="did not add"):  # GitHub skips people it cannot assign, with a 201
        set_assignee("t", "o/r", 7, "ada-demo", True, session=_Session(_Resp(201, {"assignees": []})))
    with pytest.raises(GitHubError, match="HTTP 403"):
        set_assignee("t", "o/r", 7, "ada-demo", False, session=_Session(_Resp(403, {})))


def test_a_tick_survives_a_refresh_that_read_github_just_before_it(app, monkeypatch):
    client, calls, _ = app
    t, reads = [1000.0], []

    def loader():
        reads.append(1)
        snapshot = _raw()  # GitHub as read just before the tick
        if len(reads) == 2:
            r = client.post("/api/issues/2/assignee", json={"assigned": True}, headers=JSON)
            assert r.status_code == 200
        return snapshot

    monkeypatch.setattr(main, "cache", Cache(loader, ttl=300, clock=lambda: t[0], spawn=lambda f: f()))
    main.cache.get()
    t[0] += 301
    main.cache.get()  # the refresh runs now, and the tick lands while it reads
    assert len(reads) == 2 and calls == [(2, "ada-demo", True)]
    raw, _, _ = main.cache.get()
    assert [a["login"] for a in next(n for n in raw["issues"] if n["number"] == 2)["assignees"]["nodes"]] == ["ada-demo"]
    t[0] += 301
    main.cache.get()  # a later read started after the tick, so GitHub's own answer stands
    raw, _, _ = main.cache.get()
    assert next(n for n in raw["issues"] if n["number"] == 2)["assignees"]["nodes"] == []


def test_set_assignee_rejects_a_reply_in_the_wrong_shape():
    with pytest.raises(GitHubError):
        set_assignee("t", "o/r", 7, "ada-demo", True, session=_Session(_Resp(201, ["ada-demo"])))


@pytest.mark.parametrize("viewer, hello", [
    # #3 is the one open issue assigned to ada-demo: the same set as the "Who has what" card
    ("ada-demo", "Hi Ada!</strong> 1 open issue is assigned to you, not counting chores and checklists."),
    ("kofi-demo", "Hi Kofi!</strong> No open issues are assigned to you right now, not counting chores and checklists."),
    ("Kofi-Demo", "Hi Kofi!</strong> No open issues"),  # GitHub logins ignore case
])
def test_the_hello_greets_a_known_viewer_with_their_count(app, viewer, hello):
    client, _, monkeypatch = app
    sign_in(client, viewer)
    html = client.get("/").get_data(as_text=True)
    assert '<a class="hello" href="#people" data-burst>' in html
    assert hello in html


def test_the_hello_counts_several_issues(app):
    client, _, monkeypatch = app
    raw = lambda: {"issues": [_node(2, assignees=["ada-demo"]), _node(3, assignees=["ada-demo"])], "pullRequests": []}  # noqa: E731
    monkeypatch.setattr(main, "cache", Cache(raw, ttl=300, spawn=lambda f: f()))
    assert "2 open issues are assigned to you, not counting chores and checklists." in client.get("/").get_data(as_text=True)


def test_the_you_tag_is_on_the_viewers_own_card_and_nothing_greets_a_stranger(app):
    client, _, monkeypatch = app
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="you-tag"') == 1
    assert 'class="person-name">Ada <span class="you-tag">' in html
    sign_in(client, "someone-new")
    html = client.get("/").get_data(as_text=True)
    assert "someone-new" in html and 'class="hello"' not in html and 'class="you-tag"' not in html


def test_a_login_github_spells_with_other_capitals_is_still_the_same_person(app, monkeypatch):
    """GitHub logins ignore case: GitHub may answer "Ada-Demo" for the config's ada-demo."""
    assert set_assignee("t", "o/r", 7, "ada-demo", True, session=_Session(_Resp(201, {"assignees": [{"login": "Ada-Demo"}]}))) == ["Ada-Demo"]
    client, calls, _ = app
    raw = lambda: {"issues": [_node(2, assignees=["Ada-Demo"])], "pullRequests": []}  # noqa: E731
    monkeypatch.setattr(main, "cache", Cache(raw, ttl=300, spawn=lambda f: f()))
    assert client.post("/api/issues/2/assignee", json={"assigned": False}, headers=JSON).status_code == 200
    raw_now, _, _ = main.cache.get()
    assert raw_now["issues"][0]["assignees"]["nodes"] == []  # the board's copy lets go of "Ada-Demo" too
