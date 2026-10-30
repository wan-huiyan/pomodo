from datetime import datetime, timedelta, timezone

import pytest

import main
from github import Cache, GitHubError
from test_auth import sign_in
from test_board import _closed_by, _node


def _raw():
    review = _node(30, kind="pr")
    review["reviews"] = {"totalCount": 1, "nodes": [
        {"author": {"login": "kofi-demo"}, "state": "COMMENTED", "bodyHTML": "<p>looks fine</p>", "submittedAt": None,
         "url": "https://github.com/o/r/pull/30#r1",
         "comments": {"totalCount": 0, "nodes": []}}]}
    return {"issues": [_node(1, labels=["todo"], assignees=["ada-demo"]), _node(2)], "pullRequests": [review]}


@pytest.fixture
def client(monkeypatch):
    def use(loader, **kw):
        monkeypatch.setattr(main, "cache", Cache(loader, ttl=300, spawn=lambda f: f(), **kw))
        c = main.app.test_client()
        sign_in(c, "ada-demo")
        return c
    return use


def test_board_renders_without_none_and_with_strict_csp(client):
    r = client(_raw).get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert ">None<" not in html and '"None"' not in html
    assert "Assigned to Ada" in html and "Raw data (JSON)" in html
    csp = r.headers["Content-Security-Policy"]
    assert "form-action 'self'" in csp  # the sign-out button posts to the board itself
    assert "script-src 'self' https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/;" in csp
    assert "googleapis" not in csp and "gstatic" not in csp and "'unsafe-inline'" not in csp.split("style-src-attr")[0]


def test_stale_banner_says_what_happened_and_what_to_do(client):
    t, calls = [0.0], []

    def loader():
        calls.append(1)
        if len(calls) > 1:
            raise GitHubError("GitHub returned HTTP 502")
        return _raw()

    c = client(loader, clock=lambda: t[0])
    c.get("/")
    t[0] = 400
    html = c.get("/").get_data(as_text=True)
    assert "GitHub could not be reached just now, so this is the version from" in html
    assert "Reload in a minute." in html and '<span class="stale-why">GitHub returned HTTP 502</span>' in html


def test_unexpected_error_before_any_read_is_a_503_page_not_a_500(client):
    def loader():
        raise ValueError("Expecting value")

    c = client(loader)
    r = c.get("/")
    assert r.status_code == 503 and "Unexpected error reading GitHub (ValueError)" in r.get_data(as_text=True)
    r = c.get("/api/board.json")
    assert r.status_code == 503 and r.get_json() == {"error": "Unexpected error reading GitHub (ValueError)"}


def test_every_script_is_one_the_csp_allows_and_the_sparkle_files_exist(client):
    import pathlib
    import re
    html = client(_raw).get("/").get_data(as_text=True)
    srcs = re.findall(r'<script src="([^"]+)"', html)
    assert all(s.startswith("/static/") or s.startswith("https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/") for s in srcs)
    sparkle = [s for s in srcs if s.startswith("/static/sparkle/")]
    assert sparkle[-1] == "/static/sparkle/magic.js" and len(sparkle) == 7  # it starts the effects, so it loads after them
    root = pathlib.Path(main.__file__).parent
    for s in sparkle + ["/static/sparkle/sparkle.css"]:
        assert (root / s.lstrip("/")).is_file(), s
    assert "<style" not in html  # style-src has no 'unsafe-inline'


