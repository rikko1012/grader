"""Score the frozen rules (FROZEN_DEFINITIONS.md sections 4-6) walk-forward. Read-only, local rows.json only.
-> stdout + C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/score.json"""
import json
from collections import defaultdict, Counter
import numpy as np

import os
D = os.environ.get("LG_DIR", "C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/")
WEEKS = [("W0", "2026-10-11", "2026-10-19"), ("W1", "2026-10-19", "2026-10-26"), ("W2", "2026-10-26", "2026-11-02"),
         ("W3", "2026-11-02", "2026-11-09")]
B, SEED, MIN_N = 2000, 7, 30


def week(r):
    for w, a, b in WEEKS:
        if a <= r["start"][:10] < b:
            return w
    return None


def side(p):
    if p is None:
        return None
    p = round(float(p), 6)
    return "o" if p > 0.5 else ("u" if p < 0.5 else None)


def win(s, over):
    return None if s is None else int((s == "o") == (over == 1))


def opp(s):
    return {"o": "u", "u": "o"}.get(s)


def stats(rows, key):
    g = [r for r in rows if r[key] is not None]
    w = [win(r[key], r["over"]) for r in g]
    out = {"graded": len(g), "hit": 100 * np.mean(w) if w else None,
           "over_share": 100 * np.mean([r[key] == "o" for r in g]) if g else None}
    for s in ("o", "u"):
        ws = [win(r[key], r["over"]) for r in g if r[key] == s]
        out[s + "_n"], out[s + "_hit"] = len(ws), (100 * np.mean(ws) if ws else None)
    return out


def boot(rows, fn, b=B):
    rng = np.random.default_rng(SEED)
    g = defaultdict(list)
    for r in rows:
        g[r["match"]].append(r)
    keys = list(g)
    vals = []
    for _ in range(b):
        s = [r for k in rng.choice(len(keys), len(keys)) for r in g[keys[k]]]
        v = fn(s)
        if v is not None:
            vals.append(v)
    return [round(float(np.percentile(vals, 2.5)), 2), round(float(np.percentile(vals, 97.5)), 2)] if vals else None


def hitpct(rows, key):
    w = [win(r[key], r["over"]) for r in rows if r[key] is not None]
    return 100 * np.mean(w) if w else None


def compare(rows, key, base="base"):
    a, b_ = stats(rows, base), stats(rows, key)
    d = (b_["hit"] - a["hit"]) if a["hit"] is not None and b_["hit"] is not None else None
    ci = boot(rows, lambda s: (hitpct(s, key) - hitpct(s, base)) if hitpct(s, key) is not None and hitpct(s, base) is not None else None)
    ci_o = {s: boot([r for r in rows], lambda q, s=s: _side_hit(q, key, s)) for s in ("o", "u")}
    passes = (d is not None and ci and ci[0] > 0 and b_["graded"] >= a["graded"]
              and (b_["o_hit"] or 0) >= (a["o_hit"] or 0) and (b_["u_hit"] or 0) >= (a["u_hit"] or 0)
              and abs(b_["over_share"] - 50) <= abs(a["over_share"] - 50) + 1)
    return {"base": a, "new": b_, "diff": d, "diff_ci": ci, "new_side_hit_ci": ci_o, "passes": bool(passes),
            "changed_rows": sum(1 for r in rows if r[key] != r[base]), "matches": len({r["match"] for r in rows})}


def _side_hit(q, key, s):
    w = [win(r[key], r["over"]) for r in q if r[key] == s]
    return 100 * np.mean(w) if w else None


