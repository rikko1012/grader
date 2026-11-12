"""Sports game storage - every completed game's box score and play-by-play, and every pre-game market and context
snapshot, kept so factors are computed from our own copy (never from a live call at board time).

Backend: SQLite at <SPORTS_DATA_DIR>/games.db, or PostgreSQL when SPORTS_DB_DSN is set (the same DSN as the books
store; every table here is named sports_game_*). Times are UTC ISO strings 'YYYY-MM-DDTHH:MM:SSZ'.

  sports_game_games          one row per ESPN game: teams, score, periods, venue, indoor / grass, attendance, officials,
                             ended_at (the last play's wall-clock time: a result counts as known only after it)
  sports_game_athletes       athlete id -> name, position, current team (team rosters + box scores)
  sports_game_teams          per game and team: every ESPN team box-score stat (numbers, "a-b" pairs split)
  sports_game_players        per game and athlete: position, starter, played / did-not-play + reason, every box stat
  sports_game_plays_z        play-by-play, ONE compressed row per game (plays()): same columns and values as the
                             row table below, ~7x smaller with no per-play index; the row table holds only games not
                             yet packed (pack_plays moves them, row for row - nothing is dropped)
  sports_game_plays          play-by-play: clock, type, text, score, home win probability; basketball shot location and
                             points attempted; football down / distance / yards-to-end-zone before and after, yards,
                             yards after catch, penalty, turnover, drive, offense / defense; participants with roles
  sports_game_derived        per game: what only play-by-play holds (derive.py) - shot zones, on/off, live misses on court,
                             target depth / direction, red zone, success, EPA, neutral pace and pass rate
  sports_game_snapshots      IMMUTABLE pre-game context, a row only when it changed: ESPN pickcenter (the odds ESPN
                             shows, with its provider named), ESPN predictor, injury report
  sports_game_market_lines   IMMUTABLE Kalshi game-market history (winner / spread / total / team total / halves /
                             quarters ...): a row when a market's bid / ask / status changes
  sports_game_prop_lines     IMMUTABLE ESPN-supplied DraftKings player-prop LINES (reported prices have no side identity):
                             a row when a prop's current or opening line changes
  sports_game_line_state     per market / prop: its latest row and when it was last SEEN (staleness)
  sports_game_fetches        every collection request: kind, league, game, HTTP status, error, rows
  sports_game_settings       collector bookkeeping (last update times)

Line correctness (sports/LINE_CORRECTNESS.md, same rules as the canonical store) for snapshots / market / prop lines:
  - a row is written only when the content differs from the CURRENT row (sports_game_line_state.last_id): A -> B -> A
    = three rows, the last current; A -> A moves only last_seen_at (MAX) and last_provider_updated_at (MAX)
  - change content excludes the source's own clock (prop provider_updated_at is freshness, not a line change)
  - a delayed response (older provider_updated_at when both have one and they differ, else observed before the
    state's last_seen_at) is kept with late = 1 and never becomes current; readers (factors.py) skip late rows
  - each save is one transaction under the write lock; additive migrations only
"""
import base64
import hashlib
import json
import os
import zlib

from .. import config
from ..books.store import DB, dt, iso  # noqa: F401  (dt / iso re-exported for the games package)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sports_game_games (
  league TEXT NOT NULL, event_id TEXT NOT NULL, season INTEGER, season_type INTEGER, week INTEGER, start TEXT,
  state TEXT, completed INTEGER, home_id TEXT, away_id TEXT, home_abbr TEXT, away_abbr TEXT, home_score INTEGER,
  away_score INTEGER, periods INTEGER, neutral INTEGER, venue_id TEXT, venue TEXT, city TEXT, region TEXT,
  country TEXT, indoor INTEGER, grass INTEGER, attendance INTEGER, officials TEXT, linescores TEXT, box_source TEXT,
  pbp_source TEXT, n_plays INTEGER, participants INTEGER, fetched_at TEXT, ended_at TEXT,
  PRIMARY KEY (league, event_id));
