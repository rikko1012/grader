"""AFTER scoring (not part of the frozen test): robustness of the one pass, G6 (lift the AWPer-on-HS 0U gate), and the
fresh holdout (pick rows starting 2026-11-09 or later, pulled after scoring).  -> stdout + $GU_DIR/g6_check.json"""
import gzip, json, os
import numpy as np, pandas as pd
from score import Boot, hit, summ, side_of, OUT, OPS

REASON = "AWPer on a headshot prop"


def block(name, D, over_col="over"):
    """D: rows with p, units, pk_flags/flags, match, prop. Returns live staked vs lifted staked + the added set."""
    p = D["p"].to_numpy(float); base = side_of(p); over = D[over_col].to_numpy()
    ps = np.round(np.maximum(p, 1 - p), 6); u = D["units"].to_numpy(float)
    st0 = u > 0
    add = (D["prop"].to_numpy() == "hs") & D["flags_s"].str.contains(REASON).to_numpy() & (ps >= 0.55) & (u == 0)
    st1 = st0 | add
    bt = Boot(D["match"].to_numpy())
    a, b, c = summ(base, over, st0), summ(base, over, st1), summ(base, over, add)
    c["ci"] = bt.ci(lambda w: hit(base, over, w, add)); c["matches"] = int(D.loc[add, "match"].nunique())
    c["stated"] = round(100 * float(ps[add].mean()), 1) if add.any() else None
    c["units_distinct"] = int(D.loc[add, "unitkey"].nunique())
    rest = (ps >= 0.55) & (D["prop"].to_numpy() == "hs") & ~add          # other HS picks at 55%+ (comparison)
    r = summ(base, over, rest); r["ci"] = bt.ci(lambda w: hit(base, over, w, rest))

    def f(w):
        x, y = hit(base, over, w, st1), hit(base, over, w, st0)
        return None if x is None or y is None else x - y
    out = {"rows": len(D), "matches": bt.n, "live_staked": a, "lifted_staked": b,
           "diff": None if a["hit"] is None else round(b["hit"] - a["hit"], 2), "diff_ci": bt.ci(f), "added": c,
           "other_hs_55plus": r, "all_lines": summ(base, over)}
    g = lambda x: "-" if x is None else f"{x:.1f}"
    print(f"{name:34s} rows {len(D):5d} m {bt.n:3d} | staked {a['n']:4d} {g(a['hit'])} -> {b['n']:4d} {g(b['hit'])} d {out['diff']} {out['diff_ci']}"
          f" | O {g(a['o_hit'])}->{g(b['o_hit'])} U {g(a['u_hit'])}->{g(b['u_hit'])} Osh {g(a['o_share'])}->{g(b['o_share'])}"
          f" | ADDED n {c['n']} ({c['units_distinct']} units, {c['matches']} m) hit {g(c['hit'])} {c['ci']} stated {c['stated']}"
          f" O {c['o_n']}:{g(c['o_hit'])} U {c['u_n']}:{g(c['u_hit'])} | other HS 55%+ {r['n']} {g(r['hit'])} {r['ci']}", flush=True)
    return out


def pick_rows(path, lo, hi):
    rows = {}
    with gzip.open(path, "rt") as f:
        for l in f:
            d = json.loads(l)
            rows[d["id"]] = d
    out = []
    for d in rows.values():
        if not (d.get("on_board") == 1 and d.get("line_type") == "standard" and d.get("book") in ("underdog", "prizepicks")):
            continue
        if d.get("status") != "settled" or d.get("hit") not in (0, 1) or d.get("p_more") is None:
            continue
        if not (lo <= (d.get("match_start") or "") < hi):
            continue
        over = d["hit"] if d["pick"] == "More" else 1 - d["hit"]
        out.append({"p": d["p_more"], "units": d["units"] or 0.0, "flags_s": d.get("flags") or "", "prop": d["prop"],
                    "match": d["match_slug"], "over": over, "start": d["match_start"], "id": d["id"],
                    "unitkey": f"{d.get('player_id')}|{d['match_slug']}|{d['prop']}|{d.get('scope')}",
                    "key": f"{d.get('player_id')}|{d['match_slug']}|{d['prop']}|{d.get('scope')}|{d['line']}|{d['book']}"})
    D = pd.DataFrame(out)
    if len(D):
        D = D.sort_values("id").drop_duplicates("key", keep="last").reset_index(drop=True)   # re-keyed rows counted once
    return D


def main():
    res = {}
    T = pd.read_pickle(OUT + "table.pkl")
    T["flags_s"] = T["pk_flags"].fillna(""); T["unitkey"] = T["unit"]
    cuts = [("P1 rows Oct 11-18 (W0)", "2026-10-11", "2026-10-19"), ("P1 rows Oct 19-25 (W1)", "2026-10-19", "2026-10-26"),
            ("P1 rows Oct 26-28 13:40", "2026-10-26", "2026-10-28T13:40"),
            ("P1 rows Oct 28 13:40-Nov 1 (fwd)", "2026-10-28T13:40", "2026-11-02"),
            ("P1 rows Nov 2-8 (fwd, W3)", "2026-11-02", "2026-11-09"),
            ("P1 rows FORWARD of sr-gates-1", "2026-10-28T13:40", "2026-11-09"),
            ("P1 rows ALL Oct 11-Nov 8", "2026-10-11", "2026-11-09")]
    for name, lo, hi in cuts:
        D = T[(T["start"] >= lo) & (T["start"] < hi)].reset_index(drop=True)
        res[name] = block(name, D)
    # one row per unit (the P1 book's row) - removes the two-app double count
    D = T[(T["start"] >= "2026-10-28T13:40") & T["is_p1_book"]].reset_index(drop=True)
    res["P1 units FORWARD (one per unit)"] = block("P1 units FORWARD (one per unit)", D)
    D = T[T["is_p1_book"]].reset_index(drop=True)
    res["P1 units ALL (one per unit)"] = block("P1 units ALL (one per unit)", D)
    # pick-row rule (latest pre-start grade per board line): the old export for comparison, the refresh for the holdout
    D = pick_rows(OPS + "line_gap_1109/picks.jsonl.gz", "2026-10-28T13:40", "2026-11-09")
    res["pick rows Oct 28 13:40-Nov 8"] = block("pick rows Oct 28 13:40-Nov 8", D)
    D = pick_rows(OUT + "picks_refresh.jsonl.gz", "2026-11-09", "2026-11-12")
    res["HOLDOUT pick rows Nov 9-11"] = block("HOLDOUT pick rows Nov 9-11", D)
    A = pd.concat([pick_rows(OPS + "line_gap_1109/picks.jsonl.gz", "2026-10-28T13:40", "2026-11-09"), D], ignore_index=True)
    res["pick rows Oct 28 13:40-Nov 11"] = block("pick rows Oct 28 13:40-Nov 11", A)
    json.dump(res, open(OUT + "g6_check.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