def main():
    rows = json.load(open(D + "rows.json"))
    for r in rows:
        r["wk"] = week(r)
        r["base"] = side(r["p"])
        r["gap"] = None if r["other_line"] is None else r["other_line"] - r["line"]
        r["gap_pct"] = None if r["gap"] is None else 100 * r["gap"] / r["line"]
        r["bump"] = None if r["open_line"] is None else r["line"] - r["open_line"]
    rows = [r for r in rows if r["wk"]]
    out = {"rows": len(rows), "by_week": Counter(r["wk"] for r in rows)}

    trig = {
        "a1": lambda r: r["gap"] is not None and abs(r["gap"]) >= 1.0,
        "a2": lambda r: r["gap_pct"] is not None and abs(r["gap_pct"]) >= 5.0,
        "d1": lambda r: r["bump"] is not None and abs(r["bump"]) >= 1.0,
        "d2": lambda r: r["bump"] is not None and abs(r["bump"]) >= 1.0 and r["hours_cur_line_before_start"] < 3,
    }
    signal = {"a1": lambda r: "o" if r["gap"] > 0 else "u", "a2": lambda r: "o" if r["gap"] > 0 else "u",
              "d1": lambda r: "o" if r["bump"] > 0 else "u", "d2": lambda r: "o" if r["bump"] > 0 else "u"}

    def act(rule, a, r):
        if not trig[rule](r) or a == "keep" or r["base"] is None:
            return r["base"]
        sg = signal[rule](r)
        return sg if a == "follow" else opp(sg)

    test = [r for r in rows if r["wk"] != "W0"]
    out["rules"] = {}
    for rule in trig:
        chosen = {}
        for k, (w, _, _) in enumerate(WEEKS[1:], 1):
            tr = [r for r in rows if r["wk"] in [x[0] for x in WEEKS[:k]] and trig[rule](r) and r["base"] is not None]
            best, best_h = "keep", hitpct([dict(r, s=r["base"]) for r in tr], "s") if tr else None
            info = {"n_train_triggered": len(tr), "keep": best_h}
            if len(tr) >= MIN_N:
                for a in ("follow", "fade"):
                    h = hitpct([dict(r, s=act(rule, a, r)) for r in tr], "s")
                    info[a] = h
                    if h is not None and best_h is not None and h > best_h:
                        best, best_h = a, h
            chosen[w] = {"action": best, **{k2: (round(v, 2) if isinstance(v, float) else v) for k2, v in info.items()}}
        for r in test:
            r["new_" + rule] = act(rule, chosen[r["wk"]]["action"], r)
        res = compare(test, "new_" + rule)
        trg = [r for r in test if trig[rule](r) and r["base"] is not None]
        res["triggered_test_rows"] = len(trg)
        # descriptive only (never used to choose): each fixed action on the triggered test rows
        res["triggered_test_descriptive"] = {a: stats([dict(r, s=act(rule, a, r)) for r in trg], "s") for a in ("keep", "follow", "fade")}
        res["chosen"] = chosen
        out["rules"][rule] = res

    # (b) letter by agreement: discrepancy rows (|gap| >= 0.5), side unchanged; staked set = stake > 0
    def agree(r):
        if r["gap"] is None or abs(r["gap"]) < 0.5 or r["base"] is None:
            return None
        return r["base"] == ("o" if r["gap"] > 0 else "u")
    bres = {}
    for name, f in (("b", lambda r, a: (r["units"] or 0) + 0.5 if a else max(0.0, (r["units"] or 0) - 0.5)),
                    ("b_down", lambda r, a: (r["units"] or 0) if a else 0.0)):
        for r in test:
            a = agree(r)
            r["st_" + name] = (r["units"] or 0) if a is None else f(r, a)
        before = [dict(r, s=r["base"]) for r in test if (r["units"] or 0) > 0 and r["base"]]
        after = [dict(r, s=r["base"]) for r in test if r["st_" + name] > 0 and r["base"]]
        bres[name] = {"staked_before": stats(before, "s"), "staked_after": stats(after, "s"),
                      "before_ci": boot(before, lambda s: hitpct(s, "s")), "after_ci": boot(after, lambda s: hitpct(s, "s"))}
    ag = [r for r in test if agree(r) is not None]
    bres["agree_rows"] = stats([dict(r, s=r["base"]) for r in ag if agree(r)], "s")
    bres["disagree_rows"] = stats([dict(r, s=r["base"]) for r in ag if not agree(r)], "s")
    bres["agree_minus_disagree_ci"] = boot(ag, lambda s: (hitpct([dict(r, q=r["base"]) for r in s if agree(r)], "q") or 0)
                                           - (hitpct([dict(r, q=r["base"]) for r in s if not agree(r)], "q") or 0))
    out["rules"]["b"] = bres

    # (c) best line: units with both app rows
    for scope_name, pool in (("test", test), ("all", rows)):
        byu = defaultdict(list)
        for r in pool:
            byu[r["unit"]].append(r)
        pairs = [v for v in byu.values() if len(v) == 2 and v[0]["line"] != v[1]["line"]]
        same = [v for v in pairs if v[0]["base"] and v[0]["base"] == v[1]["base"]]
        soft, hard = [], []
        for v in same:
            s = v[0]["base"]
            lo, hi = sorted(v, key=lambda r: r["line"])
            sf, hd = (lo, hi) if s == "o" else (hi, lo)
            soft.append(dict(sf, s=s)); hard.append(dict(hd, s=s))
        both = [dict(r, s=r["base"]) for v in pairs for r in v]
        # c2: the softer line for the P1 book's side, graded with the live side at that softer line
        c2, c2base = [], []
        for v in pairs:
            p1 = [r for r in v if r["is_p1_book"]]
            if not p1 or not p1[0]["base"]:
                continue
            s0 = p1[0]["base"]
            lo, hi = sorted(v, key=lambda r: r["line"])
            sf = lo if s0 == "o" else hi
            c2.append(dict(sf, s=sf["base"])); c2base.append(dict(p1[0], s=s0))
        diff_ci = boot([dict(sf, hs=win(sf["s"], sf["over"]), hh=win(hd["s"], hd["over"])) for sf, hd in zip(soft, hard)],
                       lambda q: 100 * (np.mean([x["hs"] for x in q]) - np.mean([x["hh"] for x in q])) if q else None)
        out.setdefault("c", {})[scope_name] = {
            "units_two_apps_diff_line": len(pairs), "units_same_side": len(same),
            "softer": stats(soft, "s"), "harder": stats(hard, "s"), "soft_minus_hard_ci": diff_ci,
            "both_lines_live": stats(both, "s"),
            "c2_softer_live_side": stats(c2, "s"), "c2_p1_line_live": stats(c2base, "s"),
            "c2_ci_vs_p1": boot([dict(a, hs=win(a["s"], a["over"]), hb=win(b["s"], b["over"])) for a, b in zip(c2, c2base) if a["s"]],
                                lambda q: 100 * (np.mean([x["hs"] for x in q]) - np.mean([x["hb"] for x in q])) if q else None),
            "rows_where_outcome_between_lines": sum(1 for sf, hd in zip(soft, hard) if win(sf["s"], sf["over"]) != win(hd["s"], hd["over"]))}

    out["baseline_test"] = stats([dict(r, s=r["base"]) for r in test], "s")
    out["baseline_all"] = stats([dict(r, s=r["base"]) for r in rows], "s")
    json.dump(out, open(D + "score.json", "w"), indent=1, default=float)
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
