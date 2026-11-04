"""Details open on the plain words and an agent's annotated screenshots; everything else folds under More detail."""
import logging
import re
from pathlib import Path

import pytest

import main
import shots
from board import clean_html, plain_words
from config import ConfigError, parse
from github import Cache
from test_auth import sign_in
from test_board import _node
from test_demo import demo_board  # noqa: F401  (a fixture)

PLAIN = '<h2 dir="auto">In plain words</h2>\n<p dir="auto">The export now keeps every row.</p>\n'  # as GitHub sends it
REST = '<h2 dir="auto">What and why</h2>\n<p dir="auto">The loop stopped one short.</p>'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(shots, "FOLDER", str(tmp_path))
    monkeypatch.setattr(shots, "BUCKET", "")
    monkeypatch.setattr(shots, "_last", {})

    def use(raw, signed_in=True):
        raw["shots"] = shots.list_shots()  # as main._load does after a GitHub read
        monkeypatch.setattr(main, "cache", Cache(lambda: raw, ttl=300, spawn=lambda f: f()))
        c = main.app.test_client()
        if signed_in:
            sign_in(c, "ada-demo")
        return c
    return use


def _png(folder, number, name):
    d = folder / str(number)
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(b"\x89PNG\r\n\x1a\nfake")


def _details(html, ref):
    return html.split(ref, 1)[1].split('<details class="more"', 1)[1].split("</article>", 1)[0]


def test_the_plain_words_section_splits_from_the_rest_of_the_description():
    assert plain_words("<h2>In plain words</h2><p>A</p><h2>What and why</h2><p>B</p>") == (
        "<p>A</p>", "<h2>What and why</h2><p>B</p>")
    assert plain_words("<h3>in plain words:</h3><p>A</p>") == ("<p>A</p>", "")  # to the end when nothing follows
    assert plain_words("<h2>What and why</h2><p>B</p>") == ("", "<h2>What and why</h2><p>B</p>")
    assert plain_words("<h2>In plain words</h2><h2>What</h2><p>B</p>")[0] == ""  # an empty section is no section
    assert plain_words(clean_html(PLAIN + REST))[0] == "<p>The export now keeps every row.</p>"  # GitHub's own HTML
    for nested in ("<blockquote><h2>In plain words</h2><p>Hi</p></blockquote><p>tail</p>",
                   "<details><summary>Notes</summary><h2>In plain words</h2><p>Hi</p></details>"):
        assert plain_words(nested) == ("", nested)  # inside a quote or a fold: splitting it would leave it open


def test_details_open_on_plain_words_and_screenshots_with_the_rest_under_more_detail(client, tmp_path):
    _png(tmp_path, 41, "2 Export menu.png")
    _png(tmp_path, 41, "10 Last one.png")
    _png(tmp_path, 41, "1 The export.png")
    pr = _node(41, kind="pr", labels=["robot-vouched"], body=PLAIN + REST)
    html = client({"issues": [_node(1)], "pullRequests": [pr]}).get("/").get_data(as_text=True)
    d = _details(html, "#41</span>")
    first, rest = d.split('<details class="more-detail">', 1)
    assert "The export now keeps every row." in first and "The loop stopped one short" not in first
    imgs = [s.split('"', 1)[0] for s in first.split('<img src="')[1:]]
    assert imgs == ["/shots/41/1%20The%20export.png", "/shots/41/2%20Export%20menu.png", "/shots/41/10%20Last%20one.png"]
    assert 'alt="The export"' in first and 'loading="lazy"' in first
    assert "More detail" in rest and "The loop stopped one short" in rest and "Opened by" in rest
    assert "In plain words" not in d  # the heading itself is not repeated


def test_plain_words_alone_still_fold_the_rest_under_more_detail(client):
    html = client({"issues": [_node(8, body=PLAIN + REST)], "pullRequests": []}).get("/").get_data(as_text=True)
    first, rest = _details(html, "#8</span>").split('<details class="more-detail">', 1)
    assert 'class="prose plain-words"' in first and "<img" not in first and "The loop stopped one short" in rest


def test_screenshots_show_only_once_the_robot_vouched_label_is_on(client, tmp_path):
    _png(tmp_path, 42, "1 Shot.png")
    html = client({"issues": [_node(1)], "pullRequests": [_node(42, kind="pr", body=REST)]}).get("/").get_data(as_text=True)
    assert "/shots/42/" not in html and 'class="more-detail"' not in html


def test_an_item_without_plain_words_or_screenshots_opens_as_before(client):
    html = client({"issues": [_node(7, body="<p>Typo in the guide</p>")], "pullRequests": []}).get("/").get_data(as_text=True)
    d = _details(html, "#7</span>")
    assert "Typo in the guide" in d and "more-detail" not in d and "plain-words" not in d


def test_the_shot_route_serves_a_png_and_refuses_anything_else(client, tmp_path):
    _png(tmp_path, 41, "1 The export.png")
    (tmp_path / "secret.png").write_bytes(b"no")
    (tmp_path / "41" / "notes.txt").write_text("not a screenshot")
    c = client({"issues": [], "pullRequests": []})
    r = c.get("/shots/41/1%20The%20export.png")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")
    assert r.headers["Cache-Control"] == "private, max-age=600"
    assert c.get("/shots/41/2.png").status_code == 404
    assert c.get("/shots/41/..%2Fsecret.png").status_code == 404
    assert c.get("/shots/41/notes.txt").status_code == 404
    assert c.get("/shots/x/1.png").status_code == 404


