#!/usr/bin/env python3
"""
underdog.py - Underdog Fantasy's CS2 pick'em board (Maps 1-2 kills and headshots).

One public request (no login): api.underdogfantasy.com/v1/over_under_lines?sport_id=CS - the same
feed the hltv-scraper `props` command reads. The board is kept in memory and refreshed in the
background every REFRESH seconds, so a member's grade never waits on Underdog; if Underdog can't be
reached, the last board is kept (and nothing else changes).

- board()            -> {"at", "rows": [{id, player, team, opponent, matchup, start, prop, line,
                         higher, lower, alt}], "error"}
- line_for(nick, prop) -> the board row for that player + prop, or None
"""
import re
import threading
import time
import unicodedata

URL = "https://api.underdogfantasy.com/v1/over_under_lines?sport_id=CS"
HEADERS = {"Origin": "https://underdogfantasy.com", "Referer": "https://underdogfantasy.com/",
           "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36", "Accept": "application/json"}
REFRESH = 5 * 60
_BOARD = {"at": 0.0, "rows": [], "error": None}
_LOCK = threading.Lock()
_BUSY = [False]
_START = threading.Lock()      # makes "not busy -> busy" one step: concurrent member reads start ONE refresh, never several


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _sides(title):
    parts = re.split(r"\s+(?:@|vs\.?)\s+", title or "", maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


def _scope(code, label):
    """Which maps a line covers: "12" Maps 1+2, "123" Maps 1+2+3 / 1-3, "1" Map 1 (BO1); None = other."""
    t = (code or label or "").lower().replace(" ", "")
    if "map" not in t:
        # 2026-10-14 owner: an unsupported label stays unsupported - a stat without a map set is not assumed to be
        # Maps 1-2 (the live feed sends exact codes: kills_on_maps_1_2 / headshots_on_maps_1_2, checked 2026-10-14)
        return None
    if re.search(r"maps?_?1(_|\+|-|&)2(_|\+|-|&)3|maps?_?1(_|-)3", t):
        return "123"
    if re.search(r"maps?_?1(_|\+|-|&)2$", t) or re.search(r"maps?1\+2(?![+\-&]?3)", t):
        return "12"
    if re.search(r"maps?_?1$", t):
        return "1"
    return None


_CODE = re.compile(r"^([a-z]+)(?:_on)?_maps?_?\d(?:[_+&-]\d)*$")         # "kills_on_maps_1_2"
_LABEL_MAPS = re.compile(r"(?:\bon\s+)?\bmaps?\s*\d(?:\s*[-+&]\s*\d)*")     # "Maps 1+2" / "on Maps 1-3"
_PROP = {"kills": "kills", "kill": "kills", "headshots": "hs", "headshot": "hs"}


def _prop(code, label):
    """kills / hs from Underdog's stat - the WHOLE stat name, exactly (2026-10-13 audit C14: "AWP Kills", "Opening Kills",
    "Kills + Assists" are other stats, never total kills): the exact stat code ("kills_on_maps_1_2") when it is one, else
    the exact label ("Maps 1+2 Kills"); a label naming the other stat skips the line. None = not graded."""
    m = _CODE.match(code or "")
    by_code = _PROP.get(m.group(1) if m else (code or ""))
    by_label = _PROP.get(" ".join(_LABEL_MAPS.sub(" ", (label or "").replace("(", " ").replace(")", " ")).split()))
    if by_code:
        return by_code if by_label in (None, by_code) else None
    return by_label


def parse(body):
    apps = {a["id"]: a for a in body.get("appearances", [])}
    players = {p["id"]: p for p in body.get("players", [])}
    games = {g["id"]: g for g in body.get("games", [])}
    rows = []
    for ln in body.get("over_under_lines", []):
        if ln.get("status") not in (None, "active"):
            continue
        stat = (ln.get("over_under") or {}).get("appearance_stat") or {}
        label = (stat.get("display_stat") or "").lower()
        code = (stat.get("stat") or "").lower()
        # Maps 1+2 only (what the grader grades). Underdog's stat code is exact ("kills_on_maps_1_2");
        # a "Maps 1+2+3" / "Maps 1-3" line must never be graded as Maps 1-2.
        scope = _scope(code, label)
        if scope is None:
            continue                                   # a map set the grader doesn't grade
        prop = _prop(code, label)
        app = apps.get(stat.get("appearance_id"))
        try:
            value = float(ln.get("stat_value"))
        except (TypeError, ValueError):
            continue
        if not prop or not app:
            continue
        pl = players.get(app.get("player_id"), {})
        name = " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x).strip()
        game = games.get(app.get("match_id")) or {}
        title = game.get("full_team_names_title") or game.get("title") or ""
        away, home = _sides(title)
        team = home if app.get("team_id") == game.get("home_team_id") else away if app.get("team_id") == game.get("away_team_id") else ""
        opp = away if team == home else home if team == away else ""
        # the sides Underdog offers on this line (2026-10-21 audit #9, the LoL parser's rule for the same API): an option
        # that is present with status None / "active"; a missing or suspended side is not offered (no payout, *_available
        # False), so the grader never stakes it and the board, card and slips agree
        offered = {o.get("choice") for o in ln.get("options") or [] if o.get("status") in (None, "active")}
        pay = {o.get("choice"): o.get("payout_multiplier") for o in ln.get("options") or [] if o.get("choice") in offered}
        dec = {}
        for o in ln.get("options") or []:              # Underdog's displayed per-pick price (decimal), when shown
            try:
                dec[o.get("choice")] = float(o.get("decimal_price")) if o.get("decimal_price") not in (None, "") else None
            except (TypeError, ValueError):
                dec[o.get("choice")] = None
        hi, lo = pay.get("higher"), pay.get("lower")
        alt = not (str(hi) in ("1.0", "1", "None") and str(lo) in ("1.0", "1", "None"))
        if ln.get("line_type") not in (None, "balanced"):     # an adjusted line (as lol/center/markets.py): never standard
            alt = True
        rows.append({"id": ln.get("id"), "scope": scope, "player": name, "team": team, "opponent": opp, "matchup": title,
                     "start": game.get("scheduled_at"), "prop": prop, "line": value,
                     "higher": float(hi) if hi not in (None, "") else None,
                     "lower": float(lo) if lo not in (None, "") else None, "alt": alt,
                     "over_available": "higher" in offered, "under_available": "lower" in offered,
                     # comparison layer (2026-10-09): Underdog's own ids, per-side price and the line's update time
                     "ud_player_id": app.get("player_id"), "ud_match_id": app.get("match_id"),
                     "price_higher": dec.get("higher"), "price_lower": dec.get("lower"), "updated_at": ln.get("updated_at")})
    return rows


def ids_of(body):
    """Underdog's own ids that open one line in Underdog (pplink.ud_link), by LINE id: {line id: (over_under_id, match_id,
    match_type)}. over_under_id = over_under_lines[].over_under_id (else over_under.id) - never the line or option id;
    match_id / match_type = the line's appearance. Kept beside the board, never in its rows (the board, cards, saved
    snapshots and slip records stay exactly as they were)."""
    apps = {a.get("id"): a for a in body.get("appearances", []) if isinstance(a, dict)}
    out = {}
    for ln in body.get("over_under_lines", []):
        if not isinstance(ln, dict) or not ln.get("id"):
            continue
        ou = ln.get("over_under") or {}
        app = apps.get((ou.get("appearance_stat") or {}).get("appearance_id")) or {}
        out[str(ln["id"])] = (ln.get("over_under_id") or ou.get("id"), app.get("match_id"), app.get("match_type"))
    return out


_IDS = {}           # line id -> (over_under_id, match_id, match_type) of the CURRENT board (replaced on each refresh)


def ud_ids(line_id):
    """(over_under_id, match_id, match_type) of a line on the current board, or None."""
    return _IDS.get(str(line_id)) if line_id else None


_LAST_SEEN = {}     # (player, prop, scope, start) -> (line, higher, lower) - only changes are logged


def _moves_db(con):
    """The moves table, with its scope column (2026-10-21 audit #40: rows logged before it have scope NULL)."""
    con.execute("""CREATE TABLE IF NOT EXISTS moves (at TEXT, player TEXT, team TEXT, opponent TEXT,
                   start TEXT, prop TEXT, line REAL, higher REAL, lower REAL, line_id TEXT, scope TEXT)""")
    if "scope" not in {c[1] for c in con.execute("PRAGMA table_info(moves)")}:
        con.execute("ALTER TABLE moves ADD COLUMN scope TEXT")


def _log_moves(rows):
    """Every new Underdog line and every change of line or payout, with the time (line-movement
    history: tested later against results - tracking only, never used in grades untested). Keyed per maps (2026-10-21
    audit #40: a Maps 1-2 and a Map 1 line of the same player overwrote each other and logged false moves every refresh)."""
    import os
    import sqlite3
    from pathlib import Path
    try:
        db = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / ".cache" / "ud_moves.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        if not _LAST_SEEN.get("_seeded") and db.exists():   # after a restart: the last logged state of each line
            con = sqlite3.connect(db, timeout=10)
            try:
                _moves_db(con)
                con.commit()
                # rows logged before the scope column (scope NULL) are not guessed into a scope: never seeded
                for pl, prop, scope, start, line, hi, lo in con.execute(
                        "SELECT player, prop, scope, start, line, higher, lower FROM moves WHERE rowid IN "
                        "(SELECT max(rowid) FROM moves WHERE scope IS NOT NULL GROUP BY player, prop, scope, start)"):
                    _LAST_SEEN.setdefault((norm(pl), prop, scope, start), (line, hi, lo))
            except sqlite3.Error:
                pass
            finally:
                con.close()
        _LAST_SEEN["_seeded"] = True
        new = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for r in rows:
            if r["alt"]:
                continue
            sc = r.get("scope") or "12"                     # parse() always sets it; "12" as everywhere else
            k = (norm(r["player"]), r["prop"], sc, r["start"])
            v = (r["line"], r["higher"], r["lower"])
            if _LAST_SEEN.get(k) != v:
                new.append((now, r["player"], r["team"], r["opponent"], r["start"], r["prop"], r["line"],
                            r["higher"], r["lower"], r["id"], sc))
                _LAST_SEEN[k] = v
        if not new:
            return
        con = sqlite3.connect(db, timeout=10)
        try:
            _moves_db(con)
            con.executemany("INSERT INTO moves (at, player, team, opponent, start, prop, line, higher, lower, line_id, scope) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", new)
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


_OPEN = {"by": None}    # "player|prop|scope|start" -> {"open", "first", "line", "moved_at"} (standard lines only)


def _open_path():
    import os
    from pathlib import Path
    return Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / ".cache" / "ud_open.json"


def _opening(rows, now=None):
    """Bumps (2026-10-19, display only - never a grade input): each standard line's OPENING value - the first line Scout
    observed for this player / stat / maps / start - and when it last moved, kept in .cache/ud_open.json. The first read
    after this existed seeds openings from the move log (ud_moves.db, keyed without the maps) only where that key has a
    single standard line on the board, so a Maps 1-2 and a Map 1 line are never mixed up. Never raises."""
    import json
    import os
    import sqlite3
    try:
        now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = _open_path()
        if _OPEN["by"] is None:
            try:
                _OPEN["by"] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _OPEN["by"] = {}
                db = path.with_name("ud_moves.db")
                std = [r for r in rows if not r["alt"]]
                one = {}
                for r in std:
                    one.setdefault((norm(r["player"]), r["prop"], r["start"]), []).append(r)
                if db.exists():
                    con = sqlite3.connect(db, timeout=10)
                    try:
                        for pl, prop, start, line, at in con.execute(
                                "SELECT player, prop, start, line, at FROM moves WHERE rowid IN "
                                "(SELECT min(rowid) FROM moves GROUP BY player, prop, start)"):
                            hit = one.get((norm(pl), prop, start)) or []
                            if len(hit) == 1:
                                r = hit[0]
                                _OPEN["by"]["|".join((norm(r["player"]), r["prop"], r["scope"], str(r["start"])))] = \
                                    {"open": line, "first": at, "line": line, "moved_at": None}
                    except sqlite3.Error:
                        pass
                    finally:
                        con.close()
        by, changed = _OPEN["by"], False
        for r in rows:
            if r["alt"]:
                continue
            k = "|".join((norm(r["player"]), r["prop"], r["scope"], str(r["start"])))
            e = by.get(k)
            if e is None:
                e = by[k] = {"open": r["line"], "first": now, "line": r["line"], "moved_at": None}
                changed = True
            elif e["line"] != r["line"]:
                e.update(line=r["line"], moved_at=now)
                changed = True
            r["open_line"], r["first_seen"], r["moved_at"] = e["open"], e["first"], e["moved_at"]
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3 * 86400))
        for k in [k for k in by if k.rsplit("|", 1)[-1][:19] < old]:      # matches long over
            del by[k]
            changed = True
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(by), encoding="utf-8")
            os.replace(tmp, path)
    except Exception:
        pass


