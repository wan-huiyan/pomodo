import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import board
from board import build_board, clean_html, task_counts
from github import ISSUES_QUERY, PULLS_QUERY, Cache, GitHubError, fetch_repo

NOW = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)


def _node(number, *, state="OPEN", labels=(), assignees=(), closed=None, body="", kind="issue", comments=(),
          reason=None, closes=(1,)):
    node = {
        "number": number, "title": f"Item {number}", "url": f"https://github.com/o/r/issues/{number}",
        "state": state, "createdAt": "2026-10-01T10:00:00Z", "updatedAt": "2026-10-27T10:00:00Z",
        "closedAt": closed, "bodyHTML": body, "author": {"login": "ada-demo", "avatarUrl": ""},
        "assignees": {"nodes": [{"login": a, "avatarUrl": ""} for a in assignees]},
        "labels": {"nodes": [{"name": l, "color": "e07a5f"} for l in labels]},
        "comments": {"totalCount": len(comments), "nodes": list(comments)},
    }
    if kind == "issue":
        node["stateReason"] = reason
    else:
        node.update(isDraft=False, mergedAt=closed if state == "MERGED" else None, reviewDecision=None,
                    additions=3, deletions=1, headRefName="x", baseRefName="main",
                    closingIssuesReferences={"nodes": [{"number": n, "url": f"https://github.com/o/r/issues/{n}"} for n in closes]},
                    reviews={"totalCount": 0, "nodes": []})
    return node


def test_sections():
    raw = {"issues": [
        _node(1, labels=["todo"], assignees=["ada-demo"]),
        _node(2, assignees=["ada-demo"], labels=["v1"]),
        _node(3, assignees=["kofi-demo"]),
        _node(4, labels=["v1"]),
        _node(5, state="CLOSED", closed="2026-10-25T09:00:00Z", labels=["v1"]),
        _node(6, state="CLOSED", closed="2026-09-01T09:00:00Z"),
    ], "pullRequests": [_node(7, state="MERGED", closed="2026-10-28T15:32:00Z", kind="pr")]}
    b = build_board(raw, NOW)
    assert [i["number"] for i in b["todos"]] == [1]
    people = {p["login"]: [i["number"] for i in p["items"]] for p in b["people"]}
    assert people == {"ada-demo": [2], "kofi-demo": [3], "mei-demo": []}  # to-dos are not repeated per person
    assert [i["number"] for i in b["unclaimed"]] == [4]
    assert [i["number"] for i in b["done_recent"]] == [7, 5]  # newest first; 6 is older than 14 days
    assert b["stats"]["milestone"] == {"label": "v1", "total": 3, "closed": 1}
    assert b["done_recent"][0]["closes"] == [1]


def _sections(b):
    return {"todos": [i["number"] for i in b["todos"]],
            **{p["login"]: [i["number"] for i in p["items"]] for p in b["people"]},
            "open_prs": [i["number"] for i in b["open_prs"]],
            "unclaimed": [i["number"] for i in b["unclaimed"]],
            "done": [i["number"] for i in b["done_recent"]]}


def test_open_pr_assigned_to_one_of_us_appears_once():
    raw = {"issues": [], "pullRequests": [_node(10, kind="pr", assignees=["ada-demo"])]}
    s = _sections(build_board(raw, NOW))
    assert s["open_prs"] == [10] and s["ada-demo"] == []


def test_every_open_issue_appears_in_exactly_one_section():
    raw = {"issues": [
        _node(1, labels=["todo"]),
        _node(2, assignees=["kofi-demo"]),
        _node(3),
        _node(12, assignees=["someone-else"]),                 # a third person or a coding agent
        _node(13, assignees=["someone-else", "ada-demo"]),
        _node(14, assignees=["ada-demo", "kofi-demo"]),     # shared: in both cards, nowhere else
    ], "pullRequests": []}
    b = build_board(raw, NOW)
    s = _sections(b)
    assert sorted(s["unclaimed"]) == [3, 12]
    assert sorted(s["ada-demo"]) == [13, 14] and sorted(s["kofi-demo"]) == [2, 14]
    for n in (1, 2, 3, 12, 13, 14):
        homes = [k for k in ("todos", "unclaimed") if n in s[k]] + \
                (["people"] if n in s["ada-demo"] + s["kofi-demo"] else [])
        assert len(homes) == 1, (n, homes)
    assert b["stats"]["open_issues"] == 6


def test_done_counts_work_not_events():
    raw = {"issues": [
        _node(1, state="CLOSED", closed="2026-10-27T09:00:00Z", reason="COMPLETED"),
        _node(2, state="CLOSED", closed="2026-10-27T09:00:00Z", reason="NOT_PLANNED"),
        _node(3, state="CLOSED", closed="2026-10-27T09:00:00Z", reason="DUPLICATE"),
        _node(4, state="CLOSED", closed="2026-10-27T09:00:00Z"),   # closed before stateReason existed
        _node(11, closed="2026-10-26T09:00:00Z", reason="REOPENED"),  # open again: not done
    ], "pullRequests": [
        _node(20, state="MERGED", closed="2026-10-27T10:00:00Z", kind="pr", closes=(1,)),  # closes issue 1
        _node(21, state="CLOSED", closed="2026-10-27T10:00:00Z", kind="pr"),  # closed without merging
    ]}
    b = build_board(raw, NOW)
    s = _sections(b)
    assert 11 not in s["done"] and 11 in s["unclaimed"]
    assert sorted(s["done"]) == [1, 2, 3, 4, 20, 21]  # listed with their tags
    assert b["stats"]["closed_issues"] == 2  # 1 and 4; not planned and duplicate are not work done
    assert b["stats"]["merged_prs"] == 1     # counted apart from issue 1, so nothing is counted twice
    assert "done_recent" not in b["stats"]


def test_pending_reviews_are_never_shown():
    assert "states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]" in PULLS_QUERY
    assert "comments(last: 100)" in PULLS_QUERY and "reviews(last: 50" in PULLS_QUERY  # newest kept
    pr = _node(30, kind="pr")
    pr["reviews"] = {"totalCount": 2, "nodes": [
        {"author": {"login": "kofi-demo"}, "state": "PENDING", "bodyHTML": "<p>draft</p>", "submittedAt": None,
         "url": "https://github.com/o/r/pull/30#r1",
         "comments": {"totalCount": 1, "nodes": [{"author": {"login": "kofi-demo"}, "bodyHTML": "<p>draft line</p>",
                                                  "createdAt": None, "url": "https://github.com/o/r/pull/30#c1",
                                                  "path": "a.py"}]}},
        {"author": {"login": "ada-demo"}, "state": "APPROVED", "bodyHTML": "<p>ok</p>",
         "submittedAt": "2026-10-27T10:00:00Z", "url": "https://github.com/o/r/pull/30#r2",
         "comments": {"totalCount": 0, "nodes": []}},
    ]}
    item = build_board({"issues": [], "pullRequests": [pr]}, NOW)["open_prs"][0]
    assert [c["html"] for c in item["comments"]] == ["<p>ok</p>"]
    assert all(c["ago"] for c in item["comments"])


