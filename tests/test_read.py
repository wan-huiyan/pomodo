"""The recent-only read, its retry on a heavy page, and the Up for grabs label filter."""
import re
from datetime import datetime, timezone

import pytest

import board
import github
from board import build_board, calendar_start
from config import ConfigError, parse
from github import GitHubError, fetch_repo
from test_board import NOW, _node, _task

SINCE = "2026-09-01T00:00:00Z"
TEAM = '[[team]]\nlogin = "ada-demo"\n'
BASE = 'repo = "acme/widgets"\n'


class _Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def _page(nodes, more=False, cursor="c1"):
    return {"data": {"repository": {"items": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}


class _Github:
    """A fake GitHub: answers by what the query asks for, and keeps every request."""

    def __init__(self, **pages):
        self.pages, self.sent = pages, []  # pages: name -> list of replies, used in order

    def post(self, url, json, headers, timeout):
        q = json["query"]
        self.sent.append(json)
        if "issueOrPullRequest" in q:
            return _Reply(body={"data": {"repository": {
                f"n{n}": {"state": "CLOSED"} for n in map(int, re.findall(r"number: (\d+)", q)) if n != 404}}})
        if "totalCount }" in q and "labels: [" in q:
            return _Reply(body={"data": {"repository": {
                f"c{i}": {"totalCount": 7 + i} for i in range(len(re.findall(r"c\d+: issues", q)))}}})
        name = ("open_issues" if "issues(states: OPEN" in q else "closed_issues" if "issues(states: CLOSED" in q
                else "open_pulls" if "pullRequests(states: OPEN" in q else "closed_pulls" if "states: [CLOSED, MERGED]" in q
                else "issues" if "items: issues(" in q else "pulls")
        return self.pages[name].pop(0)


def _empty():
    return [_Reply(body=_page([]))]


def _recent(**pages):
    pages = {k: _empty() for k in ("open_issues", "closed_issues", "open_pulls", "closed_pulls")} | pages
    gh = _Github(**pages)
    return fetch_repo("token", "o/r", session=gh, since=SINCE, tracker="tracker", counted=["v1", "priority"]), gh


_issue = _node


# --- config ---

def test_the_read_and_grabs_settings_default_to_today_s_behaviour():
    c = parse({"repo": "acme/widgets", "team": [{"login": "ada-demo"}]})
    assert c.recent_only is False and c.grab_labels == ()


def test_the_read_and_grabs_settings_are_read():
    c = parse({"repo": "acme/widgets", "read": {"recent_only": True}, "grabs": {"labels": ["todo", " help "]},
               "team": [{"login": "ada-demo"}]})
    assert c.recent_only is True and c.grab_labels == ("todo", "help")


@pytest.mark.parametrize("extra, message", [
    ({"read": {"recent_only": "yes"}}, "read.recent_only must be true or false"),
    ({"read": {"recent_only": 1}}, "read.recent_only must be true or false"),
    ({"read": {"recent": True}}, "unknown key read.recent"),
    ({"grabs": {"labels": "todo"}}, "grabs.labels must be a list"),
    ({"grabs": {"labels": [""]}}, "grabs.labels must be a list"),
    ({"grabs": {"label": ["todo"]}}, "unknown key grabs.label"),
])
def test_bad_read_and_grabs_settings_name_the_key(extra, message):
    with pytest.raises(ConfigError, match=message):
        parse({"repo": "acme/widgets", "team": [{"login": "ada-demo"}], **extra})


# --- the read ---

def test_the_default_read_still_reads_everything_in_pages_of_50_and_25():
    gh = _Github(issues=[_Reply(body=_page([], more=True)), _Reply(body=_page([]))], pulls=_empty())
    assert fetch_repo("token", "o/r", session=gh) == {"issues": [], "pullRequests": []}
    assert [s["variables"]["first"] for s in gh.sent] == [50, 50, 25]
    assert gh.sent[1]["variables"]["cursor"] == "c1"


def test_the_recent_read_asks_for_open_work_and_what_closed_since_the_cutoff():
    old_closed = _issue(1, state="CLOSED", closed="2026-08-30T10:00:00Z")  # updated since, closed before: dropped
    new_closed = _issue(2, state="CLOSED", closed="2026-09-02T10:00:00Z")
    merged = _node(10, kind="pr", state="MERGED", closed="2026-09-05T10:00:00Z")
    old_merged = _node(11, kind="pr", state="MERGED", closed="2026-08-01T10:00:00Z")
    raw, gh = _recent(open_issues=[_Reply(body=_page([_issue(3)]))],
                      closed_issues=[_Reply(body=_page([old_closed, new_closed]))],
                      open_pulls=[_Reply(body=_page([_node(12, kind="pr")]))],
                      closed_pulls=[_Reply(body=_page([merged, old_merged]))])
    assert [n["number"] for n in raw["issues"]] == [3, 2]
    assert [n["number"] for n in raw["pullRequests"]] == [12, 10]
    assert raw["since"] == SINCE
    by_query = {k: next(s for s in gh.sent if frag in s["query"]) for k, frag in
                (("closed", "issues(states: CLOSED"), ("open", "issues(states: OPEN"))}
    assert by_query["closed"]["variables"]["since"] == SINCE and "filterBy: {since: $since}" in by_query["closed"]["query"]
    assert "$since" not in by_query["open"]["query"]


def test_pull_requests_stop_once_a_page_reaches_work_older_than_the_cutoff():
    stale = _node(20, kind="pr", state="MERGED", closed="2026-08-01T10:00:00Z")
    stale["updatedAt"] = "2026-08-02T10:00:00Z"
    fresh = _node(21, kind="pr", state="MERGED", closed="2026-09-05T10:00:00Z")
    fresh["updatedAt"] = "2026-09-05T10:00:00Z"
    raw, gh = _recent(closed_pulls=[_Reply(body=_page([fresh, stale], more=True))])  # a second page would fail: none queued
    assert [n["number"] for n in raw["pullRequests"]] == [21]
    assert "UPDATED_AT, direction: DESC" in next(s for s in gh.sent if "states: [CLOSED, MERGED]" in s["query"])["query"]


def test_a_trackers_missing_issues_are_fetched_by_number_in_one_query():
    body = '<ul class="contains-task-list">' + _task(1, True) + _task(2, False) + _task(404, False) + "</ul>"
    raw, gh = _recent(open_issues=[_Reply(body=_page([_issue(5, labels=["tracker"], body=body), _issue(2)]))])
    asked = [s for s in gh.sent if "issueOrPullRequest" in s["query"]]
    assert len(asked) == 1
    assert sorted(map(int, re.findall(r"number: (\d+)", asked[0]["query"]))) == [1, 404]  # 2 was read, so not asked again
    assert raw["trackedStates"] == {1: "CLOSED"}  # 404 does not exist: left out, as in a full read


def test_no_tracker_means_no_extra_query():
    _, gh = _recent(open_issues=[_Reply(body=_page([_issue(3)]))])
    assert not [s for s in gh.sent if "issueOrPullRequest" in s["query"]]


def test_label_totals_are_asked_for_the_milestone_and_priority_labels():
    raw, _ = _recent()
    assert raw["labelCounts"] == {"v1": 7, "priority": 8}


# --- the retry ---

def test_a_502_or_504_is_tried_again_with_half_the_page_and_the_pages_after_stay_small():
    gh = _Github(issues=[_Reply(502), _Reply(504), _Reply(body=_page([_node(1)], more=True)), _Reply(body=_page([_node(2)]))],
                 pulls=_empty())
    raw = fetch_repo("token", "o/r", session=gh)
    assert [n["number"] for n in raw["issues"]] == [1, 2]
    assert [s["variables"]["first"] for s in gh.sent] == [50, 25, 12, 12, 25]  # the pull request read starts again at 25


def test_the_page_never_shrinks_below_the_floor():
    gh = _Github(issues=[_Reply(502) for _ in range(10)], pulls=_empty())
    with pytest.raises(GitHubError, match="HTTP 502"):
        fetch_repo("token", "o/r", session=gh)
    assert [s["variables"]["first"] for s in gh.sent] == [50, 25, 12, 6, 5]


def test_other_errors_are_not_retried():
    gh = _Github(issues=[_Reply(403)], pulls=_empty())
    with pytest.raises(GitHubError, match="HTTP 403"):
        fetch_repo("token", "o/r", session=gh)
    assert len(gh.sent) == 1


# --- the board on a recent read ---

def test_a_tracker_counts_issues_the_read_lacks_from_their_fetched_states():
    body = '<ul class="contains-task-list">' + _task(1, True) + _task(2, False) + _task(3, False) + "</ul>"
    raw = {"issues": [_issue(9, labels=["tracker"], body=body), _issue(2)], "pullRequests": [],
           "since": SINCE, "trackedStates": {"1": "CLOSED", "3": "MERGED"}}
    t = build_board(raw, NOW)["trackers"]
    assert (t[0]["total"], t[0]["closed"]) == (3, 2)
    raw["trackedStates"] = {}  # nothing known about 1 and 3: left out, as a full read leaves out what it lacks
    t = build_board(raw, NOW)["trackers"]
    assert (t[0]["total"], t[0]["closed"]) == (1, 0)


def test_label_stats_take_their_closed_count_from_the_totals_of_a_recent_read():
    raw = {"issues": [_issue(1, labels=["v1"]), _issue(2, labels=["v1"], state="CLOSED", closed="2026-10-20T10:00:00Z"),
                      _issue(3, labels=["priority"])], "pullRequests": [], "since": SINCE,
           "labelCounts": {"v1": 5, "priority": 0}}
    stats = build_board(raw, NOW)["stats"]
    assert (stats["milestone"]["total"], stats["milestone"]["closed"]) == (6, 5)  # 1 open + 5 closed
    assert (stats["priority"]["total"], stats["priority"]["closed"]) == (1, 0)
    del raw["since"]  # a full read counts what it holds
    stats = build_board(raw, NOW)["stats"]
    assert (stats["milestone"]["total"], stats["milestone"]["closed"]) == (2, 1)


def test_the_calendar_of_a_recent_read_starts_with_last_month_not_with_the_first_close():
    raw = {"issues": [_issue(2, state="CLOSED", closed="2026-10-20T10:00:00Z")], "pullRequests": [], "since": SINCE}
    cells = [c for m in build_board(raw, NOW)["calendar"]["months"] for c in m["cells"]]
    assert next(c for c in cells if c["date"] == "2026-09-02")["state"] == "open"
    del raw["since"]
    months = [m["key"] for m in build_board(raw, NOW)["calendar"]["months"]]
    assert months == ["2026-10"]  # a full read has nothing in September, so the whole month is "before" and left out


def test_the_cutoff_is_the_first_day_of_last_month_on_the_boards_clock():
    start = calendar_start(datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc))  # 01:30 in London: October
    assert start.astimezone(timezone.utc).isoformat() == "2026-08-31T23:00:00+00:00"  # 1 Sep 00:00 London (summer time)
    winter = calendar_start(datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc))
    assert winter.astimezone(timezone.utc).isoformat() == "2026-11-01T00:00:00+00:00"


