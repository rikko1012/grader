"""Follow-ups to eval20 (same rows, same rules; development evidence):
1. cross-platform with ONE orientation per pair (Underdog line as the base) - eval20 counted each pair twice, which forces
   the 0.5 slope and a >50% 'hit' from middles;
2. results by stat (kills / HS) for the rolling candidates on E1;
3. stability of the disagreement slope across the two eras (Railway days 10-02..10-10, Render days 10-11..10-16);
4. line movement: our forecast-time line vs the platform's closing line for the same unit (diagnostic only - agreement with
   a later line is not proof of skill);
5. E2 restricted to the P1 rules (one per unit, Render, matches through the 10-16 18:40 pull) to reconcile with P1;
6. power: the smallest effects the available matches can detect."""
import collections
import math
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval20 as E  # noqa: E402

RL = E.RL
D = pickle.load(open(os.path.join(RL, "eval20_rows.pkl"), "rb"))
E1, E2 = D["E1"], D["E2"]


def pair_test(rows, label):
    by = collections.defaultdict(dict)
    for r in rows:
        bk = r.get("book") or ("prizepicks" if r.get("books") == ["prizepicks"] else "underdog" if r.get("books") == ["underdog"] else None)
        if bk and r.get("other_line") is not None:
            by[(r["m"], r["p_id"], r["prop"], r["scope"])][bk] = r
    pairs = [v for v in by.values() if len(v) == 2]
    if len(pairs) < 30:
        print(f"\n{label}: {len(pairs)} pairs - too few")
        return
    g = [dict(v["underdog"], pp_line=v["prizepicks"]["line"]) for v in pairs]
    bt = E.Boot(g)
    x = np.array([r["pp_line"] - r["line"] for r in g])
    y = np.array([r["actual"] - r["line"] for r in g])
    b = bt.slope(x, y)
    mae_ud = bt.ratio(np.abs(np.array([r["actual"] - r["line"] for r in g])))
    mae_pp = bt.ratio(np.abs(np.array([r["actual"] - r["pp_line"] for r in g])))
    print(f"\n#### {label}: {len(g)} pairs with different lines / {len(bt.keys)} matches (Underdog line as the base, one row per pair)")
    print(f"  slope of (actual - UD line) on (PP line - UD line): {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f})  [0 = UD right, 1 = PP right, 0.5 = their mean]")
    print(f"  MAE Underdog line {mae_ud[0]:.2f} ({mae_ud[1]:.2f}, {mae_ud[2]:.2f}) | PrizePicks line {mae_pp[0]:.2f} ({mae_pp[1]:.2f}, {mae_pp[2]:.2f})")
    for prop in ("kills", "hs"):
        gg = [r for r in g if r["prop"] == prop]
        if len(gg) >= 30:
            bt2 = E.Boot(gg)
            b2 = bt2.slope(np.array([r["pp_line"] - r["line"] for r in gg]), np.array([r["actual"] - r["line"] for r in gg]))
            print(f"  {prop}: {len(gg)} pairs / {len(bt2.keys)} matches, slope {b2[0]:+.3f} ({b2[1]:+.3f}, {b2[2]:+.3f})")
    # one-sided bet on ONE platform only: Underdog side toward the PrizePicks line (no middles)
    side = np.array([(r["y"] if r["pp_line"] > r["line"] else 1 - r["y"]) for r in g])
    h = bt.ratio(side)
    print(f"  Underdog side pointed to by the PrizePicks line (one bet per pair): hit {h[0]:.3f} ({h[1]:.3f}, {h[2]:.3f})")


def by_stat(rows, models, label):
    for prop in ("kills", "hs"):
        g = [r for r in rows if r["prop"] == prop]
        E.prob_table(g, models, ["p_BASE"], f"{label} - {prop} only")
        E.selection(g, models[2:], f"{label} - {prop} only")


def stability(rows, lam_key, label):
    print(f"\n#### stability of the disagreement slope by era ({label}, {lam_key})")
    print("| stat x scope | era | obs | matches | slope (95%) | MSE proj - MSE line (95%) |")
    print("|---|---|---|---|---|---|")
    for prop in ("kills", "hs"):
        for era, f in (("Oct 02-10 (Railway era)", lambda r: r["day"] <= "2026-10-10"), ("Oct 11-17 (Render era)", lambda r: r["day"] >= "2026-10-11")):
            g = [r for r in rows if r["prop"] == prop and r["scope"] == "12" and f(r)]
            if len(g) < 40:
                continue
            bt = E.Boot(g)
            x = np.array([r[lam_key] - r["line"] for r in g])
            y = np.array([r["actual"] - r["line"] for r in g])
            b = bt.slope(x, y)
            dm = bt.ratio(np.array([(r[lam_key] - r["actual"]) ** 2 - (r["line"] - r["actual"]) ** 2 for r in g]))
            print(f"| {prop} Maps 1-2 | {era} | {len(g)} | {len(bt.keys)} | {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f}) | {dm[0]:+.2f} ({dm[1]:+.2f}, {dm[2]:+.2f}) |")


