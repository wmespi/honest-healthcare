#!/usr/bin/env python3
"""Smoke-check a running application image: the screen loads, its assets are
cached for good, and the API answers a real search. It asks nothing of the
data beyond a complete build, so it checks CI's image on the test tables and a
deployed one on the real tables alike.

  python3 infra/smoke.py http://localhost:8000

Waits up to 30 seconds for the app to answer, then exits 1 at the first check
that fails. Standard library only, so it runs anywhere Python does.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ATLANTA = "30308"  # a ZIP code the find-care tables always hold


def fetch(base, path, **params):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def check(ok, what, detail=""):
    print(("ok    " if ok else "FAIL  ") + what + (f": {detail}" if detail and not ok else ""))
    if not ok:
        sys.exit(1)


def main(base):
    base = base.rstrip("/")
    for _ in range(30):
        try:
            status, _, body = fetch(base, "/api/health")
            break
        except OSError:
            time.sleep(1)
    else:
        check(False, f"the app answers at {base}", "nothing answered in 30 seconds")
    check(status == 200 and json.loads(body)["status"] == "ok", "/api/health says the build is complete", body)

    plans = json.loads(fetch(base, "/api/plans")[2])
    services = json.loads(fetch(base, "/api/services")[2])
    check(bool(plans) and bool(services), "/api/plans and /api/services list something")
    status, _, body = fetch(base, "/api/find-care", service_id=services[0]["service_id"],
                            plan_id=plans[0]["plan_id"], zip=ATLANTA)
    check(status == 200 and "results" in json.loads(body), "/api/find-care answers a search", body[:200])
    status, headers, _ = fetch(base, "/api/nope")
    check(status == 404 and headers["content-type"] == "application/json", "an unknown /api path is a JSON 404")

    status, headers, index = fetch(base, "/")
    check(status == 200 and '<div id="root">' in index and headers["cache-control"] == "no-cache",
          "/ is the screen, checked on every load", f"{status} {headers['cache-control']}")
    check(fetch(base, "/find-care")[2] == index, "a screen address such as /find-care gets the screen")
    script = re.search(r'src="(/assets/[^"]+\.js)"', index)
    check(script is not None, "the screen names its script")
    status, headers, _ = fetch(base, script.group(1))
    check(status == 200 and "immutable" in headers["cache-control"], f"{script.group(1)} is cached for good")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
