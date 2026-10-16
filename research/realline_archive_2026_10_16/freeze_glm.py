"""Freeze the selected candidate 'cm-1' (complete-model GLM): one fit on every record through 2026-10-16 with the protocol's
chosen configuration (alpha 1e-4), no re-tuning. Writes cm1_frozen.json (standardisation, coefficients, spread, feature
list, data hash). Nothing is deployed or registered."""
import contextlib
import hashlib
import json
import os
import sys

import numpy as np
from sklearn.linear_model import PoissonRegressor

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RL)
with contextlib.redirect_stdout(open(os.devnull, "w", encoding="utf-8")):
    import models as M  # noqa: E402
sys.stdout = sys.__stdout__
ft = M.D <= "2026-10-16"
mu, sd = M.X[ft].mean(0), M.X[ft].std(0) + 1e-9
g = PoissonRegressor(alpha=1e-4, max_iter=1000).fit((M.X[ft] - mu) / sd, M.Y[ft])
lam = g.predict((M.X[ft] - mu) / sd)
c = M.spread_c(M.Y[ft], lam)
h = hashlib.sha256(json.dumps(sorted(zip([d["m"] for d, f in zip(M.data, ft) if f], [d["p"] for d, f in zip(M.data, ft) if f],
                                         M.Y[ft].tolist()))).encode()).hexdigest()[:12]
frozen = {"id": "cm-1", "family": "complete-model GLM (regularised Poisson, log link)", "alpha": 1e-4, "features": M.FEATS,
          "feature_rules": "PROTOCOL_MODELS.md (view n sums, oa_sums weekly refits, pre-match Elo, LAN)", "mean": mu.tolist(), "std": sd.tolist(),
          "coef": g.coef_.tolist(), "intercept": float(g.intercept_), "spread_c_kills": c, "hs": "lambda_kills x the view-n HS ratio; c_hs = 1.264 x c / 1.457",
          "line_probability": "discretised normal at the line, P(Over | no push), kills 22-78% cap", "recommendation_rule": "favoured side >= 0.57",
          "fit_rows": int(ft.sum()), "fit_through": "2026-10-16", "data_hash": h}
json.dump(frozen, open(os.path.join(RL, "cm1_frozen.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in frozen.items() if k not in ("mean", "std", "coef")}, indent=1))
print("coef:", dict(zip(M.FEATS, np.round(g.coef_, 4))))
