"""700 simultaneous members against the research endpoints (fixture upstreams, private data dir). Not a unit test."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\esp-accuracy"); os.chdir(r"C:\Users\tikic\cs2_scout\.claude\worktrees\esp-accuracy")
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp

import hltv_store  # noqa: E402
hltv_store.DB_PATH = __import__("pathlib").Path(tmp) / "hltv_store.db"
import results_view, kalshi, moneyline, polymarket_ml, server, slip_batches, slip_builder, underdog, whop_auth  # noqa: E402,E401
from tests.test_api_moneyline_slips import Boards  # noqa: E402
from tests.test_moneyline import kalshi_raw, poly_raw  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
slip_builder.DB_PATH = __import__("pathlib").Path(tmp) / "slip_studio.db"
b = Boards(n_matches=30)
server.board_payload = b.payload
underdog.board = b.feed
server.SLIP_STUDIO = True
slip_batches.results = lambda last=2000: [{"batch": "a", "version": "batch-3"}]
srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
        "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
for s in srcs.values():
    s.refresh_once()
moneyline.SOURCES = srcs
server.results_payload = lambda: results_view.member_results({'rows': [], 'at': None})
assert server.__file__.startswith(r'C:\Users\tikic\cs2_scout\.claude\worktrees\esp-accuracy'), server.__file__
ThreadingHTTPServer.request_queue_size = 1024
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
httpd.daemon_threads = True
httpd.request_queue_size = 1024
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]


def call(method, path, body=None, member="m"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Cookie": f"{whop_auth.COOKIE}=good-{member}"}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    t0 = time.time()
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, time.time() - t0, out


def member(i):
    res = []
    kind = i % 4
    if kind == 0:
        res.append(call("GET", "/api/moneylines", member=f"u{i}"))
    elif kind == 1:
        res.append(call("GET", "/api/slip_studio/batch", member=f"u{i}"))
    elif kind == 2 and i % 8 == 6:                     # closeout: manual slip log (entry rules + line recheck)
        res.append(call("POST", "/api/slip_log", {"cid": f"c{i:05d}", "mode": "member-copy", "n": 2, "book": "underdog",
                                                  "legs": [{"nick": "a", "team": "Alpha", "book": "underdog", "side": "More"},
                                                           {"nick": "b", "team": "Alpha", "book": "underdog", "side": "More"}]},
                        member=f"u{i}"))
    elif kind == 2 and i % 8 == 2:
        res.append(call("GET", "/api/board", member=f"u{i}"))
        res.append(call("GET", "/", member=f"u{i}"))
        res.append(call("GET", "/api/results", member=f"u{i}"))
    else:
        st, dt, out = call("POST", "/api/slip_studio/build", {"settings": {"sizes": [2, 3], "count": 1}, "idem": f"press-{i:05d}"},
                           member=f"u{i}")
        res.append((st, dt, out))
        if st == 200 and kind == 3:
            d = json.loads(out)
            sl = [s for n in ("2", "3") for s in d["slips"][n]]
            if sl:
                res.append(call("POST", "/api/slip_studio/verify", {"slip": sl[0]["slip_id"], "idem": f"ver-{i:05d}"},
                                member=f"u{i}"))
                res.append(call("POST", "/api/slip_studio/build", {"idem": f"repl-{i:05d}", "replace": sl[0]["slip_id"]},
                                member=f"u{i}"))
    return res


t0 = time.time()
with ThreadPoolExecutor(max_workers=700) as ex:
    results = [r for rs in ex.map(member, range(700)) for r in rs]
took = time.time() - t0
codes = {}
for st, dt, _ in results:
    codes[st] = codes.get(st, 0) + 1
lat = sorted(dt for _, dt, _ in results)
print(json.dumps({"requests": len(results), "codes": codes, "wall_s": round(took, 1),
                  "p50_s": round(lat[len(lat) // 2], 3), "p95_s": round(lat[int(len(lat) * 0.95)], 3),
                  "max_s": round(lat[-1], 3), "errors_5xx": server.STATS["errors_5xx"]}))
httpd.shutdown()