def test_a_screenshot_needs_signing_in_like_the_page(client, tmp_path):
    _png(tmp_path, 41, "1 The export.png")
    r = client({"issues": [], "pullRequests": []}, signed_in=False).get("/shots/41/1%20The%20export.png")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login") and not r.data.startswith(b"\x89PNG")


def test_no_bucket_and_no_folder_means_no_screenshots_and_nothing_read(monkeypatch):
    monkeypatch.setattr(shots, "FOLDER", "")
    monkeypatch.setattr(shots, "BUCKET", "")
    monkeypatch.setattr(shots, "_gcs", lambda: pytest.fail("read storage with nothing configured"))
    assert shots.list_shots() == {} and shots.read_shot(41, "1 The export.png") is None


def test_a_failed_bucket_listing_keeps_the_last_good_one_and_logs(monkeypatch, tmp_path, caplog):
    _png(tmp_path, 41, "1 The export.png")
    _png(tmp_path, 41, "2 Search & filter.png")  # a name the route would refuse: skipped, with a log line
    monkeypatch.setattr(shots, "FOLDER", str(tmp_path))
    monkeypatch.setattr(shots, "_last", {})
    with caplog.at_level(logging.WARNING, logger="pomodo"):
        assert shots.list_shots() == {"41": ["1 The export.png"]}
    assert "Skipped screenshot 41/2 Search & filter.png" in caplog.text
    monkeypatch.setattr(shots, "FOLDER", "")
    monkeypatch.setattr(shots, "BUCKET", "b")

    def boom():
        raise RuntimeError("no credentials")
    monkeypatch.setattr(shots, "_gcs", boom)
    with caplog.at_level(logging.WARNING, logger="pomodo"):
        assert shots.list_shots() == {"41": ["1 The export.png"]}
    assert "Listing screenshots failed: no credentials" in caplog.text


def test_a_bucket_listing_reads_every_page_and_only_numbered_folders(monkeypatch):
    pages = {None: {"items": [{"name": "41/2 B.png"}, {"name": "notes/1 A.png"}], "nextPageToken": "p2"},
             "p2": {"items": [{"name": "41/1 A.png"}, {"name": "7/1 C.png"}, {"name": "7/x/1 D.png"}]}}

    class Session:
        def get(self, url, params, timeout):
            assert url == shots.STORAGE + "my-shots/o"
            body = pages[params.get("pageToken")]
            return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: body})()
    monkeypatch.setattr(shots, "FOLDER", "")
    monkeypatch.setattr(shots, "BUCKET", "my-shots")
    monkeypatch.setattr(shots, "_gcs", Session)
    assert shots.list_shots() == {"41": ["1 A.png", "2 B.png"], "7": ["1 C.png"]}


GOOD = 'repo = "acme/widgets"\n[[team]]\nlogin = "ada-demo"\n'


def test_the_settings_take_a_bucket_or_a_folder_but_not_both():
    import tomllib
    load = lambda extra: parse(tomllib.loads(GOOD + extra))  # noqa: E731
    assert (load("").shots_bucket, load("").shots_folder) == ("", "")
    assert load('[shots]\nbucket = "my-board-shots"\n').shots_bucket == "my-board-shots"
    assert load('[shots]\nfolder = "dev/shots"\n').shots_folder == "dev/shots"
    for extra, message in (('[shots]\nbucket = "gs://my-board-shots"\n', "shots.bucket"),
                           ('[shots]\nbucket = "my-board-shots"\nfolder = "dev/shots"\n', "pick one"),
                           ('[shots]\nbuckets = "b"\n', "unknown key shots.buckets")):
        with pytest.raises(ConfigError, match=re.escape(message)):
            load(extra)


def test_the_demo_shows_one_vouched_pull_request_with_its_own_screenshot(demo_board, monkeypatch):  # noqa: F811
    monkeypatch.setattr(shots, "list_shots", lambda: pytest.fail("read the configured screenshots in DEMO mode"))
    monkeypatch.setattr(shots, "read_shot", lambda *a: pytest.fail("read the configured screenshots in DEMO mode"))
    client, _ = demo_board
    html = client.get("/").get_data(as_text=True)
    d = _details(html, "Add Spanish translations for the sign-in page")
    first = d.split('<details class="more-detail">', 1)[0]
    assert "The sign-in page now speaks Spanish" in first
    url = re.search(r'<img src="(/shots/\d+/[^"]+)"', first).group(1)
    r = client.get(url)
    assert r.status_code == 200 and r.data.startswith(b"\x89PNG")
    assert client.get(url.rsplit("/", 1)[0] + "/2%20Other.png").status_code == 404  # only what the demo lists
    assert html.count('<details class="more-detail">') == 1  # every other demo item opens as before


def test_the_dockerfile_ships_the_screenshot_code_and_the_demo_picture():
    dockerfile = (Path(main.__file__).parent / "Dockerfile").read_text()
    assert re.search(r"^COPY .*\bshots\.py\b", dockerfile, re.M)
    assert re.search(r"^COPY demo_shots\b", dockerfile, re.M)
