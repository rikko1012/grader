"""NFL round-2 repairs (audit_1121, branch claude/nfl-r2-1121). Pure numpy on one sm-1 PMF row; the forward worker
calls these per offer, replay() re-applies them from the reason codes, so a replay stays bit-identical.

1. pc-cap (SPORTS_NFL_PMF_CAP, default OFF - calibration evidence only, owner decision; never on without a board
   preview - grades-accuracy-not-fewer, Nov 5 rollback of calibrated chances):
   a degenerate PMF (the model's larger side >= CAP given no push at the offered line) is repaired by mixing in the
   same PMF shifted so its mean sits on the line (the book's view of the role), with the smallest weight that brings
   the larger side down to CAP. The raw side and gp-1 band never move (CAP >= the A band 0.60); it cannot change the
   hit rate, so it is not a grading passer. Cause: stale role history vs a new starter's line (rec 0.5 Under at
   99.8%, rush_att 16.5 Under at 100% on a back with 2 carries/game, pass_yds for a backup QB).
   p >= 0.99 rows went 42-41; p >= 0.95 went 55-63 (stated 98.9%). Walk-forward (cap chosen on earlier days by log
   loss from {none, .99, .97, .95, .92, .90}, grid set after the 42-41 rows were seen, no PREREG): 0.90 (grid edge)
   in 9 of 9 folds, paired log-loss -0.048 per row, game-clustered 95% CI [-0.101, -0.013], 6,710 rows / 43 games.
   When on, the repaired PMF is what the inputs DB stores (card quantiles / other-line re-reads) and what the
   sm-nfl-2 shadow gets as its sm-1 baseline. Any error falls back to the raw PMF.
   PROVER NOTES (2026-11-22, evidence audit_1121/round2/nfl/prover/): (a) the sg-2 'certain' hold (board_view.CERTAIN_P
   0.999) reads the forecast's chances, so with the cap on a prop held today as 'certain' is released and shows an A
   at 90%. (b) Side / letter are unchanged only at the offer's own line: line reads at other lines come from the
   repaired PMF, and on 73 capped real rush PMFs 20 of 425 other-line reads changed their lean (16 lost, 4 gained).
   Evidence: C:/Users/tikic/cs2_scout_ops/logs/audit_1121/round2/nfl/cap/. Rollback: SPORTS_NFL_PMF_CAP=0.

2. rush-1 (SPORTS_NFL_RUSH1, default OFF - SHADOW: failed its walk-forward, see RUSH1 below). Logged as rush1_p_over
   in the same shadow table for RB rush_att / rush_yds offers.

3. F12_freshcap1 shadow (SPORTS_NFL_F12_SHADOW, default OFF): the audit's confirmed same-match cap, logged per
   forward run to <SPORTS_DATA_DIR>/model/shadow_f12.db for forward evaluation only. Never touches a grade, a side,
   a letter or a stake. It rests on essentially one Sunday (11-15, 152 of 179 kept rows) at 17% volume.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta

import numpy as np

from . import dists

_ON = ("1", "true", "yes", "on")
CAP = 0.90
CAP_CODE = "pmf_cap:0.9"


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() in _ON


def cap_on():
    return _flag("SPORTS_NFL_PMF_CAP", "0")


def _shift_to(pmf, lo, ks, target):
    """The same PMF moved so its mean is `target` (x -> x + target - mean, mass split, clipped at the support)."""
    p = np.asarray(pmf, float)[None, :]
    m = float(dists.mean_of(p, ks)[0])
    K = p.shape[1]
    pos = np.clip(ks + (float(target) - m) - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    out = np.zeros(K)
    np.add.at(out, f, p[0] * (1 - w))
    np.add.at(out, np.minimum(f + 1, K - 1), p[0] * w)
    return out


def _max_cond(pmf, lo, line):
    po, pu, pp = dists.line_probs(pmf, lo, line)
    dec = po + pu
    if dec <= 0:
        return None, 0.0
    return ("over", po / dec) if po >= pu else ("under", pu / dec)


def cap_repair(pmf, lo, line, cap=CAP):
    """-> (pmf, weight). weight 0 = unchanged (the same array object). Deterministic (fixed 40-step bisection)."""
    side, p = _max_cond(pmf, lo, line)
    if side is None or p <= cap:
        return pmf, 0.0
    ks = np.arange(lo, lo + len(pmf), dtype=float)
    q = _shift_to(pmf, lo, ks, line)
    base = np.asarray(pmf, float)

    def side_p(w):
        mix = (1 - w) * base + w * q
        po, pu, pp = dists.line_probs(mix, lo, line)
        dec = po + pu
        return ((po if side == "over" else pu) / dec) if dec > 0 else 0.5

    if side_p(1.0) > cap:                       # even the line-centred copy is that sure: take it whole
        return q, 1.0
    a, b = 0.0, 1.0
    for _ in range(40):
        m = (a + b) / 2
        if side_p(m) > cap:
            a = m
        else:
            b = m
    return (1 - b) * base + b * q, b


# ------------------------------------------------------------------------------------------------- rush-1 (shadow)
# R2 rush-1-anchor, parameters of the last walk-forward fold (fit on 10-25..11-16, CRPS on actuals): the RB rushing PMF
# is moved so its mean is L*(1+b) + k*(m - L) (L = offered line, m = PMF mean), spread s. Walk-forward 11-01..11-19 on
# RB rushing rows: 762-818 48.2% vs 720-860 45.6%, +2.66 pt, game CI [-6.69, +11.55], p 0.57 -> FAILED (also R1
# c/s: +0.19 pt, CI [-3.25, +3.64]). Shadow only: logged beside sm-1, never written into a forecast.
RUSH1 = {"rush_att": {"k": 0.0, "b": 0.025, "s": 1.0}, "rush_yds": {"k": 0.0, "b": 0.075, "s": 1.0}}
RUSH1_VERSION = "rush-1-anchor"


def rush1_on():
    return _flag("SPORTS_NFL_RUSH1", "0")


def rush1_layer(pmf, lo, line, market):
    """-> the shadow PMF for one RB rushing offer (or None for other markets)."""
    prm = RUSH1.get(market)
    if prm is None:
        return None
    ks = np.arange(lo, lo + len(pmf), dtype=float)
    p = np.asarray(pmf, float)[None, :]
    m = float(dists.mean_of(p, ks)[0])
    target = float(line) * (1 + prm["b"]) + prm["k"] * (m - float(line))
    return dists.transform(p, lo, ks, np.array([m]), target / max(m, 1e-6), prm["s"])[0]


# --------------------------------------------------------------------------------------------------- F12 shadow
F12_VERSION = "f12-freshcap1"
F12_FROZEN_SHA256 = "a6d73d01c627aa7d87d76f6e5500b02d9a9fc7f02f8a2c19a99822a533e7b4de"   # frozen/nfl/F12_freshcap1.py
F12_FRESH_H = 0.5
F12_KEY = ("event", "team", "position_group", "side")
POS_GROUPS = ("QB", "RB", "WR", "TE", "FB")


def f12_on():
    return _flag("SPORTS_NFL_F12_SHADOW", "0")


def _band(p):
    if p >= 0.60:
        return "A"
    if p >= 0.5559:
        return "B"
    if p >= 0.5345:
        return "C"
    return "Lean"


def _t(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def f12_apply(rows):
    """The frozen F12_freshcap1 rule (audit_1121 frozen/nfl/F12_freshcap1.py, logic copied unchanged).
    rows: dicts with event, team, position_group, side (max-p), p_side, prop_id, book, variant, forecast_at.
    -> [{'keep', 'side', 'grade', 'stake', 'fresh', 'cell_rank'}] in input order."""
    newest = {}
    for r in rows:
        t = _t(r.get("forecast_at"))
        if t is not None:
            k = (str(r.get("prop_id")), str(r.get("book")))
            if k not in newest or t > newest[k]:
                newest[k] = t
    fresh, rank, cells = [False] * len(rows), [None] * len(rows), {}
    for i, r in enumerate(rows):
        t, p = _t(r.get("forecast_at")), r.get("p_side")
        fr = (t is not None and str(r.get("variant")) != "non_standard" and p is not None
              and t >= newest[(str(r.get("prop_id")), str(r.get("book")))] - timedelta(hours=F12_FRESH_H))
        fresh[i] = bool(fr)
        if fr:
            cells.setdefault(tuple(str(r.get(k)) for k in F12_KEY), []).append(i)
    for idx in cells.values():
        idx.sort(key=lambda i: (-float(rows[i]["p_side"]), i))
        for rk, i in enumerate(idx, 1):
            rank[i] = rk
    out = []
    for i, r in enumerate(rows):
        keep = fresh[i] and rank[i] == 1
        p = r.get("p_side")
        out.append({"keep": keep, "side": r.get("side"), "grade": _band(float(p)) if keep else "Lean",
                    "stake": 1.0 if keep else 0.0, "fresh": fresh[i], "cell_rank": rank[i]})
    return out


def position_group(pos):
    pos = (pos or "").upper()
    return pos if pos in POS_GROUPS else "other"


def variant_group(v):
    return "standard" if v in ("standard", "main", "balanced", "unknown", None, "") else "non_standard"


class F12Recorder:
    """NFL shadow log (F12 and/or rush-1, each behind its own flag, both default off). One per forecast_league call. add() for every written sm-1 forecast, close() applies the rule to the run's
    slate and logs one row per forecast. Any error is swallowed (the shadow never affects sm-1)."""

    def __init__(self, forecast_at, path=None):
        from .. import config
        self.forecast_at = forecast_at
        self.path = path or (config.DATA_DIR / "model" / "shadow_f12.db")
        self.rows, self.error = [], None
        self.f12, self.rush1 = f12_on(), rush1_on()

    def add(self, fc, o, row, pmf=None, lo=None):
        try:
            dec = (fc.p_over or 0) + (fc.p_under or 0)
            if dec <= 0:
                return
            po, pu = fc.p_over, fc.p_under
            if pmf is not None:                        # the RAW sm-1 PMF (never the pc-cap repaired one)
                po, pu, _ = dists.line_probs(pmf, lo, o.line)
                dec = po + pu
                if dec <= 0:
                    return
            side = "over" if po >= pu else "under"
            self.rows.append({"forecast_id": fc.forecast_id, "event": o.event_id, "team": row.get("team"),
                              "position_group": position_group(row.get("position")), "side": side,
                              "p_side": (po if side == "over" else pu) / dec, "start_utc": row.get("start_utc"),
                              "prop_id": f"{o.event_id}:{o.player_id}:{o.stat}", "book": o.platform,
                              "variant": variant_group(o.variant), "forecast_at": fc.forecast_at, "stat": o.stat,
                              "line": o.line, "player_id": o.player_id, "chosen_side": fc.chosen_side,
                              "rush1": self._rush1(o, row, pmf, lo)})
        except Exception as e:                                       # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:160]}"

    def _rush1(self, o, row, pmf, lo):
        """rush-1 shadow chance of the Over (given no push) for an RB rushing offer, else None."""
        if not self.rush1 or pmf is None or position_group(row.get("position")) != "RB":
            return None
        q = rush1_layer(pmf, lo, o.line, o.stat)
        if q is None:
            return None
        po, pu, pp = dists.line_probs(q, lo, o.line)
        return po / (po + pu) if po + pu > 0 else None

    def close(self):
        n = 0
        try:
            if self.rows:
                dec = f12_apply(self.rows)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                con = sqlite3.connect(str(self.path), timeout=30)
                try:
                    con.execute("""CREATE TABLE IF NOT EXISTS f12_shadow (shadow_id TEXT PRIMARY KEY, version TEXT,
                        run_at TEXT, forecast_id TEXT, event_id TEXT, player_id TEXT, stat TEXT, line REAL, book TEXT,
                        team TEXT, position_group TEXT, side TEXT, chosen_side TEXT, p_side REAL, variant TEXT,
                        fresh INTEGER, cell_rank INTEGER, keep INTEGER, grade TEXT, stake REAL, rush1_p_over REAL, start_utc TEXT)""")
                    for r, d in zip(self.rows, dec):
                        sid = hashlib.sha256(json.dumps([F12_VERSION, r["forecast_id"], self.forecast_at])
                                             .encode()).hexdigest()[:24]
                        con.execute("INSERT OR IGNORE INTO f12_shadow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (sid, F12_VERSION, self.forecast_at, r["forecast_id"], r["event"], r["player_id"],
                                     r["stat"], r["line"], r["book"], r["team"], r["position_group"], r["side"],
                                     r["chosen_side"], r["p_side"], r["variant"], int(d["fresh"]), d["cell_rank"],
                                     int(d["keep"]), d["grade"], d["stake"], r["rush1"], r["start_utc"]))
                        n += 1
                    con.commit()
                finally:
                    con.close()
        except Exception as e:                                       # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:160]}"
        return {"version": F12_VERSION, "written": n, "rows": len(self.rows), "error": self.error,
                "f12": self.f12, "rush1": self.rush1}
