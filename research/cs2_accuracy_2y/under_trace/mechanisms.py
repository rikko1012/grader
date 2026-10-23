"""UT-1 mechanism tests (diagnostic, DEVELOPMENT data; nothing is fitted or promoted).

For every P1 unit (units.json from build_units.py) and the HLTV dump:
  A. realised error split: actual - projection = rate part + rounds part (HLTV rounds actually played vs the fixed R)
  B. rounds per map: HLTV-wide by map number and month vs the fixed 21.74, and on the P1 matches
  C. step-out tests: the projection without one step (prior shrink / environment / opponent / cm-2 swap) - bias and |error|
     against the actual AND against the rounds-normalised actual (actual x R / rounds played), match-grouped 95% CI;
     the graded picks whose SIDE that step decided, and how they did
  D. N0 pin: perf-2 replayed on N0 rows vs on all HLTV rows (same code, same day), bias / |error| / side flips
  E. prior shrink by history depth; opponent factor by size
  F. cm-2: feature contributions to log-lambda (coef x standardised feature) on P1 cm-2 lines; recomputation check
  G. HS distribution shape: HS lines whose projection is above the line but whose chance is Under, and calibration there
usage: python mechanisms.py <scratch dir> > mechanisms.md"""
import collections
import json
import math
import pickle
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
from report import boot, ci, nm, SCOPE_NAME  # noqa: E402
import cm1, cm2, perf  # noqa: E402

R_FIXED = perf.PARAMS["base_r"]


def side_of(proj, line, prop):
    return "Over" if proj > line else "Under"


def table(out, head, rows):
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "---|" * len(head))
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")


def grp(us):
    for prop in ("kills", "hs"):
        for sc in ("1", "12", "123"):
            g = [u for u in us if u["prop"] == prop and u["scope"] == sc]
            if g:
                yield f"{prop} {SCOPE_NAME[sc]}", g


def sec_a(us, out):
    out.append("\n## A. Realised error: actual - projection = rate part + rounds part\n")
    out.append("rate part = (actual per round - projected per round) x R; rounds part = actual per round x (rounds played - R). "
               "HLTV-settled canonical matches only (settlement equals HLTV on every one).\n")
    rows = []
    for lab, g in grp(us):
        x = [u for u in g if u.get("hltv_rounds") and u.get("R")]
        for name, y in (("all", x), ("graded Unders", [u for u in x if u["conf"] >= .55 and u["side"] == "Under"]),
                        ("graded Overs", [u for u in x if u["conf"] >= .55 and u["side"] == "Over"])):
            if len(y) < 10:
                continue
            rate = lambda u: (u["actual"] / u["hltv_rounds"] - u["lam"] / u["R"]) * u["R"]
            rnd = lambda u: u["actual"] / u["hltv_rounds"] * (u["hltv_rounds"] - u["R"])
            rows.append([lab, name, nm(y), ci(boot(y, lambda u: u["actual"] - u["lam"])), ci(boot(y, rate)), ci(boot(y, rnd)),
                         ci(boot(y, lambda u: u["hltv_rounds"] - u["R"]))])
    table(out, ["group", "lines", "lines / matches", "actual - proj", "rate part", "rounds part", "rounds played - R"], rows)


def sec_b(dump, us, out):
    out.append("\n## B. Rounds per map (fixed R = 21.74 per map)\n")
    maps = {}
    for r in dump["rows"]:
        if r[9] is not None and r[10] is not None:
            maps[(r[0], r[3])] = (str(r[1])[:7], r[9] + r[10])
    rows = []
    per = collections.defaultdict(list)
    for (m, no), (mo, rr) in maps.items():
        per[(mo, min(no, 3))].append({"match": m, "r": rr})
    for mo in sorted({k[0] for k in per}):
        if mo < "2026-01":
            continue
        cells = [mo]
        for no in (1, 2, 3):
            x = per.get((mo, no)) or []
            cells.append(f"{ci(boot(x, lambda u: u['r'] - R_FIXED))} ({len(x)})" if x else "-")
        rows.append(cells)
    out.append("HLTV-wide, every map in the database (rounds - 21.74, match-grouped CI; map number 3 = 3rd map or later):\n")
    table(out, ["month", "map 1", "map 2", "map 3+"], rows)
    # P1 matches
    p1m = {int(u["match"][5:]) for u in us if str(u["match"]).startswith("hltv:") and u["match"][5:].isdigit()}
    rows = []
    for no in (1, 2, 3):
        x = [{"match": m, "r": maps[(m, no)][1]} for m in p1m if (m, no) in maps]
        if x:
            rows.append([f"map {no}", len(x), ci(boot(x, lambda u: u["r"] - R_FIXED))])
    out.append("\nP1 matches (canonical HLTV id), one row per map played:\n")
    table(out, ["map", "maps", "rounds - 21.74 [95% CI]"], rows)