def test_the_sparkle_pieces_name_each_other_consistently(client):
    """The effects find each other by name across files; a rename in one place would fail silently in the page."""
    import pathlib
    import re
    html = client(_raw).get("/").get_data(as_text=True)
    sparkle = pathlib.Path(main.__file__).parent / "static" / "sparkle"
    js = {f.stem: f.read_text() for f in sparkle.glob("*.js")}
    effects = re.findall(r'"(\w+)"', re.search(r"EFFECTS = \[([^\]]+)\]", js["magic"]).group(1))
    assert len(effects) == 6
    for name in effects:  # magic.js skips an effect it can't find, so check each one is registered
        assert sum(f"SparkleThatPage.{name} = " in src for src in js.values()) == 1, name
    assert 'href="/static/sparkle/sparkle.css"' in html
    assert '<div class="stp-mascot"' in html and ".stp-mascot" in js["scroll_indicator"]
    assert "magic-toggle" not in html and "no-magic" not in "".join(js.values())  # the effects are always on
    board_js = (pathlib.Path(main.__file__).parent / "static" / "board.js").read_text()
    assert '"pomodo:taken"' in board_js and '"pomodo:taken"' in js["magic"]
    # star bursts go only to elements marked data-burst, which must never be <html> or <body>: every click would match
    assert '"[data-burst]"' in js["click_confetti"]
    for tag in re.findall(r"<(?:html|body)\b[^>]*>", html):
        assert not re.search(r"\sdata-burst[\s=>]", tag), tag
    assert len(re.findall(r'<span class="person-photo"[^>]*\sdata-burst[\s>]', html)) == 3  # one per person on the team


def test_checked_in_the_app_shows_on_what_is_labelled_human_verified(client):
    ago = lambda h: (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()  # noqa: E731  # main uses the real now
    fix = _node(129, kind="pr", state="MERGED", closed=ago(2), closes=(120, 121))
    checked_pr = _node(134, kind="pr", state="MERGED", closed=ago(1), closes=(), labels=["human-verified"])
    unmerged = _node(135, kind="pr", state="CLOSED", closed=ago(1), closes=(), labels=["human-verified"])
    raw = {"issues": [
        _node(120, state="CLOSED", closed=ago(2), labels=["human-verified"]),  # under #129's row
        _node(121, state="CLOSED", closed=ago(2)),  # closed by the same pull request, not checked
        _node(39, state="CLOSED", closed=ago(5), reason="COMPLETED", labels=["human-verified"]),  # a row of its own
        _node(40, state="CLOSED", closed=ago(5), reason="COMPLETED"),
    ], "pullRequests": [fix, checked_pr, unmerged]}
    html = client(lambda: raw).get("/").get_data(as_text=True)
    assert html.count('class="tag tag--checked"') == 3  # not on 121, 40 or a pull request closed without merging
    assert 'PR #134</span> Item 134</a> <span class="tag tag--checked"' in html  # a merged one that closes no issue
    assert '#120</a> Item 120 <span class="tag tag--checked"' in html
    assert '#39</span> Item 39</a> <span class="tag tag--checked"' in html
    assert ">human-verified<" not in html  # the badge, never a raw label chip
    js = (__import__("pathlib").Path(main.__file__).parent / "static" / "board.js").read_text()
    assert "human-verified" not in js and js.count('btn.getAttribute("data-label")') == 2  # the label's name comes from the page


def test_the_sky_switch_sets_the_theme_before_the_page_paints(client):
    """theme.js must run in <head> without defer, or a stored dark choice flashes light first."""
    import pathlib
    import re
    html = client(_raw).get("/").get_data(as_text=True)
    head = html.split("</head>", 1)[0]
    assert '<script src="/static/theme.js"></script>' in head
    assert head.index("/static/theme.js") < head.index("/static/board.css")
    assert len(re.findall(r'<button type="button" class="sky-toggle" role="switch" aria-checked="false"[^>]* hidden>', html)) == 2
    theme_js = (pathlib.Path(main.__file__).parent / "static" / "theme.js").read_text()
    assert '".sky-toggle"' in theme_js and '"pomodo:theme"' in theme_js
    # the error page reads the same choice, so a dark choice doesn't flash light there either
    def fails():
        raise ValueError("x")
    error = client(fails).get("/").get_data(as_text=True)
    assert '<script src="/static/theme.js"></script>' in error.split("</head>", 1)[0]


def test_every_css_rule_has_a_selector():
    """A rule left with no selector ("{ ... }") is dropped by the browser without any error."""
    import pathlib
    import re
    static = pathlib.Path(main.__file__).parent / "static"
    for f in [static / "board.css", static / "sparkle" / "sparkle.css"]:
        bare = [n for n, line in enumerate(f.read_text().splitlines(), 1) if re.match(r"\s*\{", line)]
        assert not bare, f"{f.name}: a rule with no selector on line(s) {bare}"


def test_the_two_dark_token_blocks_match():
    """Dark comes from the computer's setting or from the sky switch; both blocks must set the same tokens."""
    import pathlib
    import re
    css = (pathlib.Path(main.__file__).parent / "static" / "board.css").read_text()
    by_system = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([^}]*)\}', css)
    by_switch = re.search(r':root\[data-theme="dark"\] \{([^}]*)\}', css)
    tokens = lambda block: sorted(" ".join(line.split()) for line in block.split(";") if line.strip())  # noqa: E731
    assert by_system and by_switch
    assert tokens(by_system.group(1)) == tokens(by_switch.group(1)) and "--bg:" in by_switch.group(1)


