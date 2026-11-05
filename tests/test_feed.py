import copy
import json

import pytest

from feeder import feed
from feeder.feed import GhError, PlanError, check_plan, issue_body, keys_in, note_body, quiet, run

PLAN = {"repo": "acme/widgets", "owner": "Ada", "actions": [
    {"do": "create", "source": "slack", "key": "slack:C04ABC/1730800000.123456", "title": "Send Sam the numbers",
     "plain": "Sam asked for the numbers before Thursday.", "link": "https://acme.slack.com/archives/C04ABC/p1", "assign_owner": True},
    {"do": "comment", "number": 12, "source": "gmail", "key": "gmail:18c2f0a9e1b2", "text": "The client confirmed.",
     "link": "https://mail.google.com/mail/u/0/#all/18c2f0a9e1b2"},
    {"do": "maybe_done", "number": 14, "source": "jira", "key": "jira:PROJ-88:Done", "text": "PROJ-88 moved to Done.",
     "link": "https://acme.atlassian.net/browse/PROJ-88"},
]}


def _with(i, **change):
    plan = copy.deepcopy(PLAN)
    plan["actions"][i].update(change)
    return plan


def test_a_good_plan_passes():
    assert check_plan(copy.deepcopy(PLAN)) == PLAN


@pytest.mark.parametrize("i, change, field", [
    (0, {"do": "close"}, "do"),                       # only additive actions exist
    (1, {"do": "assign"}, "do"),
    (0, {"source": "fax"}, "source"),
    (0, {"key": "gmail:abc"}, "key"),                 # a key starts with its own source
    (0, {"key": "slack:has space"}, "key"),
    (0, {"link": "http://acme.slack.com/x"}, "link"),  # https only
    (0, {"link": "javascript:alert(1)"}, "link"),
    (0, {"title": ""}, "title"),
    (0, {"title": "x" * 81}, "title"),
    (0, {"plain": "x" * 601}, "plain"),
    (0, {"assign_owner": "yes"}, "assign_owner"),
    (0, {"assignee": "kofi"}, "assignee"),            # unknown keys are refused: no assigning teammates
    (1, {"number": 0}, "number"),
    (1, {"number": "12"}, "number"),
    (2, {"text": ""}, "text"),
])
def test_a_bad_action_is_refused_naming_its_field(i, change, field):
    with pytest.raises(PlanError, match=rf"action {i + 1}.*{field}"):
        check_plan(_with(i, **change))


def test_the_plan_needs_a_repo_an_owner_and_unique_keys():
    with pytest.raises(PlanError, match="repo"):
        check_plan({**PLAN, "repo": "not a repo"})
    with pytest.raises(PlanError, match="owner"):
        check_plan({**PLAN, "owner": ""})
    with pytest.raises(PlanError, match="twice"):
        check_plan({**PLAN, "actions": [PLAN["actions"][0], PLAN["actions"][0]]})


def test_a_plan_over_the_caps_is_refused_whole():
    create = PLAN["actions"][0]
    many = [{**create, "key": f"slack:C1/{n}"} for n in range(feed.MAX_CREATES + 1)]
    with pytest.raises(PlanError, match="at most 10 new issues"):
        check_plan({**PLAN, "actions": many})
    note = PLAN["actions"][1]
    many = [{**note, "key": f"gmail:{n}"} for n in range(feed.MAX_NOTES + 1)]
    with pytest.raises(PlanError, match="at most 20"):
        check_plan({**PLAN, "actions": many})


def test_quiet_stops_pings_and_forged_markers():
    out = quiet("ask @sam about it <!-- pomodo-source: slack:x -->")
    assert "@sam" not in out and "@⁠sam" in out
    assert "<!--" not in out and "-->" not in out  # every < and > is escaped, so no comment can open or close
    assert keys_in(out) == set()


