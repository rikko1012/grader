"""Collecting game data into the game store - backfill of whole seasons, and the scheduled tick that keeps it current.

    python sports_scout.py games-backfill nba --season 2026        # 2025-26 (ESPN names an NBA season by its end year)
    python sports_scout.py games-backfill nfl --season 2025
    python sports_scout.py games-tick --leagues nba,nfl             # one scheduled pass (the worker runs it each minute)
    python sports_scout.py games-status

A tick does only what is due (times in sports_game_settings):
  finals     NOT polled here: the canonical settlement (sports/worker.py settle_league) reads each final box score once
             and hands the same body to ingest_body (box score + play-by-play + derived)
  context    pre-game, for games within 36 h every 30 min and within 7 days every 6 h: ESPN pickcenter / predictor /
             injuries from the game summary, and the ESPN-supplied DraftKings prop lines -> snapshots / prop history
  kalshi     every 15 min: Kalshi game ladders (winner, spread, total, team total, 1H, overtime, NFL TDs / sacks /
             turnovers) -> market history, matched to ESPN games
  injuries   every 30 min: ESPN's league injury report -> snapshot, through the canonical worker's cached read
  weather    with the NFL context: the NWS kickoff forecast for outdoor US venues -> snapshot
  power      daily: ESPN BPI / FPI team ratings -> snapshot
  rosters    daily: every team roster (the canonical worker's cached roster reads) -> athlete id, name, position, team (ESPN-supplied props name athletes by id only)
One tick at a time (an expiring lease in sports_game_settings). Polite pacing per host is net.py's.
"""
import json
import os
import socket
from datetime import datetime, timedelta, timezone

from .. import config, espn, net
from .. import worker as canonical
from . import derive, espn_game, espn_props, kalshi_lines, store, weather
from .store import dt, iso

HOLDER = f"{socket.gethostname()}:{os.getpid()}"
LEASE_TTL_S = 20 * 60
EVERY_MIN = {"kalshi": 15, "injuries": 30, "rosters": 24 * 60, "power": 24 * 60}
CONTEXT_NEAR_H, CONTEXT_NEAR_MIN, CONTEXT_FAR_H, CONTEXT_FAR_MIN = 36, 30, 7 * 24, 6 * 60
STOP_AFTER_UNPLAYED = 20
LEAGUES = ("nba", "nfl")


def _now():
    return datetime.now(timezone.utc)


def take_lease(db, now, ttl_s=LEASE_TTL_S):
    cur = db.execute("INSERT INTO sports_game_settings (key, value, updated_at) VALUES ('lease', ?, ?) ON CONFLICT "
                     "(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at WHERE "
                     "sports_game_settings.updated_at < ? OR sports_game_settings.value = ?",
                     (HOLDER, iso(now + timedelta(seconds=ttl_s)), iso(now), HOLDER))
    db.commit()
    return cur.rowcount == 1


def release_lease(db):
    db.execute("DELETE FROM sports_game_settings WHERE key = 'lease' AND value = ?", (HOLDER,))
    db.commit()


def _due(db, key, every_min, now):
    last = store.setting(db, f"at:{key}")
    return not last or dt(last) <= now - timedelta(minutes=every_min)


def _mark(db, key, now):
    store.set_setting(db, f"at:{key}", iso(now), now=now)


def _venue(db, lg, venue_id):
    """indoor / grass for a venue, read once (ESPN core API) and kept in settings."""
    if not venue_id:
        return None
    key = f"venue:{lg['key']}:{venue_id}"
    hit = store.setting(db, key)
    if hit:
        return json.loads(hit)
    try:
        v = espn_game.venue_raw(lg, venue_id)
        info = {"indoor": v.get("indoor"), "grass": v.get("grass"), "name": v.get("fullName")}
    except net.HttpError:
        info = {"indoor": None}
    store.set_setting(db, key, json.dumps(info))
    return info