def test_urls_that_do_not_point_at_github_are_dropped():
    bad = "javascript:alert(1)"
    issue = _node(40, comments=[{"author": {"login": "x", "avatarUrl": bad}, "bodyHTML": "", "createdAt": None,
                                 "url": bad}])
    issue["url"] = bad
    issue["author"]["avatarUrl"] = "https://evil.example/a.png"
    item = build_board({"issues": [issue], "pullRequests": []}, NOW)["unclaimed"][0]
    assert item["url"] is None and item["author"]["avatar"] == ""
    assert item["comments"][0]["url"] is None and item["comments"][0]["author"]["avatar"] == ""
    ok = _node(41)
    ok["author"]["avatarUrl"] = "https://avatars.githubusercontent.com/u/1?v=4"
    good = build_board({"issues": [ok], "pullRequests": []}, NOW)["unclaimed"][0]
    assert good["url"] == "https://github.com/o/r/issues/41"
    assert good["author"]["avatar"] == "https://avatars.githubusercontent.com/u/1?v=4"


def test_clean_html_strips_active_content():
    dirty = ('<p onclick="x()">hi<script>alert(1)</script></p><img src="https://x/y.png">'
             '<a href="javascript:alert(1)">bad</a><a href="https://github.com">ok</a>'
             '<style>body{display:none}</style><input type="text" value="v">')
    out = clean_html(dirty)
    for bad in ("onclick", "script", "alert", "<img", "javascript:", "<style", 'type="text"'):
        assert bad not in out
    assert 'href="https://github.com"' in out and 'rel="noopener noreferrer"' in out


def test_task_counts_and_checkboxes_stay_read_only():
    html = ('<ul><li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox" checked> a</li>'
            '<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox"> b</li></ul>')
    assert task_counts(html) == (1, 2)
    out = clean_html(html)
    assert out.count('disabled="disabled"') == 2 and "checked" in out


def test_label_colour_is_validated():
    raw = {"issues": [_node(1)], "pullRequests": []}
    raw["issues"][0]["labels"]["nodes"] = [{"name": "x", "color": "fff;}body{x"}]
    assert build_board(raw, NOW)["unclaimed"][0]["labels"][0]["color"] == "888888"


def test_cache_serves_stale_data_when_github_fails():
    t = [0.0]
    calls = []

    def loader():
        calls.append(t[0])
        if len(calls) > 1:
            raise GitHubError("GitHub returned HTTP 502")
        return {"ok": 1}

    c = Cache(loader, ttl=300, clock=lambda: t[0], spawn=lambda f: f())  # refresh inline, so the test is exact
    assert c.get() == ({"ok": 1}, 0.0, None)
    t[0] = 301
    assert c.get() == ({"ok": 1}, 0.0, "GitHub returned HTTP 502")
    t[0] = 302
    c.get()
    assert len(calls) == 2  # no retry storm: waits RETRY_AFTER_ERROR before trying again


def test_cache_raises_without_any_good_read():
    def loader():
        raise GitHubError("GITHUB_TOKEN is not set")

    with pytest.raises(GitHubError):
        Cache(loader, ttl=300).get()


class _Reply:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body, self._text = status, body, text

    def json(self):
        if self._text is not None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")  # what requests raises, a ValueError
        return self._body


class _Session:
    """Answers the first read with an empty repo, then with whatever `later` says."""

    def __init__(self, later):
        self.later, self.calls = later, 0

    def post(self, url, json, headers, timeout):
        self.calls += 1
        if self.calls <= 2:  # issues, then pull requests
            return _Reply(body={"data": {"repository": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}})
        return self.later


@pytest.mark.parametrize("later, message", [
    (_Reply(text="<html>proxy error</html>"), "GitHub sent a reply that is not JSON"),
    (_Reply(body={"data": None}), "GitHub sent a reply with no data"),
    (_Reply(body={"data": {"repository": {"items": None}}}), "GitHub sent a reply in an unexpected shape"),
])
def test_odd_github_replies_keep_the_stale_copy_and_back_off(later, message):
    t = [0.0]
    session = _Session(later)
    loads = []

    def loader():
        loads.append(t[0])
        return fetch_repo("token", "o/r", session=session)

    c = Cache(loader, ttl=300, clock=lambda: t[0], spawn=lambda f: f())
    first = c.get()
    for t[0] in (301, 306, 311, 316):  # four page views over 15 s, past the TTL
        assert c.get() == (first[0], 0.0, message)
    assert len(loads) == 2  # one good read, then one failed refresh; no retry on every page view


def test_unexpected_error_on_the_first_read_is_a_github_error():
    def loader():
        raise TypeError("'NoneType' object is not subscriptable")

    with pytest.raises(GitHubError, match=r"Unexpected error reading GitHub \(TypeError\)"):
        Cache(loader, ttl=300).get()


def test_a_copy_is_read_again_once_it_is_ttl_old_even_after_a_slow_read():
    """The footer says a newer read is on its way once the copy is ttl old. That needs the next read to be due
    ttl after the last one began, not after it ended (reads take 10 to 30 s on Cloud Run)."""
    t, spawned = [0.0], []

    def loader():
        t[0] += 20  # this read takes 20 s
        return {"ok": 1}

    c = Cache(loader, ttl=300, clock=lambda: t[0], spawn=spawned.append)
    c.get()  # began at 0, ended at 20
    t[0] = 305  # the copy is 305 s old
    c.get()
    assert len(spawned) == 1


def test_a_stale_copy_is_served_at_once_while_one_refresh_runs():
    t, release, calls = [0.0], threading.Event(), []

    def loader():
        calls.append(t[0])
        if len(calls) > 1:
            release.wait(5)
        return {"n": len(calls)}

    c = Cache(loader, ttl=300, clock=lambda: t[0])
    assert c.get()[0] == {"n": 1}
    t[0] = 301
    for _ in range(3):
        began = time.monotonic()
        assert c.get() == ({"n": 1}, 0.0, None)
        assert time.monotonic() - began < 0.1
    release.set()
    for _ in range(100):
        if c.get()[0] == {"n": 2}:
            break
        time.sleep(0.01)
    assert c.get() == ({"n": 2}, 301, None)
    assert len(calls) == 2  # a single background refresh, not one per page view


