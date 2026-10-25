import sys, json
sys.path.insert(0, '..'); from lq import lq
sql = """SELECT f.id, f.forecast_at, f.platform, f.market_key, f.stat, f.scope, f.odds_type, f.line, f.sid, f.pid, f.tid, f.opp_tid, f.role,
 f.best_of, f.series_start, f.model_version, f.fit_sha, f.lambda, f.nb_size, f.p_over, f.p_under, f.p_push, f.model_side, f.side_offered,
 f.sides, f.grade_label, f.units, json_extract(f.inputs,'$.league') AS league, json_extract(f.inputs,'$.p_win') AS p_win,
 json_extract(f.inputs,'$.team_win.market.p') AS mkt_win, json_extract(f.inputs,'$.history_games') AS hist_games,
 json_extract(f.inputs,'$.data_quality') AS dq, json_extract(f.inputs,'$.history_last_game') AS hist_last,
 s.status AS st, s.actual, s.over_hit, s.push, s.games
 FROM lol_forecasts f
 JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts
       WHERE forecast_at <= strftime('%Y-%m-%dT%H:%M:%SZ', series_start, '-30 minutes') AND odds_type IN ('standard','balanced')
       GROUP BY platform, market_key, line) z ON z.id = f.id
 JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line
 JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v"""
rows = lq(sql)
json.dump(rows, open("lol_p1.json", "w"))
import collections
print(len(rows)); print(collections.Counter((r['series_start'][:10], r['model_version'], r['stat'], r['st']) for r in rows))
