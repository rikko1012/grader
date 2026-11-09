"""lpf-1 LIVE path for LoL player props (flag LOL_LPF1=1; default OFF - nothing changes unless the flag is set).

What it is: the lpf-1 full-history projection DISTRIBUTION (lol/lpf.py, lol/EVALUATION.md lpf-1 2026-11-05) run on the live
board. A frozen artifact (lol/lpf1_model.json.gz, written offline by lol/lpf1_train.py from a read-only copy of
production's history) holds, per stat, one gradient-boosted Poisson correction to the live per-game projection (the
booster starts from log(live lol-1f per-game lambda) and learns the correction from 93 pre-match features plus the game
number and the game's result), the walk-forward win / decider model, and the negative-binomial sizes per (stat, scope,
role). The forecast mixes the won-game and lost-game means over the series paths exactly as the research harness
(lol/lpf_eval.Candidate.comps, variant W) and reads the chance at the line off that mixture (over = more than the line).

Accepted 2026-11-09 under the owner's hit-rate rule (logs/esports_hit_2026-11-09.md): on the 2,059 settled forward lines
(Oct 17 - Nov 8, walk-forward refits) hit 56.0% -> 59.5%, Over picks 48.0% -> 52.8%, Under picks 60.3% -> 63.4%, every line
still graded, Over share 34.4% -> 37.0%. The book line is NEVER an input (only the place the chance is read).

Runtime: numpy only (no lightgbm / scipy / sklearn on the server). The trees are evaluated by Trees below (tested equal to
lightgbm's own raw prediction). Inputs come from production's history tables (lol_player_games / lol_games through
lol/history.DB) and the data center (c_series, Leaguepedia Tournaments) - games on days strictly before the match day.
Where it runs (memory, 2026-11-09): ONLY in a short-lived child process (`python -m lol.lpf1_live predict`, started by
lol/lpf1_client.run_child from the LoL worker's forecast cycle). Neither the web process nor the LoL worker imports this
module. Measured on the full history (130,405 player-games): child peak RSS 366 MB for ~2.5 s, then it exits; the calling
process +0.5 MB. A failure never falls back to another model's grade: the lines are skipped for that cycle so the last
saved forecast stands (transient-failure rule).
"""
import gzip
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import lpf
from .lpf1_client import ARTIFACT, STATS, VERSION, enabled, live_stats  # noqa: F401  (one definition, stdlib module)