def test_the_order_switch_is_only_in_up_for_grabs_and_each_row_carries_both_dates(client):
    """board.js re-sorts Up for grabs in place, without loading a page (a page load painted at the top first,
    then jumped to the section), so each row carries both dates and the CSS shows the one that matches."""
    import pathlib
    import re
    c = client(_raw)
    usual = c.get("/").get_data(as_text=True)
    new = c.get("/?order=newest").get_data(as_text=True)
    for html, current, order in ((usual, "Recently updated", "updated"), (new, "Recently opened", "newest")):
        assert html.count('<div class="order-switch" role="group"') == 1
        assert re.search(rf' aria-current="true"[^>]*>{current}</a>', html) and html.count('aria-current="true"') == 1
        assert f'<section id="grabs" class="chapter wrap grabs" data-order="{order}"' in html
    assert 'href="/?order=newest#grabs"' in usual and 'href="/#grabs"' in new and "order=newest#people" not in usual
    grabs = usual.split('<section id="grabs"', 1)[1].split('<section id="chores"', 1)[0]
    assert grabs.count('<span class="meta-time meta-time--updated">Updated <time') == 1
    assert grabs.count('<span class="meta-time meta-time--opened">Opened <time') == 1
    assert re.search(r'<li class="reveal-row" data-number="2" data-updated="[^"]+" data-created="[^"]+">', grabs)
    assert "meta-time--opened" not in usual.split('<section id="grabs"', 1)[0]  # other lists: when updated only
    assert "the top priority ones first" in new  # Who has what keeps its order
    css = (pathlib.Path(main.__file__).parent / "static" / "board.css").read_text()
    assert '.grabs[data-order="newest"] .meta-time--updated' in css and '.grabs:not([data-order="newest"]) .meta-time--opened' in css
    assert c.get("/api/board.json?order=newest").get_json()["order"] == "newest"
    assert 'data-order="updated"' in c.get("/?order=bogus").get_data(as_text=True)


def test_a_page_opened_at_a_section_lands_there_and_only_link_clicks_glide():
    """With scroll-behavior: smooth on the whole page, a page opened at #section (the order switch) glided down
    from the top as it loaded, and ScrollTrigger's measuring at load stopped the glide partway, often at the top.
    So no stylesheet sets it; board.js turns it on only for a mouse click on a #link after the page has loaded
    (the same measuring would stop a glide started earlier), until that glide ends."""
    import pathlib
    static = pathlib.Path(main.__file__).parent / "static"
    for f in [static / "board.css", static / "sparkle" / "sparkle.css"]:
        assert "scroll-behavior" not in f.read_text(), f.name
    js = (static / "board.js").read_text()
    assert "closest('a[href^=\"#\"]')" in js and 'root.style.scrollBehavior = "smooth"' in js and "initGlide" in js
    assert 'e.detail === 0 || doc.readyState !== "complete"' in js


