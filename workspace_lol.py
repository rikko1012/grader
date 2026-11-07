"""Research workspace, League of Legends: the member board, research, checks, settlement and results.

This module never imports lol (lol/tests/test_lol_isolation.py). It reads what the LoL worker writes, the way server.py does:
server.lol_member_board() for the member's own view of each line, the worker's board.json for the ids that view leaves out (those
stay on the server and never reach a page), and lol.db read-only for past games and settled results.

One snapshot per board.json write, single flight. Past games are read once per snapshot, one indexed read per player on the board,
behind one connection. Settlement is the LoL worker's own lol_settlements row for the receipt's platform, market and line: this
module only says which side of it the member took. LoL letters carry 0 units, and a stake is never shown as anything else."""
import board_parity
import hashlib
import letter_containment
import workspace_h2h
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PERIOD = {"Map 1": ("map1", "Map 1", (1,)), "Maps 1-2": ("maps12", "Maps 1-2", (1, 2)), "Maps 1-3": ("maps13", "Maps 1-3", (1, 2, 3))}
LETTERS = ("A+", "A", "A-", "B+", "B", "B-")
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
SIDE = {"Over": "over", "Under": "under"}
BOARD_TTL, RESULTS_TTL, FORECAST_TTL = 5, 300, 60
QUOTE_MAX_AGE_S = 900                   # lol/center/forecast.slip_check: a line not re-read within 15 minutes is not checked
HISTORY_DAYS = 400
HISTORY_GAMES = 90                      # games read per player: enough for 20 series of up to five games
SERIES_KEEP = 24
DB_SLOTS = 3
RESEARCH_KEYS = ("role", "opponent", "event", "best_of", "scope_games", "recent_games", "recent_per_game", "median", "team_win", "drivers")
_DB = threading.BoundedSemaphore(DB_SLOTS)


def _empty():
    return {"at": 0.0, "mtime": None, "body": None, "index": {}, "props": {}, "feeds": {}, "series": {}, "gen": 0}


_SNAP = _empty()


def reset():
    """Tests: forget the board."""
    _SNAP.clear()
    _SNAP.update(_empty())


class Down(Exception):
    """A LoL file could not be read. Nothing is decided on it: the caller keeps what it has."""


def _ts(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _hash(*fields):
    return hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:16]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _open(srv, name="lol.db"):
    path = Path(srv.LOL_CENTER) / name
    if not path.exists():
        raise Down(name)
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def _canonical(con, eid):
    """lol/center/store.canonical_id, read-only: the surviving id of an id that was merged."""
    seen = set()
    while eid and eid not in seen:
        seen.add(eid)
        r = con.execute("SELECT v FROM meta WHERE k = ?", (f"merged:{eid}",)).fetchone()
        if not r:
            return eid
        try:
            eid = json.loads(r[0])
        except ValueError:
            return eid
    return eid


def _value(stat, g):
    """lol/center/settle._value for one game."""
    if stat == "fantasy_points":
        if any(g.get(k) is None for k in ("kills", "assists", "deaths", "cs")):
            return None
        return 3 * g["kills"] + 2 * g["assists"] - g["deaths"] + 0.02 * g["cs"]
    return g.get(stat)


