"""cal-cs2lol-1: the registered rolling-origin test (REGISTER.md). Development segment -> selection rule -> the one
selected candidate per sport judged on the confirmation segment by the pass rule; all others descriptive.

usage: python run_test.py <data_dir> <out_json>
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calib as C  # noqa: E402

data, out = sys.argv[1], Path(sys.argv[2])
res = {"register": "research/calibration_2026_11_05/REGISTER.md", "seed": C.SEED, "boot": C.B, "sports": {}}
for sport, loader in (("cs2", C.load_cs2), ("lol", C.load_lol)):
    units = loader(data)
    S = res["sports"][sport] = {"units_total": len(units)}
    dev, dev_fits = C.rolling(units, sport, "dev")
    conf, conf_fits = C.rolling(units, sport, "conf")
    S["fits"] = {"dev": dev_fits, "conf": conf_fits}
    # ---- development: selection only
    S["dev"] = {"n": len(dev), "matches": len({u["cluster"] for u in dev}),
                "cands": {c: C.paired(dev, c) for c in C.CANDS}}
    ok = [(S["dev"]["cands"][c]["logloss_gain"], c) for c in C.CANDS
          if S["dev"]["cands"][c]["logloss_gain"] > 0 and S["dev"]["cands"][c]["brier_gain"] > 0]
    sel = max(ok)[1] if ok else None
    S["selected"] = sel
    # ---- confirmation: every candidate reported; only the selected one is judged
    S["conf"] = {"n": len(conf), "matches": len({u["cluster"] for u in conf}), "over_rate": C.balance(conf, "C0")["over_rate"], "arms": {}}
    for c in ("C0",) + C.CANDS:
        arm = {"name": C.NAMES[c], "in_large": C.in_large(conf, c), "balance": C.balance(conf, c),
               "reliability": C.reliability(conf, c), "letters_all": C.letters(conf, c)}
        if c != "C0":
            arm["paired"] = C.paired(conf, c)
            arm["by_prop"] = {p: C.paired([u for u in conf if u["prop"] == p], c) for p in sorted({u["prop"] for u in conf})}
            arm["by_side_live"] = {s: C.paired([u for u in conf if C.side_of(u["p"]) == s], c) for s in ("Over", "Under")}
        ps = C.results_picks(conf, c)
        arm["results_cohort"] = C.picks_summary(ps)
        arm["results_letters"] = dict(Counter(x["letter"] for x in ps))
        arm["results_by_side"] = {s: C.picks_summary([x for x in ps if x["side"] == s]) for s in ("Over", "Under")}
        S["conf"]["arms"][c] = arm
    # live picks re-stated by each candidate (same picks, candidate's chance): calibration on the live Results cohort
    live_ps = C.results_picks(conf, "C0")
    S["conf"]["live_picks_restated"] = {}
    for c in C.CANDS:
        rs = []
        for x in live_ps:
            u = x["u"]
            pc = u["pc"][c]
            st = (pc if x["side"] == "Over" else 1 - pc) * (1 - u["push_p"])
            rs.append({"cluster": x["cluster"], "stated": st, "hit": x["hit"], "side": x["side"]})
        S["conf"]["live_picks_restated"][c] = C.picks_summary(rs)
    # ---- pass rule (selected only)
    if sel:
        a = S["conf"]["arms"][sel]
        pr = a["paired"]
        rc = a["results_cohort"]
        props_bad = [p for p, v in a["by_prop"].items() if v["n"] >= 100 and v["logloss_gain_ci"] and v["logloss_gain_ci"][1] < 0]
        chk = {"1_logloss_ci_above_0": bool(pr["logloss_gain_ci"] and pr["logloss_gain_ci"][0] > 0),
               "2_brier_ci_above_0": bool(pr["brier_gain_ci"] and pr["brier_gain_ci"][0] > 0),
               "3_balance": a["balance"]["ok"],
               "4_no_prop_significantly_worse": not props_bad,
               "5a_results_cohort_ci_contains_0": bool(rc.get("hit_minus_stated_ci") and rc["hit_minus_stated_ci"][0] <= 0 <= rc["hit_minus_stated_ci"][1]),
               "5b_in_large_within_3pts": abs(a["in_large"]["gap"]) <= 0.03}
        S["pass_checks"] = chk
        S["passed"] = all(chk.values())
    else:
        S["pass_checks"], S["passed"] = None, False
    for u in units:
        u.pop("pc", None)
out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")

for sport, S in res["sports"].items():
    print(f"\n===== {sport.upper()}  units {S['units_total']}  dev {S['dev']['n']} ({S['dev']['matches']} m)  conf {S['conf']['n']} ({S['conf']['matches']} m)")
    for c in C.CANDS:
        d = S["dev"]["cands"][c]
        print(f"  dev  {c} {C.NAMES[c]:24s} ll {d['logloss_gain']:+.5f} {d['logloss_gain_ci']}  brier {d['brier_gain']:+.5f} {d['brier_gain_ci']}")
    print("  SELECTED:", S["selected"], C.NAMES.get(S["selected"]))
    for c in ("C0",) + C.CANDS:
        a = S["conf"]["arms"][c]
        p = a.get("paired") or {}
        rc = a["results_cohort"]
        print(f"  conf {c} {C.NAMES[c]:24s} ll {p.get('logloss_gain', 0):+.5f} {p.get('logloss_gain_ci')}  brier {p.get('brier_gain', 0):+.5f} {p.get('brier_gain_ci')}"
              f"  inlarge {a['in_large']['stated']:.3f}/{a['in_large']['hit']:.3f}  bal {a['balance']['share_over']:.3f}/{a['balance']['over_rate']:.3f}"
              f"  results n {rc['n']} {rc.get('stated')}/{rc.get('hit')} {rc.get('hit_minus_stated_ci')}")
    print("  PASS CHECKS:", S["pass_checks"], "PASSED:", S["passed"])