CREATE INDEX IF NOT EXISTS sports_game_games_start ON sports_game_games (league, start);
CREATE TABLE IF NOT EXISTS sports_game_athletes (
  league TEXT NOT NULL, athlete_id TEXT NOT NULL, player TEXT, player_key TEXT, position TEXT, team_id TEXT,
  updated_at TEXT, PRIMARY KEY (league, athlete_id));
CREATE TABLE IF NOT EXISTS sports_game_teams (
  league TEXT NOT NULL, event_id TEXT NOT NULL, team_id TEXT NOT NULL, home INTEGER, opp_id TEXT, abbr TEXT,
  score INTEGER, stats TEXT, PRIMARY KEY (league, event_id, team_id));
CREATE TABLE IF NOT EXISTS sports_game_players (
  league TEXT NOT NULL, event_id TEXT NOT NULL, athlete_id TEXT NOT NULL, player TEXT, player_key TEXT,
  team_id TEXT, opp_id TEXT, home INTEGER, position TEXT, jersey TEXT, starter INTEGER, played INTEGER,
  dnp_reason TEXT, ejected INTEGER, stats TEXT, PRIMARY KEY (league, event_id, athlete_id));
CREATE INDEX IF NOT EXISTS sports_game_players_ath ON sports_game_players (league, athlete_id);
CREATE INDEX IF NOT EXISTS sports_game_players_key ON sports_game_players (league, player_key);
CREATE INDEX IF NOT EXISTS sports_game_players_team ON sports_game_players (league, team_id);
CREATE TABLE IF NOT EXISTS sports_game_plays (
  league TEXT NOT NULL, event_id TEXT NOT NULL, seq INTEGER NOT NULL, play_id TEXT, period INTEGER, clock_s {REAL},
  team_id TEXT, type_id TEXT, type TEXT, text TEXT, scoring INTEGER, score_value INTEGER, home_score INTEGER,
  away_score INTEGER, wallclock TEXT, home_wp {REAL}, x {REAL}, y {REAL}, shooting INTEGER, points_attempted INTEGER,
  down INTEGER, distance INTEGER, yte INTEGER, end_down INTEGER, end_distance INTEGER, end_yte INTEGER,
  yards INTEGER, yac INTEGER, penalty INTEGER, turnover INTEGER, scoring_type TEXT, drive_id TEXT, offense_id TEXT,
  defense_id TEXT, athletes TEXT, PRIMARY KEY (league, event_id, seq));
CREATE TABLE IF NOT EXISTS sports_game_derived (
  league TEXT NOT NULL, event_id TEXT NOT NULL, level TEXT NOT NULL, entity_id TEXT NOT NULL, team_id TEXT,
  stats TEXT, version TEXT, PRIMARY KEY (league, event_id, level, entity_id));
