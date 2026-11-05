"""The board's settings: one team, one repo, its clock and its labels, read from pomodo.toml."""
import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "pomodo.toml"
EXAMPLE_PATH = HERE / "pomodo.example.toml"

_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")  # owner/name, as GitHub allows
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
# The keys each table may hold, with the default for the ones that can be left out (None: required).
_TOP = {"title": "Pomodo", "repo": None}
_TIME = {"zone": "UTC", "zone_label": None, "work_start": 9, "work_end": 18}  # zone_label defaults to the zone
_LABELS = {"chore": "todo", "tracker": "tracker", "verified": "human-verified", "robot": "robot-vouched",
           "priority": "priority", "priority_title": "Top priority", "nice": "nice-to-have", "milestone": "",
           "source_prefix": "from:"}
# Recently done shows main work unless "Everything" is picked: these labels and title starts are left out, as are the
# chore and tracker labels.
_DONE = {"hide_labels": ["documentation"], "hide_titles": []}
# The read is everything by default. recent_only reads open work and what finished since last month began: far less on a big repo.
_READ = {"recent_only": False}
# Up for grabs lists every open unassigned issue by default; with labels, only those carrying at least one of them.
_GRABS = {"labels": []}
# Where an agent's annotated screenshots of robot-vouched work live: a private GCS bucket, or a local folder for
# development. Neither set: no screenshots.
_SHOTS = {"bucket": "", "folder": ""}
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")  # a GCS bucket name, as Google allows
_AUTH = {"mode": "github"}  # "iap": behind Google's Identity-Aware Proxy, people named by email instead of GitHub sign-in
_AUTH_MODES = ("github", "iap")
_PERSON = {"login": None, "name": None, "zone": None, "id": None, "email": None}  # name defaults to the login, zone to the board's
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(ValueError):
    """pomodo.toml is missing something or has a value the board can't use. The message names the key."""


@dataclass(frozen=True)
class Person:
    login: str
    name: str
    zone: str  # an IANA zone name: their own clock, for "after hours"
    id: int | None = None  # GitHub's account number: a login can pass to someone else, the number never does
    email: str | None = None  # lower-cased; how IAP names them (required when auth_mode is "iap")


@dataclass(frozen=True)
class Labels:
    chore: str           # small admin jobs: the Chores card
    tracker: str         # a checklist of other issues: a progress card
    verified: str        # "A human peeked": a person saw it work in the running app
    robot: str           # "Robot vouched": an agent saw it work in a browser
    priority: str        # the top card and the first group in Up for grabs
    priority_title: str  # what the page calls that group
    nice: str
    milestone: str       # "" when the board shows no milestone stat
    source_prefix: str   # labels starting with this say where a fed issue came from: "from:slack" -> a "From Slack" badge


@dataclass(frozen=True)
class Config:
    title: str
    repo: str            # owner/name
    zone: str            # the board's day: Today, Yesterday, the calendar
    zone_label: str      # how the page names that clock ("UK" in "UK time")
    work_start: int      # after hours is before this hour ...
    work_end: int        # ... and from this one, on each person's own clock
    labels: Labels
    team: tuple          # of Person, in card order
    hide_labels: tuple = ()  # Recently done leaves these out until "Everything" is picked ...
    hide_titles: tuple = ()  # ... and titles starting with these (ignoring case)
    auth_mode: str = "github"  # or "iap": who is signed in comes from Google's Identity-Aware Proxy
    recent_only: bool = False  # read open work plus what finished since last month began, not the whole history
    grab_labels: tuple = ()  # Up for grabs keeps only issues with one of these labels; empty keeps all
    shots_bucket: str = ""  # a GCS bucket holding <number>/<name>.png, read as the board's own service account
    shots_folder: str = ""  # or a local folder laid out the same way, for development


def _table(data, allowed, where):
    """A TOML table as a dict, refusing keys the board doesn't know (a typo would otherwise be ignored)."""
    if not isinstance(data, dict):
        raise ConfigError(f"{where or 'the file'} must be a table")
    for key in data:
        if key not in allowed:
            known = ", ".join(allowed)
            raise ConfigError(f"unknown key {where + '.' if where else ''}{key} (the keys here are: {known})")
    return data


def _text(data, key, default, where, blank=False):
    value = data.get(key, default)
    if value is None:
        raise ConfigError(f"{where} is missing")
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ConfigError(f"{where} must be {'a string' if blank else 'a non-empty string'}")
    return value.strip()


def _texts(data, key, default, where):
    value = data.get(key, default)
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"{where} must be a list of non-empty strings, such as [\"documentation\"]")
    return tuple(v.strip() for v in value)


def _zone(value, where):
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a time zone name such as \"Europe/London\"")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"{where} = \"{value}\" is not a time zone this computer knows") from None
    return value


def _hour(data, key, default, where):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 24:
        raise ConfigError(f"{where} must be a whole hour from 0 to 24")
    return value