def ingest_game(db, lg, event_id, now=None, final_only=True):
    """Fetch and store one game -> "stored" | "not final" | error text (backfill / manual). Football also reads the
    core plays (roles). The scheduled path does not call this: settlement hands its own body to ingest_body."""
    now = now or _now()
    try:
        body = espn_game.summary_raw(lg, event_id)
    except net.HttpError as e:
        fid = store.start_fetch(db, "game", lg["key"], event_id, now=now)
        store.finish_fetch(db, fid, False, http_status=e.status, error=str(e), now=now)
        return str(e)
    comp = ((body.get("header") or {}).get("competitions") or [{}])[0]
    if final_only and not bool(((comp.get("status") or {}).get("type") or {}).get("completed")):
        fid = store.start_fetch(db, "game", lg["key"], event_id, now=now)
        store.finish_fetch(db, fid, True, 0, now=now)
        return "not final"
    return ingest_body(db, lg, event_id, body, now)


def ingest_body(db, lg, event_id, body, now=None):
    """Store one game from an ESPN summary body already fetched (by settlement or ingest_game) -> "stored" | error."""
    now = now or _now()
    fid = store.start_fetch(db, "game", lg["key"], event_id, now=now)
    try:
        core = espn_game.core_plays_raw(lg, event_id) if lg["family"] == "football" else None
        venue_id = ((body.get("gameInfo") or {}).get("venue") or {}).get("id")
        g = espn_game.parse(body, lg["key"], lg["family"], core_plays=core, venue=_venue(db, lg, venue_id),
                            now_iso=iso(now))
        store.save_game(db, g)
        derive_game(db, lg, g["game"], g["plays"], g["players"])
        store.save_athletes(db, lg["key"], [{"athlete_id": p["athlete_id"], "player": p["player"],
                                             "position": p["position"], "team_id": None} for p in g["players"]],
                            now=now)
        if g["context"].get("injuries"):
            store.save_snapshot(db, lg["key"], event_id, "injuries", "espn", g["context"]["injuries"], now=now)
        if g["context"].get("pickcenter"):
            store.save_snapshot(db, lg["key"], event_id, "pickcenter", "espn", g["context"]["pickcenter"], now=now)
        store.finish_fetch(db, fid, True, len(g["plays"]) + len(g["players"]), now=now)
        return "stored"
    except net.HttpError as e:
        store.finish_fetch(db, fid, False, http_status=e.status, error=str(e), now=now)
        return str(e)
    except Exception as e:                                  # a malformed game never stops a backfill
        store.finish_fetch(db, fid, False, error=f"{type(e).__name__}: {e}", now=now)
        return f"{type(e).__name__}: {e}"


DERIVE_VERSION = "derive-1"


def derive_game(db, lg, game, plays, players, ep=None):
    """Play-by-play-only numbers for one stored game (derive.py) -> sports_game_derived."""
    team_of = {p["athlete_id"]: p["team_id"] for p in players}
    if lg["family"] == "basketball":
        per = derive.basketball_game(plays, players, game["home_id"], game["away_id"],
                                     period_s=600 if lg["key"] == "wnba" else 720)
        teams = {}
    else:
        per, teams = derive.football_game(plays, game["home_id"], game["away_id"], ep or derive.load_ep())
    store.save_derived(db, lg["key"], game["event_id"], {a: (team_of.get(a), st) for a, st in per.items()}, teams,
                       DERIVE_VERSION)


