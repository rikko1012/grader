"""line-gap-1109 NFL: score the frozen rules (FROZEN_RULES.md) at the real offered lines.

  python -m research.sports_lab.line_gap_1109_nfl.score <data.pkl> <out.json>

Baseline = live side (raw sm-1 lean) + live letter (sports-gp-1 band of the pc-1 printed chance, committed artifact).
Every arm leaves the projection and the chance untouched; it only moves a side or a letter.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from sports.model import chance_cal, grade_policy

B = 2000
SEED = 1109
ORDER = ["Lean", "C", "B", "A"]
GRADED = {"A", "B", "C"}


# ------------------------------------------------------------------------------------------------------------ prep
def prep(d):
    d = d.copy().reset_index(drop=True)
    d["base_over"] = d["po_raw"] >= 0.5
    q = []
    for s, p in zip(d["stat"], d["po_raw"]):
        a = chance_cal.adjust("nfl", s, p, 1 - p, 0.0)
        q.append(p if a is None else a["po"] / max(a["po"] + a["pu"], 1e-12))
    d["po_print"] = q
    d["printed_side_matches_raw"] = (np.asarray(q) >= 0.5) == d["base_over"]
    d["base_letter"] = [grade_policy.band(max(x, 1 - x)) for x in q]
    d["gap"] = d["other_line"] - d["line"]
    d["gap_pct"] = (d["gap"].abs() / ((d["line"] + d["other_line"]) / 2)).where(d["other_line"].notna())
    d["has_disc"] = d["other_line"].notna() & (d["gap"] != 0)
    d["soft_over"] = np.where(d["has_disc"], d["gap"] > 0, False)          # this line lower -> Over favoured here
    d["agree_disc"] = d["has_disc"] & (d["soft_over"] == d["base_over"])
    d["bump"] = d["line"] - d["open_line"]
    d["has_bump"] = d["open_line"].notna() & (d["bump"] != 0)
    d["mkt_agrees"] = d["has_bump"] & (((d["bump"] > 0) & d["base_over"]) | ((d["bump"] < 0) & ~d["base_over"]))
    d["mkt_disagrees"] = d["has_bump"] & ~d["mkt_agrees"]
    return d


def step(letters, mask, k):
    out = letters.copy()
    for i in np.flatnonzero(mask):
        out[i] = ORDER[min(3, max(0, ORDER.index(out[i]) + k))]
    return out


# ------------------------------------------------------------------------------------------------------- bootstrap
class GameBoot:
    def __init__(self, games, b=B, seed=SEED):
        self.ug, self.g = np.unique(np.asarray(games), return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def sums(self, x):
        return self.C @ np.bincount(self.g, weights=np.asarray(x, float), minlength=len(self.ug))

    def rate(self, hit, m):
        return self.sums(hit * m) / np.maximum(self.sums(m), 1)


def ci(v):
    return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]


def side_stats(over_pick, letters, y, mask):
    hit = np.where(over_pick, y, 1 - y)
    g = mask & np.isin(letters, list(GRADED))
    r = {}
    for nm, m in (("all", mask), ("graded", g)):
        s = {"n": int(m.sum()), "hit": round(float(hit[m].mean()), 4) if m.any() else None,
             "over_share": round(float(over_pick[m].mean()), 4) if m.any() else None}
        for sd, mm in (("over", m & over_pick), ("under", m & ~over_pick)):
            s[f"{sd}_n"] = int(mm.sum())
            s[f"{sd}_hit"] = round(float(hit[mm].mean()), 4) if mm.any() else None
        r[nm] = s
    r["by_letter"] = {L: {"n": int((mask & (letters == L)).sum()),
                          "hit": round(float(hit[mask & (letters == L)].mean()), 4) if (mask & (letters == L)).any()
                          else None} for L in ORDER[::-1]}
    return r, hit, g


def compare(d, mask, arm_over, arm_letter, bt):
    y = d["over"].to_numpy(float)
    bo, bl = d["base_over"].to_numpy(bool), d["base_letter"].to_numpy(object)
    sb, hb, gb = side_stats(bo, bl, y, mask)
    sa, ha, ga = side_stats(arm_over, arm_letter, y, mask)
    out = {"base": sb, "arm": sa, "side_changes": int((mask & (arm_over != bo)).sum()),
           "letter_changes": int((mask & (arm_letter != bl)).sum())}
    for nm, ma, mb in (("all", mask, mask), ("graded", ga, gb),
                       ("graded_over", ga & arm_over, gb & bo), ("graded_under", ga & ~arm_over, gb & ~bo),
                       ("all_over", mask & arm_over, mask & bo), ("all_under", mask & ~arm_over, mask & ~bo)):
        if not ma.any() or not mb.any():
            continue
        pt = float(ha[ma].mean() - hb[mb].mean())
        v = bt.rate(ha, ma.astype(float)) - bt.rate(hb, mb.astype(float))
        out[f"d_{nm}"] = round(pt, 4)
        out[f"d_{nm}_ci"] = ci(v)
    lop = lambda x: abs((x if x is not None else 0.5) - 0.5)
    H1 = out.get("d_graded", -1) > 0 and out.get("d_graded_ci", [-1, -1])[0] > 0
    H1pt = out.get("d_graded", -1) > 0
    H2 = all(out.get(k) is None or out[k] >= 0 for k in ("d_graded_over", "d_graded_under"))
    H3 = sa["graded"]["n"] >= sb["graded"]["n"]
    H4 = lop(sa["graded"]["over_share"]) <= lop(sb["graded"]["over_share"]) + 1e-9
    out["verdict"] = {"H1_graded_hit_up_ci_above_0": bool(H1), "H1_point_up": bool(H1pt), "H2_no_side_worse": bool(H2),
                      "H3_count_kept": bool(H3), "H4_not_more_lopsided": bool(H4),
                      "PASS": bool(H1 and H2 and H3 and H4),
                      "point_estimate_only": bool(H1pt and H2 and H3 and H4 and not H1)}
    return out


# ------------------------------------------------------------------------------------------------------------ arms
def fixed_arms(d):
    bo = d["base_over"].to_numpy(bool)
    bl = d["base_letter"].to_numpy(object)
    disc = d["has_disc"].to_numpy(bool)
    soft = d["soft_over"].to_numpy(bool)
    agree = d["agree_disc"].to_numpy(bool)
    large = disc & (d["gap_pct"].fillna(0).to_numpy(float) >= 0.10)
    ma = d["mkt_agrees"].to_numpy(bool)
    md = d["mkt_disagrees"].to_numpy(bool)
    late = d["bump_hours"].fillna(1e9).to_numpy(float) <= 24
    arms = {
        "a1_follow_soft": (np.where(disc, soft, bo), bl, "disc"),
        "a2_fade_soft": (np.where(disc, ~soft, bo), bl, "disc"),
        "a3_follow_soft_large": (np.where(large, soft, bo), bl, "disc"),
        "b1_agree_up_disagree_down": (bo, step(step(bl, disc & agree, +1), disc & ~agree, -1), "disc"),
        "b2_disagree_down": (bo, step(bl, disc & ~agree, -1), "disc"),
        "b3_agree_up": (bo, step(bl, disc & agree, +1), "disc"),
        "d1_mkt_agrees_up_disagrees_down": (bo, step(step(bl, ma, +1), md, -1), "bump"),
        "d2_reverse": (bo, step(step(bl, ma, -1), md, +1), "bump"),
        "d3_flip_when_mkt_disagrees": (np.where(md, ~bo, bo), bl, "bump"),
        "d4_d1_late_bumps_only": (bo, step(step(bl, ma & late, +1), md & late, -1), "bump"),
    }
    return arms


def walk_forward(d):
    """Learned-direction arms: for each game-day, the direction comes from game-days strictly before it."""
    days = sorted(d["game_day"].unique())
    y = d["over"].to_numpy(float)
    bo = d["base_over"].to_numpy(bool)
    bl = d["base_letter"].to_numpy(object)
    gd = d["game_day"].to_numpy(object)
    disc, soft, agree = d["has_disc"].to_numpy(bool), d["soft_over"].to_numpy(bool), d["agree_disc"].to_numpy(bool)
    ma, md = d["mkt_agrees"].to_numpy(bool), d["mkt_disagrees"].to_numpy(bool)
    graded = np.isin(bl, list(GRADED))
    hit_base = np.where(bo, y, 1 - y)
    hit_soft = np.where(soft, y, 1 - y)
    aW_over, bW_let, dW_let = bo.copy(), bl.copy(), bl.copy()
    test = {"aW": np.zeros(len(d), bool), "bW": np.zeros(len(d), bool), "dW": np.zeros(len(d), bool)}
    log = []
    for day in days:
        prior, cur = gd < day, gd == day
        e = {"game_day": day}
        p = prior & disc
        if p.sum() >= 50:
            r = float(hit_soft[p].mean())
            e["aW_prior_soft_hit"], e["aW_prior_n"] = round(r, 4), int(p.sum())
            if r != 0.5:
                aW_over = np.where(cur & disc, soft if r > 0.5 else ~soft, aW_over)
                e["aW_dir"] = "follow" if r > 0.5 else "fade"
            test["aW"] |= cur
        pa, pd_ = prior & disc & graded & agree, prior & disc & graded & ~agree
        if pa.sum() >= 50 and pd_.sum() >= 50:
            ra, rd = float(hit_base[pa].mean()), float(hit_base[pd_].mean())
            k = 1 if ra > rd else -1
            e["bW_prior"] = [round(ra, 4), int(pa.sum()), round(rd, 4), int(pd_.sum())]
            e["bW_dir"] = "agree_up" if k == 1 else "agree_down"
            bW_let = np.where(cur, step(step(bl, cur & disc & agree, k), cur & disc & ~agree, -k), bW_let)
            test["bW"] |= cur
        qa, qd = prior & ma & graded, prior & md & graded
        if qa.sum() >= 30 and qd.sum() >= 30:
            ra, rd = float(hit_base[qa].mean()), float(hit_base[qd].mean())
            k = 1 if ra > rd else -1
            e["dW_prior"] = [round(ra, 4), int(qa.sum()), round(rd, 4), int(qd.sum())]
            e["dW_dir"] = "d1" if k == 1 else "d2"
            dW_let = np.where(cur, step(step(bl, cur & ma, k), cur & md, -k), dW_let)
            test["dW"] |= cur
        log.append(e)
    return {"aW_learned": (aW_over, bl, "disc", test["aW"]), "bW_learned": (bo, bW_let, "disc", test["bW"]),
            "dW_learned": (bo, dW_let, "bump", test["dW"])}, log


def best_line(d, bt):
    """Rule c: in each (game, player, stat) with a discrepancy, the softest line for each row's own Scout side."""
    m = d["has_disc"].to_numpy(bool)
    bo = d["base_over"].to_numpy(bool)
    soft = d["soft_over"].to_numpy(bool)
    kept = m & (soft == bo)            # Over row at the lower line / Under row at the higher line
    dropped = m & (soft != bo)
    y = d["over"].to_numpy(float)
    hit = np.where(bo, y, 1 - y)
    graded = np.isin(d["base_letter"].to_numpy(object), list(GRADED))
    out = {}
    for scope, gm in (("all", np.ones(len(d), bool)), ("graded", graded)):
        r = {}
        for nm, mm in (("softest_kept", kept & gm), ("harder_dropped", dropped & gm), ("every_line", m & gm)):
            r[nm] = {"n": int(mm.sum()), "hit": round(float(hit[mm].mean()), 4) if mm.any() else None,
                     "over_n": int((mm & bo).sum()), "over_hit": round(float(hit[mm & bo].mean()), 4) if (mm & bo).any()
                     else None, "under_n": int((mm & ~bo).sum()),
                     "under_hit": round(float(hit[mm & ~bo].mean()), 4) if (mm & ~bo).any() else None,
                     "over_share": round(float(bo[mm].mean()), 4) if mm.any() else None}
        for a, b_ in (("softest_kept", "every_line"), ("softest_kept", "harder_dropped")):
            ma, mb = (kept if a == "softest_kept" else m) & gm, (m if b_ == "every_line" else dropped) & gm
            if ma.any() and mb.any():
                v = bt.rate(hit, ma.astype(float)) - bt.rate(hit, mb.astype(float))
                r[f"d_{a}_vs_{b_}"] = round(float(hit[ma].mean() - hit[mb].mean()), 4)
                r[f"d_{a}_vs_{b_}_ci"] = ci(v)
                for sd, sm in (("over", bo), ("under", ~bo)):
                    if (ma & sm).any() and (mb & sm).any():
                        r[f"d_{a}_vs_{b_}_{sd}"] = round(float(hit[ma & sm].mean() - hit[mb & sm].mean()), 4)
        out[scope] = r
    # props (distinct game/player/stat/side) graded: every line vs softest only
    key = d["event"] + "|" + d["player"] + "|" + d["stat"] + "|" + np.where(bo, "o", "u")
    out["distinct_props_graded_every_line"] = int(key[m & graded].nunique())
    out["distinct_props_graded_softest_only"] = int(key[kept & graded].nunique())
    out["middles_both_sides_softest"] = int(pd.Series(key[kept]).str[:-2].duplicated().sum())
    return out


