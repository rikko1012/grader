"""pnu-1: NFL projection-distribution candidates (RESEARCH / SHADOW ONLY - never imported by the live path).

Owner 2026-11-05: "dont ruin calibration simply fix projections". Every candidate here changes the PROJECTION
DISTRIBUTION (its mean or its per-player spread) - none is a multiplier on printed chances, none adds a global
Over/Under intercept, none touches stakes. The live sm-1 forecast is the starting point: each saved book-line chance
P(Over) is read as a probit distance z = Phi^-1(P(Over)) = (mean - line) / sd of the live distribution, and a
candidate moves the mean (in sd units) or rescales the sd:

    z' = (z + mean_shift) / k          (mean_shift = 0 and k = 1 reproduce the live chance exactly)

Candidates (all inputs known at forecast time; history read point-in-time, start + 6 h <= forecast_at):

  rr-1  recent role       mean shift from the player's last-2-game opportunity (targets / carries / attempts) and
                          snap share vs his last-8 level - role changes that the long half-lives lag.
  lr-1  line-vs-history   mean shift from (book line - player's last-8 average) / last-8 sd. The book line is on the
                          board before kickoff; when it sits far from the player's history the books price a role /
                          injury change the model has no input for (NFL injury_report = "not used").
  pp-1  participation     mean shift from the player's full-participation (snap share >= 50%) last-8 level vs his
                          all-games last-8 level - the partial-game left tail behind the Under lean.
  dv-1  dispersion        per-family sd inflation for mean uncertainty, larger for thin-history players.
  ma-1  market centre     reference arm from the diagnosis: m* = Lc + w (m - Lc). For a single-line group it is a
                          shrink of z (what the owner rejected) - kept only so the report can show it, never promoted.

Fitting: maximum likelihood on P(Over) of rows SETTLED BEFORE the test day (expanding window), small ridge.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from statistics import NormalDist

import numpy as np

from .dists import _phi

VERSION = "pnu-1"
FAMILY = {"rec": "rec", "rec_yds": "rec", "rush_att": "rush", "rush_yds": "rush", "pass_yds": "pass"}
FAMS = ("rec", "rush", "pass")
OPP_COL = {"rec": "targets", "rush": "carries", "pass": "pass_att"}
STAT_COL = {"rec": "rec", "rec_yds": "rec_yds", "rush_att": "carries", "rush_yds": "rush_yds", "pass_yds": "pass_yds"}
POS = ("RB", "WR", "TE", "QB")
PIT_HOURS = 6                 # model card point-in-time rule: a game counts once start + 6 h <= forecast time
P_CLIP = 1e-3
_N = NormalDist()


def _t(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def season_of(ts):
    d = _t(ts)
    return d.year if d.month >= 3 else d.year - 1


# ----------------------------------------------------------------------------------------------- history features

def load_history(db_path):
    """espn id -> chronological list of game dicts from a ds-1 history db (read-only)."""
    con = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT p.espn_id, p.position_group, p.team, g.season, g.start_utc, p.rec, p.rec_yds, p.carries,"
            " p.rush_yds, p.pass_yds, p.targets, p.pass_att, p.offense_pct FROM nfl_pg p"
            " JOIN games g ON g.game_id = p.game_id AND g.league = 'nfl'"
            " WHERE g.start_utc IS NOT NULL ORDER BY g.start_utc").fetchall()
    finally:
        con.close()
    by = {}
    for r in rows:
        if r[0] in (None, ""):
            continue
        try:
            pid = str(int(float(r[0])))
        except ValueError:
            continue
        by.setdefault(pid, []).append({
            "pos": r[1], "team": r[2], "season": r[3], "start": r[4], "rec": r[5] or 0.0, "rec_yds": r[6] or 0.0,
            "carries": r[7] or 0.0, "rush_yds": r[8] or 0.0, "pass_yds": r[9] or 0.0, "targets": r[10] or 0.0,
            "pass_att": r[11] or 0.0, "offense_pct": r[12]})
    return by


def player_key(player_id):
    return str(player_id).split(":")[-1]


def features(games, forecast_at, market, line):
    """Point-in-time features for one prop. `games` is the player's chronological history (any order is sorted)."""
    cut = (_t(forecast_at) - timedelta(hours=PIT_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    h = [g for g in games if g["start"][:19] <= cut]
    fam = FAMILY[market]
    out = {"n_hist": len(h), "n_season": 0, "r_opp": 0.0, "r_snap": 0.0, "gap": 0.0, "thin": 1.0, "pp": 0.0,
           "pos": h[-1]["pos"] if h else None, "last8_mean": None, "last8_sd": None}
    if not h:
        return out
    season = season_of(forecast_at)
    out["n_season"] = sum(1 for g in h if g["season"] == season)
    out["thin"] = 1.0 if (out["n_season"] < 3 or len(h) < 8) else 0.0
    last8 = h[-8:]
    if len(last8) >= 3:
        opp = np.array([g[OPP_COL[fam]] for g in last8], float)
        out["r_opp"] = float(np.clip((opp[-2:].mean() - opp.mean()) / (opp.std() + 1.0), -3, 3))
        snaps = [g["offense_pct"] for g in last8 if g["offense_pct"] is not None]
        recent = [g["offense_pct"] for g in last8[-2:] if g["offense_pct"] is not None]
        if len(snaps) >= 3 and recent:
            out["r_snap"] = float(np.clip((np.mean(recent) - np.mean(snaps)) / 0.15, -3, 3))
        vals = np.array([g[STAT_COL[market]] for g in last8], float)
        sd = max(float(vals.std()), 0.5 if market in ("rec", "rush_att") else 5.0)
        out["last8_mean"], out["last8_sd"] = float(vals.mean()), sd
        out["gap"] = float(np.clip((float(line) - vals.mean()) / sd, -3, 3))
        full = [g[STAT_COL[market]] for g in last8 if (g["offense_pct"] or 0.0) >= 0.5]
        if 2 <= len(full) < len(last8):      # partial-participation games (injury exits, backup weeks) in the window
            out["pp"] = float(np.clip((np.mean(full) - vals.mean()) / sd, -3, 3))
    return out


# ----------------------------------------------------------------------------------------------- group geometry

def group_geometry(rows):
    """Per (match, player, market) group: the live distribution's sd (from the probit distances across its distinct
    lines; uses only saved pre-match chances, no outcomes) and the consensus line (median offered line). Adds
    `u` = (Lc - L) / s to every row (0 when s is unknown, i.e. single-line groups)."""
    groups = {}
    for r in rows:
        groups.setdefault((r["match_key"], r["player_id"], r["market"]), []).append(r)
    for g in groups.values():
        lc = float(np.median([r["line"] for r in g]))
        by_line = {}
        for r in g:
            by_line.setdefault(r["line"], []).append(r["z"])
        s = None
        if len(by_line) >= 2:
            L = np.array(sorted(by_line), float)
            Z = np.array([np.mean(by_line[x]) for x in sorted(by_line)], float)
            slope = np.polyfit(L, Z, 1)[0]
            if slope < 0:
                s = -1.0 / slope
        for r in g:
            r["lc"] = lc
            r["s"] = s
            r["u"] = 0.0 if s is None else (lc - r["line"]) / s
    return rows


def prepare(rows, history=None):
    """Annotate export rows (gradecore-export-1 schema) in place: z, y, fam, features, geometry."""
    for r in rows:
        po = r["p"] if r["side"] == "over" else 1.0 - r["p"]
        r["po"] = float(min(max(po, P_CLIP), 1 - P_CLIP))
        r["z"] = _N.inv_cdf(r["po"])
        r["fam"] = FAMILY[r["market"]]
        if r.get("status") in ("win", "loss"):
            r["y"] = 1 if (r["status"] == "win") == (r["side"] == "over") else 0
        else:
            r["y"] = None
        r["one_sided_over"] = r["side"] == "over" and r["p"] < 0.5   # Over-only offer (goblin / demon)
        f = features((history or {}).get(player_key(r["player_id"]), []), r["forecast_at"], r["market"], r["line"])
        r.update({k: f[k] for k in ("n_hist", "n_season", "r_opp", "r_snap", "gap", "thin", "pp")})
        r["pos"] = f["pos"] if f["pos"] in POS else ("RB" if r["fam"] == "rush" else "QB" if r["fam"] == "pass" else "WR")
    return group_geometry(rows)


# ----------------------------------------------------------------------------------------------- candidates

def _arrays(rows):
    fi = np.array([FAMS.index(r["fam"]) for r in rows])
    pi = np.array([POS.index(r["pos"]) for r in rows])
    return {"z": np.array([r["z"] for r in rows], float), "fam": fi, "pos": pi,
            "r_opp": np.array([r["r_opp"] for r in rows], float), "r_snap": np.array([r["r_snap"] for r in rows], float),
            "gap": np.array([r["gap"] for r in rows], float), "thin": np.array([r["thin"] for r in rows], float),
            "pp": np.array([r["pp"] for r in rows], float),
            "u": np.array([r["u"] for r in rows], float)}


class Part:
    """One projection component. n = number of parameters; `apply` returns (mean_shift_in_sd, log_k, z_override)."""
    name = ""
    n = 0

    def shift(self, th, A):
        return np.zeros_like(A["z"])

    def logk(self, th, A):
        return np.zeros_like(A["z"])


class RecentRole(Part):
    name, n = "rr-1", 6      # per family: opportunity, snap share

    def shift(self, th, A):
        b = th.reshape(3, 2)
        return b[A["fam"], 0] * A["r_opp"] + b[A["fam"], 1] * A["r_snap"]


class LineGap(Part):
    name, n = "lr-1", 3      # per family

    def shift(self, th, A):
        return th[A["fam"]] * A["gap"]


class Participation(Part):
    """Partial-participation tail: shift by (last-8 full-game mean - last-8 mean) / sd, per family. The live
    opportunity history keeps injury-exit / backup weeks at full weight (model card: the Under lean's left tail)."""
    name, n = "pp-1", 3

    def shift(self, th, A):
        return th[A["fam"]] * A["pp"]


class Dispersion(Part):
    name, n = "dv-1", 4      # per-family log k + thin-history log k

    def logk(self, th, A):
        return th[A["fam"]] + th[3] * A["thin"]


class MarketCentre(Part):
    """m* = Lc + w (m - Lc), w = sigmoid(a + b_pos) in (0, 1): never flips which side of Lc the mean is on."""
    name, n = "ma-1", 5

    def shift(self, th, A):
        w = 1.0 / (1.0 + np.exp(-np.clip(th[0] + 3.0 + th[1:][A["pos"]], -30, 30)))   # th = 0 -> w ~ 0.95 (near live)
        target = A["u"] + w * (A["z"] - A["u"])
        return target - A["z"]


PARTS = {"rr-1": RecentRole, "lr-1": LineGap, "pp-1": Participation, "dv-1": Dispersion, "ma-1": MarketCentre}


class Candidate:
    def __init__(self, names):
        self.names = tuple(names)
        self.parts = [PARTS[n]() for n in self.names]
        self.n = sum(p.n for p in self.parts)
        self.name = "+".join(self.names) if self.names else "live"

    def _split(self, th):
        out, i = [], 0
        for p in self.parts:
            out.append(th[i:i + p.n]); i += p.n
        return out

    def zprime(self, th, A):
        sh = np.zeros_like(A["z"]); lk = np.zeros_like(A["z"])
        for p, t in zip(self.parts, self._split(np.asarray(th, float))):
            sh = sh + p.shift(t, A); lk = lk + p.logk(t, A)
        return (A["z"] + sh) / np.exp(lk)

    def p_over(self, th, rows):
        return np.clip(_phi(self.zprime(th, _arrays(rows))), P_CLIP, 1 - P_CLIP)

    def fit(self, rows, ridge=0.02):
        if not self.n:
            return np.zeros(0)
        A = _arrays(rows)
        y = np.array([r["y"] for r in rows], float)
        ridge_w = np.ones(self.n) * ridge
        if "ma-1" in self.names:   # the shared w level is not shrunk; per-position offsets are
            i = 0
            for p in self.parts:
                if p.name == "ma-1":
                    ridge_w[i] = 0.0
                i += p.n

        def f(th):
            p = np.clip(_phi(self.zprime(th, A)), 1e-6, 1 - 1e-6)
            return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)) + np.sum(ridge_w * th * th))
        return bfgs(f, np.zeros(self.n))


