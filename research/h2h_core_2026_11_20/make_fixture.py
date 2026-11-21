"""Real HLTV rows (local extract through 2026-11-05) for the hc-1 tests: 3DMAX's five became Envy on 2026-10-15; tomaszin met that
core as BESTIA (2026-03) and MIBR (2026-09), then MIBR met Envy on 2026-10-15 (hltv 2399260)."""
import json, sys, pandas as pd
D = "C:/Users/tikic/cs2_scout_ops/data/cs2_full/"
out_path = sys.argv[1]
pm = pd.read_csv(D + "work/player_map.csv.gz", usecols=["matchid", "playerid", "teamid", "oppid", "number", "k", "hs", "date", "team", "opponent", "mapid", "map"])
pl = pd.read_csv(D + "hltv/players.csv.gz").set_index("playerid")["name"].to_dict()
CUT, SINCE, ENVY, ENVY_PREV, TOMASZIN = "2026-10-15", "2025-10-15", 5991, 2399137, 22570
lu = pm[(pm.matchid == ENVY_PREV) & (pm.teamid == ENVY)].groupby("playerid").size().reset_index()
lineup_rows = [[int(p), int(n)] for p, n in lu.values]
ids = sorted(p for p, _ in lineup_rows)
w = pm[(pm.date >= SINCE) & (pm.date < CUT) & pm.playerid.isin(ids)]
core_rows = []
for (m, t, name, d), grp in w.groupby(["matchid", "teamid", "team", "date"]):
    s = sorted(int(x) for x in set(grp.playerid))
    if len(s) >= 3:
        core_rows.append([int(m), int(t), name, d, s])
me = pm[(pm.playerid == TOMASZIN) & (pm.date >= SINCE) & (pm.date < CUT) & pm.number.isin([1, 2])]
ser = []
for (m, d, opp, oid, team), grp in me.groupby(["matchid", "date", "opponent", "oppid", "team"]):
    if set(grp.number) == {1, 2}:
        g = grp.sort_values("number")
        ser.append({"date": d, "opponent": opp, "value": int(g.k.sum()), "maps": ["de_" + str(x).lower().replace(" ", "") for x in g["map"]], "values": [int(x) for x in g.k],
                    "mid": f"hltv-{int(m)}", "oid": int(oid), "team": team, "hs": int(g.hs.sum()) if g.hs.notna().all() else None})
ser.sort(key=lambda s: (s["date"], s["mid"]), reverse=True)
json.dump({"source": "HLTV extract cs2_scout_ops/data/cs2_full (player_map), rows strictly before 2026-10-15",
           "opponent": "Envy", "opponent_team_id": ENVY, "lineup_from_match": ENVY_PREV, "lineup_rows": lineup_rows,
           "names": {str(p): pl.get(p) for p in ids}, "core_rows": core_rows, "player": "tomaszin", "player_id": TOMASZIN,
           "series": ser}, open(out_path, "w"), indent=0)
print(len(core_rows), len(ser), [pl.get(p) for p in ids])