# --- Up for grabs ---

def test_up_for_grabs_can_be_limited_to_labels(monkeypatch):
    raw = {"issues": [_issue(1, labels=["todo2"]), _issue(2, labels=["help", "x"]), _issue(3),
                      _issue(4, labels=["help"], assignees=["ada-demo"]), _issue(5, labels=["todo2"], state="CLOSED",
                                                                                   closed="2026-10-20T10:00:00Z")],
           "pullRequests": []}
    assert sorted(i["number"] for i in build_board(raw, NOW)["unclaimed"]) == [1, 2, 3]
    monkeypatch.setattr(board, "GRAB_LABELS", {"help", "todo2"})
    b = build_board(raw, NOW)
    assert sorted(i["number"] for i in b["unclaimed"]) == [1, 2]  # open, unassigned, one of the labels
    assert sorted(i["number"] for g in b["unclaimed_groups"] for i in g["items"]) == [1, 2]
    assert [i["number"] for p in b["people"] for i in p["items"]] == [4]  # a person's card is not limited


# --- main ---

def test_main_reads_recent_only_when_the_setting_is_on(monkeypatch):
    import dataclasses
    import main
    seen = []
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(main, "fetch_repo", lambda *a, **kw: seen.append((a, kw)) or {})
    main._load()
    assert seen == [(("token", main.REPO), {})]  # off: the same call as always
    monkeypatch.setattr(main, "CONFIG", dataclasses.replace(main.CONFIG, recent_only=True))
    main._load()
    kw = seen[1][1]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", kw["since"])
    assert kw["tracker"] == "tracker" and kw["counted"] == ["v1", "priority"]