# ============================================================================================ trees (numpy, no lightgbm)
class Trees:
    """LightGBM dump_model() trees flattened to arrays; raw(X) = sum of leaf values (the booster's raw score, without the
    init score). Numerical splits follow LightGBM's NumericalDecision (missing types None / Zero / NaN, default_left);
    categorical splits ('a||b||c' sets) send negative / NaN values right, as CategoricalDecision."""

    def __init__(self, dump):
        self.names = list(dump["feature_names"])
        self.trees = [self._flatten(t["tree_structure"]) for t in dump["tree_info"]]

    @staticmethod
    def _flatten(root):
        feat, thr, cats, dleft, miss, left, right, value = [], [], [], [], [], [], [], []

        def add(node):
            i = len(feat)
            feat.append(-1)
            thr.append(0.0)
            cats.append(None)
            dleft.append(False)
            miss.append("None")
            left.append(-1)
            right.append(-1)
            value.append(0.0)
            if "leaf_value" in node and "split_feature" not in node:
                value[i] = float(node["leaf_value"])
                return i
            feat[i] = int(node["split_feature"])
            if node.get("decision_type") == "==":
                cats[i] = {int(x) for x in str(node["threshold"]).split("||") if x != ""}
            else:
                thr[i] = float(node["threshold"])
            dleft[i] = bool(node.get("default_left", False))
            miss[i] = str(node.get("missing_type", "None"))
            li = add(node["left_child"])
            ri = add(node["right_child"])
            left[i], right[i] = li, ri
            return i
        add(root)
        return dict(feat=np.array(feat), thr=np.array(thr), cats=cats, dleft=np.array(dleft), miss=miss,
                    left=np.array(left), right=np.array(right), value=np.array(value))

    @staticmethod
    def _go_left(t, i, x):
        """Vector of booleans: rows x (values of the split feature) going left at node i."""
        if t["cats"][i] is not None:
            ok = np.isfinite(x) & (x >= 0)
            xi = np.where(ok, x, -1).astype(np.int64)
            return ok & np.isin(xi, list(t["cats"][i]))
        m = t["miss"][i]
        v = np.where(np.isnan(x) & (m != "NaN"), 0.0, x)
        is_missing = (np.abs(v) <= 1e-35) if m == "Zero" else (np.isnan(v) if m == "NaN" else np.zeros(len(v), dtype=bool))
        return np.where(is_missing, t["dleft"][i], v <= t["thr"][i])

    def _raw_row(self, x):
        """One row in plain python (fast for the few rows a forecast needs)."""
        total = 0.0
        for t in self.trees:
            P = t.get("_py")
            if P is None:
                P = t["_py"] = (t["feat"].tolist(), t["thr"].tolist(), t["cats"], t["dleft"].tolist(), t["miss"],
                                t["left"].tolist(), t["right"].tolist(), t["value"].tolist())
            feat, thr, cats, dleft, miss, left, right, value = P
            i = 0
            while feat[i] >= 0:
                v = x[feat[i]]
                if cats[i] is not None:
                    go = (v == v) and v >= 0 and int(v) in cats[i]
                else:
                    m = miss[i]
                    if v != v and m != "NaN":
                        v = 0.0
                    if (m == "Zero" and abs(v) <= 1e-35) or (m == "NaN" and v != v):
                        go = dleft[i]
                    else:
                        go = v <= thr[i]
                i = left[i] if go else right[i]
            total += value[i]
        return total

    def raw(self, X):
        X = np.atleast_2d(np.asarray(X, dtype=float))
        if len(X) <= 64:
            return np.array([self._raw_row(r.tolist()) for r in X])
        out = np.zeros(len(X))
        for t in self.trees:
            node = np.zeros(len(X), dtype=np.int64)
            while True:
                leaf = t["feat"][node] < 0
                if leaf.all():
                    break
                for i in np.unique(node[~leaf]):
                    rows = np.where(node == i)[0]
                    gl = self._go_left(t, i, X[rows, t["feat"][i]])
                    node[rows] = np.where(gl, t["left"][i], t["right"][i])
            out += t["value"][node]
        return out


# ============================================================================================ negative-binomial mixture (no scipy)
def _nb_pmf_upto(kmax, mu, size):
    """P(X = 0..kmax) for NB(mean mu, size) by the pmf recursion."""
    mu, size = max(float(mu), 1e-9), float(size)
    q = mu / (size + mu)
    p0 = math.exp(size * math.log(size / (size + mu)))
    out = np.empty(kmax + 1)
    out[0] = p0
    for k in range(kmax):
        out[k + 1] = out[k] * (k + size) / (k + 1) * q
    return out


class MixNB:
    """One mixture of negative binomials: weights W, means M, sizes S (1-d)."""

    def __init__(self, W, M, S):
        W = np.asarray(W, dtype=float)
        self.W, self.M, self.S = W / W.sum(), np.asarray(M, dtype=float), np.asarray(S, dtype=float)

    def mean(self):
        return float((self.W * self.M).sum())

    def cdf(self, k):
        if k < 0:
            return 0.0
        return float(sum(w * _nb_pmf_upto(int(k), m, s).sum() for w, m, s in zip(self.W, self.M, self.S)))

    def probs(self, line):
        """(over, under, push): over = more than the line, push = exactly a whole line."""
        under_eq = self.cdf(math.floor(line))
        whole = abs(line - round(line)) < 1e-9
        push = under_eq - self.cdf(round(line) - 1) if whole else 0.0
        over = max(0.0, 1.0 - under_eq)
        return over, max(0.0, under_eq - push), max(0.0, push)

    def quantile(self, q):
        top = int(self.M.max() + 12 * math.sqrt(self.M.max() + self.M.max() ** 2 / max(self.S.min(), 1e-6))) + 10
        pm = sum(w * _nb_pmf_upto(top, m, s) for w, m, s in zip(self.W, self.M, self.S))
        c = np.cumsum(pm)
        return int(np.searchsorted(c, q - 1e-12))


