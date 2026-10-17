"""LoL game history - the database the sync writes and the scout reads (the HLTV way: a grade never waits on a source).

SQLite at config.HISTORY_DB by default; PostgreSQL when LOL_DB_DSN is set (e.g. a database the scheduled sync and the
web app share). Every table is lol_* so it can live next to the HLTV scraper's tables without touching them.

  lol_games         one row per game: source, match, game number, best-of, start, league, sides, kills, winner, length
  lol_player_games  one row per player per game: K / D / A, CS, gold, champion, role, team and opponent kills, result
  lol_sync_state    small key -> value notes (last sync per source)

Since 2026-10-17 the history is written ONLY by the data center (lol/center/export.py): one row per real game (the
data center keeps one source per series), keyed by the data center's ids (game_key lol:game:N, player_id lol:player:N,
team ids lol:team:N), each row still naming its source ('lp' Leaguepedia, 'le' LoL Esports). Rows written by the older
direct sync (source-prefixed keys 'le:<game id>') are read the same way: reads drop a game both sources hold."""
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
]
GAME_COLS = ("game_key", "source", "match_key", "game_number", "best_of", "played_at", "league", "tournament",
             "blue_team", "red_team", "blue_team_id", "red_team_id", "blue_kills", "red_kills", "winner", "winner_basis",
             "duration_s", "patch", "fetched_at")
PLAYER_COLS = ("game_key", "player_key", "player", "player_id", "team", "team_id", "opponent", "opponent_id", "side",
               "role", "champion", "kills", "deaths", "assists", "cs", "gold", "team_kills", "opp_kills", "win",
               "duration_s", "match_key", "game_number", "best_of", "played_at", "league", "source", "damage", "vision")
ADDED = {"lol_player_games": {"damage": "INTEGER", "vision": "INTEGER"}}     # 2026-10-17: Leaguepedia-only stats


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
        for tbl, cols in ADDED.items():                     # columns added after a database was created: in place
            have = {r["name"] for r in self.query(f"PRAGMA table_info({tbl})")} if self.kind == "sqlite" else {
                r["column_name"] for r in self.query("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (tbl,))}
            for c, typ in cols.items():
                if c not in have:
                    self.execute(f"ALTER TABLE {tbl} ADD COLUMN {c} {typ}")
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


def save_game(db, game, players):
    """One game and its ten player rows (dicts keyed like GAME_COLS / PLAYER_COLS). Idempotent: re-saving replaces."""
    game = dict(game, fetched_at=game.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.execute(_upsert("lol_games", GAME_COLS, ("game_key",)), [game.get(c) for c in GAME_COLS])
    rows = []
    for p in players:
        p = dict(p, player_key=p.get("player_key") or player_key(p.get("player")))
        rows.append([p.get(c) for c in PLAYER_COLS])
    if rows:
        db.executemany(_upsert("lol_player_games", PLAYER_COLS, ("game_key", "player_key")), rows)


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


def _dedup(rows):
    """Drop a game both sources hold (same name, UTC day, champion and K / D / A - Leaguepedia's row is kept) and give
    every row `pid`, the player's identity: 'lp:<Leaguepedia page>' or 'le:<LoL Esports player id>' - an LoL Esports id
    seen on the same game as a Leaguepedia page takes that page's identity. Two pros who share a name never share a pid."""
    seen, out, link = {}, [], {}
    for r in sorted(rows, key=lambda r: (r.get("source") != "lp", r.get("played_at") or "")):
        k = (r.get("player_key"), str(r.get("played_at") or "")[:10], teams.norm(r.get("champion")),
             r.get("kills"), r.get("deaths"), r.get("assists"))
        if k in seen:
            if r.get("source") == "le" and seen[k].get("source") == "lp" and r.get("player_id"):
                link.setdefault(f"le:{r['player_id']}", f"lp:{seen[k].get('player_id')}")
            continue
        seen[k] = r
        out.append(r)
    for r in out:
        if str(r.get("player_id") or "").startswith("lol:player:"):   # the data center's id: already one per player
            r["pid"] = r["player_id"]
            continue
        own = f"{r.get('source')}:{r.get('player_id') or r.get('player_key')}"
        r["pid"] = link.get(own, own)
    return sorted(out, key=lambda r: (r.get("played_at") or "", r.get("game_number") or 0))


def player_games(db, key, before=None, pid=None):
    """Every stored game of one player, oldest first: by name (player_key), narrowed to one identity with `pid` (from
    find_players). `before` (ISO date / time) keeps earlier games only."""
    if pid and str(pid).startswith("lol:player:"):   # a data-center id: every game of that player, under any name
        sql, args = "SELECT * FROM lol_player_games WHERE player_id = ?", [pid]
    else:
        sql, args = "SELECT * FROM lol_player_games WHERE player_key = ?", [key]
    if before:
        sql += " AND played_at < ?"
        args.append(before)
    rows = _dedup(db.query(sql, args))
    return [r for r in rows if pid is None or r["pid"] == pid]


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


def team_games(db, team, before=None, team_id=None):
    """Every stored game a team played (one row per game: the team's and the opponent's kills), oldest first. With a
    data-center team_id ('lol:team:N') the team is matched by that id; otherwise by name identity (teams.same_team)
    across every spelling stored."""
    if team_id:
        where, args = "team_id = ?", [team_id]
    else:
        names = [n for n in team_names(db) if teams.same_team(n, team)]
        if not names:
            return []
        where, args = f"team IN ({','.join('?' for _ in names)})", list(names)
    sql = (f"SELECT DISTINCT game_key, match_key, game_number, best_of, played_at, team, opponent, team_kills, opp_kills, "
           f"win, duration_s, league, source FROM lol_player_games WHERE {where}")
    if before:
        sql += " AND played_at < ?"
        args.append(before)
    rows = db.query(sql, args)
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r.get("source") != "lp", r.get("played_at") or "")):
        k = (str(r["played_at"] or "")[:10], r["game_number"], r["team_kills"], r["opp_kills"])
        if k in seen:
            continue
        seen.add(k)
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
