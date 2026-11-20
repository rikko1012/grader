"""Which pictures belong to which board player, by EXACT ids only (v3 cards; img_cache.py stores and serves the files).

Per sport, from the board snapshot members already get (read only; nothing in the board payload changes):
  cs2     none: monogram, no logo. Owner 2026-11-20 "stop using BO3.gg, we have HLTV": no BO3.gg picture, and the HLTV
          scraper DB (players: playerid, name; teams: teamid, name, country) stores no picture URLs. No route, no list.
  nba/nfl player.id "nba:player:espn:<id>" -> the ESPN headshot; player.team_abbr (the ESPN abbreviation) -> the ESPN logo.
  soccer  ESPN headshot only for a resolved "soccer:player:espn:<id>"; then the PrizePicks picture for the offer's own
  tennis  PrizePicks player id (the sports service sends a per-player map, placeholders and shared art already removed).
  lol     none (lolesports images await the owner's call).

index(srv, sport) -> JSON bytes: {"p": {player.id: [headshot url, team logo url, team text]}, "n": {name: player.id}, "tn": {team: logo url}}
("" where not ready) with READY pictures only, so a card never asks for a file that is not there. A name maps only when exactly one board player has
it. Built once per board body / picture version (single flight per sport, never waited on: while one request builds, the others
get the last list, or an empty one before the first build) and served with the board's own ETag rules.
"""
import json
import re
import threading
import time

import img_cache as ic

ESPN_ID = re.compile(r"^(nba|nfl|soccer|tennis):player:espn:(\d{1,12})$")
PP_ID = re.compile(r"^(soccer|tennis):prizepicks:(\d{1,12})$")
SOCCER_TEAM = re.compile(r"^soccer:team:espn:(\d{1,10})$")
MIN_REBUILD_S = 15

_LOCK = threading.Lock()
_SPORT_LOCKS = {s: threading.Lock() for s in ic.SPORTS}
_MEMO = {}                                 # sport -> (body id, picture version, built at, bytes)


# ---------------------------------------------------------------------------------------------------------------- per sport
def _records(srv, sport):
    """-> (body object, records list, offer index or None) from the sport's current snapshot, or (None, [], None)."""
    import workspace_api
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
    if sport not in ic.SPORTS:                         # CS2 / LoL: no picture source (monogram)
        pass
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
        return None, EMPTY
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
            p[pid] = [img, logo, tname]
        if name:
            names.setdefault(name, set()).add(pid or ("?" + name))
    n = {name: next(iter(ids)) for name, ids in names.items() if len(ids) == 1 and next(iter(ids)) in p}
    out = {"p": p, "n": n, "tn": {k: next(iter(v)) for k, v in tn.items() if len(v) == 1}}
    return body, json.dumps(out, separators=(",", ":"), sort_keys=True).encode()


def _body_id(srv, sport):
    """id() of the sport's current board body without parsing it (None when there is none)."""
    try:
        import workspace_api
        snap = workspace_api._adapter(sport).snapshot(srv, sport)
        return id(snap["body"]) if snap and snap.get("body") else None
    except Exception:                                  # noqa: BLE001
        return None


EMPTY = b'{"n":{},"p":{},"tn":{}}'


def index(srv, sport):
    """-> JSON bytes. Rebuilt only when the board body or the set of stored pictures changed (checked at most every
    MIN_REBUILD_S); one build at a time per sport and NO request waits for it: while one request builds, every other request gets
    the last list (or EMPTY before the first build) at once (review 2026-11-20: a blocking lock gave a 10 s cold p99 at 700). A
    failed build is remembered for MIN_REBUILD_S too (the last good list, or an empty one), so a sports-service outage never turns
    into one rebuild per waiting member."""
    if sport not in ic.SPORTS:
        return None
    ic.start_warmer()
    hit = _MEMO.get(sport)
    if hit and time.time() - hit[2] < MIN_REBUILD_S:
        return hit[3]
    lock = _SPORT_LOCKS[sport]
    if not lock.acquire(blocking=False):               # another request is building: never wait on it
        hit = _MEMO.get(sport)
        return hit[3] if hit else EMPTY
    try:
        hit = _MEMO.get(sport)
        now = time.time()
        if hit and now - hit[2] < MIN_REBUILD_S:
            return hit[3]
        v = ic.version()                               # read BEFORE the build: a picture stored meanwhile rebuilds next time
        if hit and hit[1] == v and hit[0] is not None and hit[0] == _body_id(srv, sport):
            _MEMO[sport] = (hit[0], v, now, hit[3])    # same board, same pictures: nothing to rebuild
            return hit[3]
        try:
            body, out = build(srv, sport)
        except Exception:                              # noqa: BLE001 - pictures are optional: the last list, or none
            out = hit[3] if hit else EMPTY
            _MEMO[sport] = (None, v, now, out)
            return out
        if hit and hit[3] == out:
            out = hit[3]                               # same bytes, same object: send_shared keeps its ETag / gzip copy
        _MEMO[sport] = (id(body) if body is not None else None, v, now, out)
        return out
    finally:
        lock.release()


def reset():
    with _LOCK:
        _MEMO.clear()
