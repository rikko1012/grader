"""Decision report on the EXISTING candidates (owner 2026-10-16 evening). Read-only.

Inputs: the production P1 export (/admin/learning/p1_units.json: each challenger's STORED value per forecast), the stored
learning dashboard (registered review cohort), and the research cohort rows (realline_test_rows.json) for reconciliation.
Rules fixed before reading results:
- forward rows of a challenger = forecast logged AND match started after its frozen_at (the production rule);
- live arm = the shadow row's p_over_base (the registered pairing: live replayed on the same inputs; for fh-1 the record's
  live arm), the logged chance reported beside it;
- intervals: whole matches resampled (learning._groups / _ci_ratio), 95% and the registered 98.33%;
- BASE (simple line-based baseline, protocol of 2026-10-16): the Over rate at offered lines per stat on EARLIER match days,
  shrunk to 50% with 50 pseudo-units; first day = 50%;
- selection rule: favoured side when its chance >= 57%; value beyond baseline = hit - BASE's chance of that same side.
Nothing is fitted here."""
import collections
import json
import math
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import learning as L

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
B = 2000


def fetch_export():
    T = os.environ["ADMIN_TOKEN"]                  # never printed
    rows, after, meta = [], 0, None
    while after is not None:
        req = urllib.request.Request(f"https://cs2-scout.onrender.com/admin/learning/p1_units.json?after={after}&limit=2000",
                                     headers={"X-Admin-Token": T})
        with urllib.request.urlopen(req, timeout=180) as r:
            pg = json.load(r)
        meta = {k: v for k, v in pg.items() if k != "rows"}
        rows += pg["rows"]
        after = pg["next_after"]
    out = dict(meta, rows=rows)
    fn = os.path.join(HERE, f"p1_units_{time.strftime('%H%M', time.gmtime())}.json")
    json.dump(out, open(fn, "w"))
    return out, fn


ll = L._ll
br = lambda p, y: (p - y) ** 2
day = lambda s: str(s)[:10]


def ci(units, f, level=0.95):
    return L._ci_ratio(L._groups(units, f), B=B, level=level)


def fmt_ci(c, d=4):
    return f"({c[0]:+.{d}f}, {c[1]:+.{d}f})" if c else "(-)"


def base_rates(rows):
    """BASE per row: earlier-day Over rate of the same stat, shrunk with 50 pseudo-units."""
    by = collections.defaultdict(lambda: [0, 0])
    days = sorted({day(r["start"]) for r in rows})
    cum = {}
    for d in days:
        cum[d] = {k: tuple(v) for k, v in by.items()}
        for r in rows:
            if day(r["start"]) == d:
                by[r["prop"]][0] += r["over"]
                by[r["prop"]][1] += 1
    for r in rows:
        o, n = cum[day(r["start"])].get(r["prop"], (0, 0))
        r["p_BASE"] = (o + 25) / (n + 50)


def slope(units, key):
    m = L.metrics([dict(u, p=u[key]) for u in units], slope=True)
    return m.get("calibration_slope"), m.get("calibration_slope_ci"), m.get("over_gap"), m.get("over_gap_ci")


def prob_block(units, a, b, label_a, label_b):
    """Paired probability accuracy of b vs a (positive = b better)."""
    gl = lambda u: ll(u[a], u["over"]) - ll(u[b], u["over"])
    gb = lambda u: br(u[a], u["over"]) - br(u[b], u["over"])
    n = len(units)
    out = {"units": n, "matches": len({u["match"] for u in units}),
           "first_start": min(u["start"] for u in units), "last_start": max(u["start"] for u in units),
           "log_loss": {label_a: sum(ll(u[a], u["over"]) for u in units) / n, label_b: sum(ll(u[b], u["over"]) for u in units) / n},
           "brier": {label_a: sum(br(u[a], u["over"]) for u in units) / n, label_b: sum(br(u[b], u["over"]) for u in units) / n},
           "ll_gain": sum(map(gl, units)) / n, "ll_gain_ci95": ci(units, gl), "ll_gain_ci9833": ci(units, gl, 0.9833),
           "brier_gain": sum(map(gb, units)) / n, "brier_gain_ci95": ci(units, gb), "brier_gain_ci9833": ci(units, gb, 0.9833)}
    for k, lab in ((a, label_a), (b, label_b)):
        s, sci, g, gci = slope(units, k)
        out[f"calib_{lab}"] = {"slope": s, "slope_ci": sci, "mean_pred_minus_obs": g, "gap_ci": gci,
                               "mean_pred": sum(u[k] for u in units) / n}
    out["over_rate"] = sum(u["over"] for u in units) / n
    return out


