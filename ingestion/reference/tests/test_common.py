"""The builders' download (ingestion/reference/_common.py fetch_to_cache),
against a local HTTP server that honors Range requests and keeps each
request's headers."""
import http.server
import os
import threading

import pytest

from ingestion.reference._common import fetch_to_cache

FILES = {"/plans.csv": b'"BusinessYear","PlanId"\n2026,45334GA0020002\n', "/page.html": b"<html>not a CSV</html>"}


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        _Handler.seen.append((self.path, dict(self.headers)))
        body = FILES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        start = int(self.headers["Range"].split("=")[1].rstrip("-")) if "Range" in self.headers else 0
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    _Handler.seen = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_a_download_is_cached_and_read_from_the_cache_after(server, tmp_path):
    cache = str(tmp_path / "downloads" / "plans.csv")
    assert fetch_to_cache(cache, [f"{server}/plans.csv"], headers={"User-Agent": "honest-healthcare"}) == cache
    assert open(cache, "rb").read() == FILES["/plans.csv"]
    assert _Handler.seen[0][1]["User-Agent"] == "honest-healthcare"
    fetch_to_cache(cache, [f"{server}/plans.csv"])
    assert len(_Handler.seen) == 1


def test_a_file_without_the_expected_header_is_dropped_for_the_next_url(server, tmp_path):
    cache = str(tmp_path / "plans.csv")
    fetch_to_cache(cache, [f"{server}/missing.csv", f"{server}/page.html", f"{server}/plans.csv"],
                   header_check=b'"BusinessYear"')
    assert open(cache, "rb").read() == FILES["/plans.csv"]
    assert not os.path.exists(f"{cache}.part")


def test_a_partial_download_resumes_where_it_stopped(server, tmp_path):
    cache = str(tmp_path / "plans.csv")
    with open(f"{cache}.part", "wb") as f:
        f.write(FILES["/plans.csv"][:10])
    fetch_to_cache(cache, [f"{server}/plans.csv"], header_check=b'"BusinessYear"')
    assert _Handler.seen[0][1]["Range"] == "bytes=10-"
    assert open(cache, "rb").read() == FILES["/plans.csv"]


def test_no_url_that_answers_stops_the_builder(server, tmp_path):
    with pytest.raises(SystemExit, match="could not fetch source"):
        fetch_to_cache(str(tmp_path / "plans.csv"), [f"{server}/page.html"], header_check=b'"BusinessYear"')
