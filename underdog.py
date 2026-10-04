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


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _sides(title):
    parts = re.split(r"\s+(?:@|vs\.?)\s+", title or "", maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


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
        if "map" in label and not re.search(r"maps?\s*1\s*[+\-&]\s*2", label):
            continue                                   # Maps 1+2 only (what the grader grades)
        prop = "hs" if "headshot" in label else "kills" if "kill" in label else None
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
        pay = {o.get("choice"): o.get("payout_multiplier") for o in ln.get("options") or []}
        hi, lo = pay.get("higher"), pay.get("lower")
        alt = not (str(hi) in ("1.0", "1", "None") and str(lo) in ("1.0", "1", "None"))
        rows.append({"id": ln.get("id"), "player": name, "team": team, "opponent": opp, "matchup": title,
                     "start": game.get("scheduled_at"), "prop": prop, "line": value,
                     "higher": float(hi) if hi not in (None, "") else None,
                     "lower": float(lo) if lo not in (None, "") else None, "alt": alt})
    return rows


_LAST_SEEN = {}     # (player, prop, start) -> (line, higher, lower) - only changes are logged


def _log_moves(rows):
    """Every new Underdog line and every change of line or payout, with the time (line-movement
    history: tested later against results - tracking only, never used in grades untested)."""
    import os
    import sqlite3
    from pathlib import Path
    try:
        db = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / ".cache" / "ud_moves.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        new = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for r in rows:
            if r["alt"]:
                continue
            k = (norm(r["player"]), r["prop"], r["start"])
            v = (r["line"], r["higher"], r["lower"])
            if _LAST_SEEN.get(k) != v:
                new.append((now, r["player"], r["team"], r["opponent"], r["start"], r["prop"], r["line"],
                            r["higher"], r["lower"], r["id"]))
                _LAST_SEEN[k] = v
        if not new:
            return
        con = sqlite3.connect(db, timeout=10)
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS moves (at TEXT, player TEXT, team TEXT, opponent TEXT,
                           start TEXT, prop TEXT, line REAL, higher REAL, lower REAL, line_id TEXT)""")
            con.executemany("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)", new)
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def _refresh():
    import requests
    try:
        r = requests.get(URL, headers=HEADERS, timeout=25)
        r.raise_for_status()
        rows = parse(r.json())
        _log_moves(rows)
        with _LOCK:
            _BOARD.update(at=time.time(), rows=rows, error=None)
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
    if stale and not _BUSY[0]:
        _BUSY[0] = True
        if wait:
            _refresh()
        else:
            threading.Thread(target=_refresh, daemon=True, name="underdog").start()
    with _LOCK:
        return {"at": _BOARD["at"], "rows": list(_BOARD["rows"]), "error": _BOARD["error"]}


def line_for(nick, prop):
    """Underdog's standard line for this player and prop (from the cached board), or None."""
    n = norm(nick)
    rows = [r for r in board()["rows"] if r["prop"] == prop and norm(r["player"]) == n]
    rows.sort(key=lambda r: (r["alt"], r["start"] or ""))
    return rows[0] if rows else None
