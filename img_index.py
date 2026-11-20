"""Which pictures belong to which board player, by EXACT ids only (v3 cards; img_cache.py stores and serves the files).

Per sport, from the board snapshot members already get (read only; nothing in the board payload changes):
  cs2     player.id is the BO3.gg player id. Picture and team come from the BO3.gg /teams and /players answers bo3.py already
          cached (kvstore bucket "bo3"; read here, bo3.py untouched). A player listed benched (status 2) or as a coach on his
          newest roster gets no picture. The team logo is shown only when that BO3.gg team's name equals the row's team text.
  nba/nfl player.id "nba:player:espn:<id>" -> the ESPN headshot; player.team_abbr (the ESPN abbreviation) -> the ESPN logo.
  soccer  ESPN headshot only for a resolved "soccer:player:espn:<id>"; then the PrizePicks picture for the offer's own
  tennis  PrizePicks player id (the sports service sends a per-player map, placeholders and shared art already removed).
  lol     none yet (lolesports images await the owner's call).

index(srv, sport) -> JSON bytes: {"p": {player.id: [headshot url, team logo url]}, "n": {name: player.id}, "tn": {team: logo url}}
("" where not ready) with READY pictures only, so a card never asks for a file that is not there. A name maps only when exactly one board player has
it. Built once per board body / picture version (single flight per sport) and served with the board's own ETag rules.
"""
import json
import re
import sqlite3
import threading
import time
import zlib

import img_cache as ic

ESPN_ID = re.compile(r"^(nba|nfl|soccer|tennis):player:espn:(\d{1,12})$")
PP_ID = re.compile(r"^(soccer|tennis):prizepicks:(\d{1,12})$")
SOCCER_TEAM = re.compile(r"^soccer:team:espn:(\d{1,10})$")
MIN_REBUILD_S = 15

_LOCK = threading.Lock()
_SPORT_LOCKS = {s: threading.Lock() for s in ic.SPORTS}
_MEMO = {}                                 # sport -> (body id, picture version, built at, bytes)
_BO3 = {"at": 0.0, "players": {}, "teams": {}}     # players: id -> (at, img, status, team_id, coach); teams: id -> (at, img, name)


# ---------------------------------------------------------------------------------------------------------------- BO3.gg cache
def _bo3_take(d, at, players, teams):
    if not isinstance(d, dict):
        return
    if isinstance(d.get("players"), list) and "slug" in d and "image_url" in d and d.get("id") is not None:   # /teams/<slug>
        tid = d["id"]
        old = teams.get(tid)
        if not old or old[0] <= at:
            teams[tid] = (at, d.get("image_url"), d.get("name"))
        for p in d["players"]:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            old = players.get(p["id"])
            if not old or old[0] <= at:
                players[p["id"]] = (at, p.get("image_url"), p.get("status"), tid, bool(p.get("is_coach")))
    elif "nickname" in d and "image_url" in d and d.get("id") is not None and "slug" in d:                     # /players/<slug>
        team = d.get("team") if isinstance(d.get("team"), dict) else {}
        old = players.get(d["id"])
        if not old or old[0] < at:
            # the profile has no roster status: keep the newest roster's status when that roster is at least as new
            status = old[2] if old and old[3] == team.get("id") else 1
            players[d["id"]] = (at, d.get("image_url"), status, team.get("id") or d.get("team_id"), bool(d.get("is_coach")))
        if team.get("id") is not None and team.get("image_url") is not None:
            old = teams.get(team["id"])
            if not old or old[0] < at:
                teams[team["id"]] = (at, team.get("image_url"), team.get("name"))


def bo3_scan(db_path=None, pause_every=25):
    """Read the BO3.gg answers saved since the last scan (by their saved time) and update the id -> picture maps. Runs on the
    warmer thread, never on a request. -> number of entries read."""
    if db_path is None:
        import kvstore
        db_path = kvstore.DB_PATH
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return 0
    players, teams = dict(_BO3["players"]), dict(_BO3["teams"])
    since, newest, n = _BO3["at"], _BO3["at"], 0
    try:
        cur = con.execute("SELECT at, data FROM kv WHERE bucket = 'bo3' AND at > ? ORDER BY at", (since,))
        for at, blob in cur:
            n += 1
            newest = max(newest, at)
            if pause_every and n % pause_every == 0:
                time.sleep(0.002)                      # step aside for request threads (GIL)
            try:
                raw = zlib.decompress(blob)
            except (zlib.error, TypeError):
                continue
            if b'"image_url"' not in raw:
                continue
            try:
                _bo3_take(json.loads(raw), at, players, teams)
            except ValueError:
                continue
    except sqlite3.Error:
        pass
    finally:
        con.close()
    with _LOCK:
        _BO3.update(at=newest, players=players, teams=teams)
    return n


def bo3_player(pid):
    """-> (picture url or None, team id or None) for a BO3.gg player id; no picture for a benched (2) or coach listing."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None, None
    p = _BO3["players"].get(pid)
    if not p:
        return None, None
    _, img, status, tid, coach = p
    if status == 2 or coach:
        return None, None
    return img, tid


def bo3_team(tid):
    t = _BO3["teams"].get(tid) if tid is not None else None
    return (t[1], t[2]) if t else (None, None)


# ---------------------------------------------------------------------------------------------------------------- per sport
def _records(srv, sport):
    """-> (body object, records list, offer index or None) from the sport's current snapshot, or (None, [], None)."""
    import workspace_api
    if sport == "cs2":
        snap = workspace_api.snapshot(srv)
    else:
        snap = workspace_api._adapter(sport).snapshot(srv, sport)
    if not snap or not snap.get("body"):
        return None, [], None
    body = snap["body"]
    try:
        recs = json.loads(body).get("records") or []
    except ValueError:
        recs = []
    return body, recs, snap


