"""Made-up GitHub data for DEMO=1, so a new team sees a full board before it has a token.

make_raw returns what github.fetch_repo returns: {"issues": [...], "pullRequests": [...]}, the nodes shaped as GitHub's
GraphQL sends them. Who does what, and how many days back, is a fixed script; the random seed only varies the minutes.
So the same `now` always gives the same data, and every time in it is at or before `now`.
"""
import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import CONFIG

REPO_URL = "https://github.com/demo-team/demo-repo"  # made up: the board only needs links that start with github.com
OUTSIDER = "guest-dev"  # someone outside the team, who opens issues and sends pull requests
SEED = 7
WEEKS = 6  # how far back the finished work reaches
RECENT = 8  # the showcase items (after hours, weekend, verified ...) sit in the last 8 working days
# The "naughty" items all land on the latest working day, so that day shows the after-hours rows together.

# Open issues handed out in order: two to the first person, three to the second, four to the third, then 2, 3 ...
# (key, title, label, rank). The key lets the pull requests and the tracker point at an issue.
ASSIGNED = [
    ("dark", "Add dark mode to the settings page", "enhancement", ""),
    ("csv", "Fix CSV export dropping the last row", "bug", "priority"),
    ("tz", "Let people pick their own time zone", "enhancement", ""),
    ("picker", "Make the date picker work with the keyboard", "bug", ""),
    ("speed", "Speed up the first page load", "enhancement", "nice"),
    ("reply", "Email me when someone replies to my comment", "enhancement", ""),
    ("photo", "Profile photos are cropped on small screens", "bug", "priority"),
    ("guide", "Write a short guide for new teammates", "documentation", ""),
    ("names", "Long project names run off the card", "bug", ""),
    ("filter", "Remember the last filter I used", "enhancement", ""),
    ("upload", "Uploads fail silently for files over 10 MB", "bug", ""),
    ("print", "Add a print-friendly view of the report", "enhancement", "nice"),
    ("sort", "Sort the team list by name", "enhancement", ""),
    ("tooltip", "Tooltip text is too small to read", "bug", ""),
    ("shortcut", "Add a keyboard shortcut for new items", "enhancement", "nice"),
    ("sizes", "Show file sizes in the upload list", "enhancement", ""),
]
UNCLAIMED = [  # open and nobody's yet: two top priority, two nice to have, three not sorted
    ("login", "Sign-in page is blank on older browsers", "bug", "priority"),
    ("reset", "Password reset email never arrives for some addresses", "bug", "priority"),
    ("keys", "Add keyboard shortcuts for the common actions", "enhancement", "nice"),
    ("compact", "Offer a compact view for long lists", "enhancement", "nice"),
    ("import", "Support importing contacts from a spreadsheet", "enhancement", ""),
    ("help", "Add a link to the help page in the footer", "enhancement", ""),
    ("save", "Add a save confirmation to the settings form", "enhancement", ""),
]
CHORES = ["Renew the domain name before it expires", "Update the onboarding checklist in the wiki",
          "Clear out old test accounts"]
TRACKED = ("layout", "notif", "dark", "tz", "save")  # what the "Settings page refresh" tracker lists, and the milestone
BLURBS = ["Several people have run into this. Worth sorting out soon.", "Came up in this week's meeting.",
          "A customer asked about this twice last month.", "A small change, but it saves a few clicks every day.",
          "To see it: open the page, try it, and watch what happens."]
APPROVALS = ["<p>Looks good to me.</p>", "<p>Nice, thank you. I tried it locally and it works.</p>",
             "<p>Approved. One idea for later, but nothing that blocks this.</p>"]


def w(kind, title, *issues, label="", **flags):
    """One piece of finished work: a pull request that fixes issues ("fix"), one that fixes none ("plain"), an issue
    closed by hand ("hand"), or a pull request closed without merging ("abandoned")."""
    return {"kind": kind, "title": title, "issues": issues, "label": label, **flags}


