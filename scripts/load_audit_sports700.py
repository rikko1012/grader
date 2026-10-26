"""Audit load test (2026-10-26): 700 simultaneous members against the sports service's hot member routes, in process
(sports.service.Server + Handler, the real concurrency cap), on a SYNTHETIC snapshot folder (no network, never production).

usage: python scripts/load_audit_sports700.py <code_dir> [members=700] [markets=4000]
Each member holds its own st1 token (minted locally with a throwaway secret) and makes: board, board part=more, slate,
manifest, results, forecast?id=, compare/check?items=, picks/mine. Right before the burst every snapshot file is
rewritten (new mtime), so the burst arrives on cold caches - the moment a snapshot rebuild lands for 700 members.
Counts how many times forecasts.json is parsed and the compare index is built (single flight = 1 per league).
Prints one line: RESULT {json}."""
import http.client
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

code = sys.argv[1]
MEMBERS = int(sys.argv[2]) if len(sys.argv) > 2 else 700
MARKETS = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
sys.path.insert(0, code)
os.chdir(code)
tmp = Path(tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__))))
os.environ.update({"SPORTS_DATA_DIR": str(tmp), "SPORTS_API": "1", "SPORTS_TOKEN_SECRET": secrets.token_hex(24)})

from sports import api_board, api_compare, authtoken, service  # noqa: E402

PARSES = {"forecasts": 0, "compare_index": 0}
_orig_index = api_compare.build_index


def counted_index(boards):
    PARSES["compare_index"] += 1
    return _orig_index(boards)


api_compare.build_index = counted_index


class CountingJson:
    def __getattr__(self, k):
        return getattr(json, k)

    @staticmethod
    def loads(s, *a, **k):
        PARSES["forecasts"] += 1
        return json.loads(s, *a, **k)


api_board.json = CountingJson()
FIDS = [f"fc-{i:016x}" for i in range(MARKETS)]


def write_snapshots():
    for lg in ("nba", "nfl"):
        d = tmp / "snapshots" / lg
        d.mkdir(parents=True, exist_ok=True)
        events = {f"{lg}:espn:{n}": {"start_utc": "2030-01-01T00:00:00Z", "status": "scheduled", "started": False}
                  for n in range(40)}
        markets = [{"k": f"{lg}:player:espn:{i}|points", "e": f"{lg}:espn:{i % 40}",
                    "q": [{"p": "underdog", "r": f"offer:underdog:o{i}:r1:abcdef", "l": 20.5 + i % 7, "x": "y" * 40}]}
                   for i in range(MARKETS)]
        board = {"version": "t", "events": events, "markets": markets}
        (d / "board.json").write_text(json.dumps(board))
        (d / "board_more.json").write_text(json.dumps(dict(board, markets=markets[: MARKETS // 2])))
        (d / "forecasts.json").write_text(json.dumps({"version": "t", "built_at": "x", "forecasts": {
            f: {"id": f, "p_over": 0.55, "grade": "B", "note": "z" * 200} for f in FIDS}}))
        for n in ("slate.json", "manifest.json", "results.json"):
            (d / n).write_text(json.dumps({"version": "t", "rows": [{"i": i, "v": "w" * 50} for i in range(500)]}))


write_snapshots()
srv = service.Server(("127.0.0.1", 0))
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


RETRIES = [0]


def call(path, tok):
    t0 = time.time()
    for attempt in range(10):                           # connect failures of the local load generator (Windows
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)   # backlog / ephemeral ports): retried, counted
        try:
            c.request("GET", path, headers={"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip"})
            r = c.getresponse()
            body = r.read()
            st = r.status
            break
        except OSError:
            st, body = -1, b""
            RETRIES[0] += 1
            time.sleep(0.2 * (attempt + 1))
        finally:
            c.close()
    return st, time.time() - t0, len(body)


def member(i):
    tok = authtoken.mint(f"user{i}")["token"]
    lg = ("nba", "nfl")[i % 2]
    paths = [f"/api/sports/board?league={lg}", f"/api/sports/board?league={lg}&part=more", f"/api/sports/slate?league={lg}",
             f"/api/sports/manifest?league={lg}", f"/api/sports/results?league={lg}",
             f"/api/sports/forecast?league={lg}&id={FIDS[i % len(FIDS)]}",
             f"/api/sports/compare/check?league={lg}&items=underdog~o{i % MARKETS}~{20.5 + (i % MARKETS) % 7}",
             "/api/sports/picks/mine"]
    return [(p.split("?")[0] + ("?part=more" if "part=more" in p else ""),) + call(p, tok) for p in paths]


warm = authtoken.mint("warm")["token"]
for lg in ("nba", "nfl"):                               # warm once, then a snapshot rebuild lands (new mtimes)
    call(f"/api/sports/forecast?league={lg}&id={FIDS[0]}", warm)
    call(f"/api/sports/compare/check?league={lg}&items=underdog~o0~20.5", warm)
time.sleep(0.05)
write_snapshots()
PARSES.update(forecasts=0, compare_index=0)
t0 = time.time()
with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
    res = [x for r in ex.map(member, range(MEMBERS)) for x in r]
wall = time.time() - t0


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1) if xs else None


lat = [x[2] for x in res]
rep = {"members": MEMBERS, "markets": MARKETS, "requests": len(res), "wall_s": round(wall, 2),
       "errors_5xx_excl_busy": sum(1 for x in res if x[1] >= 500) - service.STATS["rejected_busy"],
       "busy_503": service.STATS["rejected_busy"], "refused": sum(1 for x in res if x[1] == -1), "connect_retries": RETRIES[0],
       "non_200": sum(1 for x in res if x[1] != 200), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
       "rss_mb": round(service.rss_mb() or 0, 1), "peak_inflight": service.STATS["peak_inflight"],
       "parses_after_rebuild": dict(PARSES), "routes": {}}
for p in sorted({x[0] for x in res}):
    rows = [x for x in res if x[0] == p]
    codes = {}
    for x in rows:
        codes[x[1]] = codes.get(x[1], 0) + 1
    rep["routes"][p] = {"codes": codes, "p50_ms": pct([x[2] for x in rows], .5), "p99_ms": pct([x[2] for x in rows], .99)}
print("RESULT " + json.dumps(rep))
