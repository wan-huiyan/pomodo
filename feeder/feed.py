"""Pomodo's feeder: the only thing that writes to-dos found in Slack, Gmail, Calendar, meeting notes and Jira to GitHub.

A Claude routine reads those sources and writes a plan (JSON, see feeder/SKILL.md). This script checks the plan, then
makes only additive changes with the owner's own `gh` login: open an issue, add a comment, add the maybe-done label.
It never closes, reopens, reassigns, edits or deletes, and it refuses a public repo.

    python3 -I feeder/feed.py --project board/feeder.toml plan.json [--dry-run]

One run is for one project: board/feeder.toml (committed in the project's repo) names the repo and the sources the run
may use, and ~/.pomodo-feeder/repos lists the repos this person trusts.
"""
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SOURCES = {"slack": "Slack", "gmail": "Gmail", "calendar": "Calendar", "meeting": "Meeting notes", "jira": "Jira"}
PREFIX = "from:"
MAYBE_DONE = "maybe-done"
MAX_CREATES, MAX_NOTES = 10, 20
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+")  # used with fullmatch: `$` lets a final newline through
_KEY = re.compile(r"[a-z]+:[A-Za-z0-9._:/@#=-]{1,200}")
_LINK = re.compile(r"https://[^\s<>\"'()\[\]!@*`]{1,500}")  # no markdown punctuation: the link is written as <link>
_MARKER = re.compile(r"<!-- pomodo-source: (\S+) -->")
_SITE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
_JIRA_KEY = re.compile(r"jira:([A-Za-z][A-Za-z0-9_]*)-[0-9]+:.+")
# Each table a project file may have, and the keys it must have (all of them are lists of text except jira.site).
_TABLES = {"slack": ("channels",), "jira": ("site", "projects"), "gmail": ("domains",),
           "calendar": ("title_words",), "meeting": ("title_words",)}
_FIELDS = {"create": {"do", "source", "key", "link", "title", "plain", "assign_owner"},
           "comment": {"do", "source", "key", "link", "number", "text"},
           "maybe_done": {"do", "source", "key", "link", "number", "text"}}


class PlanError(ValueError):
    """The plan cannot be carried out. The message names the action (counted from 1) and the field."""


def _text(action, field, limit, where):
    value = action.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PlanError(f"{where}: {field} must be text of 1 to {limit} characters")
    return value


@dataclass(frozen=True)
class Project:
    """What one project's run may do: its repo, and the tables (settings) of the sources it may use."""
    repo: str
    tables: dict
    name: str = "feeder.toml"


def parse_project(data, name):
    """A Project from board/feeder.toml's contents (a dict, as tomllib reads it). Raises PlanError naming the bad key."""
    try:
        if not isinstance(data, dict):
            raise ValueError("must be a table")
        for key in data:
            if key != "repo" and key not in _TABLES:
                raise ValueError(f"unknown key {key} (the keys here are: repo, {', '.join(_TABLES)})")
        repo = data.get("repo")
        if not isinstance(repo, str) or not _REPO.fullmatch(repo):
            raise ValueError(f"repo = {repo!r} must be owner/name, as in https://github.com/owner/name" if repo is not None
                             else "repo is missing")
        tables = {}
        for table, keys in _TABLES.items():
            if table not in data:
                continue
            body = data[table]
            if not isinstance(body, dict):
                raise ValueError(f"{table} must be a table")
            for key in body:
                if key not in keys:
                    raise ValueError(f"unknown key {table}.{key} (the keys here are: {', '.join(keys)})")
            for key in keys:
                value, where = body.get(key), f"{table}.{key}"
                if key == "site":
                    if not isinstance(value, str) or not _SITE.fullmatch(value):
                        raise ValueError(f"{where} must be a bare host such as \"acme.atlassian.net\", with no https:// or path")
                elif (not isinstance(value, list) or not value
                      or not all(isinstance(v, str) and v.strip() for v in value)):
                    raise ValueError(f"{where} must be a list of non-empty strings, such as [\"BU\"]")
            tables[table] = {k: ([v.strip() for v in body[k]] if k != "site" else body[k]) for k in keys}
    except ValueError as e:
        raise PlanError(f"{name}: {e}") from None
    return Project(repo, tables, name)