def test_the_footer_says_when_github_is_read_again(monkeypatch):
    """The board reads GitHub once the copy is 2 minutes old, and the visit that starts the read still shows the old
    copy. An open page asks every minute (/api/version), so the read happens while the board is open."""
    t, spawned = [0.0], []
    monkeypatch.setattr(main, "cache", Cache(_raw, ttl=300, clock=lambda: t[0], spawn=spawned.append))
    monkeypatch.setattr(main, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: datetime.fromtimestamp(t[0], timezone.utc)),
        "fromtimestamp": staticmethod(datetime.fromtimestamp)}))
    c = main.app.test_client()
    sign_in(c, "ada-demo")
    fresh = c.get("/").get_data(as_text=True)
    assert "Read again every 2&nbsp;minutes while the board is open" in fresh and "Refreshes every" not in fresh
    t[0] = 480
    old = c.get("/").get_data(as_text=True)
    assert len(spawned) == 1  # this visit started the read, and still shows the copy from 8 minutes ago
    assert "8 minutes ago" in old and 'A newer read is on its way: a "Board updated" button appears' in old
    assert "Read again every" not in old


def _freeze(monkeypatch, iso):
    """Make main.py's clock, and the cache's, read this moment (the page is built from main.datetime.now)."""
    moment = datetime.fromisoformat(iso)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(main, "datetime", Frozen)
    return moment


def _closed_raw(*closed):
    """Closed issues as (number, closed at, who closed it); one open issue so the board has something else on it."""
    issues = [_closed_by(_node(n, state="CLOSED", closed=at, reason="COMPLETED"), who) for n, at, who in closed]
    return {"issues": [_node(1), *issues], "pullRequests": []}


def _panels(html):
    """Every day panel of the calendar as {date: hidden?}, in page order."""
    import re
    return {m.group(1): " hidden" in m.group(2) for m in re.finditer(r'<section class="tl[^"]*" id="tl-([\d-]+)"([^>]*)>', html)}


def test_the_calendar_shows_one_month_with_today_open_and_each_person_counted(client, monkeypatch):
    import re
    moment = _freeze(monkeypatch, "2026-10-29T11:00:00+00:00")  # Thursday 12:00 UK, the second-oldest closing is 21 Oct
    raw = _closed_raw((10, "2026-10-28T10:00:00Z", "ada-demo"), (11, "2026-10-21T09:00:00Z", "kofi-demo"),
                      (12, "2026-10-29T09:00:00Z", "ada-demo"))
    html = client(lambda: raw, clock=lambda: moment.timestamp()).get("/").get_data(as_text=True)
    assert ">None<" not in html and '"None"' not in html
    assert html.count('class="cal-grid"') == 1 and 'data-month="2026-10"' in html  # September has no clickable day
    panels = _panels(html)
    assert sorted(panels) == [f"2026-10-{d}" for d in range(21, 30)]  # 21 Oct, the first day anything closed, to today
    assert [d for d, hidden in panels.items() if not hidden] == ["2026-10-29"]  # today, and only today, is open
    day = re.search(r'<button type="button" class="cal-day[^"]*" data-date="2026-10-28"[^>]*>', html).group(0)
    assert 'data-n-all="1"' in day and 'data-n-ada-demo="1"' in day and 'data-n-kofi-demo="0"' in day
    assert 'data-level-all="1"' in day and 'data-late-all="0"' in day and 'data-label-all="Wednesday 28 October: 1 finished, all by Ada"' in day
    assert 'aria-pressed="true"' in re.search(r'<button[^>]*data-date="2026-10-29"[^>]*>', html).group(0)
    for gone in ("show-all", "data-earlier", 'class="day"', "data-days="):
        assert gone not in html, gone
    # the since line knows where the window starts (1 Sep UK, as an offset time) and the first day the calendar shows
    assert 'data-since data-zone="Europe/London" data-from="2026-09-01T00:00:00+01:00" data-first="21 October"' in html
    text = re.sub(r"<[^>]+>", "", html)
    assert "3 things finished in October: 2 by Ada and 1 by Kofi." in text
    assert 'data-month-variant="2026-10">' in html and "hidden" not in re.search(r'<p class="done-big"[^>]*>', html).group(0)


