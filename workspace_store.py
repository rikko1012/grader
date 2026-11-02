"""Research workspace store: tracked receipts, their settlements, and each member's working slip. DATA_DIR/workspace.db (WAL),
outside .cache so a cache sweep never touches it. Nothing here grades, logs a forecast or writes to picks.db.

Receipts, settlements and slips are append-only: triggers abort any UPDATE or DELETE, a correction is a new settlement
revision naming the one it replaces. member_state is the mutable working draft (slip, preferences, saved views).

Writes go through one bounded queue and one writer thread that group-commits up to WRITE_BATCH operations in one short
transaction (synchronous=FULL, so one fsync per group). The request waits up to WRITE_WAIT seconds for its commit; a full
queue or a timeout raises Busy, the page answers 503 and the slip stays on screen. Retries are safe: receipt ids are
deterministic and inserted with INSERT OR IGNORE.

Settlement has no rule of its own: settle_once passes a receipt's own pick row to slip_batches.leg_result_from_picks, so
the receipt's line and side decide, and one platform's settlement can never settle another platform's leg. An open, held or
reopened pick returns None, which writes nothing and keeps the last result."""
import json
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_MAX = 2000
WRITE_BATCH = 200
WRITE_WAIT = 2.0
READ_SLOTS = 8
READ_WAIT = 1.0
SETTLE_EVERY = 120
SETTLE_LIMIT = 5000
SETTLE_DAYS = 21
SETTLE_OPEN_DAYS = 60
RECORD_SCAN = 2000
PAGE_MAX = 200
SLIP_LIST = PAGE_MAX
DIAG_MAX = 200
VOID_SIBLING_CHECKS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(
  receipt_id TEXT PRIMARY KEY, member TEXT NOT NULL, slip_id TEXT NOT NULL, leg_no INTEGER NOT NULL, tracked_at TEXT NOT NULL,
  sport TEXT NOT NULL CHECK(sport='cs2'), platform TEXT NOT NULL, row_id TEXT NOT NULL, offer_rev TEXT NOT NULL, side TEXT NOT NULL,
  line REAL NOT NULL, line_type TEXT NOT NULL, pick_id INTEGER NOT NULL, forecast_id INTEGER NOT NULL, match_slug TEXT NOT NULL,
  match_id TEXT, nick TEXT NOT NULL, prop TEXT NOT NULL, scope TEXT NOT NULL, start_utc TEXT, model_version TEXT,
  scratch INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS receipts_member ON receipts(member, tracked_at DESC);
CREATE INDEX IF NOT EXISTS receipts_member_match ON receipts(member, match_slug);
CREATE INDEX IF NOT EXISTS receipts_time ON receipts(tracked_at);
CREATE INDEX IF NOT EXISTS receipts_slip ON receipts(slip_id, leg_no);
CREATE TABLE IF NOT EXISTS settlements(
  receipt_id TEXT NOT NULL, revision INTEGER NOT NULL, result TEXT NOT NULL CHECK(result IN('hit','miss','push','void')),
  actual REAL, actual_maps TEXT, source TEXT, picklog_settled_at TEXT, recorded_at TEXT NOT NULL, supersedes INTEGER, reason TEXT,
  PRIMARY KEY(receipt_id, revision));
CREATE TABLE IF NOT EXISTS slips(
  slip_id TEXT PRIMARY KEY, member TEXT NOT NULL, tracked_at TEXT NOT NULL, platform TEXT NOT NULL, entry_type TEXT, sports TEXT NOT NULL,
  legs INTEGER NOT NULL, rules_version TEXT, checks TEXT, scratch INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS slips_member ON slips(member, tracked_at DESC);
CREATE TABLE IF NOT EXISTS member_state(member TEXT PRIMARY KEY, rev INTEGER NOT NULL, updated_at TEXT NOT NULL, slip TEXT, prefs TEXT, views TEXT);
CREATE TABLE IF NOT EXISTS member_rev(member TEXT PRIMARY KEY, rev INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
APPEND_ONLY = ("receipts", "settlements", "slips")


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
        con = self._open()
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
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
        while True:
            try:
                self._readers.get_nowait().close()
            except queue.Empty:
                return

    def _settle_loop(self, every):
        while not self._stop.wait(every):
            try:
                self.settle_once()
            except Exception:                       # the next pass tries again; a failed pass has written nothing
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
        try:
            con.execute("BEGIN IMMEDIATE")
            for op in batch:
                con.execute("SAVEPOINT op")
                try:
                    op.result = op.fn(con)
                    con.execute("RELEASE op")
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
            for op in batch:
                op.result, op.error = None, op.error or e
        finally:
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
    def reader(self, wait=READ_WAIT):
        con = None
        try:
            con = self._readers.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._n_readers < READ_SLOTS:
                    self._n_readers += 1
                    con = self._open()
                    con.execute("PRAGMA query_only=ON")
        if con is None:
            try:
                con = self._readers.get(timeout=wait)
            except queue.Empty:
                raise Busy("no read slot") from None
        try:
            yield con
        finally:
            self._readers.put(con)

    # ---- revisions
    @staticmethod
    def _bump(con, member):
        con.execute("INSERT INTO member_rev(member, rev) VALUES(?, 1) ON CONFLICT(member) DO UPDATE SET rev = rev + 1", (member,))

    def rev(self, member):
        with self.reader() as con:
            r = con.execute("SELECT rev FROM member_rev WHERE member = ?", (member,)).fetchone()
        return r["rev"] if r else 0

    # ---- tracking
    def slip_receipts(self, member, slip_id):
        with self.reader() as con:
            rows = con.execute("SELECT receipt_id, tracked_at FROM receipts WHERE slip_id = ? AND member = ? ORDER BY leg_no",
                               (slip_id, member)).fetchall()
        return [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in rows]

    def tracked_for(self, member, slugs):
        """The member's receipts on these exact matches (bounded by the leg limit), for the duplicate and conflict checks."""
        slugs = sorted({s for s in slugs if s})[:16]
        if not slugs:
            return []
        with self.reader() as con:
            return [dict(r) for r in con.execute(
                f"SELECT receipt_id, row_id, nick, prop, scope, match_slug, platform, line, side FROM receipts "
                f"WHERE member = ? AND match_slug IN ({','.join('?' * len(slugs))}) LIMIT 400", (member, *slugs))]

    def track(self, slip, receipts):
        """One entry, all or nothing. slip: the slips row; receipts: rows with payload (dict). -> {"ok": True, "receipts"} or
        {"ok": False, "duplicate": [...]} when a concurrent press already tracked one of these picks."""
        def op(con):
            have = con.execute("SELECT receipt_id, tracked_at FROM receipts WHERE slip_id = ? ORDER BY leg_no", (slip["slip_id"],)).fetchall()
            if have:
                return {"ok": True, "again": True, "receipts": [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in have]}
            dup = []
            for r in receipts:
                other = con.execute("SELECT receipt_id, line, side FROM receipts WHERE member = ? AND match_slug = ? AND nick = ? AND prop = ? "
                                    "AND scope = ? AND platform = ? LIMIT 1",
                                    (r["member"], r["match_slug"], r["nick"], r["prop"], r["scope"], r["platform"])).fetchone()
                if other:
                    dup.append({"receipt_id": r["receipt_id"], "same": other["line"] == r["line"] and other["side"] == r["side"]})
            if dup:
                return {"ok": False, "duplicate": dup}
            con.execute("INSERT OR IGNORE INTO slips(slip_id, member, tracked_at, platform, entry_type, sports, legs, rules_version, checks, scratch) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (slip["slip_id"], slip["member"], slip["tracked_at"], slip["platform"], slip.get("entry_type"), json.dumps(slip["sports"]),
                         len(receipts), slip.get("rules_version"), json.dumps(slip.get("checks")), int(bool(slip.get("scratch")))))
            for r in receipts:
                con.execute("INSERT OR IGNORE INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, "
                            "line_type, pick_id, forecast_id, match_slug, match_id, nick, prop, scope, start_utc, model_version, scratch, payload) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (r["receipt_id"], r["member"], r["slip_id"], r["leg_no"], r["tracked_at"], r["sport"], r["platform"], r["row_id"],
                             r["offer_rev"], r["side"], r["line"], r["line_type"], r["pick_id"], r["forecast_id"], r["match_slug"], r.get("match_id"),
                             r["nick"], r["prop"], r["scope"], r.get("start_utc"), r.get("model_version"), int(bool(r.get("scratch"))),
                             json.dumps(r["payload"], separators=(",", ":"))))
            self._bump(con, slip["member"])
            return {"ok": True, "receipts": [{"id": r["receipt_id"], "tracked_at": r["tracked_at"]} for r in receipts]}
        return self.submit(op)

    # ---- member state: the working draft. A stale rev is refused with the current copy; the page merges once.
    def get_state(self, member):
        with self.reader() as con:
            r = con.execute("SELECT rev, slip, prefs, views FROM member_state WHERE member = ?", (member,)).fetchone()
        return self._state(r)

    @staticmethod
    def _state(r):
        if r is None:
            return {"rev": 0, "slip": None, "prefs": None, "views": None}
        return {"rev": r["rev"], "slip": json.loads(r["slip"]) if r["slip"] else None, "prefs": json.loads(r["prefs"]) if r["prefs"] else None,
                "views": json.loads(r["views"]) if r["views"] else None}

    def state_rev(self, member):
        with self.reader() as con:
            r = con.execute("SELECT rev FROM member_state WHERE member = ?", (member,)).fetchone()
        return r["rev"] if r else 0

    def put_state(self, member, rev, patch):
        """patch: any of slip, prefs, views. -> {"ok": True, "rev"} or {"ok": False, "state": current}."""
        def op(con):
            cur = con.execute("SELECT rev, slip, prefs, views FROM member_state WHERE member = ?", (member,)).fetchone()
            have = cur["rev"] if cur else 0
            if rev != have:
                return {"ok": False, "state": self._state(cur)}
            keep = {k: (cur[k] if cur else None) for k in ("slip", "prefs", "views")}
            for k in keep:
                if k in patch:
                    keep[k] = json.dumps(patch[k], separators=(",", ":")) if patch[k] is not None else None
            con.execute("INSERT INTO member_state(member, rev, updated_at, slip, prefs, views) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(member) DO UPDATE SET rev = excluded.rev, updated_at = excluded.updated_at, slip = excluded.slip, "
                        "prefs = excluded.prefs, views = excluded.views", (member, have + 1, iso(), keep["slip"], keep["prefs"], keep["views"]))
            return {"ok": True, "rev": have + 1}
        return self.submit(op)

    # ---- reads: a member's receipts
    def mine(self, member, before=None, limit=50):
        """Receipts newest first with the append-only timeline, the record (newest RECORD_SCAN receipts) and slip list."""
        limit = max(1, min(int(limit or 50), PAGE_MAX))
        with self.reader() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM receipts WHERE member = ? ORDER BY tracked_at DESC, leg_no ASC LIMIT ?",
                                                 (member, RECORD_SCAN + 1))]
            total_capped = len(rows) > RECORD_SCAN
            rows = rows[:RECORD_SCAN]
            revs = {}
            for i in range(0, len(rows), 400):
                part = [r["receipt_id"] for r in rows[i:i + 400]]
                for s in con.execute(f"SELECT * FROM settlements WHERE receipt_id IN ({','.join('?' * len(part))}) ORDER BY revision", part):
                    revs.setdefault(s["receipt_id"], []).append(dict(s))
            slips = [dict(r) for r in con.execute("SELECT slip_id, platform, entry_type, sports, tracked_at, legs, scratch FROM slips WHERE member = ? "
                                                  "ORDER BY tracked_at DESC LIMIT ?", (member, SLIP_LIST))]
        views = [receipt_view(r, revs.get(r["receipt_id"], [])) for r in rows]
        by_slip = {}
        for v in views:
            by_slip.setdefault(v["slip_id"], []).append(v)
        slip_out = [{"slip_id": s["slip_id"], "platform": s["platform"], "entry_type": s["entry_type"], "tracked_at": s["tracked_at"],
                     "legs": s["legs"], "counts": _counts(by_slip.get(s["slip_id"], []))} for s in slips]
        rest = [v for v in views if not before or v["tracked_at"] < before]
        cut = limit
        while cut < len(rest) and rest[cut]["tracked_at"] == rest[cut - 1]["tracked_at"]:
            cut += 1                                # a page never splits one slip, whose legs share a time
        page, more = rest[:cut], cut < len(rest)
        return {"record": record_of(views, total_capped), "by_platform": _groups(views, "platform_label"), "by_version": _groups(views, "model_version"),
                "slips": slip_out, "receipts": page, "next_before": page[-1]["tracked_at"] if more and page else None}

    # ---- settlement
    def settle_once(self, now=None):
        """One pass. -> {"seen", "first", "corrected", "skipped"}. A transient read failure raises before anything is written."""
        import picklog
        import slip_batches
        now = now or datetime.now(timezone.utc)
        with self.reader(wait=5.0) as con:
            rows = [dict(r) for r in con.execute(
                "SELECT r.receipt_id, r.member, r.pick_id, r.line, r.side, r.platform, r.nick, r.prop, r.scope, r.start_utc, s.revision, s.result, s.actual "
                "FROM receipts r LEFT JOIN settlements s ON s.receipt_id = r.receipt_id AND "
                "s.revision = (SELECT MAX(x.revision) FROM settlements x WHERE x.receipt_id = r.receipt_id) "
                "WHERE r.tracked_at >= ? OR (r.tracked_at >= ? AND s.receipt_id IS NULL) ORDER BY r.tracked_at DESC LIMIT ?",
                (iso(now - timedelta(days=SETTLE_DAYS)), iso(now - timedelta(days=SETTLE_OPEN_DAYS)), SETTLE_LIMIT))]
        if not rows:
            return {"seen": 0, "first": 0, "corrected": 0, "skipped": 0}
        picks = {}
        ids = sorted({r["pick_id"] for r in rows})
        pc = picklog._read_db()
        try:
            for i in range(0, len(ids), 200):
                part = ids[i:i + 200]
                for p in pc.execute("SELECT id, player, prop, scope, match_slug, status, actual, actual_maps, settled_at, result, line, book, match_start "
                                    f"FROM picks WHERE id IN ({','.join('?' * len(part))})", part):
                    picks[p["id"]] = dict(p)
        finally:
            pc.close()
        writes, out, voids = [], {"seen": len(rows), "first": 0, "corrected": 0, "skipped": 0}, []
        stamp = iso(now)
        for r in rows:
            p = picks.get(r["pick_id"])
            if p is None:
                out["skipped"] += 1
                continue
            leg = {"nick": p["player"], "prop": p["prop"], "scope": p["scope"] or "12", "match_slug": p["match_slug"], "line": r["line"],
                   "side": "More" if r["side"] == "over" else "Less"}
            res = slip_batches.leg_result_from_picks([p])(leg)
            if res is None:                         # open, rule pending, held for review, outage, reopened: nothing changes
                out["skipped"] += 1
                continue
            actual = None if res == "void" else p["actual"]
            if r["result"] == res and r["actual"] == actual:
                continue
            first = r["revision"] is None
            out["first" if first else "corrected"] += 1
            writes.append({"receipt_id": r["receipt_id"], "revision": 1 if first else r["revision"] + 1, "result": res, "actual": actual,
                           "actual_maps": _text(p["actual_maps"]), "source": _source(p["result"]), "picklog_settled_at": p["settled_at"],
                           "recorded_at": stamp, "supersedes": None if first else r["revision"], "reason": None if first else "changed in the pick log",
                           "member": r["member"]})
            if res == "void" and len(voids) < VOID_SIBLING_CHECKS:
                voids.append((r, p))
        notes = self._void_siblings(voids) if voids else []
        for i in range(0, len(writes), WRITE_BATCH):
            part = writes[i:i + WRITE_BATCH]

            def op(con, part=part):
                for w in part:
                    con.execute("INSERT OR IGNORE INTO settlements(receipt_id, revision, result, actual, actual_maps, source, picklog_settled_at, "
                                "recorded_at, supersedes, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (w["receipt_id"], w["revision"], w["result"], w["actual"], w["actual_maps"], w["source"], w["picklog_settled_at"],
                                 w["recorded_at"], w["supersedes"], w["reason"]))
                for m in {w["member"] for w in part}:
                    self._bump(con, m)
            self.submit(op, wait=30.0)
        if notes:
            self._note_voids(notes)
        return out

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
                t = datetime.fromisoformat(str(p["match_start"]).replace("Z", "+00:00"))
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
        def op(con):
            cur = con.execute("SELECT v FROM meta WHERE k = 'void_siblings'").fetchone()
            have = json.loads(cur["v"]) if cur else []
            con.execute("INSERT INTO meta(k, v) VALUES('void_siblings', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                        (json.dumps((have + notes)[-DIAG_MAX:]),))
        self.submit(op, wait=30.0)

    def diagnostics(self):
        with self.reader() as con:
            row = con.execute("SELECT v FROM meta WHERE k = 'void_siblings'").fetchone()
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in APPEND_ONLY + ("member_state",)}
        return {"counts": counts, "void_siblings": json.loads(row["v"]) if row else [], "queue": self._q.qsize()}


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


def _settled_text(s, first):
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
    last = revisions[-1] if revisions else None
    return dict(p, id=r["receipt_id"], slip_id=r["slip_id"], leg_no=r["leg_no"], sport=r["sport"], platform=r["platform"], side=r["side"],
                line=r["line"], line_type=r["line_type"], pick_id=r["pick_id"], forecast_id=r["forecast_id"], match_slug=r["match_slug"],
                offer_revision=r["offer_rev"], row_id=r["row_id"], model_version=r["model_version"], tracked_at=r["tracked_at"],
                start_utc=r["start_utc"], status=last["result"] if last else "pending", final=last["actual"] if last else None,
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


def record_of(views, capped=False):
    """The member's own record. Pushes count apart and stay out of the rate, voids and pending are left out."""
    c = _rate(_counts(views))
    idents = [(v["nick"], v["prop"], v["scope"], v["match_slug"]) for v in views]
    return dict(c, label="Your tracked picks",
                rule="Every pick you tracked, scored on its own line and side. Pushes, voids and pending picks stay out of the rate.",
                picks=len(views), matches=len({v["match_slug"] for v in views}), dupes=len(idents) - len(set(idents)),
                scope=f"Newest {RECORD_SCAN} receipts" if capped else "All your receipts")