# ============================================================================================ production history -> lpf store
PCOLS = ("game_key", "player_id", "team_id", "opponent_id", "team", "opponent", "role", "champion", "kills", "deaths", "assists",
         "cs", "gold", "team_kills", "opp_kills", "win", "duration_s", "match_key", "game_number", "best_of", "played_at", "league",
         "damage", "vision")
GCOLS = ("game_key", "match_key", "played_at", "league", "tournament", "blue_kills", "red_kills", "duration_s", "patch")


def _day(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


class ProdStore:
    """The attributes lol/lpf.Features reads (rows / games / match_day / series / tourn), built from production's history
    database (lol/history.DB: lol_player_games, lol_games) and the data center (c_series, Leaguepedia Tournaments).
    Player rows carry only the columns the features use (memory)."""

    def __init__(self, hdb, con=None, tournaments=None, player_table="lol_player_games", game_table="lol_games",
                 series_sql="SELECT sid, tournament_page, tournament, best_of FROM c_series"):
        # the query's own row dicts are kept (one copy of the history at a time - memory)
        rows = [r if isinstance(r, dict) else dict(r) for r in hdb.query(f"SELECT {', '.join(PCOLS)} FROM {player_table} WHERE played_at IS NOT NULL")]
        self.games = {}
        for r in hdb.query(f"SELECT {', '.join(GCOLS)} FROM {game_table}"):
            self.games[r["game_key"]] = r if isinstance(r, dict) else dict(r)
        md = {}
        for r in rows:
            d = _day(r["played_at"])
            md[r["match_key"]] = min(md.get(r["match_key"], d), d)
        self.match_day = md
        for r in rows:
            r["_day"] = md[r["match_key"]]
        rows.sort(key=lambda r: (r["_day"], r["match_key"], r["game_number"] or 0))
        self.rows = rows
        self.extra_rows = 0
        self.series = {}
        if con is not None:
            self.series = {r["sid"]: dict(r) for r in con.execute(series_sql)}
        self.tourn = dict(tournaments or {})
        # the patch a series will be played on is not known before it is played: the latest patch of the same tournament's
        # earlier games (events stay on one patch), else the league's, else any league's (all strictly earlier days)
        self._patch = {}
        for g in self.games.values():
            d = md.get(g["match_key"])
            if not g.get("patch") or d is None:
                continue
            for k in (("t", g.get("tournament")), ("l", g.get("league")), ("g", None)):
                self._patch.setdefault(k, []).append((d, g.get("played_at") or "", g["patch"]))
        for v in self._patch.values():
            v.sort()

    def patch_before(self, day, tournament=None, league=None):
        import bisect
        for k in (("t", tournament), ("l", league), ("g", None)):
            v = self._patch.get(k)
            if not v or (k[0] != "g" and k[1] is None):
                continue
            i = bisect.bisect_left(v, (day,))
            if i:
                return v[i - 1][2]
        return None

    def tournament_info(self, page):
        t = self.tourn.get(page or "") or {}
        return {"intl": t.get("Region") == "International", "level": t.get("TournamentLevel"), "playoffs": t.get("IsPlayoffs") in ("1", 1, True)}


def tournaments_from_center(con):
    """{OverviewPage: Leaguepedia Tournaments row} (what lol_full's lp_Tournaments holds, keyed the same way)."""
    from .center import store
    return {r["OverviewPage"]: r for r in store.current_rows(con, "Tournaments") if r.get("OverviewPage")}


# ============================================================================================ artifact + projector
def load_artifact(path=None):
    p = Path(path or ARTIFACT)
    if not p.exists():
        return None
    raw = p.read_bytes()
    art = json.loads(gzip.decompress(raw).decode("utf-8") if p.suffix == ".gz" else raw.decode("utf-8"))
    art["_sha"] = hashlib.sha256(raw).hexdigest()[:12]
    return art


class Projector:
    """lpf-1 projections for upcoming series from one history snapshot. Build once per history version (forecast.py)."""

    def __init__(self, store, art):
        self.art, self.st = art, store
        self.F = lpf.Features(store, log=lambda *a, **k: None)
        for ro in lpf.ROLES:                 # fill the running role prior, then let the per-row dicts go (memory)
            for s in STATS:
                self.F.role_prior(ro, s, 10 ** 7)
        store.rows = []
        self.trees = {s: Trees(d) for s, d in art["boosters"].items()}
        self.feat = list(art["features"])
        self.win = lpf.WinModel()
        self.win.coef = np.array(art["win"]["coef"], dtype=float)
        self.win.p3coef = None if art["win"].get("p3coef") is None else np.array(art["win"]["p3coef"], dtype=float)
        self.sizes = {}
        for k, v in art["sizes"].items():
            s, sc, role = k.split("|")
            self.sizes[(s, sc, None if role in ("", "None") else role)] = float(v)
        self._fc, self._mu = {}, {}

    def target(self, sid, pid, tid, opp, role, day, league, tournament, best_of, tournament_page=None):
        ti = self.st.tournament_info(tournament_page)
        return {"match": sid, "pid": pid, "day": day, "team": tid, "opp": opp, "role": role, "league": league,
                "tournament": tournament, "patch": self.st.patch_before(day, tournament, league), "best_of": int(best_of or 1),
                "intl": ti["intl"], "level": ti["level"], "lp_playoffs": ti["playoffs"]}

    def features(self, t):
        k = (t["match"], t["pid"], t["day"], t["role"], t["team"], t["opp"])
        if k not in self._fc:
            self._fc[k] = self.F.target(t)
        return self._fc[k]

    def size(self, stat, part, role):
        return self.sizes.get((stat, part, role), self.sizes.get((stat, part, None), 5.0))

    def mixture(self, t, stat, scope, live_lambda_game):
        """MixNB of the scope total and the win chance used. live_lambda_game = the live (lol-1f) per-game projection."""
        f = self.features(t)
        bo = int(t["best_of"] or 1)
        ck = (t["match"], t["pid"], t["day"], t["role"], t["team"], t["opp"], t.get("patch"), bo, stat, float(live_lambda_game))
        mu = self._mu.get(ck)
        if mu is None:
            x = np.array([f.get(n, np.nan) for n in self.feat], dtype=float)
            off = math.log(max(float(live_lambda_game), 1e-3))
            keys = [(n, w) for n in (1, 2, 3) for w in (1.0, 0.0)]
            X = np.array([np.concatenate([x, [float(n), float(n == 3 and bo == 3), w]]) for n, w in keys])
            mu = dict(zip(keys, (float(v) for v in np.exp(self.trees[stat].raw(X) + off))))
            self._mu[ck] = mu
        F1 = {c: np.array([f.get(c, np.nan)], dtype=float) for c in lpf.WinModel.COLS}
        p = float(self.win.p(F1)[0])
        p3 = float(self.win.p3(np.array([p]))[0])
        W = {n: mu[(n, 1.0)] for n in (1, 2, 3)}
        L = {n: mu[(n, 0.0)] for n in (1, 2, 3)}
        q = 1 - p
        if scope == "1":
            comps = [(p, W[1], "1"), (q, L[1], "1")]
        elif scope == "12":
            comps = [(p * p, W[1] + W[2], "12"), (2 * p * q, (W[1] + L[2] + L[1] + W[2]) / 2, "12"), (q * q, L[1] + L[2], "12")]
        elif bo == 3:
            sw = p * p + q * q
            comps = [((1 - p3) * p * p / sw, W[1] + W[2], "12"), ((1 - p3) * q * q / sw, L[1] + L[2], "12"),
                     (p3 * p, (W[1] + L[2] + W[3] + L[1] + W[2] + W[3]) / 2, "123"),
                     (p3 * q, (W[1] + L[2] + L[3] + L[1] + W[2] + L[3]) / 2, "123")]
        else:
            s3, l3 = W[1] + W[2] + W[3], L[1] + L[2] + L[3]
            comps = [(p ** 3, s3, "123"), (3 * p * p * q, s3 * 2 / 3 + l3 / 3, "123"), (3 * p * q * q, s3 / 3 + l3 * 2 / 3, "123"),
                     (q ** 3, l3, "123")]
        mix = MixNB([c[0] for c in comps], [c[1] for c in comps], [self.size(stat, c[2], t["role"]) for c in comps])
        return mix, p, p3

    def project(self, t, stat, scope, live_lambda_game, line):
        mix, p, p3 = self.mixture(t, stat, scope, live_lambda_game)
        po, pu, push = mix.probs(line)
        return {"p_over": po, "p_under": pu, "p_push": push, "mean": mix.mean(), "median": mix.quantile(0.5),
                "range80": [mix.quantile(0.1), mix.quantile(0.9)], "p_win": p, "p_decider": p3}


_CACHE = {}


def projector_for(hdb, con, version_key, art=None):
    """One Projector per history version (version_key: e.g. the last game time + row count); rebuilt when it changes."""
    art = art or load_artifact()
    if art is None:
        raise RuntimeError("lpf-1 artifact missing (lol/lpf1_model.json.gz)")
    key = (version_key, art["_sha"])
    if _CACHE.get("key") != key:
        _CACHE.clear()
        st = ProdStore(hdb, con, tournaments_from_center(con) if con is not None else None)
        _CACHE["proj"] = Projector(st, art)
        _CACHE["key"] = key
    return _CACHE["proj"]


# ============================================================================================ the child process
def peak_rss_mb():
    """This process's peak resident memory (Linux VmHWM; psutil peak working set elsewhere when installed)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return round(getattr(mi, "peak_wset", mi.rss) / 2 ** 20, 1)
    except Exception:       # noqa: BLE001
        return None


def predict_main(argv=None):
    """python -m lol.lpf1_live predict --req <json> --out <json> --center <center db> [--history <sqlite>] [--artifact <gz>]
    Builds the projector once, answers every request ({key: {t, stat, scope, lam_game, line}}), writes {"answers": {key:
    result or None}, "peak_rss_mb", ...} and exits. Run by lol/lpf1_client.run_child from the LoL worker's forecast cycle."""
    import argparse
    import sqlite3
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("predict",))
    ap.add_argument("--req", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--center", required=True)
    ap.add_argument("--history", default=None)
    ap.add_argument("--artifact", default=None)
    a = ap.parse_args(argv)
    # a short-lived child: first in line for the kernel OOM killer and low CPU priority (Linux; no-ops elsewhere), so a
    # memory squeeze ends this cycle's lpf-1 answers (lines skipped, last forecast stands), never the web process
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")
    except OSError:
        pass
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    from . import history
    t0 = time.time()
    req = json.loads(Path(a.req).read_text(encoding="utf-8"))
    art = load_artifact(a.artifact)
    if art is None:
        raise SystemExit("lpf-1 artifact missing")
    hdb = history.DB(dsn="", path=Path(a.history)) if a.history else history.DB()
    con = sqlite3.connect(f"file:{a.center}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        st = ProdStore(hdb, con, tournaments_from_center(con))
        proj = Projector(st, art)
    finally:
        hdb.con.close()
    build_s = round(time.time() - t0, 1)
    answers = {}
    for k, q in req.items():
        t = q["t"]
        try:
            tt = proj.target(t["match"], t["pid"], t["team"], t["opp"], t["role"], int(t["day"]), t["league"], t["tournament"],
                             t["best_of"], t.get("tournament_page"))
            r = proj.project(tt, q["stat"], q["scope"], q["lam_game"], q["line"])
            r["patch_assumed"], r["cutoff"] = tt["patch"], art.get("cutoff")
            answers[k] = r
        except Exception:      # noqa: BLE001 - one bad target never stops the others; it is skipped this cycle
            answers[k] = None
    out = {"answers": answers, "answered": sum(v is not None for v in answers.values()), "build_s": build_s,
           "seconds": round(time.time() - t0, 1), "artifact_sha": art["_sha"], "peak_rss_mb": peak_rss_mb()}
    tmp = Path(a.out).with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, a.out)


if __name__ == "__main__":
    predict_main()
