import logging

import pytest

import board
import config
import main
from config import ConfigError, load, parse

GOOD = """
repo = "acme/widgets"

[time]
zone = "Europe/London"
zone_label = "UK"
work_start = 9
work_end = 18

[[team]]
login = "ada-demo"
name = "Ada"
zone = "Europe/London"

[[team]]
login = "kofi-demo"
name = "Kofi"
zone = "Africa/Accra"
"""


def _load(tmp_path, text):
    path = tmp_path / "pomodo.toml"
    path.write_text(text)
    return load(path)


def test_the_tests_run_with_the_fictional_test_team():
    assert config.CONFIG.repo == main.REPO == "test-org/test-repo"
    assert board.PEOPLE == {"ada-demo": "Ada", "kofi-demo": "Kofi", "mei-demo": "Mei"}  # in card order
    assert board.PEOPLE_TZ == {"ada-demo": "Europe/London", "kofi-demo": "Europe/Rome", "mei-demo": "Asia/Singapore"}
    assert (str(board.BOARD_TZ), board.ZONE_LABEL, board.WORK_START, board.WORK_END) == ("Europe/London", "UK", 9, 18)
    assert (board.PRIORITY_LABEL, board.MILESTONE_LABEL, board.TODO_LABEL) == ("priority", "v1", "todo")


def test_the_example_file_is_the_plans_shape_with_a_fictional_team():
    c = load(config.EXAMPLE_PATH)
    assert (c.title, c.repo, c.zone, c.zone_label, c.work_start, c.work_end) == (
        "Pomodo", "your-org/your-repo", "Europe/London", "UK", 9, 18)
    assert [(p.login, p.name, p.zone) for p in c.team] == [
        ("ada-demo", "Ada", "Europe/London"), ("kofi-demo", "Kofi", "Africa/Accra"), ("mei-demo", "Mei", "Asia/Singapore")]
    assert c.labels == config.Labels(chore="todo", tracker="tracker", verified="human-verified", priority="priority",
                                     priority_title="Top priority", nice="nice-to-have", milestone="")


def test_left_out_keys_take_their_defaults(tmp_path):
    c = _load(tmp_path, 'repo = "acme/widgets"\n[[team]]\nlogin = "ada-demo"\n')
    assert (c.title, c.zone, c.zone_label, c.work_start, c.work_end) == ("Pomodo", "UTC", "UTC", 9, 18)
    assert c.team == (config.Person(login="ada-demo", name="ada-demo", zone="UTC"),)  # name: the login; zone: the board's
    assert (c.labels.priority, c.labels.priority_title, c.labels.milestone) == ("priority", "Top priority", "")


def test_a_missing_file_falls_back_to_the_example_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        c = load(tmp_path / "nowhere.toml")
    assert c == load(config.EXAMPLE_PATH)
    assert "No settings file at" in caplog.text and "nowhere.toml" in caplog.text and "pomodo.example.toml" in caplog.text


def test_the_path_comes_from_pomodo_config(tmp_path, monkeypatch):
    path = tmp_path / "team.toml"
    path.write_text(GOOD)
    monkeypatch.setenv("POMODO_CONFIG", str(path))
    assert load().repo == "acme/widgets"


@pytest.mark.parametrize("change, key", [
    (('zone = "Europe/London"', 'zone = "Europe/Lndon"'), "time.zone"),  # the board's zone, the first one
    (('zone = "Africa/Accra"', 'zone = "Mars/Olympus"'), "team[1].zone"),
    (('zone = "Africa/Accra"', 'zone = 3'), "team[1].zone"),
    (('repo = "acme/widgets"', 'repo = "widgets"'), "repo"),
    (('repo = "acme/widgets"', 'repo = "https://github.com/acme/widgets"'), "repo"),
    (('repo = "acme/widgets"', 'repo = "acme/"'), "repo"),
    (('repo = "acme/widgets"', ''), "repo is missing"),
    (("work_start = 9", "work_start = 18"), "time.work_start (18) must be before time.work_end (18)"),
    (("work_end = 18", "work_end = 8"), "time.work_start (9) must be before time.work_end (8)"),
    (("work_end = 18", "work_end = 25"), "time.work_end"),
    (("work_end = 18", "work_end = true"), "time.work_end"),  # TOML true is not an hour, though Python counts it as 1
    (("work_end = 18", 'work_end = "18"'), "time.work_end"),
    (('login = "kofi-demo"', 'login = "ADA-demo"'), 'team[1].login = "ADA-demo" is already on the team'),
    (('login = "kofi-demo"', 'login = "kofi demo"'), "team[1].login"),
    (("work_end = 18", "work_end = 18\nwork_ned = 17"), "unknown key time.work_ned"),
    (('name = "Kofi"', 'name = "Kofi"\nemail = "k@example.com"'), "unknown key team[1].email"),
    (('repo = "acme/widgets"', 'repo = "acme/widgets"\n[labels]\npriority = ""'), "labels.priority"),
])
def test_a_bad_value_is_refused_by_name(tmp_path, change, key):
    old, new = change
    assert old in GOOD
    with pytest.raises(ConfigError, match="^pomodo.toml: ") as e:
        _load(tmp_path, GOOD.replace(old, new, 1))
    assert key in str(e.value)


def test_a_team_needs_someone_and_the_file_must_be_toml(tmp_path):
    with pytest.raises(ConfigError, match="team needs at least one person"):
        parse({"repo": "acme/widgets"})
    with pytest.raises(ConfigError, match="team needs at least one person"):
        parse({"repo": "acme/widgets", "team": []})
    with pytest.raises(ConfigError, match="not valid TOML"):
        _load(tmp_path, 'repo = "acme/widgets\n')


def test_an_empty_milestone_is_allowed_and_other_labels_are_not():
    base = {"repo": "acme/widgets", "team": [{"login": "ada-demo"}]}
    assert parse({**base, "labels": {"milestone": ""}}).labels.milestone == ""
    with pytest.raises(ConfigError, match="labels.chore"):
        parse({**base, "labels": {"chore": "  "}})


def test_hours_and_names_as_the_page_says_them():
    assert [board.hour_text(h) for h in (0, 9, 12, 13, 18, 24)] == ["midnight", "9 am", "noon", "1 pm", "6 pm", "midnight"]
    assert [board.team_names(n) for n in (["Ada"], ["Ada", "Kofi"], ["Ada", "Kofi", "Mei"])] == [
        "Ada", "Ada and Kofi", "Ada, Kofi and Mei"]


def test_a_team_member_may_carry_their_github_account_number(tmp_path):
    import config
    base = 'repo = "o/r"\n[[team]]\nlogin = "ada-demo"\n'
    f = tmp_path / "p.toml"
    f.write_text(base + "id = 42\n")
    assert config.load(f).team[0].id == 42
    for bad in ('"42"', "0", "true"):
        f.write_text(base + f"id = {bad}\n")
        with pytest.raises(config.ConfigError, match="team\\[0\\].id"):
            config.load(f)


def test_a_deployed_board_will_not_fall_back_to_the_example_team(tmp_path, monkeypatch):
    import config
    monkeypatch.setenv("POMODO_PRODUCTION", "1")
    with pytest.raises(config.ConfigError, match="needs its own pomodo.toml"):
        config.load(tmp_path / "missing.toml")
    monkeypatch.setenv("DEMO", "1")  # a demo container may still show the made-up team
    assert config.load(tmp_path / "missing.toml").team[0].login == "ada-demo"