FINISHED = [
    w("fix", "Give the settings page a cleaner layout", "Redesign the settings page layout", label="enhancement",
      key="layout", recent=True),
    w("fix", "Fix the broken link in the welcome email", "Welcome email links to a page that does not exist", label="bug"),
    w("fix", "Show the newest comments first", "Comments should show the newest first", label="enhancement", late="evening"),
    w("fix", "Add a loading spinner to the reports page", "Reports page looks frozen while it loads", label="enhancement"),
    w("fix", "Stop the sidebar jumping when it opens", "Sidebar jumps when it opens", label="bug"),
    w("fix", "Add pagination to the activity list", "Activity list is very slow with many entries", label="enhancement"),
    w("fix", "Check email addresses on sign-up", "Sign-up accepts addresses without an @", label="bug"),
    w("fix", "Use the same blue for every link", "Links are a different blue on each page", label="bug"),
    w("fix", "Cache the project list for a minute", "Project list reloads on every click", label="enhancement",
      late="evening"),
    w("fix", "Add alt text to the logo and icons", "Icons have no text for screen readers", label="bug",
      verified_issue=True, recent=True),
    w("fix", "Round the totals to two decimals", "Totals show too many decimal places", label="bug"),
    w("fix", "Let people reorder the dashboard cards", "Reorder the dashboard cards", "Remember the dashboard card order",
      label="enhancement", recent=True),
    w("plain", "Update the dependencies to their latest versions"),
    w("plain", "Tidy up the test setup", late="early", recent=True),
    w("plain", "Add a contributing guide", label="documentation"),
    w("plain", "Rename the Projects tab to Workspaces", verified=True, recent=True),
    w("fix", "Make the export button work on phones", "Export button does nothing on phones", label="bug",
      naughty="night", robot=True, by=1),
    w("plain", "Speed up the search box", label="enhancement", naughty="evening", robot=True, verified=True, by=0),
    w("plain", "Show the right date format in exports", label="bug", naughty="day", robot=True, by=2),
    w("plain", "Stop the weekly email going out twice", label="bug", weekend="afternoon", robot=True),
    w("plain", "Fix typos in the help pages", label="documentation", outsider=True),
    w("plain", "Add a favicon for dark browser themes", outsider=True, recent=True),
    w("hand", "Move notification options into their own tab", label="enhancement", key="notif"),
    w("hand", "Delete the old beta banner"),
    w("hand", "Reply to the feedback from the first testers", late="evening", recent=True),
    w("hand", "Archive the unused staging sandbox"),
    w("hand", "Check that backups restore correctly", verified=True, weekend=True),
    w("hand", "Collect screenshots for the help pages"),
    w("hand", "Confirm the new password rules with the team"),
    w("hand", "Set up a shared inbox for support questions"),
    w("hand", "Review the wording of all error messages"),
    w("hand", "Update the numbers on the pricing page"),
    w("hand", "Add the team calendar to the wiki"),
    w("hand", "Add a built-in chat window", reason="NOT_PLANNED", recent=True),
    w("abandoned", "Try a different font for headings", label="enhancement"),
    # Finished today, shortly before now: (the longest wait before now, and the share of today so far that is at most)
    w("plain", "Fix the spacing in the page footer", today=(25, 0.4)),
    w("hand", "Update the release notes", today=(180, 0.8)),
]


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # UTC, whole seconds, as GitHub sends them


def _user(login):
    return {"login": login, "avatarUrl": ""}  # no photo: the page shows initials, and its CSP allows only GitHub's avatars


def _task(number, done, text=""):
    """One task-list line as GitHub renders `- [x] #N` in bodyHTML (a plain line with no link when number is None)."""
    box = ('<input type="checkbox" id="" disabled="" class="task-list-item-checkbox" aria-label="Completed task" checked="">'
           if done else '<input type="checkbox" id="" disabled="" class="task-list-item-checkbox" aria-label="Incomplete task">')
    if number is None:
        return f'<li class="task-list-item">{box} {text}</li>'
    url = f"{REPO_URL}/issues/{number}"
    return (f'<li class="task-list-item">{box} <a class="issue-link js-issue-link" data-url="{url}" '
            f'data-hovercard-type="issue" href="{url}">#{number}</a></li>')