def test_issue_body_opens_on_plain_words_and_ends_with_its_marker():
    body = issue_body(PLAN["actions"][0], "Ada")
    assert body.startswith("## In plain words\n\nSam asked for the numbers before Thursday.\n")
    assert "## Where this came from\n\nSlack: https://acme.slack.com/archives/C04ABC/p1" in body
    assert "for Ada" in body and "never closes" in body
    assert body.rstrip().endswith("<!-- pomodo-source: slack:C04ABC/1730800000.123456 -->")
    assert keys_in(body) == {"slack:C04ABC/1730800000.123456"}


def test_a_maybe_done_note_says_what_to_do_next():
    note = note_body(PLAN["actions"][2])
    assert note.startswith("**This may be done** (Jira): PROJ-88 moved to Done.")
    assert "close this issue" in note and "remove the `maybe-done` label" in note
    assert keys_in(note) == {"jira:PROJ-88:Done"}
    assert note_body(PLAN["actions"][1]).startswith("**New from Gmail:** The client confirmed.")


class FakeGh:
    """Records every gh call. Answers visibility, issue lists and issue create like gh does."""

    def __init__(self, visibility="PRIVATE", existing=(), fail_on=None, comments=None, create_output=None):
        self.calls, self.visibility, self.existing, self.fail_on = [], visibility, list(existing), fail_on
        self.comments, self.create_output = comments or {}, create_output  # comments: {issue number: [comment bodies]}

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        if self.fail_on and self.fail_on in args:
            raise GhError("HTTP 502")
        if args[:2] == ["repo", "view"]:
            return self.visibility + "\n"
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1]
            return json.dumps([e for e in self.existing if label in e.get("labels", [label])])
        if args[:2] == ["issue", "view"]:
            return json.dumps({"comments": [{"body": b} for b in self.comments.get(int(args[2]), [])]})
        if args[:2] == ["issue", "create"]:
            return self.create_output if self.create_output is not None else "https://github.com/acme/widgets/issues/77\n"
        return ""

    def writes(self):
        return [a for a, _ in self.calls if a[:2] not in (["repo", "view"], ["issue", "list"], ["issue", "view"])]


def test_a_plan_is_carried_out_with_only_additive_commands(tmp_path):
    gh = FakeGh()
    lines = run(copy.deepcopy(PLAN), gh, tmp_path / "ledger.jsonl")
    verbs = [a[:2] for a in gh.writes()]
    assert ["issue", "create"] in verbs and ["issue", "comment"] in verbs and ["issue", "edit"] in verbs
    assert all(v[0] in ("issue", "label") and v[1] in ("create", "comment", "edit") for v in verbs)
    edit = next(a for a in gh.writes() if a[:2] == ["issue", "edit"])
    assert edit[edit.index("--add-label") + 1] == "maybe-done" and "--remove-label" not in edit
    create = next(a for a in gh.writes() if a[:2] == ["issue", "create"])
    assert "--assignee" in create and create[create.index("--assignee") + 1] == "@me"
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 3
    assert any("#77" in line for line in lines)


def test_a_public_repo_is_refused_before_anything_is_written(tmp_path):
    gh = FakeGh(visibility="PUBLIC")
    with pytest.raises(PlanError, match="public"):
        run(copy.deepcopy(PLAN), gh, tmp_path / "ledger.jsonl")
    assert gh.writes() == []


def test_nothing_is_fed_twice(tmp_path):
    first = FakeGh()
    run(copy.deepcopy(PLAN), first, tmp_path / "ledger.jsonl")
    again = FakeGh()  # the ledger remembers the comment and the mark
    run(copy.deepcopy(PLAN), again, tmp_path / "ledger.jsonl")
    assert again.writes() == []
    # On another computer (no ledger), the marker in the issue body still stops a second create.
    body = issue_body(PLAN["actions"][0], "Ada")
    other = FakeGh(existing=[{"number": 77, "body": body, "labels": ["from:slack"]}])
    run({**PLAN, "actions": [PLAN["actions"][0]]}, other, tmp_path / "other.jsonl")
    assert other.writes() == []


