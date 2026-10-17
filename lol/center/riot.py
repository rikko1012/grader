"""
LoL Esports (Riot's lolesports.com data) into the data center - the source that needs no login, while Leaguepedia
needs a bot password. Uses the lol scout's client (lol/lolesports.py: its pacing, its compressed cache, its API key).

Stored like every source (store.put_rows: revisions, raw copy, event time = match start):
  LE:Match        one per match: league, tournament, start, best of, teams (Riot team ids, names, codes, game wins),
                  game slots in schedule order (number, Riot game id, state, sides)
  LE:Game         one per finished game: start, end, length, patch, each side's team id and kills, winner + basis
  LE:PlayerGame   one per player per finished game: Riot player id, name as shown, team, side, role, champion, K/D/A, CS
The "raw" copy of a game is the scout client's summary of the livestats frames (lolesports.game_stats), not the frames.

Coverage limits (checked 2026-10-17): no LPL; remakes and forfeits are not flagged by this source (left unknown);
the per-game winner is inferred (certain only for sweeps and each series' last game).

Units: le_live (every 30 min: the next 7 days' schedule + the last 3 days' results), le_backfill (8 months - LOL_BACKFILL_DAYS -
newest match first, 15 matches per unit, each match checkpointed).
"""
import json
import time
from datetime import datetime, timedelta, timezone

from . import store

LIVE_EVERY_S = 1800
BATCH = 15
BACKFILL_DAYS = int(__import__("os").environ.get("LOL_BACKFILL_DAYS", "245"))   # 8 months first


def _le():
    from .. import lolesports
    return lolesports


def _key_match(r):
    return r.get("match_id")


def _save(con, tbl, rows, key, raw, at, event_time=None):
    snap = store.save_raw(con, "lolesports", tbl, {"key": key(rows[0]) if rows else None}, json.dumps(raw, default=str).encode(),
                          n_rows=len(rows), fetched_at=at, dedupe=True)
    return store.put_rows(con, tbl, rows, key, event_time, snap, at)


def store_match(con, m, details, with_games=True):
    """One match (matches_since / slate entry + event_details) -> LE rows. Returns counts."""
    le = _le()
    at = store.now_iso()
    mt = details.get("match") or {}
    teams = [{"id": t.get("id"), "name": t.get("name"), "code": t.get("code"), "game_wins": (t.get("result") or {}).get("gameWins")}
             for t in mt.get("teams") or []]
    games = [{"number": g.get("number"), "id": g.get("id"), "state": g.get("state"),
              "sides": {t.get("id"): t.get("side") for t in g.get("teams") or []}} for g in mt.get("games") or []]
    row = {"match_id": str(details.get("id") or m.get("match_id")), "league_slug": (details.get("league") or {}).get("slug"),
           "league": (details.get("league") or {}).get("name"), "tournament_id": (details.get("tournament") or {}).get("id"),
           "tournament": m.get("tournament"), "start": m.get("start"), "best_of": (mt.get("strategy") or {}).get("count"),
           "state": m.get("state") or ("completed" if games and all(g["state"] in ("completed", "unneeded") for g in games) else "unknown"),
           "teams": teams, "games": games}
    ev = (m.get("start") or "").replace("T", " ").replace("Z", "")[:19] or None
    c = _save(con, "LE:Match", [row], _key_match, details, at, lambda r: ev)
    out = {"rows": 1, "new": c["new"], "changed": c["changed"]}
    if not with_games:
        return out
    for g in games:
        if g["state"] != "completed" or not g["id"]:
            continue
        st = le.game_stats(g["id"])
        if not st:
            continue
        sides = st.get("sides") or {}
        grow = {"game_id": str(g["id"]), "match_id": row["match_id"], "number": g["number"], "start": st.get("start"), "end": st.get("end"),
                "duration_s": st.get("duration_s"), "patch": st.get("patch"),
                "blue_team_id": (sides.get("blue") or {}).get("team_id"), "red_team_id": (sides.get("red") or {}).get("team_id"),
                "blue_kills": (sides.get("blue") or {}).get("kills"), "red_kills": (sides.get("red") or {}).get("kills"),
                "winner_side": st.get("winner_side"), "winner_basis": st.get("winner_basis")}
        prow = [{"game_id": str(g["id"]), "match_id": row["match_id"], "number": g["number"], "player_id": p.get("player_id"),
                 "summoner": p.get("summoner"), "name": le.clean_name(p.get("summoner"), next((t["code"] for t in teams if t["id"] == p.get("team_id")), None)),
                 "team_id": p.get("team_id"), "side": p.get("side"), "role": p.get("role"), "champion": p.get("champion"),
                 "kills": p.get("kills"), "deaths": p.get("deaths"), "assists": p.get("assists"), "cs": p.get("cs"), "gold": p.get("gold")}
                for p in st.get("players") or []]
        c1 = _save(con, "LE:Game", [grow], lambda r: r["game_id"], st, at, lambda r: ev)
        c2 = store.put_rows(con, "LE:PlayerGame", prow, lambda r: f"{r['game_id']}|{r['player_id']}", lambda r: ev, None, at)
        out["rows"] += 1 + len(prow)
        out["new"] += c1["new"] + c2["new"]
        out["changed"] += c1["changed"] + c2["changed"]
    return out


