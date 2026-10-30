"""The Pomodo board: one page and one JSON endpoint, both read from GitHub through a short cache."""
import logging
import os
import threading
from datetime import datetime, timezone

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from board import BOARD_TZ, NOT_DONE, PEOPLE, VERIFIED_LABEL, build_board
from github import Cache, GitHubError, add_comment, fetch_repo, load_fixture, set_assignee, set_label
from config import CONFIG
import auth
import demo
from auth import local_copy

REPO = CONFIG.repo  # owner/name, from pomodo.toml
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))
NOTES_MAX = 1000  # characters of notes on a "Checked in the app" tick; board.js caps its box at the same length

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/",
    "style-src 'self' https://api.fontshare.com",
    "style-src-attr 'unsafe-inline'",  # style="--lc:#..." on labels and bars; no inline <style> blocks
    "font-src 'self' https://cdn.fontshare.com",
    "img-src 'self' data: https://avatars.githubusercontent.com",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",  # the sign-out button
    "frame-ancestors 'none'",
])


_DEMO = {"raw": None}  # the made-up read, built once


def _demo_read():
    """The DEMO=1 data, built when first asked for and then the same object every time. The cache applies "It's mine!"
    and "Checked in the app" to the read in place and drops them at its next refresh, expecting the source to have
    them by then; a new read each time would lose them every few minutes."""
    if _DEMO["raw"] is None:
        _DEMO["raw"] = demo.make_raw(datetime.now(timezone.utc), CONFIG)
    return _DEMO["raw"]


def _load():
    fixture = os.environ.get("DEV_FIXTURE")
    if fixture:
        return load_fixture(fixture)
    if local_copy():  # DEMO=1: made-up data only, never the real repo
        return _demo_read()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubError("GITHUB_TOKEN is not set")
    return fetch_repo(token, REPO)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pomodo")
cache = Cache(_load, CACHE_SECONDS)
app = Flask(__name__)
app.jinja_env.globals["settings"] = CONFIG  # the labels' names and titles, for the templates and their macros
auth.configure(app)  # the session cookie and the sign-in routes; stops here if SECRET_KEY is missing

# The only routes open without signing in. Everything else, including any route added later, needs a team member.
OPEN = {"auth.login", "auth.login_github", "auth.callback", "auth.logout", "static", "healthz"}


def _warm():
    try:
        cache.get()
    except GitHubError:
        pass  # the first page view shows the error and tries again


# On Cloud Run (which sets K_SERVICE), start the first GitHub read while the instance boots, so the first
# visitor after a scale-to-zero waits less. gunicorn imports this module in the worker, so the thread lives there.
if os.environ.get("K_SERVICE"):
    threading.Thread(target=_warm, name="github-warm", daemon=True).start()