def select(units, key, thr=0.57):
    sel = []
    for u in units:
        p = u[key]
        if max(p, 1 - p) < thr:
            continue
        over = p >= 0.5
        hit = u["over"] if over else 1 - u["over"]
        pb = u["p_BASE"] if over else 1 - u["p_BASE"]
        sel.append(dict(u, _hit=hit, _pp=max(p, 1 - p), _pb=pb, _side="Over" if over else "Under"))
    if not sel:
        return {"picks": 0}
    hv = lambda x: x["_hit"]
    vb = lambda x: x["_hit"] - x["_pb"]
    v5 = lambda x: x["_hit"] - 0.5
    side = lambda s: [x for x in sel if x["_side"] == s]
    return {"picks": len(sel), "matches": len({x["match"] for x in sel}), "printed": sum(x["_pp"] for x in sel) / len(sel),
            "hit": sum(map(hv, sel)) / len(sel), "hit_ci": ci(sel, hv),
            "base_chance_same_side": sum(x["_pb"] for x in sel) / len(sel),
            "beyond_base": sum(map(vb, sel)) / len(sel), "beyond_base_ci": ci(sel, vb),
            "beyond_50": sum(map(v5, sel)) / len(sel), "beyond_50_ci": ci(sel, v5),
            "overs": len(side("Over")), "hit_overs": (sum(x["_hit"] for x in side("Over")) / len(side("Over"))) if side("Over") else None,
            "unders": len(side("Under")), "hit_unders": (sum(x["_hit"] for x in side("Under")) / len(side("Under"))) if side("Under") else None}


def show_prob(name, pb, a, b):
    print(f"  {name}: {pb['units']} units / {pb['matches']} matches, starts {pb['first_start'][:16]} .. {pb['last_start'][:16]}, Over rate {pb['over_rate']:.3f}")
    print(f"    log loss {a} {pb['log_loss'][a]:.4f} | {b} {pb['log_loss'][b]:.4f} | gain {pb['ll_gain']:+.5f} 95% {fmt_ci(pb['ll_gain_ci95'])} 98.33% {fmt_ci(pb['ll_gain_ci9833'])}")
    print(f"    Brier    {a} {pb['brier'][a]:.4f} | {b} {pb['brier'][b]:.4f} | gain {pb['brier_gain']:+.5f} 95% {fmt_ci(pb['brier_gain_ci95'])} 98.33% {fmt_ci(pb['brier_gain_ci9833'])}")
    for lab in (a, b):
        c = pb[f"calib_{lab}"]
        sl = f"{c['slope']:.2f} {fmt_ci(c['slope_ci'], 2)}" if c["slope"] is not None else "-"
        print(f"    calibration {lab}: mean pred {c['mean_pred']:.3f}, pred - obs {c['mean_pred_minus_obs']:+.4f} {fmt_ci(c['gap_ci'])}, slope {sl}")


def show_sel(name, s):
    if not s["picks"]:
        print(f"    {name}: 0 picks")
        return
    print(f"    {name}: {s['picks']} picks / {s['matches']} matches, printed {s['printed']:.3f}, hit {s['hit']:.3f} {fmt_ci(s['hit_ci'], 3)}; "
          f"BASE chance of the same sides {s['base_chance_same_side']:.3f} -> beyond BASE {s['beyond_base']:+.3f} {fmt_ci(s['beyond_base_ci'], 3)}; "
          f"Overs {s['overs']} hit {s['hit_overs'] if s['hit_overs'] is None else round(s['hit_overs'], 3)}, Unders {s['unders']} hit {s['hit_unders'] if s['hit_unders'] is None else round(s['hit_unders'], 3)}")


def collapse(rows):
    """One row per forecast (the same picks stored under a stale match key and the canonical one are ONE unit); match keys
    that share a forecast are one match (representative: the canonical hltv: key when present)."""
    parent = {}
    find = lambda k: k if parent.setdefault(k, k) == k else find(parent[k])
    byf = collections.defaultdict(list)
    for r in rows:
        byf[r["forecast_id"]].append(r)
    for rs in byf.values():
        ks = sorted({r["match"] for r in rs}, key=lambda k: (not str(k).startswith("hltv:"), k))
        for k in ks[1:]:
            a, b = find(ks[0]), find(k)
            if a != b:
                if str(b).startswith("hltv:") and not str(a).startswith("hltv:"):
                    a, b = b, a
                parent[b] = a
    out = []
    for fid, rs in byf.items():
        r = dict(rs[0])
        r["match"] = find(r["match"])
        out.append(r)
    out.sort(key=lambda r: (r["start"], r["forecast_id"]))
    return out