def rederive(db, league, refit_ep=False, train_seasons=None, progress=print):
    """Recompute every stored game's derived rows (after a derive.py change or a new expected-points fit)."""
    import json as _json
    lg = config.league(league)
    ep = None
    if lg["family"] == "football":
        if refit_ep:
            # frozen on EARLIER seasons only: a fit that saw a game cannot value features before that game ended
            train = train_seasons or sorted({r["season"] for r in db.all(
                "SELECT DISTINCT season FROM sports_game_games WHERE league = ?", (lg["key"],))})[:-1]
            if not train:
                raise ValueError("no completed earlier season to train the expected-points table on")
            marks = ",".join("?" * len(train))
            keep = ("league", "event_id", "seq", "period", "type", "text", "down", "distance", "yte", "scoring",
                    "turnover", "offense_id", "defense_id")
            rows = [{k: p[k] for k in keep}
                    for g in db.all(f"SELECT event_id FROM sports_game_games WHERE league = ? AND completed = 1 AND "
                                    f"season IN ({marks})", (lg["key"], *train))
                    for p in store.plays(db, lg["key"], g["event_id"])]
            ep = derive.fit_ep(rows)
            last = db.one(f"SELECT MAX(COALESCE(ended_at, start)) AS t FROM sports_game_games WHERE league = ? AND "
                          f"completed = 1 AND season IN ({marks})", (lg["key"], *train))
            ep.update(trained_seasons=list(train), trained_through=last["t"], version="ep-1")
            derive.EP_FILE.write_text(_json.dumps(ep, indent=1, sort_keys=True))
            progress(f"  expected points fitted on {ep['n_plays']} plays from {ep['n_games']} games of seasons "
                     f"{train} (through {last['t']}) -> {derive.EP_FILE}")
        ep = ep or derive.load_ep()
    games = db.all("SELECT * FROM sports_game_games WHERE league = ? AND completed = 1", (lg["key"],))
    for i, g in enumerate(games):
        plays = store.plays(db, lg["key"], g["event_id"])
        players = db.all("SELECT athlete_id, team_id, starter FROM sports_game_players WHERE league = ? AND "
                         "event_id = ?", (lg["key"], g["event_id"]))
        derive_game(db, lg, g, plays, players, ep)
        if (i + 1) % 200 == 0:
            progress(f"    derived {i + 1}/{len(games)}")
    return len(games)


def backfill(db, league, season, types=(2, 3), limit=None, progress=print):
    lg = config.league(league)
    out = {"stored": 0, "skipped_have": 0, "not final": 0, "errors": 0}
    for t in types:
        try:
            ids = espn_game.season_event_ids(lg, season, t)
        except net.HttpError as e:
            progress(f"  {league} {season} type {t}: {e}")
            continue
        progress(f"  {league} {season} type {t}: {len(ids)} games listed")
        unplayed = 0
        for i, ev in enumerate(ids):
            if unplayed >= STOP_AFTER_UNPLAYED:              # ESPN lists a season in date order: the rest is future
                out["not final"] += len(ids) - i
                break
            if limit is not None and out["stored"] >= limit:
                return out
            if store.has_game(db, lg["key"], ev):
                out["skipped_have"] += 1
                continue
            r = ingest_game(db, lg, ev)
            unplayed = unplayed + 1 if r == "not final" else 0
            if r == "stored":
                out["stored"] += 1
            elif r == "not final":
                out["not final"] += 1
            else:
                out["errors"] += 1
                progress(f"    {ev}: {r}")
            if (i + 1) % 50 == 0:
                progress(f"    {i + 1}/{len(ids)} {out}")
    return out


# ----------------------------------------------------------------------------------------------------------- tick

def _games_window(lg, now, back_days, ahead_days):
    return espn.scoreboard(lg, (now - timedelta(days=back_days)).date(), (now + timedelta(days=ahead_days)).date())


def _context(db, lg, games, now, clock):
    """Pre-game snapshots and ESPN-supplied prop lines for games due by their distance to the start. Rows are stamped
    with the clock at the moment each game was read (a tick can run for minutes)."""
    ath = store.athletes(db, lg["key"])
    out = {"games": 0, "snapshots": 0, "prop_rows": 0}
    for e in games:
        s = dt(e["start"]) if e.get("start") else None
        if not s or s <= now or e["completed"]:
            continue
        hours = (s - now).total_seconds() / 3600
        every = CONTEXT_NEAR_MIN if hours <= CONTEXT_NEAR_H else (CONTEXT_FAR_MIN if hours <= CONTEXT_FAR_H else None)
        key = f"context:{lg['key']}:{e['id']}"
        if every is None or not _due(db, key, every, now):
            continue
        fid = store.start_fetch(db, "context", lg["key"], e["id"], now=now)
        try:
            body = espn_game.summary_raw(lg, e["id"])
            ctx = espn_game.context(body)
            t = clock()
            for kind, data in ctx.items():
                out["snapshots"] += store.save_snapshot(db, lg["key"], e["id"], kind, "espn", data, now=t)
            rows, info = espn_props.fetch(lg, e["id"], ath)
            out["prop_rows"] += store.save_prop_lines(db, rows, now=clock())
            if lg["family"] == "football":
                out["snapshots"] += _weather(db, lg, e, body, clock)
            store.finish_fetch(db, fid, True, len(rows), now=now)
        except net.HttpError as err:
            store.finish_fetch(db, fid, False, http_status=err.status, error=str(err), now=now)
        _mark(db, key, now)
        out["games"] += 1
    return out


