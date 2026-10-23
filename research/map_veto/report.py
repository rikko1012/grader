"""Markdown tables from results_<dev|final>.json. usage: python report.py dev|final"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = {"M0": "M0 uniform-legal", "M1": "M1 simple recent", "M2": "M2 frozen sim", "M2full": "M2 frozen (+uniform fallback)",
         "M3": "M3 candidate", "M3nl": "M3 without listing order"}


def ci(x):
    return f"{x[0]:.4f} [{x[1]:.4f}, {x[2]:.4f}]" if x else "-"


def main(which):
    r = json.load(open(os.path.join(HERE, f"results_{which}.json")))
    groups = ["DEV(D1-D4)", "D1", "D2", "D3", "D4"] if which == "dev" else ["FINAL"]
    out = [f"## {which.upper()} - {r['version']}\n"]
    for g in groups:
        out.append(f"\n### {g}\n")
        out.append("| format | target | model | matches | log-loss [95% CI] | Brier | top-1 |")
        out.append("|---|---|---|---|---|---|---|")
        keys = [k for k in r["tables"] if k.startswith(g + "|") and "@M2cov" not in k]
        order = {"map": 0, "map1": 1, "map2": 2, "map3": 3, "map4": 4, "decider": 5, "pair": 6, "set": 7, "seq": 8, "incl": 9}
        keys.sort(key=lambda k: (k.split("|")[1], order.get(k.split("|")[2], 99), k.split("|")[3]))
        for k in keys:
            _, fmt, tgt, mdl = k.split("|")
            v = r["tables"][k]
            out.append(f"| {fmt} | {tgt} | {NAMES.get(mdl, mdl)} | {v['n']} | {ci(v['logloss'])} | {v.get('brier', '-')} | {v['top1']} |")
        out.append("\nHead-to-head on M2's coverage (BO3 matches where the frozen simulator returns a forecast):\n")
        out.append("| target | model | matches | log-loss [95% CI] | top-1 |")
        out.append("|---|---|---|---|---|")
        for k in sorted(x for x in r["tables"] if x.startswith(g + "|bo3|") and ("@M2cov" in x or x.endswith("|M2"))):
            _, fmt, tgt, mdl = k.split("|")
            v = r["tables"][k]
            out.append(f"| {tgt} | {NAMES.get(mdl.replace('@M2cov', ''), mdl)} | {v['n']} | {ci(v['logloss'])} | {v['top1']} |")
        out.append("\nPaired log-loss difference (baseline - candidate; > 0 = candidate better), match-grouped 95% CI:\n")
        out.append("| format | target | comparison | matches | mean diff [95% CI] |")
        out.append("|---|---|---|---|---|")
        for k in sorted(x for x in r["diffs"] if x.startswith(g + "|")):
            _, fmt, tgt, cmp_ = k.split("|")
            v = r["diffs"][k]
            out.append(f"| {fmt} | {tgt} | {cmp_} | {v['n']} | {ci(v['ll_diff'])} |")
    out.append("\n### Live updates (conditioned on the first 2 / 4 confirmed actions; M2 cannot condition)\n")
    out.append("| period | format | known | target | model | matches | log-loss [95% CI] | top-1 |")
    out.append("|---|---|---|---|---|---|---|---|")
    g0 = groups[0]
    for k in sorted(x for x in r["live"] if x.startswith(g0 + "|") and "-M3" not in x):
        _, fmt, kk, tgt, mdl = k.split("|")
        v = r["live"][k]
        out.append(f"| {g0} | {fmt} | {kk} | {tgt} | {NAMES.get(mdl, mdl)} | {v['n']} | {ci(v['logloss'])} | {v['top1']} |")
    for k in sorted(x for x in r["live"] if x.startswith(g0 + "|") and "-M3" in x):
        _, fmt, kk, tgt, cmp_ = k.split("|")
        out.append(f"| {g0} | {fmt} | {kk} | {tgt} | diff {cmp_} | {r['live'][k]['n']} | {ci(r['live'][k]['ll_diff'])} | |")
    out.append("\n### Calibration (ECE, 10 bins, all pool maps per match)\n")
    out.append("| group | format | model | target | ECE |")
    out.append("|---|---|---|---|---|")
    for k in sorted(r["calibration"]):
        grp, fmt, mdl, tgt = k.split("|")
        out.append(f"| {grp} | {fmt} | {NAMES.get(mdl, mdl)} | {tgt} | {r['calibration'][k]['ece']} |")
    out.append("\nCoverage: " + json.dumps(r["coverage"]))
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
