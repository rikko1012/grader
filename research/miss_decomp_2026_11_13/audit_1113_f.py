"""Round-10 audit, part F (owner corrections, 2026-11-13): per-metric design effects by series.

Design effect = variance of the series-cluster bootstrap / variance of the independent-row bootstrap, same seed, same
2,000 resamples, for each metric separately (it depends on the metric, the group sizes and the correlation). For the
unit-level kills residual it also gives the one-way ANOVA formula 1 + (k0 - 1) * ICC with series as the group.
Read-only on the frozen outputs; checks the frozen forward hashes before and after; writes audit/audit_1113_f.json only.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
FROZEN = Path("C:/Users/tikic/cs2_scout_ops/data/miss_decomp_1113")
A = FROZEN / "audit"


def sha_files():
    fp = json.load(open(FROZEN / "fit_params.json"))
    txt = open(FROZEN / "forward_predictions.jsonl", encoding="utf-8").read()
    return {"fit_params_sha16": hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:16],
            "forward_predictions_sha16": hashlib.sha256(txt.encode()).hexdigest()[:16],
            "fit_params_file_sha256": hashlib.sha256(open(FROZEN / "fit_params.json", "rb").read()).hexdigest(),
            "forward_file_sha256": hashlib.sha256(open(FROZEN / "forward_predictions.jsonl", "rb").read()).hexdigest()}


H0 = sha_files()
import score as S  # noqa: E402

C = json.load(open(A / "audit_1113_c.json"))
key = {}
for l in C["links"]:
    s = l["slug"]
    if l["settlement"] == [l["hltv"]] or (s.startswith("hltv-") and s[5:] == str(l["hltv"])):
        key[s] = f"hltv:{l['hltv']}"
for s, v in json.load(open(A / "audit_1113.json"))["relink"].items():
    if v.get("resolved"):
        key[s] = f"hltv:{v['hltv']}"

test_rows = [r for r in S.board if S.TEST0 <= r["day"] <= S.TEST1 and r["match_slug"] not in S.MOTIVATING]
UT = S.P[(S.P.day >= S.TEST0) & (S.P.day <= S.TEST1) & ~S.P.match_slug.isin(S.MOTIVATING)]
base = None
for c in S.CANDS:
    f = S.factors(UT, c)
    d = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT.itertuples(), f.values)})
    if base is None:
        base = d[["m", "lh", "over"]].copy()
    base["ch_" + c] = d.ch.values
base["k"] = base.m.map(lambda s: key.get(s, s))

metrics = {"live_hit": lambda z: z.lh.mean(), "over_outcome_share": lambda z: z.over.mean()}
for c in S.CANDS:
    metrics["paired_hit_" + c] = (lambda z, c=c: z["ch_" + c].mean() - z.lh.mean())


def boot(groups):
    rng = np.random.default_rng(7)
    out = []
    for _ in range(2000):
        ii = np.concatenate([groups[g] for g in rng.choice(list(groups), len(groups))])
        z = base.iloc[ii]
        out.append([fn(z) for fn in metrics.values()])
    return np.array(out)


ser = {m: np.where(base.k.values == m)[0] for m in base.k.unique()}
rows = {i: np.array([i]) for i in range(len(base))}
Jc, Ji = boot(ser), boot(rows)
out = {"n_rows": len(base), "n_series": len(ser), "mean_rows_per_series": round(len(base) / len(ser), 2), "metrics": {}}
for j, name in enumerate(metrics):
    vc, vi = Jc[:, j].var(ddof=1), Ji[:, j].var(ddof=1)
    out["metrics"][name] = {"se_cluster_pts": round(100 * float(np.sqrt(vc)), 3), "se_iid_pts": round(100 * float(np.sqrt(vi)), 3),
                            "design_effect": round(float(vc / vi), 2), "effective_n_rows": int(round(len(base) * vi / vc))}

# unit-level kills residual (players in one lobby share rounds), formula with series as the group
U = UT[UT.act_k.notna() & UT.lam_k.notna()].copy()
U["res"] = U.act_k - U.lam_k
U["k"] = U.match_slug.map(lambda s: key.get(s, s))
g = U.groupby("k").res; kk = g.size(); n, a = len(U), len(kk); gm = U.res.mean()
msb = (kk * (g.mean() - gm) ** 2).sum() / (a - 1); msw = ((U.res - U.k.map(g.mean())) ** 2).sum() / (n - a)
k0 = (n - (kk ** 2).sum() / n) / (a - 1); icc = (msb - msw) / (msb + (k0 - 1) * msw)
out["unit_kills_residual"] = {"n_units": n, "n_series": a, "k0_units_per_series": round(float(k0), 2), "icc": round(float(icc), 3),
                              "design_effect_formula": round(float(1 + (k0 - 1) * icc), 2)}
H1 = sha_files()
out["hashes_before"], out["hashes_after"], out["hashes_unchanged"] = H0, H1, H0 == H1
print(json.dumps(out, indent=1))
json.dump(out, open(A / "audit_1113_f.json", "w"), indent=1)
