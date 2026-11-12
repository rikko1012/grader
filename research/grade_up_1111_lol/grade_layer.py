"""gu-lol-1 grade-layer rules G1-G6 (FROZEN.md section 4) on the baseline lpf-1 chances at the forward lines.
The projection and the printed chance are never changed here. Research only.

  python research/grade_up_1111_lol/grade_layer.py <out dir>      (needs <out dir>/rows_base.pkl from score.py)
"""
import json
import os
import pickle
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
B = 2000
BURN_END = "2026-10-24"          # series days before this are training only (G3 / G4)
BANDS = ((0.80, "A+"), (0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-"))


def band(p):
    for c, g in BANDS:
        if p >= c:
            return g
    return "Lean"


class Boot:
    def __init__(self, sids, seed=7):
        self.keys, self.inv = np.unique(np.asarray(sids), return_inverse=True)
        self.nk = len(self.keys)
        self.bi = np.random.default_rng(seed).integers(0, self.nk, (B, self.nk))

    def _s(self, w):
        return np.bincount(self.inv, weights=np.asarray(w, dtype=float), minlength=self.nk)[self.bi].sum(1)

    def ratio(self, num, den):
        n, d = self._s(num), self._s(den)
        return 100 * n[d > 0] / d[d > 0]

    def ci(self, num, den):
        v = self.ratio(num, den)
        return [round(float(np.percentile(v, 2.5)), 1), round(float(np.percentile(v, 97.5)), 1)] if len(v) else [None, None]

    def dci(self, n1, d1, n0, d0):
        a, b, c, d = self._s(n1), self._s(d1), self._s(n0), self._s(d0)
        m = (b > 0) & (d > 0)
        v = 100 * (a[m] / b[m] - c[m] / d[m])
        return [round(float(np.percentile(v, 2.5)), 1), round(float(np.percentile(v, 97.5)), 1)], round(float(v.std()), 2)


def pct(a):
    a = np.asarray(a, dtype=float)
    return round(100 * float(a.mean()), 1) if len(a) else None


def one_sided_ud(r):
    return r["platform"] == "underdog" and len([k for k in ("higher", "lower") if r["sides_d"].get(k)]) == 1


def offered(r, side):
    s = r["sides_d"].get("higher" if side == 1 else "lower")
    if r["platform"] == "underdog":
        return bool(s) and (s.get("status") in (None, "active"))
    return bool(s and s.get("offered"))


def main():
    out = sys.argv[1]
    Z = pickle.load(open(os.path.join(out, "rows_base.pkl"), "rb"))
    keep = [j for j, a in enumerate(Z["base"]) if a is not None]
    rows = [Z["rows"][j] for j in keep]
    A = [Z["base"][j] for j in keep]
    n = len(rows)
    graded = np.array([r["st"] == "graded" for r in rows])
    y = np.array([r["over_hit"] if r["st"] == "graded" else 0 for r in rows], dtype=float)      # pushes: always masked out
    po, pu, mean = (np.array([a[k] for a in A]) for k in range(3))
    line = np.array([float(r["line"]) for r in rows])
    whole = np.isclose(line, np.round(line))
    side = np.where(po >= pu, 1, -1)
    ps = np.where(side == 1, po, pu)
    win = np.where(side == 1, y, 1 - y)
    day = np.array([r["day"] for r in rows])
    stat = np.array([r["stat"] for r in rows])
    scope = np.array([r["scope"] for r in rows])
    bo = np.array([int(r["best_of"] or 0) for r in rows])
    sid = np.array([r["sid"] for r in rows])
    os_ud = np.array([one_sided_ud(r) for r in rows])
    test = day >= BURN_END
    bt = Boot(sid)
    res = {"rows": n, "graded": int(graded.sum()), "push": int((~graded).sum()), "series": bt.nk,
           "test_rows": int((graded & test).sum()), "test_series": len(set(sid[test]))}

    def side_rule(new_side, m=None):
        """owner-rule row for a rule that changes sides only (chance of the new side = the model's chance for it)."""
        m = graded if m is None else (graded & m)
        nw = np.where(new_side == 1, y, 1 - y)
        nps = np.where(new_side == 1, po, pu)
        O0, U0, O1, U1 = side == 1, side == -1, new_side == 1, new_side == -1
        ch = m & (new_side != side)
        dci, dsd = bt.dci(nw * m, m, win * m, m)
        r = {"lines": int(m.sum()), "changed": int(ch.sum()), "base_hit": pct(win[m]), "hit": pct(nw[m]),
             "d_hit": round(pct(nw[m]) - pct(win[m]), 2), "d_ci": dci,
             "changed_rows_hit": [pct(win[ch]), pct(nw[ch])] if ch.any() else None,
             "changed_ci": bt.ci(nw * ch, ch) if ch.any() else None,
             "over_hit": [pct(win[m & O0]), pct(nw[m & O1])], "under_hit": [pct(win[m & U0]), pct(nw[m & U1])],
             "over_share": [pct((O0)[m]), pct((O1)[m])], "data_over_rate": pct(y[m]),
             "letters": [int((m & (ps >= 0.55)).sum()), int((m & (nps >= 0.55)).sum())]}
        c1 = r["d_hit"] > 0
        c2 = r["over_hit"][1] >= r["over_hit"][0] and r["under_hit"][1] >= r["under_hit"][0]
        c3 = r["letters"][1] >= 0.95 * r["letters"][0]
        c4 = abs(r["over_share"][1] - r["data_over_rate"]) <= abs(r["over_share"][0] - r["data_over_rate"]) + 1e-9
        r["clauses"] = {"1 hit up": bool(c1), "2 Over and Under not lower": bool(c2), "3 count": bool(c3), "4 split": bool(c4)}
        ok = c1 and c2 and c3 and c4
        r["verdict"] = "PASS" if ok and dci[0] > 0 else "NEAR-MISS" if ok and r["d_hit"] >= 1.0 else "FAIL"
        if r["d_hit"] > 0:
            r["series_needed"] = int(np.ceil(bt.nk * (1.96 * dsd / r["d_hit"]) ** 2))
        return r

    # ---------------- G1 / G2 projection side vs chance side
    mside = np.where(mean > line, 1, np.where(mean < line, -1, side))
    dis = mside != side
    desc = {}
    for lab, m in (("whole lines", whole), ("half lines", ~whole)):
        for sd, nm in ((-1, "Under side, projection above the line"), (1, "Over side, projection below the line")):
            k = m & dis & (side == sd)
            g = k & graded
            desc[f"{lab}: {nm}"] = {"rows": int(k.sum()), "W": int(win[g].sum()), "L": int((1 - win[g]).sum()), "push": int((k & ~graded).sum()),
                                    "hit": pct(win[g]), "ci": bt.ci(win * g, g) if g.any() else None}
        k = m & ~dis & graded
        desc[f"{lab}: chance side = projection side"] = {"rows": int(k.sum()), "hit": pct(win[k]), "ci": bt.ci(win * k, k)}
    res["G1_G2_descriptive"] = desc
    res["G1 WL-1"] = {"all days": side_rule(np.where(whole & dis, mside, side)), "test days": side_rule(np.where(whole & dis, mside, side), test),
                      "whole lines only": side_rule(np.where(whole & dis, mside, side), whole)}
    res["G2 WL-2"] = {"all days": side_rule(mside), "test days": side_rule(mside, test)}
    res["push_lines"] = {"whole-line rows": int(whole.sum()), "pushes": int((whole & ~graded).sum()),
                         "mean model push chance": round(float((1 - po - pu)[whole].mean()), 3),
                         "push rate": round(float((~graded)[whole].mean()), 3)}

    # ---------------- G3 / G4 letter rules (walk-forward by series day)
    pn = ps / (po + pu)
    days = sorted(set(day[test]))
    adj = ps.copy()
    lean_cell = np.zeros(n, bool)
    log3, log4 = {}, {}
    for d in days:
        tr = graded & (day < d)
        g_all = float(pn[tr].mean() - win[tr].mean())
        for s in ("kills", "assists"):
            for sd in (1, -1):
                cell = (stat == s) & (side == sd)
                k = tr & cell
                off = 0.0
                if k.sum() >= 100:
                    off = float(pn[k].mean() - win[k].mean()) - g_all
                adj[(day == d) & cell] = ps[(day == d) & cell] - off
                log3.setdefault(d, {})[f"{s} {'over' if sd == 1 else 'under'}"] = round(off, 4)
                kl = k & (ps >= 0.55)
                bad = bool(kl.sum() >= 60 and win[kl].mean() < 0.5)
                lean_cell[(day == d) & cell] = bad
                log4.setdefault(d, {})[f"{s} {'over' if sd == 1 else 'under'}"] = [int(kl.sum()), pct(win[kl]), bad]

    def letter_rule(new_letter):
        m = graded & test
        L0, L1 = m & (ps >= 0.55), m & new_letter
        dci, dsd = bt.dci(win * L1, L1, win * L0, L0)
        r = {"letters": [int(L0.sum()), int(L1.sum())], "letter_hit": [pct(win[L0]), pct(win[L1])],
             "d_hit": round(pct(win[L1]) - pct(win[L0]), 2), "d_ci": dci,
             "over_letter_hit": [pct(win[L0 & (side == 1)]), pct(win[L1 & (side == 1)])],
             "under_letter_hit": [pct(win[L0 & (side == -1)]), pct(win[L1 & (side == -1)])],
             "over_letters": [int((L0 & (side == 1)).sum()), int((L1 & (side == 1)).sum())],
             "over_share_of_letters": [pct((side == 1)[L0]), pct((side == 1)[L1])], "data_over_rate": pct(y[m]),
             "gained": {"n": int((L1 & ~L0).sum()), "hit": pct(win[L1 & ~L0])}, "lost": {"n": int((L0 & ~L1).sum()), "hit": pct(win[L0 & ~L1])}}
        c1 = r["d_hit"] > 0
        c2 = (r["over_letter_hit"][1] or 0) >= r["over_letter_hit"][0] and (r["under_letter_hit"][1] or 0) >= r["under_letter_hit"][0]
        c3 = r["letters"][1] >= r["letters"][0]
        c4 = abs(r["over_share_of_letters"][1] - r["data_over_rate"]) <= abs(r["over_share_of_letters"][0] - r["data_over_rate"]) + 1e-9
        r["clauses"] = {"1 lettered hit up": bool(c1), "2 Over and Under not lower": bool(c2), "3 letter count": bool(c3), "4 split": bool(c4)}
        ok = c1 and c2 and c3 and c4
        r["verdict"] = "PASS" if ok and dci[0] > 0 else "NEAR-MISS" if ok and r["d_hit"] >= 1.0 else "FAIL"
        return r

    res["G3 AS-1"] = dict(letter_rule(adj >= 0.55), offsets_by_day=log3)
    res["G4 TH-1"] = dict(letter_rule((ps >= 0.55) & ~lean_cell), cells_by_day=log4)

    # ---------------- G5 one-sided lines
    off_side = np.array([offered(r, s) for r, s in zip(rows, side)])
    two = graded & ~os_ud
    base_letters = two & (ps >= 0.55)
    g5 = {"baseline shown letters (not Underdog one-sided)": {"n": int(base_letters.sum()), "hit": pct(win[base_letters]),
                                                              "ci": bt.ci(win * base_letters, base_letters)}}

    def blk(m):
        m = m & graded
        return {"n": int(m.sum()), "series": len(set(sid[m])), "hit": pct(win[m]), "ci": bt.ci(win * m, m) if m.any() else None,
                "stated": pct(pn[m])}
    over_only = os_ud & np.array([bool(r["sides_d"].get("higher")) for r in rows])
    mult = np.array([float(((r["sides_d"].get("higher") or {}).get("payout_multiplier")) or np.nan) if r["platform"] == "underdog" else np.nan for r in rows])
    g5["Underdog Over-only"] = {
        "lines": blk(over_only), "data over rate": pct(y[over_only & graded]), "by stat": {s: int((over_only & graded & (stat == s)).sum()) for s in ("kills", "assists")},
        "model side Over (= offered)": blk(over_only & (side == 1)), "model side Under (not offered)": blk(over_only & (side == -1)),
        "OS-1 added letters: model Over, chance >= 55%": blk(over_only & (side == 1) & (ps >= 0.55)),
        "model Over, Lean": blk(over_only & (side == 1) & (ps < 0.55)),
        "mean Over payout multiplier": round(float(np.nanmean(mult[over_only])), 3) if over_only.any() else None,
        "by stat, model Over": {s: blk(over_only & (side == 1) & (stat == s)) for s in ("kills", "assists")}}
    g5["Underdog Under-only rows"] = int((os_ud & ~over_only).sum())
    a = g5["Underdog Over-only"]["OS-1 added letters: model Over, chance >= 55%"]
    b0 = g5["baseline shown letters (not Underdog one-sided)"]["hit"]
    g5["OS-1 verdict"] = ("PASS" if a["n"] and a["hit"] >= b0 and a["ci"][0] >= 50 else "FAIL")
    for ot in ("standard", "goblin", "demon"):
        m = np.array([r["platform"] == "prizepicks" and r["odds_type"] == ot for r in rows])
        g5[f"PrizePicks {ot}"] = {"lines": blk(m), "data over rate": pct(y[m & graded]), "model side offered": blk(m & off_side),
                                  "model side not offered": blk(m & ~off_side), "lettered and offered": blk(m & off_side & (ps >= 0.55)),
                                  "model Over": blk(m & (side == 1)), "model Under": blk(m & (side == -1))}
    m = np.array([r["platform"] == "underdog" for r in rows]) & ~os_ud
    g5["Underdog two-sided"] = {"lines": blk(m), "model Over": blk(m & (side == 1)), "model Under": blk(m & (side == -1)),
                                "lettered": blk(m & (ps >= 0.55))}
    res["G5 OS-1"] = g5

    # ---------------- G6 descriptive
    g6 = {"all": blk(np.ones(n, bool)), "Over picks": blk(side == 1), "Under picks": blk(side == -1)}
    for s in ("kills", "assists"):
        for sd, nm in ((1, "Over"), (-1, "Under")):
            g6[f"{s} {nm}"] = blk((stat == s) & (side == sd))
        for sc in ("1", "12", "123"):
            for b_ in (1, 3, 5):
                m = (stat == s) & (scope == sc) & (bo == b_)
                if (m & graded).sum():
                    g6[f"{s} scope {sc} best-of-{b_}"] = dict(blk(m), over=blk(m & (side == 1)), under=blk(m & (side == -1)))
    bands = defaultdict(dict)
    L = np.array([band(p) for p in ps])
    for g in [b_[1] for b_ in BANDS] + ["Lean"]:
        bands["all"][g] = blk(L == g)
        bands["Over"][g] = blk((L == g) & (side == 1))
        bands["Under"][g] = blk((L == g) & (side == -1))
        bands["shown (not Underdog one-sided)"][g] = blk((L == g) & ~os_ud)
    g6["letter bands"] = bands
    g6["lettered"] = {"all": blk(ps >= 0.55), "Over": blk((ps >= 0.55) & (side == 1)), "Under": blk((ps >= 0.55) & (side == -1)),
                      "Lean": blk(ps < 0.55)}
    res["G6 SC-1"] = g6
    json.dump(res, open(os.path.join(HERE, "results_grade_layer.json"), "w"), indent=1)
    for k in ("G1_G2_descriptive", "G1 WL-1", "G2 WL-2", "push_lines", "G3 AS-1", "G4 TH-1"):
        v = dict(res[k])
        v.pop("offsets_by_day", None), v.pop("cells_by_day", None)
        print(k, json.dumps(v, indent=None))
    print(json.dumps(res["G5 OS-1"], indent=1))
    print(json.dumps({k: v for k, v in g6.items() if k != "letter bands"}, indent=None))
    for k, v in bands.items():
        print(k, {g: (x["n"], x["stated"], x["hit"]) for g, x in v.items()})


if __name__ == "__main__":
    main()
