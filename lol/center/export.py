"""
The data center's canonical tables -> the ONE LoL game history (lol/history.py: lol_games / lol_player_games), which the
scout, the backtest and the live forecasts read. Nothing else writes those tables any more (the scout's `sync` runs
the data center), so there is one ingestion path and one source of truth.

Each export rewrites the history in one transaction from the canonical tables:
- one row per finished game (remakes left out) whose scoreboard passes the checks (10 players, player kills = team
  kills); a game failing them is left out and counted
- ids are the data center's internal ids: game_key = lol:game:N, match_key = lol:series:N, player_id = lol:player:N,
  team ids = lol:team:N - so a player's history follows the player across renames and across both sources
- names are the canonical labels (team: Leaguepedia page or LoL Esports name; player: the name shown in that game)
- league = the LoL Esports league slug ("lck", "worlds") or the Leaguepedia league name; winner None when unresolved
"""
import json
import time
from collections import Counter

from . import store

ROLE = {"Top": "top", "Jungle": "jungle", "Mid": "mid", "Bot": "bot", "Support": "support"}
EXCLUDE_FLAGS = ("kills_mismatch", "players_incomplete")


def _labels(con):
    lab = {r[0]: r[1] for r in con.execute("SELECT eid, label FROM entities")}
    return lambda eid: lab.get(store.canonical_id(con, eid)) if eid else None


def _league(con):
    tours = {r["OverviewPage"]: r for r in store.current_rows(con, "Tournaments") if r.get("OverviewPage")}

    def f(series_row):
        tp = series_row["tournament_page"] or ""
        if tp.startswith("LE:"):
            return tp[3:], series_row["tournament"]
        t = tours.get(tp) or {}
        return (t.get("League") or tp), (t.get("Name") or tp)
    return f


def rows(con):
    """(game rows, player rows, counts) in the history's column shapes."""
    from .. import history
    label = _labels(con)
    league_of = _league(con)
    series = {r["sid"]: dict(r) for r in con.execute("SELECT * FROM c_series")}
    counts = Counter()
    games, players = [], []
    pg_by = {}
    for r in con.execute("SELECT * FROM c_player_games ORDER BY gid"):
        pg_by.setdefault(r["gid"], []).append(dict(r))
    for g in con.execute("SELECT * FROM c_games ORDER BY start_utc"):
        g = dict(g)
        flags = json.loads(g.get("flags") or "[]")
        if g["remake"]:
            counts["left_out_remake"] += 1
            continue
        bad = [f for f in EXCLUDE_FLAGS if f in flags]
        s = series.get(g["sid"]) or {}
        if bad or not s:
            counts["left_out_" + (bad[0] if bad else "no_series")] += 1
            continue
        if "game_order_invalid" in (s.get("flags") or ""):
            counts["left_out_game_order_invalid"] += 1
            continue
        lg, tour = league_of(s)
        src = "lp" if (g.get("source") or s.get("source")) == "leaguepedia" else "le"
        win = label(g["winner_tid"])
        games.append({"game_key": g["gid"], "source": src, "match_key": g["sid"], "game_number": g["game_n"],
                      "best_of": s.get("best_of"), "played_at": g["start_utc"], "league": lg, "tournament": tour,
                      "blue_team": label(g["team1"]), "red_team": label(g["team2"]), "blue_team_id": g["team1"],
                      "red_team_id": g["team2"], "blue_kills": g["team1_kills"], "red_kills": g["team2_kills"],
                      "winner": win, "winner_basis": ("Leaguepedia" if src == "lp" else "LoL Esports") if win else "unresolved",
                      "duration_s": g["length_s"], "patch": g["patch"]})
        if not win:
            counts["games_winner_unresolved"] += 1
        kills = {g["team1"]: g["team1_kills"], g["team2"]: g["team2_kills"]}
        for p in pg_by.get(g["gid"], []):
            pid = store.canonical_id(con, p["pid"])
            players.append({"game_key": g["gid"], "player_key": history.player_key(p["name_shown"] or label(pid)),
                            "player": p["name_shown"] or label(pid), "player_id": pid, "team": label(p["tid"]),
                            "team_id": store.canonical_id(con, p["tid"]), "opponent": label(p["opp_tid"]),
                            "opponent_id": store.canonical_id(con, p["opp_tid"]), "side": (p["side"] or "").lower() or None,
                            "role": ROLE.get(p["role"]), "champion": p["champion"], "kills": p["kills"], "deaths": p["deaths"],
                            "assists": p["assists"], "cs": p["cs"], "gold": p["gold"], "team_kills": p["team_kills"],
                            "opp_kills": kills.get(p["opp_tid"]), "win": p["win"], "duration_s": g["length_s"],
                            "match_key": g["sid"], "game_number": g["game_n"], "best_of": s.get("best_of"),
                            "played_at": g["start_utc"], "league": lg, "source": src})
        counts["games"] += 1
    counts["player_rows"] = len(players)
    return games, players, dict(counts)