def sec_c(us, out):
    out.append("\n## C. Step-out tests: is each step that lowers projections justified by outcomes?\n")
    out.append("alt = projection without the step. Columns: mean step size; bias = mean(actual - proj) and mean(actual - alt); "
               "|err| gain = mean(|actual - alt| - |actual - proj|) (+ = the step helps). 'norm' = against the rounds-normalised actual "
               "(actual x R / rounds played) - removes the shared rounds noise. Then the graded (55%+) picks whose side the step "
               "decided (sign(proj - line) != sign(alt - line)): their count, side, and hit rate.\n")
    steps = (("d_prior", "prior shrink"), ("d_env", "environment blend"), ("d_opp", "opponent factor"), ("d_cm2", "cm-2 swap"))
    rows, rows2 = [], []
    for lab, g in grp(us):
        for k, name in steps:
            x = [u for u in g if u.get(k) is not None and u.get("L_raw") is not None]
            if k == "d_cm2":
                x = [u for u in x if u["source"] == "perf-cm2"]
            if len(x) < 15:
                continue
            xn = [u for u in x if u.get("hltv_rounds")]
            an = lambda u: u["actual"] * u["R"] / u["hltv_rounds"]
            rows.append([lab, name, nm(x), ci(boot(x, lambda u: u[k])), ci(boot(x, lambda u: u["actual"] - u["lam"])),
                         ci(boot(x, lambda u: u["actual"] - (u["lam"] - u[k]))),
                         ci(boot(x, lambda u: abs(u["actual"] - (u["lam"] - u[k])) - abs(u["actual"] - u["lam"])), d=3),
                         ci(boot(xn, lambda u: an(u) - u["lam"])), ci(boot(xn, lambda u: an(u) - (u["lam"] - u[k]))),
                         ci(boot(xn, lambda u: abs(an(u) - (u["lam"] - u[k])) - abs(an(u) - u["lam"])), d=3)])
            dec = [u for u in x if u["conf"] >= .55 and (u["lam"] > u["line"]) != (u["lam"] - u[k] > u["line"])]
            hit = lambda u: float(u["over"] if u["side"] == "Over" else 1 - u["over"])
            for s in ("Under", "Over"):
                y = [u for u in dec if u["side"] == s]
                if y:
                    rows2.append([lab, name, s, nm(y), f"{100 * sum(u['conf'] for u in y) / len(y):.1f}%", ci(boot(y, hit), pct=True)])
    table(out, ["group", "step", "lines / matches", "step size", "bias proj", "bias alt", "|err| gain", "norm bias proj", "norm bias alt",
                "norm |err| gain"], rows)
    out.append("\nGraded picks whose side the step decided:\n")
    table(out, ["group", "step", "side it produced", "lines / matches", "printed", "won [95% CI]"], rows2)


