"""The app serves the screen at every path outside /api (application/serving/screen.py),
from the stand-in build conftest.py makes, and fails without saying how.
"""
import os

from fastapi.testclient import TestClient

INDEX_HTML = open(os.path.join(os.environ["SCREEN_DIR"], "index.html")).read()
SCRIPT = "/assets/index-abc123.js"


def test_the_screen_is_at_the_root_and_checked_on_every_load(api):
    r = api.get("/")
    assert r.status_code == 200 and r.text == INDEX_HTML
    assert r.headers["content-type"].startswith("text/html") and r.headers["cache-control"] == "no-cache"


def test_a_screen_address_gets_the_screen(api):
    for path in ("/find-care?plan=45334GA0020002&zip=30308", "/docs", "/apiary"):
        r = api.get(path)
        assert r.status_code == 200 and r.text == INDEX_HTML, path


def test_a_built_asset_is_cached_for_good_and_compressed(api):
    r = api.get(SCRIPT, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.text.startswith("console.log")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["content-encoding"] == "gzip"


def test_a_missing_asset_is_a_404_not_the_screen(api):
    assert api.get("/assets/index-gone.js").status_code == 404


def test_a_path_out_of_the_build_never_reaches_the_file(api):
    for path in ("/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd", "/assets/..%2f..%2f..%2f..%2f..%2fetc%2fpasswd"):
        r = api.get(path)
        assert r.text == INDEX_HTML, path


def test_an_unknown_api_path_is_a_json_404(api):
    for path in ("/api", "/api/nope", "/api/find-care/extra"):
        r = api.get(path)
        assert r.status_code == 404 and r.headers["content-type"] == "application/json", path


def test_the_api_describes_itself_under_api(api):
    assert api.get("/api/openapi.json").json()["info"]["title"] == "Honest Healthcare API"
    assert api.get("/api/docs").status_code == 200


def test_a_screen_that_isnt_built_says_so(api, monkeypatch):
    from application.serving.main import app
    screen = next(r.app for r in app.routes if getattr(r, "name", None) == "screen")
    monkeypatch.setattr(screen, "all_directories", [os.path.join(os.environ["SCREEN_DIR"], "gone")])
    r = api.get("/find-care")
    assert r.status_code == 503 and "npm run build" in r.json()["detail"]


def test_a_failure_says_only_that_it_happened(api, monkeypatch):
    from application.serving.main import app
    from application.serving.routers import find_care

    def broken():
        raise RuntimeError("IO Error: /app/data/serving/plans.parquet is unreadable")
    monkeypatch.setattr(find_care, "db", broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/plans")
    assert r.status_code == 500
    assert r.json() == {"detail": {"code": "internal_error", "message": "Something went wrong on our side."}}
    assert "parquet" not in r.text and "Traceback" not in r.text
