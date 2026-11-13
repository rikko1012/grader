"""Round-10 audit, part B (2026-11-13): (1) units whose own HLTV match is dated BEFORE the pick's UTC day (the
'strictly before the match day' rule then lets the outcome match into Elo / form / per-map / openings) - list, count
scored rows, RERUN all six with those units' inputs rebuilt at the HLTV date; (2) settlement `result.rounds` vs HLTV R12
(is the settlement rounds field usable for the linked-vs-unlinked outcome comparison?). Read-only; writes only
miss_decomp_1113/audit/audit_1113_b.json.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OPS = Path("C:/Users/tikic/cs2_scout_ops/data")
FROZEN = OPS / "miss_decomp_1113"
import score as S  # noqa: E402

src = (HERE / "build.py").read_text(encoding="utf-8")
cut = src.index("# ------------------------------------------------------------------ 2. pick units")
B = {"__file__": str(HERE / "build.py"), "__name__": "build_audit"}
exec(compile(src[:cut], "build_section1", "exec"), B)
cm, ms, mlan, player_inputs = B["cm"], B["ms"], B["mlan"], B["player_inputs"]
mdate = cm.groupby("match").date.first()

test_rows = [r for r in S.board if S.TEST0 <= r["day"] <= S.TEST1 and r["match_slug"] not in S.MOTIVATING]
UT = S.P[(S.P.day >= S.TEST0) & (S.P.day <= S.TEST1) & ~S.P.match_slug.isin(S.MOTIVATING)].copy()
out = {}

# (1) own match dated before the pick day
bad = []
for slug, g in UT[UT.hltv.notna()].groupby("match_slug"):
    hd = mdate.get(int(g.hltv.iloc[0]))
    if hd is not None and hd < g.day.iloc[0]:
        sett = set()
        for r in S.picks:
            if r["match_slug"] == slug:
                try:
                    v = json.loads(r.get("result") or "{}").get("hltv_match")
                except ValueError:
                    v = None
                if v:
                    sett.add(int(v))
        bad.append({"slug": slug, "hltv": int(g.hltv.iloc[0]), "hltv_date": str(hd), "pick_day": str(g.day.iloc[0]),
                    "match_start": str(g.match_start.iloc[0]), "settlement_ids": sorted(sett),
                    "units": len(g), "scored_rows": sum(1 for r in test_rows if r["match_slug"] == slug)})
out["own_match_dated_before_pick_day"] = bad
print(json.dumps(bad, indent=1))

UT2 = UT.copy()
for b in bad:
    idx = UT2.index[UT2.match_slug == b["slug"]]
    day = pd.Timestamp(b["hltv_date"]).date().toordinal()
    for i in idx:
        r = UT2.loc[i]
        if pd.isna(r.hltv_pid) or pd.isna(r.team_tid) or pd.isna(r.opp_tid):
            continue
        lan = mlan.get(b["hltv"])
        x = player_inputs(int(r.hltv_pid), int(r.team_tid), int(r.opp_tid), day, bool(lan) if lan is not None and lan == lan else False)
        for kk in ("elo_own", "elo_opp", "k10", "r10", "rate_all", "open_att", "open_r", "per_map", "k365", "r365"):
            UT2.at[i, kk] = x.get(kk) if kk == "per_map" else x.get(kk, np.nan)
UT2["S"] = (UT2.elo_own + UT2.elo_opp) / 800.0
UT2["G"] = (UT2.elo_own - UT2.elo_opp) / 400.0
UT2["d_elo"] = UT2.apply(lambda r: abs(S.q_from_series(S.elo_p(r.elo_own, r.elo_opp)) - 0.5) if pd.notna(r.elo_own) else np.nan, axis=1)
UT2["form"] = UT2.apply(lambda r: (((r.k10 + 100 * r.rate_all) / (r.r10 + 100)) / r.rate_all) if pd.notna(r.get("k10")) and pd.notna(r.get("rate_all")) else np.nan, axis=1)
UT2["open_rate"] = UT2.apply(lambda r: r.open_att / r.open_r if pd.notna(r.get("open_r")) and r.open_r >= 200 else np.nan, axis=1)
UT2["mapr"] = UT2.apply(lambda r: S.map_ratio(r.to_dict()), axis=1)
res = {}
for cand in S.CANDS:
    f0 = S.factors(UT, cand)
    f2 = S.factors(UT2, cand)
    d = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT2.itertuples(), f2.values)})
    s = S.score_rows(d, ci=True)
    res[cand] = {"label": "RERUN (own-match date fix), not the frozen result", "d_hit": round(s["d_hit"], 4),
                 "ci": [round(v, 4) for v in s["ci_d_hit"]], "units_factor_changed": int((np.abs(f2.values - f0.values) > 1e-12).sum()),
                 "over_hit": round(s["cand_over_hit"], 4), "under_hit": round(s["cand_under_hit"], 4)}
    print(cand, res[cand], flush=True)
out["rerun_own_match_date_fix"] = res

# (2) settlement rounds vs HLTV R12 on linked units
cmp_ = []
for slug, g in UT[UT.hltv.notna() & UT.R12.notna()].groupby("match_slug"):
    for r in S.picks:
        if r["match_slug"] == slug and r.get("status") == "settled":
            try:
                rs = json.loads(r.get("result") or "{}")
            except ValueError:
                continue
            if rs.get("rounds") and all(v is not None for v in rs["rounds"]):
                cmp_.append({"slug": slug, "settle_sum": sum(rs["rounds"]), "settle_n": len(rs["rounds"]), "R12": float(g.R12.iloc[0]),
                             "maps_used": str(rs.get("maps_used"))})
                break
c = pd.DataFrame(cmp_)
out["settlement_rounds_vs_hltv"] = {"matches": len(c), "exact_equal": int((c.settle_sum == c.R12).sum()),
                                    "mean_settle_minus_R12": round(float((c.settle_sum - c.R12).mean()), 3),
                                    "examples": c.head(5).to_dict("records")}
print(out["settlement_rounds_vs_hltv"])
json.dump(out, open(FROZEN / "audit" / "audit_1113_b.json", "w"), indent=1, default=str)
fp = json.load(open(FROZEN / "fit_params.json"))
txt = open(FROZEN / "forward_predictions.jsonl", encoding="utf-8").read()
print("HASHES", hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:16], hashlib.sha256(txt.encode()).hexdigest()[:16])
