"""Map-veto research dataset (read-only inputs, local outputs).

Source: the 2-year read-only export of the production HLTV DB (research/cs2_accuracy_2y/inventory/export_hltv.py ->
scratch cs2acc/data/hltv_window.sqlite, matches dated 2024-10-01 .. 2026-10-22). Its `map_vetoes` table is the
scraper's parse of the veto box on each stored HLTV match page (raw_pages.html_gz): one row per step (step, teamid,
removed|picked|leftover, mapid). check_parse.py re-parses a sample of stored pages with hltv._vetoes and compares.

Per match: event, date, LAN flag, HLTV listed order (team1/team2), format (from the veto layout), actions in order,
action-order template (actor pattern relative to the first actor), pool, lineups (player ids, player_stats map 0),
per-map winners, series winner and both teams' pre-match series Elo. Record classes and pools come from
veto_model.make_record / assign_pools (one definition shared with the live table builder):

  complete   7 consecutive steps, 7 distinct maps, a template some event uses for that format
  inferred   only the leftover step is missing; it is reconstructed as the single unseen map of the event/period pool
  partial    other missing steps
  anomalous  an actor pattern no event uses (eg 'AAAAAA', 'ABABBB'), duplicate maps, an unknown layout ('ppppppL')

usage: python build_dataset.py  -> <scratch>/veto/vmodel/veto_dataset.json.gz
"""
import collections, datetime as dt, gzip, json, os, sqlite3, sys

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
SRC = os.path.join(SCR, "cs2acc", "data", "hltv_window.sqlite")
OUT = os.path.join(SCR, "veto", "vmodel")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import veto_model as vm  # noqa: E402


def winner(sc):
    if len(sc) != 2:
        return None
    (ta, a), (tb, b) = sorted(sc.items())
    return ta if a > b else tb if b > a else None


def main():
    os.makedirs(OUT, exist_ok=True)
    c = sqlite3.connect(SRC)
    mapname = {r[0]: vm.map_key(r[1]) for r in c.execute("select mapid, name from maps")}
    ov = {r[0]: r[1:] for r in c.execute("select matchid, event, date, lan from match_overview")}
    sq = {r[0]: (r[1], r[2]) for r in c.execute("select matchid, team1, team2 from scrape_queue")}
    tname = {r[0]: r[1] for r in c.execute("select teamid, name from teams")}
    acts = collections.defaultdict(list)
    for mid, st, tid, act, mp in c.execute("select matchid, step, teamid, action, mapid from map_vetoes"):
        acts[mid].append((int(st), tid, act, mapname.get(mp, str(mp))))
    teams, series = collections.defaultdict(set), collections.defaultdict(dict)
    permap = collections.defaultdict(lambda: collections.defaultdict(dict))
    for mid, tid, mp, sc in c.execute("select matchid, teamid, mapid, score from matches where sideid = 0"):
        teams[mid].add(tid)
        if mp == 0:
            series[mid][tid] = sc
        else:
            permap[mid][mapname.get(mp, str(mp))][tid] = sc
    lineups = collections.defaultdict(lambda: collections.defaultdict(set))
    for mid, pid, tid in c.execute("select matchid, playerid, teamid from player_stats where mapid = 0"):
        lineups[mid][tid].add(pid)
    recs = []
    for mid, (event, date, lan) in ov.items():
        tl = sorted(teams.get(mid, ()))
        t1, t2 = sq.get(mid, (None, None))
        by_name = {vm.norm(tname.get(t)): t for t in tl}
        listed = [by_name.get(vm.norm(t1)), by_name.get(vm.norm(t2))]
        if None in listed or len(set(listed)) != 2:
            listed = None
        r = vm.make_record(mid, date, event, lan, tl, listed, acts.get(mid, []),
                           {t: lineups[mid].get(t, ()) for t in tl},
                           {m: winner(sc) for m, sc in permap.get(mid, {}).items()}, winner(series.get(mid, {})))
        r["n_maps_played"] = len(permap.get(mid, {}))
        recs.append(r)
    recs.sort(key=lambda r: (r["date"], r["mid"]))
    vm.assign_pools(recs)
    # pre-match series Elo (same rule as VetoTable: same-day results applied after the whole day) - for tiers
    elo = collections.defaultdict(lambda: 1500.0)
    by_day = collections.defaultdict(list)
    for r in recs:
        by_day[r["date"]].append(r)
    for d in sorted(by_day):
        for r in by_day[d]:
            r["elo"] = {str(t): round(elo[t], 1) for t in r["teams"]}
        for r in by_day[d]:
            if r["winner"] in r["teams"] and len(r["teams"]) == 2:
                a, b = r["teams"]
                ea = 1 / (1 + 10 ** ((elo[b] - elo[a]) / 400))
                s = 1.0 if r["winner"] == a else 0.0
                elo[a] += vm.ELO_K * (s - ea)
                elo[b] -= vm.ELO_K * (s - ea)
    with gzip.open(os.path.join(OUT, "veto_dataset.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"source": SRC, "built": dt.datetime.utcnow().isoformat() + "Z", "records": recs}, f)
    print("records", len(recs), collections.Counter(r["status"] for r in recs))


if __name__ == "__main__":
    main()
