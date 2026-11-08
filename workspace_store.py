"""Research workspace store: tracked receipts of every sport, their settlements, and each member's working slip.
DATA_DIR/workspace.db (WAL), outside .cache so a cache sweep never touches it. Nothing here grades, logs a forecast or writes to
picks.db, the LoL database or the sports store.

Receipts, settlements and slips are append-only: triggers abort any UPDATE or DELETE, a correction is a new settlement
revision naming the one it replaces. member_state is the mutable working draft (slip, preferences, saved views); pick_watch is
the settler's mutable worklist.

Writes go through one bounded queue and one writer thread that group-commits up to WRITE_BATCH operations in one short
transaction (synchronous=FULL, so one fsync per group). The request waits up to WRITE_WAIT seconds for its commit; a full
queue or a timeout raises Busy, the page answers 503 and the slip stays on screen. Retries are safe: a repeated press finds its
own slip, and any other receipt on the same prop is refused before anything is inserted.

Reads for 700 members: the revision that keys a member's cached page lives in memory (the writer sets it after each commit,
and this process is the only writer), so a cache hit touches no database. A /mine page reads one page of receipts plus light
columns for the record, and at most PAGE_SLOTS of them run at once, so Track's point reads always find a read slot.

Settlement has no rule of its own: the settler passes a receipt's own pick row to slip_batches.leg_result_from_picks, so the
receipt's line and side decide, and one platform's settlement can never settle another platform's leg. It works per pick, not
per receipt: pick_watch holds one row per tracked pick with a mark of the pick-log state last applied. A pass reads the marks
(every pick with no final result, and a rotating page of the rest), and only a pick whose state moved costs a receipt read. So
no number of newer receipts can push an older one out of a pass, and the cost follows the picks that changed.

The pick log decides, and a receipt follows it. A pick that settled and then went back to open or was held for review adds a
"reopened" revision: the receipt reads Pending again and leaves the record until the pick settles. A pick log that cannot be
read raises before anything is written, and a pick row that is missing changes nothing: an outage keeps the last result.

LoL, NBA and NFL settle the same way from their own settled results. Store.sources holds one reader per sport, set by the API:
a reader gets a page of leg_watch rows and answers {settle_key: {result, actual, ...}} from that sport's existing settlement
(never a rule of its own here). A key with no answer, or an answer with no result yet, changes nothing, and a reader that raises
costs that sport one pass. A leg_watch row is one exact pick: sport, offer or unit, line and side."""
import hashlib
import json
import queue
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_MAX = 2000
WRITE_BATCH = 200
WRITE_WAIT = 12.0                       # a burst of 700 presses queues here: a queued write lands, so giving up early only hides it
READ_SLOTS = 8
READ_WAIT = 3.0
PAGE_SLOTS = 1                          # serialize bulk row conversion; parallel sqlite fetches contend on Python's GIL
PAGE_WAIT = 12.0                        # a burst of cold pages queues here: the page's own request gives up at 15 s
PAGE_SPILL = 24                         # receipts read past a page's limit: one press of 8 legs and a second press in the same second
REV_CACHE = 8192
SETTLE_EVERY = 120
SETTLE_PICKS = 2000                     # picks read per tier per pass; a longer list takes more passes, and every pick gets its turn
SETTLE_CHANGED = 400                    # changed picks handled per pass; the rest keep their old mark and come back next pass
SETTLE_CHUNK = 50
SETTLE_DAYS = 21
SETTLE_OPEN_DAYS = 60
RECORD_SCAN = 2000
PAGE_MAX = 200
DIAG_MAX = 200
VOID_SIBLING_CHECKS = 20
SETTLE_LEGS = 400                       # other-sport keys read per sport per pass; a longer list takes more passes
REOPENED = "reopened"
FINAL = ("settled", "push", "void")
SPORTS = ("cs2", "lol", "nba", "nfl")
SPORT_LABEL = {"cs2": "CS2", "lol": "LoL", "nba": "NBA", "nfl": "NFL"}

RECEIPTS_DDL = """CREATE TABLE IF NOT EXISTS receipts(
  receipt_id TEXT PRIMARY KEY, member TEXT NOT NULL, slip_id TEXT NOT NULL, leg_no INTEGER NOT NULL, tracked_at TEXT NOT NULL,
  sport TEXT NOT NULL CHECK(sport IN ('cs2','lol','nba','nfl')), platform TEXT NOT NULL, row_id TEXT NOT NULL, offer_rev TEXT NOT NULL, side TEXT NOT NULL,
  line REAL NOT NULL, line_type TEXT NOT NULL, pick_id INTEGER, forecast_id NOT NULL, match_slug TEXT NOT NULL,
  match_id TEXT, match_key TEXT NOT NULL, nick TEXT NOT NULL, prop TEXT NOT NULL, scope TEXT NOT NULL, start_utc TEXT, model_version TEXT,
  scratch INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, settle_key TEXT,
  CHECK(sport <> 'cs2' OR pick_id IS NOT NULL), CHECK(sport = 'cs2' OR settle_key IS NOT NULL));"""
