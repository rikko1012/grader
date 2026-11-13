"""Round-10 audit, part D: cluster by the real series. Several board slugs are the same HLTV series (named slug +
'hltv-<id>' slug, or two listings). Cluster key = HLTV id when the slug is 'hltv-<id>' itself or its settlement series id
equals its link (strict identity); otherwise the slug. Recomputes paired hit CIs, ICC and the 150-match MDE with that key.
Read-only; writes audit/audit_1113_d.json."""
import json, math, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as S  # noqa
A = Path("C:/Users/tikic/cs2_scout_ops/data/miss_decomp_1113/audit")
C = json.load(open(A / "audit_1113_c.json"))
key = {}
for l in C["links"]:
    s = l["slug"]
    if l["settlement"] == [l["hltv"]] or (s.startswith("hltv-") and s[5:] == str(l["hltv"])):
        key[s] = f"hltv:{l['hltv']}"
# settlement ids of unlinked slugs (strict relink part A) also canonicalise
B = json.load(open(A / "audit_1113.json"))["relink"]
for s, v in B.items():
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
out = {"slug_clusters": int(base.m.nunique()), "series_clusters": int(base.k.nunique())}
ms_ = base.k.unique(); idx = {m: np.where(base.k.values == m)[0] for m in ms_}
rng = np.random.default_rng(7); J = []
for _ in range(2000):
    ii = np.concatenate([idx[m] for m in rng.choice(ms_, len(ms_))]); d = base.iloc[ii]
    J.append([d["ch_" + c].mean() - d.lh.mean() for c in S.CANDS] + [d.lh.mean()])
J = np.array(J)
z = 1.959964 + 0.841621; NM = len(ms_)
for j, c in enumerate(S.CANDS):
    se = J[:, j].std(ddof=1)
    out[c] = {"ci": [round(float(np.percentile(J[:, j], 2.5)), 4), round(float(np.percentile(J[:, j], 97.5)), 4)], "se": round(float(se), 4),
              "mde_150_pts": round(100 * z * se * math.sqrt(NM / 150), 2), "series_for_plus_1pt": int(math.ceil(NM * (z * se / 0.01) ** 2))}
se = J[:, 6].std(ddof=1)
out["live_hit_se"] = round(float(se), 4)
out["live_hit_mde_150_pts"] = round(100 * z * se * math.sqrt(NM / 150), 2)
out["series_for_plus_1pt_absolute"] = int(math.ceil(NM * (z * se / 0.01) ** 2))
g = base.groupby("k").lh; k_ = g.size(); n, a = len(base), len(k_); gm = base.lh.mean()
msb = (k_ * (g.mean() - gm) ** 2).sum() / (a - 1); msw = ((base.lh - base.k.map(g.mean())) ** 2).sum() / (n - a)
k0 = (n - (k_ ** 2).sum() / n) / (a - 1)
out["icc_live_hit_series"] = round(float((msb - msw) / (msb + (k0 - 1) * msw)), 4); out["rows_per_series_k0"] = round(float(k0), 2)
print(json.dumps(out, indent=1)); json.dump(out, open(A / "audit_1113_d.json", "w"), indent=1)
