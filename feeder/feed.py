"""Pomodo's feeder: the only thing that writes to-dos found in Slack, Gmail, Calendar, meeting notes and Jira to GitHub.

A Claude routine reads those sources and writes a plan (JSON, see feeder/SKILL.md). This script checks the plan, then
makes only additive changes with the owner's own `gh` login: open an issue, add a comment, add the maybe-done label.
It never closes, reopens, reassigns, edits or deletes, and it refuses a public repo.

    python3 -I feeder/feed.py plan.json [--dry-run]
"""
import re

SOURCES = {"slack": "Slack", "gmail": "Gmail", "calendar": "Calendar", "meeting": "Meeting notes", "jira": "Jira"}
PREFIX = "from:"
MAYBE_DONE = "maybe-done"
MAX_CREATES, MAX_NOTES = 10, 20
_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")
_KEY = re.compile(r"^[a-z]+:[A-Za-z0-9._:/@#=-]{1,200}$")
_LINK = re.compile(r"^https://[^\s<>\"']{1,500}$")
_MARKER = re.compile(r"<!-- pomodo-source: (\S+) -->")
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


def check_plan(plan):
    """The plan, if every action is one the feeder may take. Raises PlanError otherwise; nothing is half-done."""
    if not isinstance(plan, dict) or not _REPO.match(str(plan.get("repo", ""))):
        raise PlanError("repo must be owner/name")
    if not isinstance(plan.get("owner"), str) or not plan["owner"].strip() or len(plan["owner"]) > 60:
        raise PlanError("owner must be the person's name, 1 to 60 characters")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise PlanError("actions must be a list")
    seen = set()
    for n, a in enumerate(actions, 1):
        where = f"action {n}"
        if not isinstance(a, dict) or a.get("do") not in _FIELDS:
            raise PlanError(f"{where}: do must be one of {', '.join(_FIELDS)}")
        extra = set(a) - _FIELDS[a["do"]]
        if extra:
            raise PlanError(f"{where}: {', '.join(sorted(extra))} is not allowed for {a['do']}")
        if a.get("source") not in SOURCES:
            raise PlanError(f"{where}: source must be one of {', '.join(SOURCES)}")
        if not isinstance(a.get("key"), str) or not _KEY.match(a["key"]) or not a["key"].startswith(a["source"] + ":"):
            raise PlanError(f"{where}: key must look like {a['source']}:<id>, with no spaces")
        if a["key"] in seen:
            raise PlanError(f"{where}: key {a['key']} appears twice")
        seen.add(a["key"])
        if not isinstance(a.get("link"), str) or not _LINK.match(a["link"]):
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
    """Text as it goes into GitHub: an @name never pings anyone, and nothing can pose as a feeder marker."""
    return text.replace("<!--", "").replace("-->", "").replace("@", "@⁠").strip()


def marker(key):
    return f"<!-- pomodo-source: {key} -->"


def keys_in(text):
    return set(_MARKER.findall(text or ""))


def issue_body(action, owner):
    return (f"## In plain words\n\n{quiet(action['plain'])}\n\n"
            f"## Where this came from\n\n{SOURCES[action['source']]}: {action['link']}\n\n"
            f"Opened by Pomodo's feeder for {quiet(owner)}. The feeder only adds: it never closes, reassigns or edits. "
            f"A person does that.\n\n{marker(action['key'])}\n")


def note_body(action):
    name = SOURCES[action["source"]]
    if action["do"] == "maybe_done":
        head = (f"**This may be done** ({name}): {quiet(action['text'])}\n{action['link']}\n\n"
                f"If it is, close this issue. If not, remove the `{MAYBE_DONE}` label.")
    else:
        head = f"**New from {name}:** {quiet(action['text'])}\n{action['link']}"
    return f"{head}\n\n{marker(action['key'])}\n"
