"""rm-1 / th-1 development evaluation on population (a) (HLTV 2y, ED-1 series.csv.gz) - REGISTER.md frozen definitions.

usage: python run_hltv.py <scratch cs2acc dir> [fold ...]
  reads  <S>/ed1/series.csv.gz, <S>/codex_export/{maps,scores}.csv.gz, <S>/data/rounds_archive.sqlite
  writes <S>/cands/maps_rounds.csv.gz, <S>/cands/params_<fold>.json, <S>/cands/hltv_<fold>_<scope>.csv.gz
Resumable: a fold / scope whose checkpoint file exists is skipped (delete it to recompute). Folds: F1..F6, FINAL (= the fit on
all development data <= 2026-10-21 used only to freeze a passing shadow; it evaluates nothing)."""
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "error_breakdown"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import lib  # noqa: E402
from common import live_frame  # noqa: E402

S = Path(sys.argv[1])
OUT = S / "cands"
OUT.mkdir(exist_ok=True)
FOLDS = {"F1": (date(2025, 7, 1), date(2025, 10, 1)), "F2": (date(2025, 10, 1), date(2026, 1, 1)),
         "F3": (date(2026, 1, 1), date(2026, 4, 1)), "F4": (date(2026, 4, 1), date(2026, 7, 1)),
         "F5": (date(2026, 7, 1), date(2026, 10, 1)), "F6": (date(2026, 10, 1), date(2026, 10, 22)),
         "FINAL": (date(2026, 10, 22), None)}
want = sys.argv[2:] or list(FOLDS)
C1, C12, C123, CCM2 = 1.442, 1.457, 1.457, 1.4756515691194962
M = {"1": 1, "12": 2, "123": 3}
t0 = time.time()


def log(*a):
    print(f"[{time.time() - t0:7.0f}s]", *a, flush=True)


# ---------------------------------------------------------------- per-map rounds (archive first, export score as fallback)
mr_path = OUT / "maps_rounds.csv.gz"
if not mr_path.exists():
    maps = pd.read_csv(S / "codex_export/maps.csv.gz", usecols=["matchid", "mapid", "mapstatsid", "date", "forfeited_maps"])
    maps = maps[pd.to_datetime(maps.date).dt.date < date(2026, 10, 22)]
    sc = pd.read_csv(S / "codex_export/scores.csv.gz")
    sc = sc[(sc.sideid == 0) & (sc.mapid > 0)].groupby(["matchid", "mapid"]).score.sum().rename("score_rounds").reset_index()
    maps = maps.merge(sc, on=["matchid", "mapid"], how="left")
    con = sqlite3.connect(S / "data/rounds_archive.sqlite")
    ar = pd.read_sql("select r.mapstatsid, count(*) as arch_rounds, sum(r.ot) as arch_ot from rounds r join pages p on "
                     "p.mapstatsid = r.mapstatsid where p.status = 'ok' group by r.mapstatsid", con)
    maps = maps.merge(ar, on="mapstatsid", how="left")
    maps["rounds"] = maps.arch_rounds.fillna(maps.score_rounds)
    maps["src"] = np.where(maps.arch_rounds.notna(), "archive", np.where(maps.score_rounds.notna(), "score", "none"))
    maps = maps[(maps.forfeited_maps == 0) & maps.rounds.notna()]
    maps["day"] = pd.to_datetime(maps.date).map(lambda x: x.toordinal())
    maps.to_csv(mr_path, index=False, compression="gzip")
    agree = maps.dropna(subset=["arch_rounds", "score_rounds"])
    log("maps", len(maps), maps.src.value_counts().to_dict(), "archive == score on", int((agree.arch_rounds == agree.score_rounds).sum()), "of", len(agree))
MR = pd.read_csv(mr_path)