def read_series(con, pids):
    """{pid: newest-first series of one player}, each {date, opponent, games: {game_n: stats}, best_of}. Remade and forfeited games and
    unfinished series are left out: a value here is one the settlement would have counted."""
    since = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    out, names = {}, {}
    for pid in pids:
        rows = con.execute(
            "SELECT p.sid, p.game_n, p.start_utc, p.kills, p.deaths, p.assists, p.cs, p.opp_tid, s.best_of, s.status, s.ff, g.remake, g.ff AS gff FROM c_player_games p "
            "JOIN c_series s ON s.sid = p.sid LEFT JOIN c_games g ON g.gid = p.gid WHERE p.pid = ? AND p.start_utc >= ? ORDER BY p.start_utc DESC LIMIT ?", (pid, since, HISTORY_GAMES)).fetchall()
        by, order = {}, []
        for r in rows:
            if r["status"] != "complete" or r["ff"] or r["gff"] or r["remake"]:
                continue
            if r["sid"] not in by:
                by[r["sid"]] = {"date": str(r["start_utc"])[:10], "opp": r["opp_tid"], "best_of": r["best_of"], "games": {}}
                order.append(r["sid"])
            by[r["sid"]]["games"][r["game_n"]] = {k: r[k] for k in ("kills", "deaths", "assists", "cs")}
        ser = [by[s] for s in order[:SERIES_KEEP]]
        for s in ser:
            o = s.pop("opp")
            if o not in names:
                n = con.execute("SELECT label FROM entities WHERE eid = ?", (_canonical(con, o),)).fetchone() if o else None
                names[o] = n[0] if n else ""
            s["opponent"] = names[o]
        out[pid] = ser
    return out


def series_values(series, stat, scope):
    """Newest-first values of this stat on these maps: every game in scope that was played, the way a line settles. A best-of-one has
    no Maps 1-2, and Maps 1-3 needs a series that could reach three games."""
    want = PERIOD[scope][2] if scope in PERIOD else None
    if want is None:
        return []
    out = []
    for s in series or []:
        games = {n: g for n, g in s["games"].items() if n in want}
        if len(want) == 1 and 1 not in games:
            continue
        if len(want) == 2 and len(games) < 2:
            continue
        if len(want) == 3 and (len(games) < 2 or (s.get("best_of") or 3) < 3):
            continue
        vals = [_value(stat, games[n]) for n in sorted(games)]
        if any(v is None for v in vals):
            continue
        total = sum(vals)
        out.append({"date": s["date"], "opponent": s.get("opponent") or "", "value": round(total, 2) if stat == "fantasy_points" else total, "values": vals, "maps": [f"Game {n}" for n in sorted(games)]})
    return out


def _tally(values, line, n):
    w = values[:n]
    if not w:
        return None
    o = sum(1 for v in w if v["value"] > line)
    u = sum(1 for v in w if v["value"] < line)
    return {"over": o, "under": u, "push": len(w) - o - u, "n": len(w)}


def _payout(sides, name):
    for s in sides or []:
        if s.get("side") == name:
            try:
                return float(s["payout"]) if s.get("payout") is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _offered(sides, name):
    return any(s.get("side") == name and s.get("offered") for s in sides or [])


def offer_rev(x, raw):
    return "d-" + _hash(x.get("platform"), raw.get("market_key"), x.get("line"), x.get("line_type"), x.get("sides"), x.get("start"))


def read_rev(x):
    return "r-" + _hash(x.get("pick_side"), x.get("p_over"), x.get("p_under"), x.get("grade"))


ONE_SIDED = ("Over only", "Under only")


def one_sided_reason(label):
    return f"{label}: Scout grades lines that offer both sides."


