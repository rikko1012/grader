"""PrizePicks CS2 board - the receiver (2026-10-10; direct reads 2026-10-11).

Read like Underdog: straight from PrizePicks every REFRESH seconds, so a line is here minutes after it drops. The
source is PrizePicks' partner API (partner-api.prizepicks.com), which answers cloud servers; the app's own host
(api.prizepicks.com) shows this server a CAPTCHA and is never used (not bypassed). When the partner API does not
answer, the HLTV scraper's copy is the backup: its props workflow reads the same API about hourly and rewrites the
board into prop_lines / prop_status in the shared database. That copy also supplies each line's history from the
scraper (opening line, moves, Underdog's line for the same prop), matched by PrizePicks' line id.

Scout keeps its own history of every line it observed: opening line, each move, provider-id changes (a re-posted
line keeps its market), removals.

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

REFRESH = 2 * 60                 # how often Scout reads PrizePicks (Underdog: every 5 minutes)
STALE_MIN = 90                   # last good read older than this = stale (the scraper's backup copy is about hourly)
DIRECT_URL = "https://partner-api.prizepicks.com/projections"
DIRECT_SOURCE = "partner-api.prizepicks.com (direct)"
LEAGUE_ID = 265                  # CS2 on PrizePicks (CSGO was 124); an empty board with a moved league shows as "empty"
DIRECT_HEADERS = {"Origin": "https://app.prizepicks.com", "Referer": "https://app.prizepicks.com/", "Accept": "application/json",
                  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/140.0.0.0 Safari/537.36"}
LOG_EVERY = 30 * 60              # an ingest row at least this often when nothing changed
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


# ---------------------------------------------------------------- direct reads (PrizePicks' partner API, JSON:API)
_MARKETS = {"headshot": "headshots", "kill": "kills"}
_MAP_SCOPE = re.compile(r"\s+maps?\s*\d+(?:\s*[-+]\s*\d+)*\s*$", re.I)


def _plain(text):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def market_of(stat):
    """"MAPS 1-2 Kills" -> ("kills", "1-2"); "MAP 1 Headshots" -> ("headshots", "1"); other stats -> (None, maps)."""
    text = _plain(stat).lower()
    maps = ""
    m = re.search(r"maps?\s*(\d)((?:\s*[-+]\s*\d)*)", text)
    if m:
        nums = [m.group(1)] + re.findall(r"\d", m.group(2))
        maps = nums[0] if len(nums) == 1 else f"{nums[0]}-{nums[-1]}"
        text = text[:m.start()] + text[m.end():]
    return next((v for k, v in _MARKETS.items() if k in text), None), maps


def parse_direct(body, seen):
    """The partner API's projections -> records in the shape of prop_lines rows (the same reading as the scraper's
    hltv_scraper/props/prizepicks.py): pre-game single-player lines only, opponent without its map scope."""
    included = {(r.get("type"), str(r.get("id"))): r for r in body.get("included") or []}

    def linked(res, rel):
        ref = ((res.get("relationships") or {}).get(rel) or {}).get("data")
        return included.get((ref.get("type"), str(ref.get("id"))), {}) if isinstance(ref, dict) else {}

    out = []
    for p in body.get("data") or []:
        a = p.get("attributes") or {}
        if (a.get("status") or "pre_game") != "pre_game":
            continue
        pres = linked(p, "new_player")
        pl = pres.get("attributes") or {}
        if pl.get("combo"):
            continue
        name = _plain(pl.get("display_name") or pl.get("name"))
        try:
            line = float(a.get("line_score"))
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        td = linked(pres, "team_data").get("attributes") or {}
        team = _plain(pl.get("team") or td.get("name") or td.get("abbreviation"))
        scope = _plain((linked(p, "duration").get("attributes") or {}).get("name"))
        opp = _plain(a.get("description"))
        if scope and opp.lower().endswith(" " + scope.lower()):
            opp = opp[: -len(scope)]
        opp = _MAP_SCOPE.sub("", opp).strip()
        stat = _plain(a.get("stat_display_name") or a.get("stat_type") or (linked(p, "stat_type").get("attributes") or {}).get("name"))
        market, maps = market_of(stat)
        ot = str(a.get("odds_type") or "standard").lower()
        if a.get("is_promo") and ot == "standard":
            ot = "promo"
        wagers = a.get("allowed_wager_types")
        wagers = {wagers} if isinstance(wagers, str) else set(wagers or [])
        try:
            start = datetime.fromisoformat(str(a.get("start_time")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            start = None
        out.append({"line_id": str(p.get("id", "")), "player": name, "team": team, "opponent": opp, "start_time": start,
                    "market": market, "maps": maps, "line": line, "odds_type": ot,
                    "higher": None if wagers and "over" not in wagers else 1.0,
                    "lower": None if ot in ("goblin", "demon") or (wagers and "under" not in wagers) else 1.0,
                    "stat": stat, "updated_at": seen, "open_line": None, "prev_line": None, "move": None, "moves": None,
                    "moved_at": None, "first_seen": None, "other_line": None})
    singles = sum(1 for p in body.get("data") or [] if ((p.get("attributes") or {}).get("status") or "pre_game") == "pre_game"
                  and not ((linked(p, "new_player").get("attributes") or {}).get("combo")))
    if singles and not out:
        raise ValueError(f"{singles} PrizePicks lines but none could be read - did the response format change?")
    return out


def _direct():
    import requests
    r = requests.get(DIRECT_URL, params={"league_id": LEAGUE_ID, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"},
                     headers=DIRECT_HEADERS, timeout=25)
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError(f"unexpected PrizePicks response: {str(body)[:120]}")
    return body


def enrich(records, db_records):
    """The scraper's history of each line (same PrizePicks line id): opening line and first sighting always; its last
    move only while that copy still shows the current line; Underdog's line for the same prop (other_line)."""
    by_id = {str(r.get("line_id")): r for r in db_records or []}
    for rec in records:
        d = by_id.get(rec["line_id"])
        if not d:
            continue
        rec.update(open_line=d.get("open_line"), first_seen=d.get("first_seen"), other_line=d.get("other_line"))
        if d.get("line") is not None and float(d["line"]) == rec["line"]:
            rec.update(prev_line=d.get("prev_line"), move=d.get("move"), moves=d.get("moves"), moved_at=d.get("moved_at"))
    return records


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