def load_project(path):
    """The Project in the file at path. Raises PlanError if it is not valid TOML or not a valid project file."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise PlanError(f"{path.name} is not valid TOML: {e}") from None
    return parse_project(data, path.name)


def load_allowed(path):
    """The repos the person trusts: one owner/name per line of path; blank lines and # comments ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [line.split("#", 1)[0].strip() for line in text.splitlines() if line.split("#", 1)[0].strip()]


def check_scope(plan, project):
    """Refuse the whole plan if it names another repo or uses a source, channel, Jira project or host the project
    file does not list. Gmail, calendar and meeting actions are accepted when their table exists: the sender or title
    is not in the plan, so the routine applies domains and title_words."""
    if plan["repo"].lower() != project.repo.lower():
        raise PlanError(f"the plan is for {plan['repo']} but {project.name} is for {project.repo}; nothing was done")
    for n, a in enumerate(plan["actions"], 1):
        where, source = f"action {n}", a["source"]
        table = project.tables.get(source)
        if table is None:
            raise PlanError(f"{where}: {project.name} has no [{source}] table, so {SOURCES[source]} is off for this project")
        if source == "slack":
            channel = a["key"][len("slack:"):].split("/", 1)
            if len(channel) != 2 or not channel[1] or channel[0] not in table["channels"]:
                raise PlanError(f"{where}: key must be slack:<channel>/<ts> with a channel listed in {project.name} "
                                f"slack.channels ({', '.join(table['channels'])})")
        elif source == "jira":
            found = _JIRA_KEY.fullmatch(a["key"])
            if not found or found.group(1) not in table["projects"]:
                raise PlanError(f"{where}: key must be jira:<KEY>-<n>:<status> with KEY listed in {project.name} "
                                f"jira.projects ({', '.join(table['projects'])})")
            if urlsplit(a["link"]).netloc.lower() != table["site"].lower():
                raise PlanError(f"{where}: link host must be {table['site']} (jira.site in {project.name})")


def check_plan(plan):
    """The plan, if every action is one the feeder may take. Raises PlanError otherwise; nothing is half-done."""
    if not isinstance(plan, dict) or not _REPO.fullmatch(str(plan.get("repo", ""))):
        raise PlanError("repo must be owner/name")
    if not isinstance(plan.get("owner"), str) or not plan["owner"].strip() or len(plan["owner"]) > 60:
        raise PlanError("owner must be the person's name, 1 to 60 characters")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise PlanError("actions must be a list")
    seen = set()
    for n, a in enumerate(actions, 1):
        where = f"action {n}"
        if not isinstance(a, dict) or not isinstance(a.get("do"), str) or a["do"] not in _FIELDS:
            raise PlanError(f"{where}: do must be one of {', '.join(_FIELDS)}")
        extra = set(a) - _FIELDS[a["do"]]
        if extra:
            raise PlanError(f"{where}: {', '.join(sorted(extra))} is not allowed for {a['do']}")
        if not isinstance(a.get("source"), str) or a["source"] not in SOURCES:
            raise PlanError(f"{where}: source must be one of {', '.join(SOURCES)}")
        if not isinstance(a.get("key"), str) or not _KEY.fullmatch(a["key"]) or not a["key"].startswith(a["source"] + ":"):
            raise PlanError(f"{where}: key must look like {a['source']}:<id>, with no spaces")
        if a["key"] in seen:
            raise PlanError(f"{where}: key {a['key']} appears twice")
        seen.add(a["key"])
        if not isinstance(a.get("link"), str) or not _LINK.fullmatch(a["link"]):
            raise PlanError(f"{where}: link must be one https:// address")
        if a["do"] == "create":
            _text(a, "title", 80, where)
            _text(a, "plain", 600, where)
            if not isinstance(a.get("assign_owner", False), bool):
                raise PlanError(f"{where}: assign_owner must be true or false")
        else:
            if type(a.get("number")) is not int or a["number"] <= 0:
                raise PlanError(f"{where}: number must be the issue's number")
            _text(a, "text", 600, where)
    creates = sum(1 for a in actions if a["do"] == "create")
    if creates > MAX_CREATES:
        raise PlanError(f"the plan opens {creates} issues; a run opens at most {MAX_CREATES} new issues")
    if len(actions) - creates > MAX_NOTES:
        raise PlanError(f"the plan has {len(actions) - creates} comments and marks; a run makes at most {MAX_NOTES}")
    return plan


