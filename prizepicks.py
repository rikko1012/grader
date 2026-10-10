"""PrizePicks CS2 board - the receiver (2026-10-10).

PrizePicks shows this server a CAPTCHA (not bypassed), so its board arrives through the HLTV scraper: its props
workflow reads PrizePicks' partner API and rewrites the whole board into prop_lines / prop_status in the shared
database on every read. A line is therefore OFFERED exactly while it is in the latest successful read.

Scout reads that table in the background every 5 minutes (never on a member's clock) and keeps its own history of
every line it observed: opening line, each move, provider-id changes (a re-posted line keeps its market), removals.

Not in the model hash: a book's line only sets the threshold a grade is calculated at - it never changes a projection.
The feed carries no PrizePicks player or match ids; lines are tied to our player and match by grading them (the same
strict name + team + opponent + start-time joins as Underdog), and the provider's line id is kept on every row."""
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import hltv
from bo3 import DATA_DIR

REFRESH = 5 * 60                 # how often Scout re-reads the shared table
STALE_MIN = 90                   # the props workflow reads PrizePicks about hourly: older than this = stale
SCOPE = {"1-2": "12", "1": "1", "1-3": "123"}   # the maps this app grades (Map 2-only lines are not graded)
HISTORY = Path(DATA_DIR) / ".cache" / "pp_lines.jsonl"     # every observed opening / move / id change / removal
SNAPSHOT = Path(DATA_DIR) / ".cache" / "pp_last.json"     # the last observed board, by market (survives restarts)
INGEST = Path(DATA_DIR) / ".cache" / "pp_ingest.jsonl"     # each new upstream read Scout took in: both timestamps
_S = {"at": 0.0, "tried": 0.0, "rows": [], "status": {}, "error": None, "busy": False}
_HLOCK = threading.Lock()
_SQL = """SELECT line_id, player, team, opponent, start_time, market, maps, line, odds_type, open_line, prev_line, move,
                 moves, moved_at, first_seen, other_line, higher, lower, stat, updated_at
          FROM prop_lines WHERE book = 'prizepicks'"""
_STATUS_SQL = "SELECT ok, lines, source, error, checked_at, last_ok FROM prop_status WHERE book = 'prizepicks'"


def _num(v):
    return float(v) if v is not None else None


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def market_key(r):
    """The underlying market, independent of PrizePicks' line id: player, team, opponent, match day, stat, maps and
    market type. A re-posted line (new id) keeps this key, so its history and forecasts stay linked."""
    return "|".join([_norm(r.get("player")), _norm(r.get("team")), _norm(r.get("opponent")), str(r.get("start") or "")[:10],
                     r.get("prop") or "", r.get("scope") or "", r.get("odds_type") or "standard"])


def rows_from(records):
    """prop_lines records (dicts) -> board rows in the shape of underdog.board() rows, plus book / market fields."""
    out = []
    for rec in records:
        scope = SCOPE.get(str(rec.get("maps")))
        market = rec.get("market")
        prop = "hs" if market == "headshots" else "kills" if market == "kills" else None
        if not scope or not prop or rec.get("line") is None or rec.get("start_time") is None:
            continue
        ot = rec.get("odds_type") or "standard"
        changed = rec.get("moved_at") or rec.get("first_seen")
        r = {"id": f"pp:{rec.get('line_id')}", "book": "prizepicks", "market_id": str(rec.get("line_id")),
             "player": rec.get("player"), "team": rec.get("team"), "opponent": rec.get("opponent"),
             "start": _iso(rec.get("start_time")), "prop": prop, "scope": scope, "line": float(rec["line"]),
             "alt": ot != "standard", "odds_type": ot,
             # PrizePicks' payout modifiers as reported (1.0 on standard lines) - display only. NOT entry payouts: a goblin /
             # demon changes the whole entry's payout, so they never feed the "same payout, easier line" ranking
             "higher": None, "lower": None, "mod_higher": _num(rec.get("higher")), "mod_lower": _num(rec.get("lower")),
             "price_higher": None, "price_lower": None,
             "updated_at": _iso(changed), "seen_at": _iso(rec.get("updated_at")), "first_seen": _iso(rec.get("first_seen")),
             "open_line": _num(rec.get("open_line")), "prev_line": _num(rec.get("prev_line")), "moves": rec.get("moves"),
             "other_line": _num(rec.get("other_line")), "stat_label": rec.get("stat")}
        r["market"] = market_key(r)
        out.append(r)
    return out


