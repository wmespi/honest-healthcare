"""Shared fixtures for the app's tests.

`api_data` builds the build's default test corpus (build/tests/corpus.py) with
`make build`'s own build() into FIX_DIR/serving, so the app reads tables the
real build wrote, and `api` binds a `TestClient` to the app reading them.
"""
import os

import pytest

# DATA_DIR / SERVING_DIR must be set before `serving.*` is imported
# (data_sources.py freezes the glob paths at import time). conftest.py is
# loaded before any test module. Repo-root relative (not "/app/…") so this
# also runs on the host under `make test LOCAL=1` — in the container the root
# resolves to /app. GH #59.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIX_DIR = os.path.join(_REPO, "data-test", "apifix")
os.environ["DATA_DIR"] = FIX_DIR
os.environ["SERVING_DIR"] = f"{FIX_DIR}/serving"


@pytest.fixture(scope="session")
def api_data():
    """The default corpus, built into FIX_DIR/serving by `make build`'s own
    build(), which the app reads."""
    import shutil
    from build.build import build
    from build.tests.corpus import default_corpus
    shutil.rmtree(FIX_DIR, ignore_errors=True)
    c = default_corpus(f"{FIX_DIR}/corpus")
    build(c.root, f"{FIX_DIR}/serving", c.targets, c.services_file)
    yield FIX_DIR
    shutil.rmtree(FIX_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def api(api_data):
    """A TestClient bound to the app, reading the fixture DATA_DIR in-process."""
    from fastapi.testclient import TestClient
    from serving.main import app
    with TestClient(app) as c:
        yield c