def _done(con, match_id):
    return con.execute("SELECT 1 FROM ref_state WHERE kind = 'LE:MatchDone' AND name = ?", (str(match_id),)).fetchone() is not None


def _mark_done(con, match_id):
    con.execute("INSERT OR REPLACE INTO ref_state (kind, name, checked_at, found) VALUES ('LE:MatchDone', ?, ?, 1)", (str(match_id), store.now_iso()))


def run_live(con, leagues=None):
    """Upcoming 7 days (schedule only) + matches completed in the last 3 days (with games)."""
    le = _le()
    leagues = leagues or le.MAJOR
    tot = {"rows": 0, "new": 0, "changed": 0, "upcoming": 0, "completed": 0}
    for m in le.slate(hours=24 * 7, league_slugs=leagues):
        c = store_match(con, m, le.event_details(m["match_id"]), with_games=m.get("state") == "inProgress")
        tot["upcoming"] += 1
        for k in ("rows", "new", "changed"):
            tot[k] += c[k]
        con.commit()
    since = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    for m in le.matches_since(leagues, since=since):
        c = store_match(con, m, le.event_details(m["match_id"]))
        _mark_done(con, m["match_id"])
        tot["completed"] += 1
        for k in ("rows", "new", "changed"):
            tot[k] += c[k]
        con.commit()
    store.set_meta(con, "le_live_at", store.now_iso())
    if tot["new"] or tot["changed"]:
        store.set_meta(con, "dirty", store.now_iso())
    con.commit()
    return tot


def run_backfill(con, leagues=None, days=BACKFILL_DAYS):
    """One unit: the next BATCH not-yet-stored completed matches of the last `days` days, newest first. -> counts or None."""
    le = _le()
    leagues = leagues or le.MAJOR
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    ms = sorted(le.matches_since(leagues, since=since), key=lambda m: m.get("start") or "", reverse=True)
    todo = [m for m in ms if not _done(con, m["match_id"])][:BATCH]
    if not todo:
        return None
    tot = {"rows": 0, "new": 0, "changed": 0, "matches": 0, "remaining": len([m for m in ms if not _done(con, m["match_id"])]) - len(todo)}
    for m in todo:
        c = store_match(con, m, le.event_details(m["match_id"]))
        _mark_done(con, m["match_id"])
        tot["matches"] += 1
        for k in ("rows", "new", "changed"):
            tot[k] += c[k]
        con.commit()
    store.set_meta(con, "dirty", store.now_iso())
    con.commit()
    return tot


def loop(state):
    """Worker thread: live every 30 min; backfill units in between."""
    con = store.connect()
    while True:
        try:
            last = store.get_meta(con, "le_live_at")
            if not last or last < store.now_iso(time.time() - LIVE_EVERY_S):
                state["live"] = dict(run_live(con), at=store.now_iso())
                continue
            b = run_backfill(con)
            state["backfill"] = dict(b, at=store.now_iso()) if b else {"done": True, "at": store.now_iso()}
            if not b:
                time.sleep(300)
        except Exception as e:
            state["error"] = {"at": store.now_iso(), "error": f"{type(e).__name__}: {str(e)[:300]}"}
            try:
                con.rollback()
            except Exception:
                pass
            time.sleep(600)
