"""Checked in the app: a signed-in person ticks a closed issue, and the board labels it human-verified with a comment."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import main
from github import Cache, GitHubError, add_comment, set_label
from test_board import _node
from test_auth import sign_in
from test_take import JSON, _Resp

AGO = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()  # inside Recently done, whatever today is


def _raw():
    fix = _node(129, kind="pr", state="MERGED", closed=AGO, closes=(120, 121))
    return {"issues": [
        _node(120, state="CLOSED", closed=AGO, labels=["human-verified"]),  # already checked
        _node(121, state="CLOSED", closed=AGO),  # closed by #129, not checked yet
        _node(39, state="CLOSED", closed=AGO, reason="COMPLETED"),  # a row of its own
        _node(126, state="CLOSED", closed=AGO, reason="NOT_PLANNED"),  # closed without being done
        _node(127, state="CLOSED", closed=AGO, reason="NOT_PLANNED", labels=["human-verified"]),  # labelled by mistake
        _node(2),  # open
    ], "pullRequests": [fix,
                        _node(140, kind="pr", state="MERGED", closed=AGO, closes=()),  # merged, closes no issue
                        _node(141, kind="pr", state="CLOSED", closed=AGO, closes=()),  # closed without merging
                        _node(142, kind="pr", state="MERGED", closed=AGO, closes=(2,))]}  # names an issue still open


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(main, "cache", Cache(_raw, ttl=300, spawn=lambda f: f()))
    monkeypatch.delenv("DEV_FIXTURE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    calls = []
    monkeypatch.setattr(main, "set_label", lambda token, repo, number, label, on: calls.append(("label", number, label, on)))
    monkeypatch.setattr(main, "add_comment", lambda token, repo, number, body: calls.append(("comment", number, body)))
    client = main.app.test_client()
    sign_in(client, "kofi-demo")
    return client, calls, monkeypatch


def _verified(number):
    raw, _, _ = main.cache.get()
    node = next(n for n in raw["issues"] + raw["pullRequests"] if n["number"] == number)
    return "human-verified" in [l["name"] for l in node["labels"]["nodes"]]


def test_tick_labels_the_issue_and_says_who_checked_and_what_they_saw(app):
    client, calls, _ = app
    r = client.post("/api/issues/121/checked", headers=JSON,
                    json={"checked": True, "notes": "On dev: the result stayed.\nAfter a refresh too. "})
    assert r.status_code == 200 and r.get_json() == {"number": 121, "checked": True, "changed": True}
    assert calls[0] == ("label", 121, "human-verified", True)
    kind, number, body = calls[1]
    today = datetime.now(main.BOARD_TZ)
    assert (kind, number) == ("comment", 121)
    assert body == (f"Checked in the running app by Kofi on {today.day} {today:%b %Y} (ticked on Pomodo).\n\n"
                    "> On dev: the result stayed.\n> After a refresh too.")
    assert _verified(121)  # the badge shows at once, without waiting for the next GitHub read


def test_tick_without_notes_leaves_out_the_quote(app):
    client, calls, _ = app
    assert client.post("/api/issues/39/checked", json={"checked": True}, headers=JSON).status_code == 200
    assert calls[1][2].endswith("(ticked on Pomodo).")


def test_untick_removes_the_label_and_notes_it(app):
    client, calls, _ = app
    r = client.post("/api/issues/120/checked", json={"checked": False}, headers=JSON)
    assert r.status_code == 200 and r.get_json()["changed"] is True
    assert calls == [("label", 120, "human-verified", False), ("comment", 120, "Unticked by Kofi on Pomodo.")]
    assert not _verified(120)


@pytest.mark.parametrize("number, checked", [(120, True), (121, False)])
def test_asking_for_the_state_it_already_has_writes_nothing(app, number, checked):
    client, calls, _ = app
    r = client.post(f"/api/issues/{number}/checked", json={"checked": checked}, headers=JSON)
    assert r.status_code == 200 and r.get_json()["changed"] is False and calls == []  # a double click posts one comment


def test_a_merged_pull_request_can_be_ticked(app):
    client, calls, _ = app
    r = client.post("/api/issues/140/checked", json={"checked": True, "notes": "Seen on dev."}, headers=JSON)
    assert r.status_code == 200 and r.get_json()["changed"] is True
    assert calls[0] == ("label", 140, "human-verified", True) and calls[1][2].endswith("\n\n> Seen on dev.")
    assert _verified(140)


def test_a_pull_request_tick_refused_by_the_token_says_which_permission_it_needs(app):
    client, calls, monkeypatch = app

    def refuse(*a):
        raise GitHubError("GitHub refused the change (HTTP 403)")

    monkeypatch.setattr(main, "set_label", refuse)
    r = client.post("/api/issues/140/checked", json={"checked": True}, headers=JSON)
    assert r.status_code == 502 and "Pull requests: Read and write" in r.get_json()["error"]
    r = client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON)
    assert "Pull requests" not in r.get_json()["error"]  # an issue needs only the Issues permission


@pytest.mark.parametrize("number", [2, 141, 126, 999])  # open issue, unmerged pull request, not planned, unknown
def test_only_finished_issues_and_merged_pull_requests_can_be_ticked(app, number):
    client, calls, _ = app
    assert client.post(f"/api/issues/{number}/checked", json={"checked": True}, headers=JSON).status_code == 404
    assert calls == []


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
def test_requests_from_other_pages_are_refused(app, headers):
    client, calls, _ = app
    assert client.post("/api/issues/121/checked", json={"checked": True}, headers=headers).status_code == 403
    assert client.post("/api/issues/121/checked", data={"checked": "true"}).status_code == 403  # an HTML form
    assert calls == []


def test_only_a_team_member_can_tick(app):
    client, calls, _ = app
    sign_in(client, None)
    assert client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON).status_code == 401
    sign_in(client, "someone-new")
    assert client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON).status_code == 403
    assert calls == []


@pytest.mark.parametrize("body", [{"checked": "yes"}, {}, [1], None, {"checked": True, "notes": 5},
                                  {"checked": True, "notes": "x" * (main.NOTES_MAX + 1)}])
def test_body_must_say_true_or_false_with_short_text_notes(app, body):
    client, calls, _ = app
    r = client.post("/api/issues/121/checked", data=json.dumps(body), headers=JSON)
    assert r.status_code == 400 and calls == []


def test_a_refused_label_changes_nothing(app):
    client, calls, monkeypatch = app

    def refuse(*a):
        raise GitHubError("GitHub refused the change (HTTP 403)")

    monkeypatch.setattr(main, "set_label", refuse)
    r = client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON)
    assert r.status_code == 502 and "HTTP 403" in r.get_json()["error"]
    assert calls == [] and not _verified(121)


def test_a_label_put_on_a_not_planned_issue_by_mistake_can_be_taken_off(app):
    client, calls, _ = app
    assert client.post("/api/issues/127/checked", json={"checked": True}, headers=JSON).get_json()["changed"] is False
    r = client.post("/api/issues/127/checked", json={"checked": False}, headers=JSON)
    assert r.status_code == 200 and calls[0] == ("label", 127, "human-verified", False)


def test_a_failed_comment_after_the_label_says_so(app):
    client, calls, monkeypatch = app

    def refuse(*a):
        raise GitHubError("GitHub refused the change (HTTP 422)")

    monkeypatch.setattr(main, "add_comment", refuse)
    r = client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON)
    assert r.status_code == 502
    assert r.get_json()["error"].startswith("The label is on, but the comment did not post")
    assert r.get_json()["checked"] is True  # so the button can show the label as it now is
    assert _verified(121)  # the label did land, so the board shows it


def test_a_local_preview_never_writes_to_github(app, monkeypatch):
    client, calls, _ = app
    monkeypatch.setenv("DEV_FIXTURE", "dev/raw.json")
    assert client.post("/api/issues/121/checked", json={"checked": True}, headers=JSON).status_code == 200
    assert calls == [] and _verified(121)


def test_buttons_render_only_on_finished_issues_and_only_for_a_known_viewer(app):
    client, _, monkeypatch = app
    html = client.get("/").get_data(as_text=True)
    assert 'class="check" data-number="121" data-on="false"' in html
    assert 'class="check" data-number="39" data-on="false"' in html
    assert 'class="check" data-number="120" data-on="true"' in html
    assert 'class="check" data-number="127" data-on="true"' in html  # labelled by mistake: it can be unticked
    assert 'class="check" data-number="140" data-on="false" data-label="human-verified" data-ref="PR #140"' in html  # merged, closes no issue; board.js names the label from data-label
    assert 'class="check" data-number="142"' in html  # its issue is still open, so no issue line can carry the tick
    assert html.count('class="check"') == 6  # not on #129 (its issues carry the ticks), unmerged #141,
    #                                           the unlabelled not-planned row or the open issue
    sign_in(client, "someone-new")  # only a team member sees the board, so only they get buttons
    r = client.get("/")
    assert r.status_code == 403 and 'class="check"' not in r.get_data(as_text=True)


class _Session:
    def __init__(self, *resps):
        self.resps, self.sent = list(resps), []

    def request(self, method, url, **kw):
        self.sent.append((method, url, kw.get("json")))
        return self.resps.pop(0)


def test_set_label_adds_and_removes_one_label():
    s = _Session(_Resp(200, [{"name": "bug"}, {"name": "human-verified"}]), _Resp(200, []),
                 _Resp(404, {"message": "Label does not exist"}))
    set_label("t", "o/r", 7, "human-verified", True, session=s)
    set_label("t", "o/r", 7, "human-verified", False, session=s)
    set_label("t", "o/r", 7, "human-verified", False, session=s)  # 404: the issue no longer has it, which is the goal
    assert s.sent == [("POST", "https://api.github.com/repos/o/r/issues/7/labels", {"labels": ["human-verified"]}),
                      ("DELETE", "https://api.github.com/repos/o/r/issues/7/labels/human-verified", None),
                      ("DELETE", "https://api.github.com/repos/o/r/issues/7/labels/human-verified", None)]
    with pytest.raises(GitHubError, match="did not add"):
        set_label("t", "o/r", 7, "human-verified", True, session=_Session(_Resp(200, [{"name": "bug"}])))
    with pytest.raises(GitHubError, match="HTTP 403"):
        set_label("t", "o/r", 7, "human-verified", True, session=_Session(_Resp(403, {})))
    with pytest.raises(GitHubError, match="HTTP 404"):  # no such issue, or the token lost access: not a removal
        set_label("t", "o/r", 7, "human-verified", False, session=_Session(_Resp(404, {"message": "Not Found"})))


def test_add_comment_posts_the_body():
    s = _Session(_Resp(201, {"id": 1}))
    add_comment("t", "o/r", 7, "hello", session=s)
    assert s.sent == [("POST", "https://api.github.com/repos/o/r/issues/7/comments", {"body": "hello"})]
    with pytest.raises(GitHubError, match="HTTP 422"):
        add_comment("t", "o/r", 7, "", session=_Session(_Resp(422, {})))
