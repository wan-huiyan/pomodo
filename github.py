"""Read every issue and pull request of one GitHub repo, with all their comments.

One GraphQL query per page of 50, so the whole repo costs a few rate-limit points.
"""
import json
import logging
import threading
import time
from urllib.parse import quote

import requests

API = "https://api.github.com/graphql"
log = logging.getLogger(__name__)

# `last:` keeps the newest comments when a thread is longer than the limit (GitHub lists them oldest first).
# The ClosedEvent's `closer` is not read: when a commit closed the issue, GitHub refuses the whole field to a token
# without Contents access, and the board's token has only Issues and Pull requests. board.py links issues to pull
# requests from what each pull request says it closes instead.
_COMMENTS = "comments(last: 100) { totalCount nodes { author { login avatarUrl } bodyHTML createdAt url } }"
_COMMON = """
  number title url state createdAt updatedAt closedAt bodyHTML
  author { login avatarUrl }
  assignees(first: 10) { nodes { login avatarUrl } }
  labels(first: 20) { nodes { name color } }
"""

# The page size is a variable so a page GitHub cannot answer in time can be asked again with fewer items.
ISSUE_PAGE, PULL_PAGE = 50, 25
MIN_PAGE = 5  # below this a 502 or 504 is an error, not a reason to ask for less
_PAGE = "first: $first, after: $cursor"
_OLDEST_FIRST = "orderBy: {field: CREATED_AT, direction: ASC}"
_NEWEST_UPDATE_FIRST = "orderBy: {field: UPDATED_AT, direction: DESC}"

_ISSUES_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $first: Int!%%s) {
  repository(owner: $owner, name: $name) {
    items: issues(%%s) {
      pageInfo { hasNextPage endCursor }
      nodes {
        %s stateReason %s
        timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) { nodes { ... on ClosedEvent { actor { login avatarUrl } } } }
      }
    }
  }
}
""" % (_COMMON, _COMMENTS)

_PULLS_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $first: Int!%%s) {
  repository(owner: $owner, name: $name) {
    items: pullRequests(%%s) {
      pageInfo { hasNextPage endCursor }
      nodes {
        %s isDraft mergedAt reviewDecision additions deletions headRefName baseRefName
        mergedBy { login avatarUrl }
        closingIssuesReferences(first: 25) { nodes { number url } }
        %s
        reviews(last: 50, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) { totalCount nodes {
          author { login avatarUrl } state bodyHTML submittedAt url
          comments(last: 50) { totalCount nodes { author { login avatarUrl } bodyHTML createdAt url path } }
        } }
      }
    }
  }
}
""" % (_COMMON, _COMMENTS)

# Everything: the default read.
ISSUES_QUERY = _ISSUES_QUERY % ("", f"{_PAGE}, {_OLDEST_FIRST}")
PULLS_QUERY = _PULLS_QUERY % ("", f"{_PAGE}, {_OLDEST_FIRST}")
# The recent read (see fetch_recent): open issues and pull requests whatever their age, and the ones finished since a date.
# filterBy since means "updated since", and an issue is updated when it closes, so no closed issue in the window is missed.
OPEN_ISSUES_QUERY = _ISSUES_QUERY % ("", f"states: OPEN, {_PAGE}, {_OLDEST_FIRST}")
OPEN_PULLS_QUERY = _PULLS_QUERY % ("", f"states: OPEN, {_PAGE}, {_OLDEST_FIRST}")
CLOSED_ISSUES_QUERY = _ISSUES_QUERY % (", $since: DateTime", f"states: CLOSED, filterBy: {{since: $since}}, {_PAGE}, {_NEWEST_UPDATE_FIRST}")
CLOSED_PULLS_QUERY = _PULLS_QUERY % ("", f"states: [CLOSED, MERGED], {_PAGE}, {_NEWEST_UPDATE_FIRST}")


