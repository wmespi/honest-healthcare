#!/usr/bin/env python3
"""Smoke-check a running application image: the screen loads, its assets are
cached for good, and the API answers a real search. It asks nothing of the
data beyond a complete build, so it checks CI's image on the test tables and a
deployed one on the real tables alike.

  python3 infra/smoke.py http://localhost:8000
  python3 infra/smoke.py https://<the service>.run.app --token <an IAP token>

With --token, the app sits behind Google sign-in (IAP, infra/deploy.md): a
request without the token must be turned away, and every other check signs in
with it. make deploy makes the token. Waits up to 30 seconds for the app to
answer, then exits 1 at the first check that fails. Standard library only, so
it runs anywhere Python does.
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


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_STAY = urllib.request.build_opener(_Stay)


def fetch(base, path, token=None, follow=True, **params):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with (urllib.request.urlopen if follow else _STAY.open)(request, timeout=30) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def check(ok, what, detail=""):
    print(("ok    " if ok else "FAIL  ") + what + (f": {detail}" if detail and not ok else ""))
    if not ok:
        sys.exit(1)


def main(base, token):
    base = base.rstrip("/")

    def get(path, **params):
        return fetch(base, path, token=token, **params)

    for _ in range(30):
        try:
            status, _, body = get("/api/health")
            break
        except OSError:
            time.sleep(1)
    else:
        check(False, f"the app answers at {base}", "nothing answered in 30 seconds")
    if token:
        stranger, _, page = fetch(base, "/", follow=False)
        check(stranger in (302, 401, 403) and '<div id="root">' not in page,
              "a request without signing in is turned away", f"{stranger} {page[:200]}")
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
    ap.add_argument("--token", help="an identity token IAP admits, for an app behind Google sign-in")
    args = ap.parse_args()
    main(args.url, args.token)
