"""The invite gate: the app answers only invited testers, since the deployed
service is open to the internet (#17).

Each tester's link is any screen address with `?invite=<token>`, their own
token. The first visit checks it, keeps it in a cookie and sends the browser to
the same address without it, so the token leaves the address bar. From then on
every request needs the cookie, the screen's files and the API's alike, and
anything else is a 401: a page for a person, a `Problem` under /api.

INVITES lists who is invited, as comma-separated `label:sha256(token)` entries,
which `make invite NAME=<label>` makes (this module's main, below). The app
holds only the hashes, so reading the list
gets no one in, and removing an entry shuts that tester out at once, cookie and
all. The gate is on unless GATE=off, as the dev stack sets, so a deploy without
INVITES lets nobody in rather than everybody.
"""
import hashlib
import logging
import os
import re
import secrets
import sys
from urllib.parse import urlencode

from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .contract import Problem

COOKIE = "hh_invite"
PARAM = "invite"
_LABEL = r"[a-z0-9-]+"
_KEEP_S = 365 * 24 * 3600
# uvicorn's own logger, so a line reaches the server's log without logging setup.
log = logging.getLogger("uvicorn.error")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_invites(spec: str) -> dict:
    """INVITES as {sha256 of a token: its label}. An entry that isn't
    `label:sha256` stops the app, rather than shutting someone out quietly."""
    invites = {}
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        label, _, hashed = entry.partition(":")
        if not re.fullmatch(_LABEL, label) or not re.fullmatch(r"[0-9a-f]{64}", hashed):
            raise ValueError(f"INVITES has an entry that isn't label:sha256-of-the-token, labelled {label!r}")
        invites[hashed] = label
    return invites


def new_invite(label: str) -> tuple:
    """A new token for a tester's link, and the INVITES entry that admits it."""
    if not re.fullmatch(_LABEL, label):
        raise ValueError(f"a label is lowercase letters, digits and dashes, such as rosa or tester-2, not {label!r}")
    token = secrets.token_urlsafe(24)
    return token, f"{label}:{digest(token)}"


ON = os.getenv("GATE", "on") != "off"
INVITES = parse_invites(os.getenv("INVITES", ""))

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Honest Healthcare</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #fff; color: #0f172b;
         font: 15px/1.5 Inter, system-ui, Avenir, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }
  main { box-sizing: border-box; width: 100%; max-width: 29rem; padding: 0 1.5rem; }
  .brand { margin: 0 0 1.5rem; color: #0f172b; font-weight: 700; letter-spacing: -0.01em; }
  h1 { margin: 0 0 0.5rem; font-size: 1.375rem; line-height: 1.3; letter-spacing: -0.01em; }
  p { margin: 0; color: #45556c; }
</style>
</head>
<body>
<main>
  <p class="brand">Honest Healthcare</p>
  <h1>This app is open to invited testers</h1>
  <p>Open the link from your invite on this device. If it doesn’t work, ask whoever sent it for a new one.</p>
</main>
</body>
</html>
"""


def _refused(path: str):
    if path == "/api" or path.startswith("/api/"):
        response = JSONResponse(status_code=401, content={"detail": Problem(
            code="no_invite", message="This app is open to invited testers, and this request has no invite.").model_dump()})
    else:
        response = HTMLResponse(_PAGE, status_code=401)
    response.headers["Cache-Control"] = "no-store"
    return response


class Gate:
    """ASGI middleware that lets a request through only with an invite."""

    def __init__(self, app, invites: dict, on: bool = True):
        self.app, self.invites, self.on = app, invites, on
        if on and not invites:
            log.warning("the invite gate is on and INVITES is empty, so nobody can get in")

    def _who(self, token):
        return self.invites.get(digest(token)) if token else None

    async def __call__(self, scope, receive, send):
        if not self.on or scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        invited = self._who(conn.cookies.get(COOKIE))
        if scope["type"] == "http" and PARAM in conn.query_params:
            response = self._open(conn, invited)
        elif invited:
            return await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        else:
            response = _refused(conn.url.path)
        await response(scope, receive, send)

    def _open(self, conn, invited):
        """An invite link: keep a good token in the cookie and send the browser
        on without it. A bad one is refused, unless the cookie already admits
        the browser, as when an invited tester opens an old link."""
        token = conn.query_params[PARAM]
        who = self._who(token)
        if who is None and not invited:
            return _refused(conn.url.path)
        rest = urlencode([(k, v) for k, v in conn.query_params.multi_items() if k != PARAM])
        response = RedirectResponse(conn.url.path + (f"?{rest}" if rest else ""), status_code=303)
        response.headers["Cache-Control"] = "no-store"
        if who is not None:
            response.set_cookie(COOKIE, token, max_age=_KEEP_S, httponly=True, secure=True, samesite="lax")
            log.info("invite %r opened the app", who)
        return response


def main(args):
    """`python3 -m application.serving.gate <label>`: a new invite, as the
    INVITES entry and the link to send, then what to do with them. The token is
    kept nowhere else, so a lost link means a new invite."""
    if len(args) != 1:
        sys.exit("usage: python3 -m application.serving.gate <label>")
    try:
        token, entry = new_invite(args[0])
    except ValueError as e:
        sys.exit(str(e))
    print(f"INVITES entry: {entry}")
    print(f"Link: <the app's address>/find-care?{PARAM}={token}")
    print(f"Add the entry to the app's INVITES, comma-separated, and send {args[0]} the link.")


if __name__ == "__main__":
    main(sys.argv[1:])