def _read_db():
    """The scraper's copy: (records, status) from prop_lines / prop_status."""
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
    return records, status


def _load():
    now = time.time()
    records = status = None
    direct_err = db_err = None
    try:                                         # 1. PrizePicks itself, like Underdog
        body = _direct()
        records = parse_direct(body, datetime.fromtimestamp(now, timezone.utc))
        status = {"ok": True, "lines": len(records), "source": DIRECT_SOURCE, "error": None, "checked_at": now, "last_ok": now}
    except Exception as e:
        direct_err = f"{type(e).__name__}: {str(e)[:160]}"
    db_records = []
    if hltv.dsn():                               # 2. the scraper's copy: line history, or the whole board as backup
        try:
            db_records, db_status = _read_db()
            if records is None:
                records = db_records
                status = dict(db_status, source=f"{db_status.get('source') or 'prizepicks'} via the HLTV scraper "
                                                f"(direct read failed: {direct_err})")
        except Exception as e:
            db_err = f"{type(e).__name__}: {str(e)[:160]}"
    try:
        if records is None:
            raise RuntimeError(f"PrizePicks: direct read failed ({direct_err}); scraper copy unavailable ({db_err or 'no database'})")
        if status.get("source") == DIRECT_SOURCE:
            enrich(records, db_records)
        rows = rows_from(records)
        prev = _S["status"] or {}
        _S.update(at=now, rows=rows, status=status, error=None)
        if status.get("ok"):
            ev = _history(rows, now)
            new_read = status.get("last_ok") != prev.get("last_ok")
            # every new read of the scraper's copy (about hourly); direct reads (every few minutes) when something
            # changed, the source switched, or at least every LOG_EVERY
            if new_read and (status.get("source") != DIRECT_SOURCE or ev or status.get("source") != prev.get("source")
                             or now - _S.get("logged", 0) > LOG_EVERY):
                _S["logged"] = now                # when Scout took a read in, where from, and what changed
                _ingest_log({"ingested_at": _utc(now), "source": status.get("source"),
                             "upstream_checked_at": _utc(status.get("checked_at")),
                             "upstream_last_ok": _utc(status.get("last_ok")), "upstream_lines": status.get("lines"),
                             "rows": len(rows), "events": dict(_count(ev))})
    except Exception as e:                       # both sources down: keep the last rows, say so
        _S.update(tried=time.time(), error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        _S["busy"] = False


def feed_state(now=None):
    """loading / ok / stale / failed / empty - an empty board and a failed read are never the same.
    failed: the latest read failed (neither PrizePicks nor the scraper's copy could be read, or the copy reports a failure)."""
    now = now or time.time()
    st = _S["status"] or {}
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
    if now - max(_S["at"], _S["tried"]) > REFRESH and not _S["busy"]:
        _S["busy"] = True
        if wait and not _S["at"]:
            _load()
        else:
            threading.Thread(target=_load, daemon=True, name="prizepicks-feed").start()
    state = feed_state(now)
    st = _S["status"] or {}
    return {"state": state, "at": st.get("last_ok"), "checked_at": st.get("checked_at"), "lines": st.get("lines"),
            "ingested_at": _S["at"] or None,              # when Scout last took a read in
            "source": st.get("source"),
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
