"""Polite, paged, read-only pull of /api/sports/admin/model/gradecore-comparison?league=nfl (100 rows/page, ~1.2 s/req,
resumable, token never printed; the token is read from the ops folder, never stored here). Stops when a page's oldest
captured_at < STOP or next_before is None.

  python -m research.sports_lab.line_gap_1109_nfl.page_cmp <out_dir> [STOP]
"""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
STOP = sys.argv[2] if len(sys.argv) > 2 else "2026-11-03"
class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
T = open("C:/Users/tikic/cs2_scout_ops/.admin_token").read().strip()
op = urllib.request.build_opener(NR)
def raw(url, h):
    with op.open(urllib.request.Request(url, headers=h), timeout=120) as r: return r.read()
tok, tok_t = None, 0
def get(path):
    global tok, tok_t
    for a in range(5):
        try:
            if tok is None or time.time() - tok_t > 600:
                tok = json.loads(raw("https://cs2-scout.onrender.com/api/sports/token", {"X-Admin-Token": T, "X-Load-Test": T}))["token"]; tok_t = time.time()
            b = raw("https://scout-sports-api.onrender.com" + path, {"Authorization": "Bearer " + tok}); time.sleep(1.2); return b
        except (urllib.error.HTTPError, OSError) as e:
            print("retry", a, getattr(e, "code", e), flush=True); time.sleep(5 * (a + 1)); tok = None
    raise SystemExit("gave up")
pages = sorted(OUT.glob("page_*.json"))
before = None; n = len(pages)
if pages:
    last = json.loads(pages[-1].read_bytes()); before = last["next_before"]
    if before is None or min(r["captured_at"] for r in last["rows"]) < STOP: print("already done"); raise SystemExit
while True:
    path = "/api/sports/admin/model/gradecore-comparison?league=nfl" + (f"&before={before}" if before else "")
    b = get(path); d = json.loads(b)
    (OUT / f"page_{n:04d}.json").write_bytes(b); n += 1
    rows = d["rows"]
    if not rows: break
    oldest = min(r["captured_at"] for r in rows)
    if n % 20 == 0: print(n, oldest, flush=True)
    before = d["next_before"]
    if before is None or oldest < STOP: break
print("done", n, flush=True)
