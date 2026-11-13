"""Round-10 audit, part E: strict-availability RERUN. Candidate inputs (Elo, form, openings, per-map rates) rebuilt from
local-extract HLTV rows only (every row has an overview fetched_at; 34 player-map inputs were fetched after the scored
row, all else before), i.e. without the 2026-10-28.. export, which has no availability timestamp. Outcomes (R12, actual
kills) unchanged. Frozen parameters, frozen scoring. Read-only; writes audit/audit_1113_e.json."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as S  # noqa
src = (HERE / "build.py").read_text(encoding="utf-8")
cut = src.index("# ------------------------------------------------------------------ 2. pick units")
src1 = src[:cut].replace("allr = pd.concat([L, E], ignore_index=True)", "allr = L.copy()  # audit E: local extract only")
assert "audit E" in src1
B = {"__file__": str(HERE / "build.py"), "__name__": "build_audit_e"}
exec(compile(src1, "build_section1_local_only", "exec"), B)
player_inputs = B["player_inputs"]
test_rows = [r for r in S.board if S.TEST0 <= r["day"] <= S.TEST1 and r["match_slug"] not in S.MOTIVATING]
UT = S.P[(S.P.day >= S.TEST0) & (S.P.day <= S.TEST1) & ~S.P.match_slug.isin(S.MOTIVATING)].copy()
UT2 = UT.copy()
for i, r in UT.iterrows():
    if pd.isna(r.hltv_pid) or pd.isna(r.team_tid) or pd.isna(r.opp_tid):
        continue
    lan = r.get("lan_known")
    x = player_inputs(int(r.hltv_pid), int(r.team_tid), int(r.opp_tid), r.day.toordinal(), False)
    for kk in ("elo_own", "elo_opp", "k10", "r10", "rate_all", "open_att", "open_r", "per_map", "k365", "r365"):
        UT2.at[i, kk] = x.get(kk) if kk == "per_map" else x.get(kk, np.nan)
UT2["S"] = (UT2.elo_own + UT2.elo_opp) / 800.0
UT2["G"] = (UT2.elo_own - UT2.elo_opp) / 400.0
UT2["d_elo"] = UT2.apply(lambda r: abs(S.q_from_series(S.elo_p(r.elo_own, r.elo_opp)) - 0.5) if pd.notna(r.elo_own) else np.nan, axis=1)
UT2["form"] = UT2.apply(lambda r: (((r.k10 + 100 * r.rate_all) / (r.r10 + 100)) / r.rate_all) if pd.notna(r.get("k10")) and pd.notna(r.get("rate_all")) else np.nan, axis=1)
UT2["open_rate"] = UT2.apply(lambda r: r.open_att / r.open_r if pd.notna(r.get("open_r")) and r.open_r >= 200 else np.nan, axis=1)
UT2["mapr"] = UT2.apply(lambda r: S.map_ratio(r.to_dict()), axis=1)
out = {"label": "RERUN strict availability (local extract only; inputs frozen at 2026-10-27), not the frozen result"}
for cand in S.CANDS:
    f0 = S.factors(UT, cand); f2 = S.factors(UT2, cand)
    d = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT2.itertuples(), f2.values)})
    pf = S.factors(UT2, cand, placebo=True)
    pdd = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT2.itertuples(), pf.values)})
    s = S.score_rows(d, ci=True); ps = S.score_rows(pdd, ci=True)
    mae = S.mae_units(UT2, f2, cand)
    out[cand] = {"d_hit": round(float(s["d_hit"]), 4), "ci": [round(v, 4) for v in s["ci_d_hit"]],
                 "over_hit": round(float(s["cand_over_hit"]), 4), "under_hit": round(float(s["cand_under_hit"]), 4),
                 "over_share": round(float(s["cand_over_share"]), 4), "d_brier": round(float(s["d_brier"]), 5),
                 "placebo_ci": [round(v, 4) for v in ps["ci_d_hit"]], "units_factor_changed": int((np.abs(f2.values - f0.values) > 1e-12).sum()),
                 "kpr_mae": [round(mae["kpr_mae_live"], 5), round(mae["kpr_mae_cand"], 5)], "kills_mae": [round(mae["kills_mae_live"], 4), round(mae["kills_mae_cand"], 4)],
                 "C1_pass": bool(s["ci_d_hit"][0] > 0)}
    print(cand, out[cand], flush=True)
json.dump(out, open(Path("C:/Users/tikic/cs2_scout_ops/data/miss_decomp_1113/audit/audit_1113_e.json"), "w"), indent=1)