def _history(rows, now):
    """Append what changed since the last observed board: open / move / id change / removed (still upcoming)."""
    with _HLOCK:
        try:
            last = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        cur, events = {}, []
        at = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        base = lambda r: {"at": at, "book": "prizepicks", "market": r["market"], "id": r["market_id"], "line": r["line"],
                          "odds_type": r["odds_type"], "player": r["player"], "team": r["team"], "opponent": r["opponent"],
                          "start": r["start"], "prop": r["prop"], "scope": r["scope"], "src_seen": r.get("seen_at")}
        for r in rows:
            cur[r["market"]] = {"id": r["market_id"], "line": r["line"], "start": r["start"], "row": base(r)}
            p = last.get(r["market"])
            if p is None:
                events.append(dict(base(r), event="open", open_line=r.get("open_line")))
            else:
                if p["line"] != r["line"]:
                    events.append(dict(base(r), event="move", from_line=p["line"]))
                if p["id"] != r["market_id"]:
                    events.append(dict(base(r), event="id_change", old_id=p["id"]))
        for k, p in last.items():
            if k not in cur and (p.get("start") or "") > at:
                events.append(dict(p.get("row") or {}, at=at, event="removed"))     # pulled / suspended before the start
        try:
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            if events:
                with HISTORY.open("a", encoding="utf-8") as f:
                    for e in events:
                        f.write(json.dumps(e, default=str) + "\n")
            tmp = SNAPSHOT.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur, default=str), encoding="utf-8")
            tmp.replace(SNAPSHOT)
        except OSError:
            pass
        return events


def _utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t else None


def _count(events):
    out = {}
    for e in events or []:
        out[e["event"]] = out.get(e["event"], 0) + 1
    return out


def _ingest_log(rec):
    try:
        INGEST.parent.mkdir(parents=True, exist_ok=True)
        with INGEST.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def ingests(limit=50):
    """Scout's record of each new upstream PrizePicks read it took in (newest last)."""
    try:
        return [json.loads(x) for x in INGEST.read_text(encoding="utf-8").splitlines() if x.strip()][-limit:]
    except (OSError, ValueError):
        return []


def _load():
    try:
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute(_SQL)
                cols = [c[0] for c in cur.description]
                records = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.execute(_STATUS_SQL)
                s = cur.fetchone()
        finally:
            con.close()
        status = {} if not s else {"ok": bool(s[0]), "lines": s[1], "source": s[2], "error": s[3],
                                   "checked_at": s[4].timestamp() if s[4] else None, "last_ok": s[5].timestamp() if s[5] else None}
        rows = rows_from(records)
        now = time.time()
        prev_ok = (_S["status"] or {}).get("last_ok")
        _S.update(at=now, rows=rows, status=status, error=None)
        if status.get("ok"):
            ev = _history(rows, now)
            if status.get("last_ok") != prev_ok:            # a new upstream read: when it happened and when Scout took it in
                _ingest_log({"ingested_at": _utc(now), "upstream_checked_at": _utc(status.get("checked_at")),
                             "upstream_last_ok": _utc(status.get("last_ok")), "upstream_lines": status.get("lines"),
                             "rows": len(rows), "events": dict(_count(ev))})
    except Exception as e:                       # database down / tables missing: keep the last rows, say so
        _S.update(tried=time.time(), error=f"{type(e).__name__}: {str(e)[:160]}")
    finally:
        _S["busy"] = False


def feed_state(now=None):
    """loading / ok / stale / failed / empty / unconfigured - an empty board and a failed read are never the same.
    failed: the latest read failed (Scout could not read the table, or the scraper's PrizePicks read failed)."""
    now = now or time.time()
    st = _S["status"] or {}
    if not hltv.dsn():
        return "unconfigured"
    if not _S["at"] and not _S["error"]:
        return "loading"
    if _S["error"] or st.get("ok") is False or (st.get("checked_at") and st.get("last_ok") and st["checked_at"] > st["last_ok"] + 60):
        return "failed"
    if not st.get("last_ok") or now - st["last_ok"] > STALE_MIN * 60:
        return "stale"
    upcoming = datetime.fromtimestamp(now, timezone.utc).isoformat()
    if not any((r.get("start") or "") > upcoming for r in _S["rows"]):
        return "empty"
    return "ok"


def board(now=None, wait=False):
    """{"state", "at" (last good PrizePicks read, epoch s), "checked_at", "error", "rows"}. Rows are the lines in the
    latest read; each row's "stale" is True unless the feed is fresh ("ok"). Never blocks a member (background
    refresh; wait=True only for the board loop's first read)."""
    now = now or time.time()
    if not hltv.dsn():
        return {"state": "unconfigured", "at": None, "checked_at": None, "rows": [], "error": "HLTV database not configured (HLTV_DB_DSN)"}
    if now - max(_S["at"], _S["tried"]) > REFRESH and not _S["busy"]:
        _S["busy"] = True
        if wait and not _S["at"]:
            _load()
        else:
            threading.Thread(target=_load, daemon=True, name="prizepicks-feed").start()
    state = feed_state(now)
    st = _S["status"] or {}
    return {"state": state, "at": st.get("last_ok"), "checked_at": st.get("checked_at"), "lines": st.get("lines"),
            "ingested_at": _S["at"] or None,              # when Scout last read the shared table
            "error": _S["error"] or (st.get("error") if state in ("failed", "stale") else None),
            "rows": [dict(r, stale=state != "ok") for r in _S["rows"]]}


def history(market=None, limit=500):
    """The observed line history (newest last), optionally for one market."""
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = [json.loads(x) for x in lines if x.strip()]
    if market:
        out = [e for e in out if e.get("market") == market]
    return out[-limit:]