def test_on_the_first_of_a_month_today_is_empty_and_yesterdays_work_is_in_last_months_grid(client, monkeypatch):
    import re
    moment = _freeze(monkeypatch, "2026-11-02T10:00:00+00:00")  # Monday 10:00 UK, nothing finished yet today
    raw = _closed_raw((10, "2026-10-30T16:00:00Z", "ada-demo"), (11, "2026-10-21T09:00:00Z", "kofi-demo"))
    html = client(lambda: raw, clock=lambda: moment.timestamp()).get("/").get_data(as_text=True)
    grids = re.findall(r'<div class="cal-month-wrap" data-month="(\d{4}-\d\d)"([^>]*)>', html)
    assert [(k, " hidden" in rest) for k, rest in grids] == [("2026-10", True), ("2026-11", False)]  # October is out of sight
    assert html.count('class="cal-grid"') == 2
    panels = _panels(html)
    assert [d for d, hidden in panels.items() if not hidden] == ["2026-11-02"]
    today = html.split('id="tl-2026-11-02"', 1)[1].split("</section>", 1)[0]
    assert "Nothing finished yet today." in today and "<ol" not in today.split('class="tl-empty"', 1)[1]
    assert 'data-go="2026-10-30"' in today and 'href="#grabs"' in today  # Friday's work is in October's grid
    assert "2026-10-30" in panels and 'aria-controls="tl-2026-10-30"' in html
    text = re.sub(r"<[^>]+>", "", html)
    assert "2 things finished in October: 1 by Ada and 1 by Kofi." in text and "Nothing finished in November yet." in text
    heads = re.findall(r'<p class="done-big" data-month-variant="([\d-]+)"([^>]*)>', html)
    assert [(k, " hidden" in rest) for k, rest in heads] == [("2026-10", True), ("2026-11", False)]
    # each headline counts up to its own month's number, so the one shown after a switch ends on the right value
    assert re.search(r'data-month-variant="2026-10"[^>]*>\s*<span data-count="2">2</span>', html)
    arrows = re.findall(r'<button type="button" class="cal-arrow"([^>]*)>', html)
    assert len(arrows) == 4 and sum(" disabled" in a for a in arrows) == 2  # no month before October, none after November
    assert "Previous month: the board starts on 21 October" in html


def test_the_calendar_page_and_its_script_agree_and_the_old_list_is_gone():
    import pathlib
    root = pathlib.Path(main.__file__).parent
    css, js = (root / "static" / "board.css").read_text(), (root / "static" / "board.js").read_text()
    macros = (root / "templates" / "_macros.html").read_text()
    for old in (".show-all", ".days {", ".day[hidden]", ".day-title"):
        assert old not in css, old
    assert "macro day(" not in macros and all(f"macro {m}(" in macros for m in ("moon", "sun", "variants"))
    for old in ("show-all", "data-earlier", "data-days"):
        assert old not in js, old
    assert "initCalendar" in js.split("function start()", 1)[1]  # started with the other enhancements
    for name in ("data-n-", "data-level-", "data-late-", "data-label-", "data-month", "data-go", "data-who-variant"):
        assert name in js, name  # the attributes the page writes are the ones the script reads
    assert '" earlier"' in js and '" later"' not in js  # a day reads newest first, so a pause below a row came earlier
    # the top row, not the last, rounds the night strip: the evening is on top and the 6 pm marker closes it below
    assert '"is-top"' in js and '"is-end"' not in js and ".tl-row.zone-night.is-top" in css and ".is-end" not in css


def test_a_day_reads_newest_first_with_pomos_line_on_top(client, monkeypatch):
    import re
    moment = _freeze(monkeypatch, "2026-10-29T11:00:00+00:00")
    raw = _closed_raw((10, "2026-10-27T09:30:00Z", "ada-demo"), (11, "2026-10-27T20:30:00Z", "ada-demo"),
                      (12, "2026-10-27T19:00:00Z", "ada-demo"), (13, "2026-10-21T09:00:00Z", "kofi-demo"))
    html = client(lambda: raw, clock=lambda: moment.timestamp()).get("/").get_data(as_text=True)
    day = html.split('id="tl-2026-10-27"', 1)[1].split("</section>", 1)[0]
    parts = re.findall(r'<li class="(tl-foot|tl-divider|tl-row)[^"]*"(?: data-at="([^"]+)")?', day)
    # Pomo's line for everyone and for Ada first, then the evening newest first, the 6 pm marker, then the day
    assert [kind for kind, _ in parts] == ["tl-foot", "tl-foot", "tl-row", "tl-row", "tl-divider", "tl-row"]
    assert [at[11:16] for kind, at in parts if kind == "tl-row"] == ["20:30", "19:00", "09:30"]
    text = re.sub(r"<[^>]+>", "", day)
    assert "2 things finished after hours. Dinner was hours ago. Pomo would like a word." in text
    assert "tomato" not in html.lower()  # the mascot is Pomo wherever the page talks about it, attributes included


