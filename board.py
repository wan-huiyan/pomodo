"""Turn GitHub's issues and pull requests into the board's sections."""
import re
from calendar import monthrange
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import nh3

from config import CONFIG

PEOPLE = {p.login: p.name for p in CONFIG.team}  # in card order
_SPELLING = {login.lower(): login for login in PEOPLE}  # GitHub logins ignore case; the board uses the config's spelling
# Where each person works, for "after hours" in Recently done. Anyone else uses the board's clock.
PEOPLE_TZ = {p.login: p.zone for p in CONFIG.team}
TODO_LABEL = CONFIG.labels.chore
MILESTONE_LABEL = CONFIG.labels.milestone  # "" for none: its closed/total is a stat on the board
PRIORITY_LABEL = CONFIG.labels.priority
PRIORITY_TITLE = CONFIG.labels.priority_title
NICE_LABEL = CONFIG.labels.nice
TRACKER_LABEL = CONFIG.labels.tracker  # a checklist of other issues: shown as a progress card, never as a task
VERIFIED_LABEL = CONFIG.labels.verified  # a person checked the fix in the running app: a badge in Recently done
HIDDEN_LABELS = {MILESTONE_LABEL, PRIORITY_LABEL, NICE_LABEL} - {""}  # shown as a tag, a group or a stat, not as chips
GROUPS = [("priority", PRIORITY_TITLE), ("nice", "Nice to have"), (None, "Not sorted yet")]
RECENT_DAYS = 14
BOARD_TZ = ZoneInfo(CONFIG.zone)  # "Today" and "Yesterday" in Recently done follow the board's clock
ZONE_LABEL = CONFIG.zone_label  # how the page names that clock: "UK" in "times are UK time"
WORK_START, WORK_END = CONFIG.work_start, CONFIG.work_end  # working hours, Monday to Friday, on the finisher's own clock
HEAT_LEVELS = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 10 ** 6)]  # level k of a calendar day holds counts in HEAT_LEVELS[k]
NOT_DONE = {"not_planned", "duplicate"}  # closed, but not work that got done
GITHUB = "https://github.com/"
AVATARS = "https://avatars.githubusercontent.com/"

_TAGS = {"p", "br", "hr", "ul", "ol", "li", "code", "pre", "em", "strong", "b", "i", "del", "a",
         "blockquote", "table", "thead", "tbody", "tr", "th", "td", "h1", "h2", "h3", "h4", "h5", "h6",
         "details", "summary", "input", "span", "div", "sup", "sub", "kbd"}
_ATTRS = {"a": {"href", "title"}, "input": {"checked"}, "th": {"align"}, "td": {"align"}}
_CHECKBOX = re.compile(r"<input[^>]*task-list-item-checkbox[^>]*>")
_HEX = re.compile(r"^[0-9a-fA-F]{6}$")
_TASK_START = re.compile(r'<li class="task-list-item[^"]*">')
_TASK_END = re.compile(r"<li[\s>]|</li>")  # a line ends where it closes or where a nested line starts
_ITEM_LINK = re.compile(r'(?:data-url|href)="(https://github\.com/[^"]+?)/(?:issues|pull)/(\d+)(?:[#?][^"]*)?"')


def clean_html(html):
    """GitHub's own rendered HTML, cut down to plain formatting. No images, scripts, styles or forms."""
    return nh3.clean(
        html or "", tags=_TAGS, attributes=_ATTRS, url_schemes={"http", "https", "mailto"},
        tag_attribute_values={"input": {"type": {"checkbox"}}},
        set_tag_attribute_values={"input": {"disabled": "disabled"}, "a": {"target": "_blank"}},
    )


def task_counts(html):
    """(done, total) for the checkboxes in an issue body."""
    boxes = _CHECKBOX.findall(html or "")
    return sum(1 for b in boxes if re.search(r"\schecked", b)), len(boxes)


def tracked_numbers(node):
    """The issue and PR numbers a tracker lists as task lines (`- [ ] #N`), in order, from its own repo only."""
    repo = (node.get("url") or "").rsplit("/issues/", 1)[0]
    numbers = []
    html = node.get("bodyHTML") or ""
    for start in _TASK_START.finditer(html):
        end = _TASK_END.search(html, start.end())
        task = html[start.end():end.start() if end else len(html)]
        n = next((int(m.group(2)) for m in _ITEM_LINK.finditer(task) if m.group(1) == repo), None)
        if n is not None and n not in numbers:
            numbers.append(n)
    return numbers


