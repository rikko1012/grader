"""
Actual coverage of the LoL data center, from what is stored (never from what was requested):
by competition (Leaguepedia tournament page, with league / region / level), by patch and by stat; gaps (stretches of
more than 21 days without a game inside a league's covered span); identity quarantine; the line archive.
refresh() is run by the worker after each build and stored; cached() only reads it (cheap for the web process).
"""
import json
import time
from collections import defaultdict
from datetime import datetime

from . import store
from .names import page_key

STATS = ("kills", "deaths", "assists", "team_kills", "cs", "gold", "damage", "vision", "champion", "role", "side")


def compute(con):
    t0 = time.time()
    tours = {page_key(r["OverviewPage"]): r for r in store.current_rows(con, "Tournaments") if r.get("OverviewPage")}
    span = con.execute("SELECT MIN(start_utc), MAX(start_utc), COUNT(*) FROM c_games WHERE remake = 0").fetchone()
    by_t = {}
    for r in con.execute("""SELECT g.tournament_page AS t, COUNT(*) AS games, SUM(g.n_players = 10) AS full_scoreboards,
            SUM(g.flags LIKE '%kills_mismatch%') AS kills_mismatch, SUM(g.flags LIKE '%order_conflict%') AS order_conflict,
            SUM(g.sid IS NULL) AS no_series, MIN(g.start_utc) AS first, MAX(g.start_utc) AS last
            FROM c_games g WHERE g.remake = 0 GROUP BY g.tournament_page"""):
        d = dict(r)
        tn = tours.get(d["t"]) or {}
        d.update(league=tn.get("League"), region=tn.get("Region"), level=tn.get("TournamentLevel"), official=tn.get("IsOfficial"),
                 name=tn.get("Name"), tournament_row=bool(tn))
        by_t[d.pop("t")] = d
    for r in con.execute("SELECT tournament_page, COUNT(*), SUM(status = 'complete'), SUM(n_games = 0 AND status = 'complete'), "
                         "SUM(flags LIKE '%no_schedule_row%') FROM c_series GROUP BY tournament_page"):
        d = by_t.setdefault(r[0], {"games": 0})
        d.update(series=r[1], series_complete=r[2] or 0, complete_series_without_games=r[3] or 0, series_without_schedule=r[4] or 0)
    stat_rows = con.execute("SELECT COUNT(*), " + ", ".join(f"SUM({c} IS NOT NULL)" for c in STATS) + " FROM c_player_games").fetchone()
    n_pg = stat_rows[0] or 0
    by_stat = {c: {"present": stat_rows[i + 1] or 0, "share": round((stat_rows[i + 1] or 0) / n_pg, 4) if n_pg else None}
               for i, c in enumerate(STATS)}
    by_stat_t = defaultdict(dict)
    for r in con.execute("SELECT tournament_page, COUNT(*), SUM(kills IS NOT NULL), SUM(assists IS NOT NULL), SUM(deaths IS NOT NULL), "
                         "SUM(cs IS NOT NULL), SUM(role IS NOT NULL) FROM c_player_games GROUP BY tournament_page"):
        by_t.setdefault(r[0], {})["stats"] = {"player_rows": r[1], "kills": r[2], "assists": r[3], "deaths": r[4], "cs": r[5], "role": r[6]}
    by_patch = {r[0] or "unknown": {"games": r[1], "first": r[2], "last": r[3]} for r in con.execute(
        "SELECT patch, COUNT(*), MIN(start_utc), MAX(start_utc) FROM c_games WHERE remake = 0 GROUP BY patch ORDER BY MIN(start_utc)")}
    # gaps: per league, stretches > 21 days without a game inside its covered span
    days = defaultdict(set)
    for t, st in con.execute("SELECT tournament_page, start_utc FROM c_games WHERE remake = 0 AND start_utc IS NOT NULL"):
        lg = (tours.get(t) or {}).get("League") or t
        days[lg].add(st[:10])
    gaps = {}
    for lg, ds in days.items():
        ds = sorted(datetime.strptime(d, "%Y-%m-%d") for d in ds)
        g = [(a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d"), (b - a).days) for a, b in zip(ds, ds[1:]) if (b - a).days > 21]
        if g and len(ds) >= 10:
            gaps[lg] = g
    q = {f"{r[0]}:{r[1]}": r[2] for r in con.execute(
        "SELECT kind, reason, COUNT(*) FROM quarantine WHERE resolved_at IS NULL GROUP BY kind, reason")}
    from . import markets
    return {"computed_at": store.now_iso(), "seconds": None if t0 is None else round(time.time() - t0, 1),
            "games": {"first": span[0], "last": span[1], "count": span[2]}, "player_rows": n_pg,
            "by_competition": dict(sorted(by_t.items(), key=lambda kv: -(kv[1].get("games") or 0))),
            "by_patch": by_patch, "by_stat": by_stat, "gaps_over_21_days": gaps, "quarantine": q,
            "markets": markets.status(con), "history_export": store.get_meta(con, "last_export"),
            "series_by_source": {r[0] or "unknown": {"series": r[1], "complete": r[2] or 0} for r in con.execute(
                "SELECT source, COUNT(*), SUM(status = 'complete') FROM c_series GROUP BY source")},
            "games_winner_unresolved": con.execute("SELECT COUNT(*) FROM c_games WHERE remake = 0 AND winner_tid IS NULL").fetchone()[0],
            "other_sources": {"thunderpick": "LoL coverage not verified - not used", "polymarket": "not verified - not used",
                              "kalshi": "not verified - not used"}}


def refresh(con):
    out = compute(con)
    store.set_meta(con, "coverage", out)
    con.commit()
    return out


def cached(con):
    return store.get_meta(con, "coverage") or {"note": "no coverage report yet (built after the first canonical build)"}
