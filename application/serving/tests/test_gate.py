"""The invite gate (application/serving/gate.py): nothing the app holds
answers without an invite, an invite link becomes a cookie, and removing an
invite shuts its tester out.
"""
import os
import subprocess
import sys

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route
from starlette.websockets import WebSocketDisconnect

from application.serving import gate

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
INVITE = "test-invite-token"  # conftest.py invites it
SEARCH = "plan=45334GA0020002&zip=30308"
# Paths the screen's mount answers, built or not, and paths no route answers.
SCREEN_PATHS = ("/", "/find-care", f"/find-care?{SEARCH}", "/assets/index-abc123.js", "/assets/gone.js",
                "/vite.svg", "/anything", "/api", "/api/nope")


def _routes(routes, prefix=""):
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:  # include_router keeps the router whole, with its prefix
            yield from _routes(inner.routes, prefix + r.include_context.prefix)
        else:
            yield prefix, r


def every_path(app):
    """A request path for each thing the app serves, so a new route is checked
    without this test changing. A kind of route it doesn't know fails it, and
    so does a route outside /api, whose path belongs to the screen."""
    paths = list(SCREEN_PATHS)
    for prefix, r in _routes(app.routes):
        if isinstance(r, (APIRoute, Route)):
            path = prefix + r.path
            assert path.startswith("/api/"), f"{path} is outside /api"
            assert "{" not in path, f"{path} takes a parameter: give every_path an example of it"
            paths.append(path)
        elif not (isinstance(r, Mount) and r.name == "screen"):
            raise AssertionError(f"every_path doesn't know how to reach {r!r}")
    return paths


def test_nothing_answers_without_an_invite(app, stranger):
    paths = every_path(app)
    assert {"/api/health", "/api/plans", "/api/services", "/api/find-care", "/api/docs", "/api/openapi.json"} <= set(paths)
    for path in paths:
        for method in ("GET", "HEAD", "POST"):
            r = stranger.request(method, path)
            assert r.status_code == 401, (method, path)
            assert r.headers["cache-control"] == "no-store", (method, path)
            assert "set-cookie" not in r.headers, (method, path)


def test_a_person_without_an_invite_is_told_how_to_get_in(stranger):
    r = stranger.get("/find-care")
    assert r.status_code == 401 and r.headers["content-type"].startswith("text/html")
    assert "open to invited testers" in r.text and 'id="root"' not in r.text


def test_the_api_without_an_invite_answers_a_problem(stranger):
    r = stranger.get("/api/find-care", params={"service_id": "mri-lower-back", "plan_id": "45334GA0020002", "zip": "30308"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "no_invite"


def test_an_invite_link_keeps_the_token_and_drops_it_from_the_address(stranger):
    r = stranger.get(f"/find-care?plan=45334GA0020002&invite={INVITE}&zip=30308")
    assert r.status_code == 303 and r.headers["location"] == f"/find-care?{SEARCH}"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{gate.COOKIE}={INVITE};")
    assert {"HttpOnly", "Secure", "SameSite=lax", "Max-Age=31536000"} <= {p.strip() for p in cookie.split(";")}
    assert r.headers["cache-control"] == "no-store"
    assert stranger.get(r.headers["location"]).status_code == 200
    assert stranger.get("/api/plans").status_code == 200


def test_a_wrong_invite_is_refused_and_keeps_nothing(stranger):
    r = stranger.get(f"/find-care?invite=not-{INVITE}")
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert stranger.get("/api/plans").status_code == 401


def test_an_invited_browser_opening_an_old_link_still_gets_in(stranger):
    stranger.cookies.set(gate.COOKIE, INVITE)
    r = stranger.get(f"/find-care?invite=revoked-token&{SEARCH}")
    assert r.status_code == 303 and r.headers["location"] == f"/find-care?{SEARCH}"
    assert "set-cookie" not in r.headers


def test_removing_an_invite_shuts_its_tester_out(api, monkeypatch):
    assert api.get("/api/plans").status_code == 200
    monkeypatch.delitem(gate.INVITES, gate.digest(INVITE))
    assert api.get("/api/plans").status_code == 401
    assert api.get("/").status_code == 401


def test_invites_are_labels_and_hashes():
    hashed = gate.digest(INVITE)
    assert gate.parse_invites(f" rosa:{hashed} ,, dana-2:{'0' * 64}") == {hashed: "rosa", "0" * 64: "dana-2"}
    assert gate.parse_invites("") == {}
    for spec in (f"rosa:{INVITE}", hashed, f"Rosa:{hashed}", f"rosa:{hashed.upper()}"):
        with pytest.raises(ValueError, match="label:sha256"):
            gate.parse_invites(spec)


async def _hello(scope, receive, send):
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"hello"})


def test_a_new_invite_admits_its_link():
    token, entry = gate.new_invite("rosa")
    assert gate.parse_invites(entry) == {gate.digest(token): "rosa"}
    assert gate.new_invite("rosa")[0] != token and len(token) >= 32
    with pytest.raises(ValueError, match="lowercase"):
        gate.new_invite("Rosa Lee")


def test_make_invite_prints_the_entry_and_the_link():
    r = subprocess.run([sys.executable, "-m", "application.serving.gate", "rosa"], cwd=REPO, capture_output=True,
                       text=True, env={**os.environ, "INVITES": ""})
    assert r.returncode == 0, r.stderr
    entry, link, _ = r.stdout.splitlines()
    token = link.split("?invite=")[1]
    assert link.startswith("Link: ") and gate.parse_invites(entry.removeprefix("INVITES entry: ")) == {
        gate.digest(token): "rosa"}


# Clients of _hello aren't opened with `with`, which would send it a lifespan it doesn't speak.

def test_the_gate_off_lets_everyone_in():
    assert TestClient(gate.Gate(_hello, {}, on=False)).get("/api/plans").text == "hello"


def test_a_gate_with_no_invites_lets_nobody_in():
    client = TestClient(gate.Gate(_hello, {}), base_url="https://testserver")
    client.cookies.set(gate.COOKIE, INVITE)
    assert client.get("/").status_code == 401
    assert client.get(f"/?invite={INVITE}").status_code == 401


def test_a_websocket_without_an_invite_is_closed():
    client = TestClient(gate.Gate(_hello, {gate.digest(INVITE): "tester"}))
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws"):
            pass
    assert closed.value.code == 1008
    client.cookies.set(gate.COOKIE, INVITE)
    with client.websocket_connect("/ws"):
        pass