def hour_text(h):
    """An hour of the day as people say it: "9 am", "6 pm", "noon", "midnight"."""
    if h % 24 == 0:
        return "midnight"
    return "noon" if h == 12 else f"{h % 12} {'am' if h < 12 else 'pm'}"


def team_names(names):
    """Names as a phrase: "Ada", "Ada and Kofi", "Ada, Kofi and Mei"."""
    names = list(names)
    return " and ".join(names) if len(names) < 3 else f"{', '.join(names[:-1])} and {names[-1]}"


def _when(s):
    return datetime.fromisoformat(s) if s else None


def _gh_url(value, prefix=GITHUB):
    """The URL only if it points where GitHub's URLs point, else None. They go into href and src."""
    return value if isinstance(value, str) and value.startswith(prefix) else None


def _person(user):
    login = (user or {}).get("login") or "ghost"
    login = _SPELLING.get(login.lower(), login)  # so "Ada-Demo" from GitHub lands on ada-demo's card
    return {"login": login, "name": PEOPLE.get(login, login),
            "avatar": _gh_url((user or {}).get("avatarUrl"), AVATARS) or ""}


def _comment(node, kind, now, **extra):
    at = _when(node.get("createdAt") or node.get("submittedAt"))
    return {"kind": kind, "author": _person(node.get("author")), "at": at.isoformat() if at else None,
            "ago": ago(at, now), "url": _gh_url(node.get("url")), "html": clean_html(node.get("bodyHTML")), **extra}


def _closed_by(node):
    """Who closed the issue, from its last ClosedEvent, if GitHub sent one. For a close by a merge: whoever merged."""
    events = ((node.get("timelineItems") or {}).get("nodes") or [])
    actor = events[-1].get("actor") if events else None
    return _person(actor) if actor else None


_REPO = re.compile(r"^https://github\.com/[^/]+/[^/]+/")
_MENTION = re.compile(r"(?<![\w/])#(\d+)\b")  # "#15", not "other/repo#15"


def _repo_of(url):
    m = _REPO.match(url or "")
    return m.group(0) if m else None


def _item(node, kind, now):
    state = node["state"].lower()  # open, closed, merged
    repo = _repo_of(node.get("url"))
    comments = [_comment(c, "comment", now) for c in node["comments"]["nodes"]]
    hidden = node["comments"]["totalCount"] - len(node["comments"]["nodes"])
    if kind == "pr":
        for r in node["reviews"]["nodes"]:
            if r.get("state") == "PENDING":  # someone's unsubmitted draft; the query already leaves these out
                continue
            if r.get("bodyHTML"):
                comments.append(_comment(r, "review", now, review_state=r["state"].lower()))
            comments += [_comment(c, "review_comment", now, path=c.get("path"))
                         for c in r["comments"]["nodes"]]
            hidden += r["comments"]["totalCount"] - len(r["comments"]["nodes"])
        hidden += max(0, node["reviews"]["totalCount"] - len(node["reviews"]["nodes"]))
    comments.sort(key=lambda c: c["at"] or "")
    done, total = task_counts(node.get("bodyHTML"))
    closed = _when(node.get("mergedAt") or node.get("closedAt"))
    updated = _when(node["updatedAt"])
    labels = [{"name": l["name"], "color": l["color"] if _HEX.match(l["color"] or "") else "888888"}
              for l in node["labels"]["nodes"]]
    names = {l["name"] for l in labels}
    # To-dos are admin jobs and trackers are lists of other issues: neither has a priority of its own.
    ranked = not names & {TODO_LABEL, TRACKER_LABEL}
    return {
        "kind": kind, "number": node["number"], "title": node["title"], "url": _gh_url(node.get("url")),
        "state": state, "draft": bool(node.get("isDraft")),
        "state_reason": (node.get("stateReason") or "").lower() or None,  # completed, not_planned, duplicate
        "author": _person(node.get("author")),
        "assignees": [_person(a) for a in node["assignees"]["nodes"]],
        "labels": labels, "chips": [l for l in labels if l["name"] not in HIDDEN_LABELS],
        "priority": None if not ranked else "priority" if PRIORITY_LABEL in names else "nice" if NICE_LABEL in names else None,
        "tracks": tracked_numbers(node) if kind == "issue" and TRACKER_LABEL in names else None,
        # A closed issue, or a merged pull request (one that closes no issue gets its tick on its own row).
        "verified": VERIFIED_LABEL in names and (kind == "issue" or state == "merged"),
        "created_at": _when(node["createdAt"]).isoformat(),
        "created_ago": ago(_when(node["createdAt"]), now), "updated_ago": ago(updated, now),
        "updated_at": updated.isoformat(), "closed_at": closed.isoformat() if closed else None,
        "closed_ago": ago(closed, now),
        "body_html": clean_html(node.get("bodyHTML")), "tasks": {"done": done, "total": total},
        "comments": comments, "comments_hidden": hidden,
        # Only this repo's issues: the board matches them by number.
        "closes": [n["number"] for n in node.get("closingIssuesReferences", {}).get("nodes", [])
                   if repo and _repo_of(n.get("url")) == repo],
        "mentions": sorted({int(n) for n in _MENTION.findall(node["title"] + " " + (node.get("bodyHTML") or ""))})
                    if kind == "pr" else [],  # issues a pull request names, for _closing_pr
        "closed_by": _closed_by(node) if kind == "issue" else None,
        "merged_by": _person(node["mergedBy"]) if node.get("mergedBy") else None,
        "review_decision": (node.get("reviewDecision") or "").lower() or None,
        "size": {"add": node.get("additions"), "del": node.get("deletions")} if kind == "pr" else None,
    }


