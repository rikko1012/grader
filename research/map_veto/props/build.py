"""vm-1 research inputs (REGISTER.md sections 2-3). Read-only inputs, scratch outputs.

One chronological pass over the veto dataset with a day-by-day VetoTable (every context taken BEFORE that day's matches
are added - the veto-1 leakage rule):
  maps.json.gz   one row per played map 2025-07-01 .. 2026-10-22 with a legal MR12 final score: match, date, map, teams,
                 A/B score, Elo expectation, map-strength difference (team A = the lower team id)
  ctx.json.gz    for every match dated 2026-07-01 .. 2026-10-22 with a complete veto (+ any P1 match): the pre-veto
                 veto-1 prediction (top 250 sequences), the vm-1 match context, the confirmed played order (RETROSPECTIVE)
usage: python build.py
"""
import collections, gzip, json, math, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import veto_model as vmod  # noqa: E402
import vm1  # noqa: E402
import pandas as pd  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
OUT = os.path.join(SCR, "veto", "vprops")
MAP_FROM, CTX_FROM, END = "2025-07-01", "2026-07-01", "2026-10-22"


def played_order(r):
    acts = sorted(r["actions"])
    picks = [a[3] for a in acts if a[2] == "picked"]
    left = [a[3] for a in acts if a[2] == "leftover"]
    return picks + left


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    with gzip.open(os.path.join(SCR, "veto", "vmodel", "veto_dataset.json.gz"), "rt", encoding="utf-8") as f:
        recs = json.load(f)["records"]
    p1 = pd.read_csv(os.path.join(SCR, "cs2acc", "ed1", "p1.csv.gz"), usecols=["match"])
    p1m = {int(m.split(":")[1]) for m in p1.match.unique() if str(m).startswith("hltv:")}
    c = sqlite3.connect(os.path.join(SCR, "cs2acc", "data", "hltv_window.sqlite"))
    mapname = {r[0]: vmod.map_key(r[1]) for r in c.execute("select mapid, name from maps")}
    score = collections.defaultdict(dict)
    for mid, tid, mp, sc in c.execute("select m.matchid, m.teamid, m.mapid, m.score from matches m join match_overview mo "
                                      "using (matchid) where m.sideid = 0 and m.mapid > 0 and mo.date >= ?", (MAP_FROM,)):
        score[(mid, mapname.get(mp, str(mp)))][tid] = sc
    by = collections.defaultdict(list)
    for r in recs:
        if r["date"] <= END:
            by[r["date"]].append(r)
    table = vmod.VetoTable()
    maps_rows, ctx_rows, bad = [], [], collections.Counter()
    for d in sorted(by):
        if d >= MAP_FROM:
            day = vmod._day(d)
            for r in by[d]:
                if len(r["teams"]) != 2:
                    continue
                ta, tb = sorted(r["teams"])
                mw = r.get("map_winner") or {}
                pool = list(r["pool"]) if r.get("pool") else None
                pmaps = [m for m in mw]
                if pool is None or len(pool) != 7:
                    pool_s = sorted(set(pmaps) | set(pool or ()))
                else:
                    pool_s = pool
                if not pool_s:
                    continue
                ctx = vm1.match_context(table, ta, tb, pool_s, d)
                ea = 1.0 / (1.0 + 10 ** ((ctx["elo_b"] - ctx["elo_a"]) / 400.0))
                for m in pmaps:
                    sc = score.get((r["mid"], m), {})
                    if len(sc) != 2 or ta not in sc or tb not in sc:
                        bad["no score"] += 1
                        continue
                    a, b = sc[ta], sc[tb]
                    if (max(a, b), min(a, b)) not in vm1.VALID or a == b:
                        bad["not a legal MR12 score"] += 1
                        continue
                    maps_rows.append([r["mid"], d, m, ta, tb, a, b, ea, ctx["strength_a"].get(m, 0.0) - ctx["strength_b"].get(m, 0.0),
                                      r["fmt"]])
                if d >= CTX_FROM and r["status"] == "complete" and len(pool_s) == 7:
                    lf = None
                    if r.get("listed"):
                        lf = "a" if r["listed"][0] == ta else "b"
                    try:
                        pred = vmod.predict(ta, tb, r["fmt"], pool_s, before=d, event=r.get("event"), listed_first=lf,
                                            table=table, top=250)
                    except vmod.IllegalVeto as e:
                        bad["veto: " + str(e)[:40]] += 1
                        continue
                    ctx_rows.append({"mid": r["mid"], "date": d, "fmt": r["fmt"], "teams": [ta, tb], "pool": pool_s,
                                     "pred": {"version": pred["version"], "format": pred["format"], "sequences": pred["sequences"],
                                              "sequences_remaining_mass": pred["sequences_remaining_mass"],
                                              "maps": pred["maps"]},
                                     "ctx": ctx, "retro_order": played_order(r), "map_winner": mw,
                                     "n_maps_played": r.get("n_maps_played"), "p1": r["mid"] in p1m})
        table.add_day(by[d], d)
        if d.endswith("-01"):
            print(f"[{time.time() - t0:6.0f}s] {d} maps {len(maps_rows)} ctx {len(ctx_rows)}", flush=True)
    with gzip.open(os.path.join(OUT, "maps.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"cols": ["mid", "date", "map", "ta", "tb", "a", "b", "ea", "dstr", "fmt"], "rows": maps_rows,
                   "excluded": dict(bad)}, f)
    with gzip.open(os.path.join(OUT, "ctx.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"rows": ctx_rows}, f)
    print("maps", len(maps_rows), "ctx", len(ctx_rows), "p1 ctx", sum(r["p1"] for r in ctx_rows), dict(bad),
          f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