def line_moves():
    close = {}
    for r in E1:
        for bk in r["books"]:
            close[(r["m"], r["p_id"], r["prop"], r["scope"], bk)] = r["line"]
    g = []
    for r in E2:
        c = close.get((r["m"], r["p_id"], r["prop"], r["scope"], r["book"]))
        if c is not None:
            g.append(dict(r, close=c, move=c - r["line"]))
    mv = [r for r in g if abs(r["move"]) > 1e-9]
    print(f"\n#### line movement (E2 forecast-time line vs the platform's closing line): {len(g)} joined, {len(mv)} moved "
          f"({len({r['m'] for r in mv})} matches)")
    if not mv:
        return
    bt = E.Boot(mv)
    tow = np.array([1.0 if np.sign(r["move"]) == np.sign(r["lam_n0"] - r["line"]) else 0.0 for r in mv])
    towL = np.array([1.0 if np.sign(r["move"]) == np.sign(r["p_live"] - 0.5) else 0.0 for r in mv])
    print(f"  moved toward our rebuilt projection: {bt.ratio(tow)[0]:.3f} ({bt.ratio(tow)[1]:.3f}, {bt.ratio(tow)[2]:.3f}); toward the side live favoured: "
          f"{bt.ratio(towL)[0]:.3f} ({bt.ratio(towL)[1]:.3f}, {bt.ratio(towL)[2]:.3f})")
    b = bt.slope(np.array([r["move"] for r in mv]), np.array([r["actual"] - r["line"] for r in mv]))
    print(f"  slope of (actual - forecast-time line) on the later move: {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f})  [1 = the move was fully right]")
    print("  (diagnostic only: a line moving our way is agreement with a later market, not proof of selection skill)")


def p1_like():
    g = [r for r in E2 if r["host"] == "render" and r["start"] <= "2026-10-16T18:10"]
    best = {}
    for r in g:                                   # P1: one per unit (latest forecast across platforms)
        k = (r["m"], r["p_id"], r["prop"], r["scope"])
        if k not in best or r["logged"] > best[k]["logged"]:
            best[k] = r
    u = list(best.values())
    print(f"\n#### E2 restricted to P1-like rules (Render, one per unit across platforms, starts <= 10-16 18:10): {len(u)} units / "
          f"{len({r['m'] for r in u})} matches (P1 at 19:37: 1,211 / 72)")
    E.selection(u, ["p_live", "p_n0"], "P1-like E2")
    both = [r for r in g if r not in u]
    print(f"  rows E2 keeps beyond P1 (the other platform's different line): {len(both)}")


def power(rows, label):
    """Per-match variance of paired gains -> standard error at M matches; detectable effect (80% power, two-sided 5%)."""
    bt = E.Boot(rows)
    y = np.array([r["y"] for r in rows], float)
    gl = np.array([E.ll(r["p_BASE"], r["y"]) - E.ll(r["p_ANCHORfit_full"] if "p_ANCHORfit_full" in r else r["p_n"], r["y"]) for r in rows])
    s = np.bincount(bt.g, weights=gl, minlength=len(bt.keys))
    c = np.bincount(bt.g, minlength=len(bt.keys)).astype(float)
    mean = s.sum() / c.sum()
    # ratio-estimator variance per match: var(s - mean c) / (mean c)^2 / M
    v = np.var(s - mean * c, ddof=1) / (c.mean() ** 2)
    sel = []
    for r in rows:
        p = r.get("p_ANCHORfit_full", r["p_n"])
        if max(p, 1 - p) >= 0.57:
            o = p >= 0.5
            sel.append(((r["y"] if o else 1 - r["y"]) - (r["p_BASE"] if o else 1 - r["p_BASE"]), r["m"]))
    sm = collections.defaultdict(lambda: [0.0, 0])
    for val, m in sel:
        sm[m][0] += val
        sm[m][1] += 1
    S = np.array([a for a, b in sm.values()])
    C = np.array([b for a, b in sm.values()], float)
    ms = S.sum() / C.sum()
    vs = np.var(S - ms * C, ddof=1) / (C.mean() ** 2)
    frac = len(sm) / len(bt.keys)
    print(f"\n#### power ({label}): {len(bt.keys)} matches, {len(rows) / len(bt.keys):.1f} observations per match; "
          f"selection picks in {frac:.0%} of matches, {C.mean():.1f} picks per picked match")
    print("| matches | SE log-loss gain | detectable log-loss gain (80%, 5% two-sided) | SE hit - BASE | detectable selection edge (80%, 5%) |")
    print("|---|---|---|---|---|")
    for M in (100, 189, 300, 600, 1000, 2000):
        se_l = math.sqrt(v / M)
        se_s = math.sqrt(vs / (M * frac))
        print(f"| {M} | {se_l:.4f} | {2.80 * se_l:.4f} | {se_s:.3f} | {2.80 * se_s:.3f} |")
    return v, vs, frac


def main():
    sc1 = [r for r in E1 if r.get("scored")]
    pair_test(E1, "E1 closing lines, platforms differ")
    pair_test(E2, "E2 forecast-time lines, platforms differ")
    by_stat(sc1, ["flat", "p_BASE", "p_n0", "p_n", "p_PL2_proj", "p_CORR_full", "p_ANCHORfit_proj", "p_ANCHORfit_full"], "E1 rolling")
    stability(E1, "lam_n", "E1 full history")
    stability(E1, "lam_n0", "E1 live-pin history")
    stability(E2, "lam_n", "E2 full history")
    line_moves()
    p1_like()
    power(sc1, "E1 scored")


if __name__ == "__main__":
    main()