def describe(d):
    def rate(mask, col="base_over"):
        y = d["over"].to_numpy(float)
        bo = d[col].to_numpy(bool)
        hit = np.where(bo, y, 1 - y)
        return {"n": int(mask.sum()), "hit": round(float(hit[mask].mean()), 4) if mask.any() else None}
    g = np.isin(d["base_letter"].to_numpy(object), list(GRADED))
    disc = d["has_disc"].to_numpy(bool)
    out = {"rows": len(d), "games": int(d["event"].nunique()), "disc_rows": int(disc.sum()),
           "gap_units_abs": d.loc[disc, "gap"].abs().describe().round(3).to_dict(),
           "gap_pct": d.loc[disc, "gap_pct"].describe().round(4).to_dict(),
           "soft_over_share": round(float(d.loc[disc, "soft_over"].mean()), 4) if disc.any() else None,
           "agree_all": rate(disc & d["agree_disc"].to_numpy(bool)),
           "disagree_all": rate(disc & ~d["agree_disc"].to_numpy(bool)),
           "agree_graded": rate(disc & g & d["agree_disc"].to_numpy(bool)),
           "disagree_graded": rate(disc & g & ~d["agree_disc"].to_numpy(bool)),
           "bump_rows": int(d["has_bump"].sum()), "open_known_rows": int(d["open_line"].notna().sum()),
           "mkt_agrees_all": rate(d["mkt_agrees"].to_numpy(bool)),
           "mkt_disagrees_all": rate(d["mkt_disagrees"].to_numpy(bool)),
           "mkt_agrees_graded": rate(d["mkt_agrees"].to_numpy(bool) & g),
           "mkt_disagrees_graded": rate(d["mkt_disagrees"].to_numpy(bool) & g),
           "bump_units_abs": d.loc[d["has_bump"], "bump"].abs().describe().round(3).to_dict(),
           "bump_hours": d.loc[d["has_bump"], "bump_hours"].describe().round(2).to_dict(),
           "printed_side_matches_raw_share": round(float(d["printed_side_matches_raw"].mean()), 4)}
    # soft-side hit rate by gap size (descriptive)
    y = d["over"].to_numpy(float)
    hs = np.where(d["soft_over"].to_numpy(bool), y, 1 - y)
    for lo, hi in ((0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 9)):
        mm = disc & (d["gap_pct"].fillna(-1).to_numpy(float) >= lo) & (d["gap_pct"].fillna(-1).to_numpy(float) < hi)
        out[f"soft_side_hit_gap_{lo:.2f}_{hi:.2f}"] = {"n": int(mm.sum()),
                                                        "hit": round(float(hs[mm].mean()), 4) if mm.any() else None}
    for nm, grp in (("by_book", "book"), ("by_stat", "stat")):
        out[nm] = {}
        for k, idx in d[disc].groupby(grp).groups.items():
            mm = np.zeros(len(d), bool)
            mm[np.asarray(idx)] = True
            out[nm][k] = {"n": int(mm.sum()), "soft_side_hit": round(float(hs[mm].mean()), 4),
                          "scout_hit": rate(mm)["hit"], "agree_share": round(float(d.loc[mm, "agree_disc"].mean()), 4)}
    return out