ser = pd.read_csv(S / "ed1/series.csv.gz")
frames = {sc: live_frame(ser, sc).reset_index(drop=True) for sc in ("1", "12", "123")}
for sc, d in frames.items():
    d["lam_p2"] = M[sc] * d.lam_p2_1
    d["c_p2"] = {"1": C1, "12": C12, "123": C123}[sc]
    d["band"] = np.where(d.r_w < 50, "D0", np.where(d.r_w < 150, "D1", "deep"))
    if sc == "12":
        d["rho_cell"] = np.where(d.cm2_ok == 1, "12_cm2", "12_p2")
    else:
        d["rho_cell"] = f"{sc}_p2"
    # th-1 live-route cell
    if sc == "12":
        d["th_cell"] = np.where(d.cm2_ok == 0, "F", np.where(d.band != "deep", "cm2_" + d.band, ""))
    else:
        d["th_cell"] = np.where(d.band != "deep", f"p2_{sc}_" + d.band, "")
    d["th_cell_p2"] = np.where(d.band != "deep", f"p2_{sc}_" + d.band, "")     # perf-2 projection cells (check / real lines)
log("frames", {k: len(v) for k, v in frames.items()})


def fit_fold(name, start):
    so = start.toordinal()
    mm = MR[(MR.day < so) & (MR.day >= so - 365)]
    pi = lib.pi_fit(mm.rounds.values)
    pi["window"] = [str(date.fromordinal(so - 365)), str(date.fromordinal(so - 1))]
    pi["src"] = mm.src.value_counts().to_dict()
    sups = {m: lib.pi_scope(pi["pmf"], m) for m in (1, 2, 3)}
    rng = np.random.default_rng(lib.SEED)
    rho, th = {}, {}
    for sc, d in frames.items():
        tr = d[d.day < so]
        sup, w = sups[M[sc]]
        cells = {f"{sc}_p2": (tr.K.values, tr.lam_p2.values, tr.c_p2.values)}
        if sc == "12":
            t2 = tr[tr.cm2_ok == 1]
            cells["12_cm2"] = (t2.K.values, t2.lam_cm2.values, np.full(len(t2), CCM2))
        for cell, (y, lam, c) in cells.items():
            idx = rng.choice(len(y), min(40000, len(y)), replace=False) if len(y) > 40000 else np.arange(len(y))
            rho[cell] = {"rho": lib.fit_rho(y[idx], lam[idx], c[idx], sup, w), "n_fit": int(len(idx)), "n_train": int(len(y))}
        # th-1 cells
        for cell in sorted(set(tr.th_cell_p2) - {""}):
            t_ = tr[tr.th_cell_p2 == cell]
            th[cell] = _th(t_.K.values, t_.lam_p2.values, t_.c_p2.values)
        if sc == "12":
            for b in ("D0", "D1"):
                t_ = tr[(tr.cm2_ok == 1) & (tr.band == b)]
                th["cm2_" + b] = _th(t_.K.values, t_.lam_cm2.values, np.full(len(t_), CCM2))
            t_ = tr[tr.cm2_ok == 0]
            th["F"] = _th(t_.K.values, t_.lam_p2.values, t_.c_p2.values)
    return {"fold": name, "fit_before": str(start), "pi": pi, "rho": rho, "th": th}


def _th(y, lam, c):
    if len(y) < 500:
        return {"a": 0.0, "s": 0.0, "n_train": int(len(y)), "note": "< 500 training rows -> (0, 0)"}
    a, s = lib.fit_th(y, lam, c)
    return {"a": a, "s": s, "n_train": int(len(y)), "bias_train": float((y - lam).mean())}


