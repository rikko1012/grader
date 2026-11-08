"""Validate cs2_roles.build() on the local HLTV extract (read only; nothing written outside this folder's results file).

It rebuilds, with pandas, exactly the rows production's _AGG_SQL returns (CT/T map_player_stats rows in the 180-day window,
team totals per map-side, decayed sums grouped by player, team, map name, side) plus the player_awp / player_roles listings,
runs the same pure build(), and reports: role counts, same-role collisions per lineup, coverage of board-relevant players
(names on the prop-line feed in the last 30 days), and the ground-truth list.

usage: python research/cs2_roles_2026_11_07/validate.py [extract_dir] [ref_date]  -> results.json beside this file"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import cs2_roles  # noqa: E402

D = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\tikic\cs2_scout_ops\data\cs2_full\hltv"
REF = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else "2026-11-05")


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


# Ground truth (research brief 2026-11-07; role from general knowledge, mid-2026 - IGL truth = Liquipedia listing)
# nick -> (accepted roles, confidence)
GT = {
    "zywoo": ({"AWPer", "Star rifler"}, "High"), "m0nesy": ({"AWPer"}, "High"), "w0nderful": ({"AWPer"}, "High"),
    "torzsi": ({"AWPer"}, "High"), "sh1ro": ({"AWPer"}, "High"), "910": ({"AWPer"}, "High"),
    "molodoy": ({"AWPer"}, "High"), "ultimate": ({"AWPer"}, "Med"), "jame": ({"IGL", "AWPer"}, "High"),
    "apex": ({"IGL"}, "High"), "aleksib": ({"IGL"}, "High"), "karrigan": ({"IGL"}, "High"), "blitz": ({"IGL"}, "High"),
    "fallen": ({"IGL"}, "High"), "tabsen": ({"IGL"}, "High"), "hooxi": ({"IGL"}, "Med"),
    "flamez": ({"Entry"}, "High"), "donk": ({"Entry", "Star rifler"}, "High"), "xertion": ({"Entry", "IGL"}, "High"),
    "kyousuke": ({"Entry"}, "Med"), "malbsmd": ({"Entry"}, "Med"), "xantares": ({"Entry"}, "Med"),
    "brollan": ({"Entry"}, "Med"), "ropz": ({"Lurker"}, "High"), "kscerato": ({"Lurker", "Star rifler"}, "Med"),
    "spinx": ({"Lurker"}, "Med"), "frozen": ({"Lurker", "Star rifler"}, "Low"), "mezii": ({"Support"}, "High"),
    "zont1x": ({"Support"}, "Med"), "b1t": ({"Support", "Star rifler"}, "Med"), "jimpphat": ({"Support"}, "Med"),
    "niko": ({"Star rifler"}, "High"), "elige": ({"Star rifler"}, "Med"),
}


def load_rows(parity=None):
    """parity 0 / 1: only maps with an even / odd mapstatsid (split-half checks)."""
    since = REF - pd.Timedelta(days=cs2_roles.WINDOW_DAYS)
    mo = pd.read_csv(os.path.join(D, "match_overview.csv.gz"), usecols=["matchid", "date"], parse_dates=["date"])
    mo = mo[(mo.date >= since) & (mo.date <= REF)]
    s = pd.read_csv(os.path.join(D, "map_player_stats.csv.gz"),
                    usecols=["mapstatsid", "matchid", "playerid", "teamid", "sideid", "k", "a", "fa", "d", "td", "ok", "od",
                             "clutches", "rating"])
    s = s[s.sideid.isin([1, 2])].merge(mo, on="matchid")
    if parity is not None:
        s = s[s.mapstatsid % 2 == parity]
    s["att"] = s.ok + s.od
    grp = s.groupby(["mapstatsid", "teamid", "sideid"])
    s["tk"], s["tat"], s["tfa"] = grp.k.transform("sum"), grp.att.transform("sum"), grp.fa.transform("sum")
    s["w"] = 0.5 ** ((REF - s.date).dt.days / cs2_roles.HALF_LIFE_DAYS)
    mm = pd.read_csv(os.path.join(D, "match_maps.csv.gz"), usecols=["matchid", "mapstatsid", "mapid"])
    mp = pd.read_csv(os.path.join(D, "maps.csv.gz")).rename(columns={"name": "map"})
    s = s.merge(mm, on=["matchid", "mapstatsid"]).merge(mp, on="mapid")
    for c, src in (("k", "k"), ("a", "a"), ("fa", "fa"), ("d", "d"), ("td", "td"), ("ok", "ok"), ("od", "od"),
                   ("cl", "clutches"), ("rt", "rating"), ("tk", "tk"), ("tat", "tat"), ("tfa", "tfa")):
        s["w_" + c] = s.w * s[src].fillna(0)
    agg = s.groupby(["playerid", "teamid", "map", "sideid"]).agg(
        n=("w", "size"), last=("date", "max"), w=("w", "sum"),
        **{c: ("w_" + c, "sum") for c in ("k", "a", "fa", "d", "td", "ok", "od", "cl", "rt", "tk", "tat", "tfa")}).reset_index()
    pl = pd.read_csv(os.path.join(D, "players.csv.gz")).rename(columns={"name": "nick"})
    tm = pd.read_csv(os.path.join(D, "teams.csv.gz"), usecols=["teamid", "name"]).rename(columns={"name": "team"})
    agg = agg.merge(pl, on="playerid").merge(tm, on="teamid", how="left")
    rows = []
    for r in agg.itertuples(index=False):
        rows.append({"pid": str(r.playerid), "tid": str(r.teamid), "map": r.map, "side": "ct" if r.sideid == 1 else "t",
                     "n": int(r.n), "last": r.last.date(), "w": r.w, "k": r.k, "a": r.a, "fa": r.fa, "d": r.d, "td": r.td,
                     "ok": r.ok, "od": r.od, "cl": r.cl, "rt": r.rt, "tk": r.tk, "tat": r.tat, "tfa": r.tfa,
                     "nick": r.nick, "team": r.team})
    awp_df = pd.read_csv(os.path.join(D, "player_awp.csv.gz"))
    awp = {str(r.playerid): (r.kills, r.awp_share) for r in awp_df.itertuples() if pd.notna(r.awp_share)}
    pr = pd.read_csv(os.path.join(D, "player_roles.csv.gz"))
    listed = {}
    for r in pr.itertuples():
        listed[str(r.playerid)] = {"roles": [x.strip() for x in str(r.roles if pd.notna(r.roles) else "").split(",") if x.strip()],
                                   "is_igl": bool(r.is_igl) if pd.notna(r.is_igl) else None,
                                   "team_match": bool(r.team_match) if pd.notna(r.team_match) else None,
                                   "status": r.status if pd.notna(r.status) else None,
                                   "hltv_team": r.hltv_team if pd.notna(r.hltv_team) else None}
    return rows, awp, listed, len(s)


HALF = {"MIN_MAPS": 15, "MAP_MIN": 5, "NOTE_MIN": 8}     # half the data: half the sample minimums


def split_half(awp, listed):
    """Build on even-id maps (A) and odd-id maps (B) separately. Roles: how often B shows the same role A shows (stat and
    listed roles; AWPer / IGL come from the same snapshot fields in both halves and are left out). Notes: A's named maps
    (the real rule) against B's two strongest maps (B with no lift needed), next to the chance level 2 / (B's maps)."""
    keep = {k: getattr(cs2_roles, k) for k in list(HALF) + ["NOTE_LIFT", "MAP_NOTES"]}
    try:
        cs2_roles.MAP_NOTES = True                         # the test turns the (shipped-off) note on
        for k, v in HALF.items():
            setattr(cs2_roles, k, v)
        ra, rb = load_rows(0)[0], load_rows(1)[0]
        A = cs2_roles.build(ra, awp, listed, ref_date=REF.date())["players"]
        cs2_roles.NOTE_LIFT = 0.0
        B = cs2_roles.build(rb, awp, listed, ref_date=REF.date())["players"]
    finally:
        for k, v in keep.items():
            setattr(cs2_roles, k, v)
    nb = defaultdict(int)                                  # B: maps with >= NOTE_MIN played, per player
    per = defaultdict(int)
    for r in rb:
        if r["side"] == "t":
            per[(r["pid"], r["map"])] += r["n"]
    for (pid, _), n in per.items():
        if n >= HALF["NOTE_MIN"]:
            nb[pid] += 1
    out = {}
    for src in ("stats", "liquipedia"):
        both = [(A[p]["stat_role"], B[p]["stat_role"]) for p in A if p in B and A[p]["source"] == src and A[p]["stat_role"]
                and A[p]["stat_role"] not in ("AWPer",)]
        out[src] = {"a_shown": len(both), "b_same": sum(a == b for a, b in both),
                    "b_other": sum(bool(b) and a != b for a, b in both), "b_blank": sum(not b for _, b in both)}
    pairs = [(p, A[p], B[p]) for p in A if p in B and A[p]["map_note"] and B[p]["map_note"]
             and A[p]["stat_role"] == B[p]["stat_role"]]
    named = same = 0
    chance = 0.0
    for p, a, b in pairs:
        ma, mb = a["map_note"][len("strongest on "):].split("/"), b["map_note"][len("strongest on "):].split("/")
        named += len(ma)
        same += sum(m in mb for m in ma)
        chance += len(ma) * 2.0 / max(nb[p], 2)
    out["notes"] = {"pairs": len(pairs), "named_maps": named, "in_other_half_top2": same,
                    "chance_expected": round(chance, 1)}
    return out


def main():
    cache = os.environ.get("ROLES_ROWS_CACHE")          # optional pickle of load_rows() for repeat runs
    if cache and os.path.exists(cache):
        rows, awp, listed, raw = pd.read_pickle(cache)
    else:
        rows, awp, listed, raw = load_rows()
        if cache:
            pd.to_pickle((rows, awp, listed, raw), cache)
    art = cs2_roles.build(rows, awp, listed, ref_date=REF.date())
    P = art["players"]
    res = {"ref_date": art["ref_date"], "raw_side_rows": raw, "aggregate_rows": len(rows), "lineup_players": len(P),
           "teams": len(art["teams"]), "counts": art["counts"]}

    # lineups of 5+: same-role collisions
    coll, full = Counter(), 0
    for tid, members in art["teams"].items():
        if len(members) < 5:
            continue
        full += 1
        c = Counter(P[p]["role"] for p in members if P[p]["role"])
        for role, n in c.items():
            if n > 1:
                coll[role] += 1
        sc = Counter(P[p]["stat_role"] for p in members if P[p]["stat_role"])
        for role, n in sc.items():
            if n > 1:
                coll["stat:" + role] += 1
    res["lineups_5plus"] = full
    res["collisions"] = dict(coll)
    res["igl_teams"] = sum(1 for m in art["teams"].values() if any(P[p]["igl"] for p in m))
    res["map_notes"] = sum(1 for e in P.values() if e["map_note"])
    res["stat_roles"] = dict(Counter(e["stat_role"] or "" for e in P.values()))

    # top-30 teams (latest ranking): role per lineup slot
    rk = pd.read_csv(os.path.join(D, "team_rankings.csv.gz"))
    rk = rk[rk.date == rk.date.max()].sort_values("rank").head(30)
    top = []
    for r in rk.itertuples():
        m = art["teams"].get(str(r.teamid)) or []
        top.append({"rank": int(r.rank), "team": P[m[0]]["team"] if m else str(r.teamid),
                    "players": [[P[p]["nick"], P[p]["role"], P[p]["stat_role"], P[p]["map_note"]] for p in m]})
    res["top30"] = top
    res["top30_players"] = sum(len(t["players"]) for t in top)
    res["top30_with_role"] = sum(1 for t in top for p in t["players"] if p[1])

    # board-relevant: names on the prop-line feed in the last 30 days (book listings), matched by nick + team name
    pl = pd.read_csv(os.path.join(D, "prop_lines.csv.gz"), usecols=["player", "team", "start_time"])
    pl = pl[pd.to_datetime(pl.start_time, utc=True) >= pd.Timestamp(REF, tz="UTC") - pd.Timedelta(days=30)]
    board = pl[["player", "team"]].drop_duplicates()
    by = defaultdict(list)
    for pid, e in P.items():
        by[norm(e["nick"])].append(e)
    found = withrole = 0
    for r in board.itertuples():
        es = by.get(norm(r.player)) or []
        same = [e for e in es if norm(e["team"]) == norm(r.team)] or (es if len(es) == 1 else [])
        if same:
            found += 1
            withrole += bool(same[0]["role"])
    res["board"] = {"names_30d": len(board), "in_lineups": found, "with_role": withrole}

    # ground truth
    gt_rows, hit, shown, total = [], 0, 0, 0
    for nk, (ok, conf) in GT.items():
        es = sorted(by.get(nk) or [], key=lambda e: e["last"], reverse=True)
        e = es[0] if es else None
        role = e["role"] if e else None
        total += 1
        verdict = "absent" if e is None else "blank" if not role else ("hit" if role in ok else "miss")
        if e is not None and role:
            shown += 1
            hit += role in ok
        gt_rows.append({"player": nk, "team": e["team"] if e else None, "truth": sorted(ok), "conf": conf, "role": role,
                        "stat_role": e["stat_role"] if e else None, "note": e["map_note"] if e else None, "verdict": verdict,
                        "pct": e["pct"] if e else None, "awp_share": e["awp_share"] if e else None,
                        "listed": listed.get(next((p for p, x in P.items() if x is e), None), {}).get("roles") if e else None})
    res["ground_truth"] = {"total": total, "shown": shown, "hits": hit, "rows": gt_rows}

    # the stat method on its own: Liquipedia entry / lurk / support listings removed (IGL / AWP kept), the rifler part of
    # the list only (players whose truth is not AWPer / IGL) - the listings and the list share information, this does not
    bare = {p: dict(x, roles=[r for r in x.get("roles") or [] if r.lower() in ("igl", "awp")]) for p, x in listed.items()}
    SP = cs2_roles.build(rows, awp, bare, ref_date=REF.date())["players"]
    sby = defaultdict(list)
    for e in SP.values():
        sby[norm(e["nick"])].append(e)
    so = []
    for nk, (ok, conf) in GT.items():
        if ok & {"AWPer", "IGL"}:
            continue
        es = sorted(sby.get(nk) or [], key=lambda e: e["last"], reverse=True)
        r = es[0]["stat_role"] if es else None
        so.append({"player": nk, "truth": sorted(ok), "stat_only": r,
                   "verdict": "blank" if not r else "hit" if r in ok else "miss"})
    res["stats_only_riflers"] = {"total": len(so), "shown": sum(bool(x["stat_only"]) for x in so),
                                 "hits": sum(x["verdict"] == "hit" for x in so), "rows": so}
    print("stats only (riflers):", {k: v for k, v in res["stats_only_riflers"].items() if k != "rows"})
    for x in so:
        print("  ", x)
    if not os.environ.get("ROLES_SKIP_SPLIT"):
        res["split_half"] = split_half(awp, listed)
        print("split half", json.dumps(res["split_half"]))
    out = os.path.join(HERE, "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ("top30", "ground_truth")}, indent=1, default=str))
    print("GT shown", shown, "hits", hit, "of", total)
    for g in gt_rows:
        print(f"{g['player']:10} {str(g['team'])[:14]:14} truth={','.join(g['truth']):22} role={str(g['role']):12} "
              f"stat={str(g['stat_role']):12} {g['verdict']:6} note={g['note']} pct={g['pct']} awp={g['awp_share']} listed={g['listed']}")


if __name__ == "__main__":
    main()