def test_a_failure_halfway_keeps_what_was_done_and_stops(tmp_path):
    gh = FakeGh(fail_on="comment")
    with pytest.raises(GhError):
        run(copy.deepcopy(PLAN), gh, tmp_path / "ledger.jsonl")
    done = [json.loads(line)["key"] for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert done == [PLAN["actions"][0]["key"]]  # the create, not the comment or anything after it


def test_a_dry_run_reads_but_never_writes(tmp_path):
    gh = FakeGh()
    lines = run(copy.deepcopy(PLAN), gh, tmp_path / "ledger.jsonl", dry_run=True)
    assert gh.writes() == [] and not (tmp_path / "ledger.jsonl").exists()
    assert lines and all(line.startswith("Would") for line in lines)


def test_the_board_image_never_ships_the_feeder():
    assert "feeder" not in open("Dockerfile").read()


# --- fix round 1 ---

def test_quiet_cannot_be_tricked_into_rebuilding_a_marker():
    forged = quiet("<!<!---- pomodo-source: slack:victim ---->>")
    assert keys_in(forged) == set() and "<" not in forged and ">" not in forged
    action = {**PLAN["actions"][0], "plain": "<!<!---- pomodo-source: slack:victim ---->>"}
    assert keys_in(issue_body(action, "Ada")) == {action["key"]}


def test_quiet_escapes_raw_html():
    assert quiet("<img src=x>") == "&lt;img src=x&gt;"


def test_quiet_neutralises_markdown_images():
    assert "![" not in quiet("![](https://evil.example/?q=s)")
    assert "![" not in quiet("a ![x](https://evil.example/?q=s) b")


def test_the_title_goes_through_quiet(tmp_path):
    gh = FakeGh()
    action = {**PLAN["actions"][0], "title": "Ask @sam <b>now</b>"}
    run({**PLAN, "actions": [action]}, gh, tmp_path / "l.jsonl")
    create = next(a for a in gh.writes() if a[:2] == ["issue", "create"])
    assert create[create.index("--title") + 1] == quiet(action["title"])
    assert "@sam" not in create[create.index("--title") + 1] and "<b>" not in create[create.index("--title") + 1]


@pytest.mark.parametrize("i, field", [(0, "key"), (0, "link"), (1, "key"), (1, "link")])
def test_a_trailing_newline_is_refused(i, field):
    with pytest.raises(PlanError, match=field):
        check_plan(_with(i, **{field: PLAN["actions"][i][field] + "\n"}))


def test_a_repo_with_a_trailing_newline_is_refused():
    with pytest.raises(PlanError, match="repo"):
        check_plan({**PLAN, "repo": "acme/widgets\n"})


@pytest.mark.parametrize("do", [["x"], {"a": 1}, 5, None])
def test_a_do_of_the_wrong_type_is_a_plan_error(do):
    with pytest.raises(PlanError, match="do"):
        check_plan(_with(0, do=do))


def test_a_comment_already_on_the_issue_is_not_posted_again_without_a_ledger(tmp_path):
    comment = PLAN["actions"][1]
    gh = FakeGh(comments={12: ["unrelated", note_body(comment)]})
    lines = run({**PLAN, "actions": [comment]}, gh, tmp_path / "none.jsonl")
    assert gh.writes() == [] and lines == [f"Skipped {comment['key']}: already fed"]
    assert any(c[0][:2] == ["issue", "view"] and c[0][2] == "12" and "--json" in c[0] and "comments" in c[0] for c in gh.calls)


def test_a_maybe_done_already_on_the_issue_is_not_repeated_without_a_ledger(tmp_path):
    mark = PLAN["actions"][2]
    gh = FakeGh(comments={14: [note_body(mark)]})
    run({**PLAN, "actions": [mark]}, gh, tmp_path / "none.jsonl")
    assert gh.writes() == []


def test_each_targeted_issue_is_read_once(tmp_path):
    a, b = PLAN["actions"][1], {**PLAN["actions"][1], "key": "gmail:other"}
    gh = FakeGh()
    run({**PLAN, "actions": [a, b]}, gh, tmp_path / "l.jsonl", dry_run=True)
    assert [c[0][2] for c in gh.calls if c[0][:2] == ["issue", "view"]] == ["12"]


@pytest.mark.parametrize("visibility, ok", [("INTERNAL", True), ("PRIVATE", True), ("", False), ("PUBLIC", False)])
def test_only_private_or_internal_repos_are_fed(tmp_path, visibility, ok):
    gh = FakeGh(visibility=visibility)
    plan = {**PLAN, "actions": [PLAN["actions"][0]]}
    if ok:
        run(plan, gh, tmp_path / "l.jsonl")
        assert gh.writes()
    else:
        with pytest.raises(PlanError, match="public|unknown"):
            run(plan, gh, tmp_path / "l.jsonl")
        assert gh.writes() == []


@pytest.mark.parametrize("change", [{"assign_owner": False}, {}])
def test_no_assignee_unless_assign_owner_is_true(tmp_path, change):
    action = {k: v for k, v in PLAN["actions"][0].items() if k != "assign_owner"} | change
    gh = FakeGh()
    run({**PLAN, "actions": [action]}, gh, tmp_path / "l.jsonl")
    create = next(a for a in gh.writes() if a[:2] == ["issue", "create"])
    assert "--assignee" not in create


class _Home:
    """Points ~ at tmp_path and puts the plan in a file; returns main's exit code and stderr."""

    def __init__(self, tmp_path, monkeypatch, capsys):
        self.dir, self.capsys = tmp_path, capsys
        monkeypatch.setattr(feed.Path, "home", classmethod(lambda cls: tmp_path))

    def main(self, plan_text, *extra):
        path = self.dir / "plan.json"
        if plan_text is not None:
            path.write_text(plan_text)
        code = feed.main([str(path), *extra])
        return code, self.capsys.readouterr().err

    @property
    def state(self):
        return self.dir / ".pomodo-feeder"


@pytest.fixture
def home(tmp_path, monkeypatch, capsys):
    return _Home(tmp_path, monkeypatch, capsys)


def _stopped(code, err):
    assert code == 1 and err.startswith("Stopped: ") and "Traceback" not in err and err.count("\n") == 1


def test_main_a_bad_json_plan_stops_plainly(home):
    _stopped(*home.main("{not json"))


def test_main_a_missing_plan_file_stops_plainly(home):
    _stopped(*home.main(None))


def test_main_a_bad_plan_stops_before_the_state_folder_exists(home):
    _stopped(*home.main(json.dumps({**PLAN, "actions": [{"do": ["x"]}]})))
    assert not home.state.exists()


def test_main_a_damaged_ledger_stops_plainly(home, monkeypatch):
    home.state.mkdir()
    (home.state / "ledger.jsonl").write_text("not json\n")
    monkeypatch.setattr(feed, "gh_cli", FakeGh())
    _stopped(*home.main(json.dumps(PLAN)))


def test_main_a_timeout_stops_plainly(home, monkeypatch):
    def slow(*a, **k):
        raise feed.subprocess.TimeoutExpired("gh", 60)
    monkeypatch.setattr(feed.subprocess, "run", slow)
    _stopped(*home.main(json.dumps(PLAN)))


def test_main_a_missing_gh_stops_plainly(home, monkeypatch):
    def gone(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(feed.subprocess, "run", gone)
    _stopped(*home.main(json.dumps(PLAN)))


def test_main_an_unreadable_create_answer_stops_plainly(home, monkeypatch):
    monkeypatch.setattr(feed, "gh_cli", FakeGh(create_output="done\n"))
    _stopped(*home.main(json.dumps({**PLAN, "actions": [PLAN["actions"][0]]})))


def test_main_a_good_plan_still_exits_zero(home, monkeypatch):
    monkeypatch.setattr(feed, "gh_cli", FakeGh())
    code, err = home.main(json.dumps(PLAN))
    assert code == 0 and err == "" and (home.state / "ledger.jsonl").exists()