def test_requests_waiting_on_the_first_read_share_it():
    calls, release = [], threading.Event()

    def loader():
        calls.append(1)
        release.wait(5)
        raise GitHubError("GitHub returned HTTP 502")

    c = Cache(loader, ttl=300)
    errors = []

    def visit():
        try:
            c.get()
        except GitHubError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=visit) for _ in range(4)]
    for th in threads:
        th.start()
    for _ in range(100):  # wait until the first read is under way and the others are queued behind it
        if calls:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    release.set()
    for th in threads:
        th.join(5)
    assert errors == ["GitHub returned HTTP 502"] * 4
    assert len(calls) == 1
    with pytest.raises(GitHubError):
        c.get()  # a later visit (the error page's Try again) does read again
    assert len(calls) == 2


def test_priority_items_come_first_and_are_grouped():
    raw = {"issues": [
        _node(1, labels=["todo", "nice-to-have"]),
        _node(2, labels=["todo", "priority"]),
        _node(3, labels=["v1", "nice-to-have", "app-ux"]),
        _node(4, labels=["v1", "priority", "bug"]),
        _node(5),
        _node(6, labels=["priority"], assignees=["ada-demo"]),
        _node(7, labels=["nice-to-have"], assignees=["ada-demo"]),
        _node(8, state="CLOSED", closed="2026-10-27T09:00:00Z", labels=["priority"]),
    ], "pullRequests": []}
    b = build_board(raw, NOW)
    assert [i["number"] for i in b["todos"]] == [1, 2]  # to-dos have no priority: oldest first
    assert {i["priority"] for i in b["todos"]} == {None}
    groups = {g["key"]: [i["number"] for i in g["items"]] for g in b["unclaimed_groups"]}
    assert groups == {"priority": [4], "nice": [3], None: [5]}
    assert sum(len(g["items"]) for g in b["unclaimed_groups"]) == len(b["unclaimed"])
    ada = next(p for p in b["people"] if p["login"] == "ada-demo")
    assert [i["number"] for i in ada["items"]] == [6, 7]
    assert b["stats"]["priority"] == {"label": "priority", "title": "Top priority", "total": 3, "closed": 1}  # 4, 6 and the closed 8, not to-do 2
    four = next(i for i in b["unclaimed"] if i["number"] == 4)
    assert [l["name"] for l in four["chips"]] == ["bug"]  # the milestone and the priority labels are not shown as chips
    assert four["priority"] == "priority"


def _task(number, done, repo="https://github.com/o/r"):
    """One task-list line as GitHub renders `- [x] #N` in bodyHTML."""
    box = '<input type="checkbox" id="" disabled="" class="task-list-item-checkbox" aria-label="Completed task" checked="">' \
        if done else '<input type="checkbox" id="" disabled="" class="task-list-item-checkbox" aria-label="Incomplete task">'
    url = f"{repo}/issues/{number}"
    return (f'<li class="task-list-item">{box} <a class="issue-link js-issue-link" data-url="{url}" '
            f'data-hovercard-type="issue" href="{url}">#{number}</a></li>')


def test_a_tracker_is_a_progress_card_not_a_task():
    body = ('<p>Every review item:</p><ul class="contains-task-list">'
            + _task(1, True) + _task(2, False) + _task(3, True)  # 3's box is ticked, but GitHub says it's open
            + _task(999, False)  # not in the read: left out of the count
            + _task(4, False, repo="https://github.com/other/repo")  # another repo: left out
            + '</ul><p>Merged: <a href="https://github.com/o/r/issues/5">#5</a></p>')  # not a task line
    raw = {"issues": [
        _node(73, labels=["tracker", "chore", "nice-to-have"], body=body),
        _node(1, state="CLOSED", closed="2026-10-27T09:00:00Z"), _node(2), _node(3), _node(5),
    ], "pullRequests": []}
    b = build_board(raw, NOW)
    assert b["trackers"] == [{"number": 73, "title": "Item 73", "url": "https://github.com/o/r/issues/73",
                              "closed": 1, "total": 3}]
    shown = [i["number"] for i in b["todos"] + b["unclaimed"]] + [i["number"] for p in b["people"] for i in p["items"]]
    assert 73 not in shown
    assert b["stats"]["priority"]["total"] == 0


def test_tracker_counts_the_live_read_against_the_real_repo_body():
    from pathlib import Path as P
    import json
    fixture = P(__file__).resolve().parent.parent / "dev" / "raw-labelled.json"
    if not fixture.exists():
        pytest.skip("no saved GitHub read in dev/")
    raw = json.loads(fixture.read_text())
    for n in raw["issues"]:
        if n["number"] == 73:
            n["labels"]["nodes"].append({"name": "tracker", "color": "5f6f8f"})
    t = build_board(raw, NOW)["trackers"]
    assert len(t) == 1 and t[0]["number"] == 73 and t[0]["total"] >= 72


def test_tracker_edge_cases():
    nested = ('<ul class="contains-task-list">' + _task(2, False)[:-5]  # line 2 holds a nested list
              + '<ul class="contains-task-list">' + _task(3, True) + '</ul></li>' + _task(4, False) + '</ul>')
    comment_link = ('<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox"> '
                    '<a href="https://github.com/o/r/issues/5#issuecomment-9">#5 (comment)</a> then '
                    '<a href="https://github.com/o/r/issues/6">#6</a></li>')
    raw = {"issues": [
        _node(70, labels=["tracker", "todo"], body=nested + comment_link),
        _node(71, labels=["tracker"], body="<p>Nothing listed yet</p>"),
        *[_node(n) for n in (2, 3, 4, 5, 6)],
    ], "pullRequests": []}
    b = build_board(raw, NOW)
    from board import tracked_numbers
    assert tracked_numbers(raw["issues"][0]) == [2, 3, 4, 5]  # nested line 3 kept; a comment link still names #5
    assert [t["number"] for t in b["trackers"]] == [70, 71]
    assert b["todos"] == []  # labelled both: it is a tracker, not a to-do
    assert next(t for t in b["trackers"] if t["number"] == 71)["total"] == 0


def _ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _closed_by(node, actor):
    """Add the ClosedEvent GitHub reports: who closed the issue (for a close by a merge, whoever merged)."""
    node["timelineItems"] = {"nodes": [{"actor": {"login": actor, "avatarUrl": ""}}]}
    return node


def _recent_raw():
    kofi = {"login": "kofi-demo", "avatarUrl": ""}
    fix = _node(118, kind="pr", state="MERGED", closed=_ago(2), closes=(107, 50))  # 50 is listed but still open
    fix["author"] = fix["mergedBy"] = kofi
    return {"issues": [
        _closed_by(_node(107, state="CLOSED", closed=_ago(2)), "kofi-demo"),  # closed by #118 merging
        _node(50),
        _closed_by(_node(39, state="CLOSED", closed=_ago(30), reason="COMPLETED"), "ada-demo"),  # by hand, yesterday
        _closed_by(_node(126, state="CLOSED", closed=_ago(30), reason="NOT_PLANNED"), "ada-demo"),
        _closed_by(_node(60, state="CLOSED", closed=_ago(50)), "ada-demo"),  # a commit pushed straight to main
        _node(61, state="CLOSED", closed=_ago(24 * 20)),  # older than 14 days, but inside the calendar's window
        _node(62, state="CLOSED", closed=_ago(3)),  # a saved read without the ClosedEvent: nobody to credit
    ], "pullRequests": [fix, _node(140, kind="pr", state="MERGED", closed=_ago(1), closes=())]}