def _weather(db, lg, e, body, clock):
    """NWS kickoff forecast for an outdoor US game -> snapshot (indoor and foreign venues get none)."""
    v = (body.get("gameInfo") or {}).get("venue") or {}
    info = _venue(db, lg, v.get("id")) or {}
    if info.get("indoor"):
        return store.save_snapshot(db, lg["key"], e["id"], "weather", "nws", {"indoor": True}, now=clock())
    c = weather.coords(v.get("fullName"))
    if not c:
        return False
    cache = json.loads(store.setting(db, "nws_points", "{}"))
    try:
        fc = weather.forecast(c[0], c[1], e["start"], cache)
    except net.HttpError:
        return False
    store.set_setting(db, "nws_points", json.dumps(cache))
    if not fc:
        return False
    return store.save_snapshot(db, lg["key"], e["id"], "weather", "nws", dict(fc, indoor=False, venue=v.get("fullName")),
                               now=clock())


def season_year(lg, now):
    """ESPN's season year for the season in progress (NBA: the year it ends; NFL: the year it starts)."""
    if lg["family"] == "basketball":
        return now.year + 1 if now.month >= 8 else now.year
    return now.year if now.month >= 3 else now.year - 1


def _power(db, lg, now):
    """ESPN BPI (NBA) / FPI (NFL) -> daily snapshot [{team_id, rating, off, def}]."""
    url = f"{espn_game.CORE}/{lg['espn'][0]}/leagues/{lg['espn'][1]}/seasons/{season_year(lg, now)}/powerindex"
    body = net.get_json(url, params={"limit": 100})
    rows = []
    for it in body.get("items") or []:
        st = {}
        for grp in ("stats", "predictives", "efficiencies"):
            for x in it.get(grp) or []:
                st[x.get("name")] = espn_game._num(x.get("value", x.get("displayValue")))
        rows.append({"team_id": espn_game.ref_id(it.get("team")), "rating": st.get("bpi", st.get("fpi")),
                     "off": st.get("bpioffense", st.get("offefficiency")),
                     "def": st.get("bpidefense", st.get("defefficiency")), "updated": it.get("lastUpdated")})
    return store.save_snapshot(db, lg["key"], "league", "powerindex", "espn", rows, now=now)


def _kalshi(db, lg, games, now, clock):
    fid = store.start_fetch(db, "kalshi", lg["key"], now=now)
    try:
        rows, n = kalshi_lines.fetch(lg["key"])
        kalshi_lines.match(rows, lg["key"], games)
        new = store.save_market_lines(db, rows, now=clock())
        store.finish_fetch(db, fid, True, new, now=now)
        return {"markets": len(rows), "matched": sum(1 for r in rows if r["event_id"]), "new_rows": new,
                "requests": n}
    except net.HttpError as err:
        store.finish_fetch(db, fid, False, http_status=err.status, error=str(err), now=now)
        return {"error": str(err)}


def _rosters(db, lg, now):
    n = 0
    for t in espn.teams(lg):
        try:                     # the canonical worker's cached roster read (same key / TTL): no second request
            body = canonical._espn_roster(lg, t["id"])
        except net.HttpError:
            continue
        rows = []
        for a in body.get("athletes") or []:
            for x in (a.get("items") or []) if "items" in a else [a]:
                rows.append({"athlete_id": str(x.get("id")), "player": x.get("displayName"),
                             "position": (x.get("position") or {}).get("abbreviation"), "team_id": str(t["id"])})
        store.save_athletes(db, lg["key"], rows, now=now)
        n += len(rows)
    return n


