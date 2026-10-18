"""cm-1 paired forward scorecard (CLAUDE_NEXT_RELEASE section 6): recomputed from the STORED P1 unit export
(/admin/learning/p1_units.json - each unit's published live chance and each challenger's value saved before the start;
nothing recomputed or backfilled). usage: python scorecard.py p1_units.json out.json

Cohort: P1 units (latest standard board forecast >= 30 min before the start; pushes excluded) of Maps 1-2 kills / HS whose
forecast was logged AND whose match started after cm-1's registry freeze (2026-10-16T22:35:14Z). Arms on the SAME units:
cm-1 (its saved value), LIVE (the record's live arm = the published chance), BASE (earlier-day Over rate per stat x scope
over every earlier P1 unit, shrunk with 50 pseudo-units: (overs + 25) / (n + 50), the study's registered baseline).
Intervals resample whole matches (B = 4000)."""
import collections
import json
import math
import random
import sys

CM, FZ = "cm-001-4e5f42fb", "2026-10-16T22:35:14+00:00"
B = 4000


def ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -math.log(p if y else 1 - p)


def boot(rows, f, seed=7):
    g = collections.defaultdict(list)
    for r in rows:
        g[r["match"]].append(r)
    ks, rnd, v = list(g), random.Random(seed), []
    for _ in range(B):
        s = [r for k in (rnd.choice(ks) for _ in ks) for r in g[k]]
        if s:
            v.append(sum(f(r) for r in s) / len(s))
    v.sort()
    return [round(v[int(.025 * len(v))], 5), round(v[int(.975 * len(v)) - 1], 5)]


def block(rs):
    if not rs:
        return {"units": 0}
    out = {"units": len(rs), "matches": len({r["match"] for r in rs}), "overs": sum(r["over"] for r in rs)}
    for a in ("cm1", "live", "base"):
        out[f"log_loss_{a}"] = round(sum(ll(r[a], r["over"]) for r in rs) / len(rs), 5)
        out[f"brier_{a}"] = round(sum((r[a] - r["over"]) ** 2 for r in rs) / len(rs), 5)
        out[f"mean_p_{a}"] = round(sum(r[a] for r in rs) / len(rs), 4)
    out["observed_over_rate"] = round(out["overs"] / len(rs), 4)
    for x, y in (("cm1", "live"), ("cm1", "base"), ("live", "base")):       # + = the first arm better
        f = lambda r, x=x, y=y: ll(r[y], r["over"]) - ll(r[x], r["over"])
        out[f"ll_gain_{x}_vs_{y}"] = round(sum(f(r) for r in rs) / len(rs), 5)
        out[f"ll_gain_{x}_vs_{y}_ci"] = boot(rs, f)
        fb = lambda r, x=x, y=y: (r[y] - r["over"]) ** 2 - (r[x] - r["over"]) ** 2
        out[f"brier_gain_{x}_vs_{y}"] = round(sum(fb(r) for r in rs) / len(rs), 5)
        out[f"brier_gain_{x}_vs_{y}_ci"] = boot(rs, fb)
    pts = [r for r in rs if r["lam_cm1"] is not None and r["lam_live"] is not None and r["actual"] is not None]
    if pts:
        out["point"] = {"units": len(pts),
                        "mae_cm1": round(sum(abs(r["lam_cm1"] - r["actual"]) for r in pts) / len(pts), 3),
                        "mae_live": round(sum(abs(r["lam_live"] - r["actual"]) for r in pts) / len(pts), 3),
                        "mse_cm1": round(sum((r["lam_cm1"] - r["actual"]) ** 2 for r in pts) / len(pts), 2),
                        "mse_live": round(sum((r["lam_live"] - r["actual"]) ** 2 for r in pts) / len(pts), 2),
                        "bias_cm1": round(sum(r["lam_cm1"] - r["actual"] for r in pts) / len(pts), 3),
                        "bias_live": round(sum(r["lam_live"] - r["actual"] for r in pts) / len(pts), 3)}
        fa = lambda r: abs(r["lam_live"] - r["actual"]) - abs(r["lam_cm1"] - r["actual"])
        out["point"]["mae_gain_cm1_vs_live_ci"] = boot(pts, fa)
        fs = lambda r: (r["lam_live"] - r["actual"]) ** 2 - (r["lam_cm1"] - r["actual"]) ** 2
        out["point"]["mse_gain_cm1_vs_live_ci"] = boot(pts, fs)
    cal = {}
    for a in ("cm1", "live"):
        bins = collections.defaultdict(list)
        for r in rs:
            p = r[a]
            k = "<0.40" if p < .4 else "0.40-0.50" if p < .5 else "0.50-0.60" if p < .6 else ">=0.60"
            bins[k].append(r)
        cal[a] = {k: {"units": len(v), "mean_p_over": round(sum(r[a] for r in v) / len(v), 3),
                      "observed_over": round(sum(r["over"] for r in v) / len(v), 3)} for k, v in sorted(bins.items())}
    out["calibration_p_over"] = cal
    sel = {}
    for a in ("cm1", "live"):                  # the registered 57% rule - different pick sets per arm, not a paired comparison
        s = [r for r in rs if max(r[a], 1 - r[a]) >= .57]
        w = sum((r["over"] if r[a] >= .5 else 1 - r["over"]) for r in s)
        sel[a] = {"picks": len(s), "matches": len({r["match"] for r in s}), "wins": w, "hit": round(w / len(s), 4) if s else None}
    out["rule57_not_paired"] = sel
    return out


