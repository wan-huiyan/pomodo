"""DEMO=1: the made-up read fills every section of the board, whatever day the board is started on."""
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import main
from board import build_board
from config import CONFIG, Config, Labels, Person
from demo import OUTSIDER, REPO_URL, make_raw
from github import ISSUES_QUERY, PULLS_QUERY, Cache
from test_auth import JSON, sign_in

UTC = timezone.utc
NOWS = [
    datetime(2026, 10, 15, 14, 0, tzinfo=UTC),   # the middle of a month
    datetime(2026, 10, 1, 10, 0, tzinfo=UTC),    # the first of the month
    datetime(2026, 10, 26, 8, 30, tzinfo=UTC),   # a Monday, the day after the clocks went back
    datetime(2026, 11, 1, 0, 30, tzinfo=UTC),    # a Sunday, the first, half an hour after midnight
    datetime(2027, 1, 1, 10, 0, tzinfo=UTC),     # last month is in the year before
    datetime(2028, 2, 29, 12, 0, tzinfo=UTC),    # a leap day
]
_ids = [f"{n:%Y-%m-%d %H:%M}" for n in NOWS]
_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def _times(value):
    """Every timestamp string anywhere in the read."""
    if isinstance(value, str):
        return [value] if _TIME.match(value) else []
    if isinstance(value, dict):
        return [t for v in value.values() for t in _times(v)]
    if isinstance(value, list):
        return [t for v in value for t in _times(v)]
    return []


def _at(s):
    return datetime.fromisoformat(s)


@pytest.mark.parametrize("now", NOWS, ids=_ids)
def test_the_demo_fills_every_section(now):
    raw = make_raw(now)
    json.dumps(raw)  # plain JSON data, like a saved GitHub read
    b = build_board(raw, now)
    assert [p["login"] for p in b["people"]] == [p.login for p in CONFIG.team]
    for p in b["people"]:
        assert 2 <= len(p["items"]) <= 4, p["name"]
    assert 4 <= len(b["unclaimed"]) <= 8
    assert all(g["items"] for g in b["unclaimed_groups"])  # top priority, nice to have and not sorted yet
    assert 2 <= len(b["todos"]) <= 3
    assert len(b["trackers"]) == 1 and 0 < b["trackers"][0]["closed"] < b["trackers"][0]["total"]
    assert 2 <= len(b["open_prs"]) <= 3
    assert any(p["draft"] for p in b["open_prs"]) and any(p["review_decision"] for p in b["open_prs"])
    assert any(p["comments"] for p in b["open_prs"] + b["unclaimed"] + b["todos"] + [i for q in b["people"] for i in q["items"]])
    assert b["priority_open"]
    assert 0 < b["stats"]["milestone"]["closed"] < b["stats"]["milestone"]["total"]


@pytest.mark.parametrize("now", NOWS, ids=_ids)
def test_the_demo_fills_the_calendar_and_pomos_lines(now):
    b = build_board(make_raw(now), now)
    cal = b["calendar"]
    assert [m["current"] for m in cal["months"]] == [False, True]  # last month and this month, both with something in them
    assert all(m["total"] > 0 for m in cal["months"])
    rows = [r for d in cal["days"] for r in d["rows"]]
    assert 25 <= len([r for r in rows if r["done"]]) <= 40
    assert any(r["clock"]["kind"] in ("early", "late") for r in rows)  # after hours, on the finisher's own clock
    assert any(r["clock"]["kind"] == "weekend" for r in rows)
    assert any(c["says"][""]["late"] for m in cal["months"] for c in m["cells"])
    assert any(c["weekend"] and c["says"][""]["n"] for m in cal["months"] for c in m["cells"])
    assert any(r["item"]["state_reason"] == "not_planned" and not r["done"] for r in rows)
    assert any(r["item"]["kind"] == "pr" and r["fixed"] for r in rows)  # a merged pull request carrying the issues it closed
    assert sum(1 for r in rows if r["item"]["verified"] or any(i["verified"] for i in r["fixed"])) >= 2
    assert sum(1 for r in rows if r["item"]["robot"]) >= 3 and any(p["robot"] for p in b["open_prs"])
    # the latest working day shows after-hours work together, with the night joke
    latest = max((d for d in cal["days"] if d["says"][""]["late"] >= 2 and not d["is_today"]), key=lambda d: d["date"])
    assert latest["says"][""].get("foot")
    every = build_board(make_raw(now), now, done="all")["calendar"]
    assert sum(m["total"] for m in every["months"]) > sum(m["total"] for m in cal["months"])  # docs are left out by default
    assert {r["who"]["login"] for r in rows if r["who"] and r["done"]} == {p.login for p in CONFIG.team}
    # every person shows up in the finished-this-fortnight headline too
    assert set(b["stats"]["finished"]["by"]) == {p.login for p in CONFIG.team}