def test_recently_done_is_one_row_per_piece_of_work():
    b = build_board(_recent_raw(), NOW)
    rows = [r for d in b["done_days"] for r in d["rows"]]
    seen = [(r["item"]["number"], r["verb"], r["who"]["login"] if r["who"] else None, [i["number"] for i in r["fixed"]])
            for r in rows]
    assert seen == [
        (140, "merged this", "ada-demo", []),
        (118, "merged this", "kofi-demo", [107]),  # the issue it closed rides on the pull request; open 50 does not
        (62, "closed this", None, []),
        (126, "closed this as not planned", "ada-demo", []),
        (39, "closed this as done", "ada-demo", []),
        (60, "closed this", "ada-demo", []),  # no pull request lists or names it
        (61, "closed this", None, []),  # 20 days ago: done_days covers the calendar window, not just 14 days
    ]
    shown = {r["item"]["number"] for r in rows} | {i["number"] for r in rows for i in r["fixed"]}
    assert shown == {140, 118, 107, 62, 126, 39, 60, 61}  # everything closed since 1 Sep, each once
    assert [r["done"] for r in rows] == [True, True, True, False, True, True, True]


def test_recently_done_counts_describe_the_rows():
    b = build_board(_recent_raw(), NOW)
    # done_days now spans the calendar window; the hero stat still counts the last 14 days, so compare like with like.
    cutoff = (NOW - timedelta(days=14)).isoformat()
    rows = [r for d in b["done_days"] for r in d["rows"] if r["item"]["closed_at"] >= cutoff]
    f = b["stats"]["finished"]
    assert f["total"] == sum(r["done"] for r in rows) == 5
    assert f["by"] == {"ada-demo": 3, "kofi-demo": 1}
    assert f["breakdown"] == [{"name": "Ada", "n": 3}, {"name": "Kofi", "n": 1}, {"name": "someone else", "n": 1}]
    assert sum(x["n"] for x in f["breakdown"]) == f["total"]  # 62 has nobody to credit: "someone else"
    assert f["not_done"] == 1


def test_an_issue_fixed_by_an_older_pull_request_names_it():
    raw = _recent_raw()
    old = _node(90, kind="pr", state="MERGED", closed=_ago(24 * 60), closes=(91,))  # before the calendar window (1 Sep)
    old["author"] = {"login": "kofi-demo", "avatarUrl": ""}
    raw["pullRequests"].append(old)
    raw["issues"].append(_closed_by(_node(91, state="CLOSED", closed=_ago(5)), "ada-demo"))
    rows = [r for d in build_board(raw, NOW)["done_days"] for r in d["rows"]]
    r = next(r for r in rows if r["item"]["number"] == 91)
    assert (r["verb"], r["who"]["login"], r["done"]) == ("fixed this in PR #90", "kofi-demo", True)


def test_priority_card_lists_every_open_priority_issue():
    raw = {"issues": [_node(24, labels=["priority"]), _node(88, labels=["priority"], assignees=["ada-demo"]),
                      _node(5, labels=["nice-to-have"]), _node(2, labels=["todo", "priority"]),
                      _node(8, state="CLOSED", closed=_ago(1), labels=["priority"])], "pullRequests": []}
    assert [i["number"] for i in build_board(raw, NOW)["priority_open"]] == [24, 88]


def test_recently_done_groups_by_uk_day():
    b = build_board(_recent_raw(), NOW)  # NOW is 18:00 on Wed 28 Oct, UK time
    assert [(d["label"], [r["item"]["number"] for r in d["rows"]]) for d in b["done_days"]] == [
        ("Today", [140, 118, 62]), ("Yesterday", [126, 39]), ("Monday 26 Oct", [60]), ("Thursday 8 Oct", [61])]
    assert all("older" not in d for d in b["done_days"])  # only the old "Show more" used it


def test_an_issue_a_merge_closed_rides_on_that_pull_request():
    # A commit saying "Fixes #77" closes #77 when its PR merges: GitHub names whoever merged as the closer, but the work
    # is the PR author's. The board's token cannot read the commit, so it uses what the PR says about itself.
    raw = _recent_raw()
    kofi, ada = {"login": "kofi-demo", "avatarUrl": ""}, {"login": "ada-demo", "avatarUrl": ""}
    batch = _node(74, kind="pr", state="MERGED", closed=_ago(6), closes=(77,))
    batch["author"], batch["mergedBy"] = ada, kofi
    named = _node(85, kind="pr", state="MERGED", closed=_ago(8), closes=())  # lists nothing, but its title names #15
    named["title"], named["author"], named["mergedBy"] = "Batch 3: long work off the request (#15)", ada, kofi
    raw["pullRequests"] += [batch, named]
    closed_15 = (NOW - timedelta(hours=8) + timedelta(seconds=2)).isoformat()  # two seconds after #85 merged
    raw["issues"] += [_closed_by(_node(77, state="CLOSED", closed=_ago(6)), "kofi-demo"),
                      _closed_by(_node(15, state="CLOSED", closed=closed_15), "kofi-demo")]
    rows = {r["item"]["number"]: r for d in build_board(raw, NOW)["done_days"] for r in d["rows"]}
    assert 77 not in rows and 15 not in rows  # no rows of their own
    assert [i["number"] for i in rows[74]["fixed"]] == [77] and [i["number"] for i in rows[85]["fixed"]] == [15]
    assert (rows[74]["who"]["login"], rows[74]["verb"]) == ("ada-demo", "made this, merged by Kofi")


def test_a_named_issue_closed_later_or_by_someone_else_is_not_folded_in():
    raw = _recent_raw()
    pr = _node(86, kind="pr", state="MERGED", closed=_ago(10), closes=())
    pr["title"], pr["mergedBy"] = "Tidy (#16, #17)", {"login": "kofi-demo", "avatarUrl": ""}
    raw["pullRequests"].append(pr)
    raw["issues"] += [_closed_by(_node(16, state="CLOSED", closed=_ago(9)), "kofi-demo"),  # an hour after the merge
                      _closed_by(_node(17, state="CLOSED", closed=_ago(10)), "ada-demo")]  # not the person who merged
    rows = {r["item"]["number"]: r for d in build_board(raw, NOW)["done_days"] for r in d["rows"]}
    assert rows[86]["fixed"] == [] and 16 in rows and 17 in rows


