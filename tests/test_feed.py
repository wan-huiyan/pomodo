import copy

import pytest

from feeder import feed
from feeder.feed import PlanError, check_plan, issue_body, keys_in, note_body, quiet

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
    assert "<!--" not in out and "-->" not in out


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