@app.template_filter("since")
def since(seconds):
    """Seconds as 'just now', '4 minutes ago' or '2 hours ago'."""
    s = max(0, int(seconds or 0))
    if s < 60:
        return "just now"
    n, unit = (s // 60, "minute") if s < 3600 else (s // 3600, "hour") if s < 86400 else (s // 86400, "day")
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


@app.template_global()
def avatar_for(board, login):
    """Any avatar URL GitHub gave us for this login, or '' (the page then shows an initial)."""
    sections = [board["todos"], board["open_prs"], board["unclaimed"], board["done_recent"]]
    sections += [p["items"] for p in board["people"]]
    for item in (i for s in sections for i in s):
        for who in [item["author"], *item["assignees"], *(c["author"] for c in item["comments"])]:
            if who["login"] == login and who["avatar"]:
                return who["avatar"]
    return ""


def current_board(viewer=None, order="updated"):
    raw, fetched_at, error = cache.get()
    now = datetime.now(timezone.utc)
    board = build_board(raw, now, order=order)
    board.update(repo=REPO, repo_url=f"https://github.com/{REPO}", error=error,
                 viewer=None,
                 refresh_minutes=max(1, CACHE_SECONDS // 60),
                 refreshing=error is None and now.timestamp() - fetched_at >= CACHE_SECONDS,  # this visit's read is under way
                 fetched_at=datetime.fromtimestamp(fetched_at, timezone.utc).isoformat(),
                 fetched_seconds_ago=int(now.timestamp() - fetched_at))
    if viewer:  # the photo on the "All mine" state of the It's mine! button
        board["viewer"] = {"login": viewer, "name": PEOPLE.get(viewer, viewer), "avatar": avatar_for(board, viewer)}
    return board


@app.before_request
def gate():
    """Let a signed-in team member through, with their login in g.login. Pages send anyone else to sign in or to the
    "not on this team" page; /api/* answers 401 or 403 JSON."""
    if request.endpoint in OPEN:
        return None
    api = request.path.startswith("/api/")
    login = auth.current_login()
    if not login:
        if api:
            return jsonify(error="Sign in to use the board."), 401
        return redirect(url_for("auth.login"))
    g.login = auth.team_login(login)
    if not g.login:
        if api:
            return jsonify(error=f"{login} is not on this board's team."), 403
        return render_template("signin.html", kind="not_team", login=login), 403
    return None


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store" if resp.mimetype != "image/svg+xml" else "private, max-age=86400"
    return resp


@app.get("/")
def index():
    try:
        board = current_board(g.login, order=request.args.get("order", "updated"))
    except GitHubError as e:
        return render_template("error.html", message=str(e), repo=REPO), 503
    return render_template("board.html", board=board)


@app.get("/api/board.json")
def board_json():
    try:
        return jsonify(current_board(order=request.args.get("order", "updated")))
    except GitHubError as e:
        return jsonify(error=str(e)), 503


def _mark(number, login, assigned):
    """Fold an assignment the board just made into the cached read. Adding or removing one login is safe to repeat."""
    def edit(raw):
        node = next((n for n in raw["issues"] if n["number"] == number), None)
        if node is None:
            return
        same = lambda w: (w.get("login") or "").lower() == login.lower()  # noqa: E731  GitHub ignores case
        people = [a for a in node["assignees"]["nodes"] if not same(a)]
        if assigned:
            avatar = next((w.get("avatarUrl") for n in raw["issues"] + raw["pullRequests"]
                           for w in [n.get("author") or {}] + n["assignees"]["nodes"]
                           if same(w) and w.get("avatarUrl")), "")
            people.append({"login": login, "avatarUrl": avatar})
        node["assignees"]["nodes"] = people
    cache.edit(edit)


@app.post("/api/issues/<int:number>/assignee")
def take_issue(number):
    """Assign (or unassign) the signed-in person on an open issue. Called by the tick on the board."""
    # A cross-site page cannot send JSON here without a CORS preflight this app never answers, and modern browsers
    # say where a request came from. Both have to look like the board's own page.
    if not request.is_json or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
        return jsonify(error="This only works from the board itself."), 403
    login = g.get("login")  # the gate let only a team member through
    if not login:
        return jsonify(error="The board could not tell who you are."), 403
    body = request.get_json(silent=True)
    assigned = body.get("assigned") if isinstance(body, dict) else None
    if not isinstance(assigned, bool):
        return jsonify(error="Send {\"assigned\": true} or false."), 400
    try:
        raw, _, _ = cache.get()
    except GitHubError as e:
        return jsonify(error=str(e)), 503
    node = next((n for n in raw["issues"] if n["number"] == number), None)
    if node is None or node["state"] != "OPEN":
        return jsonify(error=f"#{number} is not an open issue on the board."), 404
    try:
        if local_copy():  # never write to GitHub
            logins = [a["login"] for a in node["assignees"]["nodes"]
                      if a["login"].lower() != login.lower()] + ([login] if assigned else [])
        else:
            logins = set_assignee(os.environ.get("GITHUB_TOKEN", ""), REPO, number, login, assigned)
    except GitHubError as e:
        log.warning("Could not %s %s on #%d: %s", "assign" if assigned else "unassign", login, number, e)
        return jsonify(error=str(e)), 502
    _mark(number, login, assigned)
    log.info("%s %s on #%d", "Assigned" if assigned else "Unassigned", login, number)
    return jsonify(number=number, assigned=assigned, assignees=logins)


def checked_comment(name, checked, notes, now):
    """What a tick posts on the issue. GitHub shows the token's owner as the author, so the text names the person."""
    if not checked:
        return f"Unticked by {name} on {CONFIG.title}."
    day = now.astimezone(BOARD_TZ)
    text = f"Checked in the running app by {name} on {day.day} {day:%b %Y} (ticked on {CONFIG.title})."
    if notes:
        text += "\n\n" + "\n".join(f"> {line}".rstrip() for line in notes.splitlines())
    return text


def _relabel(number, checked):
    """Fold a human-verified change the board just made into the cached read. Adding or removing it is safe to repeat."""
    def edit(raw):
        node = next((n for n in raw["issues"] + raw["pullRequests"] if n["number"] == number), None)
        if node is None:
            return
        labels = [l for l in node["labels"]["nodes"] if l.get("name") != VERIFIED_LABEL]
        if checked:
            labels.append({"name": VERIFIED_LABEL, "color": "2da44e"})  # the label's colour on GitHub
        node["labels"]["nodes"] = labels
    cache.edit(edit)


@app.post("/api/issues/<int:number>/checked")
def check_issue(number):
    """Mark (or unmark) a finished issue or a merged pull request as checked in the running app: the human-verified
    label and a comment saying who. GitHub numbers issues and pull requests in one sequence, so the number is enough."""
    if not request.is_json or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
        return jsonify(error="This only works from the board itself."), 403
    login = g.get("login")  # the gate let only a team member through
    if not login:
        return jsonify(error="The board could not tell who you are."), 403
    body = request.get_json(silent=True)
    checked = body.get("checked") if isinstance(body, dict) else None
    notes = body.get("notes", "") if isinstance(body, dict) else ""
    if not isinstance(checked, bool) or not isinstance(notes, str):
        return jsonify(error="Send {\"checked\": true, \"notes\": \"...\"} or {\"checked\": false}."), 400
    notes = notes.strip()
    if len(notes) > NOTES_MAX:
        return jsonify(error=f"Notes can be at most {NOTES_MAX} characters."), 400
    try:
        raw, _, _ = cache.get()
    except GitHubError as e:
        return jsonify(error=str(e)), 503
    issue = next((n for n in raw["issues"] if n["number"] == number), None)
    node = issue or next((n for n in raw["pullRequests"] if n["number"] == number), None)
    if issue is not None:
        closed = issue["state"] == "CLOSED"
        finished = closed and (issue.get("stateReason") or "").lower() not in NOT_DONE
    else:
        closed = finished = node is not None and node["state"] == "MERGED"
    has = closed and VERIFIED_LABEL in [l.get("name") for l in node["labels"]["nodes"]]
    if not (finished or has):  # a label put on a not-planned issue by mistake can still be taken off
        return jsonify(error=f"#{number} is not a finished issue or a merged pull request on the board."), 404
    if has == checked:  # a double click or a second tab: nothing to change, and no second comment
        return jsonify(number=number, checked=checked, changed=False)
    comment = checked_comment(PEOPLE.get(login, login), checked, notes, datetime.now(timezone.utc))
    if not local_copy():  # a local copy never writes to GitHub
        token = os.environ.get("GITHUB_TOKEN", "")
        try:
            set_label(token, REPO, number, VERIFIED_LABEL, checked)
        except GitHubError as e:
            log.warning("Could not %s %s on #%d: %s", "add" if checked else "remove", VERIFIED_LABEL, number, e)
            # Labels and comments on a pull request need the token's Pull requests permission, not Issues.
            hint = (". The board's GitHub token needs Pull requests: Read and write to tick a pull request."
                    if issue is None and "HTTP 403" in str(e) else "")
            return jsonify(error=f"{e}{hint}"), 502
        _relabel(number, checked)
        try:
            add_comment(token, REPO, number, comment)
        except GitHubError as e:
            log.warning("Labelled #%d but could not comment: %s", number, e)
            return jsonify(error=f"The label is {'on' if checked else 'off'}, but the comment did not post ({e}). "
                                 f"Say who {'checked and what they saw' if checked else 'unticked it'} on GitHub.",
                           checked=checked), 502  # board.js shows the label as it now is
    else:
        _relabel(number, checked)
    log.info("%s #%d as checked in the app, by %s", "Marked" if checked else "Unmarked", number, login)
    return jsonify(number=number, checked=checked, changed=True)


@app.get("/healthz")
def healthz():
    return "ok"
