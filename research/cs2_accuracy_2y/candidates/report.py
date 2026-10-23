"""rm-1 / th-1 development report: registered gate G1-G5 per cell, effect sizes, match-grouped intervals, subgroups,
probability-vs-selection split. usage: python report.py <scratch cs2acc dir>  ->  <S>/cands/report.json, <S>/cands/tables.md"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

S = Path(sys.argv[1])
C = S / "cands"
FOLDS = ["F1", "F2", "F3", "F4", "F5", "F6"]
BONF = (100 * 0.05 / 12 / 2, 100 - 100 * 0.05 / 12 / 2)
H = pd.concat([pd.read_csv(C / f"hltv_{f}_{sc}.csv.gz") for f in FOLDS for sc in ("1", "12", "123")], ignore_index=True)
H["scope"] = H.scope.astype(str)
H["depth5"] = pd.cut(H.r_w, [-1, 50, 150, 500, 1500, 1e12], right=False, labels=["<50", "50-150", "150-500", "500-1500", "1500+"]).astype(str)
me = H.groupby("match").elo_m.mean()
cuts = np.quantile(me.values, [0.2, 0.4, 0.6, 0.8])
H["tier"] = H.match.map(lambda m: int(np.searchsorted(cuts, me[m]) + 1)).astype(str)
STAT = {"kills": "k", "hs": "h"}
R = {"hltv": {}, "p1": {}, "gate": {}}
md = []


def f3(t, d=4):
    return f"{t[0]:+.{d}f} [{t[1]:+.{d}f}, {t[2]:+.{d}f}]"


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def side_cols(p, y):
    side = np.where(p > 0.5, "Over", "Under")
    pf = np.where(p > 0.5, p, 1 - p)
    hit = np.where(p > 0.5, y, 1 - y)
    return side, pf, hit


def cell_rows(cand, stat, sc):
    s = STAT[stat]
    d = H[(H.scope == sc) & H[f"{s}_ls_live"].notna()]
    if cand == "th1":
        d = d[d.th_cell.notna() & (d.th_cell.astype(str) != "nan") & (d.th_cell.astype(str) != "")]
    return d


# ================================================================ HLTV
md.append("## HLTV 2y (population a): paired log-score gain of the integer actual vs live (nats per player-series; + = candidate better)\n")
md.append("| cand | stat | scope | rows | matches | pooled gain 95% | pooled Bonferroni 99.58% | " + " | ".join(FOLDS) + " | G1 | G2 |")
md.append("|---|---|---|---|---|---|---|" + "---|" * len(FOLDS) + "---|---|")
for cand in ("rm1", "th1"):
    for stat in ("kills", "hs"):
        s = STAT[stat]
        for sc in ("1", "12", "123"):
            d = cell_rows(cand, stat, sc)
            gain = (d[f"{s}_ls_{cand}"] - d[f"{s}_ls_live"]).values
            b95, G = lib.boot({"g": gain}, d.match)
            bb, _ = lib.boot({"g": gain}, d.match, qs=BONF)
            per, g1 = {}, True
            for f in FOLDS:
                m = (d.fold == f).values
                if m.sum() == 0:
                    per[f] = None; continue
                bf, Gf = lib.boot({"g": gain[m]}, d.match.values[m])
                per[f] = {"gain": bf["g"], "matches": Gf, "rows": int(m.sum())}
                if Gf >= 200 and bf["g"][1] <= 0:
                    g1 = False
            g2 = bb["g"][1] > 0
            key = f"{cand}|{stat}|{sc}"
            R["hltv"][key] = {"rows": int(len(d)), "matches": G, "pooled": b95["g"], "bonf": bb["g"], "folds": per, "G1": g1, "G2": bool(g2)}
            fs = " | ".join("-" if per[f] is None else (f"{per[f]['gain'][0]:+.4f} [{per[f]['gain'][1]:+.4f}, {per[f]['gain'][2]:+.4f}] ({per[f]['matches']} m)") for f in FOLDS)
            md.append(f"| {cand} | {stat} | {sc} | {len(d)} | {G} | {f3(b95['g'])} | {f3(bb['g'])} | {fs} | {'pass' if g1 else 'FAIL'} | {'pass' if g2 else 'FAIL'} |")

# subgroups, calibration at synthetic lines, PIT coverage, line metrics
md.append("\n## HLTV subgroups (pooled folds; gain 95%, B=1000) - deterioration flag = interval entirely < 0\n")
md.append("| cand | stat | scope | group | level | rows | matches | gain | flag |")
md.append("|---|---|---|---|---|---|---|---|---|")
for cand in ("rm1", "th1"):
    for stat in ("kills", "hs"):
        s = STAT[stat]
        for sc in ("1", "12", "123"):
            d = cell_rows(cand, stat, sc)
            key = f"{cand}|{stat}|{sc}"
            sub, worst = {}, False
            for grp in ("depth5", "tier", "model"):
                for lv, q in d.groupby(grp):
                    if q.match.nunique() < 30:
                        continue
                    b, G = lib.boot({"g": (q[f"{s}_ls_{cand}"] - q[f"{s}_ls_live"]).values}, q.match, b=1000)
                    flag = b["g"][2] < 0
                    if grp != "model":          # registered G4 subgroups: depth, tier (scope / stat = the cells); model = unregistered screen
                        worst |= flag
                    sub[f"{grp}={lv}"] = {"gain": b["g"], "rows": int(len(q)), "matches": G, "flag": bool(flag)}
                    md.append(f"| {cand} | {stat} | {sc} | {grp} | {lv} | {len(q)} | {G} | {f3(b['g'])} | {('WORSE' if grp != 'model' else 'worse (unregistered screen)') if flag else ''} |")
            # synthetic line: log loss / Brier / calibration by side
            y = d[f"{s}_over"].values
            pl, pc = d[f"{s}_p_live"].values, d[f"{s}_p_{cand}"].values
            bl, _ = lib.boot({"ll": ll(pl, y) - ll(pc, y), "br": (pl - y) ** 2 - (pc - y) ** 2, "over": y, "p_live": pl, "p_cand": pc}, d.match)
            sa, pfa, ha = side_cols(pl, y)
            sb, pfb, hb = side_cols(pc, y)
            cal = lib.boot_absgap(pl, pc, ha, hb, pfa, pfb, sa, sb, d.match.values)
            calflag = any(v["lo"] > 0 for v in cal.values())
            pit = {}
            for nm in ("live", cand):
                z = d[f"{s}_pit_{nm}"].values
                pit[nm] = {"c50": float(((z > 0.25) & (z < 0.75)).mean()), "c80": float(((z > 0.1) & (z < 0.9)).mean()),
                           "c95": float(((z > 0.025) & (z < 0.975)).mean()), "below_half": float((z < 0.5).mean())}
            R["hltv"][key].update({"subgroups": sub, "subgroup_flag": bool(worst), "synthetic_line": bl, "calibration_by_side": cal,
                                   "cal_flag": bool(calflag), "pit": pit})
            if cand == "th1" and stat == "kills" and sc == "12":
                q = H[(H.scope == "12") & H.p2chk_ls_live.notna()]
                b, G = lib.boot({"g": (q.p2chk_ls_th1 - q.p2chk_ls_live).values}, q.match)
                R["hltv"][key]["perf2_maps12_bands_check"] = {"gain": b["g"], "rows": int(len(q)), "matches": G}

md.append("\n## HLTV synthetic line floor(projection)+0.5: line log-loss gain, Brier gain, over rate vs mean chance, calibration |gap| by favoured side (cand - live), PIT coverage\n")
md.append("| cand | stat | scope | d log loss | d Brier | over rate | mean p live | mean p cand | Over-fav |gap| live -> cand (diff 95%) | Under-fav |gap| live -> cand (diff 95%) | PIT 50/80/95 live | cand |")
md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for key, v in R["hltv"].items():
    cand, stat, sc = key.split("|")
    sl, cal, pit = v["synthetic_line"], v["calibration_by_side"], v["pit"]
    cs = lambda k: f"{100*cal[k]['gap_live']:.1f} -> {100*cal[k]['gap_cand']:.1f} ({100*cal[k]['diff']:+.1f} [{100*cal[k]['lo']:+.1f}, {100*cal[k]['hi']:+.1f}]; n {cal[k]['n_live']}/{cal[k]['n_cand']})"
    pp = lambda q: f"{100*q['c50']:.1f}/{100*q['c80']:.1f}/{100*q['c95']:.1f}"
    md.append(f"| {cand} | {stat} | {sc} | {f3(sl['ll'])} | {f3(sl['br'])} | {100*sl['over'][0]:.1f}% | {100*sl['p_live'][0]:.1f}% | {100*sl['p_cand'][0]:.1f}% | {cs('Over')} | {cs('Under')} | {pp(pit['live'])} | {pp(pit[cand])} |")

# dependence across overlapping scopes (kills, same player-series): Spearman of PIT(Map 1), PIT(Maps 1-2)
a = H[H.scope == "1"][["match", "player", "k_pit_live", "k_pit_rm1", "k_pit_th1"]]
b = H[H.scope == "12"][["match", "player", "k_pit_live", "k_pit_rm1", "k_pit_th1"]]
j = a.merge(b, on=["match", "player"], suffixes=("_1", "_12"))
dep = {nm: float(spearmanr(j[f"k_pit_{nm}_1"], j[f"k_pit_{nm}_12"]).correlation) for nm in ("live", "rm1", "th1")}
R["dependence_pit_spearman_map1_vs_maps12"] = dict(dep, pairs=int(len(j)), matches=int(j.match.nunique()))
md.append(f"\nDependence (kills, {len(j)} player-series in both scopes / {j.match.nunique()} m): Spearman PIT(Map 1) vs PIT(Maps 1-2) live {dep['live']:.4f}, rm-1 {dep['rm1']:.4f}, th-1 {dep['th1']:.4f}\n")

# ================================================================ real lines
P = pd.read_csv(C / "p1_cands.csv.gz")
P["scope"] = P.scope.astype(str)
P["depth5"] = pd.cut(P.depth, [-1, 50, 150, 500, 1500, 1e12], right=False, labels=["<50", "50-150", "150-500", "500-1500", "1500+"]).astype(str)
y = P.over.values.astype(float)


def p1_block(q, cand):
    y = q.over.values.astype(float)
    pl, pc, pr = q.p_over.values, q[f"p_{cand}"].values, q.p_replay.values
    sl, pfl, hl = side_cols(pl, y)
    sc_, pfc, hc = side_cols(pc, y)
    flip = sl != sc_
    b, G = lib.boot({"ll": ll(pl, y) - ll(pc, y), "br": (pl - y) ** 2 - (pc - y) ** 2, "ll_vs_replay": ll(pr, y) - ll(pc, y),
                     "ll_live": ll(pl, y), "ll_cand": ll(pc, y), "over": y, "p_live": pl, "p_cand": pc,
                     "hit_live": hl, "hit_cand": hc, "g_same": np.where(~flip, ll(pl, y) - ll(pc, y), 0.0),
                     "g_flip": np.where(flip, ll(pl, y) - ll(pc, y), 0.0)}, q.match)
    cal = lib.boot_absgap(pl, pc, hl, hc, pfl, pfc, sl, sc_, q.match.values)
    return {"units": int(len(q)), "matches": G, **b, "flipped_units": int(flip.sum()),
            "p55_live": int((pfl >= 0.55).sum()), "p55_cand": int((pfc >= 0.55).sum()),
            "over_fav_live": int((sl == "Over").sum()), "over_fav_cand": int((sc_ == "Over").sum()), "calibration_by_side": cal}


md.append("\n## Real offered lines (P1 units, start < 2026-10-22; F6 fit): paired log-loss gain of P(Over | no push) vs the LOGGED live chance (+ = candidate better)\n")
md.append("| cand | set | units | matches | d log loss 95% | d Brier | vs replay | over rate | mean p live / cand | fav hit live / cand | flipped | gain on same-side / flipped | 55%+ live / cand | Over-fav live / cand |")
md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for cand in ("rm1", "th1"):
    Q = P if cand == "rm1" else P[P.th_in]
    sets = {"all": Q}
    for stat in ("kills", "hs"):
        sets[stat] = Q[Q.prop == stat]
        for sc in ("1", "12", "123"):
            sets[f"{stat}_{sc}"] = Q[(Q.prop == stat) & (Q.scope == sc)]
    for nm, q in sets.items():
        if len(q) == 0:
            continue
        r = p1_block(q, cand)
        R["p1"][f"{cand}|{nm}"] = r
        md.append(f"| {cand} | {nm} | {r['units']} | {r['matches']} | {f3(r['ll'])} | {f3(r['br'])} | {r['ll_vs_replay'][0]:+.4f} | {100*r['over'][0]:.1f}% | "
                  f"{100*r['p_live'][0]:.1f} / {100*r['p_cand'][0]:.1f} | {100*r['hit_live'][0]:.1f} / {100*r['hit_cand'][0]:.1f} | {r['flipped_units']} | "
                  f"{r['g_same'][0]:+.4f} / {r['g_flip'][0]:+.4f} | {r['p55_live']} / {r['p55_cand']} | {r['over_fav_live']} / {r['over_fav_cand']} |")
    # subgroups
    for grp in ("scope", "prop", "depth5", "tier", "source"):
        for lv, q in Q.groupby(grp):
            if q.match.nunique() < 5:
                continue
            yy = q.over.values.astype(float)
            b, G = lib.boot({"ll": ll(q.p_over.values, yy) - ll(q[f"p_{cand}"].values, yy)}, q.match, b=1000)
            R["p1"][f"{cand}|sub|{grp}={lv}"] = {"units": int(len(q)), "matches": G, "ll": b["ll"], "flag": bool(b["ll"][2] < 0)}

md.append("\n## Real-line subgroups (d log loss 95%, B=1000; flag = interval entirely < 0)\n")
md.append("| cand | subgroup | units | matches | d log loss | flag |")
md.append("|---|---|---|---|---|---|")
for k, v in R["p1"].items():
    if "|sub|" in k:
        c_, _, g_ = k.split("|")
        md.append(f"| {c_} | {g_} | {v['units']} | {v['matches']} | {f3(v['ll'])} | {'WORSE' if v['flag'] else ''} |")
md.append("\n## Real-line calibration by favoured side (|mean chance - hit rate|, live -> candidate, difference 95%)\n")
for k, v in R["p1"].items():
    if "|sub|" not in k and k.split("|")[1] in ("all", "kills", "hs"):
        cal = v["calibration_by_side"]
        md.append(f"- {k}: " + "; ".join(f"{s} fav {100*cal[s]['gap_live']:.1f} -> {100*cal[s]['gap_cand']:.1f} ({100*cal[s]['diff']:+.1f} [{100*cal[s]['lo']:+.1f}, {100*cal[s]['hi']:+.1f}], n {cal[s]['n_live']}/{cal[s]['n_cand']})" for s in ("Over", "Under")))

# ================================================================ gate
md.append("\n## Registered development gate per cell\n")
md.append("| cand | cell | G1 every fold | G2 Bonferroni | G3 real-line point (cell units / matches) | G4 subgroups + calibration | G5 coverage | PASS |")
md.append("|---|---|---|---|---|---|---|---|")
for cand in ("rm1", "th1"):
    passing = []
    for stat in ("kills", "hs"):
        for sc in ("1", "12", "123"):
            h = R["hltv"][f"{cand}|{stat}|{sc}"]
            pr = R["p1"].get(f"{cand}|{stat}_{sc}")
            g3c = True if pr is None or pr["units"] < 100 or pr["matches"] < 15 else pr["ll"][0] >= 0
            g4 = not h["subgroup_flag"] and not h["cal_flag"]
            ok = h["G1"] and h["G2"] and g3c and g4
            R["gate"][f"{cand}|{stat}|{sc}"] = {"G1": h["G1"], "G2": h["G2"], "G3_cell": bool(g3c), "G4_hltv": bool(g4), "G5": True, "pre_pool_pass": bool(ok)}
            if ok:
                passing.append((stat, sc))
            p_s = "-" if pr is None else f"{pr['ll'][0]:+.4f} ({pr['units']} / {pr['matches']})"
            md.append(f"| {cand} | {stat} {sc} | {h['G1']} | {h['G2']} | {p_s} {'ok' if g3c else 'FAIL'} | {'ok' if g4 else 'FAIL'} | ok | {ok} |")
    # G3 pooled + G4 real lines on the passing cells
    Q = P if cand == "rm1" else P[P.th_in]
    sel = np.zeros(len(Q), bool)
    for stat, sc in passing:
        sel |= ((Q.prop == stat) & (Q.scope == sc)).values
    q = Q[sel]
    res = {"passing_cells_hltv": passing, "units": int(len(q))}
    if len(q):
        r = p1_block(q, cand)
        st_ok = all(p1_block(q[q.prop == s_], cand)["ll"][0] >= 0 for s_ in q.prop.unique())
        sub_bad = []
        for grp in ("scope", "prop", "depth5", "tier"):
            for lv, qq in q.groupby(grp):
                if qq.match.nunique() < 5:
                    continue
                yy = qq.over.values.astype(float)
                bq, _ = lib.boot({"ll": ll(qq.p_over.values, yy) - ll(qq[f"p_{cand}"].values, yy)}, qq.match, b=1000)
                if bq["ll"][2] < 0:
                    sub_bad.append(f"{grp}={lv}")
        br_bad = r["br"][2] < 0
        cal_bad = any(v["lo"] > 0 for v in r["calibration_by_side"].values())
        res.update({"pooled_ll": r["ll"], "per_stat_ok": bool(st_ok), "subgroups_worse": sub_bad, "brier": r["br"], "brier_worse": bool(br_bad),
                    "cal_worse": bool(cal_bad), "PASS": bool(r["ll"][0] >= 0 and st_ok and not sub_bad and not br_bad and not cal_bad)})
    else:
        res["PASS"] = False
    R["gate"][f"{cand}|final"] = res
    md.append(f"\n**{cand} final**: {json.dumps(res, default=str)}\n")
json.dump(R, open(C / "report.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
open(C / "tables.md", "w", encoding="utf-8").write("\n".join(md) + "\n")
print("\n".join(md[-12:]))
