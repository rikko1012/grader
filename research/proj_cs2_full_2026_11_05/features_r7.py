"""Round 7 factor group (research only, never imported by the live grader): round-level / economy / score-state / side /
trade / opponent-tier features that pd-1 (features.py) does not use. Output: proj_full/r7_features.pkl, one row per
(matchid, playerid) of series_features.pkl, merged on those keys.

Sources (local read-only HLTV extract): hltv/map_rounds (winner, side, win type per round), hltv/map_round_economy
(team buy type, equipment, won per round), work/player_map (per-side kills/deaths, traded deaths, flash assists, map won),
work/team_match_ctx (walk-forward Elo before the match day).

Groups (named so the ablation can drop each):
  ECON   team + opponent: pistol win rate, post-pistol conversion, eco/force share of rounds, force-buy win rate, full-buy
         win rate, anti-eco win rate, mean equipment
  ROUND  team + opponent: elimination share of rounds (kills per round depend on how rounds end), bomb / defuse / time
         share of wins, CT / T round win rate
  STATE  team + opponent: close-map rate (final margin <= 3), comeback rate (won after trailing by 5+), mean max
         deficit, share of rounds played trailing by 3+; player kills/round in maps the team won vs lost and in close maps
  SIDE   player: CT and T kills/round, CT-T gap, deaths/round per side
  TRADE  player: traded share of deaths, flash assists/round
  TIER   player: kills/round vs stronger (opp Elo > own + 50), similar (|gap| <= 50) and weaker opponents; tonight's gap is
         already in pd-1 (elo_diff)

LEAKAGE RULE (same as features.py): a row on day D reads state built ONLY from maps dated strictly before D; every row of
day D is featurised before day D's maps update the state."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from features import DATA, OUT, Decay, POP_KPR, shrink

H = os.path.join(DATA, "hltv")
HL = (90.0,)

TEAM_V = ("maps", "rounds", "won_r", "pis_n", "pis_w", "conv_n", "conv_w", "ecof_n", "force_n", "force_w", "full_n",
          "full_w", "anti_n", "anti_w", "equip", "elim", "bomb_w", "def_w", "time_w", "ct_n", "ct_w", "t_n", "t_w",
          "close", "comeback", "maxdef", "trail_r", "won_m")
TIX = {n: i for i, n in enumerate(TEAM_V)}
PL_V = ("rounds", "k", "ct_r", "ct_k", "ct_d", "t_r", "t_k", "t_d", "d", "td", "fa",
        "won_r", "won_k", "lost_r", "lost_k", "close_r", "close_k",
        "str_r", "str_k", "sim_r", "sim_k", "weak_r", "weak_k")
PIX = {n: i for i, n in enumerate(PL_V)}
GROUPS = {
    "ECON": ["pis_wr", "conv_wr", "ecof_share", "force_wr", "full_wr", "anti_wr", "equip"],
    "ROUND": ["elim_share", "bomb_share", "def_share", "time_share", "ct_wr", "t_wr"],
    "STATE": ["close_rate", "comeback_rate", "maxdef", "trail_share"],
}
PLAYER_GROUPS = {
    "STATE": ["p_kpr_won", "p_kpr_lost", "p_kpr_close"],
    "SIDE": ["p_kpr_ct", "p_kpr_t", "p_side_gap", "p_dpr_ct", "p_dpr_t"],
    "TRADE": ["p_traded_share", "p_fapr"],
    "TIER": ["p_kpr_vs_stronger", "p_kpr_vs_similar", "p_kpr_vs_weaker"],
}


def group_columns():
    """{group: [feature columns]} for the ablation (team features exist as t_ and o_ copies, plus elim_mean)."""
    out = {g: [] for g in ("ECON", "ROUND", "STATE", "SIDE", "TRADE", "TIER")}
    for g, cols in GROUPS.items():
        for c in cols:
            out[g] += [f"r7t_{c}", f"r7o_{c}"]
    out["ROUND"].append("r7_elim_mean")
    for g, cols in PLAYER_GROUPS.items():
        out[g] += [f"r7_{c}" for c in cols]
    return out


def team_map_table():
    """One row per (mapstatsid, teamid) with the round-level counts of TEAM_V (except maps, which is 1)."""
    r = pd.read_csv(os.path.join(H, "map_rounds.csv.gz"))
    e = pd.read_csv(os.path.join(H, "map_round_economy.csv.gz"))
    e = e.merge(r[["mapstatsid", "round", "winnerid", "win_type"]], on=["mapstatsid", "round"], how="left")
    o = e[["mapstatsid", "round", "teamid", "buy"]].rename(columns={"teamid": "oppid", "buy": "opp_buy"})
    e = e.merge(o, on=["mapstatsid", "round"], how="left")
    e = e[e.teamid != e.oppid].copy()
    e["won"] = e.won.astype(str).isin(["True", "true", "1", "1.0"])
    # post-pistol conversion: the two rounds after a pistol round the team won
    pis = e[e.buy == "pistol"][["mapstatsid", "teamid", "round", "won"]]
    pw = pis[pis.won]
    conv_keys = pd.concat([pw.assign(round=pw["round"] + 1), pw.assign(round=pw["round"] + 2)])[["mapstatsid", "teamid", "round"]]
    conv_keys["is_conv"] = True
    e = e.merge(conv_keys.drop_duplicates(), on=["mapstatsid", "teamid", "round"], how="left")
    e["is_conv"] = e.is_conv.fillna(False).astype(bool)
    # score state per round (before the round): team score - opponent score
    e = e.sort_values(["mapstatsid", "teamid", "round"])
    e["w_int"] = e.won.astype(int)
    e["l_int"] = 1 - e.w_int
    e["sc_t"] = e.groupby(["mapstatsid", "teamid"]).w_int.cumsum() - e.w_int
    e["sc_o"] = e.groupby(["mapstatsid", "teamid"]).l_int.cumsum() - e.l_int
    e["deficit"] = e.sc_o - e.sc_t
    eco = e.buy.isin(["eco", "half_buy"])
    g = e.assign(
        pis_n=(e.buy == "pistol"), pis_w=(e.buy == "pistol") & e.won,
        conv_n=e.is_conv, conv_w=e.is_conv & e.won,
        ecof_n=eco, force_n=(e.buy == "half_buy"), force_w=(e.buy == "half_buy") & e.won,
        full_n=(e.buy == "full_buy"), full_w=(e.buy == "full_buy") & e.won,
        anti_n=(e.buy == "full_buy") & (e.opp_buy == "eco"), anti_w=(e.buy == "full_buy") & (e.opp_buy == "eco") & e.won,
        elim=(e.win_type == "elimination"), bomb_w=(e.win_type == "bomb") & e.won,
        def_w=(e.win_type == "defuse") & e.won, time_w=(e.win_type == "time") & e.won,
        ct_n=(e.side == "ct"), ct_w=(e.side == "ct") & e.won, t_n=(e.side == "t"), t_w=(e.side == "t") & e.won,
        trail_r=(e.deficit >= 3), one=1,
    ).groupby(["mapstatsid", "matchid", "teamid"]).agg(
        rounds=("one", "sum"), won_r=("w_int", "sum"), pis_n=("pis_n", "sum"), pis_w=("pis_w", "sum"),
        conv_n=("conv_n", "sum"), conv_w=("conv_w", "sum"), ecof_n=("ecof_n", "sum"), force_n=("force_n", "sum"),
        force_w=("force_w", "sum"), full_n=("full_n", "sum"), full_w=("full_w", "sum"), anti_n=("anti_n", "sum"),
        anti_w=("anti_w", "sum"), equip=("equipment", "mean"), elim=("elim", "sum"), bomb_w=("bomb_w", "sum"),
        def_w=("def_w", "sum"), time_w=("time_w", "sum"), ct_n=("ct_n", "sum"), ct_w=("ct_w", "sum"),
        t_n=("t_n", "sum"), t_w=("t_w", "sum"), maxdef=("deficit", "max"), trail_r=("trail_r", "sum"),
    ).reset_index()
    g["lost_r"] = g.rounds - g.won_r
    g["won_m"] = (g.won_r > g.lost_r).astype(float)
    g["close"] = ((g.won_r - g.lost_r).abs() <= 3).astype(float)
    g["comeback"] = ((g.won_m == 1) & (g.maxdef >= 5)).astype(float)
    g["side_rounds_ct"] = g.ct_n
    g["side_rounds_t"] = g.t_n
    return g


def build(verbose=True):
    S = pd.read_pickle(os.path.join(OUT, "series_features.pkl"))[["matchid", "playerid", "teamid", "oppid", "day"]]
    TM = team_map_table()
    pm = pd.read_csv(os.path.join(DATA, "work", "player_map.csv.gz"), low_memory=False,
                     usecols=["mapstatsid", "matchid", "playerid", "teamid", "oppid", "k", "d", "td", "fa", "k_ct", "k_t",
                              "d_ct", "d_t", "rounds", "map_won", "date", "team_rounds", "opp_rounds"])
    pm["day"] = pd.to_datetime(pm["date"]).map(lambda d: d.toordinal())
    # complete scoreboards only (features.py rule)
    gsz = pm.groupby("mapstatsid").playerid.transform("size")
    gnu = pm.groupby("mapstatsid").playerid.transform("nunique")
    tsz = pm.groupby(["mapstatsid", "teamid"]).playerid.transform("size")
    pm = pm[(gsz == 10) & (gnu == 10) & (tsz == 5) & (pm.rounds > 0)].copy()
    pm = pm.merge(TM[["mapstatsid", "teamid", "side_rounds_ct", "side_rounds_t", "close"]], on=["mapstatsid", "teamid"],
                  how="left")
    ctx = pd.read_csv(os.path.join(DATA, "work", "team_match_ctx.csv.gz"), usecols=["matchid", "teamid", "elo_pre", "opp_elo_pre"])
    ctx = ctx.drop_duplicates(["matchid", "teamid"])
    pm = pm.merge(ctx, on=["matchid", "teamid"], how="left")
    gap = pm.opp_elo_pre - pm.elo_pre
    pm["tier"] = np.where(gap.isna(), 9, np.where(gap > 50, 0, np.where(gap < -50, 2, 1)))
    TM = TM.merge(pm.drop_duplicates("mapstatsid")[["mapstatsid", "day"]], on="mapstatsid", how="inner")

    T = Decay(HL, len(TEAM_V))
    P = Decay(HL, len(PL_V))
    tm_by_day = {d: g for d, g in TM.groupby("day")}
    pm_by_day = {d: g for d, g in pm.groupby("day")}
    rows_by_day = {d: g for d, g in S.groupby("day")}
    days = sorted(set(tm_by_day) | set(rows_by_day) | set(pm_by_day))
    out = []
    zt = np.zeros(len(TEAM_V))
    zp = np.zeros(len(PL_V))
    for d in days:
        rows = rows_by_day.get(d)
        if rows is not None:
            for r in rows.itertuples(index=False):
                f = {"matchid": r.matchid, "playerid": r.playerid}
                for pre, tid in (("r7t_", r.teamid), ("r7o_", r.oppid)):
                    v = T.read(tid, d)
                    v = v[0] if v is not None else zt
                    f.update(_team_feats(pre, v))
                f["r7_elim_mean"] = (f["r7t_elim_share"] + f["r7o_elim_share"]) / 2
                v = P.read(r.playerid, d)
                f.update(_player_feats(v[0] if v is not None else zp))
                out.append(f)
        tg = tm_by_day.get(d)
        if tg is not None:
            for x in tg.itertuples(index=False):
                T.add(x.teamid, d, [1, x.rounds, x.won_r, x.pis_n, x.pis_w, x.conv_n, x.conv_w, x.ecof_n, x.force_n,
                                    x.force_w, x.full_n, x.full_w, x.anti_n, x.anti_w, x.equip * x.rounds, x.elim, x.bomb_w,
                                    x.def_w, x.time_w, x.ct_n, x.ct_w, x.t_n, x.t_w, x.close, x.comeback, x.maxdef,
                                    x.trail_r, x.won_m])
        pg = pm_by_day.get(d)
        if pg is not None:
            for x in pg.itertuples(index=False):
                R = x.rounds
                ctr = x.side_rounds_ct if not pd.isna(x.side_rounds_ct) else 0.0
                tr = x.side_rounds_t if not pd.isna(x.side_rounds_t) else 0.0
                has_side = ctr > 0 and tr > 0 and not pd.isna(x.k_ct) and not pd.isna(x.k_t)
                won = (x.map_won == 1) or (x.map_won is True)
                close = (x.close == 1)
                vec = np.zeros(len(PL_V))
                vec[PIX["rounds"]] = R
                vec[PIX["k"]] = x.k
                if has_side:
                    vec[PIX["ct_r"]], vec[PIX["ct_k"]], vec[PIX["ct_d"]] = ctr, x.k_ct, (x.d_ct if not pd.isna(x.d_ct) else 0)
                    vec[PIX["t_r"]], vec[PIX["t_k"]], vec[PIX["t_d"]] = tr, x.k_t, (x.d_t if not pd.isna(x.d_t) else 0)
                vec[PIX["d"]] = x.d
                vec[PIX["td"]] = x.td if not pd.isna(x.td) else 0
                vec[PIX["fa"]] = x.fa if not pd.isna(x.fa) else 0
                key = ("won" if won else "lost")
                vec[PIX[key + "_r"]], vec[PIX[key + "_k"]] = R, x.k
                if close:
                    vec[PIX["close_r"]], vec[PIX["close_k"]] = R, x.k
                if x.tier != 9:
                    key = ("str", "sim", "weak")[int(x.tier)]
                    vec[PIX[key + "_r"]], vec[PIX[key + "_k"]] = R, x.k
                P.add(x.playerid, d, vec)
        if verbose and d % 90 == 0:
            print("r7 day", pd.Timestamp.fromordinal(d).date(), "rows", len(out), flush=True)
    return pd.DataFrame(out)


def _team_feats(pre, v):
    t = lambda n: v[TIX[n]]
    mp, R = t("maps"), t("rounds")
    return {
        pre + "pis_wr": shrink(t("pis_w"), t("pis_n"), 0.5, 20.0),
        pre + "conv_wr": shrink(t("conv_w"), t("conv_n"), 0.75, 20.0),
        pre + "ecof_share": shrink(t("ecof_n"), R, 0.18, 100.0),
        pre + "force_wr": shrink(t("force_w"), t("force_n"), 0.30, 20.0),
        pre + "full_wr": shrink(t("full_w"), t("full_n"), 0.52, 50.0),
        pre + "anti_wr": shrink(t("anti_w"), t("anti_n"), 0.85, 20.0),
        pre + "equip": shrink(t("equip"), R, 20000.0, 100.0),
        pre + "elim_share": shrink(t("elim"), R, 0.65, 100.0),
        pre + "bomb_share": shrink(t("bomb_w"), t("won_r"), 0.09, 50.0),
        pre + "def_share": shrink(t("def_w"), t("won_r"), 0.06, 50.0),
        pre + "time_share": shrink(t("time_w"), t("won_r"), 0.02, 50.0),
        pre + "ct_wr": shrink(t("ct_w"), t("ct_n"), 0.5, 50.0),
        pre + "t_wr": shrink(t("t_w"), t("t_n"), 0.5, 50.0),
        pre + "close_rate": shrink(t("close"), mp, 0.35, 5.0),
        pre + "comeback_rate": shrink(t("comeback"), mp, 0.05, 5.0),
        pre + "maxdef": shrink(t("maxdef"), mp, 4.0, 5.0),
        pre + "trail_share": shrink(t("trail_r"), R, 0.25, 100.0),
    }


def _player_feats(v):
    p = lambda n: v[PIX[n]]
    base = shrink(p("k"), p("rounds"), POP_KPR, 150.0)
    ct = shrink(p("ct_k"), p("ct_r"), base, 100.0)
    tt = shrink(p("t_k"), p("t_r"), base, 100.0)
    return {
        "r7_p_kpr_won": shrink(p("won_k"), p("won_r"), base, 100.0),
        "r7_p_kpr_lost": shrink(p("lost_k"), p("lost_r"), base, 100.0),
        "r7_p_kpr_close": shrink(p("close_k"), p("close_r"), base, 100.0),
        "r7_p_kpr_ct": ct, "r7_p_kpr_t": tt, "r7_p_side_gap": ct - tt,
        "r7_p_dpr_ct": shrink(p("ct_d"), p("ct_r"), 0.68, 100.0),
        "r7_p_dpr_t": shrink(p("t_d"), p("t_r"), 0.68, 100.0),
        "r7_p_traded_share": shrink(p("td"), p("d"), 0.2, 50.0),
        "r7_p_fapr": shrink(p("fa"), p("rounds"), 0.03, 150.0),
        "r7_p_kpr_vs_stronger": shrink(p("str_k"), p("str_r"), base, 100.0),
        "r7_p_kpr_vs_similar": shrink(p("sim_k"), p("sim_r"), base, 100.0),
        "r7_p_kpr_vs_weaker": shrink(p("weak_k"), p("weak_r"), base, 100.0),
    }


if __name__ == "__main__":
    df = build()
    df.to_pickle(os.path.join(OUT, "r7_features.pkl"))
    print("r7 rows", len(df), "cols", df.shape[1])