def record_of(x, raw, values):
    """One display record from one member row. Chances, grade and the 0U stake are the member row's own.
    os-1 (owner 2026-11-06, as sports cap-2): a line the book offers on one side only (server.lol_one_sided_label) keeps its
    line, price and Goblin / Demon tag and reads "Over only" / "Under only": no Scout chance, side, letter, stake or slip."""
    one = x.get("one_sided") if x.get("one_sided") in ONE_SIDED else None
    scope = x.get("scope")
    period, period_label, _ = PERIOD.get(scope, (_norm(scope) or "other", scope or "Other", ()))
    side = None if one else SIDE.get(x.get("pick_side"))
    link = raw.get("link") or {}
    sid = "lol-" + _hash(link.get("sid") or (x.get("match"), x.get("start")))    # the worker's own ids stay on the server, as on /api/lol/board
    who = x.get("player") or ""
    pkey = "p" + _hash(link.get("pid") or who)
    lt = {"Goblin": "goblin", "Demon": "demon"}.get(x.get("line_type"), "standard")
    grade = x.get("grade") if x.get("grade") in LETTERS and not one else None
    teams = sorted([str(x.get("team") or ""), str(x.get("opponent") or "")], key=str.lower)
    p_side = None if one else x.get("p_over") if side == "over" else x.get("p_under")
    key = ".".join(str(k) for k in ("lol", sid, pkey, x.get("stat"), period, x.get("line"))) + ("" if lt == "standard" else "." + lt)
    return {"sport": "lol", "key": key,
            "event": {"id": sid, "id_kind": "canonical" if link.get("sid") else "derived", "label": x.get("match") or " vs ".join(t for t in teams if t), "teams": teams, "competition": x.get("event") or "",
                      "best_of": x.get("best_of"), "start_utc": x.get("start"), "lock_utc": x.get("start"), "status": "scheduled"},
            "player": {"id": pkey, "key": pkey, "name": who, "team": x.get("team") or "", "opponent": x.get("opponent") or "", "role": x.get("role") or ""},
            "market": {"stat": x.get("stat"), "stat_label": x.get("stat_name") or x.get("stat"), "period": period, "period_label": period_label, "line_type": lt},
            "offer": {"platform": x.get("platform"), "platform_label": x.get("platform_label") or x.get("platform"), "offer_id": x.get("key"), "revision": offer_rev(x, raw), "revision_kind": "derived", "line": x.get("line"),
                      "seen_at": x.get("line_checked") or x.get("line_seen"), "over": {"offered": _offered(x.get("sides"), "Over"), "payout": _payout(x.get("sides"), "Over"), "price": None},
                      "under": {"offered": _offered(x.get("sides"), "Under"), "payout": _payout(x.get("sides"), "Under"), "price": None}, "moved_from": None, "moved_at": None},
            "scout": ({"side": None, "read_rev": read_rev(dict(x, pick_side=None, p_over=None, p_under=None, grade=one)), "p_side": None, "p_over": None, "p_under": None, "p_push": None,
                       "projection": None, "range80": None, "grade": None, "units": None, "stake_reason": None, "hold": {"label": one, "reason": one_sided_reason(one)},
                       "model_version": None, "forecast_at": None, "validation": {"kind": "live", "label": one, "detail": one_sided_reason(one)}} if one else
                      {"side": side, "read_rev": read_rev(x), "p_side": p_side, "p_over": x.get("p_over"), "p_under": x.get("p_under"), "p_push": x.get("p_push"), "projection": x.get("projection"), "range80": x.get("range80"),
                       "grade": grade, "units": 0.0, "stake_reason": x.get("stake_reason"), "hold": None, "model_version": x.get("model_version"), "forecast_at": x.get("forecast_at"), "validation": {"kind": "live"},
                       **({"letter_held": True} if x.get("letter_held") else {})}),
            "history": {"l5": _tally(values, x.get("line"), 5), "l10": _tally(values, x.get("line"), 10), "l20": _tally(values, x.get("line"), 20),
                        "avg": round(sum(v["value"] for v in values[:10]) / len(values[:10]), 1) if values else x.get("recent_per_game"), "recent": x.get("recent_games"),
                        **({"h2h": workspace_h2h.board_h2h(values, x.get("opponent"), x.get("line")),
                            "season": workspace_h2h.board_season(values, x.get("line"))} if workspace_h2h.board_enabled() else {})},
            "market_chance": None, "state": {"kind": "ok", "reason": None}}


