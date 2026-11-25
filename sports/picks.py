"""Member pick tracking pk-1 (owner 2026-10-24): prospective picks recorded against their EXACT offer revision and forecast,
settled against real results at the pick's OWN line. Stakes are always 0; the research grade shown at tracking time is recorded (grade_shown, rg-1) here
(labelled 'Research grade - not validated'; the validation state text is shown too). No EV / returns.

A pick = an explicit member action BEFORE the game starts: "Track" on the board / player card, or a slip copy that the
server validated. check() refuses it unless, right now (live store read, same rule as the slip copy check):
  pick'em platform (PrizePicks / Underdog); the offer record is the CURRENT revision of that offer (else "changed");
  open; the side offered; capabilities.gate passes (it can be settled); seen by ingestion within STALE_S; the game has
  not started (event start, else the platform's listed start; unknown start = refused); the forecast (if any) was made
  for this offer (this revision, or an earlier revision of the same offer at the same line / stat) and the displayed
  chances equal the forecast record's unconditional p_over / p_under / p_push (to 0.05 points).

Write path (sports/RUNBOOK.md 5b): the handler only SPOOLS (bounded queue SPOOL_QUEUE, full = dropped + counted, never
blocking) -> one writer thread -> <SPORTS_DATA_DIR>/picks_spool/<UTC date>.jsonl (SPOOL_DAY_MAX_MB per day). The worker
(`python -m sports.picks ingest|settle`, scheduler jobs picks-ingest / picks-settle) loads it into
<SPORTS_DATA_DIR>/picks.db idempotently (pick_id = member + offer revision + side; INSERT OR IGNORE; byte offsets) and
settles each pick from the canonical leg settlement (sports/settlement.at_line at the pick's line). A corrected result
is a NEW settlement revision naming the one it supersedes; rows are append-only (triggers).

Member ids: the token subject (already pseudonymous, never the Whop id) hashed again (sha256 "picks|" prefix).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import capabilities, config, store
from .contracts import parse_utc

VERSION = "pk-1"
PLATFORMS = ("prizepicks", "underdog")
SURFACES = ("board", "card", "slip_copy")
STALE_S = 25 * 60                       # same freshness rule as the slip pool (sports/slips/pool.py STALE_S)
DISPLAY_TOL = 5e-4                      # displayed chance vs the forecast record (0.05 points; shown to 0.1)
SPOOL_QUEUE = 2000
SPOOL_DAY_MAX_MB = 20
MAX_PER_REQUEST = 8
NOT_PLAYING = ("postponed", "cancelled", "delayed", "suspended")
STATS = {"spooled": 0, "dropped_queue_full": 0, "dropped_day_cap": 0, "written": 0, "write_errors": 0,
         "refused": 0}
_Q = queue.Queue(maxsize=SPOOL_QUEUE)
_LOCK = threading.Lock()
_WRITER = None


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__(code)
        self.code, self.text = code, text


def clock():
    return datetime.now(timezone.utc)


def now_s(now=None):
    return (now or clock()).strftime("%Y-%m-%dT%H:%M:%SZ")


def scratch_reason(role, item):
    """Why a pick is a TEST record (kept apart from member measurement), or None for a member pick."""
    if role == "admin":
        return "admin"
    if item.get("test") is True:
        return "marked test"
    if os.environ.get("SPORTS_PICKS_SCRATCH", "").strip().lower() in ("1", "true", "yes", "on"):
        return "SPORTS_PICKS_SCRATCH"
    return None


def member_hash(member):
    return hashlib.sha256(f"picks|{member}".encode()).hexdigest()[:16] if member else None


def pick_id(mhash, record_id, side):
    return "pk-" + hashlib.sha256(f"{mhash}|{record_id}|{side}".encode()).hexdigest()[:20]


def spool_dir() -> Path:
    return config.DATA_DIR / "picks_spool"


def db_path() -> Path:
    return config.DATA_DIR / "picks.db"


# --------------------------------------------------------------------------------------------- the live check
def _event(con, event_id):
    if not event_id:
        return None
    rows = store._latest_rows(con, "event", "event_id = ?", [event_id], None, 1)
    return store._rec(rows[0]) if rows else None


def _validation_state(fc):
    for c in fc.reason_codes or ():
        if isinstance(c, str) and c.startswith("gp:"):
            return c[3:]
    return "unvalidated"


def _num(x):
    if x is None:
        return None
    v = float(x)
    if not math.isfinite(v) or v < 0 or v > 1:
        raise ValueError("chances are numbers in [0, 1]")
    return v


GRADES = ("A", "B", "C", "Lean")
GRADE_RULE = "rg-1"


def research_grade(fc, side, league, probs=None, rid=None):
    """The board's research letter (sports/board_view rg-1) this forecast can carry on `side`: the lean rule (chance >
    50% and the mean on that side), no vg-1 hold while containment is on, no sg-2 hold (the ratio / floor / certain
    rules re-checked here, the history rules read from the board's last sanity.json for this record); else None. The
    board's market (N4) hold is applied by the page before it shows a letter - a letter it did show must equal this.
    probs = the DISPLAYED chances ({p_over, p_under}: the quote's cal-mi-1 chance when the board showed one)."""
    from . import board_view
    if fc.p_over is None or fc.p_under is None or fc.model_version not in board_view.scout_models():
        return None
    po, pu = (probs["p_over"], probs["p_under"]) if probs else (fc.p_over, fc.p_under)
    line = float(fc.line)
    if board_view.lean_of(po, pu, fc.projection_mean, line) != side:
        return None
    if board_view.contain_reason(league, fc.stat):
        return None
    if board_view.sanity_check(fc.projection_mean, line, side, chances=(po, pu, fc.p_over, fc.p_under)) is not None:
        return None
    if rid is not None and held_quote(league, rid, fc.forecast_id):
        return None
    from . import grade_rules                           # mvlean-1 / os-2: the letter the board showed on this quote
    return grade_rules.letter_for(league, rid, side, board_view.research_grade(po if side == "over" else pu, league, fc.stat))


_HELD = {}


def held_quote(league, rid, fid):
    """The sg-2 reason the last snapshot held this quote + forecast with (snapshots sanity.json "keys"), or None.
    Cached per league like cal_quote: the file's mtime is re-checked every CAL_CHECK_S seconds."""
    import json as _json
    import time as _t
    from . import snapshots
    now = _t.time()
    c = _HELD.get(league)
    if c is None or now - c[0] > CAL_CHECK_S:
        p = snapshots.snap_root() / league / "sanity.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            mt = None
        if c is not None and c[1] == mt:
            _HELD[league] = (now, mt, c[2])
        else:
            idx = {}
            if mt is not None:
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                    keys = d.get("keys") if isinstance(d.get("keys"), list) else \
                        [[e.get("record_id"), e.get("forecast_id"), e.get("reason")] for e in d.get("entries") or ()]
                    idx = {(k[0], k[1]): k[2] for k in keys if isinstance(k, list) and len(k) == 3}
                    from . import availability
                    for (r_, _f), c_ in list(idx.items()):  # av-1 holds the LINE, whichever forecast a pick uses
                        if c_ in availability.CODES:
                            idx[(r_, None)] = c_
                except (OSError, ValueError, AttributeError):
                    idx = {}
            _HELD[league] = (now, mt, idx)
        c = _HELD[league]
    return c[2].get((rid, fid)) or c[2].get((rid, None))      # (rid, None) = an av-1 hold of the whole line


_CAL = {}
CAL_CHECK_S = 10.0


def cal_quote(league, rid, fid):
    """The cal-mi-1 chance the last snapshot showed on this quote for this forecast (snapshots cal.json), or None.
    Cached per league; the file's mtime is re-checked every CAL_CHECK_S seconds (cheap under load)."""
    import time as _t
    from . import snapshots
    now = _t.time()
    c = _CAL.get(league)
    if c is None or now - c[0] > CAL_CHECK_S:
        p = snapshots.snap_root() / league / "cal.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            mt = None
        if c is not None and c[1] == mt:
            _CAL[league] = (now, mt, c[2])
        else:
            _CAL[league] = (now, mt, snapshots.read_cal_index(league) if mt is not None else {})
        c = _CAL[league]
    ca = c[2].get(rid)
    return ca if ca and ca.get("f") == fid else None


def check(con, item, now=None):
    """-> the pick record (dict) or raise Refused(code, text). ValueError for a malformed item."""
    now = now or clock()
    t = now_s(now)
    platform, rid, side = item.get("platform"), item.get("record_id"), item.get("side")
    if side not in ("over", "under"):
        raise ValueError("side must be over or under")
    if not isinstance(rid, str) or not rid or len(rid) > 300:
        raise ValueError("record_id expected")
    surface = item.get("surface") or "board"
    if surface not in SURFACES:
        raise ValueError("surface must be board, card or slip_copy")
    if platform not in PLATFORMS:
        raise Refused("not_pickem", "Picks are tracked on PrizePicks and Underdog lines")
    o = store.get(con, rid)
    if o is None or getattr(o, "KIND", None) != "offer" or o.platform != platform:
        raise Refused("gone", "This line is no longer on the board")
    cur = store.current_record_id(con, "offer", platform, o.offer_id)
    if cur is None:
        raise Refused("gone", "This line is no longer on the board")
    if cur != rid:
        raise Refused("changed", "The line changed since the board was built - refresh and check it again")
    if o.availability != "open":
        raise Refused("not_open", f"The offer is {o.availability}")
    q = o.side(side)
    if q is None or not q.offered:
        raise Refused("side_not_offered", f"{side.title()} is not offered on this line")
    ok, why = capabilities.gate(o)
    if not ok:
        raise Refused("not_gradable", plain_gate_reason(why))
    ev = _event(con, o.event_id)
    start = ev.start_utc if ev is not None and ev.start_utc else (
        o.listed_start_utc if o.listed_time_kind == "start" else None)
    if not start:
        raise Refused("start_unknown", "The game's start time is not known, so the pick cannot be time-stamped")
    if ev is not None and ev.status in NOT_PLAYING and start > t:     # postponed is not 'started' (same code)
        raise Refused("started", f"The game is {ev.status} - picks are recorded for scheduled games only")
    if (ev is not None and ev.status != "scheduled") or start <= t:
        raise Refused("started", "The game has started - picks are recorded before the start only")
    meta = store.row_meta(con, rid) or {}
    last = max(x for x in (meta.get("last_seen_at"), o.observed_at) if x)
    age = (now - parse_utc(last)).total_seconds()
    if age > STALE_S:
        raise Refused("stale", f"Price not fresh: last seen {int(age // 60)} min ago")
    fid, fc, ca = item.get("forecast_id"), None, None
    shown = item.get("shown") or {}
    if not isinstance(shown, dict):
        raise ValueError("shown must be an object")
    shown = {k: _num(shown.get(k)) for k in ("p_over", "p_under", "p_push")}
    if fid:
        if not isinstance(fid, str) or len(fid) > 200:
            raise ValueError("forecast_id must be a string")
        fc = store.get(con, fid)
        if fc is None or getattr(fc, "KIND", None) != "forecast":
            raise Refused("forecast_mismatch", "That forecast is not on record")
        same_offer = rid in tuple(fc.offer_snapshot_ids or ())
        if not same_offer:
            for x in fc.offer_snapshot_ids or ():
                m = store.row_meta(con, x)
                if m and m.get("source") == platform and m.get("source_key") == o.offer_id:
                    same_offer = True
                    break
        # audit 2026-11-02: player, stat, line and a linked offer are not enough - event, league and period must match
        if not same_offer or fc.event_id != o.event_id or fc.league != o.league or fc.scope != o.scope \
                or fc.stat != o.stat or abs(float(fc.line) - float(o.line)) > 1e-9 \
                or fc.player_id != o.player_id:
            raise Refused("forecast_mismatch", "The chance shown was made for another line - refresh")
        rec_p = {"p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push}
        ca = cal_quote(o.league, rid, fc.forecast_id)
        if ca:                                        # cal-mi-1 (REGISTER A8): the board showed the calibrated chance
            rec_p = {"p_over": ca["po"], "p_under": ca["pu"], "p_push": ca["pp"]}
        for k, v in shown.items():
            if v is not None and abs(v - rec_p[k]) > DISPLAY_TOL:
                raise Refused("display_mismatch", "The chance shown differs from Grader's record - refresh")
        displayed = rec_p
    else:
        if any(v is not None for v in shown.values()):
            raise Refused("display_mismatch", "A chance was shown without a forecast record - refresh")
        displayed = None
    shown_grade = item.get("grade")
    if shown_grade is not None and shown_grade not in GRADES:
        raise ValueError("grade must be A, B, C or Lean")
    expect = research_grade(fc, side, o.league, displayed, rid) if fc else None
    mismatch = None
    if shown_grade is not None and shown_grade != expect:      # never a letter the forecast record cannot carry
        mismatch, shown_grade = shown_grade, None
    return {"version": VERSION, "tracked_at": t, "surface": surface, "league": o.league, "platform": platform,
            "offer_id": o.offer_id, "record_id": rid, "revision": meta.get("revision"),
            "offer_observed_at": o.observed_at, "offer_last_seen_at": last, "event_id": o.event_id,
            "event_ref": o.event_ref, "start_utc": start, "player_id": o.player_id, "player": o.player_name,
            "team": o.team, "stat": o.stat, "stat_label": o.source_stat, "scope": o.scope, "line": o.line,
            "side": side, "variant": o.variant, "multiplier": q.multiplier,
            "forecast_id": fc.forecast_id if fc else None, "model_version": fc.model_version if fc else None,
            "artifact_version": fc.artifact_version if fc else None,
            "calibration_version": fc.calibration_version if fc else None,
            "grade_policy_version": fc.grade_policy_version if fc else None,
            "forecast_at": fc.forecast_at if fc else None, "research_only": bool(fc.research_only) if fc else None,
            "validation_state": _validation_state(fc) if fc else None, "displayed": displayed,
            "displayed_source": ((str(ca.get("v") or "cal-mi-1").split(":")[0]) if fc and ca else "scout_forecast")
            if fc else None,
            **({"calibration": {"v": ca["v"], "mode": ca["mode"], "raw": ca["raw"]}} if fc and ca else {}), "stake_units": 0, "grade_shown": shown_grade,
            "grade_rule": GRADE_RULE if shown_grade else None,
            **({"grade_not_recorded": mismatch} if mismatch else {})}


# ------------------------------------------------------------------------------------------------------- spool
def spool(rec):
    """Queue one pick for the spool file. Never blocks; never raises. -> True if queued."""
    try:
        _Q.put_nowait(dict(rec, kind="pick"))
    except queue.Full:
        with _LOCK:
            STATS["dropped_queue_full"] += 1
        return False
    with _LOCK:
        STATS["spooled"] += 1
    _ensure_writer()
    return True


def _ensure_writer():
    global _WRITER
    with _LOCK:
        if _WRITER is None or not _WRITER.is_alive():
            _WRITER = threading.Thread(target=_write_loop, name="picks-spool", daemon=True)
            _WRITER.start()


def _write_loop():
    while True:
        batch = [_Q.get()]
        while len(batch) < 200:
            try:
                batch.append(_Q.get_nowait())
            except queue.Empty:
                break
        try:
            flush(batch)
        except Exception:                               # noqa: BLE001 - logging never breaks the service
            with _LOCK:
                STATS["write_errors"] += len(batch)
        finally:
            for _ in batch:
                _Q.task_done()


def flush(batch):
    d = spool_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    size = f.stat().st_size if f.exists() else 0
    lines = []
    for r in batch:
        s = json.dumps(r, separators=(",", ":"), default=str) + "\n"
        if size + len(s) > SPOOL_DAY_MAX_MB * 1e6:
            with _LOCK:
                STATS["dropped_day_cap"] += 1
            continue
        size += len(s)
        lines.append(s)
    if lines:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("".join(lines))
        with _LOCK:
            STATS["written"] += len(lines)


def drain(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if _Q.unfinished_tasks == 0:
            return True
        time.sleep(0.02)
    return False


def stats():
    with _LOCK:
        return dict(STATS, queue=_Q.qsize(), queue_max=SPOOL_QUEUE, day_cap_mb=SPOOL_DAY_MAX_MB)


# -------------------------------------------------------------------------------------------- worker: picks.db
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS picks (pick_id TEXT PRIMARY KEY, member TEXT NOT NULL, tracked_at TEXT NOT NULL,
         league TEXT, platform TEXT NOT NULL, offer_id TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER,
         event_id TEXT, side TEXT NOT NULL, line REAL NOT NULL, forecast_id TEXT, surface TEXT, payload TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS picks_member ON picks (member, tracked_at)",
    """CREATE TABLE IF NOT EXISTS settlements (pick_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
         outcome TEXT, value REAL, settlement_id TEXT, settlement_revision INTEGER, settled_at TEXT NOT NULL,
         supersedes INTEGER, PRIMARY KEY (pick_id, revision))""",
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS picks_ro BEFORE UPDATE ON picks BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS picks_nodel BEFORE DELETE ON picks BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS psettle_ro BEFORE UPDATE ON settlements BEGIN SELECT RAISE(ABORT, 'immutable'); END",
]


def connect(path=None):
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    for s in SCHEMA:
        con.execute(s)
    con.commit()
    return con


def connect_ro(path=None):
    p = Path(path) if path else db_path()
    if not p.exists():
        raise FileNotFoundError(p)
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


_PICK_FIELDS = ("pick_id", "member", "tracked_at", "platform", "offer_id", "record_id", "side", "line")


def ingest(con=None, directory=None):
    """Load every complete spool line not loaded yet. -> counts. Idempotent (pick_id) and resumable (offsets)."""
    own = con is None
    con = con or connect()
    d = Path(directory) if directory else spool_dir()
    c = {"lines": 0, "picks": 0, "duplicates": 0, "bad": 0}
    try:
        for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
            row = con.execute("SELECT v FROM meta WHERE k=?", (f"offset:{f.name}",)).fetchone()
            off = int(row[0]) if row else 0
            with open(f, "rb") as fh:
                fh.seek(off)
                data = fh.read()
            end = data.rfind(b"\n") + 1
            for raw in data[:end].splitlines():
                c["lines"] += 1
                try:
                    r = json.loads(raw)
                    if r.get("kind") != "pick" or any(r.get(k) in (None, "") for k in _PICK_FIELDS):
                        raise ValueError
                    cur = con.execute("INSERT OR IGNORE INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (r["pick_id"], r["member"], r["tracked_at"], r.get("league"), r["platform"],
                                       r["offer_id"], r["record_id"], r.get("revision"), r.get("event_id"), r["side"],
                                       float(r["line"]), r.get("forecast_id"), r.get("surface"),
                                       json.dumps(r, default=str, sort_keys=True)))
                except (ValueError, TypeError, KeyError):
                    c["bad"] += 1
                    continue
                c["picks" if cur.rowcount else "duplicates"] += 1
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"offset:{f.name}", str(off + end)))
            con.commit()
    finally:
        if own:
            con.close()
    return c


def latest_settlement(con, pid):
    return con.execute("SELECT * FROM settlements WHERE pick_id=? ORDER BY revision DESC LIMIT 1", (pid,)).fetchone()


def leg_settlement(store_con, platform, offer_id, side):
    """The canonical leg settlement for the pick's side; else the other side's (same box-score value - at_line reads
    the pick's side and line from it). None while unsettled."""
    s = store.latest(store_con, "settlement", platform, f"{offer_id}|{side}")
    if s is None:
        s = store.latest(store_con, "settlement", platform, f"{offer_id}|{'under' if side == 'over' else 'over'}")
    return s


SETTLE_WINDOW_DAYS = 21        # a pick is re-read for provider corrections this long (games <= 8 days out + 4 days)
SETTLE_GIVE_UP_DAYS = 60       # a pick with no decided result is retried this long


def settle_window(newest, now=None, window_days=SETTLE_WINDOW_DAYS, give_up_days=SETTLE_GIVE_UP_DAYS):
    """(recheck_from, give_up_before) ISO cutoffs for the settle scans, measured back from the NEWEST record (or now
    when earlier): rows newer than recheck_from are always re-read; older rows only while undecided and newer than
    give_up_before. Bounds the hourly settle job as picks / slips accumulate (was: every row ever, every run)."""
    from datetime import timedelta
    ref = min(newest, now_s(now)) if newest else now_s(now)
    t = parse_utc(ref)
    f = "%Y-%m-%dT%H:%M:%SZ"
    return (t - timedelta(days=window_days)).strftime(f), (t - timedelta(days=give_up_days)).strftime(f)


def settle_all(store_con, con=None, settled_at=None, now=None):
    """Worker job: settle every pick at its own line; a changed result = a new revision. -> counts.
    Scans picks tracked within SETTLE_WINDOW_DAYS of the newest pick, plus older ones still undecided (no win / loss /
    push / void yet) up to SETTLE_GIVE_UP_DAYS; a decided pick older than the window is final (skipped_final)."""
    from .settlement import at_line
    own = con is None
    con = con or connect()
    c = {"picks": 0, "pending": 0, "inserted": 0, "revision": 0, "unchanged": 0, "skipped_final": 0}
    try:
        recheck, give_up = settle_window(con.execute("SELECT MAX(tracked_at) FROM picks").fetchone()[0], now)
        rows = con.execute(
            "SELECT pick_id, platform, offer_id, side, line FROM picks p WHERE p.tracked_at >= ? OR "
            "(p.tracked_at >= ? AND NOT EXISTS (SELECT 1 FROM settlements s WHERE s.pick_id = p.pick_id AND "
            "s.status IN ('win', 'loss', 'push', 'void')))", (recheck, give_up)).fetchall()
        c["skipped_final"] = con.execute("SELECT COUNT(*) FROM picks").fetchone()[0] - len(rows)
        for p in rows:
            c["picks"] += 1
            s = leg_settlement(store_con, p["platform"], p["offer_id"], p["side"])
            status, outcome = at_line(s, p["line"], p["side"])
            if status is None:
                c["pending"] += 1
                continue
            value = getattr(s, "source_value", None)
            prev = latest_settlement(con, p["pick_id"])
            if prev is not None and (prev["status"], prev["outcome"], prev["value"]) == (status, outcome, value):
                c["unchanged"] += 1
                continue
            rev = 1 if prev is None else prev["revision"] + 1
            con.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?,?)",
                        (p["pick_id"], rev, status, outcome, value, s.settlement_id, s.revision,
                         settled_at or now_s(), prev["revision"] if prev is not None else None))
            c["inserted" if prev is None else "revision"] += 1
        con.commit()
    finally:
        if own:
            con.close()
    return c