def test_a_closing_reference_to_another_repo_is_not_matched_by_number():
    raw = _recent_raw()
    raw["pullRequests"][1]["closingIssuesReferences"]["nodes"] = [{"number": 95, "url": "https://github.com/other/repo/issues/95"}]
    raw["issues"].append(_closed_by(_node(95, state="CLOSED", closed=_ago(1)), "ada-demo"))
    rows = {r["item"]["number"]: r for d in build_board(raw, NOW)["done_days"] for r in d["rows"]}
    assert rows[140]["fixed"] == [] and rows[95]["verb"] == "closed this"
    assert rows[140]["item"]["closes"] == []  # the PR card's "Closes" line leaves it out too


def test_rows_say_when_in_words_today_and_by_clock_on_older_days():
    b = build_board(_recent_raw(), NOW)
    when = {r["item"]["number"]: r["when"] for d in b["done_days"] for r in d["rows"]}
    assert when[140] == "1 hour ago"  # today
    assert when[39] == "12:00"  # yesterday, UK time (UTC+0 on 27 Oct)


def test_pull_request_titles_lose_commit_prefixes():
    raw = _recent_raw()
    raw["pullRequests"][1]["title"] = "fix(sql): keep two decimals"
    rows = [r for d in build_board(raw, NOW)["done_days"] for r in d["rows"]]
    assert next(r for r in rows if r["item"]["number"] == 140)["title"] == "Keep two decimals"


def test_the_queries_ask_only_for_what_the_boards_token_can_read():
    # The token has Issues and Pull requests access, not Contents. Asking for a ClosedEvent's closer (or anything on a
    # Commit) makes GitHub answer "Resource not accessible by personal access token" for the whole read, and the board
    # then shows nothing. Checked with the deployed token on 29 Oct 2026.
    for q in (ISSUES_QUERY, PULLS_QUERY):
        assert "closer" not in q and "Commit" not in q and "associatedPullRequests" not in q


def test_recently_opened_orders_only_up_for_grabs_by_the_date_opened():
    """The order switch is only in Up for grabs (the long list); every other list keeps its usual order."""
    def opened(n, day, **kw):
        node = _node(n, **kw)
        node["createdAt"] = f"2026-10-{day:02d}T10:00:00Z"
        return node
    raw = {"issues": [
        opened(1, 5, labels=["priority"], assignees=["ada-demo"]),
        opened(2, 20, labels=["nice-to-have"], assignees=["ada-demo"]),
        opened(3, 9, labels=["nice-to-have"]),
        opened(4, 25, labels=["nice-to-have"]),
        opened(5, 12, labels=["priority"]),
        opened(6, 27, labels=["priority"]),
        opened(7, 2, labels=["todo"]),
        opened(8, 22, labels=["todo"]),
    ], "pullRequests": [opened(30, 3, kind="pr"), opened(31, 26, kind="pr")]}
    nums = lambda xs: [i["number"] for i in xs]  # noqa: E731

    usual = build_board(raw, NOW)
    assert usual["order"] == "updated"
    assert nums(usual["people"][0]["items"]) == [1, 2]  # priority first
    assert nums(usual["priority_open"]) == [1, 5, 6] and nums(usual["todos"]) == [7, 8]  # by number
    # Same update time: the higher number first, as board.js breaks the tie when it re-sorts in the browser.
    assert nums(usual["unclaimed"]) == [6, 5, 4, 3]

    new = build_board(raw, NOW, order="newest")
    assert new["order"] == "newest"
    groups = {g["key"]: nums(g["items"]) for g in new["unclaimed_groups"]}
    assert groups == {"priority": [6, 5], "nice": [4, 3], None: []}  # the groups stay, newest first inside each
    assert nums(new["unclaimed"]) == [6, 4, 5, 3]
    assert new["unclaimed"][0]["created_at"] == "2026-10-27T10:00:00+00:00"
    for key in ("priority_open", "todos", "open_prs"):
        assert nums(new[key]) == nums(usual[key]), key
    assert nums(new["people"][0]["items"]) == [1, 2]
    assert build_board(raw, NOW, order="oldest")["order"] == "updated"  # anything else is the usual order


# ---- Recently done as a calendar: board.build_board(...)["calendar"] ----

def _merged(number, closed, who="ada-demo"):
    node = _node(number, kind="pr", state="MERGED", closed=closed, closes=())
    node["author"] = {"login": who, "avatarUrl": ""}
    return node


def _cal(prs, issues=(), now=NOW):
    return board.build_board({"issues": list(issues), "pullRequests": list(prs)}, now)["calendar"]


def _panel(cal, day):
    return next(p for p in cal["days"] if p["date"] == day)


def _cell(cal, day):
    return next(c for m in cal["months"] for c in m["cells"] if c and c["date"] == day)


def _foot(day_prs, who="ada-demo"):
    """The 27 Oct panel (a Tuesday; UK clocks = UTC, Rome = UTC+1) for pull requests merged at the given times."""
    prs = [_merged(10 + i, f"2026-10-27T{t}:00Z", who) for i, t in enumerate(day_prs)]
    return _panel(_cal(prs), "2026-10-27")["says"][""]


def test_calendar_window_starts_at_uk_midnight_on_the_first_of_last_month():
    # 1 Sep 00:00 BST is 2026-08-31T23:00Z. recently_done compares closed_at as text, so the cutoff must be UTC isoformat.
    inside, outside = _merged(1, "2026-08-31T23:30:00Z"), _merged(2, "2026-08-31T22:59:00Z")
    b = board.build_board({"issues": [], "pullRequests": [inside, outside]}, NOW)
    assert [r["item"]["number"] for d in b["done_days"] for r in d["rows"]] == [1]
    assert b["calendar"]["from"] == "2026-09-01T00:00:00+01:00"
    assert _cell(b["calendar"], "2026-09-01")["says"][""]["n"] == 1  # UK date, not the UTC one


def test_the_hero_stat_still_counts_14_days_while_the_calendar_goes_back_further():
    b = board.build_board({"issues": [], "pullRequests": [_merged(1, _ago(24 * 20)), _merged(2, _ago(3))]}, NOW)
    assert b["stats"]["finished"]["total"] == 1
    assert sum(len(d["rows"]) for d in b["done_days"]) == 2