def _build(srv):
    path = Path(srv.LOL_CENTER) / "board.json"
    try:
        mt = (path.stat().st_mtime, letter_containment.key("lol"))     # lc-1: a containment change rebuilds once
    except OSError:
        raise Down("board.json") from None
    aged = any(f["state"] == "ok" and _ts(f["at"]) is not None and (datetime.now(timezone.utc) - _ts(f["at"])).total_seconds() > QUOTE_MAX_AGE_S for f in _SNAP["feeds"].values())
    if _SNAP["mtime"] == mt and _SNAP["body"] is not None and not aged:
        _SNAP["at"] = time.time()
        return 200, _SNAP["body"]
    body = srv.lol_member_board()
    if body is None:
        raise Down("member board")
    member = json.loads(body)
    raw = {}
    for r in json.loads(path.read_text(encoding="utf-8")).get("rows") or []:
        if r.get("market_key"):
            raw[srv._lol_key(r.get("platform"), r["market_key"])] = {"market_key": r["market_key"], "link": r.get("link") or {}, "fid": (r.get("forecast") or {}).get("id"), "line_id": r.get("line_id"),
                                                                     "fc_line": (r.get("forecast") or {}).get("line"), "market_type": r.get("market_type"), "stat": r.get("stat"), "ud_link": None,
                                                                     "nb": (r.get("forecast") or {}).get("nb_size")}
    pids, series, canon = sorted({r["link"]["pid"] for r in raw.values() if r["link"].get("pid")}), {}, {}
    try:
        con = _open(srv)
        try:
            canon = {p: _canonical(con, p) for p in pids}
            series = read_series(con, sorted(set(canon.values())))
        finally:
            con.close()
    except (Down, sqlite3.Error):               # the board stands without past games; hit rates are left out, never zero
        series = _SNAP.get("series") or {}
    records, index, props = [], {}, {}
    for x in member.get("rows") or []:
        r = raw.get(x.get("key"))
        if r is None or x.get("line") is None:
            continue
        pid = canon.get(r["link"].get("pid"), r["link"].get("pid"))
        values = series_values(series.get(pid), x.get("stat"), x.get("scope"))
        rec = board_parity.attach(record_of(x, r, values))     # 4.16 parity numbers, only while a SCOUT_PARITY_* row flag is on
        records.append(rec)
        ix = {"rec": json.dumps(rec, default=str, separators=(",", ":")).encode(), "market_key": r["market_key"], "sid": r["link"].get("sid"), "pid": pid, "raw_pid": r["link"].get("pid"), "fid": r["fid"], "fc_line": r["fc_line"],
              "line": x.get("line"), "stat": x.get("stat"), "scope": x.get("scope"), "scope_digits": "".join(str(n) for n in PERIOD.get(x.get("scope"), ("", "", ()))[2]), "period": rec["market"]["period"], "platform": x.get("platform"), "start": x.get("start"), "checked": x.get("line_checked"),
              "over": rec["offer"]["over"]["offered"], "under": rec["offer"]["under"]["offered"], "rev": rec["offer"]["revision"], "read_rev": rec["scout"]["read_rev"], "po": x.get("p_over"), "pu": x.get("p_under"), "side": rec["scout"]["side"],
              "model": x.get("model_version"), "at": x.get("forecast_at"), "name": x.get("player") or "", "team": x.get("team") or "", "pkey": rec["player"]["key"], "lt": rec["market"]["line_type"], "ud": x.get("ud_app_link"),
              "eid": rec["event"]["id"], "projection_id": r.get("line_id"), "member": {k: x.get(k) for k in RESEARCH_KEYS},
              "nb": r.get("nb"), "lam": x.get("projection")}     # the saved NB (server side only): another book's line is read from it
        if rec["scout"].get("hold"):
            ix.update(fid=None, po=None, pu=None, one_sided=rec["scout"]["hold"]["label"])
        index[(x.get("platform"), x.get("key"))] = ix
        props.setdefault((ix["eid"], ix["pkey"], ix["stat"], ix["period"]), []).append((x.get("platform"), x.get("key")))
    at = member.get("at")
    feeds = {}
    for p, label in LABEL.items():
        t = (member.get("feeds") or {}).get(label)
        if t:
            age = None if _ts(t) is None else (datetime.now(timezone.utc) - _ts(t)).total_seconds()
            feeds[p] = {"state": "stale" if age is None or age > QUOTE_MAX_AGE_S else "ok", "at": t, "error": None}
    out = json.dumps({"sport": "lol", "version": None, "board_at": at, "feeds": feeds, "records": records}, default=str, separators=(",", ":")).encode()
    if _SNAP["body"] == out:
        out = _SNAP["body"]
    _SNAP.update(at=time.time(), mtime=mt, body=out, index=index, props=props, feeds=feeds, series=series, gen=_SNAP["gen"] + 1)
    return 200, out


