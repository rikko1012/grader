"""nrm-1: NFL role / minutes-trend MEAN layer on sm-1 for rush_att and rec, in SHADOW (sports/research/REGISTER.md A12).

The sm-1 PMF of the row is moved by one multiplier r = exp(clip(gamma . z, -2, 2)) (every support point x -> r x, mass
split linearly between neighbours; the shape is otherwise kept), z = the row's pre-game role inputs:
  const, log((last game opp + 1) / (EW4 opp + 1)), log((EW2 opp + 1) / (EW4 opp + 1)), L3 snap share - EW4 snap share,
  team spread / 7, (implied team total - 22) / 5, back from a 10+ day absence, lagged QB change, team change
(opp = carries for rush_att, targets for rec). gamma is frozen in the artifact (fitted on box scores only, by Poisson
deviance against the out-of-sample sm-1 mean; the book line is never an input).

Never displayed, never graded, never changes sm-1 (owner 2026-11-09: hidden test). The recorder runs inside the existing
forward job after sm-1 wrote the market, computes its inputs from the sm-1 frame WITHOUT adding columns to it, and writes
to <SPORTS_DATA_DIR>/model/shadow.db: shadow_forecasts (model_version "nrm-1") + nrm1_inputs (base sm-1 PMF / chance of
the same row, the printed sm-1 chance, z). Off switch: SPORTS_SHADOW_NRM1=0. Any error -> skipped; sm-1 untouched.

  python -m sports.model.nrm1 status            counts (admin, never member-facing)
  python -m sports.model.nrm1 heldout <hist.db> the one-time A12 score once its threshold is reached
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .. import config
from . import dists, grade_policy
from .frame import ew_prior, group_starts

VERSION = "nrm-1"
SPORT = "nfl"
MARKETS = ("rush_att", "rec")
OPP = {"rush_att": "carries", "rec": "targets"}
Z_NAMES = ("const", "log_lag1_vs_ew_opp", "log_e2_vs_ew_opp", "snap_trend", "team_spread", "implied_total",
           "back_from_absence", "qb_change", "team_change")
ART_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT = ART_DIR / "nrm1_nfl.json"
MIN_GAMES = 48                          # A12 4
TARGETS = ("prizepicks", "underdog")
_ON = {"1", "true", "yes", "on"}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def enabled():
    return os.environ.get("SPORTS_SHADOW_NRM1", "1").strip().lower() in _ON


# ---------------------------------------------------------------------------------------------------------- inputs
def _col(df, c):
    return np.asarray(df[c], float) if c in df else None


def role_inputs(df, market):
    """z for EVERY row of an sm-1 NFL frame (sorted by player, ts, upcoming rows included; results of upcoming rows are
    NaN). Point-in-time: every term uses the player's EARLIER rows only (ew_prior) or pre-game game context. Exactly the
    research definition (sacc_1109 nfl_cand.mean_Z on proj_nfl_full.augment of the h=4 frame)."""
    n = len(df)
    pid = np.asarray(df["player_id"], dtype=object)
    gs = group_starts(pid)
    o = OPP[market]
    x = _col(df, o)
    ew, _ = ew_prior(x, gs, 4.0)
    lag1, _ = ew_prior(x, gs, 0.02)
    e2, _ = ew_prior(x, gs, 2.0)
    snap, _ = ew_prior(_col(df, "offense_pct"), gs, 4.0)
    ew = np.maximum(np.nan_to_num(ew, nan=1.0), 0.3)
    lag1 = np.where(np.isfinite(lag1), lag1, ew)
    e2 = np.where(np.isfinite(e2), e2, ew)
    snap = np.nan_to_num(snap, nan=0.5)
    l3 = _col(df, "l3_snap")
    l3 = np.where(np.isfinite(l3), l3, snap) if l3 is not None else snap
    ts = np.asarray(df["ts"], float)
    i = np.arange(n)
    has_prev = i - 1 >= gs
    prev = np.where(has_prev, i - 1, i)
    gap = np.where(has_prev, (ts - ts[prev]) / 86400.0, np.nan)
    season = np.asarray(df["season"], dtype=object) if "season" in df else np.zeros(n, dtype=object)
    sk = np.array([f"{a}|{b}" for a, b in zip(pid, season)], dtype=object)
    n_season = (i - group_starts(sk)).astype(float)
    back = np.where(np.isfinite(gap) & (n_season > 0), (gap > 10).astype(float), 0.0)
    team = np.asarray(df["team"], dtype=object)
    team_chg = np.where(has_prev, (team[prev] != team).astype(float), 0.0)

    def g(c, fill):
        v = _col(df, c)
        return np.full(n, fill) if v is None else np.nan_to_num(v, nan=fill)
    imp = g("implied_total", 22.0)
    return np.column_stack([np.ones(n), np.log((lag1 + 1) / (ew + 1)), np.log((e2 + 1) / (ew + 1)), l3 - snap,
                            g("team_spread", 0.0) / 7.0, (imp - 22.0) / 5.0, back, g("qb_change_lag", 0.0), team_chg])


def multiplier(z, gamma):
    return float(np.exp(np.clip(np.asarray(z, float) @ np.asarray(gamma, float), -2.0, 2.0)))


def remap(pmf, lo, r):
    """x -> r x on the integer grid, mass split linearly between neighbours, clipped to the grid (sum kept)."""
    pmf = np.asarray(pmf, float)
    K = len(pmf)
    ks = lo + np.arange(K, dtype=float)
    pos = np.clip(r * ks - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    out = np.bincount(f, weights=pmf * (1 - w), minlength=K)
    out += np.bincount(np.minimum(f + 1, K - 1), weights=pmf * w, minlength=K)
    return out


# ------------------------------------------------------------------------------------------------------------ load
def load(art_path=None):
    """The frozen artifact if its content sha agrees AND it pairs with the live sm-1 NFL artifact; else None."""
    from . import forward
    p = Path(art_path or ARTIFACT)
    if not p.exists():
        return None
    a = json.loads(p.read_text(encoding="utf-8"))
    if a.get("sha") != _sha(a.get("markets"))[:16] or a.get("z_names") != list(Z_NAMES):
        return None
    sm1 = forward.load_artifact(SPORT)
    if sm1 is None or sm1.get("sha") != a.get("pairs_with_sm1"):
        return None
    return a


# ---------------------------------------------------------------------------------------------------------- record
def db(path=None):
    from . import shadow
    con = shadow.db(path)
    con.execute("""CREATE TABLE IF NOT EXISTS nrm1_inputs (shadow_id TEXT PRIMARY KEY, ds1_game_id TEXT,
        ds1_player_id TEXT, eligible INTEGER, z_json TEXT, multiplier REAL, sm1_mean REAL, sm1_pmf_json TEXT,
        sm1_p_over REAL, sm1_p_under REAL, sm1_p_push REAL, live_p_over REAL, live_p_under REAL, live_p_push REAL)""")
    return con


class Recorder:
    """One per NFL forecast_league call; market() after sm-1 wrote that market."""

    def __init__(self, forecast_at, path=None, art=None):
        self.forecast_at = forecast_at
        self.art = art if art is not None else (load() if enabled() else None)
        self.written, self.skipped = 0, {}
        self.con = db(path) if self.art else None

    def active(self, market):
        return self.art is not None and market in self.art["markets"]

    def market(self, market, df, hl, rowpos, items, events, base_of, pmf_of):
        """items: [(offer record id, offer, (game_id, player_id), sm-1 chosen p)]; base_of {gk: frozen sm-1 PMF};
        pmf_of {gk: (printed sm-1 PMF, mean, eligible)}. Reads df, never writes it."""
        if not self.active(market) or not items:
            return 0
        try:
            gamma = self.art["markets"][market]["gamma"]
            Z = role_inputs(df, market)
            lo, ks = dists.grid(market)
            pm, n = {}, 0
            for rid, o, gk, _ in items:
                if gk not in pm:
                    z = Z[rowpos[gk]]
                    r = multiplier(z, gamma)
                    base = np.asarray(base_of[gk], float)
                    pmf = remap(base, lo, r)
                    pm[gk] = (pmf, float(pmf @ ks), z, r, base, float(base @ ks), np.asarray(pmf_of[gk][0], float),
                              bool(pmf_of[gk][2]))
                pmf, mean, z, r, base, bmean, live, elig = pm[gk]
                po, pu, pp = dists.line_probs(pmf, lo, o.line)
                b_po, b_pu, b_pp = dists.line_probs(base, lo, o.line)
                l_po, l_pu, l_pp = dists.line_probs(live, lo, o.line)
                pol = grade_policy.apply(po, pu, o.offered_sides(), "unvalidated")
                ev = events.get(o.event_id)
                sid = _sha([VERSION, self.art["sha"], rid, self.forecast_at])[:24]
                sm1_side = (b_po if pol["chosen_side"] == "over" else b_pu) if pol["chosen_side"] else None
                self.con.execute("INSERT OR IGNORE INTO shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                                 "?,?,?,?,?)",
                                 (sid, VERSION, self.art["sha"], SPORT, market, o.event_id, o.player_id, rid, o.platform,
                                  o.line, self.forecast_at, ev.start_utc if ev else None, mean, po, pu, pp,
                                  ",".join(o.offered_sides()), pol["chosen_side"], pol.get("chosen_p"), pol["band"],
                                  json.dumps({"multiplier": r}, default=float), int(lo),
                                  json.dumps([round(float(x), 12) for x in pmf]), sm1_side))
                self.con.execute("INSERT OR IGNORE INTO nrm1_inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (sid, gk[0], gk[1], int(elig), json.dumps(dict(zip(Z_NAMES, map(float, z)))), r,
                                  bmean, json.dumps([round(float(x), 12) for x in base]), b_po, b_pu, b_pp,
                                  l_po, l_pu, l_pp))
                n += 1
            self.con.commit()
            self.written += n
            return n
        except Exception as e:                                       # noqa: BLE001 - shadow never breaks sm-1
            self.skipped[market] = f"{type(e).__name__}: {str(e)[:160]}"
            return 0

    def close(self):
        if self.con is not None:
            self.con.close()
        return {"model_version": VERSION, "artifact": (self.art or {}).get("sha"), "written": self.written,
                "skipped": self.skipped, "enabled": enabled(), "markets": sorted((self.art or {}).get("markets", {}))}


# --------------------------------------------------------------------------------------------------------- held-out
def heldout_path():
    return config.DATA_DIR / "model" / "heldout_nrm1.json"


def _rows(hcon, shadow_path=None):
    """Last pre-start nrm-1 row per (game, player, stat, line) on target books, game final with a box score."""
    p = Path(shadow_path) if shadow_path else config.DATA_DIR / "model" / "shadow.db"
    if not p.exists():
        return [], None
    con = db(p)
    first = con.execute("SELECT MIN(forecast_at) FROM shadow_forecasts WHERE model_version = ?", (VERSION,)).fetchone()[0]
    raw = con.execute("SELECT f.stat, f.line, f.platform, f.forecast_at, f.event_start, f.p_over, f.p_under, f.p_push, "
                      "i.ds1_game_id, i.ds1_player_id, i.live_p_over, i.live_p_under, i.live_p_push, i.multiplier, "
                      "f.mean, i.sm1_mean FROM shadow_forecasts f JOIN nrm1_inputs i ON i.shadow_id = f.shadow_id "
                      "WHERE f.model_version = ? AND f.forecast_at < f.event_start", (VERSION,)).fetchall()
    con.close()
    last = {}
    for r in raw:
        if r[2] not in TARGETS or (first and (r[4] or "") <= first):          # A12 3: games after the first row
            continue
        k = (r[8], r[9], r[0], float(r[1]))
        if k not in last or r[3] > last[k][3]:
            last[k] = r
    final = {g for (g,) in hcon.execute("SELECT game_id FROM games WHERE league = 'nfl' AND status = 'final'")}
    col = {"rush_att": "carries", "rec": "rec"}
    out = []
    for k, r in last.items():
        if k[0] not in final:
            continue
        y = hcon.execute(f"SELECT {col[k[2]]} FROM nfl_pg WHERE game_id = ? AND player_id = ?", (k[0], k[1])).fetchone()
        if not y or y[0] is None or float(y[0]) == k[3]:
            continue
        out.append({"game": k[0], "player": k[1], "market": k[2], "line": k[3], "y": float(y[0]),
                    "p_over": r[5], "p_under": r[6], "live_p_over": r[10], "live_p_under": r[11],
                    "mean": r[14], "sm1_mean": r[15]})
    return out, first


def hit_score(rows, b=2000, seed=1109):
    """A12 5: side = the lean (P(Over) >= P(Under) -> Over) on every recorded prop at its own line, pushes dropped.
    Hit rate nrm-1 vs the printed sm-1, all props and graded (chosen side >= 53.45%), Over picks and Under picks
    separately; game-clustered bootstrap CIs of the differences; graded counts; Over share."""
    if not rows:
        return {"props": 0}
    games = np.array([r["game"] for r in rows], dtype=object)
    ug, g = np.unique(games, return_inverse=True)
    rng = np.random.default_rng(seed)
    C = np.stack([np.bincount(rng.integers(0, len(ug), len(ug)), minlength=len(ug)) for _ in range(b)]).astype(float)
    over = np.array([r["y"] > r["line"] for r in rows], float)

    def arm(po, pu):
        po, pu = np.array(po, float), np.array(pu, float)
        ov = po >= pu
        pc = np.where(ov, po, pu) / np.maximum(po + pu, 1e-12)
        return ov, pc >= 0.5345, np.where(ov, over, 1 - over)

    def rate(h, m):
        return (C @ np.bincount(g, weights=h * m, minlength=len(ug))) / np.maximum(
            C @ np.bincount(g, weights=m.astype(float), minlength=len(ug)), 1)
    a = arm([r["p_over"] for r in rows], [r["p_under"] for r in rows])
    s = arm([r["live_p_over"] for r in rows], [r["live_p_under"] for r in rows])
    out = {"props": len(rows), "games": int(len(ug)), "over_rate": float(over.mean()),
           "graded_n": {"nrm1": int(a[1].sum()), "sm1": int(s[1].sum())},
           "over_share_graded": {"nrm1": float(a[0][a[1]].mean()) if a[1].any() else None,
                                 "sm1": float(s[0][s[1]].mean()) if s[1].any() else None},
           "flips": int((a[0] != s[0]).sum())}
    for scope in ("all", "graded"):
        for side in ("", "over", "under"):
            ma = np.ones(len(rows), bool) if scope == "all" else a[1]
            ms = np.ones(len(rows), bool) if scope == "all" else s[1]
            if side:
                ma, ms = ma & (a[0] if side == "over" else ~a[0]), ms & (s[0] if side == "over" else ~s[0])
            if not ma.any() or not ms.any():
                continue
            d = rate(a[2], ma) - rate(s[2], ms)
            out[f"{scope}_{side or 'both'}"] = {
                "nrm1": {"n": int(ma.sum()), "hit": float(a[2][ma].mean())},
                "sm1": {"n": int(ms.sum()), "hit": float(s[2][ms].mean())},
                "diff": float(a[2][ma].mean() - s[2][ms].mean()),
                "diff_ci_game": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}
    return out


def decide(sc):
    """A12 6 (pre-stated): pass = hit rate up on all props AND graded props (point estimate > 0, CI upper > 0), neither
    Over picks nor Under picks down, graded count >= sm-1's, graded Over share no further from 50% than sm-1's."""
    if not sc.get("props"):
        return "waiting"
    try:
        h1 = all(sc[f"{s}_both"]["diff"] > 0 and sc[f"{s}_both"]["diff_ci_game"][1] > 0 for s in ("all", "graded"))
        h2 = all(sc[k]["diff"] >= 0 for k in ("all_over", "all_under", "graded_over", "graded_under") if k in sc)
        h3 = sc["graded_n"]["nrm1"] >= sc["graded_n"]["sm1"]
        h4 = abs(sc["over_share_graded"]["nrm1"] - 0.5) <= abs(sc["over_share_graded"]["sm1"] - 0.5) + 1e-12
    except (KeyError, TypeError):
        return "fail - incomplete score (A12 6)"
    return ("pass - owner decision to switch (A12 6)" if h1 and h2 and h3 and h4 else
            "fail - retired for this market (A12 6)")


def progress(shadow_path=None):
    p = Path(shadow_path) if shadow_path else config.DATA_DIR / "model" / "shadow.db"
    if not p.exists():
        return {"first_recorded": None, "events": 0, "threshold_games": MIN_GAMES}
    con = db(p)
    first, ev = con.execute("SELECT MIN(forecast_at), COUNT(DISTINCT event_id) FROM shadow_forecasts WHERE "
                            "model_version = ?", (VERSION,)).fetchone()
    con.close()
    return {"first_recorded": first, "events": ev, "threshold_games": MIN_GAMES}


def heldout(hcon, shadow_path=None, now=None):
    """The one-time A12 score per market once >= 48 final games have a recorded row; never re-scored."""
    from ..contracts import now_utc
    p = heldout_path()
    done = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    rows, first = _rows(hcon, shadow_path)
    for mk in MARKETS:
        rm = [r for r in rows if r["market"] == mk]
        if mk not in done and len({r["game"] for r in rm}) >= MIN_GAMES:
            sc = hit_score(rm)
            done[mk] = {"scored_at": now or now_utc(), "first_recorded": first, **sc, "decision": decide(sc)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(done, indent=1, default=float), encoding="utf-8")
    return done


def status(path=None):
    a = json.loads(ARTIFACT.read_text(encoding="utf-8")) if ARTIFACT.exists() else None
    out = {"model_version": VERSION, "enabled": enabled(), "artifact": (a or {}).get("sha"), "display": "never (shadow only)",
           "loaded": load() is not None, "counts": {}}
    p = Path(path) if path else config.DATA_DIR / "model" / "shadow.db"
    if p.exists():
        con = db(p)
        for st, n, ev in con.execute("SELECT stat, COUNT(*), COUNT(DISTINCT event_id) FROM shadow_forecasts WHERE "
                                     "model_version = ? GROUP BY 1", (VERSION,)):
            out["counts"][st] = {"forecasts": n, "events": ev}
        con.close()
    return out


def main(argv=None):
    import argparse
    import sqlite3
    ap = argparse.ArgumentParser(prog="python -m sports.model.nrm1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("heldout")
    s.add_argument("history")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
        return
    hcon = sqlite3.connect(f"file:{Path(a.history).as_posix()}?mode=ro", uri=True)
    print(json.dumps(heldout(hcon), indent=1, default=float))


if __name__ == "__main__":
    main()
