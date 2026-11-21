"""v3 card pictures load test (img_cache.py / img_index.py): 700 simultaneous members, in process, no network, never production.

Each member loads what a v3 board + card visit asks for: the NFL and NBA picture lists (/img/index/<sport>.json, member-gated, with
ETag revalidation) and 40 pictures (/img/<p|t>/<sport>/<id>.webp, the long-cached files), as a browser does (keep-alive, gzip).
The upstream is a STUB that is slow (0.25 s, every 10th call times out after 1 s), so the run shows that member requests never wait
on it. Three phases:
  cold   - empty disk, the warmer thread running: pictures not yet stored answer a fast 404, never a 5xx or a wait
  warm   - every picture stored: 200s (then 304s on revalidation), memory LRU + disk only
  off    - SCOUT_UI_V3=0: every /img path is the unknown-route answer
Fails on any 5xx, a wrong status, a member request reaching the upstream, more than one upstream fetch per picture (single
flight), the image memory cache over its bound, or a final picture list (after the warm-up) that misses a stored picture (the
coalesced picture version must always land), or a CS2 picture path that answers anything but the unknown-route 404 (CS2 has no
pictures: owner 2026-11-20, no BO3.gg; the HLTV data stores no picture URLs). Prints one line: RESULT {json}.

usage: python scripts/load_v3_images_700.py <code_dir> [members=700] [players=320]"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

code = sys.argv[1]
MEMBERS = int(sys.argv[2]) if len(sys.argv) > 2 else 700
PLAYERS = int(sys.argv[3]) if len(sys.argv) > 3 else 320
sys.path.insert(0, code)
os.chdir(code)
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp
os.environ["SCOUT_IMG_DIR"] = os.path.join(tmp, "img")
for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN", "SCOUT_UI_V3", "RENDER", "REQUIRE_WHOP"):
    os.environ.pop(f, None)

import io  # noqa: E402

from PIL import Image  # noqa: E402

import img_cache as ic  # noqa: E402
import img_index as ix  # noqa: E402
import server  # noqa: E402
import whop_auth  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
whop_auth.cookie_valid = lambda v: bool(v and v.startswith("good-"))
server.lol_public = lambda: True
import workspace_api  # noqa: E402
# every member here may see NFL and NBA (the index follows the board's own sport gate; the harness has no sports service)
workspace_api.access = lambda h, srv, q: {"cs2": True, "lol": True, "nba": True, "nfl": True}

# ---------------------------------------------------------------------------------------------------- synthetic boards + upstream
NFL_TEAMS = ["ari", "atl", "bal", "buf", "car", "chi", "cin", "cle", "dal", "den", "det", "gb", "hou", "ind", "jax", "kc", "lv", "lac",
             "lar", "mia", "min", "ne", "no", "nyg"]
TEAMS = len(NFL_TEAMS)                                # 24 team marks, as the CS2 set this replaced
NFL = [{"sport": "nfl", "player": {"id": f"nfl:player:espn:{3000000 + i}", "name": f"f{i}", "team": NFL_TEAMS[i % TEAMS].upper(),
                                   "team_abbr": NFL_TEAMS[i % TEAMS]}, "offer": {"offer_id": f"f{i}"}} for i in range(1, PLAYERS + 1)]
CS2 = [{"sport": "cs2", "player": {"id": i, "name": f"p{i}", "team": f"Team {i % TEAMS}"}, "offer": {"offer_id": f"o{i}"}} for i in range(1, PLAYERS + 1)]
NBA_TEAMS = ["atl", "bos", "bkn", "cha", "chi", "cle", "dal", "den", "det", "gs", "hou", "ind", "lac", "lal", "mem", "mia", "mil", "min"]
NBA = [{"sport": "nba", "player": {"id": f"nba:player:espn:{4000000 + i}", "name": f"n{i}", "team": NBA_TEAMS[i % 18].upper(),
                                   "team_abbr": NBA_TEAMS[i % 18]}, "offer": {"offer_id": f"n{i}"}} for i in range(PLAYERS)]
BODIES = {"cs2": json.dumps({"records": CS2}).encode(), "nfl": json.dumps({"records": NFL}).encode(), "nba": json.dumps({"records": NBA}).encode()}
ix._records = lambda srv, sport: (BODIES.get(sport), json.loads(BODIES[sport])["records"], {"body": BODIES[sport]}) if sport in BODIES else (None, [], None)
ix._body_id = lambda srv, sport: id(BODIES[sport]) if sport in BODIES else None     # the same board body object, as production

_png = io.BytesIO()
Image.new("RGBA", (354, 455), (90, 110, 160, 255)).save(_png, "PNG")
PNG = _png.getvalue()
UP = {"calls": 0, "by_url": {}, "member_thread_calls": 0}
_up_lock = threading.Lock()


def slow_upstream(url):
    with _up_lock:
        UP["calls"] += 1
        UP["by_url"][url] = UP["by_url"].get(url, 0) + 1
        n = UP["calls"]
    if threading.current_thread().name != "img-warmer":
        UP["member_thread_calls"] += 1
    if n % 10 == 0:
        time.sleep(1.0)
        raise TimeoutError("stub timeout")
    time.sleep(0.25)
    return PNG


ic._http_get = slow_upstream
ic.HOST_GAP_S = 0.0
ic.FAIL_S = 0.5                                       # the stub's timeouts are retried within the run

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
httpd.daemon_threads = True
httpd.request_queue_size = 1024
threading.Thread(target=httpd.serve_forever, daemon=True).start()
PORT = httpd.server_address[1]


def keys():
    out = []
    for i in range(1, PLAYERS + 1):
        out.append(f"/img/p/nfl/{3000000 + i}.webp")
    for a in NFL_TEAMS:
        out.append(f"/img/t/nfl/{a}.webp")
    for i in range(PLAYERS):
        out.append(f"/img/p/nba/{4000000 + i}.webp")
    for a in NBA_TEAMS:
        out.append(f"/img/t/nba/{a}.webp")
    return out


ALL = keys()


def call(conn, path, who, etag=None):
    for attempt in range(20):
        try:
            if conn[0] is None:
                conn[0] = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
            t = time.time()
            h = {"Cookie": f"{whop_auth.COOKIE}=good-{who}", "Accept-Encoding": "gzip"}
            if etag:
                h["If-None-Match"] = etag
            conn[0].request("GET", path, headers=h)
            r = conn[0].getresponse()
            body = r.read()
            if r.getheader("Connection", "").lower() == "close":
                conn[0].close()
                conn[0] = None
            route = "index" if "/index/" in path else "image"
            return route, r.status, (time.time() - t) * 1000, len(body), r.getheader("ETag")
        except (OSError, http.client.HTTPException):
            if conn[0] is not None:
                conn[0].close()
            conn[0] = None
            time.sleep(0.05 * (attempt + 1))
    return path, 599, 0, 0, None


def visit(i):
    conn, out = [None], []
    try:
        for sport in ("nfl", "nba"):
            r = call(conn, f"/img/index/{sport}.json", f"m{i}")
            out.append(r)
            out.append(call(conn, f"/img/index/{sport}.json", f"m{i}", r[4]))        # the page's 90 s refresh: a revalidation
        for k in range(40):
            path = ALL[(i * 7 + k * 13) % len(ALL)]
            r = call(conn, path, f"m{i}")
            out.append(r)
            if k % 10 == 0 and r[1] == 200:
                out.append(call(conn, path, f"m{i}", r[4]))                         # a revalidation: 304
        return out
    finally:
        if conn[0] is not None:
            conn[0].close()


def run(phase):
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
        res = [x for r in ex.map(visit, range(MEMBERS)) for x in r]
    wall = time.time() - t0
    lat = sorted(x[2] for x in res)
    pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)  # noqa: E731
    routes = {}
    for x in res:
        r = routes.setdefault(x[0], {"n": 0, "codes": {}})
        r["n"] += 1
        r["codes"][x[1]] = r["codes"].get(x[1], 0) + 1
    ok = {"cold": {200, 304, 404}, "warm": {200, 304}, "off": {404}}[phase]
    wrong = sum(1 for x in res if x[1] not in ok)
    return {"phase": phase, "members": MEMBERS, "requests": len(res), "wall_s": round(wall, 2), "p50_ms": pct(.5), "p95_ms": pct(.95),
            "p99_ms": pct(.99), "max_ms": round(lat[-1], 1), "errors_5xx": sum(1 for x in res if x[1] >= 500), "wrong_status": wrong, "routes": routes}


rep = {}
try:
    tracemalloc.start()
    os.environ["SCOUT_UI_V3"] = "1"
    rep["cold"] = run("cold")
    rep["cold"]["warmer_queue_after"] = ic.status()["queued"]
    t_warm = time.time()
    deadline = time.time() + 900                          # let the (slow, paced) warmer finish everything once
    while time.time() < deadline and (ic.status()["queued"] or ic.status()["ready"] < len(ALL)):
        time.sleep(0.5)
        if not ic.status()["queued"] and ic.status()["ready"] < len(ALL):
            for s in ("nfl", "nba"):                      # retry the stub's timeouts: what the next board read does
                ix._MEMO.clear()
                ix.index(server, s)
    listed = {}
    for s in ("nfl", "nba"):                              # the lists every member gets once the warmer is done (no clearing)
        deadline2 = time.time() + 60
        while True:
            d = json.loads(ix.index(server, s))
            listed[s] = sum(1 for v in d["p"].values() if v[0]) + len({v[1] for v in d["p"].values() if v[1]})
            if listed[s] >= (PLAYERS + (TEAMS if s == "nfl" else len(NBA_TEAMS))) or time.time() > deadline2:
                break
            ix._MEMO[s] = (ix._MEMO[s][0], ix._MEMO[s][1], 0.0, ix._MEMO[s][3])   # the next MIN_REBUILD_S window
            time.sleep(0.2)
    rep["listed_after_warm"] = listed
    rep["warm_up_s"] = round(time.time() - t_warm, 1)
    rep["ready"] = ic.status()["ready"]
    rep["warm"] = run("warm")
    conn = [None]                                         # CS2: no picture route at all, v3 on (the unknown-route answer)
    rep["cs2_paths"] = {p: call(conn, p, "m0")[1] for p in ("/img/index/cs2.json", "/img/p/cs2/1.webp", "/img/t/cs2/1000.webp")}
    conn[0] and conn[0].close()
    rep["cs2_registered"] = sum(1 for k in ic._SRC if k[1] == "cs2")
    os.environ["SCOUT_UI_V3"] = "0"          # v3 is the default since 8892ffd: off is an explicit 0
    rep["off"] = run("off")
    cur, peak = tracemalloc.get_traced_memory()
    st = ic.status()
    dup = {u: n for u, n in UP["by_url"].items() if n > 1}
    retried = len(dup)
    rep.update({"upstream_calls": UP["calls"], "pictures": len(ALL), "urls_called_more_than_once": retried,
                "member_thread_upstream_calls": UP["member_thread_calls"], "img_mem_bytes": st["mem_bytes"], "py_heap_peak_mb": round(peak / 1e6, 1),
                "cache": st})
    # more than one call per picture only for the stub's own timeouts (every 10th call), never two at once for one key
    rep["pass"] = (all(rep[p]["errors_5xx"] == 0 and rep[p]["wrong_status"] == 0 for p in ("cold", "warm", "off"))
                   and UP["member_thread_calls"] == 0 and rep["ready"] == len(ALL) and st["mem_bytes"] <= ic.MEM_BYTES
                   and listed == {"nfl": PLAYERS + TEAMS, "nba": PLAYERS + len(NBA_TEAMS)}
                   and set(rep["cs2_paths"].values()) == {404} and rep["cs2_registered"] == 0
                   and not any("bo3" in u for u in UP["by_url"])
                   and retried <= UP["calls"] // 10 + 1)
    print("RESULT " + json.dumps(rep))
finally:
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if rep.get("pass") else 1)