def snapshot(srv):
    """The shared LoL snapshot, or None when no board has been read. A failed read keeps the last good copy."""
    try:
        srv.shared(("ws_lol",), lambda: _build(srv), ttl=BOARD_TTL)
    except Exception:
        pass
    return _SNAP if _SNAP["body"] is not None else None


# ---------------------------------------------------------------------------------------------------------------- checks

def forecast_row(srv, fid):
    """The saved forecast, read once a minute across members. A locked or missing file is not an answer about the forecast."""
    def compute():
        if not _DB.acquire(timeout=1.0):
            return 200, {"state": "busy"}, 1
        try:
            con = _open(srv)
            try:
                r = con.execute("SELECT id, platform, market_key, sid, pid, stat, scope, line, market_type, forecast_at, series_start, model_version FROM lol_forecasts WHERE id = ?", (fid,)).fetchone()
            finally:
                con.close()
            return (200, {"state": "ok", **dict(r)}) if r else (200, {"state": "none"}, 5)
        except (Down, sqlite3.Error):
            return 200, {"state": "busy"}, 1
        finally:
            _DB.release()
    return srv.shared(("ws_lol_fc", fid), compute, ttl=FORECAST_TTL)[1]


def forecast_problem(srv, x, leg, pr, gap):
    def bad(code, text, **kw):
        return pr(2, code, text, **kw)
    if not x["fid"]:
        return bad("no_forecast", "No saved forecast for this line, so it cannot be tracked.")
    fc = forecast_row(srv, x["fid"])
    if fc.get("state") == "busy":
        return bad("forecast_pending", "The forecast for this line could not be read just now.", retry=True)
    if fc.get("state") != "ok":
        return bad("no_forecast", "No saved forecast for this line, so it cannot be tracked.")
    logged, start = _ts(fc.get("forecast_at")), _ts(fc.get("series_start"))
    same = (fc.get("platform") == x["platform"] and fc.get("market_key") == x["market_key"] and fc.get("sid") == x["sid"] and fc.get("pid") == x["raw_pid"] and fc.get("stat") == x["stat"]
            and str(fc.get("scope")) == x["scope_digits"] and float(fc.get("line")) == float(x["line"]) and logged is not None and start is not None and logged < start)
    if not same:
        return bad("forecast_mismatch", "The saved forecast does not match this line.")
    now_p, shown = (x["po"] if leg["side"] == "over" else x["pu"]), leg.get("shown_p")
    if leg.get("read_rev") != x["read_rev"] or shown is None or now_p is None or abs(shown - now_p) > gap:
        word = "Over" if leg["side"] == "over" else "Under"
        return bad("display_mismatch", f"Scout's read changed: now {word} {(now_p or 0) * 100:.1f}%.", swap=True)
    return None