def _refresh():
    global _IDS
    import requests
    try:
        r = requests.get(URL, headers=HEADERS, timeout=25)
        r.raise_for_status()
        body = r.json()
        rows = parse(body)
        try:
            ids = ids_of(body)
        except Exception:
            ids = {}
        _log_moves(rows)
        _opening(rows)
        with _LOCK:
            _BOARD.update(at=time.time(), rows=rows, error=None)
            _IDS = ids                                  # one swap: a reader sees the old or the new map, never half
    except Exception as e:                              # keep the last board; try again later
        with _LOCK:
            _BOARD.update(error=f"{type(e).__name__}: {str(e)[:160]}", at=_BOARD["at"] or 0.0)
            _BOARD["tried"] = time.time()
    finally:
        _BUSY[0] = False


def board(wait=False):
    """The current board. Stale -> refreshed in the background (or inline with wait=True)."""
    stale = time.time() - max(_BOARD["at"], _BOARD.get("tried", 0)) > REFRESH
    wait = wait or not (_BOARD["at"] or _BOARD.get("tried"))      # very first load: wait for it
    with _START:                                       # 700 members at once: only the first stale read starts a refresh
        start = stale and not _BUSY[0]
        if start:
            _BUSY[0] = True
    if start:
        if wait:
            _refresh()
        else:
            threading.Thread(target=_refresh, daemon=True, name="underdog").start()
    with _LOCK:
        return {"at": _BOARD["at"], "rows": list(_BOARD["rows"]), "error": _BOARD["error"]}


def line_for(nick, prop):
    """Underdog's standard line for this player and prop (from the cached board), or None."""
    n = norm(nick)
    rows = [r for r in board()["rows"] if r["prop"] == prop and norm(r["player"]) == n and r.get("scope", "12") == "12"]
    rows.sort(key=lambda r: (r["alt"], r["start"] or ""))
    return rows[0] if rows else None