class _Demo:
    def __init__(self, now, config):
        self.now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
        self.rng = random.Random(SEED)
        self.labels = L = config.labels
        self.zone = ZoneInfo(config.zone)
        self.team = [p.login for p in config.team]
        self.outsider = OUTSIDER
        while self.outsider.lower() in {t.lower() for t in self.team}:
            self.outsider += "-x"
        self.zones = {p.login: ZoneInfo(p.zone) for p in config.team}
        self.zones[self.outsider] = self.zone
        self.start, self.end = config.work_start, config.work_end
        self.today = self.now.astimezone(self.zone).date()
        # The working days of the last WEEKS weeks on the board's clock, oldest first. Today is not one of them.
        self.days = [d for d in (self.today - timedelta(days=n) for n in range(WEEKS * 7, 0, -1)) if d.weekday() < 5]
        self.colors = {"bug": "d73a4a", "enhancement": "a2eeef", "documentation": "0075ca", L.priority: "d93f0b", L.nice: "0e8a16",
                       L.chore: "fbca04", L.tracker: "5319e7", L.verified: "2da44e", L.robot: "8b84d7"}
        if L.milestone:
            self.colors[L.milestone] = "1d76db"
        self.specs, self.keys = [], {}

    # ---------- moments ----------

    def ago(self, days=0, hours=0):
        """A moment before now, with minutes of its own."""
        return self.now - timedelta(days=days, hours=hours, minutes=self.rng.randint(0, 59))

    def slot(self, index, who, hour, minute):
        """hour:minute on a working day of the person's own clock: day `index`, or the nearest earlier one (else a later
        one) that is a weekday on the board's clock too and already in the past: a far-off zone can fall on the other side."""
        for i in [*range(index, -1, -1), *range(index + 1, len(self.days))]:
            d = self.days[i]
            t = datetime(d.year, d.month, d.day, hour, minute, tzinfo=self.zones[who]).astimezone(timezone.utc)
            if t < self.now - timedelta(minutes=1) and t.astimezone(self.zone).weekday() < 5:
                return t
        return self.now - timedelta(hours=2)  # not reached: some working day always fits

    def weekend(self, afternoon=False):
        """Late morning (or mid-afternoon) on the latest Saturday, on the board's clock."""
        d = next(self.today - timedelta(days=n) for n in range(1, 8) if (self.today - timedelta(days=n)).weekday() == 5)
        hour, minute = (15, 5) if afternoon else (11, 20)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=self.zone).astimezone(timezone.utc)

    def earlier_today(self, minutes, share):
        """A moment today: `minutes` before now, but no further back than `share` of the time since midnight."""
        midnight = datetime.combine(self.today, datetime.min.time(), self.zone).astimezone(timezone.utc)
        back = min(timedelta(minutes=minutes), (self.now - midnight) * share)
        return (self.now - back).replace(microsecond=0)

    def hour(self, late):
        if late == "night" and self.end <= 22:
            return self.rng.randint(22, 23)
        if late == "evening" and self.end < 24:
            return self.rng.randint(self.end, min(23, self.end + 3))
        if late == "early" and self.start > 0:
            return self.rng.randint(max(0, self.start - 3), self.start - 1)
        return self.rng.randint(self.start, max(self.start, self.end - 1))

    # ---------- building blocks ----------

    def add(self, kind, title, created, updated, key=None, **more):
        key = key or f"item{len(self.specs)}"
        assert key not in self.keys, key
        spec = {"key": key, "kind": kind, "title": title, "created": created, "updated": updated, "state": "OPEN",
                "author": self.team[0], "assignees": [], "labels": [], "body": "", "reason": None, "closed": None,
                "closer": None, "comments": [], "draft": False, "merged_by": None, "closes": [], "decision": None,
                "reviews": [], "size": (0, 0), **more}
        self.specs.append(spec)
        self.keys[key] = spec
        return spec

    def tags(self, *names, rank="", milestone=False):
        L = self.labels
        out = [n for n in names if n] + ([L.priority] if rank == "priority" else [L.nice] if rank == "nice" else [])
        return out + ([L.milestone] if milestone and L.milestone else [])

    def talk(self, spec, comments):
        """Comments [(login, html)] spread evenly between when the item was opened and when it last changed."""
        span = spec["updated"] - spec["created"]
        spec["comments"] = [(who, html, (spec["created"] + span * (j + 1) / (len(comments) + 1)).replace(microsecond=0))
                            for j, (who, html) in enumerate(comments)]

    def pick(self, *pool):
        return self.rng.choice(pool)

    def open_issue(self, key, title, label, rank="", owner=None, **more):
        created, updated = self.ago(self.rng.randint(7, 38), self.rng.randint(0, 23)), self.ago(self.rng.randint(0, 6), self.rng.randint(1, 20))
        return self.add("issue", title, created, updated, key=key, author=self.pick(*self.team, self.outsider),
                        assignees=[owner] if owner else [], labels=self.tags(label, rank=rank, milestone=key in TRACKED),
                        body=f"<p>{self.pick(*BLURBS)}</p>", **more)

    # ---------- what is open ----------

    def open_work(self):
        L, team, rng = self.labels, self.team, self.rng
        n = 0
        for i, login in enumerate(team):  # two to four open issues each
            for _ in range(2 + i % 3):
                key, title, label, rank = ASSIGNED[n % len(ASSIGNED)]
                self.open_issue(key if n < len(ASSIGNED) else f"{key}{n}", title, label, rank, owner=login)
                n += 1
        guide = self.keys.get("guide")
        if guide:  # an issue with its own small checklist
            guide["body"] = ('<p>What the guide should cover:</p><ul class="contains-task-list">'
                             + _task(None, True, "How we work") + _task(None, False, "Where things live")
                             + _task(None, False, "Who to ask") + "</ul>")
        for key, title, label, rank in UNCLAIMED:
            self.open_issue(key, title, label, rank)
        for i, title in enumerate(CHORES):
            spec = self.open_issue(f"chore{i}", title, "", owner=team[0] if i == 1 else None)
            spec["labels"] = [L.chore]
        # Comments, written by the others where there are others.
        other = lambda login: next((p for p in team if p != login), self.outsider)  # noqa: E731
        csv = self.keys["csv"]
        who = csv["assignees"][0]
        self.talk(csv, [(other(who), "<p>I can reproduce this with a file of three rows. The last line is always missing.</p>"),
                        (who, "<p>Found it: the loop stops one row early. A fix is on its way.</p>")])
        self.talk(self.keys["login"], [(self.outsider, "<p>Same here on an older tablet.</p>")])
        self.talk(self.keys["chore1"], [(team[0], "<p>Reminder: please keep the old checklist until the new one is signed off.</p>")])
        self.talk(self.keys["reset"], [(team[-1], "<p>Looks like the mail provider holds some addresses back. Checking with them.</p>"),
                                       (other(team[-1]), "<p>Thanks! I can help test once there is a fix.</p>")])
        # The tracker: a checklist of other issues, shown as a progress card.
        def tracker_body(numbers):
            lines = "".join(_task(numbers[k], self.keys[k]["state"] != "OPEN") for k in TRACKED if k in self.keys)
            return f'<p>Everything we want in the settings refresh:</p><ul class="contains-task-list">{lines}</ul>'
        self.add("issue", "Settings page refresh", self.ago(46, 3), self.ago(0, 5), key="tracker",
                 author=team[0], labels=[L.tracker], body=tracker_body)
        self.talk(self.keys["tracker"], [(team[0], "<p>I will tick these off as they land.</p>")])

    def open_prs(self):
        team, K, rng = self.team, self.keys, self.rng
        owner = K["csv"]["assignees"][0]
        self.add("pr", "Include the last row in CSV exports", self.ago(3, 2), self.ago(0, 4), key="pr_csv", author=owner,
                 assignees=[owner], draft=True, closes=["csv"], labels=["bug"], size=(rng.randint(8, 40), rng.randint(2, 12)))
        picker = K.get("picker")
        author = picker["assignees"][0] if picker else team[0]
        reviewer = next((p for p in team if p != author), self.outsider)
        at = self.ago(0, rng.randint(1, 12))
        self.add("pr", "Let the date picker be used with the keyboard", self.ago(rng.randint(2, 5), 4), at, key="pr_picker",
                 author=author, assignees=[author], closes=["picker"] if picker else [], labels=["bug"],
                 decision="CHANGES_REQUESTED", size=(rng.randint(60, 180), rng.randint(10, 60)),
                 reviews=[{"author": reviewer, "state": "CHANGES_REQUESTED", "at": at,
                           "html": "<p>Nice start! Could the Escape key close the picker too?</p>",
                           "comments": [("src/datepicker.js", "<p>This skips days that are hidden. Is that on purpose?</p>")]}])
        at = self.ago(0, rng.randint(13, 22))
        self.add("pr", "Add Spanish translations for the sign-in page", self.ago(rng.randint(3, 6), 2), at, key="pr_spanish",
                 author=self.outsider, decision="APPROVED", labels=["enhancement", self.labels.robot], size=(rng.randint(30, 90), rng.randint(0, 8)),
                 reviews=[{"author": team[0], "state": "APPROVED", "at": at,
                           "html": "<p>Thank you, these read well to me.</p>", "comments": []}])

    # ---------- what is finished ----------

    def finished(self):
        rng, team = self.rng, self.team
        work = list(FINISHED)
        rng.shuffle(work)
        spread = [x for x in work if not (x.get("recent") or x.get("today") or x.get("weekend") or x.get("naughty"))]
        seen = recent = 0
        for k, item in enumerate(work):
            who = team[item.get("by", k) % len(team)]  # whose work it is
            if item.get("today"):
                t = self.earlier_today(*item["today"])
            elif item.get("weekend"):
                t = self.weekend(afternoon=item["weekend"] == "afternoon")
            elif item.get("naughty"):
                naughty = item["naughty"]
                t = self.slot(len(self.days) - 1, who, self.hour("" if naughty == "day" else naughty), rng.randint(0, 59))
            else:
                if item.get("recent"):
                    index, recent = len(self.days) - 1 - recent % RECENT, recent + 1
                else:  # evenly through the weeks, a little more of it lately
                    index, seen = min(len(self.days) - 1, int(((seen + rng.random()) / len(spread)) ** 0.7 * len(self.days))), seen + 1
                t = self.slot(index, who, self.hour(item.get("late")), rng.randint(0, 59))
            self.finish(item, k, who, t)

    def finish(self, item, k, who, t):
        rng, team, L = self.rng, self.team, self.labels
        kind, label = item["kind"], item["label"]
        verified = ([L.verified] if item.get("verified") else []) + ([L.robot] if item.get("robot") else [])
        if kind == "hand":
            self.add("issue", item["title"], t - timedelta(days=rng.randint(2, 14), hours=rng.randint(0, 6)), t,
                     key=item.get("key"), author=self.pick(*team), state="CLOSED", closed=t, closer=who,
                     reason=item.get("reason") or "COMPLETED", labels=self.tags(label, *verified, milestone="key" in item),
                     body=f"<p>{self.pick(*BLURBS)}</p>")
            return
        outsider = item.get("outsider")
        author = self.outsider if outsider else who
        merger = who
        if not outsider and len(team) > 1 and k % 4 == 3:  # now and then a teammate merges it
            merger = team[(k + 1) % len(team)]
        opened = t - timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 6))
        size = (rng.randint(5, 240), rng.randint(0, 120))
        if kind == "abandoned":
            self.add("pr", item["title"], opened, t, author=who, assignees=[who], state="CLOSED", closed=t,
                     labels=[label], size=size)
            return
        issues = []
        for j, title in enumerate(item["issues"]):
            closed = min(self.now, t + timedelta(seconds=rng.randint(5, 40)))  # GitHub closes them just after the merge
            on = [L.verified] if item.get("verified_issue") and j == 0 else []
            issues.append(self.add("issue", title, opened - timedelta(days=rng.randint(0, 6), hours=rng.randint(1, 10)), closed,
                                   key=item.get("key") if j == 0 else None, author=self.pick(*team, self.outsider),
                                   state="CLOSED", closed=closed, closer=merger, reason="COMPLETED",
                                   labels=self.tags(label, *on, milestone=bool(item.get("key"))), body=f"<p>{self.pick(*BLURBS)}</p>"))
        reviews, decision = [], None
        reviewers = [p for p in team if p not in (author, merger)]
        if reviewers and rng.random() < 0.5:
            at = t - timedelta(minutes=rng.randint(15, 180))
            reviews = [{"author": rng.choice(reviewers), "state": "APPROVED", "at": at, "html": self.pick(*APPROVALS), "comments": []}]
            decision = "APPROVED"
        self.add("pr", item["title"], opened, t, author=author, assignees=[] if outsider else [author], state="MERGED",
                 closed=t, merged_by=merger, closes=[i["key"] for i in issues], labels=[x for x in [label, *verified] if x],
                 decision=decision, reviews=reviews, size=size)

    # ---------- the finished read ----------

    def node(self, s, number):
        path = "pull" if s["kind"] == "pr" else "issues"
        url = f"{REPO_URL}/{path}/{number[s['key']]}"
        comments = [{"author": _user(who), "bodyHTML": html, "createdAt": _iso(at), "url": f"{url}#issuecomment-{1000 * number[s['key']] + j}"}
                    for j, (who, html, at) in enumerate(s["comments"])]
        node = {
            "number": number[s["key"]], "title": s["title"], "url": url, "state": s["state"],
            "createdAt": _iso(s["created"]), "updatedAt": _iso(s["updated"]),
            "closedAt": _iso(s["closed"]) if s["closed"] else None,
            "bodyHTML": s["body"](number) if callable(s["body"]) else s["body"],
            "author": _user(s["author"]), "assignees": {"nodes": [_user(a) for a in s["assignees"]]},
            "labels": {"nodes": [{"name": n, "color": self.colors.get(n, "ededed")} for n in s["labels"]]},
            "comments": {"totalCount": len(comments), "nodes": comments},
        }
        if s["kind"] == "issue":
            node["stateReason"] = s["reason"]
            node["timelineItems"] = {"nodes": [{"actor": _user(s["closer"])}] if s["closer"] else []}
            return node
        reviews = []
        for r in s["reviews"]:
            base = f"{url}#pullrequestreview-{1000 * number[s['key']]}"
            inline = [{"author": _user(r["author"]), "bodyHTML": html, "createdAt": _iso(r["at"]), "url": f"{url}#discussion_r{1000 * number[s['key']] + j}",
                       "path": p} for j, (p, html) in enumerate(r["comments"])]
            reviews.append({"author": _user(r["author"]), "state": r["state"], "bodyHTML": r["html"], "submittedAt": _iso(r["at"]),
                            "url": base, "comments": {"totalCount": len(inline), "nodes": inline}})
        node.update(
            isDraft=s["draft"], mergedAt=_iso(s["closed"]) if s["state"] == "MERGED" else None, reviewDecision=s["decision"],
            additions=s["size"][0], deletions=s["size"][1],
            headRefName=re.sub(r"[^a-z0-9]+", "-", s["title"].lower()).strip("-")[:40], baseRefName="main",
            mergedBy=_user(s["merged_by"]) if s["merged_by"] else None,
            closingIssuesReferences={"nodes": [{"number": number[k], "url": f"{REPO_URL}/issues/{number[k]}"} for k in s["closes"]]},
            reviews={"totalCount": len(reviews), "nodes": reviews})
        return node

    def build(self):
        self.specs.sort(key=lambda s: s["created"])  # GitHub numbers issues and pull requests in one sequence, oldest first
        number = {s["key"]: i for i, s in enumerate(self.specs, 1)}
        nodes = [(s["kind"], self.node(s, number)) for s in self.specs]
        return {"issues": [n for kind, n in nodes if kind == "issue"], "pullRequests": [n for kind, n in nodes if kind == "pr"]}


def make_raw(now, config=CONFIG):
    """A small team's repo as of `now`, for the configured team and label names. Same shape as github.fetch_repo."""
    demo = _Demo(now, config)
    demo.open_work()
    demo.open_prs()
    demo.finished()
    return demo.build()
