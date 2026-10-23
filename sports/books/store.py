"""Sports odds storage - the only place the board, comparison views and models read sportsbook prices from.

Backend: SQLite at <SPORTS_DATA_DIR>/books.db (the server's persistent disk), or PostgreSQL when SPORTS_DB_DSN is set
(needed only if the worker runs as a separate service that cannot share that disk). Every table is named sports_book_*
so a shared database never collides with HLTV / LoL tables. Times are UTC ISO strings 'YYYY-MM-DDTHH:MM:SSZ'.

  sports_book_quotes          IMMUTABLE history: a row when an offer is first seen, when its price or status changes,
                              and a 'removed' row when a complete fetch of its game no longer lists it. Never updated.
  sports_book_offer_state     per offer: its latest quote and when it was last SEEN (observation time - this is what
                              staleness is measured from; a quote unchanged for an hour was still seen a minute ago)
  sports_book_events          provider game -> canonical game (ESPN event id), as listed
  sports_book_fetches         every request batch: provider, scope, times, HTTP status, error, offers, credits
  sports_book_provider_state  health + backoff per provider: cooldown, failure streak, last success / error, credits
  sports_book_schedule        when each provider's game is next due
  sports_book_lease           one worker at a time (an expiring lease, taken atomically)
"""
import os
import sqlite3
from datetime import datetime, timezone

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sports_book_quotes (
  id {ID}, offer_key TEXT NOT NULL, observed_at TEXT NOT NULL, fetch_id INTEGER,
  provider TEXT NOT NULL, book TEXT NOT NULL, jurisdiction TEXT, league TEXT NOT NULL, event_key TEXT,
  provider_event_id TEXT, event_name TEXT, home TEXT, away TEXT, start TEXT,
  player TEXT, player_key TEXT, provider_player_id TEXT, market TEXT, stat_label TEXT, period TEXT, line {REAL},
  side TEXT, market_type TEXT, price_american INTEGER, price_decimal {REAL}, status TEXT,
  provider_market_id TEXT, provider_selection_id TEXT, provider_updated_at TEXT);
CREATE INDEX IF NOT EXISTS sports_book_quotes_offer ON sports_book_quotes (offer_key, id);
CREATE INDEX IF NOT EXISTS sports_book_quotes_prop ON sports_book_quotes (league, event_key, player_key, market);
CREATE TABLE IF NOT EXISTS sports_book_offer_state (
  offer_key TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_event_id TEXT NOT NULL, league TEXT NOT NULL,
  event_key TEXT, player_key TEXT, market TEXT, last_quote_id INTEGER NOT NULL, last_status TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_fetch_id INTEGER);
CREATE INDEX IF NOT EXISTS sports_book_state_scope ON sports_book_offer_state (provider, provider_event_id);
CREATE INDEX IF NOT EXISTS sports_book_state_prop ON sports_book_offer_state (league, event_key, player_key, market);
CREATE TABLE IF NOT EXISTS sports_book_events (
  provider TEXT NOT NULL, provider_event_id TEXT NOT NULL, league TEXT NOT NULL, event_key TEXT, name TEXT,
  home TEXT, away TEXT, start TEXT, first_seen_at TEXT, last_seen_at TEXT,
  PRIMARY KEY (provider, provider_event_id));
CREATE TABLE IF NOT EXISTS sports_book_fetches (
  id {ID}, provider TEXT NOT NULL, league TEXT, scope TEXT NOT NULL, provider_event_id TEXT, event_key TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER, blocked INTEGER, http_status INTEGER, error TEXT,
  requests INTEGER, n_offers INTEGER, n_new_quotes INTEGER, n_removed INTEGER, credits_used INTEGER,
  credits_remaining INTEGER);