class GitHubError(RuntimeError):
    """GitHub could not be read. The message never contains the token. `status` is the HTTP status, when there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _post(session, token, query, variables):
    try:
        r = session.post(API, json={"query": query, "variables": variables},
                         headers={"Authorization": f"bearer {token}"}, timeout=30)
    except requests.RequestException as e:
        raise GitHubError(f"Could not reach GitHub ({type(e).__name__})") from None
    if r.status_code != 200:
        raise GitHubError(f"GitHub returned HTTP {r.status_code}", r.status_code)
    try:
        data = r.json()
    except ValueError:
        raise GitHubError("GitHub sent a reply that is not JSON") from None
    if not isinstance(data, dict):
        raise GitHubError("GitHub sent a reply in an unexpected shape")
    if data.get("errors"):
        raise GitHubError("GitHub: " + "; ".join(str(e.get("message", "")) for e in data["errors"])[:300])
    if not isinstance(data.get("data"), dict):
        raise GitHubError("GitHub sent a reply with no data")
    return data["data"]


def _all_items(session, token, query, owner, name, size, extra=None, until=None):
    """Every node of one query, page by page. A 502 or 504 (GitHub gave up on a heavy page) is tried again with half
    as many items, down to MIN_PAGE, and the pages after it stay that small. With `until` (newest-update-first
    queries), stop once a page holds a node last updated before that time: nothing later can be newer."""
    items, cursor = [], None
    while True:
        try:
            data = _post(session, token, query, {"owner": owner, "name": name, "cursor": cursor, "first": size, **(extra or {})})
        except GitHubError as e:
            if e.status not in (502, 504) or size <= MIN_PAGE:
                raise
            size = max(MIN_PAGE, size // 2)
            log.warning("GitHub gave up on a page (HTTP %s); trying again with %d items", e.status, size)
            continue
        if data.get("repository") is None:
            raise GitHubError(f"The token cannot see {owner}/{name}")
        try:
            page = data["repository"]["items"]
            items += page["nodes"]
            more, cursor = page["pageInfo"]["hasNextPage"], page["pageInfo"]["endCursor"]
            if until and any(n["updatedAt"] < until for n in page["nodes"]):
                more = False
        except (KeyError, TypeError):
            raise GitHubError("GitHub sent a reply in an unexpected shape") from None
        if not more:
            return items


def fetch_repo(token, repo, session=None, since=None, tracker=None, counted=()):
    """Return {"issues": [...], "pullRequests": [...]} exactly as GitHub sends the nodes.

    With `since` (an ISO time such as 2026-09-01T00:00:00Z) it reads only the open issues and pull requests plus those
    finished on or after that time, and the result also carries what the board needs about the rest: see fetch_recent.
    """
    owner, name = repo.split("/", 1)
    session = session or requests.Session()
    if since:
        return fetch_recent(token, owner, name, session, since, tracker, counted)
    return {
        "issues": _all_items(session, token, ISSUES_QUERY, owner, name, ISSUE_PAGE),
        "pullRequests": _all_items(session, token, PULLS_QUERY, owner, name, PULL_PAGE),
    }


def fetch_recent(token, owner, name, session, since, tracker, counted):
    """The recent read: open work plus what was finished since `since`, in far fewer pages on a big repo.

    Three things on the board look at more than that, so the read carries them too:
    - "trackedStates": the state of each issue a tracker lists that the read does not hold, asked for by number in one
      query, so a tracker's progress counts the issues it lists whatever their age.
    - "labelCounts": how many issues with each label in `counted` (the milestone and priority labels) are closed.
    - "since": so the calendar can tell a quiet day from a day before the read starts.
    """
    def read(query, size, **kw):
        return _all_items(session, token, query, owner, name, size, **kw)

    issues = read(OPEN_ISSUES_QUERY, ISSUE_PAGE)
    issues += [n for n in read(CLOSED_ISSUES_QUERY, ISSUE_PAGE, extra={"since": since}) if (n.get("closedAt") or "") >= since]
    pulls = read(OPEN_PULLS_QUERY, PULL_PAGE)
    pulls += [n for n in read(CLOSED_PULLS_QUERY, PULL_PAGE, until=since) if (n.get("mergedAt") or n.get("closedAt") or "") >= since]
    raw = {"issues": issues, "pullRequests": pulls, "since": since,
           "trackedStates": _tracked_states(session, token, owner, name, issues, pulls, tracker),
           "labelCounts": _label_counts(session, token, owner, name, counted)}
    return raw


def _tracked_states(session, token, owner, name, issues, pulls, tracker):
    """{number: state} for issues and pull requests the open trackers list but the read does not hold."""
    from board import tracked_numbers  # the board decides what a tracker lists; asked here, once, for the missing ones
    if not tracker:
        return {}
    have = {n["number"] for n in issues + pulls}
    wanted = sorted({k for n in issues if n["state"] == "OPEN" and tracker in {l["name"] for l in n["labels"]["nodes"]}
                     for k in tracked_numbers(n)} - have)
    states = {}
    for i in range(0, len(wanted), 100):
        chunk = wanted[i:i + 100]
        fields = " ".join(f"n{k}: issueOrPullRequest(number: {int(k)}) {{ ... on Issue {{ state }} ... on PullRequest {{ state }} }}"
                          for k in chunk)
        data = _post(session, token, "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s } }"
                     % fields, {"owner": owner, "name": name})
        found = data.get("repository") or {}
        states.update({k: found[f"n{k}"]["state"] for k in chunk if isinstance(found.get(f"n{k}"), dict) and found[f"n{k}"].get("state")})
    return states


def _label_counts(session, token, owner, name, labels):
    """{label: closed issue count} for each label, from GitHub's own totals."""
    labels = [l for l in dict.fromkeys(labels) if l]
    if not labels:
        return {}
    fields = " ".join(f"c{i}: issues(labels: [{json.dumps(l)}], states: CLOSED) {{ totalCount }}" for i, l in enumerate(labels))
    data = _post(session, token, "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s } }"
                 % fields, {"owner": owner, "name": name})
    found = data.get("repository") or {}
    try:
        return {l: found[f"c{i}"]["totalCount"] for i, l in enumerate(labels)}
    except (KeyError, TypeError):
        raise GitHubError("GitHub sent a reply in an unexpected shape") from None