def bfgs(f, x0, iters=200, tol=1e-7, h=1e-5):
    """Small dense BFGS with central-difference gradients (no scipy in the service image)."""
    x = np.array(x0, float); n = len(x); H = np.eye(n)

    def grad(x):
        g = np.zeros(n)
        for i in range(n):
            e = np.zeros(n); e[i] = h
            g[i] = (f(x + e) - f(x - e)) / (2 * h)
        return g
    fx, g = f(x), grad(x)
    for _ in range(iters):
        d = -H @ g
        t, fn = 1.0, None
        while t > 1e-8:
            fn = f(x + t * d)
            if fn <= fx + 1e-4 * t * (g @ d):
                break
            t *= 0.5
        if fn is None or t <= 1e-8:
            break
        xn = x + t * d; gn = grad(xn); s = xn - x; yv = gn - g
        sy = s @ yv
        if sy > 1e-12:
            rho = 1.0 / sy; I = np.eye(n)
            H = (I - rho * np.outer(s, yv)) @ H @ (I - rho * np.outer(yv, s)) + rho * np.outer(s, s)
        conv = abs(fx - fn) < tol
        x, fx, g = xn, fn, gn
        if conv:
            break
    return x


# ----------------------------------------------------------------------------------------------- sides / grades

GRADE_C, GRADE_B, GRADE_A = 0.5345, 0.5559, 0.60     # sports-gp-1 bands (grade_policy.BANDS)


def chosen(p_over, rows):
    """Chosen side and its chance. Over-only offers (live Over with chance under 50%) stay Over."""
    side = np.where(p_over >= 0.5, 1, 0)
    forced = np.array([r["one_sided_over"] for r in rows])
    side = np.where(forced, 1, side)
    pc = np.where(side == 1, p_over, 1 - p_over)
    return side, pc


def letter(pc):
    return np.where(pc >= GRADE_A, "A", np.where(pc >= GRADE_B, "B", np.where(pc >= GRADE_C, "C", "Lean")))


def slate_day(start_at):
    return (_t(start_at) - timedelta(hours=8)).strftime("%Y-%m-%d")