def test_the_weekend_stamp_follows_the_person_filter(client, monkeypatch):
    """The stamp is a variant like the stat: a weekend only Ada worked must not stay stamped under Kofi's chip."""
    import pathlib
    import re
    moment = _freeze(monkeypatch, "2026-10-29T11:00:00+00:00")
    raw = _closed_raw((10, "2026-10-24T10:00:00Z", "ada-demo"), (11, "2026-10-21T09:00:00Z", "kofi-demo"))  # 24 Oct: Saturday
    html = client(lambda: raw, clock=lambda: moment.timestamp()).get("/").get_data(as_text=True)
    title = re.search(r'<h3 class="tl-title" id="tl-title-2026-10-24"[^>]*>(.*?)</h3>', html, re.S).group(1)
    assert re.findall(r'<span class="tl-stamp" data-who-variant="([^"]*)"', title) == ["", "ada-demo"]  # none for Kofi
    assert 'class="tl-stamp" data-who-variant="ada-demo" hidden' in title and 'data-who-variant="" hidden' not in title
    assert ".tl-stamp[hidden]" in (pathlib.Path(main.__file__).parent / "static" / "board.css").read_text()


def test_the_page_takes_its_clock_team_and_labels_from_the_settings(client, monkeypatch):
    """Change the settings the page reads and check every place that used to say UK, 6 pm or a name follows."""
    import re
    import board
    moment = _freeze(monkeypatch, "2026-10-29T11:00:00+00:00")
    monkeypatch.setattr(board, "WORK_END", 17)
    monkeypatch.setattr(board, "ZONE_LABEL", "office")
    raw = _closed_raw((10, "2026-10-27T09:30:00Z", "ada-demo"), (11, "2026-10-27T17:30:00Z", "ada-demo"),
                      (13, "2026-10-21T09:00:00Z", "kofi-demo"))
    raw["issues"].append(_node(5, labels=["priority"]))
    html = client(lambda: raw, clock=lambda: moment.timestamp()).get("/").get_data(as_text=True)
    text = re.sub(r"<[^>]+>", "", html)
    assert "Home time was 5 pm. Pomo is tapping its watch." in text
    assert "5 pm, office time" in text and "times are office time" in text
    assert "before 9 am or after 5 pm, on the finisher's own clock" in text
    assert "The shared board for Ada, Kofi and Mei on" in text and "Ada, Kofi and Mei's shared progress board" in html
    assert 'href="#priority"' in html and '<span class="tag tag--priority">Top priority</span>' in html  # in Up for grabs
    assert "Every open issue labelled priority, assigned or not." in text
    assert "<title>Pomodo · team board</title>" in html and "<span>Pomodo</span>" in html
    for gone in ("UK time", "6 pm", "neither"):
        assert gone not in html, gone


def test_the_milestone_stat_shows_only_when_a_label_is_set(client, monkeypatch):
    import re
    import board
    raw = {"issues": [_node(1, labels=["v1"]), _node(2, labels=["v1"], state="CLOSED", closed="2026-10-27T09:30:00Z"),
                      _node(3)], "pullRequests": []}
    html = client(lambda: raw).get("/").get_data(as_text=True)
    card = html.split('b-milestone', 1)[1].split("</div>\n", 2)
    assert "1</span> of 2 issues labelled v1 are closed" in re.sub(r"\s+", " ", "".join(card))
    assert "label%3Av1" in html and ">v1<" not in html  # a stat, not a chip on each issue
    monkeypatch.setattr(board, "MILESTONE_LABEL", "")
    html = client(lambda: raw).get("/").get_data(as_text=True)
    assert "b-milestone" not in html and "issues labelled" not in html
    assert main.current_board()["stats"]["milestone"] is None