def _injuries(db, lg, now):
    body = canonical._espn_injuries_raw(lg)     # the canonical worker's cached injury read: no second request
    rows = []
    for t in body.get("injuries") or []:
        for i in t.get("injuries") or []:
            a, d = i.get("athlete") or {}, i.get("details") or {}
            rows.append({"team_id": str(t.get("id")), "team": t.get("displayName"), "athlete_id": str(a.get("id")),
                         "player": a.get("displayName"), "status": i.get("status"), "date": i.get("date"),
                         "type": d.get("type"), "detail": d.get("detail"), "side": d.get("side"),
                         "return": d.get("returnDate")})
    return store.save_snapshot(db, lg["key"], "league", "injuries", "espn", rows, now=now)


def tick(db, leagues=LEAGUES, now=None, clock=None):
    """One scheduled pass -> summary. {"busy": True} when another tick holds the lease. `now` decides what is due;
    `clock` stamps observations (default: the real clock; tests pass a fixed one)."""
    clock = clock or ((lambda: now) if now else _now)
    now = now or _now()
    if not take_lease(db, now):
        return {"busy": True}
    out = {"at": iso(now)}
    for r in db.all("SELECT key, value FROM sports_game_settings WHERE key LIKE 'hold:%'"):
        t = dt(r["value"])                                 # a source's Retry-After from an earlier tick / process
        if t and t > now:
            net.hold(r["key"][len("hold:"):], t.timestamp())
    try:
        for key in leagues:
            lg = config.league(key)
            info = out.setdefault(key, {})
            try:
                if _due(db, f"rosters:{key}", EVERY_MIN["rosters"], now):
                    info["roster_athletes"] = _rosters(db, lg, now)
                    _mark(db, f"rosters:{key}", now)
                if _due(db, f"power:{key}", EVERY_MIN["power"], now):
                    info["powerindex_snapshot"] = bool(_power(db, lg, now))
                    _mark(db, f"power:{key}", now)
                if _due(db, f"injuries:{key}", EVERY_MIN["injuries"], now):
                    info["injury_snapshot"] = bool(_injuries(db, lg, now))
                    _mark(db, f"injuries:{key}", now)
                games = _games_window(lg, now, 0, 8)
                info["context"] = _context(db, lg, games, now, clock)
                if _due(db, f"kalshi:{key}", EVERY_MIN["kalshi"], now):
                    info["kalshi"] = _kalshi(db, lg, games, now, clock)
                    _mark(db, f"kalshi:{key}", now)
            except Exception as e:                       # one league's failure never stops the other
                info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            take_lease(db, now)
    finally:
        for host, until in net.holds().items():
            store.set_setting(db, f"hold:{host}", datetime.fromtimestamp(until, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"), now=now)
        release_lease(db)
    return out


def status(db):
    """Counts per league and the last fetch per kind."""
    out = {}
    for r in db.all("SELECT league, COUNT(*) AS games, MIN(start) AS first, MAX(start) AS last, SUM(n_plays) AS plays "
                    "FROM sports_game_games GROUP BY league"):
        out[r["league"]] = dict(r)
    for tbl, col in (("sports_game_players", "players"), ("sports_game_snapshots", "snapshots"),
                     ("sports_game_market_lines", "kalshi_rows"), ("sports_game_prop_lines", "espn_dk_prop_rows"),
                     ("sports_game_athletes", "athletes")):
        for r in db.all(f"SELECT league, COUNT(*) AS n FROM {tbl} GROUP BY league"):
            out.setdefault(r["league"], {})[col] = r["n"]
    out["last_fetches"] = db.all("SELECT kind, MAX(finished_at) AS last, SUM(1 - ok) AS failed, COUNT(*) AS n FROM "
                                 "sports_game_fetches GROUP BY kind")
    return out