def test_calendar_months_add_up_to_their_headline_overall_and_per_person():
    raw = _recent_raw()
    raw["pullRequests"] += [_merged(200, "2026-09-15T10:00:00Z"), _merged(201, "2026-09-15T11:00:00Z", "kofi-demo"),
                            _merged(202, "2026-09-29T12:00:00Z", "kofi-demo")]
    raw["issues"].append(_node(203, state="CLOSED", closed="2026-09-16T09:00:00Z", reason="NOT_PLANNED"))
    b = board.build_board(raw, NOW)
    cal = b["calendar"]
    assert [m["key"] for m in cal["months"]] == ["2026-09", "2026-10"]  # oldest first
    for m in cal["months"]:
        cells = [c for c in m["cells"] if c]
        assert m["total"] == sum(c["says"][""]["n"] for c in cells)
        for login in board.PEOPLE:
            assert m["by"].get(login, 0) == sum(c["says"][login]["n"] for c in cells)
        assert sum(x["n"] for x in m["breakdown"]) == m["total"]
    sept, octo = cal["months"]
    assert (sept["total"], sept["by"]) == (3, {"ada-demo": 1, "kofi-demo": 2})  # the not-planned close is not done
    assert sept["breakdown"] == [{"name": "Ada", "n": 1}, {"name": "Kofi", "n": 2}]
    assert (octo["total"], octo["by"]) == (6, {"ada-demo": 3, "kofi-demo": 1})  # 62 and 61 have nobody to credit
    assert octo["breakdown"] == [{"name": "Ada", "n": 3}, {"name": "Kofi", "n": 1}, {"name": "someone else", "n": 2}]
    done = sum(r["done"] for d in b["done_days"] for r in d["rows"])
    assert sept["total"] + octo["total"] == done  # the calendar counts the rows of done_days, nothing else
    # panels hold the same rows the cells count
    assert sum(sum(r["done"] for r in p["rows"]) for p in cal["days"]) == done


def test_calendar_month_shape_and_first_day_of_the_grid():
    raw = _recent_raw()
    raw["pullRequests"].append(_merged(200, "2026-09-15T10:00:00Z"))
    sept, octo = board.build_board(raw, NOW)["calendar"]["months"]
    assert (octo["label"], octo["name"], octo["current"], octo["lead"], len(octo["cells"])) == ("October 2026", "October", True, 3, 31)
    assert (sept["label"], sept["name"], sept["current"], sept["lead"], len(sept["cells"])) == ("September 2026", "September", False, 1, 30)


def test_a_day_is_before_open_or_future_by_the_oldest_day_anything_was_closed():
    cal = board.build_board(_recent_raw(), NOW)["calendar"]  # oldest close: #61 on 8 Oct, so September has no clickable day
    assert [m["key"] for m in cal["months"]] == ["2026-10"]
    state = {c["date"]: c["state"] for c in cal["months"][0]["cells"]}
    assert state["2026-10-07"] == "before" and state["2026-10-08"] == "open"
    assert state["2026-10-28"] == "open" and state["2026-10-29"] == "future" and state["2026-10-31"] == "future"
    assert [p["date"] for p in cal["days"]] == sorted(d for d, s in state.items() if s == "open")  # oldest first
    assert _cell(cal, "2026-10-28")["today"] and _cell(cal, "2026-10-28")["selected"]
    assert (cal["today"], cal["selected"]) == ("2026-10-28", "2026-10-28")
    assert not _cell(cal, "2026-10-27")["today"] and not _cell(cal, "2026-10-27")["selected"]
    assert _cell(cal, "2026-10-24")["weekend"] and not _cell(cal, "2026-10-26")["weekend"]


def test_first_day_comes_from_every_closed_item_not_just_the_window():
    # Closed in July: far outside the window, but it is the oldest thing ever closed, so 1 Sep is clickable.
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z")], [_node(2, state="CLOSED", closed="2026-07-04T10:00:00Z")])
    assert [m["key"] for m in cal["months"]] == ["2026-09", "2026-10"]
    assert _cell(cal, "2026-09-01")["state"] == "open"


def test_last_month_is_dropped_without_a_clickable_day_and_kept_with_one():
    assert [m["key"] for m in _cal([_merged(1, "2026-10-27T10:00:00Z")])["months"]] == ["2026-10"]
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z"), _merged(2, "2026-09-30T10:00:00Z")])
    assert [m["key"] for m in cal["months"]] == ["2026-09", "2026-10"]
    sept = {c["date"]: c["state"] for c in cal["months"][0]["cells"]}
    assert sept["2026-09-29"] == "before" and sept["2026-09-30"] == "open"


def test_an_empty_board_still_shows_today():
    cal = _cal([])
    assert [m["key"] for m in cal["months"]] == ["2026-10"]
    assert [p["date"] for p in cal["days"]] == ["2026-10-28"]
    assert cal["latest_active"] is None
    assert cal["days"][0]["says"][""]["empty"] == "Nothing finished yet today."


def test_panel_rows_read_newest_first_with_uk_and_own_clocks():
    cal = _cal([_merged(2, "2026-10-27T09:30:00Z"), _merged(1, "2026-10-27T17:15:00Z", "kofi-demo")])
    p = _panel(cal, "2026-10-27")
    assert [r["item"]["number"] for r in p["rows"]] == [1, 2]
    kofi = p["rows"][0]["clock"]
    assert (kofi["board"].strftime("%H:%M"), kofi["own"].strftime("%H:%M")) == ("17:15", "18:15")
    assert (kofi["kind"], kofi["zone"], kofi["login"], kofi["own_note"]) == ("late", "day", "kofi-demo", "18:15 Rome")
    ada = p["rows"][1]["clock"]
    assert (ada["kind"], ada["zone"], ada["own_note"]) == ("", "day", "")
    assert (p["long"], p["title"], p["is_today"], p["weekend"], p["selected"]) == ("Tuesday 27 October", "Yesterday", False, False, False)


def test_kofis_quarter_past_five_is_after_hours_on_her_own_clock():
    # 17:15 in the UK is 18:15 in Rome: after hours for Kofi, a normal afternoon on the UK timeline.
    says = _panel(_cal([_merged(1, "2026-10-27T17:15:00Z", "kofi-demo")]), "2026-10-27")["says"]
    assert (says[""]["late"], says[""]["stat"]) == (1, "1 finished, 1 after hours")
    assert says["kofi-demo"]["late"] == 1 and says["ada-demo"]["late"] == 0
    assert says[""]["foot"] == "All of these finished after hours. Home time was 6 pm. Pomo is tapping its watch."
    # 16:59 UK is 17:59 there: still a working day.
    assert _foot(["16:59"], "kofi-demo")["late"] == 0