SCHEMA = RECEIPTS_DDL + """
CREATE INDEX IF NOT EXISTS receipts_member ON receipts(member, tracked_at DESC);
CREATE INDEX IF NOT EXISTS receipts_member_match ON receipts(member, match_slug);
CREATE INDEX IF NOT EXISTS receipts_member_key ON receipts(member, match_key);
CREATE INDEX IF NOT EXISTS receipts_pick ON receipts(pick_id);
CREATE INDEX IF NOT EXISTS receipts_slip ON receipts(slip_id, leg_no);
CREATE INDEX IF NOT EXISTS receipts_settle ON receipts(settle_key) WHERE settle_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS settlements(
  receipt_id TEXT NOT NULL, revision INTEGER NOT NULL, result TEXT NOT NULL CHECK(result IN('hit','miss','push','void','reopened')),
  actual REAL, actual_maps TEXT, source TEXT, picklog_settled_at TEXT, recorded_at TEXT NOT NULL, supersedes INTEGER, reason TEXT,
  PRIMARY KEY(receipt_id, revision));
CREATE TABLE IF NOT EXISTS slips(
  slip_id TEXT PRIMARY KEY, member TEXT NOT NULL, tracked_at TEXT NOT NULL, platform TEXT NOT NULL, entry_type TEXT, sports TEXT NOT NULL,
  legs INTEGER NOT NULL, rules_version TEXT, checks TEXT, scratch INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS slips_member ON slips(member, tracked_at DESC);
CREATE TABLE IF NOT EXISTS member_state(member TEXT PRIMARY KEY, rev INTEGER NOT NULL, updated_at TEXT NOT NULL, slip TEXT, prefs TEXT, views TEXT, watch TEXT, studio TEXT);
CREATE TABLE IF NOT EXISTS member_rev(member TEXT PRIMARY KEY, rev INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pick_watch(pick_id INTEGER PRIMARY KEY, mark TEXT, active INTEGER NOT NULL DEFAULT 1, last_tracked TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pick_watch_active ON pick_watch(active, pick_id);
CREATE TABLE IF NOT EXISTS leg_watch(settle_key TEXT PRIMARY KEY, sport TEXT NOT NULL, ref TEXT NOT NULL, start_utc TEXT, mark TEXT, active INTEGER NOT NULL DEFAULT 1, last_tracked TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS leg_watch_page ON leg_watch(sport, active, settle_key);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
APPEND_ONLY = ("receipts", "settlements", "slips")
RECEIPT_COLS = ("receipt_id", "member", "slip_id", "leg_no", "tracked_at", "sport", "platform", "row_id", "offer_rev", "side", "line", "line_type", "pick_id",
                "forecast_id", "match_slug", "match_id", "match_key", "nick", "prop", "scope", "start_utc", "model_version", "scratch", "payload")
LIGHT_SQL = ("SELECT r.sport, r.platform, r.model_version, r.match_key, r.nick, r.prop, r.scope, (SELECT s.result FROM settlements s "
             "WHERE s.receipt_id = r.receipt_id ORDER BY s.revision DESC LIMIT 1) AS st FROM receipts r WHERE r.member = ? "
             "ORDER BY r.tracked_at DESC LIMIT ?")
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}


class Busy(Exception):
    """The store could not take this request in time: the caller answers 503 and the slip stays on screen."""


def iso(t=None):
    return (t or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class _Op:
    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn):
        self.fn, self.done, self.result, self.error = fn, threading.Event(), None, None


class Store:
    def __init__(self, path, queue_max=QUEUE_MAX, start=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._q = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self._readers = queue.LifoQueue()
        self._lock = threading.Lock()
        self._n_readers = 0
        self._pages = threading.BoundedSemaphore(PAGE_SLOTS)
        self._page_cons = []
        self._revs = OrderedDict()              # ("rev" | "state", member) -> revision, set by the writer after each commit
        self._rev_lock = threading.Lock()
        self._op_revs = {}                      # the running operation's revisions: writer thread only
        self._cursor = {0: 0, 1: 0}             # where each pick_watch tier resumes: settler thread only
        self._leg_cursor = {}                   # where each sport's leg_watch tier resumes: settler thread only
        self.sources = {}                       # sport -> reader of that sport's settled results, set by the API
        con = self._open()
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        self._migrate(con)
        con.executescript(SCHEMA)
        for t in APPEND_ONLY:
            for act in ("UPDATE", "DELETE"):
                con.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{act.lower()} BEFORE {act} ON {t} "
                            f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END")
        con.close()
        self._writer = None
        self._settler = None
        if start:
            self.start()

    def _open(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _migrate(con):
        """A database written while receipts took CS2 only: one transaction copies every row into the table that takes all four
        sports, or changes nothing. The append-only triggers are made again by the caller, and receipts stay as written."""
        cols = [r[1] for r in con.execute("PRAGMA table_info(member_state)")]
        if cols and "watch" not in cols:
            con.execute("ALTER TABLE member_state ADD COLUMN watch TEXT")
        if cols and "studio" not in cols:
            con.execute("ALTER TABLE member_state ADD COLUMN studio TEXT")
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'receipts'").fetchone()
        if row is None or "'lol'" in row["sql"]:
            return
        names = ", ".join(RECEIPT_COLS)
        con.execute("BEGIN IMMEDIATE")
        try:
            before = con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            con.execute("DROP TRIGGER IF EXISTS receipts_no_update")
            con.execute("DROP TRIGGER IF EXISTS receipts_no_delete")
            con.execute("ALTER TABLE receipts RENAME TO receipts_cs2_only")
            con.execute(RECEIPTS_DDL)
            con.execute(f"INSERT INTO receipts({names}) SELECT {names} FROM receipts_cs2_only")
            if con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] != before:
                raise sqlite3.DatabaseError("receipt migration lost rows")
            con.execute("DROP TABLE receipts_cs2_only")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    # ---- threads
    def start(self):
        with self._lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(target=self._write_loop, daemon=True, name="workspace-writer")
                self._writer.start()

    def start_settler(self, every=SETTLE_EVERY):
        with self._lock:
            if self._settler is None or not self._settler.is_alive():
                self._settler = threading.Thread(target=self._settle_loop, args=(every,), daemon=True, name="workspace-settler")
                self._settler.start()

    def close(self):
        self._stop.set()
        for th in (self._writer, self._settler):
            if th is not None and th.is_alive():
                th.join(timeout=3)
        with self._lock:
            for con in self._page_cons:
                con.close()
            self._page_cons.clear()
        while True:
            try:
                self._readers.get_nowait().close()
            except queue.Empty:
                return

    def _settle_loop(self, every):
        while not self._stop.wait(every):
            try:
                self.settle_once()
            except Exception:                       # the next pass tries again; a mark only moves after its writes commit
                import traceback
                traceback.print_exc()

    def _write_loop(self):
        con = self._open()
        con.execute("PRAGMA synchronous=FULL")
        try:
            while not self._stop.is_set() or not self._q.empty():
                try:
                    batch = [self._q.get(timeout=0.2)]
                except queue.Empty:
                    continue
                while len(batch) < WRITE_BATCH:
                    try:
                        batch.append(self._q.get_nowait())
                    except queue.Empty:
                        break
                self._commit(con, batch)
        finally:
            con.close()

    def _commit(self, con, batch):
        moved = {}
        try:
            con.execute("BEGIN IMMEDIATE")
            for op in batch:
                self._op_revs = {}
                con.execute("SAVEPOINT op")
                try:
                    op.result = op.fn(con)
                    con.execute("RELEASE op")
                    moved.update(self._op_revs)
                except Exception as e:              # this one operation failed; the rest of the group still commits
                    op.error = e
                    con.execute("ROLLBACK TO op")
                    con.execute("RELEASE op")
            con.execute("COMMIT")
        except sqlite3.Error as e:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            moved = dict.fromkeys(moved)            # no longer known: the next read asks the database
            for op in batch:
                op.result, op.error = None, op.error or e
        finally:
            with self._rev_lock:
                for k, v in moved.items():
                    if v is None:
                        self._revs.pop(k, None)
                    else:
                        self._revs[k] = v
                        self._revs.move_to_end(k)
                while len(self._revs) > REV_CACHE:
                    self._revs.popitem(last=False)
            for op in batch:
                op.done.set()

    def submit(self, fn, wait=WRITE_WAIT):
        """Run fn(con) inside the writer's transaction and wait for the commit."""
        op = _Op(fn)
        try:
            self._q.put_nowait(op)
        except queue.Full:
            raise Busy("write queue full") from None
        if not op.done.wait(wait):
            raise Busy("write timed out")
        if op.error is not None:
            raise op.error
        return op.result

    @contextmanager
    def reader(self, wait=None):
        wait = READ_WAIT if wait is None else wait
        con = None
        try:
            con = self._readers.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._n_readers < READ_SLOTS:
                    # a slot is counted only once its connection really opened: a failed open (EMFILE, disk fault)
                    # gives the slot back and closes a half-opened connection (bug sweep 2026-11-06 #57)
                    con = self._open_reader()
                    self._n_readers += 1
        if con is None:
            try:
                con = self._readers.get(timeout=wait)
            except queue.Empty:
                raise Busy("no read slot") from None
        try:
            yield con
        finally:
            self._readers.put(con)

    @contextmanager
    def page_reader(self):
        """A /mine page's own connection (one per page slot): a page that holds its slot never queues behind point reads."""
        with self._lock:
            con = self._page_cons.pop() if self._page_cons else None
        if con is None:
            con = self._open_reader()
        try:
            yield con
        finally:
            with self._lock:
                self._page_cons.append(con)

    def _open_reader(self):
        """A read-only connection, or the error with nothing left open."""
        con = self._open()
        try:
            con.execute("PRAGMA query_only=ON")
        except BaseException:
            con.close()
            raise
        return con

    # ---- revisions: what keys a member's cached page. A cache hit reads no database.
    def _bump(self, con, member):
        con.execute("INSERT INTO member_rev(member, rev) VALUES(?, 1) ON CONFLICT(member) DO UPDATE SET rev = rev + 1", (member,))
        self._op_revs[("rev", member)] = con.execute("SELECT rev FROM member_rev WHERE member = ?", (member,)).fetchone()["rev"]

    def _known_rev(self, kind, member, sql):
        k = (kind, member)
        with self._rev_lock:
            v = self._revs.get(k)
        if v is not None:
            return v
        with self.reader() as con:
            r = con.execute(sql, (member,)).fetchone()
        with self._rev_lock:
            v = self._revs.setdefault(k, r["rev"] if r else 0)     # the writer's value, set while this read ran, stands
            while len(self._revs) > REV_CACHE:
                self._revs.popitem(last=False)
        return v

    def rev(self, member):
        return self._known_rev("rev", member, "SELECT rev FROM member_rev WHERE member = ?")

    def state_rev(self, member):
        return self._known_rev("state", member, "SELECT rev FROM member_state WHERE member = ?")

    # ---- tracking
    def slip_receipts(self, member, slip_id):
        with self.reader() as con:
            rows = con.execute("SELECT receipt_id, tracked_at FROM receipts WHERE slip_id = ? AND member = ? ORDER BY leg_no",
                               (slip_id, member)).fetchall()
        return [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in rows]

    def tracked_for(self, member, keys, slugs):
        """The member's receipts on these exact matches, by canonical key or slug (bounded by the leg limit), for the duplicate and
        conflict checks."""
        keys, slugs = sorted({k for k in keys if k})[:16], sorted({s for s in slugs if s})[:16]
        if not keys and not slugs:
            return []
        with self.reader() as con:
            return [dict(r) for r in con.execute(
                f"SELECT receipt_id, row_id, nick, prop, scope, match_slug, match_key, platform, line, side FROM receipts WHERE member = ? "
                f"AND (match_key IN ({','.join('?' * len(keys))}) OR match_slug IN ({','.join('?' * len(slugs))})) LIMIT 400",
                (member, *keys, *slugs))]

    def track(self, slip, receipts):
        """One entry, all or nothing. slip: the slips row; receipts: rows with payload (dict). -> {"ok": True, "receipts"} or
        {"ok": False, "duplicate": [...]} when a concurrent press already tracked one of these picks. A receipt is never dropped
        from a slip: anything that would clash is refused here, before the first insert."""
        def op(con):
            have = con.execute("SELECT receipt_id, tracked_at FROM receipts WHERE slip_id = ? ORDER BY leg_no", (slip["slip_id"],)).fetchall()
            if have:
                return {"ok": True, "again": True, "receipts": [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in have]}
            dup = []
            for r in receipts:
                other = con.execute("SELECT receipt_id, line, side FROM receipts WHERE receipt_id = ? OR (member = ? AND nick = ? AND prop = ? "
                                    "AND scope = ? AND platform = ? AND (match_key = ? OR match_slug = ?)) LIMIT 1",
                                    (r["receipt_id"], r["member"], r["nick"], r["prop"], r["scope"], r["platform"], r["match_key"], r["match_slug"])).fetchone()
                if other:
                    dup.append({"receipt_id": r["receipt_id"], "same": other["line"] == r["line"] and other["side"] == r["side"]})
            if dup:
                return {"ok": False, "duplicate": dup}
            con.execute("INSERT INTO slips(slip_id, member, tracked_at, platform, entry_type, sports, legs, rules_version, checks, scratch) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (slip["slip_id"], slip["member"], slip["tracked_at"], slip["platform"], slip.get("entry_type"), json.dumps(slip["sports"]),
                         len(receipts), slip.get("rules_version"), json.dumps(slip.get("checks")), int(bool(slip.get("scratch")))))
            for r in receipts:
                con.execute("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, "
                            "line_type, pick_id, forecast_id, match_slug, match_id, match_key, nick, prop, scope, start_utc, model_version, scratch, payload, settle_key) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (r["receipt_id"], r["member"], r["slip_id"], r["leg_no"], r["tracked_at"], r["sport"], r["platform"], r["row_id"],
                             r["offer_rev"], r["side"], r["line"], r["line_type"], r.get("pick_id"), r["forecast_id"], r["match_slug"], r.get("match_id"),
                             r["match_key"], r["nick"], r["prop"], r["scope"], r.get("start_utc"), r.get("model_version"), int(bool(r.get("scratch"))),
                             json.dumps(r["payload"], separators=(",", ":")), r.get("settle_key")))
                # a pick the settler had finished with is read again, so this new receipt cannot wait behind an old mark
                if r["sport"] == "cs2":
                    con.execute("INSERT INTO pick_watch(pick_id, mark, active, last_tracked) VALUES(?, NULL, 1, ?) ON CONFLICT(pick_id) DO UPDATE SET "
                                "last_tracked = excluded.last_tracked, mark = CASE WHEN pick_watch.active = 1 THEN pick_watch.mark END, active = 1",
                                (r["pick_id"], r["tracked_at"]))
                else:
                    con.execute("INSERT INTO leg_watch(settle_key, sport, ref, start_utc, mark, active, last_tracked) VALUES(?,?,?,?,NULL,1,?) ON CONFLICT(settle_key) DO UPDATE SET "
                                "last_tracked = excluded.last_tracked, mark = CASE WHEN leg_watch.active = 1 THEN leg_watch.mark END, active = 1",
                                (r["settle_key"], r["sport"], json.dumps(r["settle_ref"], separators=(",", ":")), r.get("start_utc"), r["tracked_at"]))
            self._bump(con, slip["member"])
            return {"ok": True, "receipts": [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in receipts]}
        return self.submit(op)

    # ---- member state: the working draft. A stale rev is refused with the current copy; the page merges.
    def get_state(self, member):
        with self.reader() as con:
            r = con.execute("SELECT rev, slip, prefs, views, watch, studio FROM member_state WHERE member = ?", (member,)).fetchone()
        return self._state(r)

    @staticmethod
    def _state(r):
        if r is None:
            return {"rev": 0, "slip": None, "prefs": None, "views": None, "watch": None, "studio": None}
        return {"rev": r["rev"], "slip": json.loads(r["slip"]) if r["slip"] else None, "prefs": json.loads(r["prefs"]) if r["prefs"] else None,
                "views": json.loads(r["views"]) if r["views"] else None, "watch": json.loads(r["watch"]) if r["watch"] else None,
                "studio": json.loads(r["studio"]) if r["studio"] else None}

    def put_state(self, member, rev, patch):
        """patch: any of slip, prefs, views, watch. -> {"ok": True, "rev"} or {"ok": False, "state": current}."""
        def op(con):
            cur = con.execute("SELECT rev, slip, prefs, views, watch, studio FROM member_state WHERE member = ?", (member,)).fetchone()
            have = cur["rev"] if cur else 0
            if rev != have:
                return {"ok": False, "state": self._state(cur)}
            keep = {k: (cur[k] if cur else None) for k in ("slip", "prefs", "views", "watch", "studio")}
            for k in keep:
                if k in patch:
                    keep[k] = json.dumps(patch[k], separators=(",", ":")) if patch[k] is not None else None
            con.execute("INSERT INTO member_state(member, rev, updated_at, slip, prefs, views, watch, studio) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(member) DO UPDATE SET rev = excluded.rev, updated_at = excluded.updated_at, slip = excluded.slip, "
                        "prefs = excluded.prefs, views = excluded.views, watch = excluded.watch, studio = excluded.studio", (member, have + 1, iso(), keep["slip"], keep["prefs"], keep["views"], keep["watch"], keep["studio"]))
            self._op_revs[("state", member)] = have + 1
            return {"ok": True, "rev": have + 1}
        return self.submit(op)

    # ---- reads: a member's receipts
    def mine(self, member, before=None, limit=50):
        """One page of receipts newest first, with their append-only timelines and slips, plus the record over the newest
        RECORD_SCAN receipts. The record reads light columns only: no payload is parsed for a receipt off this page."""
        limit = max(1, min(int(limit or 50), PAGE_MAX))
        if not self._pages.acquire(timeout=PAGE_WAIT):
            raise Busy("no page slot")
        try:
            with self.page_reader() as con:
                light = con.execute(LIGHT_SQL, (member, RECORD_SCAN + 1)).fetchall()
                sql, args = "SELECT * FROM receipts WHERE member = ?", [member]
                if before:
                    sql, args = sql + " AND tracked_at < ?", args + [before]
                fetch = limit + PAGE_SPILL + 1
                rows = [dict(r) for r in con.execute(sql + " ORDER BY tracked_at DESC, leg_no ASC LIMIT ?", (*args, fetch))]
                cut = min(limit, len(rows))
                while cut < len(rows) and rows[cut]["tracked_at"] == rows[cut - 1]["tracked_at"]:
                    cut += 1                        # a page never splits one slip, whose legs share a time
                page, more = rows[:cut], cut < len(rows) or len(rows) == fetch
                revs = {}
                for i in range(0, len(page), 400):
                    part = [r["receipt_id"] for r in page[i:i + 400]]
                    for s in con.execute(f"SELECT * FROM settlements WHERE receipt_id IN ({','.join('?' * len(part))}) ORDER BY revision", part):
                        revs.setdefault(s["receipt_id"], []).append(dict(s))
                ids = sorted({r["slip_id"] for r in page})
                slips = [dict(r) for r in con.execute(f"SELECT slip_id, platform, entry_type, sports, tracked_at, legs, scratch FROM slips WHERE slip_id IN "
                                                      f"({','.join('?' * len(ids))}) ORDER BY tracked_at DESC", ids)] if ids else []
        finally:
            self._pages.release()
        views = [receipt_view(r, revs.get(r["receipt_id"], [])) for r in page]
        for v, r in zip(views, page):           # the offer's identity for workspace_api.get_mine's line-move note; never sent
            v["_match_key"], v["_settle_key"] = r["match_key"], r.get("settle_key")
        by_slip = {}
        for v in views:
            by_slip.setdefault(v["slip_id"], []).append(v)
        slip_out = [{"slip_id": s["slip_id"], "platform": s["platform"], "entry_type": s["entry_type"], "tracked_at": s["tracked_at"],
                     "legs": s["legs"], "counts": _counts(by_slip.get(s["slip_id"], []))} for s in slips]
        total_capped = len(light) > RECORD_SCAN
        lights = [{"status": _status(r["st"]), "sport": r["sport"], "platform_label": LABEL.get(r["platform"], r["platform"]), "model_version": r["model_version"],
                   "match_key": r["match_key"], "nick": r["nick"], "prop": r["prop"], "scope": r["scope"]} for r in light[:RECORD_SCAN]]
        return {"record": record_of(lights, total_capped), "by_sport": _sport_groups(lights), "by_platform": _groups(lights, "platform_label"), "by_version": _groups(lights, "model_version"),
                "slips": slip_out, "receipts": views, "next_before": page[-1]["tracked_at"] if more and page else None}

    # ---- settlement
    def settle_once(self, now=None):
        """One pass. -> {"seen", "first", "corrected", "skipped"}, counted in receipts read. A pick log that cannot be read raises
        before anything is written; one bad row costs that row only."""
        now = now or datetime.now(timezone.utc)
        out = {"seen": 0, "first": 0, "corrected": 0, "skipped": 0}
        for sport in sorted(self.sources):
            try:
                self._settle_sport(sport, now, out)
            except Exception:                       # that sport's results stay as they were; it is asked again next pass
                import traceback
                traceback.print_exc()
        watch = self._watch_page(1) + self._watch_page(0)
        picks = self._read_picks([w["pick_id"] for w in watch]) if watch else {}
        changed = []
        for w in watch:
            p = picks.get(w["pick_id"])
            if p is not None and _mark(p) != w["mark"]:
                changed.append((w, p))
        voids = []
        for i in range(0, min(len(changed), SETTLE_CHANGED), SETTLE_CHUNK):
            self._apply(changed[i:i + SETTLE_CHUNK], now, out, voids)
        if voids:
            try:
                self._note_voids(self._void_siblings(voids))
            except Exception:                       # the owner's diagnostics never cost a member a settlement
                import traceback
                traceback.print_exc()
        self._prune(now)
        return out

    def _watch_page(self, active):
        with self.reader(wait=5.0) as con:
            rows = [dict(r) for r in con.execute("SELECT pick_id, mark, last_tracked FROM pick_watch WHERE active = ? AND pick_id > ? ORDER BY pick_id LIMIT ?",
                                                 (active, self._cursor[active], SETTLE_PICKS))]
        self._cursor[active] = rows[-1]["pick_id"] if len(rows) == SETTLE_PICKS else 0
        return rows

    @staticmethod
    def _read_picks(ids):
        import picklog
        picks = {}
        pc = picklog._read_db()
        try:
            for i in range(0, len(ids), 200):
                part = ids[i:i + 200]
                for p in pc.execute("SELECT id, player, prop, scope, match_slug, status, actual, actual_maps, settled_at, result, line, book, match_start "
                                    f"FROM picks WHERE id IN ({','.join('?' * len(part))})", part):
                    picks[p["id"]] = dict(p)
        finally:
            pc.close()
        return picks

    def _apply(self, part, now, out, voids):
        """The receipts of these changed picks against their pick rows, then the marks. A mark moves only after its pick's writes
        commit, so a pass cut short repeats them: the same answer again writes nothing."""
        import slip_batches
        by_pick = {w["pick_id"]: (w, p, slip_batches.leg_result_from_picks([p])) for w, p in part}
        ids = list(by_pick)
        with self.reader(wait=5.0) as con:
            rows = [dict(r) for r in con.execute(
                "SELECT r.receipt_id, r.member, r.pick_id, r.line, r.side, s.revision, s.result, s.actual "
                "FROM receipts r LEFT JOIN settlements s ON s.receipt_id = r.receipt_id AND "
                "s.revision = (SELECT MAX(x.revision) FROM settlements x WHERE x.receipt_id = r.receipt_id) "
                f"WHERE r.pick_id IN ({','.join('?' * len(ids))})", ids)]
        writes, stamp = [], iso(now)
        for r in rows:
            w_, p, result_of = by_pick[r["pick_id"]]
            out["seen"] += 1
            leg = {"nick": p["player"], "prop": p["prop"], "scope": p["scope"] or "12", "match_slug": p["match_slug"], "line": r["line"],
                   "side": "More" if r["side"] == "over" else "Less"}
            try:
                res = result_of(leg)
            except Exception:                       # one bad row costs that row only
                import traceback
                traceback.print_exc()
                out["skipped"] += 1
                continue
            first = r["revision"] is None
            if res is None:
                if first or r["result"] == REOPENED:   # open, rule pending, held: nothing was settled, so nothing changes
                    out["skipped"] += 1
                    continue
                res, actual = REOPENED, None           # it was settled and the pick log took that back
                reason = "the pick log is holding this result for review" if slip_batches._held(p) else "the pick log reopened this result"
            else:
                actual = None if res == "void" else p["actual"]
                if r["result"] == res and r["actual"] == actual:
                    continue
                reason = None if first else "changed in the pick log"
            out["first" if first else "corrected"] += 1
            writes.append({"receipt_id": r["receipt_id"], "revision": 1 if first else r["revision"] + 1, "result": res, "actual": actual,
                           "actual_maps": _text(p["actual_maps"]), "source": _source(p["result"]), "picklog_settled_at": p["settled_at"],
                           "recorded_at": stamp, "supersedes": None if first else r["revision"], "reason": reason, "member": r["member"]})
            if res == "void" and len(voids) < VOID_SIBLING_CHECKS:
                voids.append((r, p))
        for i in range(0, len(writes), WRITE_BATCH):
            chunk = writes[i:i + WRITE_BATCH]

            def op(con, chunk=chunk):
                for w in chunk:
                    con.execute("INSERT OR IGNORE INTO settlements(receipt_id, revision, result, actual, actual_maps, source, picklog_settled_at, "
                                "recorded_at, supersedes, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (w["receipt_id"], w["revision"], w["result"], w["actual"], w["actual_maps"], w["source"], w["picklog_settled_at"],
                                 w["recorded_at"], w["supersedes"], w["reason"]))
                for m in {w["member"] for w in chunk}:
                    self._bump(con, m)
            self.submit(op, wait=30.0)
        marks = [(_mark(p), 0 if p["status"] in FINAL and not slip_batches._held(p) else 1, w["pick_id"], w["last_tracked"]) for w, p, _ in by_pick.values()]

        def done(con):                              # a receipt tracked since this read moved last_tracked: its pick stays due
            con.executemany("UPDATE pick_watch SET mark = ?, active = ? WHERE pick_id = ? AND last_tracked = ?", marks)
        self.submit(done, wait=30.0)

    def _leg_page(self, sport, active):
        """The next page of one tier. A reader that can only answer so many in a pass says so with .page: a longer page would leave
        the same keys unread on every turn."""
        k, limit = (sport, active), getattr(self.sources.get(sport), "page", SETTLE_LEGS)
        with self.reader(wait=5.0) as con:
            rows = [dict(r) for r in con.execute("SELECT settle_key, sport, ref, start_utc, mark, last_tracked FROM leg_watch WHERE sport = ? AND active = ? AND settle_key > ? "
                                                 "ORDER BY settle_key LIMIT ?", (sport, active, self._leg_cursor.get(k, ""), limit))]
        self._leg_cursor[k] = rows[-1]["settle_key"] if len(rows) == limit else ""
        return rows

    def _settle_sport(self, sport, now, out):
        """One sport's page of legs against that sport's own settled results. The reader decides every result: nothing is worked
        out here. A key it does not answer, or answers with no result yet, keeps the last result and comes back next pass."""
        watch = self._leg_page(sport, 1) + self._leg_page(sport, 0)
        for w in watch:
            w["ref"] = json.loads(w["ref"])
        if not watch:
            return
        answers = self.sources[sport](watch) or {}
        changed = []
        for w in watch:
            a = answers.get(w["settle_key"])
            if a and a.get("result") and a.get("mark") != w["mark"]:
                changed.append((w, a))
        for i in range(0, min(len(changed), SETTLE_CHANGED), SETTLE_CHUNK):
            self._apply_legs(changed[i:i + SETTLE_CHUNK], now, out)

    def _apply_legs(self, part, now, out):
        """Same revision rule as _apply: the first answer is revision 1, a different one is a correction naming it, the same one
        writes nothing, and the mark moves only after the writes commit."""
        by_key = {w["settle_key"]: (w, a) for w, a in part}
        keys = list(by_key)
        with self.reader(wait=5.0) as con:
            rows = [dict(r) for r in con.execute(
                "SELECT r.receipt_id, r.member, r.settle_key, s.revision, s.result, s.actual FROM receipts r LEFT JOIN settlements s ON s.receipt_id = r.receipt_id AND "
                "s.revision = (SELECT MAX(x.revision) FROM settlements x WHERE x.receipt_id = r.receipt_id) "
                f"WHERE r.settle_key IN ({','.join('?' * len(keys))})", keys)]
        writes, stamp = [], iso(now)
        for r in rows:
            _, a = by_key[r["settle_key"]]
            out["seen"] += 1
            res, actual = a["result"], None if a["result"] == "void" else a.get("actual")
            first = r["revision"] is None
            if not first and r["result"] == res and r["actual"] == actual:
                continue
            out["first" if first else "corrected"] += 1
            writes.append((r["receipt_id"], 1 if first else r["revision"] + 1, res, actual, _text(a.get("actual_maps")), a.get("source"), a.get("settled_at"), stamp,
                           None if first else r["revision"], None if first else "changed in the settled results", r["member"]))

        def op(con):
            for w in writes:
                con.execute("INSERT OR IGNORE INTO settlements(receipt_id, revision, result, actual, actual_maps, source, picklog_settled_at, recorded_at, supersedes, reason) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)", w[:10])
            for m in {w[10] for w in writes}:
                self._bump(con, m)
        if writes:
            self.submit(op, wait=30.0)
        marks = [(a.get("mark"), 0 if a.get("final") else 1, w["settle_key"], w["last_tracked"]) for w, a in by_key.values()]

        def done(con):
            con.executemany("UPDATE leg_watch SET mark = ?, active = ? WHERE settle_key = ? AND last_tracked = ?", marks)
        self.submit(done, wait=30.0)

    def _prune(self, now):
        open_cut, done_cut = iso(now - timedelta(days=SETTLE_OPEN_DAYS)), iso(now - timedelta(days=SETTLE_DAYS))

        def op(con):
            con.execute("DELETE FROM pick_watch WHERE last_tracked < ? OR (active = 0 AND last_tracked < ?)", (open_cut, done_cut))
            con.execute("DELETE FROM leg_watch WHERE last_tracked < ? OR (active = 0 AND last_tracked < ?)", (open_cut, done_cut))
        self.submit(op, wait=30.0)

    def _void_siblings(self, voids):
        """A voided receipt whose same player, stat, maps and platform settled under another match key (a slug switch after
        tracking). The receipt follows its own row; this is for the owner's diagnostics only."""
        import picklog
        notes = []
        pc = picklog._read_db()
        try:
            for r, p in voids:
                if not p.get("match_start"):
                    continue
                try:
                    t = datetime.fromisoformat(str(p["match_start"]).replace("Z", "+00:00"))
                except ValueError:
                    continue
                lo, hi = (t - timedelta(hours=12)).isoformat(), (t + timedelta(hours=12)).isoformat()
                sib = pc.execute("SELECT id, match_slug, status FROM picks WHERE player = ? AND prop = ? AND COALESCE(scope, '12') = ? "
                                 "AND COALESCE(book, 'underdog') = ? AND id <> ? AND match_slug <> ? AND status IN ('settled', 'push') "
                                 "AND match_start BETWEEN ? AND ? LIMIT 1",
                                 (p["player"], p["prop"], p["scope"] or "12", p["book"] or "underdog", p["id"], p["match_slug"], lo, hi)).fetchone()
                if sib:
                    notes.append({"receipt_id": r["receipt_id"], "pick_id": p["id"], "slug": p["match_slug"], "sibling_pick": sib["id"],
                                  "sibling_slug": sib["match_slug"], "at": iso()})
        finally:
            pc.close()
        return notes

    def _note_voids(self, notes):
        if not notes:
            return

        def op(con):
            cur = con.execute("SELECT v FROM meta WHERE k = 'void_siblings'").fetchone()
            have = json.loads(cur["v"]) if cur else []
            con.execute("INSERT INTO meta(k, v) VALUES('void_siblings', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                        (json.dumps((have + notes)[-DIAG_MAX:]),))
        self.submit(op, wait=30.0)

    def diagnostics(self):
        with self.reader() as con:
            row = con.execute("SELECT v FROM meta WHERE k = 'void_siblings'").fetchone()
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in APPEND_ONLY + ("member_state", "pick_watch", "leg_watch")}
        return {"counts": counts, "void_siblings": json.loads(row["v"]) if row else [], "queue": self._q.qsize()}