def _rest(session, token, method, url, body=None):
    """One GitHub REST call with the board's token. A network failure is a GitHubError."""
    try:
        return session.request(method, url, json=body, timeout=20,
                               headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                        "X-GitHub-Api-Version": "2022-11-28"})
    except requests.RequestException as e:
        raise GitHubError(f"Could not reach GitHub ({type(e).__name__})") from None


def set_assignee(token, repo, number, login, assigned, session=None):
    """Add or remove one assignee on an issue. Returns the assignee logins GitHub reports afterwards."""
    session = session or requests.Session()
    r = _rest(session, token, "POST" if assigned else "DELETE",
              f"https://api.github.com/repos/{repo}/issues/{int(number)}/assignees", {"assignees": [login]})
    if r.status_code not in (200, 201):
        raise GitHubError(f"GitHub refused the change (HTTP {r.status_code})")
    try:
        reply = r.json()
        if not isinstance(reply, dict):
            raise TypeError
        logins = [a["login"] for a in reply.get("assignees") or []]
    except (ValueError, TypeError, KeyError):
        raise GitHubError("GitHub sent a reply in an unexpected shape") from None
    if (login.lower() in [str(l).lower() for l in logins]) != assigned:  # GitHub skips people it will not assign, silently
        raise GitHubError(f"GitHub did not {'add' if assigned else 'remove'} {login}")
    return logins


def _message(r):
    try:
        body = r.json()
    except ValueError:
        return None
    return body.get("message") if isinstance(body, dict) else None


def set_label(token, repo, number, label, on, session=None):
    """Add or remove one label on an issue. Removing a label the issue no longer has counts as done."""
    session = session or requests.Session()
    base = f"https://api.github.com/repos/{repo}/issues/{int(number)}/labels"
    if on:
        r = _rest(session, token, "POST", base, {"labels": [label]})
    else:
        r = _rest(session, token, "DELETE", f"{base}/{quote(label, safe='')}")
        if r.status_code == 404 and _message(r) == "Label does not exist":  # the issue doesn't carry it: done
            return  # a plain "Not Found" (no such issue, or no access) stays an error
    if r.status_code != 200:
        raise GitHubError(f"GitHub refused the change (HTTP {r.status_code})")
    try:
        names = [l["name"] for l in r.json()]
    except (ValueError, TypeError, KeyError):
        raise GitHubError("GitHub sent a reply in an unexpected shape") from None
    if (label in names) != on:
        raise GitHubError(f"GitHub did not {'add' if on else 'remove'} the {label} label")