def quiet(text):
    """Text as it goes into GitHub: an @name never pings anyone, no HTML or comment (so no forged marker) gets through,
    and a markdown image cannot make GitHub fetch an address that carries the text."""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("![", "!\\[").replace("@", "@⁠").strip()


def marker(key):
    return f"<!-- pomodo-source: {key} -->"


def keys_in(text):
    return set(_MARKER.findall(text or ""))


def issue_body(action, owner):
    return (f"## In plain words\n\n{quiet(action['plain'])}\n\n"
            f"## Where this came from\n\n{SOURCES[action['source']]}: <{action['link']}>\n\n"
            f"Opened by Pomodo's feeder for {quiet(owner)}. The feeder only adds: it never closes, reassigns or edits. "
            f"A person does that.\n\n{marker(action['key'])}\n")


def note_body(action):
    name = SOURCES[action["source"]]
    if action["do"] == "maybe_done":
        head = (f"**This may be done** ({name}): {quiet(action['text'])}\n<{action['link']}>\n\n"
                f"If it is, close this issue. If not, remove the `{MAYBE_DONE}` label.")
    else:
        head = f"**New from {name}:** {quiet(action['text'])}\n<{action['link']}>"
    return f"{head}\n\n{marker(action['key'])}\n"


class GhError(RuntimeError):
    """A gh command failed. The message is gh's own, without the token."""


def gh_cli(args, stdin=None):
    """Run one gh command with the owner's own login."""
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise GhError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def _fed_keys(plan, gh, ledger):
    repo, keys = plan["repo"], set()
    for source in sorted({a["source"] for a in plan["actions"]}):
        listed = json.loads(gh(["issue", "list", "-R", repo, "--state", "all", "--label", PREFIX + source,
                                "--limit", "1000", "--json", "number,body"]) or "[]")
        for issue in listed:
            keys |= keys_in(issue.get("body"))
    for number in sorted({a["number"] for a in plan["actions"] if a["do"] != "create"}):
        seen = json.loads(gh(["issue", "view", str(number), "-R", repo, "--json", "comments"]) or "{}")
        for comment in seen.get("comments", []):
            keys |= keys_in(comment.get("body"))
    if ledger.exists():
        for n, line in enumerate(ledger.read_text().splitlines(), 1):
            try:
                entry = json.loads(line)
                if entry.get("repo") == repo:
                    keys.add(entry["key"])
            except (ValueError, AttributeError, KeyError):
                raise PlanError(f"the ledger {ledger} is damaged at line {n}; fix or remove that line") from None
    return keys


def _label(gh, repo, name, description):
    gh(["label", "create", name, "-R", repo, "--color", "c5def5", "--description", description, "--force"])


def _remember(ledger, repo, a, number):
    with ledger.open("a") as f:
        f.write(json.dumps({"repo": repo, "key": a["key"], "do": a["do"], "number": number,
                            "at": datetime.now(timezone.utc).isoformat()}) + "\n")


