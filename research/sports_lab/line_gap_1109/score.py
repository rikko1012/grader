"""lg-1 scoring (rules frozen in RULES.md + Amendment 1). python -m research.sports_lab.line_gap_1109.score <lg1.pkl> <out.json>

A flipped side keeps the row's letter (so a flip never changes the graded count); letter rules keep the side.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

LET = ["Lean", "C", "B", "A"]
BURN_IN_DAYS = 5
MIN_CHANGED = 30
B, SEED = 1000, 7


def letter_idx(p):
    return np.select([p >= 0.60, p >= 0.5559, p >= 0.5345], [3, 2, 1], 0)


def flip(side):
    return np.where(side == "over", "under", "over")


# each rule: df -> (side, letter_idx, changed mask)
def r_a(df, mask):
    s = np.where(mask, df["fav"].fillna("x"), df["side"])
    return s, df["L0"].values, mask


def r_letter(df, up, down):
    li = df["L0"].values.copy()
    li = np.where(up, np.minimum(li + 1, 3), li)
    li = np.where(down, np.maximum(li - 1, 0), li)
    return df["side"].values, li, (up | down)


RULES = {
    "a1": lambda d: r_a(d, (d["D2"] & d["disagree"]).values),
    "a2": lambda d: r_a(d, d["disagree"].values),
    "a3": lambda d: (np.where(d["price_disagree"], d["price_side"].fillna("x"), d["side"]), d["L0"].values,
                     d["price_disagree"].values),
    "b1": lambda d: r_letter(d, d["agree"].values, d["disagree"].values),
    "b2": lambda d: r_letter(d, np.zeros(len(d), bool), d["disagree"].values),
    "c1": lambda d: r_letter(d, np.zeros(len(d), bool), d["c1_hard"].values & (d["L0"].values > 0)),
    "d1": lambda d: (np.where(d["big_bump"] & d["away"], flip(d["side"].values), d["side"]), d["L0"].values,
                     (d["big_bump"] & d["away"]).values),
    "d2": lambda d: r_letter(d, d["toward"].values, d["away"].values),
    "d3": lambda d: r_letter(d, np.zeros(len(d), bool),
                             (d["away"] & (d["window"] == "S") & (d["bump_hrs"] <= 3)).values),
}
C1_DOWN_TO_LEAN = True     # c1: the hard line becomes Lean (not one step)


def prep(df):
    df = df[df["result"].isin(["over", "under"])].copy().reset_index(drop=True)
    df["L0"] = letter_idx(df["p"].values)
    # c1 pairs: same run, both apps, same Scout side, different lines
    df["c1_hard"] = False
    df["c1_soft"] = False
    pair = df[df["same_run"] & df["L_O"].notna() & (df["gap"] != 0)]
    other = df.set_index(["game_id", "player_id", "stat", "book", "forecast_at"])["side"]
    for i, r in pair.iterrows():
        ob = "underdog" if r["book"] == "prizepicks" else "prizepicks"
        os_ = other.get((r["game_id"], r["player_id"], r["stat"], ob, r["forecast_at"]))
        if isinstance(os_, pd.Series):
            os_ = os_.iloc[0]
        if os_ != r["side"]:
            continue
        soft = (r["side"] == "over" and r["gap"] < 0) or (r["side"] == "under" and r["gap"] > 0)
        df.at[i, "c1_soft" if soft else "c1_hard"] = True
    last = df.groupby(["game_id", "player_id", "stat", "book"])["forecast_at"].transform("max")
    df["is_final"] = df["forecast_at"] == last
    return df


def apply_rule(df, name):
    s, li, ch = RULES[name](df)
    if name == "c1" and C1_DOWN_TO_LEAN:
        li = np.where(ch, 0, df["L0"].values)
    return np.asarray(s), np.asarray(li), np.asarray(ch, bool)


def summary(df, side, li):
    win = side == df["result"].values
    g = li >= 1
    ov = side == "over"
    def hr(m):
        return float(win[m].mean()) if m.any() else None
    return {"n": int(len(df)), "hit": hr(np.ones(len(df), bool)), "over_n": int(ov.sum()), "over_hit": hr(ov),
            "under_hit": hr(~ov), "graded_n": int(g.sum()), "graded_hit": hr(g), "graded_over_hit": hr(g & ov),
            "graded_under_hit": hr(g & ~ov), "graded_over_share": float(ov[g].mean()) if g.any() else None}


def boot(df, base, new):
    """Game-grouped bootstrap CI of new - base hit rate: all props and graded."""
    games, gi = np.unique(df["game_id"].values, return_inverse=True)
    res = df["result"].values
    out = {}
    for scope in ("all", "graded"):
        cols = []
        for side, li in (base, new):
            m = np.ones(len(df), bool) if scope == "all" else li >= 1
            w = np.bincount(gi, weights=((side == res) & m).astype(float), minlength=len(games))
            n = np.bincount(gi, weights=m.astype(float), minlength=len(games))
            cols += [w, n]
        rng = np.random.default_rng(SEED)
        idx = rng.integers(0, len(games), size=(B, len(games)))
        bw, bn, nw, nn = (c[idx].sum(1) for c in cols)
        d = nw / np.maximum(nn, 1) - bw / np.maximum(bn, 1)
        pt = cols[2].sum() / max(cols[3].sum(), 1) - cols[0].sum() / max(cols[1].sum(), 1)
        out[scope] = {"diff": float(pt), "ci": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}
    return out


def verdict(bs, sb, sn, bt):
    h1 = bt["all"]["diff"] > 0 and bt["graded"]["diff"] > 0 and bt["graded"]["ci"][0] > 0
    def ok(a, b):
        return a is None or b is None or b >= a - 1e-12
    h2 = all(ok(sb[k], sn[k]) for k in ("over_hit", "under_hit", "graded_over_hit", "graded_under_hit"))
    h3 = sn["graded_n"] >= sb["graded_n"]
    h4 = (sb["graded_over_share"] is None or sn["graded_over_share"] is None or
          abs(sn["graded_over_share"] - 0.5) <= abs(sb["graded_over_share"] - 0.5) + 1e-12)
    fails = [h for h, v in (("H1", h1), ("H2", h2), ("H3", h3), ("H4", h4)) if not v]
    return "PASS" if not fails else "FAIL: " + ", ".join(fails)


def gate_ok(df, name, side, li, ch):
    """Earlier days pooled: changed rows >= 30 and their hit (graded hit for letter rules) after > before."""
    if ch.sum() < MIN_CHANGED:
        return False, int(ch.sum())
    base_side, base_li = df["side"].values, df["L0"].values
    res = df["result"].values
    if (side != base_side)[ch].any():
        after, before = (side == res)[ch].mean(), (base_side == res)[ch].mean()
    else:
        # letter rule: graded hit over ALL earlier rows, after vs before
        ga, gb = li >= 1, base_li >= 1
        after = (side == res)[ga].mean() if ga.any() else 0
        before = (base_side == res)[gb].mean() if gb.any() else 0
    return bool(after > before), int(ch.sum())


def run(df):
    days = sorted(df["day"].unique())
    test_days = days[BURN_IN_DAYS:]
    test = df["day"].isin(test_days).values
    out = {"days": days, "test_days": test_days, "rules": {}}
    base = (df["side"].values, df["L0"].values)
    for name in RULES:
        side, li, ch = apply_rule(df, name)
        # walk-forward: per test day, gate on earlier days only
        ws, wl = base[0].copy(), base[1].copy()
        gates = {}
        for d in test_days:
            prior = (df["day"] < d).values
            sub = df[prior]
            ok, n = gate_ok(sub, name, side[prior], li[prior], ch[prior])
            gates[d] = {"active": ok, "changed_prior": n}
            if ok:
                m = (df["day"] == d).values
                ws[m], wl[m] = side[m], li[m]
        rec = {"changed_test": int(ch[test].sum()), "active_days": sum(g["active"] for g in gates.values()),
               "gates": gates}
        for label, (s2, l2), mask in (("walk_forward", (ws, wl), test), ("fixed_test_days", (side, li), test),
                                      ("fixed_all_days", (side, li), np.ones(len(df), bool)),
                                      ("fixed_test_final_lines", (side, li), test & df["is_final"].values)):
            sub = df[mask].reset_index(drop=True)
            sb, sn = summary(sub, base[0][mask], base[1][mask]), summary(sub, s2[mask], l2[mask])
            bt = boot(sub, (base[0][mask], base[1][mask]), (s2[mask], l2[mask]))
            rec[label] = {"base": sb, "rule": sn, "diff": bt, "verdict": verdict(None, sb, sn, bt),
                          "games": int(sub["game_id"].nunique())}
        # the changed rows themselves (information)
        m = ch & test
        if m.any():
            res = df["result"].values
            rec["changed_rows_test"] = {"n": int(m.sum()), "hit_before": float((base[0] == res)[m].mean()),
                                        "hit_after": float((side == res)[m].mean())}
        out["rules"][name] = rec
    return out


def descriptive(df):
    """Baseline hit rate by feature group, all days (information only; nothing below is a rule)."""
    res = df["result"].values
    win = df["side"].values == res
    g = df["L0"].values >= 1
    def grp(m):
        m = np.asarray(m, bool)
        ov = df["side"].values == "over"
        def h(x):
            return [int(x.sum()), float(win[x].mean()) if x.any() else None]
        return {"all": h(m), "over": h(m & ov), "under": h(m & ~ov), "graded": h(m & g)}
    d = df
    return {
        "no_other_line": grp(d["L_O"].isna()), "lines_equal": grp(d["L_O"].notna() & ~d["D1"]),
        "agree": grp(d["agree"]), "disagree": grp(d["disagree"]),
        "agree_D2": grp(d["agree"] & d["D2"]), "disagree_D2": grp(d["disagree"] & d["D2"]),
        "c1_soft": grp(d["c1_soft"]), "c1_hard": grp(d["c1_hard"]),
        "no_bump": grp(d["bump"] == 0), "bump_toward": grp(d["toward"]), "bump_away": grp(d["away"]),
        "big_bump_toward": grp(d["toward"] & d["big_bump"]), "big_bump_away": grp(d["away"] & d["big_bump"]),
        "away_late_S": grp(d["away"] & (d["window"] == "S") & (d["bump_hrs"] <= 3)),
        "away_early_S": grp(d["away"] & (d["window"] == "S") & (d["bump_hrs"] > 3)),
        "price_agree": grp(d["price_side"].notna() & ~d["price_disagree"]), "price_disagree": grp(d["price_disagree"]),
        "price_none": grp(d["q_mkt"].notna() & d["price_side"].isna()),
        "pp": grp(d["book"] == "prizepicks"), "ud": grp(d["book"] == "underdog"),
        "gap_pct": d.loc[d["D1"], "gap_pct"].describe().to_dict(),
        "gap_abs_counts": d.loc[d["L_O"].notna(), "gap"].abs().value_counts().sort_index().head(12).to_dict(),
    }


if __name__ == "__main__":
    df = prep(pd.read_pickle(sys.argv[1]))
    out = run(df)
    out["descriptive"] = descriptive(df)
    out["counts"] = {"decided_rows": int(len(df)), "games": int(df["game_id"].nunique()),
                     "c1_pairs_soft": int(df["c1_soft"].sum()), "c1_pairs_hard": int(df["c1_hard"].sum()),
                     "test_rows": int(df["day"].isin(out["test_days"]).sum()),
                     "test_games": int(df.loc[df["day"].isin(out["test_days"]), "game_id"].nunique())}
    json.dump(out, open(sys.argv[2], "w"), indent=1, default=str)
    for k, v in out["rules"].items():
        w, f = v["walk_forward"], v["fixed_test_days"]
        print(k, "changed", v["changed_test"], "active", v["active_days"], "| WF", w["verdict"],
              {s: round(w["diff"][s]["diff"] * 100, 2) for s in w["diff"]}, "| fixed", f["verdict"],
              {s: (round(f["diff"][s]["diff"] * 100, 2), [round(c * 100, 1) for c in f["diff"][s]["ci"]])
               for s in f["diff"]})
