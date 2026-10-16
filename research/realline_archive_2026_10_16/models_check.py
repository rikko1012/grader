"""Robustness checks of the selected GLM (no new selection): (a) GLM vs CUR-FULL (fh-1's history set) in history and at
lines, so the model gain is separated from the history gain; (b) the GLM coefficients; (c) the two halves of the line
period; (d) a leakage probe - the same GLM with the features shuffled across rows within each day must lose its gain."""
import math
import os
import pickle
import sys

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RL)
sys.argv = ["models.py"]
import io
import contextlib
buf = open(os.devnull, "w", encoding="utf-8")
with contextlib.redirect_stdout(buf):
    import models as M  # noqa: E402  (re-runs the protocol pipeline; output suppressed)
import eval20 as E  # noqa: E402
from study import ci, LIVE  # noqa: E402

sys.stdout = sys.__stdout__
sys.stdout.reconfigure(encoding="utf-8")
test = M.test
book = M.book
print("## (a) GLM vs CUR-FULL (full-history current formula = fh-1's history set), history test Sep 1 - Oct 16")
for t in test:
    t["ls_full"] = float(M.logscore_v(np.array([t["y"]]), np.array([t["cur_full"]]), LIVE["c"])[0])
for nm, f in (("all", lambda t: True), ("book-listed", lambda t: t["p"] in book), ("book-listed established", lambda t: t["p"] in book and t["hist"] >= 600),
              ("established", lambda t: t["hist"] >= 600), ("top-200 set", lambda t: t["top"])):
    g = [t for t in test if f(t)]
    a = lambda t: abs(t["cur_full"] - t["y"]) - abs(t["lam_GLM"] - t["y"])
    l = lambda t: t["ls_GLM"] - t["ls_full"]
    print(f"  {nm}: {len(g)} series; MAE gain {np.mean([a(t) for t in g]):+.3f} {tuple(round(x, 3) for x in ci(g, a, B=1000))}; "
          f"log gain {np.mean([l(t) for t in g]):+.4f} {tuple(round(x, 4) for x in ci(g, l, B=1000))}; bias GLM {np.mean([t['lam_GLM'] - t['y'] for t in g]):+.2f} "
          f"CUR {np.mean([t['cur'] - t['y'] for t in g]):+.2f} CUR-FULL {np.mean([t['cur_full'] - t['y'] for t in g]):+.2f}")
rows = M.rows
recmap = M.recmap
for r in rows:
    pass
# CUR-FULL probabilities at the lines
full_rows = []
for o, r in zip([o for o in M.A_obs if o["scope"] == "12" and o["day"] >= "2026-10-01" and M.recmap.get((o["m"], o["p_id"])) is not None
                 and M.feats(M.recmap[(o["m"], o["p_id"])]) is not None], rows):
    rec = M.recmap[(o["m"], o["p_id"])]
    cn = M.comp(rec, "n")
    lam = cn["lam"] * (1.0 if o["prop"] == "kills" else cn["hsr"])
    r["p_FULL"] = M.p_line(lam, LIVE["c"] if o["prop"] == "kills" else E.SPREAD("hs", "12"), o["line"], o["prop"])
E.prob_table(rows, ["p_BASE", "p_CUR", "p_FULL", "p_GLM"], ["p_FULL", "p_CUR"], "(a) real lines: GLM vs CUR-FULL (kills + HS pooled)")
E.selection(rows, ["p_FULL", "p_GLM"], "(a) real lines")
print("\n## (b) GLM coefficients (standardised features; final fit on matches <= 2026-09-30)")
ft = (M.D >= "2025-11-01") & (M.D <= "2026-09-30")
mu, sd = M.X[ft].mean(0), M.X[ft].std(0) + 1e-9
from sklearn.linear_model import PoissonRegressor  # noqa: E402
g = PoissonRegressor(alpha=1e-4, max_iter=1000).fit((M.X[ft] - mu) / sd, M.Y[ft])
for f, c, s_ in zip(M.FEATS, g.coef_, sd):
    print(f"  {f:14s} {c:+.4f} (per raw unit {c / s_:+.4f})")
print(f"  intercept {g.intercept_:+.4f}")
print("\n## (c) the two halves of the line period")
for lo, hi in (("2026-10-01", "2026-10-09"), ("2026-10-10", "2026-10-16")):
    h = [r for r in rows if lo <= r["day"] <= hi]
    E.prob_table(h, ["p_BASE", "p_CUR", "p_GLM"], ["p_CUR", "p_BASE"], f"(c) lines {lo} .. {hi}")
    E.selection(h, ["p_CUR", "p_GLM"], f"(c) {lo} .. {hi}")
print("\n## (d) leakage probe: features permuted across rows within each match day (the target kept) - the GLM must lose its gain")
rng = np.random.default_rng(1)
Xp = M.X.copy()
for d in np.unique(M.D):
    idx = np.where(M.D == d)[0]
    Xp[idx] = Xp[rng.permutation(idx)]
te = (M.D >= "2026-09-01")
fp = M.fit_glm(Xp[ft & ~te], M.Y[ft & ~te], 1e-4)
lam = fp(Xp[te])
c = M.spread_c(M.Y[ft & ~te], fp(Xp[ft & ~te]))
ls_p = M.logscore_v(M.Y[te], lam, c)
ls_c = M.logscore_v(M.Y[te], np.array([d_["cur"] for d_, t_ in zip(M.data, te) if t_]), LIVE["c"])
print(f"  permuted-feature GLM vs CUR on the history test: log-score gain {np.mean(ls_p - ls_c):+.4f}; MAE gain "
      f"{np.mean(np.abs(np.array([d_['cur'] for d_, t_ in zip(M.data, te) if t_]) - M.Y[te]) - np.abs(lam - M.Y[te])):+.3f}")
