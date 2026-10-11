"""Player roles for the slip builder (display / slip filtering only - the frozen grader's igl.csv is untouched).

Sources, in order:
0. The HLTV scraper's database: AWP share of kills from each player's HLTV weapon page (player_awp, last 90
   days) and its Liquipedia roles (player_roles, team_match only). Read in bulk every 30 minutes from the board
   loop - never inside a member's request.
1. Liquipedia player page infobox (`|roles=igl` / `awp,rifle` / `rifler`) - an explicit role listing. Used only
   when the page's team matches the board's team (namesakes), or the board team is an "ex-" roster.
2. Liquipedia team page, active squad `igl=y` (IGL only). A squad with nobody marked tells us nothing.
Anything else is unknown - never assumed.

At most one batched API request (up to 50 pages) per refresh() call and 30 s apart; the board loop calls it
once per pass. Cached on the volume for 2 days - within Liquipedia's API terms (identifying user agent, gzip,
well under 1 request / 2 s).
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

from bo3 import DATA_DIR

API = "https://liquipedia.net/counterstrike/api.php"
UA = "cs2-scout-roles/1.0 (+https://cs2-scout-production.up.railway.app)"
CACHE = Path(DATA_DIR) / ".cache" / "liquipedia_roles.json"
TTL = 2 * 86400               # re-read every 2 days (roster moves)
MIN_GAP = 30                  # seconds between requests, at least
_LOCK = threading.Lock()
_STATE = {"data": None, "last_fetch": 0.0}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load():
    if _STATE["data"] is None:
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if "teams" not in d:                      # older cache held teams only
            d = {"teams": d, "players": {}}
        _STATE["data"] = d
    return _STATE["data"]


def _save():
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_STATE["data"]), encoding="utf-8")
    except OSError:
        pass


def parse_igls(wikitext):
    """IGL ids (lowercased) in the page's active squad."""
    m = re.search(r"\{\{Squad\|status=active(.*?)\n\}\}", wikitext or "", re.S)
    if not m:
        return []
    out = []
    for person in re.findall(r"\{\{Person\|([^\n]*)", m.group(1)):
        if re.search(r"\|igl=y", person) and not re.search(r"\|role=(Coach|Analyst|Manager)", person, re.I):
            pid = re.search(r"\bid=([^|}]+)", person)
            if pid:
                out.append(pid.group(1).strip().lower())
    return out


def parse_player(wikitext):
    """{'roles': [...], 'teams': [...]} from a player page infobox; None for a disambiguation / non-player page."""
    t = wikitext or ""
    if not re.search(r"\{\{Infobox player", t, re.I):
        return None
    roles = []
    for m in re.finditer(r"^\|\s*roles?\d?\s*=\s*([^\n]*)", t, re.M):
        roles += [x.strip().lower() for x in re.split(r"[,/]", m.group(1)) if x.strip()]
    teams = [m.group(1).strip() for m in re.finditer(r"^\|\s*team\d?\s*=\s*([^\n|]*)", t, re.M) if m.group(1).strip()]
    return {"roles": roles, "teams": teams}


def _team_ok(board_team, page_teams):
    b = _norm(board_team)
    if not b or (board_team or "").lower().startswith("ex-") or not page_teams:
        return True                                   # no current team on one side (ex- roster / free agent on a mix)
    for t in page_teams:
        p = _norm(t)
        if p and (p in b or b in p):                  # exact name variants only - no 4-letter prefix guesses (2026-10-11 audit)
            return True
    return False