# ------------------------------------------------------------------------------------------------------ views
def _state_text(state):
    from .model import grade_policy
    if state is None:
        return "No Grader chance on this line"
    if state == grade_policy.STATES[2]:                 # the policy's own name for a passed forward gate
        return "Validated on live lines"
    return grade_policy.TEXT_HISTORY if state == grade_policy.STATES[1] else grade_policy.TEXT_UNVALIDATED


def _view(row, st):
    r = json.loads(row["payload"])
    d = r.get("displayed")
    p_side = None if not d else (d["p_over"] if r["side"] == "over" else d["p_under"])
    return {"pick_id": r["pick_id"], "tracked_at": r["tracked_at"], "surface": r.get("surface"), "league": r.get("league"),
            "platform": r["platform"], "player": r.get("player"), "team": r.get("team"), "event_ref": r.get("event_ref"),
            "start_utc": r.get("start_utc"), "stat": r.get("stat"), "stat_label": r.get("stat_label"),
            "line": r["line"], "side": r["side"], "record_id": r["record_id"], "revision": r.get("revision"),
            "forecast_id": r.get("forecast_id"), "model_version": r.get("model_version"), "displayed": d,
            "displayed_source": r.get("displayed_source"), "forecast_at": r.get("forecast_at"),
            "event_id": r.get("event_id"), "variant": r.get("variant"),
            "p_side": p_side, "validation_state": r.get("validation_state"),
            "grade_shown": r.get("grade_shown"),
            "validation_text": _state_text(r.get("validation_state")), "stake_units": 0, "test": bool(r.get("scratch")),
            "result": None if st is None else {"status": st["status"], "outcome": st["outcome"], "value": st["value"],
                                               "revision": st["revision"],
                                               "settled_at": st["settled_at"], "corrected": st["revision"] > 1}}


