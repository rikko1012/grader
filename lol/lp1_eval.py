"""lp-1 walk-forward evaluation harness (research only; reads JSON exports, writes one report; never touches live data).

  python -m lol.lp1_eval --history hist_players.json --settled settled.json --out lol/eval/lp1_walkforward_2026-11-05.json

INPUTS (read-only exports made earlier the same day)
  --history  every stored player-game (lol_player_games columns incl. player_id / team_id / opponent_id / league / win), the
             12-month LoL history; only games on days BEFORE a target's day ever reach its projection or its parameters.
  --settled  the live record: the last pre-match saved forecast per line (lambda, nb_size, p_over / p_under, inputs) with its
             settlement (s_status, s_actual, s_over_hit).

TWO TESTS
  A. History walk-forward (independent of the forward record): the lol-1 replica (lol/backtest.py lam_game, the live fit's
     priors / params) vs the same with LE / LE+G / +TEMPO, game-1 NB log score per player-game; DISP on series totals. For
     every test day the parameters (g, tempo K, NB size) are fitted on targets of EARLIER days only (g / K daily; NB size
     weekly, at the Monday before). Clustered by match.
  B. Forward record (the exact live projections on the same settled rows): candidate lambda = live lambda x (candidate
     change), parameters refit for every test day (UTC day of the series start) on history before it. Chances from the same
     NB (lol.model.probs). Clustered by series. Calibration (stated vs hit, 5-pt bins, Over / Under, log loss, Brier) and
     selection (graded picks = the side's chance >= 55%, hit rate; offered side at the book) reported separately.

PASS (per candidate, fixed before the forward numbers were read): log-loss paired difference CI entirely below 0;
|stated - hit| <= 2.5 pts overall AND for Over picks AND for Under picks; |Over share - data Over rate| not more than 5 pts
further from the data than live's; graded-prop count >= 90% of live's.
"""
import argparse
import datetime as dt
import json
import math
import sys
from collections import defaultdict

import numpy as np

from . import backtest as bt, lp1, model

STATS = ("kills", "assists")
J = {"kills": 0, "assists": 2}
HIST_FROM = "2026-03-01"
BOOT = 2000


def _day(iso):
    return bt._day(iso)


def iso(d):
    return dt.date.fromordinal(int(d)).isoformat()


