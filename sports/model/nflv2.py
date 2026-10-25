"""sm-nfl-2: the NFL challenger registered in sports/research/REGISTER.md amendment A6 (participation split + cal-2).
Worker / workstation only (numpy). Never changes sm-1 or sm-2; NFL stays contained until a market passes A6 5.

Model (per NFL market, A6 1):
  full-role game   offense_pct >= 0.50 (unknown -> full-role, as in sm-1)
  P_full           sm-1's family around mu_full = E_full[opportunity] x EB efficiency x exp(beta . x), where E_full is
                   the EW mean over the player's PRIOR full-role games (fallback: all games, flag no_full_history) and
                   beta / family parameters are fitted on TRAIN rows whose own game was full-role
  P_partial        empirical y / mu_full of TRAIN rows whose own game was partial (by no_full_history)
  pi               P(full role | pre-game) - logistic regression on TRAIN (EW snap share, previous snap share, EW share
                   of partial games, no_full_history, position)
  PMF              pi P_full + (1 - pi) P_partial, then cal-2 (tune: c = sum y / sum E[X], s by CRPS)

  python -m sports.model.nflv2 tune <history.db>       tune report + frozen configuration (model_config_nflv2.json)
  python -m sports.model.nflv2 fit <history.db>        forward artifact smnfl2_nfl.json + MANIFEST_NFLV2.json
  python -m sports.model.nflv2 diag <history.db>       diagnostic D (post-hoc, spent sm-1 windows; never a gate)
  python -m sports.model.nflv2 heldout <history.db>    one-time held-out H2 scores (box + book lines) when due
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from .. import config
from . import dists, evaluate, featurize2, grade_policy, model, study
from .frame import Frame, ew_prior, group_starts, gshift

VERSION = "sm-nfl-2"
SPORT = "nfl"
MARKETS = ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec")
FULL_SHARE = 0.50
MIN_GROUP = 50
OPP_COLS = ("targets", "carries", "pass_att")
ART_DIR = Path(__file__).resolve().parent / "artifacts"
CONFIG = ART_DIR / "model_config_nflv2.json"
ARTIFACT = ART_DIR / "smnfl2_nfl.json"
MANIFEST = ART_DIR / "MANIFEST_NFLV2.json"
RESULTS = study.RESULTS
H2_MIN_GAMES = 48
H2_BOOK_MIN = (150, 30)               # props, games (A6 3)
DISPLAY_MIN = (300, 100)              # gate-F size (A6 5 display bar)
TARGETS = ("prizepicks", "underdog")
ADDED = ("v2_full", "v2_lag_snap", "v2_ew_partial", "v2_nfh") + tuple(f"v2_ewf_{c}" for c in OPP_COLS)
_ON = {"1", "true", "yes", "on"}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def enabled():
    return os.environ.get("SPORTS_SHADOW_NFLV2", "1").strip().lower() in _ON


# ------------------------------------------------------------------------------------------- participation columns
def augment(df, hl):
    """Participation columns IN PLACE (prior games only; the row's own game is used only as the TRAIN label v2_full).
    df: a featurize.nfl_frame (sorted by player, start). Never changes an existing column."""
    gs = group_starts(df["player_id"])
    h = hl[0] if isinstance(hl, (tuple, list)) else hl
    snap = np.asarray(df["offense_pct"], float)
    up = np.asarray(df["is_up"], float) == 1 if "is_up" in df else np.zeros(len(df), bool)
    snap = np.where(up, np.nan, snap)                       # an upcoming row's own snaps are unknown (never an input)
    full = np.where(np.isfinite(snap), (snap >= FULL_SHARE).astype(float), 1.0)
    df["v2_full"] = np.where(up, np.nan, full)
    df["v2_lag_snap"] = gshift(snap, gs, 1)
    m, _ = ew_prior(np.where(up, np.nan, 1.0 - full), gs, h)
    df["v2_ew_partial"] = m
    for c in OPP_COLS:
        x = np.asarray(df[c], float)
        m, _ = ew_prior(np.where(up | (full == 0), np.nan, x), gs, h)
        df[f"v2_ewf_{c}"] = m
    # no_full_history: no prior full-role game (the same set of games for every opportunity column)
    m, _ = ew_prior(np.where(up | (full == 0), np.nan, 1.0), gs, h)
    df["v2_nfh"] = (~np.isfinite(m)).astype(float)
    return df


def drop(df, before):
    for c in [c for c in df.cols if c not in before]:
        del df.cols[c]


# --------------------------------------------------------------------------------------------------- the model
def _logit_fit(X, y, ridge=1e-4, iters=60):
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30)
        p = 1 / (1 + np.exp(-eta))
        W = np.maximum(p * (1 - p), 1e-9)
        A = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1])
        new = beta + np.linalg.solve(A, X.T @ (y - p))
        if np.max(np.abs(new - beta)) < 1e-8:
            return new
        beta = new
    return beta


PI_COLS = ("ew_offense_pct", "v2_lag_snap", "v2_ew_partial", "v2_nfh")
POS = ("RB", "WR", "TE")


def _pi_design(df, stats=None):
    fit = stats is None
    stats = stats or {"cols": [], "z": {}}
    cols = []
    names = []
    for c in PI_COLS:
        x = np.asarray(df[c], float)
        if fit:
            ok = np.isfinite(x)
            sd = float(np.std(x[ok])) if ok.sum() > 1 else 0.0
            if sd == 0:
                continue
            stats["z"][c] = {"mean": float(np.mean(x[ok])), "sd": sd}
        if c not in stats["z"]:
            continue
        s = stats["z"][c]
        z = (x - s["mean"]) / s["sd"]
        cols.append(np.where(np.isfinite(z), z, 0.0))
        names.append(c)
    pos = np.asarray(df["position"])
    for p in POS:
        x = (pos == p).astype(float)
        if fit:
            if np.std(x) == 0:
                continue
            stats["z"]["pos_" + p] = {"mean": 0.0, "sd": 1.0}
        if "pos_" + p not in stats["z"]:
            continue
        cols.append(x)
        names.append("pos_" + p)
    if fit:
        stats["cols"] = names
    X = np.column_stack([np.ones(len(df))] + cols)
    return X, stats


class V2:
    """Everything sm-nfl-2 needs for one market (JSON-serialisable via config())."""

    def __init__(self, market, hl, feats, family):
        self.market, self.hl, self.feats, self.family = market, tuple(hl), list(feats), family
        self.prior, self.k, self.stats, self.beta, self.par = None, None, None, np.zeros(0), {}
        self.partial, self.pi, self.cal = {}, {}, (1.0, 1.0)

    # ---- pieces
    def _e_full(self, df):
        o = model.SPEC[self.market]["opp"]
        e = np.asarray(df[f"v2_ewf_{o}"], float)
        return np.where(np.isfinite(e), e, np.asarray(df[f"ew_{o}"], float))

    def mu_full(self, df):
        _, _, eff = model.base_inputs(df, self.market, self.prior, self.k)
        e = self._e_full(df)
        mu0 = e * eff
        cols = study._feature_cols(df, SPORT, self.market, self.feats)
        X, _ = model.design(df, cols, self.stats)
        X = np.column_stack([np.ones(len(df)), X])
        mu = mu0 * np.exp(np.clip(X @ self.beta, -3, 3))
        return np.maximum(np.nan_to_num(mu, nan=0.05), 0.05), e, eff

    def p_full_role(self, df):
        X, _ = _pi_design(df, self.pi["stats"])
        return 1 / (1 + np.exp(-np.clip(X @ np.asarray(self.pi["beta"], float), -30, 30)))

    def raw_pmf(self, df):
        lo, ks = dists.grid(self.market)
        mu, e, eff = self.mu_full(df)
        ctx = model.mix_context(df, self.market, mu, e, eff, self.prior)
        pf = model.family_pmf(self.family, self.market, mu, ctx, self.par, lo, ks)
        g = np.asarray(df["v2_nfh"], float)
        rs = [np.asarray(self.partial["1" if gi == 1 else "0"], float) for gi in g]
        pp = dists.ratio(mu, rs, lo, ks)
        pi = self.p_full_role(df)[:, None]
        return pi * pf + (1 - pi) * pp, mu, pi[:, 0]

    def pmf(self, df, cal=None):
        lo, ks = dists.grid(self.market)
        p, mu, pi = self.raw_pmf(df)
        c, s = cal or self.cal
        return dists.transform(p, lo, ks, dists.mean_of(p, ks), c, s), mu, pi

    # ---- fitting (train)
    def fit(self, tr):
        mk = self.market
        y = np.asarray(tr[f"y_{mk}"], float)
        prior = model.fit_priors(tr, mk)
        self.prior = model.fit_mix_priors(tr, mk, prior)
        self.k = model.fit_k(tr, mk, self.prior)
        full = np.asarray(tr["v2_full"], float) == 1
        trf = tr[full]
        _, _, eff = model.base_inputs(trf, mk, self.prior, self.k)
        mu0 = self._e_full(trf) * eff
        cols = study._feature_cols(trf, SPORT, mk, self.feats)
        X, self.stats = model.design(trf, cols)
        X = np.column_stack([np.ones(len(trf)), X])
        self.beta = model.fit_glm(X, y[full], np.log(np.maximum(np.nan_to_num(mu0, nan=0.05), 1e-3)))
        mu_f, e_f, eff_f = self.mu_full(trf)
        ctx = model.mix_context(trf, mk, mu_f, e_f, eff_f, self.prior)
        self.par = model.fit_family_params(trf, mk, mu_f, ctx)
        # partial component: y / mu_full on train partial rows, by no_full_history
        mu_all, _, _ = self.mu_full(tr)
        r = y / np.maximum(mu_all, 1e-6)
        nfh = np.asarray(tr["v2_nfh"], float)
        qs = np.linspace(0.0025, 0.9975, 200)
        pooled = r[~full]
        self.partial = {"pooled_n": int(len(pooled))}
        for gname, gm in (("0", nfh == 0), ("1", nfh == 1)):
            sel = r[(~full) & gm]
            self.partial[gname + "_n"] = int(len(sel))
            src = sel if len(sel) >= MIN_GROUP else pooled
            self.partial[gname] = np.quantile(src, qs).tolist() if len(src) else [0.0]
        # participation model
        Xp, st = _pi_design(tr)
        self.pi = {"stats": st, "beta": _logit_fit(Xp, full.astype(float)).tolist(),
                   "train_full_share": float(full.mean())}
        return self

    def calibrate(self, tu):
        """cal-2 (A6 1e) on TUNE: c = sum y / sum E[X] (clipped 0.90..1.10), then s by CRPS with c fixed."""
        lo, ks = dists.grid(self.market)
        p, _, _ = self.raw_pmf(tu)
        m = dists.mean_of(p, ks)
        y = np.asarray(tu[f"y_{self.market}"], float)
        c = float(np.clip(y.sum() / max(m.sum(), 1e-9), study.C_GRID.min(), study.C_GRID.max()))

        def cr(s):
            return float(dists.crps(dists.transform(p, lo, ks, m, c, s), lo, y).mean())
        s = float(min(study.S_GRID, key=cr))
        self.cal = (c, s)
        return {"c": c, "s": s, "tune_crps_raw": float(dists.crps(p, lo, y).mean()), "tune_crps_cal": cr(s),
                "tune_mean_ratio": float(y.sum() / max(m.sum(), 1e-9))}

    def config(self):
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.floating, np.integer)):
                return o.item()
            if isinstance(o, np.ndarray):
                return clean(o.tolist())
            return o
        return clean({"model_version": VERSION, "market": self.market, "half_lives": list(self.hl),
                      "prior": self.prior, "k": self.k, "features": self.feats, "design": self.stats,
                      "beta": list(self.beta), "family": self.family, "family_params": self.par,
                      "partial": self.partial, "pi": self.pi, "cal": list(self.cal), "full_share": FULL_SHARE})


def from_config(c):
    f = V2(c["market"], c["half_lives"], c["features"], c["family"])
    f.prior, f.k, f.stats = c["prior"], c["k"], c["design"]
    f.beta = np.asarray(c["beta"], float)
    f.par, f.partial, f.pi, f.cal = c["family_params"], c["partial"], c["pi"], tuple(c["cal"])
    return f


def pmf_from_row(cfg_m, row):
    """The calibrated PMF of one saved feature row (replay path: no dataset needed)."""
    f = from_config(cfg_m)
    one = Frame({k: np.array([np.nan if v is None else v], dtype=float) if isinstance(v, (int, float)) or v is None
                 else np.array([v], dtype=object) for k, v in row.items()})
    p, mu, pi = f.pmf(one)
    return p[0], float(dists.mean_of(p, dists.grid(cfg_m["market"])[1])[0]), float(pi[0])


# ------------------------------------------------------------------------------------------------------ frames
def sm1_config():
    return json.loads((ART_DIR / "model_config_nfl.json").read_text(encoding="utf-8"))["markets"]


def sm2_features():
    return {m: v["features"] for m, v in
            json.loads((ART_DIR / "model_config2_nfl.json").read_text(encoding="utf-8"))["markets"].items()}


def study_frame(con, hl):
    df = study._frame(SPORT, con, hl)
    study._nfl_fill(df)
    featurize2.augment(df, SPORT, con, hl)
    augment(df, hl)
    return df


def _metrics(p, lo, ks, y, lref):
    sc = evaluate.scores(p, lo, y, dists.mean_of(p, ks), lref)
    return sc


def _summ(sc, games):
    cal = evaluate.reliability(sc["p_over_ref"], sc["hit_ref"])
    return {"crps": float(np.mean(sc["crps"])), "bias": float(np.mean(sc["err"])), "mae": float(np.mean(sc["ae"])),
            "logloss_ref": float(np.mean(sc["logloss_ref"])), "brier_ref": float(np.mean(sc["brier_ref"])),
            "ece_ref": cal["ece"], "in_the_large_ref": cal["in_the_large"], "cov80_pit": float(np.mean(sc["cov80"])),
            "mean_p_over_ref": float(np.mean(sc["p_over_ref"])), "hit_rate_ref": float(np.mean(sc["hit_ref"])),
            "share_over_favoured_ref": float(np.mean(sc["p_over_ref"] > 0.5)),
            "bias_ci": list(evaluate.boot_mean(sc["err"], games)[1:])}


def compare(f2, f1, d, market, label):
    """sm-nfl-2 (V2) vs sm-1 (study.Fitted) on the rows of d -> report."""
    lo, ks = dists.grid(market)
    y = np.asarray(d[f"y_{market}"], float)
    games = np.asarray(d["game_id"])
    lref = study2_ref(d, market)
    p2, _, pi = f2.pmf(d)
    p1, _ = study.pmf_for(f1, d, market, SPORT)
    s2, s1 = _metrics(p2, lo, ks, y, lref), _metrics(p1, lo, ks, y, lref)
    full = np.asarray(d["v2_full"], float)
    return {"label": label, "rows": int(len(d)), "games": int(len(np.unique(games))),
            "partial_share_observed": float(np.mean(full == 0)), "mean_pi": float(np.mean(pi)),
            "sm_nfl_2": _summ(s2, games), "sm1": _summ(s1, games),
            "paired": {m: evaluate.paired(s2, s1, games, None, m) for m in ("crps", "logloss_ref", "brier_ref", "ae")},
            "mass_below_quarter_line": {"sm_nfl_2": _low_mass(p2, lo, lref), "sm1": _low_mass(p1, lo, lref)}}


def study2_ref(d, market):
    lags = d.attrs.get("lags", {}).get(market)
    with np.errstate(all="ignore"):
        return np.floor(np.nanmean(lags[:, :10], axis=1)) + 0.5


def _low_mass(p, lo, line):
    ks = np.arange(lo, lo + p.shape[1])
    return float(np.mean((p * (ks[None, :] <= 0.25 * np.asarray(line, float)[:, None])).sum(axis=1)))


def run_tune(con, log=print):
    t0 = time.time()
    c1, f2 = sm1_config(), sm2_features()
    out = {"model_version": VERSION, "register": "sports/research/REGISTER.md A6", "phase": "tune (development)",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "markets": {}}
    configs, frames = {}, {}
    for mk in sorted(MARKETS, key=lambda m: tuple(c1[m]["half_lives"])):
        hl = tuple(c1[mk]["half_lives"])
        if hl not in frames:
            frames.clear()
            frames[hl] = study_frame(con, hl)
        df = frames[hl]
        el = model.eligible(df, mk)
        part = model.partition(df, SPORT)
        tr, tu = df[el & (part == "train")], df[el & (part == "tune")]
        f = V2(mk, hl, f2.get(mk, c1[mk]["features"]), c1[mk]["family"]).fit(tr)
        cal = f.calibrate(tu)
        sm1 = study.from_config(c1[mk])                      # sm-1 as frozen (train fit, tune cal-1)
        rep = compare(f, sm1, tu, mk, "TUNE 2025 weeks 1-9 (development; cal-2 fitted here)")
        rep.update(cal_2=cal, features=f.feats, family=f.family, half_lives=list(hl),
                   train_rows=int(len(tr)), train_partial_rows=int((np.asarray(tr["v2_full"]) == 0).sum()),
                   partial_groups={k: v for k, v in f.partial.items() if k.endswith("_n")},
                   pi_beta=dict(zip(["intercept"] + f.pi["stats"]["cols"], f.pi["beta"])))
        ci = rep["paired"]["crps"]["ci_game"]
        rep["dropped_by_sanity_rule"] = bool(ci[0] is not None and ci[0] > 0)
        out["markets"][mk] = rep
        if not rep["dropped_by_sanity_rule"]:
            configs[mk] = f.config()
        log(f"[{mk}] tune CRPS v2-sm1 {rep['paired']['crps']['diff']:+.4f} [{ci[0]:+.4f}, {ci[1]:+.4f}] bias "
            f"{rep['sm_nfl_2']['bias']:+.3f} vs {rep['sm1']['bias']:+.3f} cal {cal} ({time.time() - t0:.0f}s)")
    frames.clear()
    cfg = {"model_version": VERSION, "sport": SPORT, "frozen_at": out["generated_at"], "markets": configs,
           "register": "REGISTER.md A6"}
    CONFIG.write_text(json.dumps(cfg, indent=1, sort_keys=True), encoding="utf-8")
    out["config_sha"] = _sha(cfg)[:16]
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "model_nflv2_tune.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return out


def run_diag(con, log=print):
    """Diagnostic D (A6 7): post-hoc, sm-1's spent windows; never a gate input."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))["markets"]
    c1 = sm1_config()
    out = {"model_version": VERSION, "label": "post-hoc diagnostic - windows already used by sm-1 (REGISTER A6 7); "
           "NOT evidence, never a gate input", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "config_sha": _sha(json.loads(CONFIG.read_text(encoding="utf-8")))[:16], "markets": {}}
    frames = {}
    for mk in sorted(cfg, key=lambda m: tuple(cfg[m]["half_lives"])):
        hl = tuple(cfg[mk]["half_lives"])
        if hl not in frames:
            frames.clear()
            frames[hl] = study_frame(con, hl)
        df = frames[hl]
        el = model.eligible(df, mk)
        part = model.partition(df, SPORT)
        f2, f1 = from_config(cfg[mk]), study.from_config(c1[mk])
        out["markets"][mk] = {w: compare(f2, f1, df[el & (part == w)], mk, lab) for w, lab in
                              (("test", "2025 week 10 - postseason (sm-1 LOCKED test, spent)"),
                               ("test2", "2026 weeks 1-7 (sm-1 secondary locked test, spent)"))}
        for w in ("test", "test2"):
            r = out["markets"][mk][w]
            log(f"[{mk}:{w}] CRPS v2-sm1 {r['paired']['crps']['diff']:+.4f} {r['paired']['crps']['ci_game']} bias "
                f"{r['sm_nfl_2']['bias']:+.3f} vs {r['sm1']['bias']:+.3f}")
    (RESULTS / "model_nflv2_diagnostic_D.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------------ forward fit
def fit_forward(hcon, as_of=None, out_dir=None):
    """Refit the frozen configuration on every eligible row known at as_of (A6 1f) -> artifact + manifest."""
    from ..contracts import now_utc
    from . import datasets, forward
    out_dir = Path(out_dir) if out_dir else ART_DIR
    cfg_p = out_dir / CONFIG.name
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    as_of = as_of or now_utc()
    arts, cur, df = {}, None, None
    for mk in sorted(cfg["markets"], key=lambda m: tuple(cfg["markets"][m]["half_lives"])):
        c = cfg["markets"][mk]
        hl = tuple(c["half_lives"])
        if hl != cur:
            df = None
            df = forward._frame_asof(SPORT, hcon, hl, as_of)
            study._nfl_fill(df)
            featurize2.augment(df, SPORT, hcon, hl, as_of=as_of)
            augment(df, hl)
            cur = hl
        el = model.eligible(df, mk)
        tr = df[el & np.isfinite(df.f(f"y_{mk}"))]
        f = V2(mk, hl, c["features"], c["family"]).fit(tr)
        f.cal = tuple(c["cal"])
        conf = f.config()
        conf["fit_rows"], conf["fit_games"] = int(len(tr)), int(len(np.unique(tr["game_id"])))
        arts[mk] = conf
    art = {"model_version": VERSION, "sport": SPORT, "fitted_at": now_utc(), "data_as_of": as_of,
           "config_sha": _sha(cfg)[:16], "dataset": datasets.coverage(hcon).get(SPORT), "markets": arts,
           "role": "challenger (shadow) - REGISTER.md A6; not displayed, not graded; NFL stays contained"}
    art["sha"] = _sha(art["markets"])[:16]
    p = out_dir / ARTIFACT.name
    p.write_text(json.dumps(art, indent=1, sort_keys=True, default=float), encoding="utf-8")
    from .fwd import _file_sha
    man = {"version": "nflv2-pkg-1", "model_version": VERSION, "artifact_sha": art["sha"], "freeze_at": art["fitted_at"],
           "data_as_of": as_of, "files": {n: {"sha256": _file_sha(out_dir / n), "bytes": (out_dir / n).stat().st_size}
                                          for n in (p.name, cfg_p.name)},
           "held_out_H2": "every NFL game starting after freeze_at (REGISTER.md A6 3): box part once at >= 48 games; "
                          "book part once per market at >= 150 props / 30 games"}
    (out_dir / MANIFEST.name).write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return {"sha": art["sha"], "fitted_at": art["fitted_at"], "markets": {m: v["fit_rows"] for m, v in arts.items()}}


def load(art_path=None, man_path=None):
    """The frozen artifact if its file sha AND content sha match the manifest; else None (shadow skipped)."""
    from .fwd import _file_sha
    p, mp = Path(art_path or ARTIFACT), Path(man_path or MANIFEST)
    if not p.exists() or not mp.exists():
        return None
    man = json.loads(mp.read_text(encoding="utf-8"))
    a = json.loads(p.read_text(encoding="utf-8"))
    if (man.get("files", {}).get(p.name, {}).get("sha256") != _file_sha(p) or a.get("sha") != _sha(a["markets"])[:16]
            or man.get("artifact_sha") != a.get("sha")):
        return None
    a["freeze_at"] = man.get("freeze_at")
    return a


# ------------------------------------------------------------------------------------------------------ shadow
def _db(path=None):
    from . import shadow
    con = shadow.db(path)
    con.execute("""CREATE TABLE IF NOT EXISTS shadow_inputs (shadow_id TEXT PRIMARY KEY, model_version TEXT,
        ds1_game_id TEXT, ds1_player_id TEXT, row_json TEXT, sm1_p_over REAL, sm1_p_under REAL, sm1_p_push REAL,
        p_full_role REAL)""")
    return con


class Recorder:
    """sm-nfl-2 SHADOW recorder (A6 6): one per NFL forecast_league call; market() after sm-1 wrote that market."""

    def __init__(self, hcon, forecast_at, absences=None, context=None, path=None, art=None):
        self.hcon, self.forecast_at, self.absences = hcon, forecast_at, absences or {}
        self.art = art if art is not None else (load() if enabled() else None)
        self.context = context if context is not None else {}
        self.written, self.skipped = 0, {}
        self.con = _db(path) if self.art else None

    def active(self, market):
        return self.art is not None and market in self.art["markets"]

    def market(self, market, df, hl, rowpos, items, events, sm1_probs):
        """items: [(offer record id, offer, (game_id, player_id), sm-1 chosen p)]; sm1_probs {rid: (po, pu, pp)}."""
        if not self.active(market) or not items:
            return 0
        from . import shadow
        from .forward import _row_dict
        am = self.art["markets"][market]
        if tuple(am["half_lives"]) != tuple(hl):
            self.skipped[market] = "half-lives differ from the sm-1 frame"
            return 0
        before = set(df.cols)
        try:
            new_ev = sorted({o.event_id for _, o, _, _ in items} - set(self.context))
            if new_ev:
                self.context.update(shadow.game_context(SPORT, [{"event_id": e} for e in new_ev], self.forecast_at))
                self.context.update({e: {} for e in new_ev if e not in self.context})
            featurize2.augment(df, SPORT, self.hcon, hl, as_of=self.forecast_at, absences=self.absences)
            augment(df, hl)
            lo, _ = dists.grid(market)
            pm, n = {}, 0
            for rid, o, gk, _ in items:
                if gk not in pm:
                    # the full feature row (F_ columns are computed from it, after the game-line / weather context)
                    row = {k: v for k, v in _row_dict(df, rowpos[gk]).items() if not k.startswith("F_")}
                    shadow._apply_context(row, SPORT, self.context.get(o.event_id))
                    pmf, mean, pi = pmf_from_row(am, row)
                    pm[gk] = (pmf, mean, pi, row)
                pmf, mean, pi, row = pm[gk]
                po, pu, pp = dists.line_probs(pmf, lo, o.line)
                pol = grade_policy.apply(po, pu, o.offered_sides(), "unvalidated")
                ev = events.get(o.event_id)
                sid = _sha([VERSION, self.art["sha"], rid, self.forecast_at])[:24]
                s1 = sm1_probs.get(rid, (None, None, None))
                self.con.execute("INSERT OR IGNORE INTO shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                                 "?,?,?,?,?)",
                                 (sid, VERSION, self.art["sha"], SPORT, market, o.event_id, o.player_id, rid, o.platform,
                                  o.line, self.forecast_at, ev.start_utc if ev else None, mean, po, pu, pp,
                                  ",".join(o.offered_sides()), pol["chosen_side"], pol.get("chosen_p"), pol["band"],
                                  json.dumps({"p_full_role": pi}, default=float), int(lo),
                                  json.dumps([round(float(x), 12) for x in pmf]), items_p(items, rid)))
                self.con.execute("INSERT OR IGNORE INTO shadow_inputs VALUES (?,?,?,?,?,?,?,?,?)",
                                 (sid, VERSION, gk[0], gk[1], json.dumps(row, default=str), s1[0], s1[1], s1[2], pi))
                n += 1
            self.con.commit()
            self.written += n
            return n
        except Exception as e:                                       # noqa: BLE001 - shadow never breaks sm-1
            self.skipped[market] = f"{type(e).__name__}: {str(e)[:160]}"
            return 0
        finally:
            drop(df, before)                                         # the sm-1 frame exactly as it was

    def close(self):
        if self.con is not None:
            self.con.close()
        return {"model_version": VERSION, "artifact": (self.art or {}).get("sha"), "written": self.written,
                "skipped": self.skipped, "enabled": enabled(), "markets": sorted((self.art or {}).get("markets", {}))}


def items_p(items, rid):
    for r, _, _, p in items:
        if r == rid:
            return p
    return None


# ------------------------------------------------------------------------------------------------ held-out H2
def h2_path():
    return config.DATA_DIR / "model" / "heldout_nflv2.json"


def h2_check(hcon, now=None):
    """Forward job: COUNT H2 games (bounded SQL) - the one-time score is the CLI (A6 3)."""
    from . import datasets
    art = load()
    if art is None:
        return {"state": "no sm-nfl-2 artifact"}
    done = json.loads(h2_path().read_text(encoding="utf-8")) if h2_path().exists() else {}
    n = hcon.execute("SELECT COUNT(DISTINCT g.game_id) FROM games g WHERE g.league = 'nfl' AND g.status = 'final' AND "
                     "g.start_utc > ? AND g.start_utc <= ? AND EXISTS (SELECT 1 FROM nfl_pg p WHERE p.game_id = "
                     "g.game_id)", (art["freeze_at"], now or datasets.now_utc())).fetchone()[0]
    box = "scored" if "box" in done else (f"waiting ({n}/{H2_MIN_GAMES} games after {art['freeze_at']})"
                                          if n < H2_MIN_GAMES else "threshold reached - run the one-time H2 score")
    return {"state": box, "games": n, "book_scored": sorted((done.get("book") or {}).keys())}


def _y_lookup(hcon, keys):
    out = {}
    stat_col = {"pass_yds": "pass_yds", "rush_yds": "rush_yds", "rec_yds": "rec_yds", "rush_att": "carries",
                "rec": "rec"}
    for g, p in keys:
        r = hcon.execute("SELECT pass_yds, rush_yds, rec_yds, carries, rec FROM nfl_pg WHERE game_id = ? AND "
                         "player_id = ?", (g, p)).fetchone()
        if r:
            out[(g, p)] = {k: (None if r[i] is None else float(r[i])) for i, k in
                           enumerate(("pass_yds", "rush_yds", "rec_yds", "carries", "rec"))}
    return out, stat_col


def book_rows(hcon, shadow_path=None, freeze_at=None):
    """H2-book rows (A6 3): last pre-start sm-nfl-2 shadow forecast per (game, player, stat, line), target books only,
    game final with a box score; pushes excluded. -> {market: [row dict]}."""
    con = _db(shadow_path)
    q = ("SELECT f.shadow_id, f.stat, f.line, f.platform, f.forecast_at, f.event_start, f.p_over, f.p_under, f.p_push, "
         "f.projection_mean, i.ds1_game_id, i.ds1_player_id, i.sm1_p_over, i.sm1_p_under, i.sm1_p_push FROM "
         "shadow_forecasts f JOIN shadow_inputs i ON i.shadow_id = f.shadow_id WHERE f.model_version = ? AND "
         "f.forecast_at < f.event_start")
    rows = con.execute(q, (VERSION,)).fetchall()
    con.close()
    last = {}
    for r in rows:
        # target books only; H2 games only; and (REGISTER 9) only forecasts made after the freeze
        if r[3] not in TARGETS or (freeze_at and ((r[5] or "") <= freeze_at or (r[4] or "") <= freeze_at)):
            continue
        k = (r[10], r[11], r[1], float(r[2]))
        if k not in last or r[4] > last[k][4]:
            last[k] = r
    final = {g for (g,) in hcon.execute("SELECT game_id FROM games WHERE league = 'nfl' AND status = 'final'")}
    ys, col = _y_lookup(hcon, sorted({(k[0], k[1]) for k in last if k[0] in final}))
    out = {}
    for k, r in last.items():
        yv = (ys.get((k[0], k[1])) or {}).get(col[k[2]])
        if yv is None or yv == k[3]:
            continue
        out.setdefault(k[2], []).append({"game": k[0], "line": k[3], "y": yv, "p_over": r[6], "p_under": r[7],
                                         "p_push": r[8], "mean": r[9], "sm1_p_over": r[12], "sm1_p_under": r[13],
                                         "sm1_p_push": r[14]})
    return out


def book_score(rows):
    """A6 4 book-line metrics for one market's rows (pushes already excluded)."""
    g = np.array([r["game"] for r in rows], dtype=object)
    hit = np.array([r["y"] > r["line"] for r in rows], float)

    def cond(po, pp):
        return np.clip(np.array(po, float) / np.maximum(1 - np.array(pp, float), 1e-9), 1e-6, 1 - 1e-6)
    p2 = cond([r["p_over"] for r in rows], [r["p_push"] for r in rows])
    ok1 = np.array([r["sm1_p_over"] is not None for r in rows])
    p1 = cond([r["sm1_p_over"] if r["sm1_p_over"] is not None else 0.5 for r in rows],
              [r["sm1_p_push"] or 0.0 for r in rows])
    ll = lambda p: -(hit * np.log(p) + (1 - hit) * np.log(1 - p))                  # noqa: E731
    br = lambda p: (p - hit) ** 2                                                  # noqa: E731
    itl = evaluate.boot_mean(hit - p2, g)
    rel = evaluate.reliability(p2, hit, min_n=20)
    s = float(np.mean(np.array([r["p_over"] > r["p_under"] for r in rows], float)))
    o = float(hit.mean())
    d_ll = evaluate.boot_mean((ll(p2) - ll(p1))[ok1], g[ok1])
    d_br = evaluate.boot_mean((br(p2) - br(p1))[ok1], g[ok1])
    res = {"props": len(rows), "games": int(len(np.unique(g))), "over_rate": o, "mean_p_over": float(p2.mean()),
           "in_the_large": float(p2.mean() - o), "hit_minus_p": {"mean": itl[0], "ci_game": [itl[1], itl[2]]},
           "ece": rel["ece"], "logloss": float(ll(p2).mean()), "brier": float(br(p2).mean()),
           "logloss_minus_sm1": {"mean": d_ll[0], "ci_game": [d_ll[1], d_ll[2]], "n": int(ok1.sum())},
           "brier_minus_sm1": {"mean": d_br[0], "ci_game": [d_br[1], d_br[2]]},
           "share_over_favoured": s, "sm1_share_over_favoured": float(np.mean(
               np.array([(r["sm1_p_over"] or 0) > (r["sm1_p_under"] or 0) for r in rows], float))),
           "projection_minus_line": float(np.mean([r["mean"] - r["line"] for r in rows]))}
    res["criteria"] = {"e_calibration": abs(res["in_the_large"]) <= 0.03 and itl[1] is not None and itl[1] <= 0 <= itl[2],
                       "f_logloss_not_worse": d_ll[0] is not None and d_ll[0] <= 0,
                       "g_not_one_sided": abs(s - o) <= 0.15 and 0.25 <= s <= 0.75}
    return res


def box_score(hcon, art, freeze):
    """H2-box (A6 3/4/5 a-d): both frozen artifacts on the eligible rows of games after the freeze."""
    from . import forward, shadow
    sm1 = forward.load_artifact(SPORT)
    out = {"freeze_at": freeze, "sm_nfl_2": art["sha"], "sm1": sm1["sha"], "markets": {}}
    cur, df = None, None
    for mk in sorted(art["markets"], key=lambda m: tuple(art["markets"][m]["half_lives"])):
        a2, a1 = art["markets"][mk], sm1["markets"][mk]
        hl = tuple(a2["half_lives"])
        if hl != cur:
            df = None
            df = forward._frame_asof(SPORT, hcon, hl, None)
            study._nfl_fill(df)
            featurize2.augment(df, SPORT, hcon, hl)
            augment(df, hl)
            cur = hl
        m = model.eligible(df, mk) & (np.asarray(df["start_utc"]) > freeze) & np.isfinite(df.f(f"y_{mk}"))
        lref = shadow._ref_lines(df, mk, np.flatnonzero(m))
        d = df[m]
        lo, ks = dists.grid(mk)
        y = np.asarray(d[f"y_{mk}"], float)
        games = np.asarray(d["game_id"])
        p2, _, _ = from_config(a2).pmf(d)
        p1, _ = study.pmf_for(study.from_config(a1), d, mk, SPORT)
        s2, s1 = _metrics(p2, lo, ks, y, lref), _metrics(p1, lo, ks, y, lref)
        crps = evaluate.paired(s2, s1, games, None, "crps")
        ll = evaluate.paired(s2, s1, games, None, "logloss_ref")
        su = _summ(s2, games)
        crit = {"a_crps_ci_below_0": crps["ci_game"][1] is not None and crps["ci_game"][1] < 0,
                "b_calibration_ref": abs(su["in_the_large_ref"]) <= 0.02 and su["ece_ref"] <= 0.03,
                "c_cov80": 0.76 <= su["cov80_pit"] <= 0.84,
                "d_ref_logloss_not_worse": ll["diff"] is not None and ll["diff"] <= 0}
        out["markets"][mk] = {"rows": int(len(d)), "games": int(len(np.unique(games))), "sm_nfl_2": su,
                              "sm1": _summ(s1, games), "crps_v2_minus_sm1": crps, "logloss_ref_v2_minus_sm1": ll,
                              "brier_ref_v2_minus_sm1": evaluate.paired(s2, s1, games, None, "brier_ref"),
                              "criteria": {k: bool(v) for k, v in crit.items()}}
    return out


def heldout(hcon, shadow_path=None, now=None):
    """One-time H2 scores (A6 3): the box part once at >= 48 games, each market's book part once at >= 150 props /
    30 games; an existing part is never re-scored. Then the per-market decision (A6 5)."""
    from . import datasets
    art = load()
    if art is None:
        return {"state": "no sm-nfl-2 artifact"}
    p = h2_path()
    res = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"register": "REGISTER.md A6",
                                                                       "artifact": art["sha"], "book": {}}
    chk = h2_check(hcon, now)
    if "box" not in res and chk["games"] >= H2_MIN_GAMES:
        res["box"] = box_score(hcon, art, art["freeze_at"])
        res["box"]["games_at_trigger"] = chk["games"]
        res["box"]["scored_at"] = now or datasets.now_utc()
    rows = book_rows(hcon, shadow_path, art["freeze_at"])
    pending = {}
    for mk in art["markets"]:
        r = rows.get(mk, [])
        ng = len({x["game"] for x in r})
        if mk in res["book"]:
            # display bar: the gate-F-size sample, evaluated once when it first holds (never re-scored)
            if "display_sample" not in res["book"][mk] and len(r) >= DISPLAY_MIN[0] and ng >= DISPLAY_MIN[1]:
                res["book"][mk]["display_sample"] = book_score(r)
            continue
        if len(r) >= H2_BOOK_MIN[0] and ng >= H2_BOOK_MIN[1]:
            res["book"][mk] = book_score(r)
            res["book"][mk]["scored_at"] = now or datasets.now_utc()
        else:
            pending[mk] = {"props": len(r), "games": ng}
    res["book_pending"] = pending
    res["decision"] = decide(res, list(art["markets"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    return res


def decide(res, markets):
    """A6 5 per market: promotion-eligible iff (a)-(g); display bar iff also the gate-F-size book sample holds (e)-(g)."""
    out = {}
    for mk in markets:
        box = ((res.get("box") or {}).get("markets") or {}).get(mk)
        book = (res.get("book") or {}).get(mk)
        if box is None or book is None:
            out[mk] = {"state": "pending", "promotion_eligible": False, "display_bar": False, "contained": True}
            continue
        crit = dict(box["criteria"], **book["criteria"])
        prom = all(crit.values())
        ds = book.get("display_sample")
        disp = bool(prom and ds is not None and all(ds["criteria"].values()))
        out[mk] = {"state": "decided" if ds is not None or not prom else "promotion-eligible, display sample pending",
                   "criteria": crit, "promotion_eligible": prom, "display_bar": disp, "contained": not disp}
    return out


def status():
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None
    return {"model_version": VERSION, "enabled": enabled(), "manifest": man, "display": "never (shadow only)",
            "heldout": json.loads(h2_path().read_text(encoding="utf-8")).get("decision") if h2_path().exists() else None}


def main(argv=None):
    import argparse
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    p = argparse.ArgumentParser(prog="python -m sports.model.nflv2")
    p.add_argument("cmd", choices=["tune", "fit", "diag", "heldout", "status"])
    p.add_argument("history", nargs="?")
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--shadow")
    a = p.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
        return
    from . import datasets
    con = datasets.connect(Path(a.history), read_only=a.cmd == "heldout")
    log = lambda x: print(x, flush=True)                                          # noqa: E731
    if a.cmd == "tune":
        run_tune(con, log)
    elif a.cmd == "diag":
        run_diag(con, log)
    elif a.cmd == "fit":
        print(json.dumps(fit_forward(con, as_of=a.as_of), indent=1, default=str), flush=True)
    else:
        print(json.dumps(heldout(con, a.shadow), indent=1, default=float)[:4000], flush=True)


if __name__ == "__main__":
    main()