RECORD_SCAN = 2000                                       # per-member rows read for the W-L-P record (bounded)


def _row_league(row, payload):
    lg = row["league"] if "league" in row.keys() else None
    return lg or payload.get("league")


def mine(con, member, limit=200, league=None, include_test=False, moves_opener=None):
    """The member's own picks, newest first. league: "nba" | "nfl" | None (all). Test / scratch records (scratch_reason:
    admin token, marked test, SPORTS_PICKS_SCRATCH preview) are HIDDEN unless include_test (the API allows that for an
    admin token only); hidden_test counts them. record: W-L-P per league over the member's non-test picks (no money,
    no EV, no returns). moves_opener (SCOUT_LINE_MOVE_NOTES, sports/line_moves.annotate): a settled pick whose offer was bumped
    between its opening and closing line gains `line_move` - wording only, its result and the record unchanged."""
    mh = member_hash(member)
    rows = con.execute("SELECT * FROM picks WHERE member=? ORDER BY tracked_at DESC LIMIT ?", (mh, RECORD_SCAN)).fetchall()
    record, hidden, kept, graded = {}, 0, [], []
    for r in rows:
        pl = json.loads(r["payload"])
        lg = _row_league(r, pl)
        test = bool(pl.get("scratch"))
        if test and not include_test:
            hidden += 1 if league in (None, lg) else 0
            continue
        if not test:
            st = latest_settlement(con, r["pick_id"])
            c = record.setdefault(lg or "unknown", {"win": 0, "loss": 0, "push": 0, "void": 0, "open": 0})
            k = st["status"] if st is not None and st["status"] in ("win", "loss", "push", "void") else "open"
            c[k] += 1
            if league is None or lg == league:
                graded.append({"pick_id": r["pick_id"], "event_id": pl.get("event_id"), "grade": pl.get("grade_shown"),
                               "status": st["status"] if st is not None else None})
        if league is None or lg == league:
            kept.append(r)
    for c in record.values():
        c["decided"] = c["win"] + c["loss"]
        c["hit_rate"] = round(c["win"] / c["decided"], 4) if c["decided"] else None
    out = [_view(r, latest_settlement(con, r["pick_id"])) for r in kept[:limit]]
    for v, r in zip(out, kept):
        if not v.get("league"):
            v["league"] = r["league"]
    if moves_opener is not None:
        from . import line_moves
        line_moves.annotate(out, [(r["platform"], r["offer_id"]) for r in kept[:limit]], moves_opener)
    by_grade = grade_table(graded)
    return {"version": VERSION, "picks": out, "total": len(kept), "shown": len(out), "stake_units": 0,
            "by_grade": by_grade,
            "league": league, "hidden_test": hidden, "include_test": bool(include_test), "record": record,
            "record_scope": f"newest {RECORD_SCAN} picks" if len(rows) >= RECORD_SCAN else "all picks"}