def check_leg(srv, leg, now):
    from workspace_api import DISPLAY_MAX_GAP, SIDE_WORD, _n, _pr
    snap = snapshot(srv)
    book, side = leg["book"], leg["side"]
    if snap is None:
        return {"ok": False, "problems": [_pr(1, "retry", "Scout could not check this line just now.", retry=True)], "current": None}
    x = snap["index"].get((book, leg["row_id"]))
    label = LABEL.get(book, book)
    if x is None:
        return {"ok": False, "problems": [_pr(1, "gone", "This line is no longer on the board.")], "current": None}
    problems = []
    checked = _ts(x["checked"])
    if checked is None or not -60 <= (now - checked).total_seconds() <= QUOTE_MAX_AGE_S:
        problems.append(_pr(1, "stale", "This line has not been re-read in the last 15 minutes.", retry=True))
    lock = _ts(x["start"])
    if lock is None:
        problems.append(_pr(0, "start_unknown", "No start time on the feed for this line."))
    elif lock <= now:
        problems.append(_pr(0, "locked", "Match started."))
    if x["rev"] != leg.get("rev") or float(leg["line"]) != float(x["line"]):
        moved = float(leg["line"]) != float(x["line"])
        problems.append(_pr(1, "moved", f"Line moved from {_n(leg['line'])} to {_n(x['line'])}." if moved else f"This offer changed on {label}.", swap=True))
    if not x[side]:
        problems.append(_pr(1, "side", f"{SIDE_WORD[side]} is not offered on {label}."))
    if x.get("one_sided"):                      # os-1: shown, never on a slip or tracked
        problems.append(_pr(1, "not_gradable", one_sided_reason(x["one_sided"])))
    fp = None if x.get("one_sided") else forecast_problem(srv, x, leg, _pr, DISPLAY_MAX_GAP)
    if fp:
        problems.append(fp)
    rec = json.loads(x["rec"])
    mult = rec["offer"][side]["payout"]
    p = x["po"] if side == "over" else x["pu"]
    return {"ok": not problems, "problems": problems,
            "current": {"row_id": leg["row_id"], "line": x["line"], "rev": x["rev"], "read_rev": x["read_rev"], "scout_side": x["side"], "p_side": p, "forecast_id": x["fid"], "forecast_at": x["at"], "model_version": x["model"],
                        "start_utc": x["start"], "lock_utc": x["start"]},
            "_x": x, "_ident": {"nick": x["pkey"], "prop": x["stat"], "scope": x["period"], "match_slug": x["eid"], "match_key": x["eid"], "platform": book, "line": x["line"], "side": side},
            "_rule": {"sport": "lol", "book": book, "team": f"lol:{x['team']}" if x["team"] else "", "nick": x["name"], "side": side, "event": x["eid"]},
            "_ev": {"name": x["name"], "sport": "lol", "match": x["eid"], "line": x["line"], "multiplier": mult, "on_side": side == x["side"], "p": p, "tag": x["model"], "prop": x["stat"], "scope": x["period"], "line_type": x['lt']}}


def payload(x, leg, res, e):
    rec = json.loads(x["rec"])
    on = leg["side"] == x["side"]
    return {"player": rec["player"]["name"], "team": rec["player"]["team"], "opponent": rec["player"]["opponent"], "matchup": rec["event"]["label"], "competition": rec["event"]["competition"],
            "stat_label": rec["market"]["stat_label"], "period_label": rec["market"]["period_label"], "platform_label": rec["offer"]["platform_label"], "scout_side": x["side"], "on_side": on,
            "displayed_p": x["po"] if leg["side"] == "over" else x["pu"], "grade": rec["scout"]["grade"], "units": 0.0, "stake_reason": rec["scout"]["stake_reason"], "projection": rec["scout"]["projection"],
            "forecast_p": rec["scout"]["p_side"], "forecast_at": x["at"], "forecast_grade": rec["scout"]["grade"], "forecast_units": 0.0, "l10": rec["history"]["l10"], "feed_at": rec["offer"]["seen_at"],
            "rules_version": e.get("rules_version"), "entry_type": e.get("entry_type")}