def run_block(d, label):
    bt = GameBoot(d["event"].to_numpy())
    res = {"label": label, "n": len(d), "games": int(d["event"].nunique()),
           "game_days": sorted(d["game_day"].unique().tolist()), "describe": describe(d), "arms": {}}
    dom = {"disc": d["has_disc"].to_numpy(bool), "bump": d["has_bump"].to_numpy(bool)}
    for name, (ov, lt, domain) in fixed_arms(d).items():
        res["arms"][name] = {"domain": domain, "domain_rows": compare(d, dom[domain], ov, lt, bt),
                             "every_row": compare(d, np.ones(len(d), bool), ov, lt, bt)}
    res["best_line"] = best_line(d, bt)
    return res


def main(pkl, out_json):
    d = prep(pd.read_pickle(pkl))
    res = {"frozen_rules": "research/sports_lab/line_gap_1109_nfl/FROZEN_RULES.md (commit 9bf5909)",
           "blocks": {}}
    for label, m in (("D1_primary", d["src"] == "D1"), ("D2_secondary", d["src"] == "D2"), ("pooled", d["src"] != "")):
        sub = d[m].reset_index(drop=True)
        if len(sub):
            res["blocks"][label] = run_block(sub, label)
    wf, log = walk_forward(d)
    bt = GameBoot(d["event"].to_numpy())
    dom = {"disc": d["has_disc"].to_numpy(bool), "bump": d["has_bump"].to_numpy(bool)}
    res["walk_forward"] = {"log": log, "arms": {}}
    for name, (ov, lt, domain, test) in wf.items():
        if test.any():
            res["walk_forward"]["arms"][name] = {
                "test_game_days": sorted(set(d.loc[test, "game_day"])),
                "domain_rows": compare(d, dom[domain] & test, ov, lt, bt),
                "every_row": compare(d, test, ov, lt, bt)}
        else:
            res["walk_forward"]["arms"][name] = {"test_game_days": [], "note": "no game-day had enough prior rows"}
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    for label, b in res["blocks"].items():
        print("==", label, b["n"], "rows", b["games"], "games")
        for k, a in b["arms"].items():
            c = a["domain_rows"]
            print(f"  {k:34s} dom n={c['base']['all']['n']:5d} gr {c['base']['graded']['n']}->{c['arm']['graded']['n']} "
                  f"hit {c['base']['graded']['hit']}->{c['arm']['graded']['hit']} d {c.get('d_graded')} {c.get('d_graded_ci')}"
                  f" O {c.get('d_graded_over')} U {c.get('d_graded_under')} PASS {c['verdict']['PASS']}")
    for k, a in res["walk_forward"]["arms"].items():
        if "domain_rows" in a:
            c = a["domain_rows"]
            print(f"  WF {k:30s} days {a['test_game_days']} gr {c['base']['graded']['n']}->{c['arm']['graded']['n']} "
                  f"d {c.get('d_graded')} {c.get('d_graded_ci')} PASS {c['verdict']['PASS']}")
        else:
            print("  WF", k, a["note"])
    return res


if __name__ == "__main__":
    main(*sys.argv[1:3])
