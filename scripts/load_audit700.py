"""Audit load test (2026-10-26): 700 simultaneous members against EVERY hot member endpoint of the esports server, in
process, fixture upstreams only (no network, never production). Extends scripts/load_sports700.py ("off" mode) with the
card (/api/grade), match compare, results, line check, veto and search routes, and records response bytes and
compression, so per-request heavy work and uncompressed bodies show up.

usage: python scripts/load_audit700.py <code_dir> [members=700]
Stubs (load harness only, never in the server): cs2_scout.grade sleeps 80 ms and returns a ~60 KB card (a real card
with ladder / recent maps / comparison is that size); match_compare sleeps 50 ms and returns ~25 KB; results_payload
returns ~40 KB. Every member sends Accept-Encoding: gzip like a browser.
Prints one line: RESULT {json}."""
import gzip
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

code = sys.argv[1]
MEMBERS = int(sys.argv[2]) if len(sys.argv) > 2 else 700
sys.path.insert(0, code)
os.chdir(code)
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp
for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN"):
    os.environ.pop(f, None)

import hltv_store  # noqa: E402
hltv_store.DB_PATH = Path(tmp) / "hltv_store.db"
import cs2_scout, kalshi, moneyline, picklog, polymarket_ml, prizepicks, server, slip_builder, underdog, whop_auth  # noqa: E402,E401
from tests.test_api_moneyline_slips import Boards  # noqa: E402
from tests.test_moneyline import kalshi_raw, poly_raw  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
slip_builder.DB_PATH = Path(tmp) / "slip_studio.db"
b = Boards(n_matches=30)
server.board_payload = b.payload
underdog.board = b.feed
prizepicks.board = lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": [], "error": None}
# Opt-in fixtures exercise the real easier-line adapter and board cache.
LINE_ADVANTAGE_LOAD = os.environ.get("LINE_ADVANTAGE_LOAD") == "1"
ADVICE_RESPONSES = []
if LINE_ADVANTAGE_LOAD:
    feed_at = time.time() - 1  # fixed cached observation, not a new read on every request
    underdog.board = lambda **kw: dict(b.feed(), at=feed_at)
    pp_rows = [dict(r, id="pp:" + r["id"], book="prizepicks", line=29.5, graded_line=29.5,
                    odds_type="standard", over_available=True, under_available=True) for r in b.rows]
    server.board_payload = lambda book="underdog": dict(book=book, updated=time.time(), error=None,
        rows=b.rows if book == "underdog" else pp_rows, version="TESTMODEL")
    prizepicks.board = lambda **kw: dict(state="ok", at=feed_at, ingested_at=feed_at, rows=pp_rows, error=None)
underdog.line_for = lambda *a, **k: None
picklog.log_async = lambda *a, **k: None
server.mark_warm = lambda *a: None
srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
        "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
for s in srcs.values():
    s.refresh_once()
moneyline.SOURCES = srcs

GRADE_CALLS = [0]
START = b.rows[0]["start"]


def fake_grade(name, line, prop="kills", lt="standard", **k):
    GRADE_CALLS[0] += 1
    time.sleep(0.08)
    maps = [{"date": f"2026-10-{1 + i % 25:02d}", "map": "Mirage", "kills": 18 + i % 9, "opp": f"Team {i}", "rating": 1.05,
             "adr": 80.1 + i % 7, "hs": 9, "event": "Fixture Cup " * 3} for i in range(120)]
    g = {"p_more": 0.41, "p_less": 0.59, "grade": "C", "grade_less": "B", "line": line, "line_type": lt, "lambda_final": line - 1,
         "units": 0.0, "units_less": 1.0, "prop": prop, "scope": "12", "flags": [], "projection_source": "perf-2",
         "factors": [{"name": f"factor {i}", "weight": 0.0, "value": i / 10, "note": "shown at weight 0 " * 4} for i in range(40)]}
    return {"grade": g, "player_block": {"player": {"nick": name, "slug": name, "id": 7}, "playing_for": "T", "maps": maps},
            "next_match": {"start": START, "opponent": "O"},
            "ladder": [{"line": line + d, "p": 0.5} for d in (-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2)]}


cs2_scout.grade = fake_grade
MC_CALLS = [0]


def fake_mc(*a, **k):
    MC_CALLS[0] += 1
    time.sleep(0.05)
    return {"match": {"team": a[2], "opponent": a[3]},
            "players": [{"nick": f"p{i}", "rows": [{"k": j, "v": "x" * 20} for j in range(30)]} for i in range(10)]}


server.match_compare = fake_mc
RES_CALLS = [0]


def fake_results():
    RES_CALLS[0] += 1
    time.sleep(0.05)
    return {"groups": [{"name": f"g{i}", "rows": [{"date": "2026-10-20", "w": 1, "l": 0, "note": "x" * 30} for _ in range(40)]}
                       for i in range(10)]}