def main(src, dst):
    ex = json.load(open(src))
    U = ex["rows"]
    mm = lambda u: (u["prop"], u["scope"] or "12")
    # BASE from every earlier-day P1 unit (any version / time), per stat x scope
    by_day = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for u in U:
        d = by_day[mm(u)][u["start"][:10]]
        d[0] += u["over"]
        d[1] += 1

    def base(u):
        o = n = 0
        for day, (oo, nn) in by_day[mm(u)].items():
            if day < u["start"][:10]:
                o, n = o + oo, n + nn
        return (o + 25) / (n + 50)
    fwd = [u for u in U if u["logged"] > FZ and u["start"] > FZ and u["prop"] in ("kills", "hs") and (u["scope"] or "12") == "12"]
    rs, missing = [], 0
    for u in fwd:
        c = (u.get("challengers") or {}).get(CM)
        if not c or c.get("p") is None:
            missing += 1
            continue
        rs.append({"match": u["match"], "over": u["over"], "prop": u["prop"], "version": u.get("version") or "untagged",
                   "cm1": c["p"], "live": c["p_base"], "base": base(u), "lam_cm1": c.get("lam"), "lam_live": c.get("lam_base"),
                   "actual": u.get("actual"), "day": u["start"][:10], "published": u["p"]})
    out = {"export_at": ex.get("head", {}).get("at") or ex.get("at"), "freeze": FZ, "challenger": CM,
           "forward_units": len(fwd), "scored": len(rs), "missing_or_ineligible": missing,
           "coverage": round(len(rs) / len(fwd), 3) if fwd else None,
           "live_arm_equals_published_max_gap": max((abs(r["live"] - r["published"]) for r in rs), default=None),
           "days": dict(collections.Counter(r["day"] for r in rs)),
           "all": block(rs), "kills": block([r for r in rs if r["prop"] == "kills"]), "hs": block([r for r in rs if r["prop"] == "hs"]),
           "by_live_version": {v: block([r for r in rs if r["version"] == v]) for v in sorted({r["version"] for r in rs})}}
    json.dump(out, open(dst, "w"), indent=1)
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2])
    print(json.dumps({k: o[k] for k in ("forward_units", "scored", "missing_or_ineligible", "coverage", "days")}, indent=1))