def main():
    ex, fn = fetch_export() if len(sys.argv) < 2 else (json.load(open(sys.argv[1])), sys.argv[1])
    raw = ex["rows"]
    rows = collapse(raw)
    print(f"rows as exported {len(raw)} ({len({r['match'] for r in raw})} match keys) -> one per forecast {len(rows)} "
          f"({len({r['match'] for r in rows})} matches)")
    print("export", fn, "built", ex["at"], "units", len(rows), "matches", len({r["match"] for r in rows}))
    print("forecast rule:", ex["forecast_rule"])
    base_rates(rows)
    for r in rows:
        r["p_live_logged"] = r["p"]

    # ---------------------------------------------------------------- 1. cohort reconciliation
    print("\n=== 1. cohorts")
    def cohort(rs, label):
        bv = collections.defaultdict(lambda: [0, set(), "9", "0"])
        for r in rs:
            v = bv[r["version"]]
            v[0] += 1; v[1].add(r["match"]); v[2] = min(v[2], r["start"]); v[3] = max(v[3], r["start"])
        print(f"  {label}: {len(rs)} units / {len({r['match'] for r in rs})} matches; match starts {min(r['start'] for r in rs)[:16]} .. "
              f"{max(r['start'] for r in rs)[:16]}; forecasts logged {min(r['logged'] for r in rs)[:16]} .. {max(r['logged'] for r in rs)[:16]}")
        for k, v in sorted(bv.items(), key=lambda kv: kv[1][2]):
            print(f"     version {k}: {v[0]} units / {len(v[1])} matches, starts {v[2][:16]} .. {v[3][:16]}")
        bd = collections.Counter(day(r["start"]) for r in rs)
        print("     by match day:", dict(sorted(bd.items())))
    cohort(rows, "production P1 (all)")
    rec = [r for r in rows if max(r["p"], 1 - r["p"]) >= 0.57]
    cohort(rec, "production P1 recommended (>= 57%)")
    rt = json.load(open(os.path.join(HERE, "realline_test_rows.json")))
    print(f"  research cohort (pull 2026-10-16 ~18:40 UTC, test days only): {len(rt)} units / {len({r['match'] for r in rt})} matches; "
          f"days {min(r['day'] for r in rt)} .. {max(r['day'] for r in rt)}")
    rv = collections.Counter(r["version"] for r in rt)
    print("     versions:", dict(rv.most_common()))
    pf = {r["forecast_id"] for r in rows}
    rf = {r["f"] for r in rt}
    print(f"     overlap by forecast id: {len(pf & rf)} in both; research only {len(rf - pf)}; production only {len(pf - rf)}")
    po = [r for r in rows if r["forecast_id"] not in rf]
    print("     production-only by match day:", dict(sorted(collections.Counter(day(r["start"]) for r in po).items())))
    ro = [r for r in rt if r["f"] not in pf]
    print("     research-only by day:", dict(sorted(collections.Counter(r["day"] for r in ro).items())))
    # same unit, different forecast chosen?
    pk = {(r["match"], r["prop"], r["scope"], round(r["line"], 1)): r["forecast_id"] for r in rows}
    print("     research-only units whose (match, stat, scope, line) is in production with another forecast:",
          sum(1 for r in ro if (r["match"], r["prop"], r["scope"], round(r["line"], 1)) in pk))

    # ---------------------------------------------------------------- 2. probability accuracy, each existing candidate
    print("\n=== 2. probability accuracy vs live (forward rows of each candidate; positive gain = candidate better)")
    chs = {c["id"]: c for c in ex["challengers"]}
    res = {}
    for cid, c in chs.items():
        fw = [dict(r, p_live=r["challengers"][cid]["p_base"], p_ch=r["challengers"][cid]["p"])
              for r in rows if cid in r["challengers"] and r["logged"] > c["frozen_at"] and r["start"] > c["frozen_at"]
              and r["challengers"][cid]["p_base"] is not None]
        print(f"\n  {cid} ({c['family']}, {c['status']}, frozen {c['frozen_at'][:16]})")
        if not fw:
            print("    no settled forward rows")
            continue
        gap = sum(abs(u["p_live"] - u["p_live_logged"]) for u in fw) / len(fw)
        print(f"    live arm = live replay on the same inputs; mean |replay - logged chance| = {gap:.4f}")
        pb = prob_block(fw, "p_live", "p_ch", "live", "cand")
        show_prob("forward", pb, "live", "cand")
        pbB = prob_block(fw, "p_BASE", "p_ch", "BASE", "cand")
        print(f"    vs BASE on the same rows: log-loss gain {pbB['ll_gain']:+.5f} {fmt_ci(pbB['ll_gain_ci95'])}, Brier gain {pbB['brier_gain']:+.5f} {fmt_ci(pbB['brier_gain_ci95'])}")
        pbL = prob_block(fw, "p_BASE", "p_live", "BASE", "live")
        print(f"    live vs BASE on the same rows: log-loss gain {pbL['ll_gain']:+.5f} {fmt_ci(pbL['ll_gain_ci95'])}, Brier gain {pbL['brier_gain']:+.5f} {fmt_ci(pbL['brier_gain_ci95'])}")
        print("    selection (favoured side >= 57%), same rows:")
        sL, sC, sB = select(fw, "p_live"), select(fw, "p_ch"), select(fw, "p_BASE")
        show_sel("live", sL); show_sel("candidate", sC); show_sel("BASE", sB)
        # projection where the candidate has its own
        pj = [u for u in fw if u["challengers"][cid]["lam"] is not None and u["challengers"][cid]["lam_base"] is not None]
        if pj and any(abs(u["challengers"][cid]["lam"] - u["challengers"][cid]["lam_base"]) > 1e-9 for u in pj):
            for prop in ("kills", "hs"):
                g = [u for u in pj if u["prop"] == prop]
                if not g:
                    continue
                ga = lambda u: abs(u["challengers"][cid]["lam_base"] - u["actual"]) - abs(u["challengers"][cid]["lam"] - u["actual"])
                bl = sum(u["challengers"][cid]["lam_base"] - u["actual"] for u in g) / len(g)
                bc = sum(u["challengers"][cid]["lam"] - u["actual"] for u in g) / len(g)
                bline = sum(u["line"] - u["actual"] for u in g) / len(g)
                gline = lambda u: abs(u["line"] - u["actual"]) - abs(u["challengers"][cid]["lam"] - u["actual"])
                print(f"    projection {prop} ({len(g)}): MAE gain cand vs live {sum(map(ga, g)) / len(g):+.3f} {fmt_ci(ci(g, ga), 3)}; "
                      f"bias live {bl:+.2f} cand {bc:+.2f} line {bline:+.2f}; cand vs line MAE gain {sum(map(gline, g)) / len(g):+.3f} {fmt_ci(ci(g, gline), 3)}")
        res[cid] = {"prob": pb, "vs_base": pbB, "live_vs_base": pbL, "sel": {"live": sL, "cand": sC, "base": sB}}

    # ---------------------------------------------------------------- 3. live itself (all P1 rows are prospective forecasts)
    print("\n=== 3. live on every P1 row (each forecast was saved before its match)")
    for r in rows:
        r["p_live"] = r["p"]
    pbL = prob_block(rows, "p_BASE", "p_live", "BASE", "live")
    show_prob("live vs BASE", pbL, "BASE", "live")
    print("    selection:")
    show_sel("live", select(rows, "p_live"))
    for v in sorted({r["version"] for r in rows}, key=lambda v: min(r["start"] for r in rows if r["version"] == v)):
        g = [r for r in rows if r["version"] == v]
        s = select(g, "p_live")
        if s["picks"]:
            print(f"      {v}: {s['picks']} picks / {s['matches']} matches, printed {s['printed']:.3f}, hit {s['hit']:.3f} {fmt_ci(s['hit_ci'], 3)}")

    # ---------------------------------------------------------------- 4. the specific error the experiments expose
    print("\n=== 4. where live errs (diagnosis only, nothing fitted): projection bias and over-confidence by the side live favours")
    for side_over in (True, False):
        g = [r for r in rows if (r["p"] > 0.5) == side_over and r["lam"] is not None]
        b_ = lambda u: u["lam"] - u["actual"]
        bl_ = lambda u: u["line"] - u["actual"]
        gp = lambda u: (u["p"] if side_over else 1 - u["p"]) - (u["over"] if side_over else 1 - u["over"])
        print(f"  live favours {'Over' if side_over else 'Under'}: {len(g)} units / {len({u['match'] for u in g})} matches; "
              f"projection - actual {sum(map(b_, g)) / len(g):+.2f} {fmt_ci(ci(g, b_), 2)}, line - actual {sum(map(bl_, g)) / len(g):+.2f} {fmt_ci(ci(g, bl_), 2)}; "
              f"chance - hit {sum(map(gp, g)) / len(g):+.3f} {fmt_ci(ci(g, gp), 3)}")
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 99)):
        for prop in ("kills", "hs"):
            g = [r for r in rows if r["prop"] == prop and r["lam"] is not None and lo <= abs(r["lam"] - r["line"]) < hi]
            if len(g) < 20:
                continue
            agree = lambda u: int((u["lam"] > u["line"]) == bool(u["over"]))
            print(f"  {prop} |projection - line| {lo}-{hi}: {len(g)} units / {len({u['match'] for u in g})} matches; projection's side hit "
                  f"{sum(map(agree, g)) / len(g):.3f} {fmt_ci(ci(g, agree), 3)}")
    json.dump({"export": fn, "results": res}, open(os.path.join(HERE, "decision_results.json"), "w"), default=str, indent=1)


if __name__ == "__main__":
    main()