server.results_payload = fake_results
import hltv  # noqa: E402
hltv.search_players = lambda q, n=8: [{"slug": "s0und", "nick": "s0und", "name": "A B", "country": "DK", "image": None}]
server.BO3 = None                                       # never BO3.gg from a load test (no network)

ThreadingHTTPServer.request_queue_size = 2048
ThreadingHTTPServer.daemon_threads = True
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
REFUSED = [0]


def call(path, member):
    t0 = time.time()
    for attempt in range(20):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-{member}", "Accept-Encoding": "gzip"})
            break
        except OSError:
            REFUSED[0] += 1
            c.close()
            time.sleep(0.25 * (attempt + 1))
    r = c.getresponse()
    body = r.read()
    enc = r.getheader("Content-Encoding") or ""
    if LINE_ADVANTAGE_LOAD and path.startswith("/api/line_check"):
        payload = json.loads(gzip.decompress(body) if enc == "gzip" else body)
        ADVICE_RESPONSES.append(payload.get("status") == "verified" and
            any(x.get("line") == 29.5 and x.get("book") == "prizepicks" for x in payload.get("line_advantages", [])))
    c.close()
    return r.status, time.time() - t0, len(body), enc


m0 = b.rows[0]
CARDS = [f"/api/grade?player=p{i}&line={28.5 + i % 5}&prop=kills&maps=12&book=underdog" for i in range(40)]
COMPARES = [f"/api/match_compare?team_slug=&opp_slug=&team=T{m}a&opponent=T{m}b&start={START}&maps=12&book=underdog"
            for m in range(15)]
LINE_QUERY = "&rid=r1&side=More&book=underdog&opponent=T0b" if LINE_ADVANTAGE_LOAD else ""
if os.environ.get("LINE_ADVANTAGE_CARD") == "1":
    LINE_QUERY = LINE_QUERY.replace("&rid=r1", "")
OTHER = ["/api/results", f"/api/line_check?player=p1&prop=kills&maps=12&line=30.5&team=T0a&start={quote(START, safe='')}{LINE_QUERY}",
         "/api/veto?match=hltv:900", "/auth/me", "/api/search?q=s0", "/api/board?book=prizepicks"]


def member(i):
    paths = ["/", "/api/board", CARDS[i % len(CARDS)], COMPARES[i % len(COMPARES)], "/api/moneylines", OTHER[i % len(OTHER)]]
    out = []
    for p in paths:
        st, dt, n, enc = call(p, f"u{i}")
        out.append((p.split("?")[0], st, dt, n, enc))
    return out


def rss():
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("a", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in
                        ("PeakWS", "WS", "q1", "q2", "q3", "q4", "pf", "ppf")]
        k32, ps = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
        return round(m.WS / 1e6, 1), round(m.PeakWS / 1e6, 1)
    except Exception:
        return None


for _ in range(10):
    call("/api/moneylines", "warm")
r0 = rss()
t0 = time.time()
with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
    res = [x for r in ex.map(member, range(MEMBERS)) for x in r]
wall = time.time() - t0
r1 = rss()


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1)


lat = [x[2] for x in res]
rep = {"members": MEMBERS, "requests": len(res), "wall_s": round(wall, 2), "connect_retries": REFUSED[0],
       "rss_before_after_peak_mb": [r0, r1], "errors_5xx": sum(1 for x in res if x[1] >= 500),
       "non_200": sum(1 for x in res if x[1] != 200), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
       "bytes_out_mb": round(sum(x[3] for x in res) / 1e6, 2),
       "computes": {"grade": GRADE_CALLS[0], "match_compare": MC_CALLS[0], "results": RES_CALLS[0]}, "routes": {}}
for p in sorted({x[0] for x in res}):
    rows = [x for x in res if x[0] == p]
    codes = {}
    for x in rows:
        codes[x[1]] = codes.get(x[1], 0) + 1
    rep["routes"][p] = {"n": len(rows), "codes": codes, "p50_ms": pct([x[2] for x in rows], .5),
                        "p99_ms": pct([x[2] for x in rows], .99), "avg_kb": round(sum(x[3] for x in rows) / len(rows) / 1e3, 1),
                        "gzip_share": round(sum(1 for x in rows if x[4] == "gzip") / len(rows), 2)}
rep["line_advantage"] = {"enabled": LINE_ADVANTAGE_LOAD, "responses": len(ADVICE_RESPONSES),
                         "correct": sum(ADVICE_RESPONSES)}
print("RESULT " + json.dumps(rep))
if rep["non_200"] or (LINE_ADVANTAGE_LOAD and (not ADVICE_RESPONSES or not all(ADVICE_RESPONSES))):
    raise SystemExit(1)
