"""Member pick tracking pk-1 (owner 2026-10-24): prospective picks recorded against their EXACT offer revision and forecast,
settled against real results at the pick's OWN line. Stakes are always 0 and grade letters are never shown here
(sports-gp-1 withholds letters until forward validation passes; the validation state is shown instead). No EV / returns.

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
        raise Refused("not_gradable", "Scout cannot settle this line: " + "; ".join(why)[:120])
    ev = _event(con, o.event_id)
    start = ev.start_utc if ev is not None and ev.start_utc else (
        o.listed_start_utc if o.listed_time_kind == "start" else None)
    if not start:
        raise Refused("start_unknown", "The game's start time is not known, so the pick cannot be time-stamped")
    if (ev is not None and ev.status != "scheduled") or start <= t:
        raise Refused("started", "The game has started - picks are recorded before the start only")
    meta = store.row_meta(con, rid) or {}
    last = max(x for x in (meta.get("last_seen_at"), o.observed_at) if x)
    age = (now - parse_utc(last)).total_seconds()
    if age > STALE_S:
        raise Refused("stale", f"Price not fresh: last seen {int(age // 60)} min ago")
    fid, fc = item.get("forecast_id"), None
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
        if not same_offer or fc.stat != o.stat or abs(float(fc.line) - float(o.line)) > 1e-9 \
                or fc.player_id != o.player_id:
            raise Refused("forecast_mismatch", "The chance shown was made for another line - refresh")
        rec_p = {"p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push}
        for k, v in shown.items():
            if v is not None and abs(v - rec_p[k]) > DISPLAY_TOL:
                raise Refused("display_mismatch", "The chance shown differs from Scout's record - refresh")
        displayed = rec_p
    else:
        if any(v is not None for v in shown.values()):
            raise Refused("display_mismatch", "A chance was shown without a forecast record - refresh")
        displayed = None
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
            "displayed_source": "scout_forecast" if fc else None, "stake_units": 0, "grade_shown": None}


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


def settle_all(store_con, con=None, settled_at=None):
    """Worker job: settle every pick at its own line; a changed result = a new revision. -> counts."""
    from .settlement import at_line
    own = con is None
    con = con or connect()
    c = {"picks": 0, "pending": 0, "inserted": 0, "revision": 0, "unchanged": 0}
    try:
        for p in con.execute("SELECT pick_id, platform, offer_id, side, line FROM picks").fetchall():
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
        return "No Scout chance on this line"
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
            "validation_text": _state_text(r.get("validation_state")), "stake_units": 0,
            "result": None if st is None else {"status": st["status"], "outcome": st["outcome"], "value": st["value"],
                                               "revision": st["revision"],
                                               "settled_at": st["settled_at"], "corrected": st["revision"] > 1}}


def mine(con, member, limit=200):
    mh = member_hash(member)
    rows = con.execute("SELECT * FROM picks WHERE member=? ORDER BY tracked_at DESC LIMIT ?", (mh, limit)).fetchall()
    out = [_view(r, latest_settlement(con, r["pick_id"])) for r in rows]
    n = con.execute("SELECT COUNT(*) FROM picks WHERE member=?", (mh,)).fetchone()[0]
    return {"version": VERSION, "picks": out, "total": n, "shown": len(out), "stake_units": 0}


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


def aggregate(con):
    items = []
    for r in con.execute("SELECT pick_id, member, payload FROM picks").fetchall():
        p = json.loads(r["payload"])
        st = latest_settlement(con, r["pick_id"])
        items.append({"pick_id": r["pick_id"], "member": r["member"], "event_id": p.get("event_id"),
                      "side": p["side"], "displayed": p.get("displayed"), "status": st["status"] if st else None,
                      "platform": p["platform"], "league": p.get("league"),
                      "state": p.get("validation_state") or "no_chance", "surface": p.get("surface"),
                      "key": (r["member"], p["platform"], p["offer_id"], p["side"]),
                      "corrected": bool(st and st["revision"] > 1)})
    seen, dupes = set(), 0
    for x in items:
        dupes += x["key"] in seen
        seen.add(x["key"])
    by = {}
    for dim in ("league", "platform", "state", "surface"):
        vals = sorted({x[dim] or "unknown" for x in items})
        by[dim] = {v: _group([x for x in items if (x[dim] or "unknown") == v]) for v in vals}
    return {"version": VERSION, "built_at": now_s(), "rule": "each pick at its own line; latest settlement revision",
            "all": _group(items), "by": by, "duplicate_picks": dupes,
            "corrected_results": sum(1 for x in items if x["corrected"]),
            "notes": ["hit rate = wins / (wins + losses); pushes, voids and unresolved are listed, not counted",
                      "Brier / log-loss of the displayed chance of the chosen side given no push, settled picks with a "
                      "Scout chance only",
                      "95% intervals: bootstrap over games (1,000 resamples, seed 7)",
                      "stakes are 0 and no grade letters are shown while sports-gp-1 validation is pending; no returns"]}


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
