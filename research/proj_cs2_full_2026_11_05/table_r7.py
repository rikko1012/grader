"""Per-prop table for every settled book-line prop (research only): live projection vs the r7 candidate, the factors that
drive the candidate's number (LightGBM SHAP contributions on the walk-forward model of that month), confidence from sample
size, and the side the candidate would pick. Output: proj_full/r7_prop_table.csv.gz + r7_prop_summary.json.

Attribution: contributions are in log-mean space of the candidate's Poisson mean model, relative to the training average.
They explain the NEW number, not the gap to the live model (a different model). Grouped by factor name below."""
from __future__ import annotations

import json
import os
from datetime import date

import lightgbm as lgb
import numpy as np
import pandas as pd

import model
from eval_r7 import load_r7
from features import OUT
from features_r7 import group_columns

NAMES = {
    "kpr": "player kill rate (recent, decayed)", "kpr_env": "kill rate LAN/online split", "hsk": "headshots per kill",
    "proj_kpm": "base kills-per-map estimate", "okpr": "opening kills", "odpr": "opening deaths", "mkpr": "multi-kill rounds",
    "clutch": "clutches", "kast": "KAST", "adr": "ADR", "apr": "assists", "dpr": "deaths per round", "rating": "rating",
    "swing": "round swing", "kshare": "share of team kills", "mapwin": "player map win rate", "days_since": "days since last map",
    "maps_with_team": "maps with current team", "rpm": "rounds per map (team/opp)", "conc_kpr": "kills conceded (team/opp)",
    "t_kpr": "team kill pace", "o_kpr": "opponent kill pace", "rshare": "round share (team/opp)", "ot": "overtime rate",
    "map_": "expected map pool", "elo": "team strength gap (Elo)", "rest": "rest days", "lineup": "lineup changes",
    "players_new": "stand-in / new players", "bo": "series format", "lan_f": "LAN vs online", "ev_": "event tier/stage",
    "r_": "sample size (rounds)", "maps_": "sample size (maps)", "hs_hist": "HS sample",
}
R7_NAMES = {"pis_wr": "pistol win rate", "conv_wr": "post-pistol conversion", "ecof_share": "eco/force share",
            "force_wr": "force-buy win rate", "full_wr": "full-buy win rate", "anti_wr": "anti-eco win rate",
            "equip": "equipment value", "elim_share": "rounds ending in elimination", "elim_mean": "rounds ending in elimination",
            "bomb_share": "bomb-win share", "def_share": "defuse-win share", "time_share": "time-win share",
            "ct_wr": "CT round win rate", "t_wr": "T round win rate", "close_rate": "close-map rate",
            "comeback_rate": "comeback rate", "maxdef": "typical max deficit", "trail_share": "rounds played trailing",
            "kpr_won": "kill rate in won maps", "kpr_lost": "kill rate in lost maps", "kpr_close": "kill rate in close maps",
            "kpr_ct": "CT-side kill rate", "kpr_t": "T-side kill rate", "side_gap": "CT/T gap", "dpr_ct": "CT deaths",
            "dpr_t": "T deaths", "traded_share": "traded deaths", "fapr": "flash assists",
            "kpr_vs_stronger": "kill rate vs stronger teams", "kpr_vs_similar": "kill rate vs similar-tier teams",
            "kpr_vs_weaker": "kill rate vs weaker teams"}


def factor_name(col):
    if col.startswith("r7"):
        side = "opp " if col.startswith("r7o_") else ("team " if col.startswith("r7t_") else "")
        key = col.split("_", 1)[1]
        key = key[2:] if key.startswith("p_") else key
        return side + R7_NAMES.get(key, key)
    if col.startswith("o_") and col not in ("o_kpr",):
        return "opp " + NAMES.get(col[2:].rstrip("_0123456789"), col)
    for k, v in NAMES.items():
        if col == k or col.startswith(k):
            return v
    return col


def confidence(r90, cell):
    lvl = 2 if r90 >= 600 else (1 if r90 >= 250 else 0)
    if cell in ("1", "123"):
        lvl = max(lvl - 1, 0)
    return ("Low", "Medium", "High")[lvl]


def side_label(p):
    st = max(p, 1 - p)
    side = "Over" if p >= 0.5 else "Under"
    return f"{side} {st * 100:.1f}%" if st >= 0.55 else f"Lean {side} {st * 100:.1f}%"


def main():
    F = load_r7()
    L = pd.read_csv(os.path.join(OUT, "r7_line_rows.csv.gz"))
    base_cols = model.feature_cols(F.drop(columns=[c for c in F.columns if c.startswith("r7")]))
    cols = base_cols + [c for cs in group_columns().values() for c in cs]
    L["fold"] = L.day.map(lambda d: date(date.fromordinal(int(d)).year, date.fromordinal(int(d)).month, 1).toordinal())
    out = []
    for (stat, scope), q in L.groupby(["stat", "scope"]):
        scope = str(scope)
        for fold, qq in q.groupby("fold"):
            M = model.fit(F[F.day < fold], stat, scope, cols)
            X = qq.merge(F, on=["matchid", "playerid"], how="left", suffixes=("", "_f"))
            C = M["mean"].predict(X[cols].to_numpy(np.float32), pred_contrib=True)[:, :-1]
            for i, (_, r) in enumerate(qq.reset_index(drop=True).iterrows()):
                c = C[i]
                agg = {}
                for j, col in enumerate(cols):
                    agg[factor_name(col)] = agg.get(factor_name(col), 0.0) + c[j]
                top = sorted(agg.items(), key=lambda kv: -abs(kv[1]))[:4]
                r90 = X.iloc[i]["r_90"]
                out.append({
                    "match": f"{r.team} vs {r.opponent}", "start": r.start_time, "book": r.book, "player": r.player,
                    "prop": f"{'Kills' if stat == 'kills' else 'HS'} Map{'s 1-2' if scope == '12' else (' 1' if scope == '1' else 's 1-3')}",
                    "line": r.line, "old_projection": round(r.mean_live_recon, 2),
                    "old_projection_logged": round(r.lam_live_exact, 2) if not pd.isna(r.get("lam_live_exact")) else None,
                    "new_projection": round(r.mean_r7, 2), "delta": round(r.mean_r7 - r.mean_live_recon, 2),
                    "p_over_old": round(r.p_live_recon, 4), "p_over_new": round(r.p_r7, 4),
                    "confidence": confidence(r90 if not pd.isna(r90) else 0, scope),
                    "top_factors": "; ".join(f"{n} {'+' if v > 0 else '-'}{abs(v) * 100:.0f}%" for n, v in top),
                    "action_new": side_label(r.p_r7), "action_old": side_label(r.p_live_recon),
                    "crossed_line": (r.mean_live_recon - r.line) * (r.mean_r7 - r.line) < 0,
                    "actual": r.actual, "result_new": "W" if (r.p_r7 >= .5) == (r.y_over == 1) else "L",
                    "result_old": "W" if (r.p_live_recon >= .5) == (r.y_over == 1) else "L",
                    "matchid": r.matchid, "playerid": r.playerid, "stat": stat, "scope": scope,
                })
            print(stat, scope, date.fromordinal(int(fold)), len(qq), flush=True)
    T = pd.DataFrame(out)
    T.to_csv(os.path.join(OUT, "r7_prop_table.csv.gz"), index=False)
    print("rows", len(T), "matches", T.matchid.nunique())


if __name__ == "__main__":
    main()