def parse(data):
    """A Config from pomodo.toml's contents (a dict, as tomllib reads it). Raises ConfigError naming the bad key."""
    _table(data, [*_TOP, "time", "labels", "done", "read", "grabs", "shots", "auth", "team"], "")
    title = _text(data, "title", _TOP["title"], "title")
    repo = _text(data, "repo", None, "repo")
    if not _REPO.match(repo) or repo.split("/")[1] in (".", ".."):
        raise ConfigError(f"repo = \"{repo}\" must be owner/name, as in https://github.com/owner/name")

    time = _table(data.get("time", {}), _TIME, "time")
    zone = _zone(time.get("zone", _TIME["zone"]), "time.zone")
    zone_label = _text(time, "zone_label", zone, "time.zone_label")
    start = _hour(time, "work_start", _TIME["work_start"], "time.work_start")
    end = _hour(time, "work_end", _TIME["work_end"], "time.work_end")
    if start >= end:
        raise ConfigError(f"time.work_start ({start}) must be before time.work_end ({end})")

    raw = _table(data.get("labels", {}), _LABELS, "labels")
    labels = Labels(**{k: _text(raw, k, d, f"labels.{k}", blank=k == "milestone") for k, d in _LABELS.items()})
    done = _table(data.get("done", {}), _DONE, "done")
    hide_labels = _texts(done, "hide_labels", _DONE["hide_labels"], "done.hide_labels")
    hide_titles = _texts(done, "hide_titles", _DONE["hide_titles"], "done.hide_titles")

    read = _table(data.get("read", {}), _READ, "read")
    recent_only = read.get("recent_only", _READ["recent_only"])
    if not isinstance(recent_only, bool):
        raise ConfigError("read.recent_only must be true or false")
    grab_labels = _texts(_table(data.get("grabs", {}), _GRABS, "grabs"), "labels", _GRABS["labels"], "grabs.labels")

    shots = _table(data.get("shots", {}), _SHOTS, "shots")
    bucket = _text(shots, "bucket", _SHOTS["bucket"], "shots.bucket", blank=True)
    folder = _text(shots, "folder", _SHOTS["folder"], "shots.folder", blank=True)
    if bucket and not _BUCKET.match(bucket):
        raise ConfigError(f"shots.bucket = \"{bucket}\" is not a bucket name: just the name, as in \"my-board-shots\", not gs://")
    if bucket and folder:
        raise ConfigError("shots.bucket and shots.folder are both set: pick one")

    auth = _table(data.get("auth", {}), _AUTH, "auth")
    mode = auth.get("mode", _AUTH["mode"])
    if mode not in _AUTH_MODES:
        raise ConfigError(f"auth.mode = {mode!r} must be \"github\" or \"iap\"")

    people = data.get("team")
    if not isinstance(people, list) or not people:
        raise ConfigError("team needs at least one person: add a [[team]] table with a login")
    team, seen, emails = [], set(), set()
    for n, p in enumerate(people):
        where = f"team[{n}]"
        _table(p, _PERSON, where)
        login = _text(p, "login", None, f"{where}.login")
        if not _LOGIN.match(login):
            raise ConfigError(f"{where}.login = \"{login}\" is not a GitHub login")
        if login.lower() in seen:  # GitHub logins ignore case
            raise ConfigError(f"{where}.login = \"{login}\" is already on the team")
        seen.add(login.lower())
        gid = p.get("id")
        if gid is not None and (type(gid) is not int or gid <= 0):
            raise ConfigError(f"{where}.id = {gid!r} must be the person's GitHub account number, as in "
                              f"https://api.github.com/users/{login}")
        email = None
        if "email" in p or mode == "iap":
            email = _text(p, "email", None, f"{where}.email").lower()  # Google ignores case in an address
            if not _EMAIL.match(email):
                raise ConfigError(f"{where}.email = \"{email}\" is not an email address")
            if email in emails:
                raise ConfigError(f"{where}.email = \"{email}\" is already on the team")
            emails.add(email)
        team.append(Person(login=login, name=_text(p, "name", login, f"{where}.name"),
                           zone=_zone(p.get("zone", zone), f"{where}.zone"), id=gid, email=email))
    return Config(title=title, repo=repo, zone=zone, zone_label=zone_label, work_start=start, work_end=end,
                  labels=labels, team=tuple(team), hide_labels=hide_labels, hide_titles=hide_titles,
                  auth_mode=mode, recent_only=recent_only, grab_labels=grab_labels,
                  shots_bucket=bucket, shots_folder=folder)


def load(path=None):
    """The settings from path, else POMODO_CONFIG, else pomodo.toml next to the code. A missing file falls back to
    pomodo.example.toml, with a warning, so a fresh copy of the code runs before anyone has written their own."""
    path = Path(path or os.environ.get("POMODO_CONFIG") or DEFAULT_PATH)
    if not path.is_file():
        if (os.environ.get("K_SERVICE") or os.environ.get("POMODO_PRODUCTION")) and os.environ.get("DEMO") != "1":
            # The example's made-up logins could be registered by anyone on GitHub: never let them sign in for real.
            raise ConfigError(f"No settings file at {path}. A deployed board needs its own pomodo.toml with your team.")
        log.warning("No settings file at %s, so the board uses %s. Copy it to pomodo.toml and fill in your team.",
                    path, EXAMPLE_PATH.name)
        path = EXAMPLE_PATH
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} is not valid TOML: {e}") from None
    try:
        return parse(data)
    except ConfigError as e:
        raise ConfigError(f"{path.name}: {e}") from None


CONFIG = load()