@pytest.mark.parametrize("now", NOWS, ids=_ids)
def test_nothing_in_the_demo_is_dated_after_now(now):
    raw = make_raw(now)
    assert all(_at(t) <= now for t in _times(raw))
    for n in raw["issues"] + raw["pullRequests"]:
        created, updated = _at(n["createdAt"]), _at(n["updatedAt"])
        assert created <= updated
        if n["closedAt"]:
            assert created <= _at(n["closedAt"]) <= updated
        for c in n["comments"]["nodes"]:
            assert created <= _at(c["createdAt"]) <= updated
        for r in n.get("reviews", {}).get("nodes", []):
            assert created <= _at(r["submittedAt"]) <= updated


def test_the_demo_has_the_shape_of_what_github_sends():
    now = NOWS[0]
    raw = make_raw(now)
    issue_keys = {"number", "title", "url", "state", "createdAt", "updatedAt", "closedAt", "bodyHTML", "author", "assignees",
                  "labels", "stateReason", "comments", "timelineItems"}
    pr_keys = (issue_keys - {"stateReason", "timelineItems"}) | {
        "isDraft", "mergedAt", "reviewDecision", "additions", "deletions", "headRefName", "baseRefName", "mergedBy",
        "closingIssuesReferences", "reviews"}
    for keys, query, nodes in ((issue_keys, ISSUES_QUERY, raw["issues"]), (pr_keys, PULLS_QUERY, raw["pullRequests"])):
        assert all(re.search(rf"\b{k}\b", query) for k in keys)  # nothing the query does not ask for
        assert all(set(n) == keys for n in nodes)  # and everything it asks for
    numbers = [n["number"] for n in raw["issues"] + raw["pullRequests"]]
    assert len(numbers) == len(set(numbers))  # one sequence, as GitHub numbers them
    assert {n["state"] for n in raw["issues"]} == {"OPEN", "CLOSED"}
    assert {n["state"] for n in raw["pullRequests"]} == {"OPEN", "MERGED", "CLOSED"}
    assert {n["stateReason"] for n in raw["issues"]} == {None, "COMPLETED", "NOT_PLANNED"}
    for n in raw["issues"]:
        assert n["url"] == f"{REPO_URL}/issues/{n['number']}"
        assert (n["state"] == "CLOSED") == bool(n["timelineItems"]["nodes"]) == bool(n["closedAt"])  # someone closed each one
    for n in raw["pullRequests"]:
        assert n["url"] == f"{REPO_URL}/pull/{n['number']}"
        assert (n["mergedAt"] == n["closedAt"]) if n["state"] == "MERGED" else n["mergedAt"] is None
        assert (n["mergedBy"] is not None) == (n["state"] == "MERGED")
        for ref in n["closingIssuesReferences"]["nodes"]:
            assert ref["url"] == f"{REPO_URL}/issues/{ref['number']}" and ref["number"] in {i["number"] for i in raw["issues"]}
    everyone = json.dumps(raw)
    assert '"avatarUrl": ""' in everyone and re.search(r'"avatarUrl": "[^"]', everyone) is None  # initials only
    assert all(u.startswith("https://github.com/") for u in re.findall(r'"url": "([^"]*)"', everyone))
    assert OUTSIDER in everyone and OUTSIDER not in [p.login for p in CONFIG.team]


def test_the_trackers_task_list_names_issues_that_exist():
    raw = make_raw(NOWS[0])
    tracker = next(n for n in raw["issues"] if any(l["name"] == CONFIG.labels.tracker for l in n["labels"]["nodes"]))
    from board import tracked_numbers
    listed = tracked_numbers(tracker)
    by_number = {n["number"]: n for n in raw["issues"]}
    assert len(listed) >= 3 and all(n in by_number for n in listed)
    assert 'class="contains-task-list"' in tracker["bodyHTML"] and 'class="task-list-item"' in tracker["bodyHTML"]
    ticked = re.findall(r'<li class="task-list-item"><input[^>]*?(checked="")?>\s*<a[^>]*>#(\d+)', tracker["bodyHTML"])
    assert [(bool(c), int(n)) for c, n in ticked] == [(by_number[n]["state"] == "CLOSED", n) for n in listed]


def test_the_same_day_gives_the_same_data():
    assert make_raw(NOWS[0]) == make_raw(NOWS[0])
    assert make_raw(NOWS[0]) != make_raw(NOWS[1])