@pytest.mark.parametrize("who,time,joke,mood", [
    ("ada-demo", "17:59", None, None),
    ("ada-demo", "18:00", "Home time was 6 pm. Pomo is tapping its watch.", "worried"),
    ("ada-demo", "19:59", "Home time was 6 pm. Pomo is tapping its watch.", "worried"),
    ("ada-demo", "20:00", "Dinner was hours ago. Pomo would like a word.", "worried"),
    ("ada-demo", "21:59", "Dinner was hours ago. Pomo would like a word.", "worried"),
    ("ada-demo", "22:00", "Way past bedtime. Even Pomo has gone to bed.", "sleepy"),
    ("ada-demo", "08:59", "Up before 9 am? Pomo hasn't had its coffee yet.", "worried"),
    ("ada-demo", "09:00", None, None),
    # the joke follows the finisher's own clock: 20:59 UK is 21:59 in Rome, 21:00 UK is 22:00 there
    ("kofi-demo", "20:59", "Dinner was hours ago. Pomo would like a word.", "worried"),
    ("kofi-demo", "21:00", "Way past bedtime. Even Pomo has gone to bed.", "sleepy"),
    ("kofi-demo", "07:59", "Up before 9 am? Pomo hasn't had its coffee yet.", "worried"),  # 08:59 in Rome
    ("kofi-demo", "08:00", None, None),  # 09:00 in Rome
])
def test_the_after_hours_joke_follows_the_latest_own_clock_hour(who, time, joke, mood):
    says = _foot([time], who)
    if joke is None:
        assert says["late"] == 0 and says["foot"] == "Nothing finished after hours. Pomo approves."
    else:
        assert (says["late"], says["foot"], says["mood"]) == (1, f"All of these finished after hours. {joke}", mood)


def test_a_late_finish_beats_an_early_one_for_the_joke_and_the_lead_counts_the_late_rows():
    says = _foot(["07:30", "19:00", "12:00"])
    assert says["late"] == 2 and says["stat"] == "3 finished, 2 after hours"
    assert says["foot"] == "2 things finished after hours. Home time was 6 pm. Pomo is tapping its watch."
    assert _foot(["07:30", "12:00"])["foot"] == "1 thing finished after hours. Up before 9 am? Pomo hasn't had its coffee yet."
    assert _foot(["12:00"])["foot"] == "Nothing finished after hours. Pomo approves."


def test_the_approves_footer_waits_for_the_day_to_be_over():
    prs = [_merged(1, "2026-10-28T10:00:00Z")]
    before_six = datetime(2026, 10, 28, 17, 59, tzinfo=timezone.utc)
    assert "foot" not in _panel(_cal(prs, now=before_six), "2026-10-28")["says"][""]
    assert _panel(_cal(prs, now=NOW), "2026-10-28")["says"][""]["foot"] == "Nothing finished after hours. Pomo approves."
    # a day that is already over is judged whatever the hour now
    early = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    yesterday = _merged(2, "2026-10-27T10:00:00Z")
    assert _panel(_cal([yesterday], now=early), "2026-10-27")["says"][""]["foot"].startswith("Nothing finished after hours")


def test_a_weekend_row_is_weekend_never_after_hours():
    # Saturday 24 Oct, 22:00: after hours on a weekday, but here it is the weekend itself.
    cal = _cal([_merged(1, "2026-10-24T22:00:00Z"), _merged(2, "2026-10-24T10:00:00Z", "kofi-demo")])
    p = _panel(cal, "2026-10-24")
    assert p["weekend"] and p["divider"] is None
    assert {r["clock"]["kind"] for r in p["rows"]} == {"weekend"}
    s = p["says"][""]
    assert (s["n"], s["late"], s["stat"], s["stamp"], s["mood"]) == (2, 0, "2 finished", "Weekend?!", "worried")
    assert s["foot"] == "2 things finished on a Saturday. Pomo will pretend it didn't see this."
    assert "after hours" not in _cell(cal, "2026-10-24")["says"][""]["label"]
    one = _panel(_cal([_merged(1, "2026-10-25T10:00:00Z")]), "2026-10-25")["says"][""]
    assert one["foot"] == "1 thing finished on a Sunday. Pomo will pretend it didn't see this."
    # The weekend follows the UK day, like the panel the row sits in; the hours follow the finisher's own clock.
    # Kofi's Friday 23:30 in the UK (BST until 25 Oct) is 00:30 on Saturday in Rome: after hours on a Friday.
    fri = _panel(_cal([_merged(1, "2026-10-23T22:30:00Z", "kofi-demo")]), "2026-10-23")
    assert fri["rows"][0]["clock"]["kind"] == "early" and fri["says"][""]["late"] == 1
    assert "approves" not in fri["says"][""]["foot"]
    # His Sunday 23:30 in the UK (GMT from 25 Oct) is 00:30 on Monday in Rome: still Sunday's weekend work.
    sun = _panel(_cal([_merged(1, "2026-10-25T23:30:00Z", "kofi-demo")]), "2026-10-25")
    assert sun["rows"][0]["clock"]["kind"] == "weekend" and sun["says"][""]["stamp"] == "Weekend?!"


def test_a_weekend_with_nothing_finished_is_as_it_should_be_and_a_stamp_needs_a_done_row():
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z")], [_node(2, state="CLOSED", closed="2026-10-24T10:00:00Z", reason="NOT_PLANNED")])
    says = _panel(cal, "2026-10-24")["says"][""]
    assert (says["n"], says["stat"]) == (0, "Nothing finished")
    assert "stamp" not in says and "foot" not in says  # the stamp and the joke are for finished work only
    tue = _panel(_cal([], [_node(3, state="CLOSED", closed="2026-10-20T10:00:00Z", reason="NOT_PLANNED")]), "2026-10-20")
    assert tue["says"][""]["stat"] == "Nothing finished" and "foot" not in tue["says"][""]  # nothing to approve of
    quiet = _panel(cal, "2026-10-25")["says"][""]  # Sunday, nothing at all
    assert (quiet["empty"], quiet["mood"], quiet["has_rows"]) == ("Sunday: nothing finished, as it should be.", "sleepy", False)


def test_empty_day_copy():
    cal = _cal([_merged(1, "2026-10-26T10:00:00Z"), _merged(2, "2026-10-27T10:00:00Z", "kofi-demo")])
    assert _panel(cal, "2026-10-28")["says"][""]["empty"] == "Nothing finished yet today."
    assert _panel(cal, "2026-10-28")["says"]["ada-demo"]["empty"] == "Nothing finished yet today."
    assert _panel(cal, "2026-10-26")["says"]["kofi-demo"]["empty"] == "Nothing by Kofi on this day."
    assert "empty" not in _panel(cal, "2026-10-26")["says"][""]
    assert _panel(cal, "2026-10-26")["says"]["kofi-demo"]["has_rows"] is False
    later = _cal([_merged(1, "2026-10-26T10:00:00Z")], now=datetime(2026, 10, 30, 12, tzinfo=timezone.utc))
    assert _panel(later, "2026-10-28")["says"][""]["empty"] == "Nothing finished on Wednesday 28 October."


