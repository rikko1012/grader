"""Markdown tables for RESULTS.md from artifacts/test_results.json and artifacts/diagnosis.json (no new numbers).
usage: python tables.py <artifacts_dir>  -> <artifacts_dir>/tables.md"""
import json
import sys
from pathlib import Path

A = Path(sys.argv[1])
T = json.loads((A / "test_results.json").read_text(encoding="utf-8"))
D = json.loads((A / "diagnosis.json").read_text(encoding="utf-8"))
NAMES = {"C0": "live", "C1": "isotonic", "C2": "Platt", "C3": "spread inflation", "C4": "mean-bias", "C5": "gradecore/calibrate.py",
         "C6": "per-side spread"}
pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"
ci = lambda c, f=1: "-" if not c else f"({c[0] * f:+.4f}, {c[1] * f:+.4f})"
cip = lambda c: "-" if not c else f"{c[0] * 100:.1f}-{c[1] * 100:.1f}%"
L = []
for sport, S in T["sports"].items():
    L.append(f"\n## {sport.upper()}\n")
    L.append(f"Development {S['dev']['n']} units / {S['dev']['matches']} matches; confirmation {S['conf']['n']} units / "
             f"{S['conf']['matches']} matches; selected: **{S['selected']} {NAMES.get(S['selected'], '')}**; passed: **{S['passed']}**\n")
    L.append("| candidate | dev log-loss gain (95% CI) | dev Brier gain (95% CI) | conf log-loss gain (95% CI) | conf Brier gain (95% CI) | conf favoured stated / hit | Over share / Over rate |")
    L.append("|---|---|---|---|---|---|---|")
    for c in ("C0", "C1", "C2", "C3", "C4", "C5", "C6"):
        a = S["conf"]["arms"][c]
        d = S["dev"]["cands"].get(c) or {}
        p = a.get("paired") or {}
        L.append(f"| {c} {NAMES[c]}{' (selected)' if c == S['selected'] else ''} | "
                 + (f"{d['logloss_gain']:+.4f} {ci(d['logloss_gain_ci'])} | {d['brier_gain']:+.4f} {ci(d['brier_gain_ci'])} | " if d else "- | - | ")
                 + (f"{p['logloss_gain']:+.4f} {ci(p['logloss_gain_ci'])} | {p['brier_gain']:+.4f} {ci(p['brier_gain_ci'])} | " if p else "- | - | ")
                 + f"{pct(a['in_large']['stated'])} / {pct(a['in_large']['hit'])} | {pct(a['balance']['share_over'])} / {pct(a['balance']['over_rate'])} |")
    if S["pass_checks"]:
        L.append("\nPass checks (selected candidate): " + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in S["pass_checks"].items()))
    L.append("\nConfirmation, Results cohort (the member rule applied to each arm's own chance):\n")
    L.append("| arm | picks | matches | stated | hit (95% CI) | hit - stated CI | letters |")
    L.append("|---|---|---|---|---|---|---|")
    order = ["A+", "A", "A-", "B+", "B", "B-", "Lean", "C"]
    for c in ("C0", "C1", "C2", "C3", "C4", "C5", "C6"):
        a = S["conf"]["arms"][c]
        r = a["results_cohort"]
        lt = ", ".join(f"{k} {a['results_letters'][k]}" for k in order if k in a["results_letters"])
        L.append(f"| {c} {NAMES[c]} | {r['n']} | {r.get('matches', '-')} | {pct(r.get('stated'))} | {pct(r.get('hit'))} ({cip(r.get('hit_ci'))}) | "
                 f"{ci(r.get('hit_minus_stated_ci'))} | {lt} |")
    L.append("\nConfirmation, every scored unit, letter of the favoured side:\n")
    L.append("| arm | " + " | ".join(order) + " |")
    L.append("|---|" + "---|" * len(order))
    for c in ("C0", "C1", "C2", "C3", "C4", "C5", "C6"):
        la = S["conf"]["arms"][c]["letters_all"]
        L.append(f"| {c} {NAMES[c]} | " + " | ".join(str(la.get(k, 0)) for k in order) + " |")
    L.append("\nConfirmation reliability, 5-point bins of the favoured-side chance (live vs selected):\n")
    for c in ("C0", S["selected"]) if S["selected"] else ("C0",):
        L.append(f"\n{c} {NAMES[c]}:\n\n| bin | n | stated | hit (95% CI) |\n|---|---|---|---|")
        for b in S["conf"]["arms"][c]["reliability"]:
            if b["n"]:
                L.append(f"| {b['bin']}% | {b['n']} | {pct(b['stated'])} | {pct(b['hit'])} ({cip(b['hit_ci'])}) |")
for sport, R in D.items():
    L.append(f"\n## Diagnosis {sport.upper()} (all {R['units']} scored units)\n")
    L.append("| slice | n | bias proj-actual (95% CI) | gap slope beta (95% CI) | resid sd / model sd | probit shift a | probit slope b | MAE proj / line | Over rate |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for k, m in R["mechanisms"].items():
        items = [("all", m)] if k == "all" else [(f"{k}: {kk}", mm) for kk, mm in m.items()]
        for name, mm in items:
            if mm.get("n", 0) < 30 or "gap_slope_beta" not in mm:
                continue
            L.append(f"| {name} | {mm['n']} | {mm['bias_proj_minus_actual']:+.2f} {ci(mm['bias_ci'])} | {mm['gap_slope_beta']:.2f} {ci(mm['gap_slope_ci'])} | "
                     f"{mm['resid_sd_over_model_sd']:.2f} | {mm['probit_shift_a']:+.2f} | {mm['probit_slope_b']:.2f} | {mm['mae_projection']:.2f} / {mm['mae_line']:.2f} | {pct(mm['over_rate'])} |")
    for tgt, title in (("slices_results_cohort", "Results cohort"), ("slices_all_units", "all scored units")):
        L.append(f"\n{title}, stated vs hit by slice (slices with 10+ units):\n")
        L.append("| slice | group | n | matches | stated | hit | overstated by | hit - stated 95% CI |")
        L.append("|---|---|---|---|---|---|---|---|")
        for name, g in R[tgt].items():
            for k, v in g.items():
                L.append(f"| {name} | {k} | {v['n']} | {v['matches']} | {pct(v['stated'])} | {pct(v['hit'])} | {v['over_by'] * 100:+.1f} | {cip(v['hit_minus_stated_ci'])} |")
    if "raw_vs_stated" in R:
        rv = R["raw_vs_stated"]
        L.append(f"\nRaw (before caps / margins) vs stated, CS2, {rv['n']} units with a saved raw chance: favoured stated "
                 f"{pct(rv['stated']['stated'])} hit {pct(rv['stated']['hit'])}; raw {pct(rv['raw']['stated'])} hit {pct(rv['raw']['hit'])}; "
                 f"log loss stated {rv['logloss_stated']} vs raw {rv['logloss_raw']}; mean |stated - raw| {rv['mean_abs_diff']}.")
(A / "tables.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print("written", A / "tables.md", len(L), "lines")