def eval_scope(sc, d, P):
    """Per-row scores for live / rm-1 / th-1 on one scope's rows of the fold."""
    sup, w = lib.pi_scope(P["pi"]["pmf"], M[sc])
    rho = d.rho_cell.map(lambda k: P["rho"][k]["rho"]).values
    a = d.th_cell.map(lambda k: P["th"][k]["a"] if k else 0.0).values
    s_ = d.th_cell.map(lambda k: P["th"][k]["s"] if k else 0.0).values
    lam, c, y = d.lam.values, d.c.values, d.K.values.astype(float)
    lam_t, c_t = lam * np.exp(a), c * np.exp(s_)
    out = pd.DataFrame({"match": d.match, "player": d.player, "day": d.day, "scope": sc, "model": d.model, "r_w": d.r_w,
                        "band": d.band, "th_cell": d.th_cell, "cm2_ok": d.cm2_ok, "elo_m": (d.elo_own + d.elo_opp) / 2,
                        "lam": lam, "K": y, "HS": d.HS, "hsr": d.hsr})
    # kills
    L = np.floor(lam) + 0.5
    for nm, kw in (("live", dict(lam=lam, c=c)), ("rm1", dict(lam=lam, c=c, rho=rho, sup=sup, w=w)), ("th1", dict(lam=lam_t, c=c_t))):
        lp = lib.kills_logpmf_at(y, **kw)
        out[f"k_ls_{nm}"] = lp
        out[f"k_pit_{nm}"] = lib.kills_cdf_at(y - 1, **kw) + 0.5 * np.exp(lp)
        out[f"k_p_{nm}"] = lib.p_over_kills(L, **kw)
    out["k_line"] = L
    out["k_over"] = (y > L).astype(float)
    # HS (chunked; rows with an HS actual)
    hs_ok = d.HS.notna().values
    for nm in ("live", "rm1", "th1"):
        for f in ("ls", "pit", "p"):
            out[f"h_{f}_{nm}"] = np.nan
    lam_h = lam * d.hsr.values
    Lh = np.floor(lam_h) + 0.5
    out["h_line"] = Lh
    out["h_over"] = np.where(hs_ok, (d.HS.values > Lh).astype(float), np.nan)
    ii = np.where(hs_ok)[0]
    CH = 2500 if sc != "123" else 1200
    for st in range(0, len(ii), CH):
        j = ii[st:st + CH]
        yh = d.HS.values[j].astype(float)
        for nm in ("live", "rm1", "th1"):
            if nm == "live":
                pk = lib.kills_pmf_grid(lam[j], c[j])
            elif nm == "rm1":
                pk = lib.kills_pmf_grid(lam[j], c[j], rho[j], sup, w)
            else:
                pk = lib.kills_pmf_grid(lam_t[j], c_t[j])
            r = lib.hs_stats(pk, d.hsr.values[j], yh, Lh[j])
            out.loc[j, f"h_ls_{nm}"] = np.log(np.maximum(r["p_y"], 1e-300))
            out.loc[j, f"h_pit_{nm}"] = r["cdf_y1"] + 0.5 * r["p_y"]
            out.loc[j, f"h_p_{nm}"] = r["p_over"]
    # check: perf-2 Maps 1-2 projection thin bands (th-1 'p2_12_*' cells vs perf-2 replay) - kills only
    if sc == "12":
        m = d.th_cell_p2.values != ""
        a2 = d.th_cell_p2.map(lambda k: P["th"][k]["a"] if k else 0.0).values
        s2 = d.th_cell_p2.map(lambda k: P["th"][k]["s"] if k else 0.0).values
        out["p2chk_ls_live"] = np.where(m, lib.kills_logpmf_at(y, d.lam_p2.values, d.c_p2.values), np.nan)
        out["p2chk_ls_th1"] = np.where(m, lib.kills_logpmf_at(y, d.lam_p2.values * np.exp(a2), d.c_p2.values * np.exp(s2)), np.nan)
    return out


for name in want:
    start, end = FOLDS[name]
    pp = OUT / f"params_{name}.json"
    if pp.exists():
        P = json.load(open(pp))
        P["pi"]["pmf"] = {int(k): v for k, v in P["pi"]["pmf"].items()}
    else:
        P = fit_fold(name, start)
        json.dump(P, open(pp, "w"), indent=1)
        log(name, "fit", "q", round(P["pi"]["q"], 4), {k: round(v["rho"], 4) for k, v in P["rho"].items()},
            {k: (round(v["a"], 3), round(v["s"], 3), v["n_train"]) for k, v in P["th"].items()})
    if end is None:
        continue
    for sc, d in frames.items():
        cp = OUT / f"hltv_{name}_{sc}.csv.gz"
        if cp.exists():
            continue
        dd = d[(d.day >= start.toordinal()) & (d.day < end.toordinal())].reset_index(drop=True)
        o = eval_scope(sc, dd, P)
        o["fold"] = name
        o.to_csv(cp, index=False, compression="gzip")
        log(name, sc, len(o), "rows", o.match.nunique(), "matches",
            "k gain rm1 %.4f th1 %.4f" % ((o.k_ls_rm1 - o.k_ls_live).mean(), (o.k_ls_th1 - o.k_ls_live).mean()))
log("done")
