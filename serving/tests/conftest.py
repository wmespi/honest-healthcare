"""Shared fixtures for the app's tests.

The app's tests read the find-care tables integration builds from its default
test corpus into data-test/serving/ (integration/tests/app_tables.py), which
`make test` runs first, so they read tables the real build wrote, handed over
the way the real ones are. `api` binds a `TestClient` to the app reading them.
"""
import os

import pytest

# DATA_DIR / SERVING_DIR must be set before `serving.*` is imported
# (data_sources.py freezes the table paths at import time). conftest.py is
# loaded before any test module. Repo-root relative (not "/app/…") so this
# also runs on the host under `make test LOCAL=1` — in the container the root
# resolves to /app. GH #59.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVING_DIR = os.path.join(_REPO, "data-test", "serving")
os.environ["DATA_DIR"] = os.path.join(_REPO, "data-test")
os.environ["SERVING_DIR"] = SERVING_DIR


@pytest.fixture(scope="session")
def api():
    """A TestClient bound to the app, reading the app's test tables in-process."""
    from fastapi.testclient import TestClient
    from serving.data_sources import missing_build
    from serving.main import app
    missing = missing_build()
    if missing:
        pytest.fail(f"{SERVING_DIR} is missing {missing}: run `make test`, which builds the app's "
                    "test tables first (python -m integration.tests.app_tables)")
    with TestClient(app) as c:
        yield c