def run(plan, gh, ledger, project, dry_run=False, on_line=None):
    """Carry out a checked plan, skipping anything already fed. Returns one line per action; on_line, when given,
    also gets each line the moment it is known, so a failure later on still shows what was done."""
    check_plan(plan)
    check_scope(plan, project)
    repo = plan["repo"]
    visibility = gh(["repo", "view", repo, "--json", "visibility", "-q", ".visibility"]).strip()
    if visibility not in ("PRIVATE", "INTERNAL"):
        raise PlanError(f"{repo} is public ({visibility or 'unknown'}): fed issues would publish private mail and chat")
    fed, lines = _fed_keys(plan, gh, ledger), []

    def say(line):
        lines.append(line)
        if on_line:
            on_line(line)

    for a in plan["actions"]:
        if a["key"] in fed:
            say(f"Skipped {a['key']}: already fed")
            continue
        what = (f"open \"{a['title']}\"" if a["do"] == "create" else
                f"comment on #{a['number']}" if a["do"] == "comment" else f"mark #{a['number']} maybe done")
        if dry_run:
            say(f"Would {what} (from {SOURCES[a['source']]})")
            continue
        number = a.get("number")
        if a["do"] == "create":
            _label(gh, repo, PREFIX + a["source"], "Found by Pomodo's feeder")
            args = ["issue", "create", "-R", repo, f"--title={quiet(a['title'])}", "--body-file", "-",
                    "--label", PREFIX + a["source"]]
            if a.get("assign_owner"):
                args += ["--assignee", "@me"]
            answer = gh(args, stdin=issue_body(a, plan["owner"])).strip()
            tail = answer.rsplit("/", 1)[-1]
            number = int(tail) if tail.isascii() and tail.isdigit() else None
            if number is None:  # the issue exists; remember the key so a rerun does not open it again
                _remember(ledger, repo, a, None)
                raise GhError(f"gh opened an issue for {a['key']} but did not say its number: {answer[:100]!r}")
            what = f"opened #{number} \"{a['title']}\""
        else:
            if a["do"] == "maybe_done":
                _label(gh, repo, MAYBE_DONE, "The feeder thinks this is done; a person closes it")
                gh(["issue", "edit", str(number), "-R", repo, "--add-label", MAYBE_DONE])
            gh(["issue", "comment", str(number), "-R", repo, "--body-file", "-"], stdin=note_body(a))
            what = f"{'commented on' if a['do'] == 'comment' else 'marked maybe done'} #{number}"
        _remember(ledger, repo, a, number)
        fed.add(a["key"])
        say(f"Done: {what} (from {SOURCES[a['source']]})")
    return lines


def main(argv):
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 3 or args[0] != "--project":
        print("usage: python3 -I feeder/feed.py --project board/feeder.toml plan.json [--dry-run]", file=sys.stderr)
        return 2
    state = Path.home() / ".pomodo-feeder"
    try:
        project = load_project(args[1])
        plan = check_plan(json.loads(Path(args[2]).read_text()))
        if project.repo.lower() not in (r.lower() for r in load_allowed(state / "repos")):
            raise PlanError(f"{project.repo} is not in your list of trusted repos: add it to ~/.pomodo-feeder/repos "
                            f"(one owner/name per line)")
        state.mkdir(exist_ok=True)
        run(plan, gh_cli, state / "ledger.jsonl", project, dry_run=dry,
            on_line=lambda line: print(line, flush=True))  # flushed: piped with 2>&1, Done must land before Stopped
    except (PlanError, GhError) as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # bad JSON, or a file that is not text
        print(f"Stopped: the plan or a gh answer could not be read as JSON ({str(e)[:100]})", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Stopped: gh took longer than 60 seconds; nothing after this point was done", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Stopped: {'gh is not installed or not on PATH' if e.filename in (None, 'gh') else f'no such file: {e.filename}'}",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Stopped: {e.strerror or e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