CREATE INDEX IF NOT EXISTS sports_book_fetches_provider ON sports_book_fetches (provider, id);
CREATE TABLE IF NOT EXISTS sports_book_provider_state (
  provider TEXT PRIMARY KEY, cooldown_until TEXT, failures INTEGER NOT NULL DEFAULT 0, last_ok_at TEXT,
  last_error TEXT, last_error_at TEXT, blocked INTEGER NOT NULL DEFAULT 0, credits_remaining INTEGER,
  credits_checked_at TEXT);
CREATE TABLE IF NOT EXISTS sports_book_schedule (
  provider TEXT NOT NULL, league TEXT NOT NULL, provider_event_id TEXT NOT NULL, next_due_at TEXT NOT NULL,
  PRIMARY KEY (provider, league, provider_event_id));
CREATE TABLE IF NOT EXISTS sports_book_lease (name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sports_book_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
"""
QUOTE_COLS = ("offer_key", "observed_at", "fetch_id", "provider", "book", "jurisdiction", "league", "event_key",
              "provider_event_id", "event_name", "home", "away", "start", "player", "player_key", "provider_player_id",
              "market", "stat_label", "period", "line", "side", "market_type", "price_american", "price_decimal",
              "status", "provider_market_id", "provider_selection_id", "provider_updated_at")


def iso(t=None):
    return (t or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


class DB:
    """A thin connection wrapper: SQL is written once with '?' placeholders; rows come back as dicts."""

    def __init__(self, path=None, dsn=None, schema=None):
        schema = schema or SCHEMA
        self.pg = bool(dsn)
        if self.pg:
            import psycopg2
            import psycopg2.extras
            self.con = psycopg2.connect(dsn)
            self._cursor = lambda: self.con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ddl = schema.replace("{ID}", "BIGSERIAL PRIMARY KEY").replace("{REAL}", "DOUBLE PRECISION")
        else:
            p = path or (config.DATA_DIR / "books.db")
            p.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(p, timeout=30)
            self.con.row_factory = sqlite3.Row
            self.con.execute("PRAGMA journal_mode=WAL")
            self._cursor = self.con.cursor
            ddl = schema.replace("{ID}", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("{REAL}", "REAL")
        cur = self._cursor()
        for stmt in (s.strip() for s in ddl.split(";")):
            if stmt:
                cur.execute(stmt)
        self.con.commit()

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql, params=()):
        cur = self._cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def all(self, sql, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        r = self.execute(sql, params).fetchone()
        return dict(r) if r else None

    def insert(self, sql, params=()):
        """INSERT ... -> the new row id."""
        if self.pg:
            return self.execute(sql + " RETURNING id", params).fetchone()["id"]
        return self.execute(sql, params).lastrowid

    def commit(self):
        self.con.commit()

    def close(self):
        self.con.close()


def connect(path=None, dsn=None):
    dsn = dsn if dsn is not None else os.environ.get("SPORTS_DB_DSN") or None
    return DB(path=path, dsn=dsn)


# ------------------------------------------------------------------------------------------------ writes (worker)

def start_fetch(db, provider, league, scope, provider_event_id=None, event_key=None, now=None):
    fid = db.insert("INSERT INTO sports_book_fetches (provider, league, scope, provider_event_id, event_key, "
                    "started_at) VALUES (?,?,?,?,?,?)", (provider, league, scope, provider_event_id, event_key,
                                                          iso(now)))
    db.commit()
    return fid


def finish_fetch(db, fid, res, n_offers=0, n_new=0, n_removed=0, now=None):
    db.execute("UPDATE sports_book_fetches SET finished_at = ?, ok = ?, blocked = ?, http_status = ?, error = ?, "
               "requests = ?, n_offers = ?, n_new_quotes = ?, n_removed = ?, credits_used = ?, credits_remaining = ? "
               "WHERE id = ?", (iso(now), int(res.error is None), int(bool(res.blocked)), res.http_status,
                                (res.error or None) and res.error[:300], res.requests, n_offers, n_new, n_removed,
                                res.credits_used, res.credits_remaining, fid))
    db.commit()


def record_events(db, provider, league, events, keys, now=None):
    t = iso(now)
    for e in events:
        db.execute("INSERT INTO sports_book_events (provider, provider_event_id, league, event_key, name, home, away, "
                   "start, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (provider, "
                   "provider_event_id) DO UPDATE SET event_key = excluded.event_key, name = excluded.name, "
                   "home = excluded.home, away = excluded.away, start = excluded.start, last_seen_at = excluded.last_seen_at",
                   (provider, e.provider_event_id, league, keys.get(e.provider_event_id), e.name, e.home, e.away,
                    e.start, t, t))
    db.commit()


def _quote_row(o, fid, t, event_key, status=None, removed=False):
    row = dict(o, observed_at=t, fetch_id=fid, event_key=event_key)
    if removed:
        row.update(status="removed", price_american=None, price_decimal=None, provider_updated_at=None)
    elif status:
        row["status"] = status
    return tuple(row.get(c) for c in QUOTE_COLS)


_INSERT_Q = (f"INSERT INTO sports_book_quotes ({', '.join(QUOTE_COLS)}) "
             f"VALUES ({', '.join('?' for _ in QUOTE_COLS)})")


def save_offers(db, fid, provider, league, provider_event_id, event_key, offers, complete, now=None):
    """Store one game's offers from one fetch. -> (new quote rows, removed). A quote row is written only when the offer
    is new or its price / status changed; last_seen_at moves on every observation. complete=True (the fetch read the
    whole game without error) also closes offers this game no longer lists with a 'removed' row."""
    t = iso(now)
    new = removed = 0
    seen = set()
    for o in offers:
        k = o["offer_key"]
        if k in seen:
            continue                                   # one offer listed twice in one answer is read once
        seen.add(k)
        st = db.one("SELECT s.last_quote_id, s.last_status, q.price_american, q.price_decimal FROM "
                    "sports_book_offer_state s JOIN sports_book_quotes q ON q.id = s.last_quote_id WHERE s.offer_key = ?",
                    (k,))
        changed = (st is None or st["last_status"] != o["status"] or st["price_american"] != o["price_american"]
                   or (st["price_decimal"] is not None and abs(st["price_decimal"] - o["price_decimal"]) > 1e-9))
        if changed:
            qid = db.insert(_INSERT_Q, _quote_row(o, fid, t, event_key))
            new += 1
            db.execute("INSERT INTO sports_book_offer_state (offer_key, provider, provider_event_id, league, event_key, "
                       "player_key, market, last_quote_id, last_status, first_seen_at, last_seen_at, last_fetch_id) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (offer_key) DO UPDATE SET "
                       "last_quote_id = excluded.last_quote_id, last_status = excluded.last_status, "
                       "event_key = excluded.event_key, last_seen_at = excluded.last_seen_at, "
                       "last_fetch_id = excluded.last_fetch_id",
                       (k, provider, provider_event_id, league, event_key, o["player_key"], o["market"], qid,
                        o["status"], t, t, fid))
        else:
            db.execute("UPDATE sports_book_offer_state SET last_seen_at = ?, last_fetch_id = ?, event_key = ? "
                       "WHERE offer_key = ?", (t, fid, event_key, k))
    if complete:
        gone = db.all("SELECT s.offer_key, s.last_quote_id FROM sports_book_offer_state s WHERE s.provider = ? AND "
                      "s.provider_event_id = ? AND s.last_status != 'removed'", (provider, provider_event_id))
        for g in gone:
            if g["offer_key"] in seen:
                continue
            last = db.one("SELECT * FROM sports_book_quotes WHERE id = ?", (g["last_quote_id"],))
            qid = db.insert(_INSERT_Q, _quote_row(last, fid, t, event_key, removed=True))
            db.execute("UPDATE sports_book_offer_state SET last_quote_id = ?, last_status = 'removed', "
                       "last_seen_at = ?, last_fetch_id = ? WHERE offer_key = ?", (qid, t, fid, g["offer_key"]))
            removed += 1
    db.commit()
    return new, removed


# ------------------------------------------------------------------------------------------------ provider health

def provider_state(db, provider):
    return db.one("SELECT * FROM sports_book_provider_state WHERE provider = ?", (provider,)) or {
        "provider": provider, "cooldown_until": None, "failures": 0, "last_ok_at": None, "last_error": None,
        "last_error_at": None, "blocked": 0, "credits_remaining": None, "credits_checked_at": None}


def note_result(db, provider, res, now=None, cooldown_until=None):
    """Record a provider's result: success clears the failure streak; a failure sets the next cooldown."""
    t = iso(now)
    s = provider_state(db, provider)
    if res.error is None:
        vals = (None, 0, t, s["last_error"], s["last_error_at"], 0)
    else:
        vals = (cooldown_until, s["failures"] + 1, s["last_ok_at"], res.error[:300], t, int(bool(res.blocked)))
    credits = res.credits_remaining if res.credits_remaining is not None else s["credits_remaining"]
    checked = t if res.credits_remaining is not None else s["credits_checked_at"]
    db.execute("INSERT INTO sports_book_provider_state (provider, cooldown_until, failures, last_ok_at, last_error, "
               "last_error_at, blocked, credits_remaining, credits_checked_at) VALUES (?,?,?,?,?,?,?,?,?) "
               "ON CONFLICT (provider) DO UPDATE SET cooldown_until = excluded.cooldown_until, "
               "failures = excluded.failures, last_ok_at = excluded.last_ok_at, last_error = excluded.last_error, "
               "last_error_at = excluded.last_error_at, blocked = excluded.blocked, "
               "credits_remaining = excluded.credits_remaining, credits_checked_at = excluded.credits_checked_at",
               (provider, *vals, credits, checked))
    db.commit()


# ------------------------------------------------------------------------------------------------ schedule + lease

def due(db, provider, league, provider_event_id):
    r = db.one("SELECT next_due_at FROM sports_book_schedule WHERE provider = ? AND league = ? AND "
               "provider_event_id = ?", (provider, league, provider_event_id))
    return r["next_due_at"] if r else None


def set_due(db, provider, league, provider_event_id, when):
    db.execute("INSERT INTO sports_book_schedule (provider, league, provider_event_id, next_due_at) VALUES (?,?,?,?) "
               "ON CONFLICT (provider, league, provider_event_id) DO UPDATE SET next_due_at = excluded.next_due_at",
               (provider, league, provider_event_id, iso(when)))
    db.commit()


def take_lease(db, holder, ttl_s, name="worker", now=None):
    """Atomically take (or renew) the lease. -> True when this holder owns it until now + ttl_s."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    cur = db.execute("INSERT INTO sports_book_lease (name, holder, expires_at) VALUES (?,?,?) ON CONFLICT (name) "
                     "DO UPDATE SET holder = excluded.holder, expires_at = excluded.expires_at "
                     "WHERE sports_book_lease.expires_at < ? OR sports_book_lease.holder = ?",
                     (name, holder, iso(now + timedelta(seconds=ttl_s)), iso(now), holder))
    db.commit()
    return cur.rowcount == 1


def release_lease(db, holder, name="worker"):
    db.execute("DELETE FROM sports_book_lease WHERE name = ? AND holder = ?", (name, holder))
    db.commit()


def setting(db, key, default=None):
    r = db.one("SELECT value FROM sports_book_settings WHERE key = ?", (key,))
    return r["value"] if r else default


def set_setting(db, key, value, now=None):
    db.execute("INSERT INTO sports_book_settings (key, value, updated_at) VALUES (?,?,?) ON CONFLICT (key) DO UPDATE "
               "SET value = excluded.value, updated_at = excluded.updated_at", (key, value, iso(now)))
    db.commit()