def add_comment(token, repo, number, body, session=None):
    """Post one comment on an issue."""
    session = session or requests.Session()
    r = _rest(session, token, "POST", f"https://api.github.com/repos/{repo}/issues/{int(number)}/comments", {"body": body})
    if r.status_code != 201:
        raise GitHubError(f"GitHub refused the comment (HTTP {r.status_code})")


def load_fixture(path):
    with open(path) as f:
        return json.load(f)


class Cache:
    """Keep the last good read, and never make a page wait for a refresh once there is one.

    Past `ttl` seconds the old read is still returned at once, and one refresh runs in the background.
    Only the very first read blocks; requests that arrive while it runs share its result. If a refresh
    fails, the old read is served with the error and the next try waits RETRY_AFTER_ERROR seconds.
    """

    RETRY_AFTER_ERROR = 60

    def __init__(self, loader, ttl, clock=time.time, spawn=None):
        self._loader, self._ttl, self._clock = loader, ttl, clock
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="github-refresh", daemon=True).start())
        self._lock = threading.Lock()   # guards the fields below
        self._read = threading.Condition(self._lock)  # told when a refresh ends
        self._first = threading.Lock()  # one first read at a time
        self._data, self._fetched_at, self._error = None, None, None
        self._next_try, self._refreshing, self._attempts = 0.0, False, 0
        self._edits, self._seq = [], 0  # changes the board made, numbered, kept until a read has seen them

    def _refresh(self):
        """Run the loader once and record the result. Never raises."""
        with self._lock:
            seen = self._seq  # GitHub already had every change up to this one when this read began
        started, data, error = self._clock(), None, "Unexpected error reading GitHub"
        try:
            data, error = self._loader(), None
            sizes = {k: len(v) for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
            log.info("Read GitHub in %.1f s: %s", self._clock() - started, sizes)
        except GitHubError as e:
            error = str(e)
            log.warning("GitHub read failed: %s", error)
        except Exception as e:  # an odd reply or a bug must not turn every page into a 500
            log.exception("Unexpected error reading GitHub")
            error = f"Unexpected error reading GitHub ({type(e).__name__})"
        finally:
            with self._lock:
                if error is None:
                    # A change made while this read was under way may be missing from it: apply it again.
                    self._edits = [(n, fn) for n, fn in self._edits if n > seen]
                    for _, fn in self._edits:
                        self._apply(fn, data)
                    self._data, self._fetched_at = data, started
                self._error = error
                # Counted from when this read began, so a copy is read again as soon as it is `ttl` old.
                self._next_try = started + self._ttl if error is None else self._clock() + min(self.RETRY_AFTER_ERROR, self._ttl)
                self._refreshing = False
                self._attempts += 1
                self._read.notify_all()

    def edit(self, fn):
        """Apply fn to the cached read, so a change the board just made shows before the next refresh.

        fn must be safe to apply twice: it is applied again to a read that was already under way.
        """
        with self._lock:
            self._seq += 1
            self._edits.append((self._seq, fn))
            if self._data is not None:
                self._apply(fn, self._data)

    @staticmethod
    def _apply(fn, data):
        try:
            fn(data)
        except Exception:  # a change that no longer fits the read must not break the board
            log.exception("Could not apply a board change to the GitHub read")

    def read(self, fn):
        """fn(data) under the lock, so a change the board is making can't alter the read halfway through."""
        with self._lock:
            return fn(self._data)

    def wait(self, timeout):
        """Block until no refresh is running, or timeout seconds pass. True if none is running now."""
        with self._lock:
            return self._read.wait_for(lambda: not self._refreshing, timeout)

    def get(self):
        """Return (data, fetched_at, error). Raises GitHubError only when there has never been a good read."""
        with self._lock:
            have, attempts = self._data is not None, self._attempts
            stale = have and not self._refreshing and self._clock() >= self._next_try
            if stale:
                self._refreshing = True
        if stale:
            self._spawn(self._refresh)
        elif not have:
            with self._first:
                with self._lock:
                    nobody_tried = self._attempts == attempts
                if nobody_tried:  # otherwise use the read that finished while this request waited
                    self._refresh()
        with self._lock:
            if self._data is None:
                raise GitHubError(self._error or "GitHub could not be read")
            return self._data, self._fetched_at, self._error
