"""Read-only pull of settled LoL forecasts: the exact cohort the LoL Results page reads (lol/center/learning.dashboard:
the latest forecast saved before the series start per platform x market x line, joined to its latest settlement).

usage: python fetch_lol.py <out_dir>

SELECT only, through /admin/lol/query.json (lq.py; the admin token is never printed). Bounded: the forecast-id range is
walked in windows of 4,000 ids, each query has a LIMIT, 0.3 s between queries. The dedupe (MAX(id) per platform x market
x line before the start) runs over the whole table inside each query, so a window only restricts which chosen rows come
back. Output: <out_dir>/lol_units.json.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\tikic\cs2_scout_ops")
from lq import lq  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
STEP = 4000
top = lq("SELECT MAX(id) AS m FROM lol_forecasts")[0]["m"]
SQL = """SELECT f.id, f.forecast_at, f.platform, f.market_key, f.stat, f.scope, f.odds_type, f.market_type, f.line, f.sides,
 f.sid, f.pid, f.tid, f.opp_tid, f.role, f.best_of, f.series_start, f.model_version, f.fit_sha, f.form, f.lambda, f.nb_size,
 f.p_over, f.p_under, f.p_push, f.model_side, f.side_offered, f.grade, f.grade_label, f.units_policy, f.units,
 f.cal_version, f.cal_p_over, f.cal_p_under, f.cal_p_push,
 json_extract(f.inputs, '$.league') AS league, json_extract(f.inputs, '$.p_win') AS p_win,
 json_extract(f.inputs, '$.history_games') AS hist_games, json_extract(f.inputs, '$.history_weight') AS hist_weight,
 json_extract(f.inputs, '$.data_quality') AS dq,
 s.status AS st, s.actual, s.over_hit, s.push, s.version AS s_version, s.games
 FROM lol_forecasts f
 JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts WHERE forecast_at < series_start
       GROUP BY platform, market_key, line) z ON z.id = f.id
 JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv
   ON lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line
 JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v
 WHERE f.id > {a} AND f.id <= {b} ORDER BY f.id LIMIT 5000"""
rows, a = [], 0
while a < top:
    got = lq(SQL.format(a=a, b=a + STEP))
    rows += got
    print("ids", a, a + STEP, len(got), flush=True)
    a += STEP
    time.sleep(0.3)
(OUT / "lol_units.json").write_text(json.dumps({"max_id": top, "rows": rows}), encoding="utf-8")
print("lol units", len(rows))