def test_the_demo_works_for_any_team_and_any_clock():
    """One person at the far side of the world, other label names, no milestone, and a login that looks like the outsider."""
    config = Config(title="X", repo="o/r", zone="America/Los_Angeles", zone_label="LA", work_start=8, work_end=17,
                    labels=Labels(chore="admin", tracker="list", verified="checked", robot="bot-ok", priority="urgent",
                                  priority_title="Urgent", nice="later", milestone=""),
                    team=(Person(login="Guest-Dev", name="Guest", zone="Pacific/Auckland"),))
    for now in NOWS + [datetime(2026, 10, 28, 3, 0, tzinfo=UTC)]:
        raw = make_raw(now, config)
        assert all(_at(t) <= now for t in _times(raw))
        names = {l["name"] for n in raw["issues"] + raw["pullRequests"] for l in n["labels"]["nodes"]}
        assert names == {"bug", "enhancement", "documentation", "admin", "list", "checked", "bot-ok", "urgent", "later"}  # theirs, and no milestone
        mine = [n for n in raw["issues"] if n["state"] == "OPEN" and [a["login"] for a in n["assignees"]["nodes"]] == ["Guest-Dev"]
                and not {"admin", "list"} & {l["name"] for l in n["labels"]["nodes"]}]
        assert 2 <= len(mine) <= 4
        outsiders = {n["author"]["login"] for n in raw["issues"] + raw["pullRequests"]} - {"Guest-Dev"}
        assert len(outsiders) == 1 and outsiders.pop().lower() != "guest-dev"
        # working days on the board's clock, except the one Saturday and anything finished today
        board_day = lambda s: _at(s).astimezone(ZoneInfo(config.zone))  # noqa: E731
        odd = [n["closedAt"] for n in raw["issues"] if n["closedAt"] and board_day(n["closedAt"]).weekday() >= 5
               and board_day(n["closedAt"]).date() != now.astimezone(ZoneInfo(config.zone)).date()]
        assert len(odd) == 1


def test_the_dockerfile_ships_demo_py():
    dockerfile = (Path(main.__file__).parent / "Dockerfile").read_text()
    assert re.search(r"^COPY .*\bdemo\.py\b", dockerfile, re.M)


# ---------- the page, on the made-up read ----------

@pytest.fixture
def demo_board(monkeypatch):
    """The board as DEMO=1 runs it, with Kofi signed in. GitHub is never read or written. Returns the client and the
    cache's clock, so a test can let the cache's read go stale."""
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setattr(main, "_DEMO", {"raw": None})
    boom = lambda *a, **k: pytest.fail("touched GitHub in DEMO mode")  # noqa: E731
    for name in ("fetch_repo", "set_assignee", "set_label", "add_comment"):
        monkeypatch.setattr(main, name, boom)
    clock = [1000.0]
    monkeypatch.setattr(main, "cache", Cache(main._load, ttl=300, clock=lambda: clock[0], spawn=lambda f: f()))
    client = main.app.test_client()
    sign_in(client, "kofi-demo")
    return client, clock


def test_the_page_renders_on_demo_data_for_a_team_member(demo_board):
    client, _ = demo_board
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Hi Kofi!</strong>" in html
    assert ">None<" not in html and '"None"' not in html
    for p in CONFIG.team:
        assert p.name in html
    assert "Add dark mode to the settings page" in html and "Settings page refresh" in html
    assert "avatars.githubusercontent.com" not in html  # nothing to load: the page shows initials
    assert client.get("/api/board.json").status_code == 200


def test_its_mine_and_checked_in_the_app_work_on_demo_data_and_survive_a_refresh(demo_board):
    client, clock = demo_board
    board = client.get("/api/board.json").get_json()
    free = board["unclaimed"][0]["number"]
    r = client.post(f"/api/issues/{free}/assignee", json={"assigned": True}, headers=JSON)
    assert r.status_code == 200 and r.get_json()["assignees"] == ["kofi-demo"]
    done = next(i for i in board["done_recent"] if i["kind"] == "issue" and i["state_reason"] == "completed" and not i["verified"])
    r = client.post(f"/api/issues/{done['number']}/checked", json={"checked": True, "notes": "Looks right"}, headers=JSON)
    assert r.status_code == 200 and r.get_json()["changed"] is True

    def state():
        b = client.get("/api/board.json").get_json()
        mine = next(p for p in b["people"] if p["login"] == "kofi-demo")["items"]
        return (free in [i["number"] for i in mine],
                next(i for i in b["done_recent"] if i["number"] == done["number"])["verified"])
    assert state() == (True, True)
    clock[0] += 400  # the cache's read is now stale: it reads again, and the changes must still be there
    assert state() == (True, True)
    clock[0] += 400
    assert state() == (True, True)
