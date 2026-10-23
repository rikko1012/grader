"""Read-only export of the small HLTV-DB line / market tables (prop_grades, prop_lines, prop_status, upcoming_matches,
thunderpick_snapshots metadata WITHOUT body) into DATA/hltv_props.sqlite."""
import os, sys, sqlite3, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import conn
DATA = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc\data"
OUT = os.path.join(DATA, "hltv_props.sqlite")
Q = {"prop_grades": "select book, line_id, start_time::text, player, team, opponent, market, maps, line::float, odds_type, status, result, actual::float, map_values, matchid, note, updated_at::text from prop_grades limit 100000",
     "prop_lines": "select book, line_id, start_time::text, player, team, opponent, market, maps, line::float, odds_type, open_line::float, prev_line::float, move::float, moves, moved_at::text, first_seen::text, other_line::float, higher::float, lower::float, stat, updated_at::text from prop_lines limit 100000",
     "prop_status": "select book, ok, lines, source, error, checked_at::text, last_ok::text, writer from prop_status",
     "upcoming_matches": "select matchid, time::text, team1, team2, event, format, stars, live, title, url, scraped_at::text from upcoming_matches limit 10000",
     "thunderpick_snapshots": "select id, fetched_at::text, path, http_status, outcome, events, sha256, length(body_gz) body_bytes, duration_ms, runner, error from thunderpick_snapshots limit 100000"}
if os.path.exists(OUT): os.remove(OUT)
o = sqlite3.connect(OUT); c = conn()
for t, s in Q.items():
    with c.cursor() as cur:
        cur.execute(s); cols = [d[0] for d in cur.description]; rows = cur.fetchall()
    o.execute(f"create table {t} ({', '.join(cols)})")
    o.executemany(f"insert into {t} values ({','.join('?'*len(cols))})", rows)
    print(t, len(rows))
o.execute("create table export_meta (k, v)"); o.execute("insert into export_meta values ('created_at', ?)", (dt.datetime.now(dt.timezone.utc).isoformat(),))
o.commit()
