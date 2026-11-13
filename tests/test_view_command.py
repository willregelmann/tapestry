"""Starting the viewer in the background, as /tapestry:view does (docs/interfaces/VIEWER.md)."""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from tapestry.mind import Mind
from tests.test_mind import toy_encode


def _view(*args):
    return subprocess.run([sys.executable, "-m", "tapestry.cli", "view", "--detach", "--no-browser", *args],
                          capture_output=True, text=True, timeout=60)


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_a_viewer_started_in_the_background_says_where_it_is_and_stops_once_unused(tmp_path):
    path = tmp_path / "m.db"
    m = Mind(path, encode=toy_encode, model_tag="toy")
    m.remember("Sam keeps bees.", source="user")
    m.close()
    started = _view("--mind", str(path), "--idle", "3")
    assert started.returncode == 0, started.stdout + started.stderr
    url = re.search(r"http://127\.0\.0\.1:\d+/\?t=\S+", started.stdout).group(0)
    status, body = _get(url.replace("/?", "/api/memories?topics=general&"))
    assert status == 200 and json.loads(body)["memories"][0]["content"] == "Sam keeps bees."
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            _get(url.replace("/?", "/api/nothing?"))   # a request that is refused still counts as use
        except (urllib.error.URLError, ConnectionError):
            break
        time.sleep(4)                                  # longer than the idle limit, so it goes
    else:
        raise AssertionError("the viewer kept running after it was no longer used")


def test_a_viewer_that_cant_start_in_the_background_says_why(tmp_path):
    started = _view("--mind", str(tmp_path / "missing.db"))
    assert started.returncode == 1 and "there is no mind at" in started.stdout
    assert not (tmp_path / "missing.db").exists()