# ------------------------------------------------------------------ clustered bootstrap
def cluster_ci(vals, clusters, B=BOOT, seed=1, weights=None):
    """95% CI of the mean of vals, resampling whole clusters."""
    vals = np.asarray(vals, dtype=float)
    keys, inv = np.unique(np.asarray(clusters), return_inverse=True)
    s = np.bincount(inv, weights=vals, minlength=len(keys))
    n = np.bincount(inv, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    b = rng.integers(0, len(keys), (B, len(keys)))
    est = s[b].sum(1) / n[b].sum(1)
    return float(vals.mean()), float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


# ------------------------------------------------------------------ A. history
class History:
    def __init__(self, rows, log=print):
        for r in rows:
            r["pid"] = r.get("player_id") or r.get("player_key")
        self.data = bt.Data(rows)
        T = self.data.targets
        md = self.data.match_day
        seen, games = set(), []
        for r in self.data.rows:
            if r["game_key"] in seen or r.get("win") is None:
                continue
            seen.add(r["game_key"])
            games.append((md[r["match_key"]], r.get("game_number") or 0, r["team_id"], r["opponent_id"], int(r["win"]),
                          r.get("league")))
        self.le = lp1.LeagueElo(games)
        for t in T:
            t["p_le"] = self.le.p_win(t["team"], t["opp"], t["day"])
            t["dL"], t["cross"] = self.le.d_league(t["team"], t["opp"], t["day"])
            t["event"] = lp1.event_key(t["league"], t["day"])
        self.T = T
        self.day = np.array([t["day"] for t in T])
        self.cross = np.array([t["cross"] for t in T])
        self.dL = np.array([t["dL"] for t in T])
        self.match = np.array([t["match"] for t in T])
        self.intl = np.array([lp1.is_intl(t["league"]) for t in T])
        f = bt.features(self.data, T, model.PARAMS["kills"]["hl"])
        f2 = dict(f, p_win=np.array([t["p_le"] for t in T]))
        self.base, self.le_lam, self.y, self.fits, self.ev = {}, {}, {}, {}, {}
        for s in STATS:
            p = model.PARAMS[s]
            self.base[s] = bt.lam_game(f, T, s, p, model.PRIORS)
            self.le_lam[s] = bt.lam_game(f2, T, s, p, model.PRIORS)
            self.y[s] = np.array([t["games"][min(t["games"])][J[s]] for t in T], dtype=float)
            self.fits[s] = lp1.DayFits(self.day, self.cross, self.dL, self.le_lam[s], self.y[s])
        self.prior = {s: np.array([model.PRIORS[s].get(t["role"], np.mean(list(model.PRIORS[s].values()))) for t in T])
                      for s in STATS}
        self._leg = {}
        self._tempo = {}
        self._size = {}
        self._shr = {}
        log(f"history: {len(self.data.rows)} player-games, {len(T)} targets, {int(self.cross.sum())} cross-league")

    # LE+G per target, g fitted for the target's own day on earlier days
    def leg(self, s):
        if s not in self._leg:
            g = np.array([self.fits[s].g(int(d)) for d in self.day])
            self._leg[s] = (self.le_lam[s] * np.exp(g * self.dL), g)
            ev = [t["event"] for t in self.T]
            self.ev[s] = lp1.EventSums(ev, self.day, self.y[s], self._leg[s][0])
        return self._leg[s]

    def event_sums(self, s, ev, d):
        self.leg(s)
        return self.ev[s].sums(ev, d)

    def tempo_k(self, s, d):
        """K for test day d: best game-1 NB log score over international targets of the 365 days before d."""
        key = (s, d)
        if key not in self._tempo:
            leg = self.leg(s)[0]
            if not hasattr(self, "_tsum"):
                self._tsum = {}
            if s not in self._tsum:
                self._tsum[s] = [self.ev[s].sums(t["event"], t["day"]) if t["event"] else (0.0, 0.0, 0) for t in self.T]
            m = self.intl & (self.day < d) & (self.day >= d - lp1.GAP_WINDOW) & np.isfinite(leg) & (leg > 0)
            idx = np.where(m)[0]
            best = (None, math.inf)
            if len(idx) >= lp1.MIN_FIT:
                size = model.R[(s, "1")]
                for k in lp1.KS:
                    mu = leg[idx] * np.array([lp1.tempo_factor(*self._tsum[s][i], k) for i in idx])
                    sc = float(lp1.nb_ll(self.y[s][idx], mu, size).sum())
                    if best[0] is None or sc > best[0]:
                        best = (sc, k)
            self._tempo[key] = best[1]
        return self._tempo[key]

    def shrink_a(self, s, d):
        """SHR exponent for test day d: best game-1 NB log score of lam^a prior^(1-a) on cross-league targets of the 365
        days before d (mean = LE+G)."""
        key = (s, d)
        if key not in self._shr:
            leg = self.leg(s)[0]
            m = self.cross & (self.day < d) & (self.day >= d - lp1.GAP_WINDOW) & np.isfinite(leg) & (leg > 0)
            self._shr[key] = (lp1.fit_shrink(leg[m], self.prior[s][m], self.y[s][m], model.R[(s, "1")])
                              if m.sum() >= lp1.MIN_FIT else 1.0)
        return self._shr[key]

    def series_idx(self, s, scope):
        n = lp1.SCOPE_GAMES[scope]
        idx, tot = [], []
        for i, t in enumerate(self.T):
            if all(g in t["games"] for g in range(1, n + 1)):
                idx.append(i)
                tot.append(sum(t["games"][g][J[s]] for g in range(1, n + 1)))
        return np.array(idx, dtype=int), np.array(tot, dtype=float)

    def size(self, s, scope, d):
        """NB size for (stat, scope) on test day d: ML on cross-league series totals of the 365 days before d, mean = LE+G."""
        key = (s, scope, d)
        if key not in self._size:
            if (s, scope) not in self._size:
                self._size[(s, scope)] = self.series_idx(s, scope)
            idx, tot = self._size[(s, scope)]
            leg = self.leg(s)[0]
            n = lp1.SCOPE_GAMES[scope]
            m = self.cross[idx] & (self.day[idx] < d) & (self.day[idx] >= d - lp1.GAP_WINDOW) & np.isfinite(leg[idx]) & (leg[idx] > 0)
            self._size[key] = lp1.fit_size(leg[idx][m] * n, tot[m]) if m.sum() >= lp1.MIN_FIT else model.R[(s, scope)]
        return self._size[key]


def history_test(H, log=print):
    out = {"from": HIST_FROM, "note": "game-1 NB log score per player-game (size = live R[(stat,'1')]); "
           "positive = candidate better; CI clustered by match"}
    d0 = _day(HIST_FROM)
    for s in STATS:
        base, le = H.base[s], H.le_lam[s]
        leg, g = H.leg(s)
        tf = np.array([lp1.tempo_factor(*H.event_sums(s, t["event"], t["day"]), H.tempo_k(s, int(t["day"])))
                       if t["event"] and t["day"] >= d0 else 1.0 for t in H.T])
        legt = leg * tf
        a = np.array([H.shrink_a(s, int(d)) if (c and d >= d0) else 1.0 for d, c in zip(H.day, H.cross)])
        legs = np.where(np.isfinite(leg) & (leg > 0), lp1.shrink(np.where(leg > 0, leg, 1.0), H.prior[s], a), leg)
        size = model.R[(s, "1")]
        ok = np.isfinite(base) & (base > 0)
        y = H.y[s]
        llb, lll, llg, llt, lls = (lp1.nb_ll(y[ok], v[ok], size) for v in (base, le, leg, legt, legs))
        res = {}
        for lab, mask in (("cross-league", H.cross & (H.day >= d0)), ("international events", H.cross & H.intl & (H.day >= d0)),
                          ("Worlds 2026", H.cross & (H.day >= _day("2026-09-01")) & np.array([t["league"] == "World Championship" for t in H.T])),
                          ("same-league control", (~H.cross) & (H.day >= d0))):
            m = mask[ok]
            if not m.sum():
                continue
            cl = H.match[ok][m]
            row = {"n": int(m.sum()), "matches": int(len(set(cl))),
                   "act_over_proj": {k: round(float(y[ok][m].sum() / v[ok][m].sum()), 3)
                                     for k, v in (("lol-1 replica", base), ("LE", le), ("LE+G", leg), ("LE+G+TEMPO", legt),
                                                  ("LE+G+SHR", legs))}}
            for lab2, v in (("LE", lll), ("LE+G", llg), ("LE+G+TEMPO", llt), ("LE+G+SHR", lls)):
                mean, lo, hi = cluster_ci((v - llb)[m], cl)
                row[f"dLL {lab2} - replica"] = [round(mean, 4), round(lo, 4), round(hi, 4)]
            for lab2, v in (("TEMPO", llt), ("SHR", lls)):
                mean, lo, hi = cluster_ci((v - llg)[m], cl)
                row[f"dLL {lab2} increment over LE+G"] = [round(mean, 4), round(lo, 4), round(hi, 4)]
            res[lab] = row
        gd = {iso(d): g for d, g in sorted({(int(d), float(gg)) for d, gg in zip(H.day, g) if d >= d0 and dt.date.fromordinal(int(d)).day == 1})}
        res["g_on_first_of_month"] = gd
        res["shr_a_on_first_of_month"] = {iso(d): H.shrink_a(s, d) for d in sorted({int(x) for x in H.day if x >= d0 and dt.date.fromordinal(int(x)).day == 1})}
        res["tempo_K_on_first_of_month"] = {iso(d): H.tempo_k(s, d) for d in sorted({int(x) for x in H.day if x >= d0 and dt.date.fromordinal(int(x)).day == 1})}
        # DISP: series totals, cross-league, weekly refit (Monday before), LE+G mean, live size vs refit size
        disp = {}
        for scope in ("12", "123"):
            idx, tot = H._size.get((s, scope)) or H.series_idx(s, scope)
            H._size[(s, scope)] = (idx, tot)
            n = lp1.SCOPE_GAMES[scope]
            m = H.cross[idx] & (H.day[idx] >= d0) & np.isfinite(leg[idx]) & (leg[idx] > 0)
            ii = idx[m]
            mu = leg[ii] * n
            mon = H.day[ii] - (np.array([dt.date.fromordinal(int(d)).weekday() for d in H.day[ii]]))
            sz = np.array([H.size(s, scope, int(w)) for w in mon])
            l_live = lp1.nb_ll(tot[m], mu, model.R[(s, scope)])
            l_new = lp1.nb_ll(tot[m], mu, sz)
            mean, lo, hi = cluster_ci(l_new - l_live, H.match[ii])
            var_ratio = float(np.sum((tot[m] - mu) ** 2) / np.sum(mu + mu ** 2 / model.R[(s, scope)]))
            disp[scope] = {"n": int(m.sum()), "live_size": model.R[(s, scope)], "refit_size_median": float(np.median(sz)),
                           "dLL refit - live size": [round(mean, 4), round(lo, 4), round(hi, 4)],
                           "realized/predicted variance at live size": round(var_ratio, 3)}
        res["DISP series totals (cross-league, LE+G mean)"] = disp
        out[s] = res
        log(f"history {s}: " + json.dumps(res, default=str)[:1500])
    return out


# ------------------------------------------------------------------ B. forward record
def load_forward(rows):
    R = []
    for r in rows:
        if r.get("s_status") != "graded" or r.get("s_push") or r.get("s_over_hit") not in (0, 1) or r.get("stat") not in STATS:
            continue
        inp = json.loads(r["inputs"] or "{}") if isinstance(r.get("inputs"), str) else (r.get("inputs") or {})
        sides = json.loads(r["sides"] or "{}") if isinstance(r.get("sides"), str) else (r.get("sides") or {})
        mk = ((inp.get("team_win") or {}).get("market") or {})
        R.append(dict(r, inp=inp, sides_d=sides, day=_day(r["series_start"]), league=inp.get("league"),
                      p_live=inp.get("p_win"), sf_live=inp.get("strength_factor"),
                      p_mkt=mk.get("p") if mk.get("found", True) and isinstance(mk.get("p"), (int, float)) else None))
    return R


def history_gates(hist):
    """The pre-specified combination rule: an add-on component (TEMPO, SHR on the per-game mean; DISP per scope) enters the
    COMBO for a stat only when its history walk-forward increment over LE+G has a match-clustered 95% CI entirely above 0
    (international events for the mean components, cross-league series totals for DISP). LE+G itself enters when its
    history gain over the replica is above 0 on international events. Decided from history only, never the forward record."""
    gates = {}
    for s in STATS:
        h = hist[s]["international events"]
        disp = hist[s]["DISP series totals (cross-league, LE+G mean)"]
        gates[s] = {"LE+G": h["dLL LE+G - replica"][1] > 0,
                    "TEMPO": h["dLL TEMPO increment over LE+G"][1] > 0,
                    "SHR": h["dLL SHR increment over LE+G"][1] > 0,
                    "DISP": {sc: v["dLL refit - live size"][1] > 0 for sc, v in disp.items()}}
    return gates


def candidate_rows(H, R, ff, gates=None):
    """Per forward row, the candidate (lambda, nb_size) of each variant; parameters refit for the row's day."""
    ew = {s: model.PARAMS[s].get("ew") or 0.0 for s in STATS}
    wr = {s: model.PRIORS["win_ratio"][s] for s in STATS}
    for r in R:
        s, d = r["stat"], r["day"]
        fs = (ff.get("stats") or {}).get(s) or {}
        beta, sd = (fs.get("beta") or {}).get("script_stomp"), (fs.get("sd") or {}).get("script_stomp")
        is1f = r["model_version"] == "lol-1f"
        p_le = H.le.p_win(r["tid"], r["opp_tid"], d)
        dL, cross = H.le.d_league(r["tid"], r["opp_tid"], d)
        g = H.fits[s].g(d)

        def strength(p_new):
            m = lp1.sf_ratio(r["p_live"], r["sf_live"], p_new, wr[s], ew[s])
            return m * (lp1.stomp_ratio(r["p_live"], p_new, beta, sd) if is1f else 1.0)
        m_le = strength(p_le)
        m_g = math.exp(g * dL)
        p_m = r["p_mkt"] if r["p_mkt"] is not None else p_le
        m_mkt = strength(p_m)
        k = H.tempo_k(s, d)
        ev = lp1.event_key(r["league"], d)
        tf = lp1.tempo_factor(*H.event_sums(s, ev, d), k) if ev else 1.0
        size_new = H.size(s, r["scope"], d)
        lam, size = r["lambda"], r["nb_size"]
        n_g = lp1.SCOPE_GAMES[r["scope"]]
        a = H.shrink_a(s, d) if cross else 1.0
        prior = model.PRIORS[s].get(r["role"], np.mean(list(model.PRIORS[s].values())))

        def shr(lam_series):
            return float(lp1.shrink(lam_series / n_g, prior, a)) * n_g
        r["cand"] = {
            "live": (lam, size),
            "LE": (lam * m_le, size),
            "LE+G": (lam * m_le * m_g, size),
            "LE+G+MKT": (lam * m_mkt * m_g, size),
            "LE+G+DISP": (lam * m_le * m_g, size_new),
            "LE+G+TEMPO": (lam * m_le * m_g * tf, size),
            "LE+G+SHR": (shr(lam * m_le * m_g), size),
            "DISP alone": (lam, size_new),
        }
        if gates:
            gt = gates[s]
            lc = lam * (m_le * m_g if gt["LE+G"] else 1.0) * (tf if gt["TEMPO"] else 1.0)
            lc = shr(lc) if gt["SHR"] else lc
            r["cand"]["COMBO (history-gated)"] = (lc, size_new if gt["DISP"].get(r["scope"]) else size)
        r["meta"] = {"p_le": p_le, "dL": dL, "cross": cross, "g": g, "tempo_K": k, "tempo": tf, "size_new": size_new, "shr_a": a,
                     "m_le": m_le, "m_mkt": m_mkt}


def score(R, name):
    from .center.forecast import side_offered
    out = []
    for r in R:
        lam, size = r["cand"][name]
        po, pu, pp = model.probs(lam, size, r["line"])
        over = po >= pu
        q = po / (po + pu)                      # the chance given no push (pushes are not scored)
        p = q if over else 1 - q
        graded = (po if over else pu) >= 0.55   # the letter is read off the raw (printed) chance, as live
        hit = r["s_over_hit"] if over else 1 - r["s_over_hit"]
        off = side_offered(r["platform"], r["sides_d"], "over" if over else "under")
        out.append({"p": p, "q": q, "over": over, "hit": hit, "y": r["s_over_hit"], "sid": r["sid"], "graded": graded,
                    "offered": bool(off), "lam": lam, "unit": (r["sid"], r["pid"], r["stat"], r["scope"]),
                    "actual": r["s_actual"], "stat": r["stat"], "scope": r["scope"]})
    return out


def summarize(S, live=None):
    P = np.array([o["p"] for o in S]); Hh = np.array([o["hit"] for o in S], dtype=float)
    Ov = np.array([o["over"] for o in S]); Q = np.array([o["q"] for o in S]); Y = np.array([o["y"] for o in S], dtype=float)
    cl = np.array([o["sid"] for o in S])
    ll = -(Y * np.log(np.clip(Q, 1e-9, 1)) + (1 - Y) * np.log(np.clip(1 - Q, 1e-9, 1)))
    br = (Q - Y) ** 2

    def gap(m):
        if not m.sum():
            return None
        mean, lo, hi = cluster_ci((P - Hh)[m], cl[m])
        return {"n": int(m.sum()), "stated": round(float(P[m].mean()), 4), "hit": round(float(Hh[m].mean()), 4),
                "gap": round(mean, 4), "gap_ci": [round(lo, 4), round(hi, 4)]}
    res = {"n": len(S), "series": int(len(set(cl))), "log_loss": round(float(ll.mean()), 4), "brier": round(float(br.mean()), 4),
           "over_share": round(float(Ov.mean()), 4), "data_over_rate": round(float(Y.mean()), 4),
           "all": gap(np.ones(len(S), bool)), "over": gap(Ov), "under": gap(~Ov)}
    for s in STATS:
        st = np.array([o["stat"] == s for o in S])
        res[s] = {"all": gap(st), "over": gap(st & Ov), "under": gap(st & ~Ov)}
    for sc in ("1", "12", "123"):
        m = np.array([o["scope"] == sc for o in S])
        res[f"scope {sc}"] = gap(m)
    bins = []
    for lo_ in np.arange(0.50, 0.95, 0.05):
        for sd, msd in (("over", Ov), ("under", ~Ov)):
            m = msd & (P >= lo_) & (P < lo_ + 0.05)
            if m.sum():
                bins.append({"bin": f"{lo_:.2f}-{lo_ + 0.05:.2f}", "side": sd, "n": int(m.sum()),
                             "stated": round(float(P[m].mean()), 4), "hit": round(float(Hh[m].mean()), 4)})
    res["bins"] = bins
    G = np.array([o["graded"] for o in S]); OF = np.array([o["offered"] for o in S])
    sel = {}
    for lab, m in (("graded", G), ("graded_offered", G & OF), ("graded_over", G & Ov), ("graded_under", G & ~Ov)):
        if m.sum():
            mean, lo, hi = cluster_ci(Hh[m], cl[m])
            sel[lab] = {"n": int(m.sum()), "stated": round(float(P[m].mean()), 4), "hit": round(mean, 4), "hit_ci": [round(lo, 4), round(hi, 4)]}
    res["selection"] = sel
    # projection accuracy on deduped units
    U = {}
    for o in S:
        U.setdefault(o["unit"], o)
    res["mae_units"] = round(float(np.mean([abs(o["actual"] - o["lam"]) for o in U.values()])), 4)
    res["act_over_proj_units"] = round(float(sum(o["actual"] for o in U.values()) / sum(o["lam"] for o in U.values())), 4)
    res["n_units"] = len(U)
    if live is not None:
        Ql = np.array([o["q"] for o in live])
        lll = -(Y * np.log(np.clip(Ql, 1e-9, 1)) + (1 - Y) * np.log(np.clip(1 - Ql, 1e-9, 1)))
        brl = (Ql - Y) ** 2
        mean, lo, hi = cluster_ci(ll - lll, cl)
        res["dLL_vs_live"] = [round(mean, 4), round(lo, 4), round(hi, 4)]
        mean, lo, hi = cluster_ci(br - brl, cl)
        res["dBrier_vs_live"] = [round(mean, 4), round(lo, 4), round(hi, 4)]
        Ul = {}
        for o in live:
            Ul.setdefault(o["unit"], o)
        res["mae_change_vs_live"] = round(res["mae_units"] - float(np.mean([abs(o["actual"] - o["lam"]) for o in Ul.values()])), 4)
    return res


def verdict(res, live):
    why = []
    ok = True
    if not (res.get("dLL_vs_live") and res["dLL_vs_live"][2] < 0):
        ok = False
        why.append(f"log-loss CI {res.get('dLL_vs_live')} not entirely below 0")
    for k in ("all", "over", "under"):
        g = res[k]
        if g is None or abs(g["gap"]) > 0.025:
            ok = False
            why.append(f"{k} stated-hit {None if g is None else round(g['gap'] * 100, 1)} pts beyond 2.5")
    dist_live = abs(live["over_share"] - live["data_over_rate"])
    dist = abs(res["over_share"] - res["data_over_rate"])
    if dist - dist_live > 0.05:
        ok = False
        why.append(f"Over share {res['over_share']} is {round((dist - dist_live) * 100, 1)} pts further from the data's Over rate")
    gl, gc = live["selection"]["graded"]["n"], res["selection"].get("graded", {"n": 0})["n"]
    if gc < 0.9 * gl:
        ok = False
        why.append(f"graded {gc} < 90% of live {gl}")
    return ok, why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--settled", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731
    H = History(json.load(open(a.history, encoding="utf-8")), log)
    hist = history_test(H, log)
    R = load_forward(json.load(open(a.settled, encoding="utf-8")))
    ff = json.loads((model.config.PKG_DIR / "factor_fit.json").read_text(encoding="utf-8"))
    gates = history_gates(hist)
    candidate_rows(H, R, ff, gates)
    live = score(R, "live")
    rep_live = summarize(live)
    report = {"version": lp1.VERSION, "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "forward_rows": len(R), "forward_days": sorted({iso(r["day"]) for r in R}),
              "history_test": hist, "history_gates": gates, "forward": {"live": rep_live}, "verdicts": {}}
    names = [n for n in R[0]["cand"] if n != "live"]
    for n in names:
        rep = summarize(score(R, n), live)
        report["forward"][n] = rep
        ok, why = verdict(rep, rep_live)
        report["verdicts"][n] = {"pass": ok, "why": why}
    report["per_day_params"] = {iso(d): {s: {"g": H.fits[s].g(d), "tempo_K": H.tempo_k(s, d), "shr_a": H.shrink_a(s, d),
                                             "size": {sc: H.size(s, sc, d) for sc in ("1", "12", "123")}} for s in STATS}
                                for d in sorted({r["day"] for r in R})}
    report["meta_examples"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["meta"].items()} for r in R[:5]]
    # the combination of the candidates that pass on their own (built from the passing parts)
    passing = [n for n in names if report["verdicts"][n]["pass"]]
    report["passing"] = passing
    json.dump(report, open(a.out, "w", encoding="utf-8"), indent=1, default=str)
    for n in ["live"] + names:
        x = report["forward"][n]
        log(f"{n:12s} LL {x['log_loss']} dLL {x.get('dLL_vs_live')} all {x['all']['stated']}/{x['all']['hit']} "
            f"over {x['over']['stated']}/{x['over']['hit']} (n {x['over']['n']}) under {x['under']['stated']}/{x['under']['hit']} "
            f"overshare {x['over_share']} graded {x['selection']['graded']['n']} hit {x['selection']['graded']['hit']} "
            f"MAE {x['mae_units']} pass {report['verdicts'].get(n)}")
    return report


if __name__ == "__main__":
    main()
