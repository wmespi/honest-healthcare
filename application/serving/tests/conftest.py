"""Shared fixtures for the app's tests.

The app's tests read the find-care tables integration builds from its default
test corpus into data-test/serving/ (integration/tests/app_tables.py), which
`make test` runs first, so they read tables the real build wrote, handed over
the way the real ones are. `api` binds a `TestClient` to the app reading them,
as an invited tester, and `stranger` binds one with no invite.
"""
import atexit
import hashlib
import os
import shutil
import tempfile

import pytest

# DATA_DIR / SERVING_DIR must be set before `serving.*` is imported
# (data_sources.py freezes the table paths at import time). conftest.py is
# loaded before any test module. Repo-root relative (not "/app/…") so this
# also runs on the host under `make test LOCAL=1` — in the container the root
# resolves to /app. GH #59.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SERVING_DIR = os.path.join(_REPO, "data-test", "serving")
os.environ["DATA_DIR"] = os.path.join(_REPO, "data-test")
os.environ["SERVING_DIR"] = SERVING_DIR

# A stand-in for the screen's build, so the tests of how the app serves it
# (test_screen.py) need no `npm run build`. It sits outside the repo and goes
# when the run ends. SCREEN_DIR is frozen at import time too (screen.py).
SCREEN_DIR = tempfile.mkdtemp(prefix="hh-screen-")
atexit.register(shutil.rmtree, SCREEN_DIR, True)
os.makedirs(os.path.join(SCREEN_DIR, "assets"))
with open(os.path.join(SCREEN_DIR, "index.html"), "w") as f:
    f.write('<!doctype html><div id="root"></div><script type="module" src="/assets/index-abc123.js"></script>')
with open(os.path.join(SCREEN_DIR, "assets", "index-abc123.js"), "w") as f:
    f.write("console.log('the screen');\n" * 100)
os.environ["SCREEN_DIR"] = SCREEN_DIR

# The invite gate on, as deployed, whatever the dev stack sets (gate.py), with
# one tester invited. Frozen at import time too.
INVITE = "test-invite-token"
os.environ["GATE"] = "on"
os.environ["INVITES"] = f"tester:{hashlib.sha256(INVITE.encode()).hexdigest()}"


@pytest.fixture(scope="session")
def app():
    from application.serving.data_sources import missing_build
    from application.serving.main import app
    missing = missing_build()
    if missing:
        pytest.fail(f"{SERVING_DIR} is missing {missing}: run `make test`, which builds the app's "
                    "test tables first (python -m integration.tests.app_tables)")
    return app


@pytest.fixture(scope="session")
def api(app):
    """A TestClient bound to the app, reading the app's test tables in-process,
    with the invited tester's cookie."""
    from fastapi.testclient import TestClient
    from application.serving.gate import COOKIE
    with TestClient(app, cookies={COOKIE: INVITE}) as c:
        yield c


@pytest.fixture()
def stranger(app):
    """A TestClient with no invite, which follows no redirect. Over https, as
    deployed, so it sends back the gate's `Secure` cookie."""
    from fastapi.testclient import TestClient
    with TestClient(app, follow_redirects=False, base_url="https://testserver") as c:
        yield c
