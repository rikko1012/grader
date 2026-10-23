"""ED-1: render the JSON results as the markdown tables of ERROR_BREAKDOWN.md (appendix). usage: python report_tables.py <out dir> > tables.md"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
H = json.load(open(OUT / "hltv_decomp.json"))
P = json.load(open(OUT / "predict.json"))
D = json.load(open(OUT / "dist_shape.json"))
Q = json.load(open(OUT / "p1_decomp.json"))
SCN = {"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"}
FN = {"Rreg": "rounds: regulation length", "Rot": "rounds: overtime", "Tw": "team kills/round via round-win share",
      "Tr": "team kills/round, rest", "S": "share of team kills", "H": "HS per kill"}


def ci(t, d=2, pct=False, sign=True):
    if t is None:
        return "-"
    a, lo, hi = (100 * x for x in t) if pct else t
    s = "+" if sign else ""
    return f"{a:{s}.{d}f} [{lo:{s}.{d}f}, {hi:{s}.{d}f}]"


print("### A1. Population (a) HLTV 2y: baseline error of the live formula\n")
print("| stat | scope | model | rows | matches | mean actual | mean proj | bias (act-proj) | MAE | MSE | log score live (nats) |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for sc in ("1", "12", "123"):
    for st in ("kills", "hs"):
        x = H[sc][st]
        b = x["baseline"]
        mix = ", ".join(f"{k} {v}" for k, v in H[sc]["model_mix"].items())
        print(f"| {st} | {SCN[sc]} | {mix} | {x['rows']} | {x['matches']} | {b['mean_actual']:.2f} | {b['mean_proj']:.2f} | {ci(b['bias'],3)} | "
              f"{ci(b['mae'],3,sign=False)} | {ci(b['mse'],2,sign=False)} | {x['logloss_live_exact']:.4f} |")

print("\n### A2. Oracle decomposition (population a): share of squared error removed by each component (Shapley over all orders; one-at-a-time; chained in the listed order), MAE removed (Shapley, kills), bias contribution (Shapley)\n")
for st in ("kills", "hs"):
    print(f"\n**{st}**\n")
    print("| scope | component | Shapley % of MSE | one-at-a-time % | chained % | Shapley MAE removed | Shapley bias part | sd of log ratio |")
    print("|---|---|---|---|---|---|---|---|")
    for sc in ("1", "12", "123"):
        x = H[sc][st]
        for k, v in x["factors"].items():
            print(f"| {SCN[sc]} | {FN[k]} | {ci(v['sh_se_share'],1,True,False)} | {ci(v['one_se_share'],1,True)} | {ci(v['ch_se_share'],1,True)} | "
                  f"{ci(v['sh_ae'],3)} | {ci(v['sh_b'],3)} | {v['sd_log_ratio']:.3f} |")
    print(f"\n({x['rows']} rows / {x['matches']} matches for Maps 1-3; Map 1 {H['1'][st]['rows']} / {H['1'][st]['matches']}; Maps 1-2 {H['12'][st]['rows']} / {H['12'][st]['matches']})")

print("\n### A3. Log score with the spread re-fitted after each oracle substitution (population a, nats per player-series; c = ML spread multiplier)\n")
print("| stat | scope | none | " + " | ".join(["Rreg", "Rot", "Tw", "Tr", "S", "H", "Rot+Rreg", "Rot+Rreg+Tw", "Rot+Rreg+Tr+Tw"]) + " |")
print("|---|---|---" + "|---" * 9 + "|")
for st in ("kills", "hs"):
    for sc in ("1", "12", "123"):
        ll = H[sc][st]["logloss_refit"]
        cells = [f"{ll[k]['nll']:.3f} (c {ll[k]['c']:.2f})" if k in ll else "-" for k in ["none", "Rreg", "Rot", "Tw", "Tr", "S", "H", "Rot+Rreg", "Rot+Rreg+Tw", "Rot+Rreg+Tr+Tw"]]
        print(f"| {st} | {SCN[sc]} | " + " | ".join(cells) + " |")

print("\n### A4. Breakdowns (population a): bias and MAE with match-grouped 95% CI, Shapley share of MSE by component (point; CI in hltv_decomp.json)\n")
for key, title in (("model", "model (cm-2 vs perf-2 fallback; perf-2 here = full-history replay, NOT the live N0 pin)"), ("depth", "player history depth (weighted rounds)"),
                   ("env", "environment"), ("ot_outcome", "overtime outcome - RETROSPECTIVE explanation only, NOT evidence OT was predictable"),
                   ("tier", "tier (Elo fifth of matches, 1 = lowest)")):
    print(f"\n**{title}**\n")
    print("| stat | scope | group | rows | matches | bias | MAE | rounds share | team share | share-of-team share | HS/kill share |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for st in ("kills", "hs"):
        for sc in ("1", "12", "123"):
            for g, r in sorted(H[sc][st]["stability"][key].items()):
                rs = r.get("share_Rreg", 0) + r.get("share_Rot", 0)
                ts = r.get("share_Tw", 0) + r.get("share_Tr", 0)
                print(f"| {st} | {SCN[sc]} | {g} | {r['rows']} | {r['matches']} | {ci(r.get('bias_ci'),2)} | {ci(r.get('mae_ci'),2,sign=False)} | "
                      f"{100*rs:.1f}% | {100*ts:.1f}% | {100*r['share_S']:.1f}% | {100*r['share_H']:.1f}% |" if "share_H" in r else
                      f"| {st} | {SCN[sc]} | {g} | {r['rows']} | {r['matches']} | {ci(r.get('bias_ci'),2)} | {ci(r.get('mae_ci'),2,sign=False)} | "
                      f"{100*rs:.1f}% | {100*ts:.1f}% | {100*r['share_S']:.1f}% | - |")

print("\n**by month (kills, point estimates; rows / matches)**\n")
print("| scope | month | rows | matches | bias | MAE | rounds share | team share | share-of-team share |")
print("|---|---|---|---|---|---|---|---|---|")
for sc in ("1", "12", "123"):
    for g, r in sorted(H[sc]["kills"]["stability"]["month"].items()):
        print(f"| {SCN[sc]} | {g} | {r['rows']} | {r['matches']} | {ci(r.get('bias_ci'),2)} | {r['mae']:.2f} | {100*(r['share_Rreg']+r['share_Rot']):.1f}% | "
              f"{100*(r['share_Tw']+r['share_Tr']):.1f}% | {100*r['share_S']:.1f}% |")

print("\n### A5. Predictability (forward quarterly folds 2025-07..2026-10-21, pre-match features only): % of MSE removed by multiplying the live projection by the PREDICTED component ratio vs by the REALIZED ratio (oracle)\n")
print("| stat | scope | component | rows | matches | predicted: % MSE removed | MAE removed (kills) | oracle: % MSE removed | corr(pred, realized) |")
print("|---|---|---|---|---|---|---|---|---|")
for sc in ("1", "12", "123"):
    r = P[sc]
    for c, nm in (("R", "rounds (total)"), ("Rreg", "rounds: regulation"), ("Rot", "rounds: overtime"), ("T", "team kills per round"), ("S", "share of team kills"),
                  ("tot", "TOTAL ratio (level + variation)"), ("totc", "TOTAL ratio, variation only")):
        k = r["kills"]
        print(f"| kills | {SCN[sc]} | {nm} | {r['rows']} | {r['matches']} | {ci(k['fix_share_'+c],2,True)} | {ci(k['fix_ae_'+c],3)} | {ci(k['orc_share_'+c],1,True)} | {r['corr_pred_real_'+c]:.3f} |")
    h = r["hs"]
    for c, nm in (("H", "HS per kill"), ("R", "rounds (total)"), ("totH", "TOTAL HS ratio")):
        print(f"| hs | {SCN[sc]} | {nm} | {h['rows']} | {h['matches']} | {ci(h['fix_share_'+c],2,True)} | {ci(h['fix_ae_'+c],3)} | {ci(h['orc_share_'+c],1,True)} | - |")
print("\nTop permutation importances (2026-07 fold, increase in weighted MSE of the ratio):\n")
for sc, v in P["importance_2026Q3"].items():
    for k, l in v.items():
        print(f"- {SCN[sc]} {k}: " + ", ".join(f"{a} {b:.1e}" for a, b in l[:5]))

print("\n### A6. Distribution shape (population a): chance the actual exceeds the projection vs the live distribution's chance (normal) and a same-mean same-variance gamma (no fitted parameter)\n")
print("| stat | scope | rows | matches | P(actual > proj) | live normal | gap (normal) | gamma | gap (gamma) | log-score gain gamma vs normal (nats) |")
print("|---|---|---|---|---|---|---|---|---|---|")
for k, v in D["a"].items():
    st, sc = k.split("_")
    g = ci([-x for x in v["d_nll"]][::-1] if False else [-v["d_nll"][0], -v["d_nll"][2], -v["d_nll"][1]], 4) if "d_nll" in v else "-"
    print(f"| {st} | {SCN[sc]} | {v['rows']} | {v['matches']} | {ci(v['above'],1,True,False)} | {100*v['p_normal'][0]:.1f}% | {ci(v['gap_normal'],1,True)} | "
          f"{100*v['p_gamma'][0]:.1f}% | {ci(v['gap_gamma'],1,True)} | {g} |")
for st in ("kills", "hs"):
    for sc in ("1", "12", "123"):
        x = H[sc][st]["dist"]
        cov = x["coverage"]
        print(f"- {st} {SCN[sc]}: PIT deciles {x['pit_deciles']}; central 50/80/95% coverage {ci(cov['50'],1,True,False)} / {ci(cov['80'],1,True,False)} / {ci(cov['95'],1,True,False)}"
              + (f"; z sd {x['z_sd']:.3f}, skew {x['z_skew']:.2f}, excess kurtosis {x['z_exkurt']:.2f}" if "z_sd" in x else ""))
print("\nSpread by group (ML c of the live discretised normal; live c 1.442 Map 1 / 1.476 cm-2 Maps 1-2 / 1.457 Maps 1-3):\n")
for sc in ("1", "12", "123"):
    for nm, gg in H[sc]["kills"]["spread_by"].items():
        print(f"- {SCN[sc]} {nm}: " + "; ".join(f"{g}: c {r['c_fit']:.3f}, bias {r['bias']:+.2f} ({r['rows']} rows)" for g, r in gg.items()))

print("\n### B1. Population (b) real P1 lines (start < 2026-10-22 00:00 UTC): coverage and data checks\n")
print(f"Units {Q['units_pre_cutoff']} / {Q['matches_pre_cutoff']} matches. " + json.dumps(Q["issues"]))
print("\n### B2. Calibration of the live chance at the line\n")
print("| set | units | matches | mean P(over) | over rate | favoured-side hit | printed | log loss | log loss at 50% | slope | intercept |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in Q["calibration_live"].items():
    print(f"| {k} | {v['units']} | {v['matches']} | {100*v['p_over'][0]:.1f}% | {ci(v['over'],1,True,False)} | {ci(v['hit'],1,True,False)} | {100*v['printed'][0]:.1f}% | "
          f"{ci(v['ll'],4,sign=False)} | 0.6931 | {ci(v['slope'],2)} | {ci(v['intercept'],3)} |")
print("\nReliability bins (all units): " + "; ".join(f"{b['bin']}: {b['units']} u / {b['matches']} m, p {b['mean_p_over']:.3f} vs {b['over_rate']:.3f}" for b in Q["calibration_live"]["all"]["bins"]))

print("\n### B3. Oracle decomposition of the SAVED projection on real lines + line-level log loss with each oracle substitution (spread from population a)\n")
print("| stat scope | units | matches | sources | MSE | bias | Shapley % MSE: Rreg / Rot / Tw / Tr / S / H | d log loss vs normal baseline: rounds (R) | Tw | S | coverage 50/80/95 |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in Q["decomp"].items():
    sh = " / ".join(f"{100*v['sh_se_share_'+f][0]:.0f}" if "sh_se_share_" + f in v else "-" for f in ("Rreg", "Rot", "Tw", "Tr", "S", "H"))
    print(f"| {k} | {v['units']} | {v['matches']} | {v['sources']} | {v['se0'][0]:.1f} | {ci(v['bias0'],2)} | {sh} | {ci(v['dll_one_R'],3)} | "
          f"{ci(v['dll_one_Tw'],3)} | {ci(v['dll_one_S'],3)} | {100*v['cov50'][0]:.0f}/{100*v['cov80'][0]:.0f}/{100*v['cov95'][0]:.0f}% |")
print("\nFixable at the line (saved projection x forward-predicted ratio; d log loss vs the normal baseline, + = better):\n")
for k, v in Q["decomp"].items():
    print(f"- {k} ({v['units']} u / {v['matches']} m; rows with a prediction {v['fix_rows_with_pred']}): " +
          "; ".join(f"{c}: {ci(v['dll_fix_'+c],4)}" for c in ("R", "T", "S", "tot", "H", "totH") if "dll_fix_" + c in v))
print("\n### B4. Real-line breakdowns (every pre-cutoff unit; live chance; decomposition shares where HLTV realized exists)\n")
for key, title in (("version", "model version tag"), ("history", "data source / history revision"), ("stat_scope", "stat x scope"), ("depth", "history depth (weighted rounds)"),
                   ("env", "environment"), ("ot_outcome", "overtime outcome - RETROSPECTIVE explanation only, NOT evidence OT was predictable"), ("book", "book")):
    print(f"\n**{title}**\n")
    print("| group / stat | units | matches | bias (act-proj) | MAE | favoured hit | printed | log loss | over rate | mean P(over) | % MSE rounds / team / share / HS-kill |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for g, r in sorted(Q["breakdowns"][key].items()):
        if "note" in r:
            print(f"| {g} | {r['units']} | {r['matches']} | {r['note']} |||||||| ")
            continue
        sh = " / ".join(f"{100*r[k][0]:.0f}" if k in r else "-" for k in ("share_rounds", "share_team", "share_share", "share_hs_per_kill"))
        print(f"| {g} | {r['units']} | {r['matches']} | {ci(r['bias'],2)} | {ci(r['ae'],2,sign=False)} | {ci(r['hit'],1,True,False)} | {100*r['printed'][0]:.1f}% | "
              f"{ci(r['ll'],3,sign=False)} | {ci(r['over'],1,True,False)} | {100*r['p_over'][0]:.1f}% | {sh} |")
print("\n### B5. Distribution shape at the real lines (same saved mean and spread; gamma vs the live normal; + = gamma better)\n")
print("| set | units | matches | over rate | live P(over) | gamma P(over) | d log loss | d Brier |")
print("|---|---|---|---|---|---|---|---|")
for k, v in D["p1"].items():
    pl = v.get("p_live")
    dl = v.get("d_ll") or v.get("d_ll_gamma_vs_live")
    db = v.get("d_br") or v.get("d_br_gamma_vs_live")
    print(f"| {k} | {v['units']} | {v['matches']} | {ci(v['over'],1,True,False)} | {100*pl[0]:.1f}% | {100*v['p_gamma'][0]:.1f}% | {ci(dl,4)} | {ci(db,4)} |")
print("\n### B6. Freshness (lead = start - forecast time; quote age = forecast time - book line update)\n")
for nm, gg in Q["freshness"].items():
    for g, r in gg.items():
        if g == "missing":
            print(f"- {nm} missing: {r}")
            continue
        print(f"- {nm} {g}: {r['units']} u / {r['matches']} m, log loss {ci(r['ll'],3,sign=False)}, favoured hit {ci(r['hit'],1,True,False)}, printed {100*r['printed'][0]:.1f}%, bias {ci(r['bias'],2)}")
