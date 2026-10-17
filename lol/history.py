"""LoL game history - the database the sync writes and the scout reads (the HLTV way: a grade never waits on a source).

SQLite at config.HISTORY_DB by default; PostgreSQL when LOL_DB_DSN is set (e.g. a database the scheduled sync and the
web app share). Every table is lol_* so it can live next to the HLTV scraper's tables without touching them.

  lol_games         one row per game: source, match, game number, best-of, start, league, sides, kills, winner, length
  lol_player_games  one row per player per game: K / D / A, CS, gold, champion, role, team and opponent kills, result
  lol_sync_state    small key -> value notes (last sync per source)

Rows carry their source ('le' = LoL Esports feed, 'lp' = Leaguepedia) and keys are source-prefixed ('le:<game id>'),
so the two sources never overwrite each other; reads drop a game that both sources hold (lp kept - exact game length
and official winner)."""
import sqlite3
import threading
from datetime import datetime, timezone

from . import config, teams

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS lol_games (
        game_key TEXT PRIMARY KEY, source TEXT NOT NULL, match_key TEXT, game_number INTEGER, best_of INTEGER,
        played_at TEXT, league TEXT, tournament TEXT,
        blue_team TEXT, red_team TEXT, blue_team_id TEXT, red_team_id TEXT, blue_kills INTEGER, red_kills INTEGER,
        winner TEXT, winner_basis TEXT, duration_s REAL, patch TEXT, fetched_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS lol_player_games (
        game_key TEXT NOT NULL, player_key TEXT NOT NULL, player TEXT, player_id TEXT,
        team TEXT, team_id TEXT, opponent TEXT, opponent_id TEXT, side TEXT, role TEXT, champion TEXT,
        kills INTEGER, deaths INTEGER, assists INTEGER, cs INTEGER, gold INTEGER,
        team_kills INTEGER, opp_kills INTEGER, win INTEGER, duration_s REAL,
        match_key TEXT, game_number INTEGER, best_of INTEGER, played_at TEXT, league TEXT, source TEXT,
        PRIMARY KEY (game_key, player_key))""",
    "CREATE INDEX IF NOT EXISTS lol_pg_player ON lol_player_games (player_key, played_at)",
    "CREATE INDEX IF NOT EXISTS lol_pg_match ON lol_player_games (match_key)",
    "CREATE INDEX IF NOT EXISTS lol_g_match ON lol_games (match_key)",
    "CREATE TABLE IF NOT EXISTS lol_sync_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)",
    # completed games a source could not deliver in full (e.g. the feed never reached its final frame) - never dropped
    # silently: `status` lists them until a source fills them
    """CREATE TABLE IF NOT EXISTS lol_missing (
        game_key TEXT PRIMARY KEY, source TEXT, match_key TEXT, game_number INTEGER, league TEXT, played_at TEXT,
        teams TEXT, reason TEXT, noted_at TEXT)""",
]
MISSING_COLS = ("game_key", "source", "match_key", "game_number", "league", "played_at", "teams", "reason", "noted_at")
# the feed's internal champion ids that differ from the names Leaguepedia uses (after teams.norm)
CHAMPIONS = {"monkeyking": "wukong", "nunuwillump": "nunu", "renataglasc": "renata"}
GAME_COLS = ("game_key", "source", "match_key", "game_number", "best_of", "played_at", "league", "tournament",
             "blue_team", "red_team", "blue_team_id", "red_team_id", "blue_kills", "red_kills", "winner", "winner_basis",
             "duration_s", "patch", "fetched_at")
PLAYER_COLS = ("game_key", "player_key", "player", "player_id", "team", "team_id", "opponent", "opponent_id", "side",
               "role", "champion", "kills", "deaths", "assists", "cs", "gold", "team_kills", "opp_kills", "win",
               "duration_s", "match_key", "game_number", "best_of", "played_at", "league", "source")


def player_key(name):
    return teams.norm(name)


class DB:
    """A thin portable connection: `?` placeholders on both engines, rows as dicts."""

    def __init__(self, dsn=None, path=None):
        self.dsn = dsn if dsn is not None else config.db_dsn()
        if self.dsn:
            import psycopg2
            self.kind = "pg"
            self.con = psycopg2.connect(self.dsn)
        else:
            self.kind = "sqlite"
            p = path or config.HISTORY_DB
            p.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(p, timeout=30, check_same_thread=False)
            self.con.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()
        for stmt in SCHEMA:
            self.execute(stmt)
        self.commit()

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.kind == "pg" else sql

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.con.cursor()
            cur.execute(self._sql(sql), tuple(args))
            return cur

    def executemany(self, sql, rows):
        with self.lock:
            cur = self.con.cursor()
            cur.executemany(self._sql(sql), [tuple(r) for r in rows])
            return cur

    def upsert_many(self, table, cols, key, rows):
        """Insert-or-update many rows in as few round trips as possible (PostgreSQL: execute_values, 500 rows a
        statement - a remote database is ~20 ms a round trip, so row-by-row writes of a season would take hours).
        Rows repeating a key keep the last one: one statement may not touch a row twice."""
        rows = list({tuple(r[cols.index(k)] for k in key): tuple(r) for r in rows}.values())
        if not rows:
            return
        if self.kind != "pg":
            self.executemany(_upsert(table, cols, key), rows)
            return
        from psycopg2.extras import execute_values
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
               f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}")
        with self.lock:
            execute_values(self.con.cursor(), sql, rows, page_size=500)

    def delete_in(self, table, col, values, chunk=500):
        values = list(dict.fromkeys(values))
        for i in range(0, len(values), chunk):
            part = values[i:i + chunk]
            self.execute(f"DELETE FROM {table} WHERE {col} IN ({','.join('?' for _ in part)})", part)

    def query(self, sql, args=()):
        cur = self.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def commit(self):
        with self.lock:
            self.con.commit()

    def close(self):
        try:
            self.con.close()
        except Exception:      # noqa: BLE001 - closing a dead connection is not an error worth raising
            pass


def _upsert(table, cols, key):
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}")


def save_games(db, items):
    """Many games at once - [(game, players)] (dicts keyed like GAME_COLS / PLAYER_COLS) - in a handful of statements.
    Idempotent: re-saving a game replaces it, its player rows included (a corrected or re-cleaned name must not leave
    the old row behind), and clears it from lol_missing."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    games, rows = [], []
    for game, players in items:
        game = dict(game, fetched_at=game.get("fetched_at") or now)
        games.append([game.get(c) for c in GAME_COLS])
        for p in players:
            p = dict(p, player_key=p.get("player_key") or player_key(p.get("player")))
            rows.append([p.get(c) for c in PLAYER_COLS])
    if not games:
        return
    keys = [g[0] for g in games]
    db.upsert_many("lol_games", GAME_COLS, ("game_key",), games)
    db.delete_in("lol_player_games", "game_key", keys)
    db.upsert_many("lol_player_games", PLAYER_COLS, ("game_key", "player_key"), rows)
    db.delete_in("lol_missing", "game_key", keys)


def save_game(db, game, players):
    save_games(db, [(game, players)])


def note_missing(db, game_key, source, match_key, game_number, league, played_at, teams_label, reason):
    db.execute(_upsert("lol_missing", MISSING_COLS, ("game_key",)),
               (game_key, source, match_key, game_number, league, played_at, teams_label, reason,
                datetime.now(timezone.utc).isoformat(timespec="seconds")))


def missing(db):
    """Completed games no source has delivered in full, each marked `filled` when the other source holds that game
    (same game number, both teams, within a day)."""
    out = db.query("SELECT * FROM lol_missing ORDER BY played_at")
    if not out:
        return out
    lp = db.query("SELECT game_number, played_at, blue_team, red_team FROM lol_games WHERE source = 'lp'")
    for m in out:
        names = (m.get("teams") or "").split(" vs ")
        m["filled"] = any(g["game_number"] == m["game_number"] and _near(g["played_at"], m["played_at"])
                          and len(names) == 2 and {teams.key(g["blue_team"]), teams.key(g["red_team"])} ==
                          {teams.key(names[0]), teams.key(names[1])} for g in lp)
    return out


def known_matches(db, source):
    """{match_key: games stored} for one source - the sync skips matches it already holds in full."""
    rows = db.query("SELECT match_key, count(*) AS n FROM lol_games WHERE source = ? GROUP BY match_key", (source,))
    return {r["match_key"]: r["n"] for r in rows}


def set_state(db, key, value):
    db.execute(_upsert("lol_sync_state", ("key", "value", "updated_at"), ("key",)),
               (key, value, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    db.commit()


def get_state(db, key):
    rows = db.query("SELECT value, updated_at FROM lol_sync_state WHERE key = ?", (key,))
    return rows[0] if rows else None


def champion(name):
    c = teams.norm(name)
    return CHAMPIONS.get(c, c)


def _near(a, b, days=1):
    """Two ISO times within `days` UTC days (one game, recorded near midnight by two sources)."""
    try:
        da, db_ = (datetime.fromisoformat(str(x).replace("Z", "+00:00")[:19]).date() for x in (a, b))
    except (TypeError, ValueError):
        return False
    return abs((da - db_).days) <= days


def _dedup(rows, links=None):
    """Drop a game both sources hold - Leaguepedia's row is kept - and give every row `pid`, the player's identity.
    One game = same champion (feed ids mapped to wiki names), K / D / A, team kills and kills conceded, within a day -
    NOT the name: the feed spells some players differently ('BrokenBIade', 'JDGxiaofang'), and a champion is played by
    one player per game, so this already pins the player. Only rows from DIFFERENT sources are ever merged (two games of
    one source never collapse).
    pid: 'lp:<Leaguepedia page>' or 'le:<LoL Esports player id>' - a feed id seen on the same game as a Leaguepedia page
    takes that page's identity. Two pros who share a name never share a pid."""
    seen, out, link, twin_games = {}, [], {}, set()
    for r in sorted(rows, key=lambda r: (r.get("source") != "lp", r.get("played_at") or "")):
        k = (champion(r.get("champion")), r.get("kills"), r.get("deaths"), r.get("assists"),
             r.get("team_kills"), r.get("opp_kills"))
        twin = next((s for s in seen.get(k, ()) if s.get("source") != r.get("source")
                     and _near(s.get("played_at"), r.get("played_at"))), None)
        if twin is not None:
            if r.get("source") == "le" and twin.get("source") == "lp" and r.get("player_id"):
                link.setdefault(f"le:{r['player_id']}", f"lp:{twin.get('player_id')}")
            twin_games.add(r.get("game_key"))
            continue
        seen.setdefault(k, []).append(r)
        out.append(r)
    # a game the other source holds goes as a WHOLE: a row of it that found no twin (a name the sources spell apart)
    # would otherwise count that game twice for that player, his team and the Elo
    out = [r for r in out if r.get("game_key") not in twin_games]
    for r in out:
        own = f"{r.get('source')}:{r.get('player_id') or r.get('player_key')}"
        r["pid"] = link.get(own, own)
    if links is not None:
        links.update(link)
    return sorted(out, key=lambda r: (r.get("played_at") or "", r.get("game_number") or 0))


def _rows(db, where, args, before=None):
    sql = f"SELECT * FROM lol_player_games WHERE {where}"
    if before:
        sql += " AND played_at < ?"
        args = list(args) + [before]
    return db.query(sql, args)


def player_games(db, key, before=None, pid=None):
    """Every stored game of one player, oldest first. By name (player_key); with `pid` (from find_players) narrowed to
    that one identity AND widened to every name that identity was stored under - the feed sends one player as
    'BrokenBlade' and 'BrokenBIade', 'BFXClear' and 'Clear', and Leaguepedia keys a renamed player's old games by his
    old name. `before` (ISO date / time) keeps earlier games only."""
    by_name = _rows(db, "player_key = ?", [key], before)
    if pid is None:
        return _dedup(by_name)
    links = {}
    named = _dedup(by_name, links)
    ids = {r["player_id"] for r in named if r["pid"] == pid and r.get("player_id")}
    ids |= {own.split(":", 1)[1] for own, to in links.items() if to == pid}
    if pid.split(":", 1)[1]:
        ids.add(pid.split(":", 1)[1])
    extra = _rows(db, f"player_id IN ({','.join('?' for _ in ids)})", sorted(ids), before) if ids else []
    seen = {(r["game_key"], r["player_key"]) for r in by_name}
    rows = _dedup(by_name + [r for r in extra if (r["game_key"], r["player_key"]) not in seen])
    return [r for r in rows if r["pid"] == pid]


def find_players(db, name):
    """Every player stored under this name, one per identity: [{pid, player_key, player, team, role, games, last}],
    most recently active first (namesakes on other teams are separate entries - the caller picks by team)."""
    by = {}
    for r in _dedup(db.query("SELECT * FROM lol_player_games WHERE player_key = ?", (player_key(name),))):
        e = by.setdefault(r["pid"], {"pid": r["pid"], "player_key": r["player_key"], "games": 0, "last": ""})
        e["games"] += 1
        if (r.get("played_at") or "") >= e["last"]:
            e.update(player=r["player"], team=r["team"], role=r["role"], last=r.get("played_at") or "")
    return sorted(by.values(), key=lambda e: e["last"], reverse=True)


def team_names(db):
    return [r["team"] for r in db.query("SELECT DISTINCT team FROM lol_player_games WHERE team IS NOT NULL")]


def team_games(db, team, before=None):
    """Every stored game a team played (one row per game: the team's and the opponent's kills), oldest first. Team
    names are matched by identity (teams.same_team) across every spelling stored."""
    names = [n for n in team_names(db) if teams.same_team(n, team)]
    if not names:
        return []
    marks = ",".join("?" for _ in names)
    sql = (f"SELECT DISTINCT game_key, match_key, game_number, best_of, played_at, team, opponent, team_kills, opp_kills, "
           f"win, duration_s, league, source FROM lol_player_games WHERE team IN ({marks})")
    args = list(names)
    if before:
        sql += " AND played_at < ?"
        args.append(before)
    rows = db.query(sql, args)
    seen, out = {}, []
    for r in sorted(rows, key=lambda r: (r.get("source") != "lp", r.get("played_at") or "")):
        k = (r["game_number"], r["team_kills"], r["opp_kills"], teams.key(r["opponent"]))   # one game in both sources:
        # Leaguepedia's kept (the opponent too: two different games can share a game number and a scoreline)
        if any(s["source"] != r["source"] and _near(s["played_at"], r["played_at"]) for s in seen.get(k, ())):
            continue
        seen.setdefault(k, []).append(r)
        out.append(r)
    return sorted(out, key=lambda r: (r["played_at"] or "", r["game_number"] or 0))


def all_player_games(db, since=None):
    """Every stored player-game (deduplicated across sources) - for the backtest and league averages."""
    sql, args = "SELECT * FROM lol_player_games", []
    if since:
        sql += " WHERE played_at >= ?"
        args.append(since)
    return _dedup(db.query(sql, args))


def summary(db):
    return db.query("SELECT source, league, count(DISTINCT match_key) AS matches, count(*) AS games, "
                    "min(played_at) AS first, max(played_at) AS last FROM lol_games GROUP BY source, league "
                    "ORDER BY source, league")
