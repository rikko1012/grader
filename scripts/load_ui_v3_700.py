"""v3 interface load test (web/UI_V3.md): 700 simultaneous members, in process, no network, never production. Each member loads
the CS2 page and LoL page, the v3 stylesheet + script bundle and one map scene, as a browser does (Accept-Encoding: gzip), with
SCOUT_UI_V3=1; then the same visit with the flag off (pages only; the v3 routes must answer 404). Fails loudly on a 5xx, a wrong
status, or more than one v3 page / bundle build per file version (single flight).

usage: python scripts/load_ui_v3_700.py <code_dir> [members=700]
Prints one line: RESULT {json}."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

code = sys.argv[1]
MEMBERS = int(sys.argv[2]) if len(sys.argv) > 2 else 700
sys.path.insert(0, code)
os.chdir(code)
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp
for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN", "SCOUT_UI_V3", "RENDER", "REQUIRE_WHOP"):
    os.environ.pop(f, None)

import server, ui_v3, whop_auth  # noqa: E402,E401

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
whop_auth.cookie_valid = lambda v: bool(v and v.startswith("good-"))
server.lol_public = lambda: True

BUILDS = {"page": 0}
_build = ui_v3._build


def counted_build(body, name):
    BUILDS["page"] += 1
    return _build(body, name)


ui_v3._build = counted_build


httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
httpd.daemon_threads = True
httpd.request_queue_size = 1024
threading.Thread(target=httpd.serve_forever, daemon=True).start()
PORT = httpd.server_address[1]
MAPS = sorted(ui_v3.ART)


def call(conn, path, who):
    """One request on the member's kept-alive connection (as a browser reuses one); reconnects on a refused / dropped socket."""
    for attempt in range(20):
        try:
            if conn[0] is None:
                conn[0] = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
            t = time.time()
            conn[0].request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-{who}", "Accept-Encoding": "gzip"})
            r = conn[0].getresponse()
            body = r.read()
            if r.getheader("Connection", "").lower() == "close":
                conn[0].close()
                conn[0] = None
            return path.split("?")[0] if not path.startswith("/v3/art/") else "/v3/art/*", r.status, (time.time() - t) * 1000, len(body)
        except (OSError, http.client.HTTPException):
            if conn[0] is not None:
                conn[0].close()
            conn[0] = None
            time.sleep(0.05 * (attempt + 1))
    return path, 599, 0, 0


def visit(i, on):
    paths = ["/", "/lol", "/v3/member.css", "/v3/member.js", f"/v3/art/{MAPS[i % len(MAPS)]}.svg"]
    conn = [None]
    try:
        return [call(conn, p, f"m{i}") for p in paths]
    finally:
        if conn[0] is not None:
            conn[0].close()


def run(on):
    if on:
        os.environ["SCOUT_UI_V3"] = "1"
    else:
        os.environ["SCOUT_UI_V3"] = "0"          # v3 is the default since 8892ffd: off is an explicit 0
    ui_v3._PAGE.clear()
    ui_v3._BUNDLE.clear()
    BUILDS["page"] = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
        res = [x for r in ex.map(lambda i: visit(i, on), range(MEMBERS)) for x in r]
    wall = time.time() - t0
    lat = sorted(x[2] for x in res)
    pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)  # noqa: E731
    routes = {}
    for x in res:
        r = routes.setdefault(x[0], {"n": 0, "codes": {}, "kb": 0.0})
        r["n"] += 1
        r["codes"][x[1]] = r["codes"].get(x[1], 0) + 1
        r["kb"] += x[3] / 1e3
    for r in routes.values():
        r["avg_kb"] = round(r.pop("kb") / r["n"], 1)
    want = (lambda p: 200) if on else (lambda p: 404 if p.startswith("/v3/") else 200)
    wrong = sum(1 for x in res if x[1] != want(x[0]))
    return {"flag": "on" if on else "off", "members": MEMBERS, "requests": len(res), "wall_s": round(wall, 2), "p50_ms": pct(.5), "p95_ms": pct(.95),
            "p99_ms": pct(.99), "errors_5xx": sum(1 for x in res if x[1] >= 500), "wrong_status": wrong, "v3_page_builds": BUILDS["page"],
            "routes": routes}


try:
    rep = {"on": run(True), "off": run(False)}
    rep["pass"] = (rep["on"]["errors_5xx"] == rep["off"]["errors_5xx"] == 0 and rep["on"]["wrong_status"] == rep["off"]["wrong_status"] == 0
                   and rep["on"]["v3_page_builds"] <= 2 and rep["off"]["v3_page_builds"] == 0)
    print("RESULT " + json.dumps(rep))
finally:
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if rep["pass"] else 1)