def _query(titles):
    """titles -> function(sent title) -> (final page title, wikitext)."""
    r = requests.get(API, params={"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                  "redirects": 1, "format": "json", "formatversion": 2, "titles": "|".join(titles)},
                     headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=30)
    q = r.json().get("query") or {}
    hop = {x["from"]: x["to"] for x in (q.get("normalized") or []) + (q.get("redirects") or [])}
    pages = {p["title"]: p for p in q.get("pages") or []}

    def text(title):
        for _ in range(4):
            title = hop.get(title, title)
        p = pages.get(title) or {}
        return title, (((p.get("revisions") or [{}])[0].get("slots") or {}).get("main") or {}).get("content") or ""
    return text


def refresh(players):
    """players: [(nick, team)] on the board. One request at most: player pages first, then team pages. Never raises."""
    d = _load()
    now = time.time()
    if now - _STATE["last_fetch"] < MIN_GAP:
        return 0
    players = [(n, t) for n, t in dict.fromkeys(players) if n]
    pl = [(n, t) for n, t in players if now - (d["players"].get(_norm(n)) or {}).get("at", 0) > TTL]
    tm = [t for t in dict.fromkeys(t for _, t in players) if t and not t.lower().startswith("ex-")
          and now - (d["teams"].get(_norm(t)) or {}).get("at", 0) > TTL]
    if not pl and not tm:
        return 0
    _STATE["last_fetch"] = now
    try:
        if pl:
            todo = list({_norm(n): n for n, _ in pl}.values())[:50]
            sent = [n.replace(" ", "_") for n in todo]
            text = _query(sent)
            with _LOCK:
                for n, s in zip(todo, sent):
                    title, body = text(s)
                    info = parse_player(body)
                    d["players"][_norm(n)] = {"at": now, "page": title if info else None, **(info or {"roles": [], "teams": []})}
                _save()
            return len(todo)
        todo = tm[:50]
        sent = [t.replace(" ", "_") for t in todo]
        text = _query(sent)
        with _LOCK:
            for t, s in zip(todo, sent):
                title, body = text(s)
                d["teams"][_norm(t)] = {"at": now, "page": title if body else None, "igl": parse_igls(body)}
            _save()
        return len(todo)
    except Exception:
        return 0


# ---- the HLTV scraper's database (read from here, not hltv.py: hltv.py is part of the grader's version tag)
HLTV_TTL = 30 * 60
AWP_SHARE, RIFLE_SHARE, AWP_MIN_KILLS = 0.25, 0.10, 100   # share of kills with the AWP (90 days)
_HLTV = {"at": 0.0, "rows": {}}


def refresh_hltv():
    """Bulk-load roles + AWP share from the scraper's database (30-minute cache). Never raises."""
    if time.time() - _HLTV["at"] < HLTV_TTL:
        return
    _HLTV["at"] = time.time() - HLTV_TTL + 30     # a failed load is tried again in 30 s (it used to mark 30 minutes done first:
    try:                                           # one blip after a restart = no AWP roles for half an hour, 2026-10-11 audit)
        import hltv
        if not hltv.dsn():
            return
        if not _HLTV["rows"]:
            _HLTV["rows"] = _last_snapshot()         # restart: the last saved snapshot until the database answers
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute("""SELECT p.name, r.hltv_team, r.wiki_title, r.roles, r.is_igl, r.is_awper, r.team_match,
                                      r.updated_at, a.kills, a.awp_kills, a.awp_share, a.end_date
                               FROM players p
                               LEFT JOIN player_roles r USING (playerid)
                               LEFT JOIN player_awp a USING (playerid)
                               WHERE r.playerid IS NOT NULL OR a.playerid IS NOT NULL""")
                rows = {}
                for (name, team, wiki, roles_, igl, awper, tmatch, upd, kills, awpk, share, end) in cur.fetchall():
                    rows.setdefault(_norm(name), []).append(dict(
                        team=team, wiki=wiki, roles=[x.strip() for x in (roles_ or "").split(",") if x.strip()],
                        igl=igl, awper=awper, team_match=tmatch, updated=str(upd)[:10] if upd else None,
                        kills=kills, awp_kills=awpk, awp_share=float(share) if share is not None else None,
                        end=str(end) if end else None))
        finally:
            con.close()
        _HLTV.update(rows=rows, at=time.time())
        _snapshot(rows)
    except Exception:
        pass                                          # tables not there yet / database down: other sources


def _last_snapshot():
    """The newest saved role snapshot (role_snapshots.jsonl) - yesterday's roles beat none at all."""
    try:
        last = None
        with open(SNAP, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return (json.loads(last).get("players") or {}) if last else {}
    except (OSError, ValueError):
        return {}


SNAP = Path(DATA_DIR) / "role_snapshots.jsonl"
_SNAP_DAY = {"day": None}


def _snapshot(rows):
    """COLLECTION ONLY (2026-10-09, never read by grading): once a UTC day, the role / AWP-share rows as HLTV has them
    that day, plus weapon kills when the scraper has that table - so a factor test can use only what was known BEFORE
    each match (player_awp itself is overwritten). Appended to DATA_DIR/role_snapshots.jsonl."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if _SNAP_DAY["day"] == day:
        return
    try:
        if SNAP.exists():
            with open(SNAP, encoding="utf-8") as f:
                for line in f:
                    pass
                if line.strip() and json.loads(line).get("day") == day:
                    _SNAP_DAY["day"] = day
                    return
    except (OSError, ValueError, UnboundLocalError):
        pass
    weapons = None
    try:
        import hltv
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute("SET statement_timeout = 60000")
                cur.execute("SELECT * FROM player_weapon_kills LIMIT 50000")
                cols = [c[0] for c in cur.description]
                weapons = {"cols": cols, "rows": [[str(v) if not isinstance(v, (int, float, type(None))) else v for v in r] for r in cur.fetchall()]}
        finally:
            con.close()
    except Exception:
        weapons = None                                # table not there: roles + AWP share only
    rec = {"day": day, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": rows, "weapon_kills": weapons}
    try:
        with open(SNAP, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n"); f.flush(); os.fsync(f.fileno())
        _SNAP_DAY["day"] = day
    except OSError:
        pass


def hltv_status():
    return {"players": len(_HLTV["rows"]), "loaded_at": _HLTV["at"]}


def _hltv(nick, team):
    rows = _HLTV["rows"].get(_norm(nick)) or []
    hit = [r for r in rows if r.get("team") and _team_ok(team, [r["team"]])]
    if hit:
        return hit[0]
    return rows[0] if len(rows) == 1 and not (rows[0].get("team") and team) else None


def _player(nick, team):
    p = _load()["players"].get(_norm(nick))
    if p and p.get("page") and p.get("roles") and _team_ok(team, p.get("teams") or []):
        return p
    return None


def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def igl_role(nick, team_name):
    """(True / False / None, source)."""
    h = _hltv(nick, team_name)
    if h and h.get("team_match") and h.get("roles"):
        return bool(h["igl"]), f"Liquipedia via HLTV scraper ({h['wiki'] or nick}: {', '.join(h['roles'])}), checked {h['updated']}"
    p = _player(nick, team_name)
    if p:
        return "igl" in p["roles"], f"Liquipedia {p['page']} (roles: {', '.join(p['roles'])}), checked {_day(p['at'])}"
    t = _load()["teams"].get(_norm(team_name))
    if t and t.get("page") and t.get("igl"):           # a squad with its IGL marked: the others are not the IGL
        return (_norm(nick) in {_norm(x) for x in t["igl"]},
                f"Liquipedia {t['page']} roster (IGL marked), checked {_day(t['at'])}")
    return None, "unknown - no Liquipedia role listing found"


def awp_role(nick, team_name):
    """(True / False / None, source): HLTV AWP share of kills first, then Liquipedia roles."""
    h = _hltv(nick, team_name)
    if h and h.get("awp_share") is not None and (h.get("kills") or 0) >= AWP_MIN_KILLS:
        sh = h["awp_share"]
        src = f"HLTV weapon stats: {sh:.0%} of {h['kills']} kills with the AWP (90 days to {h['end']})"
        if sh >= AWP_SHARE:
            return True, src
        if sh <= RIFLE_SHARE:
            return False, src
    if h and h.get("team_match") and h.get("roles"):
        return bool(h["awper"]), f"Liquipedia via HLTV scraper ({h['wiki'] or nick}: {', '.join(h['roles'])}), checked {h['updated']}"
    p = _player(nick, team_name)
    if p:
        return ("awp" in p["roles"] or "awper" in p["roles"],
                f"Liquipedia {p['page']} (roles: {', '.join(p['roles'])}), checked {_day(p['at'])}")
    return None, None


def is_igl(nick, team_name):
    return igl_role(nick, team_name)[0]
