"""700 simultaneous members against esports endpoints (+ sports endpoints when enabled), in-process server with fixture
upstreams. usage: python load_sports700.py <code_dir> <mode: off|on|failing> [sports_data_dir]
Budgets / stop conditions set BEFORE the run: 0 errors on esports routes; esports p99 must not exceed base p99 by > 25%."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

code, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, code)
os.chdir(code)
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp
for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN"):
    os.environ.pop(f, None)
if mode in ("on", "failing"):
    os.environ["SPORTS_API"] = "1"
    os.environ["SPORTS_UI"] = "1"
    os.environ["SPORTS_DATA_DIR"] = sys.argv[3]

import hltv_store  # noqa: E402
hltv_store.DB_PATH = __import__("pathlib").Path(tmp) / "hltv_store.db"
import kalshi, moneyline, polymarket_ml, server, slip_builder, underdog, whop_auth  # noqa: E402,E401
from tests.test_api_moneyline_slips import Boards  # noqa: E402
from tests.test_moneyline import kalshi_raw, poly_raw  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
slip_builder.DB_PATH = __import__("pathlib").Path(tmp) / "slip_studio.db"
b = Boards(n_matches=30)
server.board_payload = b.payload
underdog.board = b.feed
srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
        "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
for s in srcs.values():
    s.refresh_once()
moneyline.SOURCES = srcs
if mode == "failing":
    from sports import web as sports_web

    def boom(*a, **k):
        raise RuntimeError("sports outage drill")
    sports_web.handle = boom
ThreadingHTTPServer.request_queue_size = 2048          # before bind/listen (as server.py's __main__ does)
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
            c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-{member}"})
            break
        except OSError:                                 # backlog overflow / client ephemeral ports in TIME_WAIT
            REFUSED[0] += 1                             # (Windows load generator): retry, counted
            c.close()
            time.sleep(0.25 * (attempt + 1))
    r = c.getresponse()
    r.read()
    c.close()
    return r.status, time.time() - t0


ESPORTS = ["/api/moneylines", "/auth/me", "/", "/api/board"]
SPORTS = ["/api/sports/status", "/api/sports/offers?league=nba&limit=200", "/api/sports/offers?league=nfl&stat=rec"]


def member(i):
    out = []
    for k in range(3):
        out.append(("esports", ESPORTS[(i + k) % len(ESPORTS)]) + call(ESPORTS[(i + k) % len(ESPORTS)], f"u{i}"))
    if mode != "off":
        p = SPORTS[i % len(SPORTS)]
        out.append(("sports", p) + call(p, f"u{i}"))
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


for _ in range(20):                                     # warm
    call("/api/moneylines", "warm")
r0 = rss()
t0 = time.time()
with ThreadPoolExecutor(max_workers=700) as ex:
    res = [x for r in ex.map(member, range(700)) for x in r]
wall = time.time() - t0
r1 = rss()


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1)


rep = {"connect_retries": REFUSED[0], "mode": mode, "code": code, "members": 700, "wall_s": round(wall, 2), "rss_before_after_peak_mb": [r0, r1]}
for grp in ("esports", "sports"):
    rows = [x for x in res if x[0] == grp]
    if not rows:
        continue
    lat = [x[3] for x in rows]
    codes = {}
    for x in rows:
        codes[f"{x[1].split('?')[0]}:{x[2]}"] = codes.get(f"{x[1].split('?')[0]}:{x[2]}", 0) + 1
    rep[grp] = {"requests": len(rows), "errors_5xx": sum(1 for x in rows if x[2] >= 500), "p50_ms": pct(lat, .5),
                "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99), "codes": codes}
print("RESULT " + json.dumps(rep))