def receipt_fields(x, leg, res, e):
    side, unit = leg["side"], f"{x['platform']}|{x['market_key']}|{x['line']}"
    return {"sport": "lol", "row_id": leg["row_id"], "offer_rev": x["rev"], "side": side, "line": x["line"], "line_type": x["lt"], "pick_id": None, "forecast_id": x["fid"], "match_slug": x["eid"], "match_id": x["sid"],
            "match_key": x["eid"], "nick": x["pkey"], "prop": x["stat"], "scope": x["period"], "start_utc": x["start"], "model_version": x["model"], "payload": payload(x, leg, res, e),
            "settle_key": f"lol|{unit}|{side}", "settle_ref": {"unit": unit, "side": side}}


def entry_link(x, side):
    return x.get("ud")


def entry_projection(x, side):
    if x.get('platform')!='prizepicks':return None
    return {'book':'prizepicks','projection_id':x.get('projection_id'),'side':side,
            'line':x.get('line'),'odds_type':x.get('lt')}


# ---------------------------------------------------------------------------------------------------------------- settlement

def result_of(row, side):
    """(result, actual) of one side from one lol_settlements row. graded and push are the worker's own words; void and dnp remove the
    pick; rule_unverified and unsupported settle nothing here."""
    st = row["status"]
    if st == "push":
        return "push", row["actual"]
    if st in ("void", "dnp"):
        return "void", None
    if st == "graded" and row["over_hit"] in (0, 1):
        over = row["over_hit"] == 1
        return ("hit" if over == (side == "over") else "miss"), row["actual"]
    return None, None


def settle(srv, rows):
    """The latest lol_settlements version per unit. A file that cannot be read raises, and every result stays as it was."""
    units = sorted({w["ref"]["unit"] for w in rows})
    if not units:
        return {}
    con = _open(srv)
    try:
        latest = {}
        for i in range(0, len(units), 200):
            part = units[i:i + 200]
            for r in con.execute(f"SELECT unit_key, version, status, actual, over_hit, push, games, settled_at FROM lol_settlements WHERE unit_key IN ({','.join('?' * len(part))}) ORDER BY version", part):
                latest[r["unit_key"]] = r
    finally:
        con.close()
    out = {}
    for w in rows:
        r = latest.get(w["ref"]["unit"])
        if r is None:
            continue
        res, actual = result_of(r, w["ref"]["side"])
        out[w["settle_key"]] = {"result": res, "actual": actual, "actual_maps": r["games"], "source": "lol_settlements", "settled_at": r["settled_at"], "final": res is not None, "mark": _hash(r["version"], r["status"], r["actual"])}
    return out


# ---------------------------------------------------------------------------------------------------------------- research

def context_of(member_row):
    rows = []

    def add(label, value):
        if value not in (None, "", []):
            grp = "Form" if label.startswith(("Recent", "Maps counted")) else "Model" if label.startswith(("Median", "Team win", "Driver")) else "Matchup"
            rows.append({"label": label, "value": value, "group": grp})
    add("Role", member_row.get("role"))
    add("Opponent", member_row.get("opponent"))
    add("Competition", member_row.get("event"))
    add("Series length", f"Best of {member_row['best_of']}" if member_row.get("best_of") else None)
    add("Maps counted", member_row.get("scope_games"))
    add("Recent games", member_row.get("recent_games"))
    add("Recent per game", member_row.get("recent_per_game"))
    add("Median projection", member_row.get("median"))
    tw = member_row.get("team_win") or {}
    add("Team win chance, Scout", None if tw.get("elo") is None else f"{tw['elo'] * 100:.1f}%")
    mk = tw.get("market") or {}
    if mk.get("found") and mk.get("p") is not None:
        add("Team win chance, market", f"{mk['p'] * 100:.1f}%")
    return {"rows": rows, "drivers": [str(d) for d in member_row.get("drivers") or []][:6]}