CREATE TABLE IF NOT EXISTS sports_game_snapshots (
  id {ID}, league TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT, observed_at TEXT NOT NULL,
  digest TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS sports_game_snapshots_ev ON sports_game_snapshots (league, event_id, kind, id);
CREATE TABLE IF NOT EXISTS sports_game_market_lines (
  id {ID}, observed_at TEXT NOT NULL, source TEXT NOT NULL, league TEXT NOT NULL, event_id TEXT,
  provider_event_id TEXT, series TEXT, market TEXT, period TEXT, team_id TEXT, team_code TEXT, strike {REAL},
  strike_type TEXT, title TEXT, bid {REAL}, ask {REAL}, last {REAL}, volume {REAL}, open_interest {REAL},
  status TEXT, provider_market_id TEXT NOT NULL, close_time TEXT);
CREATE INDEX IF NOT EXISTS sports_game_market_ev ON sports_game_market_lines (league, event_id, market, id);
CREATE TABLE IF NOT EXISTS sports_game_prop_lines (
  id {ID}, observed_at TEXT NOT NULL, source TEXT NOT NULL, book TEXT NOT NULL, provenance TEXT NOT NULL,
  league TEXT NOT NULL, event_id TEXT NOT NULL, prop_key TEXT NOT NULL, athlete_id TEXT, player TEXT,
  player_key TEXT, team_id TEXT, market TEXT, period TEXT, kind TEXT, raw_type_id TEXT, raw_type TEXT,
  line {REAL}, line_open {REAL}, price_over INTEGER, price_under INTEGER, provider_updated_at TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS sports_game_prop_ev ON sports_game_prop_lines (league, event_id, player_key, market, id);
CREATE TABLE IF NOT EXISTS sports_game_line_state (
  key TEXT PRIMARY KEY, tbl TEXT NOT NULL, last_id INTEGER NOT NULL, digest TEXT, first_seen_at TEXT,
  last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS sports_game_fetches (
  id {ID}, kind TEXT NOT NULL, league TEXT, event_id TEXT, started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER,
  http_status INTEGER, error TEXT, n_rows INTEGER);
CREATE TABLE IF NOT EXISTS sports_game_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS sports_game_plays_z (
  league TEXT NOT NULL, event_id TEXT NOT NULL, n INTEGER NOT NULL, cols TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (league, event_id));
"""

GAME_COLS = ("league", "event_id", "season", "season_type", "week", "start", "state", "completed", "home_id",
             "away_id", "home_abbr", "away_abbr", "home_score", "away_score", "periods", "neutral", "venue_id",
             "venue", "city", "region", "country", "indoor", "grass", "attendance", "officials", "linescores",
             "box_source", "pbp_source", "n_plays", "participants", "fetched_at", "ended_at")
TEAM_COLS = ("league", "event_id", "team_id", "home", "opp_id", "abbr", "score", "stats")
PLAYER_COLS = ("league", "event_id", "athlete_id", "player", "player_key", "team_id", "opp_id", "home", "position",
               "jersey", "starter", "played", "dnp_reason", "ejected", "stats")
PLAY_COLS = ("league", "event_id", "seq", "play_id", "period", "clock_s", "team_id", "type_id", "type", "text",
             "scoring", "score_value", "home_score", "away_score", "wallclock", "home_wp", "x", "y", "shooting",
             "points_attempted", "down", "distance", "yte", "end_down", "end_distance", "end_yte", "yards", "yac",
             "penalty", "turnover", "scoring_type", "drive_id", "offense_id", "defense_id", "athletes")
MARKET_COLS = ("observed_at", "source", "league", "event_id", "provider_event_id", "series", "market", "period",
               "team_id", "team_code", "strike", "strike_type", "title", "bid", "ask", "last", "volume",
               "open_interest", "status", "provider_market_id", "close_time")
PROP_COLS = ("observed_at", "source", "book", "provenance", "league", "event_id", "prop_key", "athlete_id", "player",
             "player_key", "team_id", "market", "period", "kind", "raw_type_id", "raw_type", "line", "line_open",
             "price_over", "price_under", "provider_updated_at", "status", "reported_prices")


def connect(path=None, dsn=None):
    dsn = dsn if dsn is not None else os.environ.get("SPORTS_DB_DSN") or None
    db = DB(path=path or (config.DATA_DIR / "games.db"), dsn=dsn, schema=SCHEMA)
    _migrate(db)
    return db


MIGRATIONS = (
    ("sports_game_prop_lines", "reported_prices", "TEXT"),
    ("sports_game_games", "ended_at", "TEXT"),
    ("sports_game_snapshots", "late", "INTEGER NOT NULL DEFAULT 0"),
    ("sports_game_market_lines", "late", "INTEGER NOT NULL DEFAULT 0"),
    ("sports_game_prop_lines", "late", "INTEGER NOT NULL DEFAULT 0"),
    ("sports_game_line_state", "last_provider_updated_at", "TEXT"),
)


def _migrate(db):
    """Columns added after a store was first created (additive only)."""
    from ..books.store import migrate
    migrate(db, MIGRATIONS)
    fill_ended_at(db)


def fill_ended_at(db):
    """A game's end = its last play's wall-clock time (ESPN), filled where missing (row-table games; a packed game
    is saved with its ended_at from espn_game.parse)."""
    db.execute("UPDATE sports_game_games SET ended_at = (SELECT MAX(p.wallclock) FROM sports_game_plays p WHERE "
               "p.league = sports_game_games.league AND p.event_id = sports_game_games.event_id) WHERE ended_at IS "
               "NULL AND completed = 1")
    db.commit()


def _ins(cols):
    return f"({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"


def _j(v):
    return json.dumps(v, separators=(",", ":"), sort_keys=True) if isinstance(v, (dict, list)) else v


def has_game(db, league, event_id, complete=True):
    r = db.one("SELECT completed, n_plays FROM sports_game_games WHERE league = ? AND event_id = ?", (league, event_id))
    return bool(r) and (not complete or bool(r["completed"]))


def save_game(db, g):
    """Replace one game's box score and play-by-play (a re-read of a final game gives the same rows; a correction by
    ESPN replaces them). g = espn_game.parse() output."""
    lg, ev = g["game"]["league"], g["game"]["event_id"]
    for tbl in ("sports_game_teams", "sports_game_players", "sports_game_plays", "sports_game_plays_z"):
        db.execute(f"DELETE FROM {tbl} WHERE league = ? AND event_id = ?", (lg, ev))
    db.execute("DELETE FROM sports_game_games WHERE league = ? AND event_id = ?", (lg, ev))
    db.execute("INSERT INTO sports_game_games " + _ins(GAME_COLS), [_j(g["game"].get(c)) for c in GAME_COLS])
    for t in g["teams"]:
        db.execute("INSERT INTO sports_game_teams " + _ins(TEAM_COLS), [_j(t.get(c)) for c in TEAM_COLS])
    for p in g["players"]:
        db.execute("INSERT INTO sports_game_players " + _ins(PLAYER_COLS), [_j(p.get(c)) for c in PLAYER_COLS])
    _put_plays(db, lg, ev, [[_j(p.get(c)) for c in PLAY_COLS[2:]] for p in g["plays"]])
    db.commit()


# ---------------------------------------------------------------------------------------- packed play-by-play
PLAY_PACK_COLS = PLAY_COLS[2:]                  # league / event_id are the row key


def _put_plays(db, league, event_id, rows):
    """One game's plays (lists in PLAY_PACK_COLS order, seq ascending) as one zlib + base64 row. Lossless: the values
    go through JSON exactly as stored (ints, floats, text, NULL); cols names the order, so a later column addition
    still reads old rows."""
    rows = sorted(rows, key=lambda r: r[0])
    data = base64.b64encode(zlib.compress(json.dumps(rows, separators=(",", ":")).encode("utf-8"), 9)).decode("ascii")
    db.execute("INSERT INTO sports_game_plays_z (league, event_id, n, cols, data) VALUES (?,?,?,?,?)",
               (league, event_id, len(rows), ",".join(PLAY_PACK_COLS), data))


def plays(db, league, event_id):
    """One game's plays in seq order, as dicts with every PLAY_COLS key - the same rows as
    SELECT * FROM sports_game_plays ... ORDER BY seq, whether the game is packed or still in the row table."""
    r = db.one("SELECT cols, data FROM sports_game_plays_z WHERE league = ? AND event_id = ?", (league, event_id))
    if r:
        cols = r["cols"].split(",")
        return [dict({"league": league, "event_id": event_id}, **dict(zip(cols, v)))
                for v in json.loads(zlib.decompress(base64.b64decode(r["data"])))]
    return db.all("SELECT * FROM sports_game_plays WHERE league = ? AND event_id = ? ORDER BY seq",
                  (league, event_id))


def pack_plays(db, limit=None):
    """Move row-table games into the packed table, one game per transaction, verified (the packed copy must read back
    equal to the rows before the rows are deleted). -> games packed."""
    games = db.all("SELECT DISTINCT league, event_id FROM sports_game_plays")
    n = 0
    for g in games[:limit] if limit else games:
        rows = db.all("SELECT * FROM sports_game_plays WHERE league = ? AND event_id = ? ORDER BY seq",
                      (g["league"], g["event_id"]))
        db.execute("DELETE FROM sports_game_plays_z WHERE league = ? AND event_id = ?", (g["league"], g["event_id"]))
        _put_plays(db, g["league"], g["event_id"], [[r[c] for c in PLAY_PACK_COLS] for r in rows])
        back = plays(db, g["league"], g["event_id"])
        if [dict(r) for r in rows] != back:
            db.rollback()
            raise ValueError(f"packed plays of {g['league']} {g['event_id']} do not read back equal")
        db.execute("DELETE FROM sports_game_plays WHERE league = ? AND event_id = ?", (g["league"], g["event_id"]))
        db.commit()
        n += 1
    return n


def save_derived(db, league, event_id, players, teams, version):
    """Replace one game's derived rows. players = {athlete_id: (team_id, stats)}, teams = {team_id: stats}."""
    db.execute("DELETE FROM sports_game_derived WHERE league = ? AND event_id = ?", (league, event_id))
    for aid, (tid, st) in players.items():
        db.execute("INSERT INTO sports_game_derived (league, event_id, level, entity_id, team_id, stats, version) "
                   "VALUES (?,?,?,?,?,?,?)", (league, event_id, "player", aid, tid, _j(st), version))
    for tid, st in teams.items():
        db.execute("INSERT INTO sports_game_derived (league, event_id, level, entity_id, team_id, stats, version) "
                   "VALUES (?,?,?,?,?,?,?)", (league, event_id, "team", tid, tid, _j(st), version))
    db.commit()


def digest(data):
    return hashlib.sha1(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def save_snapshot(db, league, event_id, kind, source, data, now=None):
    """Append a context snapshot when it differs from the CURRENT one of its kind -> True when a current row was
    written (a delayed older read is kept as late history and returns False)."""
    key = f"snap:{league}:{event_id}:{kind}:{source}"
    t = iso(now)
    sql = ("INSERT INTO sports_game_snapshots (league, event_id, kind, source, observed_at, digest, data, late) "
           "VALUES (?,?,?,?,?,?,?,?)")
    db.begin_write(f"games:{key}")
    try:
        out = _save_one(db, key, "sports_game_snapshots", digest(data), None, None, t,
                        lambda late: db.insert(sql, (league, event_id, kind, source, t, digest(data),
                                                     json.dumps(data, default=str), late)))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return out


def _state(db, key, tbl, rid, d, t, src=None):
    db.execute("INSERT INTO sports_game_line_state (key, tbl, last_id, digest, first_seen_at, last_seen_at, "
               "last_provider_updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT (key) DO UPDATE SET "
               "last_id = excluded.last_id, digest = excluded.digest, last_seen_at = excluded.last_seen_at, "
               "last_provider_updated_at = excluded.last_provider_updated_at", (key, tbl, rid, d, t, t, src))


def _later(a, b):
    return max(x for x in (a, b) if x) if (a or b) else None


def _save_one(db, key, tbl, d, legacy_d, src, t, insert):
    """The one current decision for a game-store line (inside the caller's write transaction). -> True when a new
    CURRENT row was written."""
    from ..books.store import is_late
    st = db.one("SELECT last_id, digest, last_seen_at, last_provider_updated_at FROM sports_game_line_state "
                "WHERE key = ?", (key,))
    if st and st["digest"] in (d, legacy_d):          # unchanged (legacy_d: a digest written before the change rule)
        db.execute("UPDATE sports_game_line_state SET last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at < ? "
                   "THEN ? ELSE last_seen_at END, last_provider_updated_at = ?, digest = ? WHERE key = ?",
                   (t, t, _later(st["last_provider_updated_at"], src), d, key))
        return False
    if st and is_late(t, src, st["last_seen_at"], st["last_provider_updated_at"]):
        insert(1)                                     # history, never current
        return False
    rid = insert(0)
    _state(db, key, tbl, rid, d, t, src)
    return True


def _save_lines(db, tbl, cols, rows, keyf, changef, now, srcf=lambda r: None, legacyf=None):
    t, n = iso(now), 0
    sql = f"INSERT INTO {tbl} " + _ins(cols + ("late",))
    db.begin_write(f"games:{tbl}")
    try:
        for r in rows:
            row = dict(r, observed_at=t)
            n += _save_one(db, keyf(r), tbl, digest(changef(r)), digest(legacyf(r)) if legacyf else None, srcf(r), t,
                           lambda late, row=row: db.insert(sql, [row.get(c) for c in cols] + [late]))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return n


def save_market_lines(db, rows, now=None):
    """Kalshi game markets -> new immutable rows (a market whose bid / ask / last / status did not change is only
    marked seen)."""
    return _save_lines(db, "sports_game_market_lines", MARKET_COLS, rows,
                       lambda r: f"mkt:{r['source']}:{r['provider_market_id']}",
                       lambda r: (r.get("bid"), r.get("ask"), r.get("last"), r.get("status"), r.get("event_id"),
                                  r.get("team_id")), now)


def save_prop_lines(db, rows, now=None):
    """ESPN-supplied DraftKings prop lines -> new immutable rows when a prop's line, opening line or reported prices change."""
    return _save_lines(db, "sports_game_prop_lines", PROP_COLS, rows,
                       lambda r: f"prop:{r['source']}:{r['prop_key']}",
                       lambda r: (r.get("line"), r.get("line_open"), r.get("status")) +
                                  ((r["reported_prices"],) if r.get("reported_prices") else ()), now,
                       srcf=lambda r: r.get("provider_updated_at"),
                       legacyf=lambda r: (r.get("line"), r.get("line_open"), r.get("status"),
                                          r.get("provider_updated_at")) +
                                         ((r["reported_prices"],) if r.get("reported_prices") else ()))


def save_athletes(db, league, rows, now=None):
    """rows = [{"athlete_id", "player", "position", "team_id"}] -> upsert (a roster read wins over an older one)."""
    t = iso(now)
    from .. import names
    for r in rows:
        db.execute("INSERT INTO sports_game_athletes (league, athlete_id, player, player_key, position, team_id, "
                   "updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT (league, athlete_id) DO UPDATE SET "
                   "player = excluded.player, player_key = excluded.player_key, "
                   "position = COALESCE(excluded.position, sports_game_athletes.position), "
                   "team_id = COALESCE(excluded.team_id, sports_game_athletes.team_id), updated_at = excluded.updated_at",
                   (league, r["athlete_id"], r.get("player"), names.key(r.get("player")), r.get("position"),
                    r.get("team_id"), t))
    db.commit()


def athletes(db, league):
    return {r["athlete_id"]: r for r in db.all("SELECT * FROM sports_game_athletes WHERE league = ?", (league,))}


def start_fetch(db, kind, league=None, event_id=None, now=None):
    fid = db.insert("INSERT INTO sports_game_fetches (kind, league, event_id, started_at) VALUES (?,?,?,?)",
                    (kind, league, event_id, iso(now)))
    db.commit()
    return fid


def finish_fetch(db, fid, ok, n_rows=0, http_status=None, error=None, now=None):
    db.execute("UPDATE sports_game_fetches SET finished_at = ?, ok = ?, http_status = ?, error = ?, n_rows = ? "
               "WHERE id = ?", (iso(now), int(bool(ok)), http_status, (error or None) and str(error)[:300], n_rows,
                                fid))
    db.commit()


def setting(db, key, default=None):
    r = db.one("SELECT value FROM sports_game_settings WHERE key = ?", (key,))
    return r["value"] if r else default


def set_setting(db, key, value, now=None):
    db.execute("INSERT INTO sports_game_settings (key, value, updated_at) VALUES (?,?,?) ON CONFLICT (key) DO UPDATE "
               "SET value = excluded.value, updated_at = excluded.updated_at", (key, value, iso(now)))
    db.commit()
