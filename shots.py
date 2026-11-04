"""Annotated screenshots an agent saved for a robot-vouched item: PNGs at <bucket>/<number>/<name>.png.

pomodo.toml's [shots] bucket names a private Google Cloud Storage bucket, read as the board's own service account
(Application Default Credentials). [shots] folder names a local folder laid out the same way, for development.
With neither set, no item has screenshots and nothing is read.
"""
import logging
import pathlib
import re
from urllib.parse import quote

from config import CONFIG

log = logging.getLogger("pomodo")
BUCKET = CONFIG.shots_bucket
FOLDER = CONFIG.shots_folder
NAME = re.compile(r"[\w][\w .,()'-]{0,150}\.png")  # one file in an item's folder: no slashes, no "..", PNG only
STORAGE = "https://storage.googleapis.com/storage/v1/b/"
_session = None
_last = {}  # the last good listing, kept when one fails so a storage hiccup doesn't hide every picture


def _gcs():
    global _session
    if _session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        _session = AuthorizedSession(creds)
    return _session


def _keep(found, number, name):
    if NAME.fullmatch(name):
        found.setdefault(number, []).append(name)
    elif name.endswith(".png"):
        log.warning("Skipped screenshot %s/%s: use letters, digits, spaces and .,()'- only", number, name)


def _sorted(found):
    """{"24": ["1 Details.png", "2 More detail.png"]}: names in the order an agent numbered them."""
    return {n: sorted(names, key=lambda s: (int(m.group()) if (m := re.match(r"\d+", s)) else 10 ** 6, s))
            for n, names in found.items()}


def list_shots():
    """Every item's screenshots, keyed by its number as text. A failed listing logs and keeps the last good one."""
    global _last
    if not (FOLDER or BUCKET):
        return {}
    found = {}
    try:
        if FOLDER:
            for p in pathlib.Path(FOLDER).glob("*/*.png"):
                if p.parent.name.isdigit():
                    _keep(found, p.parent.name, p.name)
        else:
            page = None
            while True:
                r = _gcs().get(STORAGE + quote(BUCKET, safe="") + "/o", params={"pageToken": page} if page else {},
                               timeout=20)
                r.raise_for_status()
                body = r.json()
                for o in body.get("items", []):
                    number, _, name = o["name"].partition("/")
                    if number.isdigit():
                        _keep(found, number, name)
                page = body.get("nextPageToken")
                if not page:
                    break
    except Exception as e:  # screenshots are extra: the board still shows without them
        log.warning("Listing screenshots failed: %s", e)
        return _last
    _last = _sorted(found)
    return _last


def read_shot(number, name):
    """The PNG's bytes, or None when there is no such file."""
    if not NAME.fullmatch(name):
        return None
    if FOLDER:
        p = pathlib.Path(FOLDER) / str(int(number)) / name
        return p.read_bytes() if p.is_file() else None
    if BUCKET:
        r = _gcs().get(STORAGE + quote(BUCKET, safe="") + "/o/" + quote(f"{int(number)}/{name}", safe=""),
                       params={"alt": "media"}, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content
    return None
