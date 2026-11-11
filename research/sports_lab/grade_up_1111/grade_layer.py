"""gu-1111 grade-layer arms G1-G7 on the saved sm-1 chances at real offered lines (FROZEN_RULES.md section 1).

  python -m research.sports_lab.grade_up_1111.grade_layer nba <lg1_ext.pkl> <out.json>
  python -m research.sports_lab.grade_up_1111.grade_layer nfl <nfl_lines.pkl> <nfl_fresh.pkl> <line_gap_nfl dataset.pkl> <out.json>

Inputs are local, read-only pickles (see data_build notes in the report). Nothing is written but the JSON.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from research.sports_lab.grade_up_1111 import common as C

BUCKETS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 1.0001)
MIN_N, STEP_PTS = 150, 0.03
KEY = ["game", "player", "stat", "book", "line"]


def _day(start):
    return (pd.to_datetime(start, utc=True) - pd.Timedelta(hours=10)).dt.strftime("%Y-%m-%d")


def load_nba(path):
    d = pd.read_pickle(path)
    d = d[d["result"].isin(["over", "under"])].copy()
    out = pd.DataFrame({"game": d["game_id"], "player": d["player_id"], "stat": d["stat"], "book": d["book"],
                        "line": d["line"].astype(float), "start": d["start_at"], "po": d["p_over"].astype(float),
                        "over": (d["result"] == "over").astype(float), "q_mkt": d["q_mkt"].astype(float),
                        "window": d["window"], "D2": d["D2"].astype(bool), "disagree": d["disagree"].astype(bool)})
    # standard flag exists only in window S rows that carry the feed's break-even field for at least one row of the
    # same (game, book) capture; a null break-even there = non-standard line
    be = d["be_over"].notna()
    has_flag = be.groupby([d["game_id"], d["book"]]).transform("any") & (d["start_at"] >= "2026-11-07T12:00:00Z")
    out["std"] = np.where(has_flag.to_numpy(), be.to_numpy().astype(float), np.nan)
    out["day"] = _day(out["start"])
    return out.reset_index(drop=True)


def load_nfl(lines_pkl, fresh_pkl, lg_pkl):
    a = pd.read_pickle(lines_pkl)
    a = pd.DataFrame({"game": a["game_id"], "player": a["player_id"], "stat": a["market"], "book": a["book"],
                      "line": a["line"].astype(float), "start": a["start_at"], "po": a["po_live"].astype(float),
                      "over": a["over"].astype(float), "std": np.nan, "src": "weeks8-9"})
    f = pd.read_pickle(fresh_pkl)
    lg = pd.read_pickle(lg_pkl)
    stdkeys = set(map(tuple, lg[lg["src"] == "D1"][["event", "player", "stat", "book", "line"]].astype(
        {"event": str, "player": str, "line": float}).to_numpy()))
    k = list(zip(f["espn_event"].astype(str), f["espn_player"].astype(str), f["market"], f["book"],
                 f["line"].astype(float)))
    b = pd.DataFrame({"game": f["game_id"], "player": f["player_id"], "stat": f["market"], "book": f["book"],
                      "line": f["line"].astype(float), "start": f["start_at"], "po": f["po_live"].astype(float),
                      "over": f["over"].astype(float), "std": [float(x in stdkeys) for x in k], "src": "week10"})
    d = pd.concat([a, b], ignore_index=True)
    d = d[d["book"].isin(["prizepicks", "underdog"])]
    d["q_mkt"] = np.nan
    d["day"] = _day(d["start"])
    return d.reset_index(drop=True)


def base_cols(d, league):
    ov, pc = C.side_of(d["po"])
    q = C.printed(pc, league)
    return ov, pc, q, C.band_idx(q), np.where(ov, d["over"], 1 - d["over"])


def fixed_arms(d, league):
    ov, pc, q, L0, hit = base_cols(d, league)
    arms = {"G1 over-bar": np.where(ov, C.step_down(L0), L0),
            "G1b over-floor": np.where(ov & (L0 == 1), 0, L0),
            "G2 over-bar + under-step": np.where(ov, C.step_down(L0), C.step_up(L0, q))}
    return arms


def wf_arms(d, league, burn_days):
    """G3 cell-step and G4 evidence letters, expanding by day; -> letters, test mask, per-day log."""
    ov, pc, q, L0, hit = base_cols(d, league)
    days = sorted(d["day"].unique())
    test_days = days[burn_days:]
    L3, L4 = L0.copy(), L0.copy()
    stat = d["stat"].to_numpy()
    bidx = np.digitize(pc, BUCKETS) - 1
    dayv = d["day"].to_numpy()
    log = {"G3": {}, "G4": {}}
    for day in test_days:
        tr, te = dayv < day, dayv == day
        h_all = hit[tr].mean()
        acts = {}
        for s in np.unique(stat):
            for side in (True, False):
                cell_tr = tr & (stat == s) & (ov == side)
                n = int(cell_tr.sum())
                if n < MIN_N:
                    continue
                dlt = hit[cell_tr].mean() - h_all
                cell_te = te & (stat == s) & (ov == side)
                if dlt >= STEP_PTS:
                    L3[cell_te] = C.step_up(L0[cell_te], q[cell_te])
                    acts[f"{s}/{'over' if side else 'under'}"] = f"up ({dlt * 100:+.1f}, n {n})"
                elif dlt <= -STEP_PTS:
                    L3[cell_te] = C.step_down(L0[cell_te])
                    acts[f"{s}/{'over' if side else 'under'}"] = f"down ({dlt * 100:+.1f}, n {n})"
        log["G3"][day] = acts
        acts = {}
        for b in range(len(BUCKETS) - 1):
            for side in (True, False):
                cell_tr = tr & (bidx == b) & (ov == side)
                n = int(cell_tr.sum())
                if n < MIN_N:
                    continue
                h = hit[cell_tr].mean()
                cell_te = te & (bidx == b) & (ov == side)
                L4[cell_te] = int(C.band_idx(h))
                acts[f"{BUCKETS[b]:.2f}/{'over' if side else 'under'}"] = f"{C.LET[int(C.band_idx(h))]} ({h * 100:.1f}, n {n})"
        log["G4"][day] = acts
    return {"G3 cell-step": L3, "G4 evidence letters": L4}, np.isin(dayv, test_days), log, test_days


def descriptive(d, league):
    ov, pc, q, L0, hit = base_cols(d, league)
    bt = C.Boot(d["game"].to_numpy())
    out = {"all": C.tier_stats(hit, ov, np.ones(len(d), bool))}
    out["by_letter"] = {C.LET[k]: C.tier_stats(hit, ov, L0 == k) for k in (3, 2, 1, 0)}
    out["by_stat"] = {s: {"all": C.tier_stats(hit, ov, (d["stat"] == s).to_numpy()),
                          "graded": C.tier_stats(hit, ov, ((d["stat"] == s) & (L0 >= 1)).to_numpy())}
                      for s in sorted(d["stat"].unique())}
    out["by_raw_bucket"] = {}
    bidx = np.digitize(pc, BUCKETS) - 1
    for b in range(len(BUCKETS) - 1):
        m = bidx == b
        if m.any():
            out["by_raw_bucket"][f"{BUCKETS[b]:.2f}-{min(BUCKETS[b + 1], 1):.2f}"] = {
                **C.tier_stats(hit, ov, m), "stated_raw": round(100 * float(pc[m].mean()), 1),
                "printed": round(100 * float(q[m].mean()), 1)}
    out["by_book"] = {b: C.tier_stats(hit, ov, (d["book"] == b).to_numpy()) for b in sorted(d["book"].unique())}
    raw_g = pc >= C.BANDS[0]
    out["raw_band_graded"] = C.tier_stats(hit, ov, raw_g)
    if d["std"].notna().any():
        k = d["std"].notna().to_numpy()
        out["standard"] = {"standard": C.tier_stats(hit, ov, k & (d["std"] == 1).to_numpy()),
                           "non_standard": C.tier_stats(hit, ov, k & (d["std"] == 0).to_numpy())}
    if d["q_mkt"].notna().any():
        qs = np.where(ov, d["q_mkt"], 1 - d["q_mkt"])          # market price of Scout's side
        out["G6_cap3_market_price_of_scout_side"] = {
            lab: C.tier_stats(hit, ov, np.nan_to_num(qs, nan=-1) >= lo if hi is None else
                              (np.nan_to_num(qs, nan=-1) >= lo) & (qs < hi))
            for lab, lo, hi in (("<0.45", 0.0, 0.45), ("0.45-0.55", 0.45, 0.55), ("0.55-0.65", 0.55, 0.65),
                                (">=0.65", 0.65, None))}
        out["G6_priced_rows"] = int(d["q_mkt"].notna().sum())
    # Over vs Under hit difference with CI (the weak side)
    out["under_minus_over_hit_ci"] = bt.diff_ci(hit, ~ov, hit, ov)
    out["under_minus_over_graded_ci"] = bt.diff_ci(hit, ~ov & (L0 >= 1), hit, ov & (L0 >= 1)) if (ov & (L0 >= 1)).any() else None
    return out


def a1_retest(d):
    """G7a: lg-1 a1 (flip when D2 gap and Scout disagrees), fixed rule, NBA. Raw-band letters as in lg-1 RULES.md and
    printed letters; new slates (start >= 2026-11-09T12:00Z) alone, the Nov 9 test days, and pooled."""
    out = {}
    po1 = np.where(d["D2"] & d["disagree"], 1 - d["po"], d["po"])
    new = (d["start"] >= "2026-11-09T12:00:00Z").to_numpy()
    days = sorted(d["day"].unique())
    old_test = np.isin(d["day"], days[5:]) & ~new
    for lab, m in (("new_slates", new), ("nov9_test_days", old_test), ("pooled_test", new | old_test),
                   ("all_days_incl_burn_in", np.ones(len(d), bool))):
        x = d[m]
        if not len(x):
            continue
        bt = C.Boot(x["game"].to_numpy())
        r = C.side_arm(bt, x["over"].to_numpy(), x["po"].to_numpy(), po1[m], "nba")
        ch = (x["D2"] & x["disagree"]).to_numpy()
        ov, pc = C.side_of(x["po"])
        hit0 = np.where(ov, x["over"], 1 - x["over"])
        r["rows"], r["games"] = int(len(x)), int(x["game"].nunique())
        r["flipped_rows"] = int(ch.sum())
        r["flipped_hit_before_after"] = [C._pct(C._r(hit0, ch)), C._pct(1 - C._r(hit0, ch))] if ch.any() else None
        out[lab] = r
    return out


def run(d, league, burn_days):
    assert not d.duplicated(KEY).any(), "duplicate rows"
    ov, pc, q, L0, hit = base_cols(d, league)
    res = {"league": league, "rows": int(len(d)), "games": int(d["game"].nunique()),
           "days": sorted(d["day"].unique()), "duplicates": 0, "pc1_b": C.B_CAL[league],
           "descriptive": descriptive(d, league), "arms": {}}
    wf, test, log, test_days = wf_arms(d, league, burn_days)
    res["test_days"], res["wf_log"] = test_days, log
    scopes = {"all_rows": np.ones(len(d), bool), "test_days": test}
    arms = dict(fixed_arms(d, league))
    for nm, L1 in arms.items():
        res["arms"][nm] = {}
        for sc, m in scopes.items():
            if m.any():
                bt = C.Boot(d["game"].to_numpy()[m])
                res["arms"][nm][sc] = C.letter_arm(bt, hit[m], ov[m], L0[m], L1[m])
    bt = C.Boot(d["game"].to_numpy()[test]) if test.any() else None
    for nm, L1 in wf.items():
        res["arms"][nm] = {"test_days": C.letter_arm(bt, hit[test], ov[test], L0[test], L1[test])} if bt else {}
    k = d["std"].notna().to_numpy()
    if k.any() and (d["std"][k] == 0).any():
        L5 = np.where((d["std"] == 0).to_numpy(), C.step_down(L0), L0)
        bt5 = C.Boot(d["game"].to_numpy()[k])
        res["arms"]["G5 standard-only"] = {"flag_rows": C.letter_arm(bt5, hit[k], ov[k], L0[k], L5[k])}
        res["arms"]["G5 standard-only"]["flag_rows"]["rows"] = int(k.sum())
    else:
        res["arms"]["G5 standard-only"] = {"not_measurable": "no non-standard flag on these rows"}
    if league == "nba":
        res["G7a_a1_retest"] = a1_retest(d)
    return res


def show(res):
    for nm, a in res["arms"].items():
        for sc, r in a.items():
            if not isinstance(r, dict) or "graded" not in r:
                print(nm, sc, r)
                continue
            g, t = r["graded"], r["top"]
            print(f"{nm:26s} {sc:9s} chg {r['changed']:5d} | graded n {g['base']['n']}->{g['arm']['n']} hit "
                  f"{g['base']['hit']}->{g['arm']['hit']} d {g['diff']} {g['ci']} O {g['arm']['over_hit']} U "
                  f"{g['arm']['under_hit']} osh {g['base']['over_share']}->{g['arm']['over_share']} | top n "
                  f"{t['base']['n']}->{t['arm']['n']} hit {t['base']['hit']}->{t['arm']['hit']} d {t['diff']} {t['ci']}"
                  f" | {'PASS' if r['pass'] else 'FAIL ' + ','.join(r['failed'])}")


if __name__ == "__main__":
    lg = sys.argv[1]
    if lg == "nba":
        d = load_nba(sys.argv[2])
        res = run(d, "nba", 5)
    else:
        d = load_nfl(sys.argv[2], sys.argv[3], sys.argv[4])
        days = sorted(d["day"].unique())
        first_week = [x for x in days if x <= "2026-10-27"]        # week 8 (Thu Oct 22 - Mon Oct 26)
        res = run(d, "nfl", len(first_week))
    json.dump(res, open(sys.argv[-1], "w"), indent=1, default=str)
    print(lg, res["rows"], res["games"], "days", len(res["days"]), "test", len(res["test_days"]))
    print(json.dumps(res["descriptive"]["by_letter"]))
    show(res)
