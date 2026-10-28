"""Two small aggregate reads on the HLTV Postgres (read-only session, statement timeout): rounds per map by series
format and map number, and the Map 1 share of Maps 1-2 kills - inputs for the Map 1 / BO1 candidate design."""
import sys, json
sys.path.insert(0, r"C:\Users\tikic\OneDrive\Documents\cs2_scout\.claude\worktrees\agent-a63fa0bb4b1c57213\research\cs2_accuracy_2y\inventory")
import db
out = {}
out["rounds_by_format"] = [dict(r) for r in db.q("""
with mm as (select m.matchid, m.number, sum(x.score) as rounds
            from match_maps m join matches x on x.matchid=m.matchid and x.mapid=m.mapid
            join match_overview o on o.matchid=m.matchid
            where o.date >= '2025-10-01' and m.mapstatsid is not null group by m.matchid, m.number)
select coalesce(q.format,'?') as format, mm.number, count(*) as maps, round(avg(mm.rounds)::numeric,3) as avg_rounds,
       round(stddev(mm.rounds)::numeric,3) as sd_rounds, round(avg(case when mm.rounds>24 then 1 else 0 end)::numeric,4) as ot_share
from mm left join scrape_queue q on q.matchid=mm.matchid group by 1,2 order by 1,2""")]
print(json.dumps(out["rounds_by_format"], default=str, indent=0)[:1500], flush=True)
out["map1_share"] = [dict(r) for r in db.q("""
select lan, count(*) as series, round(avg(k1::numeric/(k1+k2)),4) as map1_share, round(avg(k1)::numeric,2) as k1, round(avg(k2)::numeric,2) as k2
from player_series where date >= '2025-10-01' and k1 is not null and k2 is not null and k1+k2 > 0 and maps_in_series >= 2 group by lan""")]
print(json.dumps(out["map1_share"], default=str))
json.dump(out, open("hist_ctx.json", "w"), default=str)