def sec_d(us, out):
    out.append("\n## D. N0 pin: perf-2 on N0 rows vs perf-2 on every HLTV row (same code, same match day)\n")
    out.append("Replay check: on the 1,560 N0-pinned (n0-r4) perf-2 lines the N0 replay equals the logged perf-2 projection within 0.01 on "
               "1,406 and within 0.43 on all (rows corrected since); the full-history arm differs only in the rows read.\n")
    rows, rows2 = [], []
    for lab, g in grp(us):
        x = [u for u in g if u.get("replay_n0") is not None and u.get("replay_full") is not None]
        if len(x) < 15:
            continue
        xn = [u for u in x if u.get("hltv_rounds")]
        an = lambda u: u["actual"] * u["R"] / u["hltv_rounds"]
        rows.append([lab, nm(x), ci(boot(x, lambda u: u["replay_full"] - u["replay_n0"])),
                     ci(boot(x, lambda u: u["actual"] - u["replay_n0"])), ci(boot(x, lambda u: u["actual"] - u["replay_full"])),
                     ci(boot(x, lambda u: abs(u["actual"] - u["replay_n0"]) - abs(u["actual"] - u["replay_full"])), d=3),
                     ci(boot(xn, lambda u: abs(an(u) - u["replay_n0"]) - abs(an(u) - u["replay_full"])), d=3),
                     f"{sum((u['replay_n0'] > u['line']) != (u['replay_full'] > u['line']) for u in x)}",
                     f"{100 * sum(u['replay_n0'] < u['line'] for u in x) / len(x):.1f}% / {100 * sum(u['replay_full'] < u['line'] for u in x) / len(x):.1f}%"])
        for lo, hi, nmb in ((0, 500, "N0 history < 500 rounds"), (500, 1500, "500-1500"), (1500, 1e9, "1500+")):
            y = [u for u in x if lo <= (u.get("replay_n0_hist_rounds") or 0) < hi]
            if len(y) >= 15:
                rows2.append([lab, nmb, nm(y), ci(boot(y, lambda u: u["replay_full"] - u["replay_n0"])),
                              ci(boot(y, lambda u: u["actual"] - u["replay_n0"])), ci(boot(y, lambda u: u["actual"] - u["replay_full"]))])
    table(out, ["group", "lines / matches", "full - N0 projection", "bias N0", "bias full", "|err| gain of full", "norm |err| gain of full",
                "side flips", "Under side N0 / full"], rows)
    out.append("\nBy N0 history depth (weighted rounds):\n")
    table(out, ["group", "depth", "lines / matches", "full - N0", "bias N0", "bias full"], rows2)


def sec_e(us, out):
    out.append("\n## E. Prior shrink by history depth, opponent factor by size (perf-family lines)\n")
    rows = []
    for lab, g in grp(us):
        if "Maps 1-2" not in lab:
            continue
        for lo, hi, nmb in ((0, 300, "< 300 rounds"), (300, 800, "300-800"), (800, 1500, "800-1500"), (1500, 1e9, "1500+")):
            y = [u for u in g if u.get("hist_rounds") is not None and lo <= u["hist_rounds"] < hi]
            if len(y) < 15:
                continue
            yn = [u for u in y if u.get("hltv_rounds")]
            an = lambda u: u["actual"] * u["R"] / u["hltv_rounds"]
            rows.append([lab, f"history {nmb}", nm(y), ci(boot(y, lambda u: u["d_prior"])), ci(boot(yn, lambda u: an(u) - u["lam"])),
                         ci(boot(yn, lambda u: an(u) - (u["lam"] - u["d_prior"]))),
                         f"{100 * sum(u['side'] == 'Under' for u in y if u['conf'] >= .55) / max(1, sum(u['conf'] >= .55 for u in y)):.0f}% of {sum(u['conf'] >= .55 for u in y)}"])
        for lo, hi, nmb in ((0, .95, "opp < 0.95"), (.95, .99, "0.95-0.99"), (.99, 1.01, "0.99-1.01 (incl. unknown = 1)"), (1.01, 9, "> 1.01")):
            y = [u for u in g if u.get("opp_factor") is not None and lo <= u["opp_factor"] < hi]
            if len(y) < 15:
                continue
            yn = [u for u in y if u.get("hltv_rounds")]
            an = lambda u: u["actual"] * u["R"] / u["hltv_rounds"]
            rows.append([lab, nmb, nm(y), ci(boot(y, lambda u: u["d_opp"])), ci(boot(yn, lambda u: an(u) - u["lam"])),
                         ci(boot(yn, lambda u: an(u) - (u["lam"] - u["d_opp"]))),
                         f"{100 * sum(u['side'] == 'Under' for u in y if u['conf'] >= .55) / max(1, sum(u['conf'] >= .55 for u in y)):.0f}% of {sum(u['conf'] >= .55 for u in y)}"])
    table(out, ["group", "slice", "lines / matches", "step size", "norm bias proj", "norm bias without the step", "graded Under share"], rows)