def _cluster_ci(items, stat, reps=1000, seed=7):
    """95% percentile interval of stat() over a bootstrap that resamples GAMES (event_id clusters), not picks."""
    groups = {}
    for it in items:
        groups.setdefault(it["event_id"] or it["pick_id"], []).append(it)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [x for _k in range(len(keys)) for x in groups[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < reps // 2:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


GRADE_TABLE_LABEL = "Grader grade: hit rate by the grade shown when each pick was tracked"


def grade_table(items):
    """Per research grade (A / B / C / Lean, the letter recorded at tracking time): n settled (win / loss), W-L-P, hit
    rate and its game-clustered 95% interval. items: {pick_id, event_id, grade, status}. Research only."""
    rows = []
    for g in GRADES:
        xs = [x for x in items if x.get("grade") == g]
        settled = [x for x in xs if x["status"] in ("win", "loss")]
        h = _hit(settled)
        rows.append({"grade": g, "picks": len(xs), "n": len(settled),
                     "wins": sum(1 for x in xs if x["status"] == "win"),
                     "losses": sum(1 for x in xs if x["status"] == "loss"),
                     "pushes": sum(1 for x in xs if x["status"] == "push"),
                     "hit_rate": None if h is None else round(h, 4),
                     "hit_rate_ci95_games": _cluster_ci(settled, _hit)})
    return {"label": GRADE_TABLE_LABEL, "rule": GRADE_RULE, "rows": rows,
            "ungraded": sum(1 for x in items if x.get("grade") not in GRADES)}


def _hit(xs):
    w = sum(1 for x in xs if x["status"] == "win")
    l = sum(1 for x in xs if x["status"] == "loss")
    return w / (w + l) if w + l else None


def _pc(x):
    """Displayed chance of the chosen side given no push (win / loss only)."""
    d = x["displayed"]
    ps, po = (d["p_over"], d["p_under"]) if x["side"] == "over" else (d["p_under"], d["p_over"])
    return ps / (ps + po) if ps + po > 0 else None


def _brier(xs):
    v = [(_pc(x) - (1.0 if x["status"] == "win" else 0.0)) ** 2 for x in xs if _pc(x) is not None]
    return sum(v) / len(v) if v else None


def _logloss(xs):
    v = []
    for x in xs:
        p = _pc(x)
        if p is None:
            continue
        p = min(max(p, 1e-6), 1 - 1e-6)
        v.append(-math.log(p if x["status"] == "win" else 1 - p))
    return sum(v) / len(v) if v else None


def _group(items):
    settled = [x for x in items if x["status"] in ("win", "loss")]
    with_p = [x for x in settled if x["displayed"]]
    r4 = (lambda v: None if v is None else round(v, 4))
    return {"picks": len(items), "members": len({x["member"] for x in items}),
            "games": len({x["event_id"] for x in items if x["event_id"]}),
            "wins": sum(1 for x in items if x["status"] == "win"),
            "losses": sum(1 for x in items if x["status"] == "loss"),
            "pushes": sum(1 for x in items if x["status"] == "push"),
            "voids": sum(1 for x in items if x["status"] == "void"),
            "unresolved": sum(1 for x in items if x["status"] == "unresolved"),
            "pending": sum(1 for x in items if x["status"] is None),
            "hit_rate": r4(_hit(settled)), "hit_rate_ci95_games": _cluster_ci(settled, _hit),
            "with_displayed_chance": len(with_p),
            "mean_displayed_chance": r4(sum(_pc(x) for x in with_p) / len(with_p)) if with_p else None,
            "brier": r4(_brier(with_p)), "brier_ci95_games": _cluster_ci(with_p, _brier),
            "log_loss": r4(_logloss(with_p)), "log_loss_ci95_games": _cluster_ci(with_p, _logloss)}


def plain_gate_reason(why):
    """capabilities.gate reasons -> one plain member sentence (audit 2026-11-05: no internal wording such as
    'variant one_sided' on member pages). The full reason list stays in the offer view for the owner."""
    w = " ".join(why or ()).lower()
    if "one-sided" in w:                                # owner 2026-11-06: Over only / Under only lines are not graded
        return "Grader doesn't grade or track this pick: the line offers one side only."
    if "scope" in w:
        part = "it is a quarter or half line, and Grader only tracks full-game lines"
    elif "promotional" in w:
        part = "it is a promotional line"
    elif "variant" in w:
        part = "it pays differently from the standard line"
    elif "not resolved" in w:
        part = "Grader could not match the game or the player"
    elif "no side offered" in w or "offer " in w:
        part = "the line is not open"
    else:
        part = "Grader does not track this stat"
    return "Grader can't track this pick: " + part + "."


def aggregate(con):
    items = []
    for r in con.execute("SELECT pick_id, member, payload FROM picks").fetchall():
        p = json.loads(r["payload"])
        st = latest_settlement(con, r["pick_id"])
        items.append({"pick_id": r["pick_id"], "member": r["member"], "event_id": p.get("event_id"),
                      "side": p["side"], "displayed": p.get("displayed"), "status": st["status"] if st else None,
                      "platform": p["platform"], "league": p.get("league"),
                      "state": p.get("validation_state") or "no_chance", "surface": p.get("surface"),
                      "grade": p.get("grade_shown"),
                      "key": (r["member"], p["platform"], p["offer_id"], p["side"]),
                      "corrected": bool(st and st["revision"] > 1), "scratch": bool(p.get("scratch"))})
    test = [x for x in items if x["scratch"]]
    items = [x for x in items if not x["scratch"]]          # member measurement: test picks never mixed in
    seen, dupes = set(), 0
    for x in items:
        dupes += x["key"] in seen
        seen.add(x["key"])
    by = {}
    for dim in ("league", "platform", "state", "surface"):
        vals = sorted({x[dim] or "unknown" for x in items})
        by[dim] = {v: _group([x for x in items if (x[dim] or "unknown") == v]) for v in vals}
    return {"version": VERSION, "built_at": now_s(), "rule": "each pick at its own line; latest settlement revision",
            "all": _group(items), "by": by, "duplicate_picks": dupes, "test": _group(test) if test else None,
            "corrected_results": sum(1 for x in items if x["corrected"]),
            "by_grade": grade_table(items),
            "by_grade_league": {lg: grade_table([x for x in items if x["league"] == lg])
                                for lg in sorted({x["league"] for x in items if x["league"]})},
            "notes": ["member picks only; test picks (admin token, marked test, SPORTS_PICKS_SCRATCH) are under 'test'",
                      "hit rate = wins / (wins + losses); pushes, voids and unresolved are listed, not counted",
                      "Brier / log-loss of the displayed chance of the chosen side given no push, settled picks with a "
                      "Grader chance only",
                      "95% intervals: bootstrap over games (1,000 resamples, seed 7)",
                      "stakes are 0; research grades (rg-1, sports-gp-1 bands) are shown labelled 'Research grade - not "
                      "validated'; by_grade uses the letter recorded when each pick was tracked; no returns"]}


def main(argv=None):
    """SPORTS_INGEST=1 python -m sports.picks ingest|settle ; python -m sports.picks stats (read-only counts)."""
    import sys
    from . import flags
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "stats"
    con = connect()
    try:
        if cmd in ("ingest", "settle"):
            flags.require("SPORTS_INGEST")
            out = {"ingest": ingest(con)}
            if cmd == "settle":
                with store.opened(read_only=True) as sc:
                    out["settle"] = settle_all(sc, con)
        elif cmd == "stats":
            out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("picks", "settlements")}
        else:
            print(main.__doc__)
            return 2
    finally:
        con.close()
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
