"""Read-only, month-paged export of the production HLTV DB rows for the research window into a LOCAL
research SQLite (never written back). Every query is bounded by one month of match_overview.date.
HTML blobs (raw_pages.html_gz, mapstats_pages.html_gz) are NOT exported - only their metadata.

usage: python export_hltv.py  -> DATA/hltv_window.sqlite"""
import os, sys, sqlite3, time, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
from db import conn

DATA = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc\data"
OUT = os.path.join(DATA, "hltv_window.sqlite")
# Window 2024-10-22 .. 2026-10-21 plus the post-cutoff days present (marked by date; forward set, not to be used in dev)
START, END = dt.date(2024, 10, 1), dt.date(2026, 10, 31)   # whole months; window filtering happens in analysis

PER_MATCH = {  # table -> select list (joined to match_overview mo on matchid, filtered by mo.date)
    "match_overview": "mo.matchid, mo.event, mo.date, mo.parser_version, mo.source, mo.forfeited_maps, mo.lan",
    "matches": "t.matchid, t.teamid, t.mapid, t.sideid, t.score",
    "match_maps": "t.matchid, t.number, t.mapid, t.mapstatsid, t.url, t.start_ct, t.picked_by",
    "map_vetoes": "t.matchid, t.step, t.teamid, t.action, t.mapid",
    "player_stats": "t.matchid, t.playerid, t.teamid, t.mapid, t.sideid, t.k, t.d, t.ek, t.ed, t.roundswing, t.adr, t.eadr, t.kast, t.ekast, t.rating",
    "map_player_stats": "t.mapstatsid, t.matchid, t.playerid, t.teamid, t.sideid, t.k, t.hs, t.a, t.fa, t.d, t.td, t.ok, t.od, t.mk, t.clutches, t.kast, t.adr, t.swing, t.rating, t.ek, t.ehs, t.ed, t.etd, t.eok, t.eod, t.ekast, t.eadr",
    "map_headshots": "t.mapstatsid, t.matchid, t.playerid, t.k, t.hs",
    "map_rounds": "t.mapstatsid, t.matchid, t.round, t.half, t.winnerid, t.winner_side, t.win_type",
    "map_round_economy": "t.mapstatsid, t.matchid, t.round, t.half, t.teamid, t.side, t.equipment, t.buy, t.won",
    "data_issues": "t.matchid, t.severity, t.code, t.message, t.found_at::text",
    "raw_pages": "t.matchid, t.url, t.fetched_at::text, length(t.html_gz) AS html_bytes",
    "mapstats_pages": "t.mapstatsid, t.kind, t.matchid, t.parser_version, t.fetched_at::text, (t.html_gz IS NOT NULL)::int AS html_in_db",
    "scrape_queue": "t.matchid, t.url, t.team1, t.score1, t.team2, t.score2, t.event, t.format, t.status, t.attempts, t.last_error, t.found_at::text, t.updated_at::text, t.not_before::text",
}
WHOLE = {  # small dimension tables, exported whole
    "teams": "select teamid, name, country, country_name from teams",
    "players": "select playerid, name from players",
    "maps": "select mapid, name from maps",
    "sides": "select sideid, name from sides",
    "events": "select eventid, name, lan, location, updated_at::text from events",
    "team_rankings": "select date::text, rank, teamid, points from team_rankings",
    "mapstats_page_status": "select mapstatsid, kind, attempts, last_error, not_before::text, updated_at::text from mapstats_page_status",
    "mapstats_status": "select mapstatsid, attempts, last_error, not_before::text, updated_at::text from mapstats_status",
}

def months():
    d = START
    while d <= END:
        n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, n
        d = n

def main():
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(OUT):
        os.remove(OUT)
    lite = sqlite3.connect(OUT)
    c = conn()
    counts = {}
    def put(name, cur):
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        lite.execute(f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(cols)})")
        lite.executemany(f"INSERT INTO {name} VALUES ({','.join('?'*len(cols))})",
                         [tuple(str(v) if isinstance(v, (dt.date, dt.datetime)) else v for v in r) for r in rows])
        counts[name] = counts.get(name, 0) + len(rows)
    for name, sql in WHOLE.items():
        with c.cursor() as cur:
            cur.execute(sql); put(name, cur)
    for a, b in months():
        for name, cols in PER_MATCH.items():
            if name == "match_overview":
                sql = f"select {cols} from match_overview mo where mo.date >= %s and mo.date < %s"
            else:
                sql = (f"select {cols} from {name} t join match_overview mo on mo.matchid = t.matchid "
                       f"where mo.date >= %s and mo.date < %s")
            with c.cursor() as cur:
                cur.execute(sql, (a, b)); put(name, cur)
        lite.commit()
        print(a, {k: counts.get(k) for k in ("match_overview", "player_stats", "map_rounds")}, flush=True)
        time.sleep(0.3)
    # scrape_queue rows whose match never got a match_overview row (failed / waiting), bounded by id range
    with c.cursor() as cur:
        cur.execute("select matchid, url, team1, score1, team2, score2, event, format, status, attempts, last_error, "
                    "found_at::text, updated_at::text, not_before::text from scrape_queue q "
                    "where not exists (select 1 from match_overview mo where mo.matchid = q.matchid)")
        put("scrape_queue_orphans", cur)
    lite.execute("CREATE TABLE export_meta (k, v)")
    lite.executemany("INSERT INTO export_meta VALUES (?,?)",
                     [("created_at", dt.datetime.now(dt.timezone.utc).isoformat()), ("months", f"{START}..{END}"),
                      ("source", "production HLTV Postgres (Render hltv-db), read-only session")] +
                     [("rows." + k, str(v)) for k, v in counts.items()])
    for ix in ["match_overview(matchid)", "match_maps(matchid)", "match_maps(mapstatsid)", "player_stats(matchid, mapid)",
               "map_player_stats(mapstatsid)", "map_headshots(mapstatsid)", "map_rounds(mapstatsid)",
               "map_round_economy(mapstatsid)", "matches(matchid)"]:
        lite.execute(f"CREATE INDEX IF NOT EXISTS ix_{ix.split('(')[0]}_{abs(hash(ix))%9999} ON {ix}")
    lite.commit(); lite.close()
    print(counts)

if __name__ == "__main__":
    main()