def sec_f(us, out):
    out.append("\n## F. cm-2 (perf-cm2 lines): feature contributions to log-lambda and recomputation\n")
    fit, names = cm2.FIT, cm2.ARTIFACT["feature_names"]
    x = [u for u in us if u["source"] == "perf-cm2" and u.get("cm2_features")]
    bad = 0
    for u in x:
        f = cm1.features(u["cm2_inputs"])
        eta = fit["intercept"] + sum(b * (v - m) / s for b, v, m, s in zip(fit["coef"], f, fit["mean"], fit["std"]))
        if abs(math.exp(eta) - u["cm2_lambda_kills"]) > 1e-6 or any(abs(a - b) > 1e-9 for a, b in zip(f, u["cm2_features"])):
            bad += 1
    out.append(f"Recomputed from the saved sums (cm1.features + the artifact): {len(x) - bad} of {len(x)} lines reproduce lambda to 1e-6.\n")
    out.append(f"exp(intercept) = {math.exp(fit['intercept']):.2f} kills (Maps 1-2, all features at the training mean). Contribution "
               "= coef x (feature - training mean) / sd, in log points; kills effect = mean of lambda x (1 - exp(-contribution)).\n")
    rows = []
    for prop in ("kills", "hs"):
        y = [u for u in x if u["prop"] == prop]
        if not y:
            continue
        for sub, z in (("all", y), ("graded Unders", [u for u in y if u["conf"] >= .55 and u["side"] == "Under"]),
                       ("graded Overs", [u for u in y if u["conf"] >= .55 and u["side"] == "Over"])):
            if not z:
                continue
            for i, n in enumerate(names):
                c = [fit["coef"][i] * (u["cm2_features"][i] - fit["mean"][i]) / fit["std"][i] for u in z]
                rows.append([prop, sub, len(z), n, f"{statistics.mean(c):+.4f}",
                             f"{statistics.mean(u['cm2_lambda_kills'] * (1 - math.exp(-ci_)) for u, ci_ in zip(z, c)):+.2f}"])
            rows.append([prop, sub, len(z), "**total vs exp(intercept)**",
                         f"{statistics.mean(math.log(u['cm2_lambda_kills']) - fit['intercept'] for u in z):+.4f}",
                         f"{statistics.mean(u['cm2_lambda_kills'] for u in z) - math.exp(fit['intercept']):+.2f}"])
    table(out, ["stat", "subset", "lines", "feature", "mean contribution (log pts)", "kills effect"], rows)


def sec_g(us, out):
    out.append("\n## G. HS distribution shape (HS | kills beta-binomial)\n")
    rows = []
    for lab, g in grp(us):
        if not lab.startswith("hs"):
            continue
        a = [u for u in g if u["lam"] > u["line"]]
        au = [u for u in a if u["side"] == "Under"]
        rows.append([lab, len(g), len(a), len(au), sum(1 for u in au if u["conf"] >= .55),
                     f"{100 * sum(u['p'] for u in au) / len(au):.1f}%" if au else "-",
                     ci(boot(au, lambda u: float(u["over"])), pct=True) if au else "-"])
    table(out, ["group", "lines", "projection above line", "of which chance says Under", "of those graded 55%+", "mean P(Over) printed",
                "observed Over rate [95% CI]"], rows)


def main(scr):
    scr = Path(scr)
    d = json.load(open(scr / "units.json"))
    us = [u for u in d["units"] if u["side"] != "none"]
    dump = pickle.load(open(sorted(scr.glob("hltv_dump_*.pkl"))[-1], "rb"))
    out = [f"<!-- generated by mechanisms.py from units.json built {d['built_at']} -->"]
    sec_a(us, out)
    sec_b(dump, us, out)
    sec_c(us, out)
    sec_d(us, out)
    sec_e(us, out)
    sec_f(us, out)
    sec_g(us, out)
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