def _mirror_pg(dsn, games, players):
    """The same rewrite in PostgreSQL (lol-scout-db), one transaction; read back the row counts to prove the write."""
    import psycopg2
    from psycopg2.extras import execute_values
    from .. import history
    con = psycopg2.connect(dsn, connect_timeout=20)
    try:
        cur = con.cursor()
        for stmt in history.SCHEMA:
            cur.execute(stmt)
        cur.execute("DELETE FROM lol_player_games")
        cur.execute("DELETE FROM lol_games")
        now = store.now_iso()
        execute_values(cur, f"INSERT INTO lol_games ({', '.join(history.GAME_COLS)}) VALUES %s",
                       [tuple(dict(g, fetched_at=now).get(c) for c in history.GAME_COLS) for g in games], page_size=2000)
        execute_values(cur, f"INSERT INTO lol_player_games ({', '.join(history.PLAYER_COLS)}) VALUES %s",
                       [tuple(p.get(c) for c in history.PLAYER_COLS) for p in players], page_size=2000)
        con.commit()
        cur.execute("SELECT (SELECT COUNT(*) FROM lol_games), (SELECT COUNT(*) FROM lol_player_games)")
        g, p = cur.fetchone()
        return {"ok": g == len(games) and p == len(players), "games": g, "player_rows": p}
    finally:
        con.close()


def export(con, db=None):
    """Rewrite the history from the canonical tables (one transaction): the local SQLite file (read by the forecasts and
    the admin export) and, when LOL_DB_DSN is set, the same rewrite in PostgreSQL. Returns counts incl. read-backs."""
    import os
    from .. import history
    t0 = time.time()
    games, players, counts = rows(con)
    own = db is None
    db = db or history.DB(dsn="")
    try:
        with db.lock:
            cur = db.con.cursor()
            cur.execute("DELETE FROM lol_player_games")
            cur.execute("DELETE FROM lol_games")
            gcols, pcols = history.GAME_COLS, history.PLAYER_COLS
            now = store.now_iso()
            cur.executemany(db._sql(f"INSERT INTO lol_games ({', '.join(gcols)}) VALUES ({', '.join('?' * len(gcols))})"),
                            [tuple(dict(g, fetched_at=now).get(c) for c in gcols) for g in games])
            cur.executemany(db._sql(f"INSERT INTO lol_player_games ({', '.join(pcols)}) VALUES ({', '.join('?' * len(pcols))})"),
                            [tuple(p.get(c) for c in pcols) for p in players])
            db.con.commit()
        history.set_state(db, "center:export", json.dumps(dict(counts, at=store.now_iso())))
    finally:
        if own:
            db.close()
    dsn = os.environ.get("LOL_DB_DSN")
    if dsn and own:
        try:
            counts["postgres"] = _mirror_pg(dsn, games, players)
        except Exception as e:                          # the local history stands; the mirror failure is reported
            counts["postgres"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    else:
        counts["postgres"] = {"ok": None, "note": "LOL_DB_DSN not set on this service"}
    counts["seconds"] = round(time.time() - t0, 1)
    store.set_meta(con, "last_export", counts)
    con.commit()
    return counts