def line_doc(srv, book, rid):
    x = _SNAP["index"].get((book, rid))
    if x is None:
        return 404, {"error": "not found"}
    rec = json.loads(x["rec"])
    values = series_values(_SNAP["series"].get(x["pid"]), x["stat"], x["scope"])
    books = []
    for bk, i in _SNAP["props"].get((x["eid"], x["pkey"], x["stat"], x["period"]), []):
        o = _SNAP["index"].get((bk, i))
        if o is not None:
            r = json.loads(o["rec"])
            books.append({"platform": bk, "platform_label": r["offer"]["platform_label"], "row_id": i, "key": r["key"], "line": o["line"], "line_type": o["lt"], "over": r["offer"]["over"], "under": r["offer"]["under"],
                          "seen_at": r["offer"]["seen_at"], "scout_side": o["side"], "p_side": r["scout"]["p_side"], "grade": r["scout"]["grade"]})
    forecast = {"id": x["fid"], "logged_at": x["at"], "model_version": x["model"], "p_saved": rec["scout"]["p_side"]} if x["fid"] else None
    return 200, {"record": rec, "forecast": forecast, "history": {"tally": rec["history"]["l10"], "avg": rec["history"]["avg"], "series": values[:20] or None}, "books": books, "market_books": None,
                 "h2h": workspace_h2h.h2h(values, rec["player"]["opponent"], x["line"]),
                 "context": context_of(x["member"]), "market": {"available": False, "reason": "LoL lines carry no market price."}}


# ---------------------------------------------------------------------------------------------------------------- results

def _group(name, m):
    return {"name": name, "picks": m.get("n") or 0, "wins": m.get("side_wins"), "losses": None if m.get("side_wins") is None else (m.get("n") or 0) - m["side_wins"], "pushes": None,
            "printed": m.get("mean_displayed_p_side"), "observed": m.get("side_hit_rate"), "matches": m.get("series")}


def results(srv):
    """The LoL worker's learning.json: its saved pre-match forecasts at offered lines. Not written yet raises Down: the page says so."""
    path = Path(srv.LOL_CENTER) / "learning.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Down("learning.json") from None
    cohort = doc.get("offered_side") or {}
    metrics = cohort.get("metrics") or {}
    # Older snapshots have no offered-only breakdowns/status counts. Never substitute the all-lines population.
    main = metrics.get("all") or (doc.get("metrics") or {}).get("side offered") or {}
    st = cohort.get("settled_by_status") or {}
    ci = ((doc.get("series_grouped_95ci") or {}).get("offered_side_only") or {}).get("side_hit") or {}
    stat = [_group(k[5:].replace("_", " ").title(), v) for k, v in sorted(metrics.items()) if k.startswith("stat ")]
    grade = [_group(k[6:], v) for k, v in sorted(metrics.items()) if k.startswith("grade ")]
    role = [_group(k[5:], v) for k, v in sorted(metrics.items()) if k.startswith("role ") and k[5:] not in ("None", "")]
    return {"sport": "lol", "label": "LoL forecast record", "as_of": doc.get("at"), 'calendar':doc.get('calendar'),
            "population": "Saved pre-match LoL forecasts on lines that offered both sides, each scored on official game stats. Over only and Under only lines are not graded and are not counted. Letters carry 0 units.",
            "rule": "One result per platform, market and line. Hit rate is results for Scout's side over graded results. Pushes stay apart, voids and lines with no settled rule stay out.",
            "totals": {"picks": main.get("n") or 0, "wins": main.get("side_wins"), "losses": None if main.get("side_wins") is None else (main.get("n") or 0) - main["side_wins"], "pushes": st.get("push", 0) if cohort else None, "observed": main.get("side_hit_rate"),
                       "ci": [ci["lo95"], ci["hi95"]] if ci.get("lo95") is not None else None, "printed": main.get("mean_displayed_p_side"), "matches": main.get("series"), "void": (st.get("void") or 0) + (st.get("dnp") or 0) if cohort else None, "match_word": "series"},
            "groups": {"chance": grade, "stat": stat, "role": role}, "gate": None, "market": None}
