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
EXCLUDE_FLAGS = ()        # 2026-10-17: no whole-game exclusion for a team-total field or a missing player row
VERIFIED_WINNER = {"official", "derived_sweep", "derived_last_game"}


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
    """(game rows as dicts, player rows as tuples in history.PLAYER_COLS order, counts). 2026-10-18 memory: the export took
    the LoL worker from ~90 MB to a 532 MB peak (the container reached 2,100 of 2,147 MB) because every player row was held
    three times at once - the grouped source rows, the output dicts and a full tuple list per database write. Source rows
    are now released as they are used, output rows are tuples, and the writes stream them. The rows written are identical."""
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
        verified = g.get("winner_status") in VERIFIED_WINNER and g["winner_tid"]
        win = label(g["winner_tid"]) if verified else None
        counts["winner_" + str(g.get("winner_status") or "unknown")] = counts.get("winner_" + str(g.get("winner_status") or "unknown"), 0) + 1
        ks = g.get("team_kills_status") or "unknown"
        counts["team_kills_" + ("consistent" if ks == "consistent" else "executions" if "execution" in ks else ks.split(";")[0])] = \
            counts.get("team_kills_" + ("consistent" if ks == "consistent" else "executions" if "execution" in ks else ks.split(";")[0]), 0) + 1
        if "players_incomplete" in flags:
            counts["games_with_unresolved_player_rows"] = counts.get("games_with_unresolved_player_rows", 0) + 1
        games.append({"game_key": g["gid"], "source": src, "match_key": g["sid"], "game_number": g["game_n"],
                      "best_of": s.get("best_of"), "played_at": g["start_utc"], "league": lg, "tournament": tour,
                      "blue_team": label(g["team1"]), "red_team": label(g["team2"]), "blue_team_id": g["team1"],
                      "red_team_id": g["team2"], "blue_kills": g["team1_kills"], "red_kills": g["team2_kills"],
                      "winner": win, "winner_basis": g.get("winner_status") or "unknown",
                      "duration_s": g["length_s"], "patch": g["patch"]})
        if not win:
            counts["games_winner_unresolved"] += 1
        kills = {g["team1"]: g["team1_kills"], g["team2"]: g["team2_kills"]}
        keys_used = set()
        for p in pg_by.pop(g["gid"], []):              # released as it is used (2026-10-18 memory)
            pid = store.canonical_id(con, p["pid"])
            # the history's key is (game, name key): a blank name or two players with the same name in one game get the
            # internal id instead (2026-10-17: the export failed on such a game) - lookups by id are unaffected
            pkey = history.player_key(p["name_shown"] or label(pid))
            if not pkey or pkey in keys_used:
                counts["player_key_from_id"] = counts.get("player_key_from_id", 0) + 1
                pkey = f"{pkey}#{pid}" if pkey else pid
            keys_used.add(pkey)
            row = {"game_key": g["gid"], "player_key": pkey,
                   "player": p["name_shown"] or label(pid), "player_id": pid, "team": label(p["tid"]),
                   "team_id": store.canonical_id(con, p["tid"]), "opponent": label(p["opp_tid"]),
                   "opponent_id": store.canonical_id(con, p["opp_tid"]), "side": (p["side"] or "").lower() or None,
                   "role": ROLE.get(p["role"]), "champion": p["champion"], "kills": p["kills"], "deaths": p["deaths"],
                   "assists": p["assists"], "cs": p["cs"], "gold": p["gold"], "team_kills": p["team_kills"],
                   "opp_kills": kills.get(p["opp_tid"]), "win": p["win"] if verified else None, "duration_s": g["length_s"],
                   "match_key": g["sid"], "game_number": g["game_n"], "best_of": s.get("best_of"),
                   "played_at": g["start_utc"], "league": lg, "source": src, "damage": p["damage"],
                   "vision": p["vision"]}
            players.append(tuple(row.get(c) for c in history.PLAYER_COLS))
        counts["games"] += 1
    counts["player_rows"] = len(players)
    return games, players, dict(counts)


def _game_args(games, now):
    from .. import history
    return (tuple(dict(g, fetched_at=now).get(c) for c in history.GAME_COLS) for g in games)


def _pg_args(games, players, now=None):
    """The rows for the two INSERTs, streamed (no second full copy of the history in memory)."""
    return _game_args(games, now or store.now_iso()), iter(players)


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
        for tbl, cols in history.ADDED.items():
            for c, typ in cols.items():
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {c} {typ}")
        # TRUNCATE, not DELETE: a DELETE of every row on each export left ~107k dead rows per export on lol-scout-db's disk
        # until a vacuum (the database kept growing). TRUNCATE frees the old storage at once and is transactional - a
        # failed export rolls back to the previous copy; every export writes the full history, so nothing is lost.
        cur.execute("TRUNCATE lol_player_games, lol_games")
        ga, pa = _pg_args(games, players)
        execute_values(cur, f"INSERT INTO lol_games ({', '.join(history.GAME_COLS)}) VALUES %s", ga, page_size=2000)
        execute_values(cur, f"INSERT INTO lol_player_games ({', '.join(history.PLAYER_COLS)}) VALUES %s", pa, page_size=2000)
        con.commit()
        cur.execute("SELECT (SELECT COUNT(*) FROM lol_games), (SELECT COUNT(*) FROM lol_player_games)")
        g, p = cur.fetchone()
        out = {"ok": g == len(games) and p == len(players), "games": g, "player_rows": p}
        try:                                            # the database's size, so its disk use is visible on the status page
            cur.execute("SELECT pg_database_size(current_database())")
            out["db_mb"] = round(cur.fetchone()[0] / 1e6, 1)
            cur.execute("SELECT relname, pg_total_relation_size(relid), n_dead_tup FROM pg_stat_user_tables "
                        "ORDER BY pg_total_relation_size(relid) DESC LIMIT 6")
            out["tables_mb"] = {r[0]: {"mb": round(r[1] / 1e6, 1), "dead_rows": r[2]} for r in cur.fetchall()}
        except Exception as e:
            con.rollback()
            out["size_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out
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
                            _game_args(games, now))
            cur.executemany(db._sql(f"INSERT INTO lol_player_games ({', '.join(pcols)}) VALUES ({', '.join('?' * len(pcols))})"),
                            iter(players))
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
    counts["at"] = store.now_iso()                      # the data version a forecast records (forecast._data_version)
    store.set_meta(con, "last_export", counts)
    con.commit()
    return counts