def ago(when, now):
    if when is None:
        return None
    s = (now - when).total_seconds()
    for limit, div, unit in ((60, 1, "second"), (3600, 60, "minute"), (86400, 3600, "hour"),
                             (86400 * 30, 86400, "day"), (86400 * 365, 86400 * 30, "month")):
        if s < limit:
            n = max(1, int(s // div)) if unit != "second" else None
            return "just now" if n is None else f"{n} {unit}{'' if n == 1 else 's'} ago"
    n = int(s // (86400 * 365))
    return f"{n} year{'' if n == 1 else 's'} ago"


def _day_label(day, today):
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return f"{day:%A} {day.day} {day:%b}"


_COMMIT_PREFIX = re.compile(r"^\s*(?:(?:feat|fix|docs|ci|chore|refactor|tests?|perf|build|style)(?:\([^)]*\))?!?:|feature/)\s*",
                            re.IGNORECASE)


def _plain_title(item):
    """A pull request's title without a commit-style prefix ("fix(sql): ", "Feature/"), which means nothing to a reader."""
    if item["kind"] != "pr":
        return item["title"]
    t = _COMMIT_PREFIX.sub("", item["title"], count=1)
    return t[:1].upper() + t[1:] if t else item["title"]


def _credit(pr):
    """Who a merged pull request's work belongs to: its author if they are one of us, else whoever merged it."""
    return pr["author"] if pr["author"]["login"] in PEOPLE or not pr["merged_by"] else pr["merged_by"]


AUTO_CLOSE_SECONDS = 120  # GitHub closes the issues a merge fixes within seconds of the merge


def _closing_pr(issue, merged_prs):
    """The merged pull request that did the work of a closed issue, or None.

    A pull request counts if it lists the issue among the issues it closes, or if it names the issue (#N in its title or
    description) and the issue was closed within two minutes after the merge by the person who merged: that is GitHub
    closing it for a commit in the pull request. Of several, the one merged closest to the close.
    """
    closed = _when(issue["closed_at"])
    if closed is None:
        return None

    def merge_closed_it(pr):
        gap = (closed - _when(pr["closed_at"])).total_seconds()
        who, merger = issue["closed_by"], pr["merged_by"]
        return 0 <= gap <= AUTO_CLOSE_SECONDS and who and merger and who["login"] == merger["login"]

    found = [p for p in merged_prs if issue["number"] in p["closes"] or (issue["number"] in p["mentions"] and merge_closed_it(p))]
    return min(found, key=lambda p: abs((_when(p["closed_at"]) - closed).total_seconds()), default=None)


def recently_done(items, cutoff, now):
    """What got finished since cutoff, newest first, one row per piece of work, grouped by the board's day.

    A merged pull request carries the issues it closed, so a fix is one row, not two. Anything else closed is a row
    of its own. "who" is the person who did the work: a pull request's author, or whoever closed the issue.
    """
    closed = [i for i in items if i["state"] != "open" and i["closed_at"] and i["closed_at"] >= cutoff]
    merged_prs = [i for i in items if i["kind"] == "pr" and i["state"] == "merged" and i["closed_at"]]
    fixed_by = {i["number"]: _closing_pr(i, merged_prs) for i in closed if i["kind"] == "issue"}
    merged = {i["number"] for i in closed if i["state"] == "merged"}

    def rides(i):  # shown under its pull request's row, not as a row of its own
        pr = fixed_by.get(i["number"]) if i["kind"] == "issue" else None
        return pr is not None and pr["number"] in merged and i["state_reason"] not in NOT_DONE
    rows = []
    for i in closed:
        if rides(i):
            continue
        if i["kind"] == "pr":
            fixed = sorted((j for j in closed if rides(j) and fixed_by[j["number"]]["number"] == i["number"]),
                           key=lambda j: j["number"])
            if i["state"] == "merged":
                who, merger = _credit(i), i["merged_by"]
                verb = "merged this" if not merger or merger["login"] == who["login"] else f"made this, merged by {merger['name']}"
                rows.append({"verb": verb, "done": True, "who": who, "fixed": fixed, "item": i})
            else:
                rows.append({"verb": "closed this without merging", "done": False, "who": i["author"],
                             "fixed": [], "item": i})
            continue
        pr = fixed_by.get(i["number"])
        dropped = {"not_planned": "closed this as not planned", "duplicate": "closed this as a duplicate"}
        verb = dropped.get(i["state_reason"]) or (f"fixed this in PR #{pr['number']}" if pr else
                                                  "closed this as done" if i["state_reason"] == "completed" else "closed this")
        rows.append({"verb": verb, "done": i["state_reason"] not in dropped, "who": _credit(pr) if pr else i["closed_by"],
                     "fixed": [], "item": i})
    for r in rows:
        r["title"] = _plain_title(r["item"])
    rows.sort(key=lambda r: (r["item"]["closed_at"], r["item"]["number"]), reverse=True)

    today = now.astimezone(BOARD_TZ).date()
    days = []
    for r in rows:
        local = datetime.fromisoformat(r["item"]["closed_at"]).astimezone(BOARD_TZ)
        day = local.date()
        # Under "Today" say how long ago; under a dated heading "1 day ago" would fight the heading, so give the time.
        r["when"] = r["item"]["closed_ago"] if day == today else f"{local:%H:%M}"
        if not days or days[-1]["date"] != day.isoformat():
            days.append({"date": day.isoformat(), "label": _day_label(day, today), "rows": []})
        days[-1]["rows"].append(r)
    done = [r for r in rows if r["done"]]
    by = {}
    for r in done:
        if r["who"]:
            by[r["who"]["login"]] = by.get(r["who"]["login"], 0) + 1
    # "12 by Ada, 9 by Kofi and 2 by someone else": the parts always add up to the total.
    breakdown = [{"name": name, "n": by[login]} for login, name in PEOPLE.items() if by.get(login)]
    others = len(done) - sum(b["n"] for b in breakdown)
    if others:
        breakdown.append({"name": "someone else", "n": others})
    return days, {"total": len(done), "by": by, "breakdown": breakdown, "not_done": len(rows) - len(done)}


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _clock(r):
    """A row's time on the board's clock for the timeline, and the finisher's own time for "after hours"."""
    at = datetime.fromisoformat(r["item"]["closed_at"])
    login = r["who"]["login"] if r["who"] else ""
    tz = ZoneInfo(PEOPLE_TZ.get(login, str(BOARD_TZ)))
    here, own = at.astimezone(BOARD_TZ), at.astimezone(tz)
    # The weekend follows the board's day, like the panel the row sits in; the hours follow the finisher's own clock.
    kind = ("weekend" if here.weekday() >= 5 else "early" if own.hour < WORK_START
            else "late" if own.hour >= WORK_END else "")
    away = own.utcoffset() != here.utcoffset()
    return {"board": here, "own": own, "kind": kind, "login": login,
            "zone": "early" if here.hour < WORK_START else "night" if here.hour >= WORK_END else "day",
            "own_note": f"{own:%H:%M} {str(tz).rsplit('/', 1)[-1].replace('_', ' ')}" if away else ""}


def _day_says(d, rows, who, now):
    """What a day says about itself for one person (who) or everyone (""): counts, the stat line, the footer joke."""
    today = now.astimezone(BOARD_TZ)
    mine = [r for r in rows if not who or r["clock"]["login"] == who]
    done = [r for r in mine if r["done"]]
    weekend = d.weekday() >= 5
    late = [r for r in done if r["clock"]["kind"] in ("early", "late")]  # a weekend row is never after hours
    n = len(done)
    out = {"who": who, "n": n, "late": 0 if weekend else len(late), "has_rows": bool(mine),
           "level": next(k for k, (lo, hi) in enumerate(HEAT_LEVELS) if lo <= n <= hi)}
    if not mine:
        if who and rows:
            out["empty"] = f"Nothing by {PEOPLE[who]} on this day."
        elif d == today.date():
            out["empty"] = "Nothing finished yet today."
        elif weekend:
            out["empty"], out["mood"] = f"{d:%A}: nothing finished, as it should be.", "sleepy"
        else:
            out["empty"] = f"Nothing finished on {d:%A} {d.day} {d:%B}."
        out["stat"] = ""
        return out
    out["stat"] = f"{n} finished" if n else "Nothing finished"
    if weekend:
        if n:
            out["stat"] = f"{n} finished"
            out["stamp"] = "Weekend?!"
            out["foot"] = f"{_plural(n, 'thing')} finished on a {d:%A}. Pomo will pretend it didn't see this."
            out["mood"] = "worried"
        return out
    if late:
        out["stat"] += f", {len(late)} after hours"
        evening = [r["clock"]["own"].hour for r in late if r["clock"]["own"].hour >= WORK_END]
        latest = max(evening, default=None)
        out["mood"] = "worried"
        if latest is None:
            joke = f"Up before {hour_text(WORK_START)}? Pomo hasn't had its coffee yet."
        elif latest >= 22:
            joke, out["mood"] = "Way past bedtime. Even Pomo has gone to bed.", "sleepy"
        elif latest >= 20:
            joke = "Dinner was hours ago. Pomo would like a word."
        else:
            joke = f"Home time was {hour_text(WORK_END)}. Pomo is tapping its watch."
        lead = "All of these" if len(late) == n else _plural(len(late), "thing")
        out["foot"] = f"{lead} finished after hours. {joke}"
    elif n and (d < today.date() or (d == today.date() and today.hour >= WORK_END)):
        out["foot"], out["mood"] = "Nothing finished after hours. Pomo approves.", "sleepy"
    return out


def _says_label(d, today, says):
    """The calendar cell's aria-label and tooltip: the day, how much got finished, by whom, how much after hours."""
    long = f"{d:%A} {d.day} {d:%B}"
    out = {}
    for who, s in says.items():
        text = f"{long}{', today' if d == today else ''}: " + (f"{s['n']} finished" if s["n"] else "nothing finished")
        if not who and s["n"]:
            split = [f"{says[p]['n']} by {PEOPLE[p]}" for p in PEOPLE if says[p]["n"]]
            others = s["n"] - sum(says[p]["n"] for p in PEOPLE)
            if others:
                split.append(f"{others} by someone else")
            text += (f", all by {PEOPLE[next(p for p in PEOPLE if says[p]['n'])]}" if len(split) == 1 and not others
                     else f" ({', '.join(split)})")
        if s["late"]:
            text += f", {s['late']} after hours"
        out[who] = text
    return out


def done_calendar(done_days, now, first_day):
    """Recently done as a month calendar plus one timeline per day, from the days recently_done returned (newest first).

    The window is this month and last month on the board's clock; a month with no clickable day is left out. A day is "before"
    until first_day (the oldest day anything was closed), "future" after today, else "open". Counts are done rows:
    the calendar, the day panels and the month headline all count the same thing.
    """
    now_here = now.astimezone(BOARD_TZ)
    today = now_here.date()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    by_date = {d["date"]: d for d in done_days}
    who_all = [""] + list(PEOPLE)  # "" is everyone
    months, panels = [], []
    for first in (last_month, this_month):
        cells = []
        for k in range(1, monthrange(first.year, first.month)[1] + 1):
            d = first.replace(day=k)
            # Newest first, as recently_done made them. Copies, so the board's own rows stay as they were.
            rows = [{**r, "clock": _clock(r)} for r in by_date.get(d.isoformat(), {}).get("rows", [])]
            state = "before" if d < first_day else "future" if d > today else "open"
            says = {w: _day_says(d, rows, w, now) for w in who_all}
            for who, label in _says_label(d, today, says).items():
                says[who]["label"] = label
            cells.append({"date": d.isoformat(), "num": k, "state": state, "today": d == today,
                          "weekend": d.weekday() >= 5, "selected": d == today, "says": says})
            if state != "open":
                continue
            # The end-of-day marker goes under the evening rows, which are on top; with no evening rows it sits on top, once
            # the evening has happened (an early start has its line before then).
            evening = d < today or now_here.hour >= WORK_END
            divider = None
            if d.weekday() < 5 and (any(r["clock"]["zone"] == "night" for r in rows) or (evening and says[""].get("foot"))):
                divider = next((j for j, r in enumerate(rows) if r["clock"]["zone"] != "night"), len(rows))
            panels.append({"date": d.isoformat(), "title": _day_label(d, today), "long": f"{d:%A} {d.day} {d:%B}",
                           "rows": rows, "says": says, "divider": divider, "selected": d == today,
                           "weekend": d.weekday() >= 5, "is_today": d == today})
        if not any(c["state"] == "open" for c in cells):
            continue
        by = {login: sum(c["says"][login]["n"] for c in cells) for login in PEOPLE}
        by = {login: n for login, n in by.items() if n}
        total = sum(c["says"][""]["n"] for c in cells)
        breakdown = [{"name": PEOPLE[login], "n": n} for login, n in by.items()]
        if total > sum(by.values()):
            breakdown.append({"name": "someone else", "n": total - sum(by.values())})
        months.append({"key": f"{first:%Y-%m}", "label": f"{first:%B %Y}", "name": f"{first:%B}",
                       "current": first == this_month, "total": total, "by": by, "breakdown": breakdown,
                       "lead": first.weekday(), "cells": cells})
    latest = max((p for p in panels if p["date"] < today.isoformat() and p["says"][""]["n"]),
                 key=lambda p: p["date"], default=None)
    window = datetime.combine(last_month, time(0), BOARD_TZ)
    return {"today": today.isoformat(), "selected": today.isoformat(), "from": window.isoformat(),
            "latest_active": latest and {"date": latest["date"], "title": latest["title"], "long": latest["long"]},
            "months": months, "days": panels}


def build_board(raw, now, order="updated"):
    """order "newest" lists Up for grabs by the date each issue was opened, newest first. Every other list keeps
    its own order: last update, priority first in a person's card, by number in the priority card and chores.
    Ties go to the higher number, as when board.js re-sorts Up for grabs in the browser."""
    newest = order == "newest"
    items = [_item(n, "issue", now) for n in raw["issues"]] + [_item(n, "pr", now) for n in raw["pullRequests"]]
    open_items = [i for i in items if i["state"] == "open"]
    by_update = lambda i: (i["updated_at"], i["number"])  # noqa: E731
    rank = lambda i: [k for k, _ in GROUPS].index(i["priority"])  # noqa: E731
    priority_first = lambda xs: sorted(sorted(xs, key=by_update, reverse=True), key=rank)  # noqa: E731
    newest_first = lambda xs: sorted(xs, key=lambda i: (i["created_at"], i["number"]), reverse=True)  # noqa: E731
    by_number = lambda xs: sorted(xs, key=lambda i: i["number"])  # noqa: E731
    recent_first = lambda xs: sorted(xs, key=by_update, reverse=True)  # noqa: E731
    labelled = lambda i, name: any(l["name"] == name for l in i["labels"])  # noqa: E731
    cutoff = (now - timedelta(days=RECENT_DAYS)).isoformat()

    # Every open issue lands in exactly one of: to-dos, a tracker card, a person's card, up for grabs.
    open_issues = [i for i in open_items if i["kind"] == "issue"
                   and not labelled(i, TODO_LABEL) and not labelled(i, TRACKER_LABEL)]
    ours = lambda i: any(a["login"] in PEOPLE for a in i["assignees"])  # noqa: E731
    people = []
    for login, name in PEOPLE.items():
        mine = [i for i in open_issues if any(a["login"] == login for a in i["assignees"])]
        people.append({"login": login, "name": name, "items": priority_first(mine)})

    milestone = [i for i in items if i["kind"] == "issue" and MILESTONE_LABEL and labelled(i, MILESTONE_LABEL)]
    priority = [i for i in items if i["kind"] == "issue" and i["priority"] == "priority"]
    unclaimed = (newest_first if newest else recent_first)([i for i in open_issues if not ours(i)])
    states = {i["number"]: i["state"] for i in items}
    trackers = []
    for t in (i for i in open_items if i["tracks"] is not None):
        listed = [n for n in t["tracks"] if n in states]  # one that GitHub didn't return can't be counted
        trackers.append({"number": t["number"], "title": t["title"], "url": t["url"], "total": len(listed),
                         "closed": sum(1 for n in listed if states[n] != "open")})  # however it was closed
    _, finished = recently_done(items, cutoff, now)  # the hero stat keeps its 14 days
    today = now.astimezone(BOARD_TZ).date()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    # recently_done compares closed_at as text, so the window start must be UTC isoformat like the stored values.
    window = datetime.combine(last_month, time(0), BOARD_TZ).astimezone(timezone.utc).isoformat()
    done_days, _ = recently_done(items, window, now)
    first_day = min([today] + [datetime.fromisoformat(i["closed_at"]).astimezone(BOARD_TZ).date()
                               for i in items if i["state"] != "open" and i["closed_at"]])
    done_recent = sorted((i for i in items if i["state"] != "open" and i["closed_at"] and i["closed_at"] >= cutoff),
                         key=lambda i: i["closed_at"], reverse=True)
    return {
        "order": "newest" if newest else "updated",
        "todos": by_number([i for i in open_items if i["kind"] == "issue" and labelled(i, TODO_LABEL)
                            and not labelled(i, TRACKER_LABEL)]),
        "trackers": sorted(trackers, key=lambda t: t["number"]),
        # Every open priority issue, assigned or not: the top card. They also appear in a person's card or up for grabs.
        "priority_open": by_number([i for i in open_issues if i["priority"] == "priority"]),
        "people": people,
        "open_prs": recent_first([i for i in open_items if i["kind"] == "pr"]),
        "unclaimed": unclaimed,
        "unclaimed_groups": [{"key": k, "title": t, "items": [i for i in unclaimed if i["priority"] == k]}
                             for k, t in GROUPS],
        "done_recent": done_recent,
        "done_days": done_days,
        "calendar": done_calendar(done_days, now, first_day),
        "stats": {
            "open_issues": sum(1 for i in open_items if i["kind"] == "issue"),
            "open_prs": sum(1 for i in open_items if i["kind"] == "pr"),
            # Issues and pull requests are counted apart: a merged PR and the issues it closes are one piece of work.
            "closed_issues": sum(1 for i in done_recent if i["kind"] == "issue" and i["state_reason"] not in NOT_DONE),
            "merged_prs": sum(1 for i in done_recent if i["state"] == "merged"),
            "recent_days": RECENT_DAYS,
            "finished": finished,
            # None when no milestone label is set: the page then leaves the stat out.
            "milestone": {"label": MILESTONE_LABEL, "total": len(milestone),
                          "closed": sum(1 for i in milestone if i["state"] != "open")} if MILESTONE_LABEL else None,
            "priority": {"label": PRIORITY_LABEL, "title": PRIORITY_TITLE, "total": len(priority),
                         "closed": sum(1 for i in priority if i["state"] != "open")},
        },
        # The words the page needs from the settings, read here so they follow the module's constants.
        "team_names": team_names(PEOPLE.values()),
        "clock": {"zone": str(BOARD_TZ), "zone_label": ZONE_LABEL,
                  "work_start": hour_text(WORK_START), "work_end": hour_text(WORK_END)},
    }