def test_the_empty_today_panel_points_at_the_newest_earlier_day_with_finished_work():
    only_dropped = _node(5, state="CLOSED", closed="2026-10-27T10:00:00Z", reason="NOT_PLANNED")  # closed, not done
    cal = _cal([_merged(1, "2026-10-26T10:00:00Z")], [only_dropped])
    assert cal["latest_active"] == {"date": "2026-10-26", "title": "Monday 26 Oct", "long": "Monday 26 October"}
    assert _panel(cal, "2026-10-28")["says"][""]["empty"] == "Nothing finished yet today."
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z")])
    assert cal["latest_active"] == {"date": "2026-10-27", "title": "Yesterday", "long": "Tuesday 27 October"}
    # the newest day *before today*: finished work today does not count
    assert _cal([_merged(1, "2026-10-28T09:00:00Z"), _merged(2, "2026-10-26T09:00:00Z")])["latest_active"]["date"] == "2026-10-26"


def test_day_labels_name_who_did_the_work():
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z"), _merged(2, "2026-10-27T11:00:00Z", "kofi-demo"),
                _merged(3, "2026-10-26T22:00:00Z"), _merged(4, "2026-10-28T09:00:00Z"),
                _merged(5, "2026-10-26T10:00:00Z", "someone")], [_node(6, state="CLOSED", closed="2026-10-26T12:00:00Z")])
    label = lambda day, who="": _cell(cal, day)["says"][who]["label"]  # noqa: E731
    assert label("2026-10-27") == "Tuesday 27 October: 2 finished (1 by Ada, 1 by Kofi)"
    assert label("2026-10-27", "kofi-demo") == "Tuesday 27 October: 1 finished"
    assert label("2026-10-28") == "Wednesday 28 October, today: 1 finished, all by Ada"
    # a PR by someone else is credited to them (not one of us); #6 was closed by nobody the board knows
    assert label("2026-10-26") == "Monday 26 October: 3 finished (1 by Ada, 2 by someone else), 1 after hours"
    assert label("2026-10-25") == "Sunday 25 October: nothing finished"
    assert _panel(cal, "2026-10-27")["says"][""]["label"] == label("2026-10-27")
    assert _panel(cal, "2026-10-27")["title"] == "Yesterday"
    only_other = _cal([_merged(1, "2026-10-27T10:00:00Z", "someone")])
    assert _cell(only_other, "2026-10-27")["says"][""]["label"] == "Tuesday 27 October: 1 finished (1 by someone else)"


def test_cells_carry_a_heat_level_per_person():
    prs = [_merged(100 + i, f"2026-10-27T{9 + i % 8:02d}:{i:02d}:00Z") for i in range(11)]
    prs += [_merged(200 + i, f"2026-10-26T1{i}:00:00Z", "kofi-demo") for i in range(3)]
    cal = _cal(prs)
    assert [_cell(cal, "2026-10-27")["says"][""]["level"], _cell(cal, "2026-10-27")["says"]["kofi-demo"]["level"]] == [4, 0]
    assert _cell(cal, "2026-10-26")["says"][""]["level"] == 2
    assert _cell(cal, "2026-10-26")["says"]["ada-demo"]["level"] == 0
    assert _cell(cal, "2026-10-25")["says"][""]["level"] == 0


@pytest.mark.parametrize("n,level", [(0, 0), (1, 1), (2, 1), (3, 2), (5, 2), (6, 3), (10, 3), (11, 4), (40, 4)])
def test_heat_levels_by_finished_rows(n, level):
    prs = [_merged(100 + i, f"2026-10-27T10:{i % 60:02d}:00Z") for i in range(n)]
    assert _panel(_cal(prs + [_merged(1, "2026-10-20T10:00:00Z")]), "2026-10-27")["says"][""]["level"] == level


def test_the_six_pm_marker_sits_under_the_evening_rows_or_on_top_of_a_finished_day():
    # Newest first: the evening is on top, so the marker goes under its last row, before the day's work.
    cal = _cal([_merged(1, "2026-10-27T10:00:00Z"), _merged(2, "2026-10-27T19:00:00Z"), _merged(5, "2026-10-27T21:00:00Z"),
                _merged(3, "2026-10-26T10:00:00Z"), _merged(4, "2026-10-24T22:00:00Z"),
                _merged(6, "2026-10-23T19:00:00Z"), _merged(7, "2026-10-23T18:30:00Z")])
    tue = _panel(cal, "2026-10-27")
    assert [r["clock"]["board"].strftime("%H:%M") for r in tue["rows"]] == ["21:00", "19:00", "10:00"]
    assert tue["divider"] == 2
    assert _panel(cal, "2026-10-23")["divider"] == 2  # only evening rows: the marker closes the strip at the bottom
    assert _panel(cal, "2026-10-26")["divider"] == 0  # a finished weekday with a footer and no evening: the marker on top
    assert _panel(cal, "2026-10-24")["divider"] is None  # weekends have no working hours
    today = _cal([_merged(1, "2026-10-28T10:00:00Z")], now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    assert _panel(today, "2026-10-28")["divider"] is None  # no evening rows and no footer yet
    # An early start has its line before 6 pm, but the marker waits for the evening to have happened.
    early = [_merged(1, "2026-10-28T07:00:00Z")]
    noon = _panel(_cal(early, now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc)), "2026-10-28")
    assert noon["says"][""]["foot"].endswith("Pomo hasn't had its coffee yet.") and noon["divider"] is None
    assert _panel(_cal(early, now=datetime(2026, 10, 28, 18, tzinfo=timezone.utc)), "2026-10-28")["divider"] == 0


def test_the_calendar_leaves_the_boards_rows_alone():
    b = board.build_board(_recent_raw(), NOW)
    assert all("clock" not in r for d in b["done_days"] for r in d["rows"])
    again = board.done_calendar(b["done_days"], NOW, datetime(2026, 10, 8).date())
    assert again["months"][0]["total"] == b["calendar"]["months"][0]["total"]


def test_people_match_their_github_login_whatever_its_case():
    """GitHub logins ignore case, so "Ada-Demo" from GitHub is the config's ada-demo: her card, her name, her clock."""
    raw = {"issues": [_node(2, assignees=["Ada-Demo"]), _node(3, assignees=["KOFI-DEMO"]),
                      _node(4, assignees=["someone-else"])], "pullRequests": []}
    b = build_board(raw, NOW)
    cards = {p["login"]: [i["number"] for i in p["items"]] for p in b["people"]}
    assert cards == {"ada-demo": [2], "kofi-demo": [3], "mei-demo": []}
    assert [i["number"] for i in b["unclaimed"]] == [4]  # not on the team, so not taken
    ada = next(i for i in b["people"][0]["items"])["assignees"][0]
    assert ada == {"login": "ada-demo", "name": "Ada", "avatar": ""}
