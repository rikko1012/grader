"""Read-only pull of the CS2 P1 scorecard export and the saved forecast record behind each unit.

usage: python fetch_cs2.py <out_dir>

1. /admin/learning/p1_units.json, paged (2,000 rows a page, 0.3 s between pages) - never an unbounded export.
2. /admin/forecasts_page.json, paged by keyset windows that start just before each needed forecast id (at most 60 rows a
   window, 0.3 s between windows), keeping only the P1 forecast ids: the saved raw chances (probs_raw), the live shadow
   block (projection source), odds source and the model tag. No inputs blob (keeps every page small).
The admin token is read by lq.py from its own file and is never printed. Output: <out_dir>/cs2_p1.json, cs2_fc.json.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\tikic\cs2_scout_ops")
from lq import adm as _adm  # noqa: E402


def adm(path, tries=4):
    """One read; a 5xx (a deploy restarting the service) waits 30 s and retries a few times, then gives up."""
    for k in range(tries):
        try:
            return _adm(path)
        except Exception as e:                      # noqa: BLE001
            if k == tries - 1 or "50" not in str(e):
                raise
            print("retry after", type(e).__name__, str(e)[:40], flush=True)
            time.sleep(30)

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
WIN = 60

SNAP = OUT / "cs2_p1.json"
rows, after, meta = [], 0, None
if SNAP.exists():                           # resume: keep the snapshot already pulled (one export, one hash)
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    rows, meta = snap["rows"], snap["meta"]
else:
    while True:
        r = adm(f"/admin/learning/p1_units.json?after={after}&limit=2000")
        meta = {k: v for k, v in r.items() if k != "rows"}
        rows += r["rows"]
        print("p1 page", after, len(r["rows"]), flush=True)
        if r.get("next_after") is None:
            break
        after = r["next_after"]
        time.sleep(0.3)
    SNAP.write_text(json.dumps({"meta": meta, "rows": rows}), encoding="utf-8")
print("p1 units", len(rows), "pushes", len(meta.get("pushes") or []), "at", meta.get("at"), flush=True)

need = sorted({r["forecast_id"] for r in rows} | {r["forecast_id"] for r in meta.get("pushes") or []})
KEEP = ("id", "pick_id", "kind", "logged_at", "match_start", "line", "pick", "p_pick", "p_more", "grade", "units", "lambda",
        "p_win", "sample_maps", "model_version", "probs_raw", "odds_source", "odds_policy", "data_rev")
got, cut, i = {}, None, 0
while i < len(need):
    fid = need[i]
    j = i
    while j + 1 < len(need) and need[j + 1] - fid < WIN:
        j += 1
    lim = need[j] - fid + 1
    q = f"/admin/forecasts_page.json?after={fid - 1}&limit={lim}" + (f"&cutoff={cut}" if cut else "")
    pg = adm(q)
    cut = pg["cutoff_id"]
    want = set(need[i:j + 1])
    for f in pg["rows"]:
        if f["id"] in want:
            keep = {k: f.get(k) for k in KEEP}
            try:
                sh = json.loads(f.get("shadow") or "{}") or {}
            except ValueError:
                sh = {}
            # the live projection's own source tag and spread fields, as saved (no recomputation)
            keep["shadow_source"] = ((sh.get("old_spread") or {}).get("source") or (sh.get("no_rest_bonus") or {}).get("source"))
            keep["shadow_keys"] = sorted(sh.keys())
            keep["live_online"] = sh.get("live_online")
            got[f["id"]] = keep
    i = j + 1
    if len(got) % 500 < len(want):
        print("forecasts", len(got), "of", len(need), flush=True)
    time.sleep(0.3)
(OUT / "cs2_fc.json").write_text(json.dumps({"cutoff_id": cut, "rows": got}), encoding="utf-8")
print("forecasts fetched", len(got), "of", len(need), "missing", len(set(need) - set(got)))
