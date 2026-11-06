#!/usr/bin/env python3
"""Smoke-check a running application image: the invite gate turns away a
request without an invite, the screen loads, its assets are cached for good,
and the API answers a real search. It asks nothing of the data beyond a
complete build, so it checks CI's image on the test tables and a deployed one
on the real tables alike.

  python3 infra/smoke.py http://localhost:8000 --invite <token>

--invite takes the token from an invite's link (`make invite`). Without it the
gate isn't checked, for an app run with GATE=off, as the dev stack is. Waits
up to 30 seconds for the app to answer, then exits 1 at the first check that
fails. Standard library only, so it runs anywhere Python does.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ATLANTA = "30308"  # a ZIP code the find-care tables always hold
COOKIE = "hh_invite"  # application/serving/gate.py


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_STAY = urllib.request.build_opener(_Stay)


def fetch(base, path, cookie=None, follow=True, **params):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"Cookie": f"{COOKIE}={cookie}"} if cookie else {})
    try:
        with (urllib.request.urlopen if follow else _STAY.open)(request, timeout=30) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def check(ok, what, detail=""):
    print(("ok    " if ok else "FAIL  ") + what + (f": {detail}" if detail and not ok else ""))
    if not ok:
        sys.exit(1)


def wait(base):
    for _ in range(30):
        try:
            return fetch(base, "/api/health")
        except OSError:
            time.sleep(1)
    check(False, f"the app answers at {base}", "nothing answered in 30 seconds")


def gate(base, invite):
    status, _, body = fetch(base, "/api/plans")
    check(status == 401 and json.loads(body)["detail"]["code"] == "no_invite", "the API turns away a request without an invite",
          f"{status} {body[:200]}")
    status, _, page = fetch(base, "/find-care")
    check(status == 401 and 'id="root"' not in page, "the screen turns away a request without an invite", str(status))
    status, headers, _ = fetch(base, "/find-care", follow=False, invite=invite, zip=ATLANTA)
    cookie = headers.get("set-cookie") or ""
    check(status == 303 and headers["location"] == f"/find-care?zip={ATLANTA}" and cookie.startswith(f"{COOKIE}={invite};")
          and "Secure" in cookie and "HttpOnly" in cookie,
          "the invite link keeps its token in a cookie and leaves the address without it", f"{status} {headers}")


def main(base, invite):
    base = base.rstrip("/")
    wait(base)
    if invite:
        gate(base, invite)
    else:
        print("skip  the invite gate: no --invite, so the app should run with GATE=off")

    def get(path, **params):
        return fetch(base, path, cookie=invite, **params)

    status, _, body = get("/api/health")
    check(status == 200 and json.loads(body)["status"] == "ok", "/api/health says the build is complete", body[:200])
    plans = json.loads(get("/api/plans")[2])
    services = json.loads(get("/api/services")[2])
    check(bool(plans) and bool(services), "/api/plans and /api/services list something")
    status, _, body = get("/api/find-care", service_id=services[0]["service_id"], plan_id=plans[0]["plan_id"], zip=ATLANTA)
    check(status == 200 and "results" in json.loads(body), "/api/find-care answers a search", body[:200])
    status, headers, _ = get("/api/nope")
    check(status == 404 and headers["content-type"] == "application/json", "an unknown /api path is a JSON 404")

    status, headers, index = get("/")
    check(status == 200 and '<div id="root">' in index and headers["cache-control"] == "no-cache",
          "/ is the screen, checked on every load", f"{status} {headers['cache-control']}")
    check(get("/find-care")[2] == index, "a screen address such as /find-care gets the screen")
    script = re.search(r'src="(/assets/[^"]+\.js)"', index)
    check(script is not None, "the screen names its script")
    status, headers, _ = get(script.group(1))
    check(status == 200 and "immutable" in headers["cache-control"], f"{script.group(1)} is cached for good")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--invite", help="the token from an invite's link")
    args = ap.parse_args()
    main(args.url, args.invite)