def _candidates(sport, rec, extra):
    """-> (player key, [urls]), (team key, [urls], team text) for one record. Exact ids only."""
    pl = rec.get("player") or {}
    pid = str(pl.get("id") or "")
    pkey = purls = tkey = turls = None
    tname = pl.get("team") or ""
    if sport == "cs2":
        img, tid = bo3_player(pl.get("id"))
        if img:
            pkey, purls = ("p", "cs2", str(int(pl["id"]))), [img]
        if tid is not None:
            timg, tn = bo3_team(tid)
            if timg and tn and tname and str(tn).strip().casefold() == str(tname).strip().casefold():
                tkey, turls = ("t", "cs2", str(int(tid))), [timg]
    elif sport in ("nba", "nfl"):
        m = ESPN_ID.match(pid)
        if m and m.group(1) == sport:
            pkey, purls = ("p", sport, m.group(2)), [ic.espn_headshot(sport, m.group(2))]
        ab = str(pl.get("team_abbr") or "").lower()
        if re.fullmatch(r"[a-z]{2,4}", ab):
            tkey, turls = ("t", sport, ab), [ic.espn_logo(sport, ab)]
    else:                                              # soccer / tennis
        urls, token = [], None
        m = ESPN_ID.match(pid)
        if m and m.group(1) == sport:
            urls.append(ic.espn_headshot(sport, m.group(2)))
            token = "e" + m.group(2)
        pp = (extra or {}).get(rec.get("_pp_id") or "")
        if pp:
            urls.append(pp)
            if token is None:
                token = "pp" + PP_ID.match(rec["_pp_id"]).group(2)
        if urls and token:
            pkey, purls = ("p", sport, token), urls
        tm = SOCCER_TEAM.match(str(rec.get("_team_id") or ""))
        if sport == "soccer" and tm:
            tkey, turls = ("t", "soccer", tm.group(1)), [ic.espn_logo("soccer", tm.group(1))]
    return (pkey, purls), (tkey, turls, tname)


def _expansion_extra(snap, recs):
    """soccer / tennis: the PrizePicks player id and ESPN team id behind each record (from the offer index the snapshot keeps),
    and the per-player PrizePicks picture map the sports service sends (workspace_expansion keeps it as snap['images'])."""
    images = {k: v for k, v in ((snap or {}).get("images") or {}).items() if isinstance(k, str) and PP_ID.match(k)}
    by_offer = {}
    for (_plat, oid), it in ((snap or {}).get("index") or {}).items():
        o, res = it.get("offer") or {}, it.get("research") or {}
        by_offer[oid] = (o.get("player_id"), (res.get("identity") or {}).get("team_id"))
    for r in recs:
        pp, team = by_offer.get((r.get("offer") or {}).get("offer_id"), (None, None))
        if isinstance(pp, str) and PP_ID.match(pp):
            r["_pp_id"] = pp
        if team:
            r["_team_id"] = team
    return images


def build(srv, sport):
    body, recs, snap = _records(srv, sport)
    if body is None:
        return None, b'{"n":{},"p":{},"tn":{}}'
    extra = _expansion_extra(snap, recs) if sport in ("soccer", "tennis") else None
    p, tn, names = {}, {}, {}
    for r in recs:
        pl = r.get("player") or {}
        (pkey, purls), (tkey, turls, tname) = _candidates(sport, r, extra)
        pid, name = str(pl.get("id") or ""), str(pl.get("name") or "").strip()
        img = logo = ""
        if pkey and purls:
            ic.register(pkey, purls)
            meta = ic.ready(pkey)
            if meta:
                img = ic.url_path(pkey, meta[0])
        if tkey and turls:
            ic.register(tkey, turls)
            meta = ic.ready(tkey)
            if meta:
                logo = ic.url_path(tkey, meta[0])
                if tname:
                    tn.setdefault(tname, set()).add(logo)
        if pid and (img or logo):
            p[pid] = [img, logo]
        if name:
            names.setdefault(name, set()).add(pid or ("?" + name))
    n = {name: next(iter(ids)) for name, ids in names.items() if len(ids) == 1 and next(iter(ids)) in p}
    out = {"p": p, "n": n, "tn": {k: next(iter(v)) for k, v in tn.items() if len(v) == 1}}
    return body, json.dumps(out, separators=(",", ":"), sort_keys=True).encode()


def index(srv, sport):
    """-> JSON bytes (memoised per board body and picture version; one build at a time per sport)."""
    if sport not in ic.SPORTS:
        return None
    ic.start_warmer()
    hit = _MEMO.get(sport)
    now = time.time()
    if hit and now - hit[2] < MIN_REBUILD_S:
        return hit[3]
    with _SPORT_LOCKS[sport]:
        hit = _MEMO.get(sport)
        if hit and now - hit[2] < MIN_REBUILD_S:
            return hit[3]
        v = ic.version()
        try:
            body, out = build(srv, sport)
        except Exception:                              # noqa: BLE001 - pictures are optional: an empty list, monograms show
            return hit[3] if hit else b'{"n":{},"p":{},"tn":{}}'
        if hit and hit[3] == out:
            out = hit[3]                               # same bytes, same object: send_shared keeps its ETag / gzip copy
        _MEMO[sport] = (id(body), v, time.time(), out)
        return out


def reset():
    with _LOCK:
        _MEMO.clear()
        _BO3.update(at=0.0, players={}, teams={})