def _mark(p):
    """The pick-log state a receipt can read. Any change here brings the pick's receipts back for one look."""
    return hashlib.blake2b(json.dumps([p["status"], p["actual"], p["result"], p["settled_at"], p["match_slug"], p["player"], p["prop"], p["scope"]],
                                      default=str).encode(), digest_size=10).hexdigest()


def _text(v):
    return v if isinstance(v, (str, type(None))) else json.dumps(v)


def _source(result):
    try:
        return (json.loads(result or "{}") or {}).get("source")
    except (TypeError, ValueError, AttributeError):
        return None


# ---- what a member reads
SIDE = {"over": "Over", "under": "Under"}
RESULT = {"hit": "Hit", "miss": "Miss", "push": "Push", "void": "Void"}


def _num(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _status(result):
    return "pending" if result in (None, REOPENED) else result


def _settled_text(s, first):
    if s["result"] == REOPENED:
        return f"Correction added. Back to pending: {s['reason'] or 'the pick log reopened this result'}"
    final = f"Final {_num(s['actual'])}" if s["actual"] is not None else None
    if s["result"] == "void":
        text = "Void. This line was not scored"
    elif s["result"] == "push":
        text = f"Settled: Push. {final}, exactly on the line"
    else:
        text = f"Settled: {RESULT[s['result']]}. {final}"
    return text if first else f"Correction added. {text}"


def receipt_view(r, revisions):
    """A receipt as the member reads it: the frozen fields, then the latest settlement, then every step in order."""
    p = json.loads(r["payload"])
    side = SIDE.get(r["side"], r["side"])
    chance = p.get("displayed_p")
    tracked = f"Tracked {side} {_num(r['line'])}" + (f" at {chance * 100:.1f}%" if chance is not None else "")
    timeline = [{"at": r["tracked_at"], "kind": "tracked", "text": tracked}]
    for s in revisions:
        first = s["revision"] == 1
        timeline.append({"at": s["recorded_at"], "kind": "settled" if first else "correction", "result": s["result"],
                         "text": _settled_text(s, first)})
    status = _status(revisions[-1]["result"]) if revisions else "pending"
    return dict(p, id=r["receipt_id"], slip_id=r["slip_id"], leg_no=r["leg_no"], sport=r["sport"], platform=r["platform"], side=r["side"],
                line=r["line"], line_type=r["line_type"], pick_id=r["pick_id"], forecast_id=r["forecast_id"], match_slug=r["match_slug"],
                offer_revision=r["offer_rev"], row_id=r["row_id"], model_version=r["model_version"], tracked_at=r["tracked_at"],
                start_utc=r["start_utc"], status=status, final=None if status == "pending" else revisions[-1]["actual"],
                corrected=len(revisions) > 1, timeline=timeline, nick=r["nick"], prop=r["prop"], scope=r["scope"])


def _counts(views):
    c = {"hit": 0, "miss": 0, "push": 0, "void": 0, "pending": 0}
    for v in views:
        c[v["status"]] += 1
    return c


def _rate(c):
    d = c["hit"] + c["miss"]
    return dict(c, decided=d, hit_rate=round(c["hit"] / d, 4) if d else None)


def _groups(views, key):
    g = {}
    for v in views:
        g.setdefault(str(v.get(key) or "untagged"), []).append(v)
    return [dict(_rate(_counts(x)), name=k, picks=len(x)) for k, x in sorted(g.items())]


def _sport_groups(views):
    """The member's record per sport: never added together on the page, and a sport with no receipts is not listed."""
    return [dict(_rate(_counts(x)), sport=s, name=SPORT_LABEL[s], picks=len(x), matches=len({v["match_key"] for v in x}))
            for s in SPORTS for x in [[v for v in views if v["sport"] == s]] if x]


def record_of(views, capped=False):
    """The member's own record. Pushes count apart and stay out of the rate, voids and pending are left out. A match is its
    canonical key, so one match under two slugs counts once."""
    c = _rate(_counts(views))
    idents = [(v["nick"], v["prop"], v["scope"], v["match_key"]) for v in views]
    return dict(c, label="Your tracked picks",
                rule="Every pick you tracked, scored on its own line and side. Pushes, voids and pending picks stay out of the rate.",
                picks=len(views), matches=len({v["match_key"] for v in views}), dupes=len(idents) - len(set(idents)),
                scope=f"Newest {RECORD_SCAN} receipts" if capped else "All your receipts")
