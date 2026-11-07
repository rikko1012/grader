"""Sports pre-game news archive job (pgn-1, see pregame_news/__init__.py): ESPN injury report, depth charts and - for games
starting within SPORTS_NEWS_GAME_H hours - the game summary's injury list and starter / active flags, saved change-only
to <SPORTS_DATA_DIR>/pregame_news.db with the time each was observed. Research input only: no board, card, grade,
forecast or member page reads it.

Sources are the reads the service already makes, through the same caches:
  injury report  worker._espn_injuries_raw (30-min cache shared with ingest / games)   -> kind injury  (scope league)
  depth charts   espn.depth_index (6-h cache shared with the scout)                    -> kind depth   (scope team:<abbr>)
  game summary   one ESPN summary request per game due (at most SPORTS_NEWS_MAX_GAMES per league per run)
                                                                     -> kinds game_injury / lineup (scope event:<id>)
Only the status, type and short detail of an injury are kept (never ESPN's news text). A failed read writes nothing,
so an outage never looks like "everyone got healthy".

    python -m sports.pregame_news run nba nfl      # the scheduler's job (every 15 min, SPORTS_INGEST + PREGAME_NEWS)
    python -m sports.pregame_news stats            # read-only
    python -m sports.pregame_news as-of nba <player_id> <iso time> [event_id]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pregame_news as core

from . import config, identity

SPORT = "sports"
_ATH_ID = re.compile(r"/id/(\d+)/")
GAME_H = float(os.environ.get("SPORTS_NEWS_GAME_H", "2"))
MAX_GAMES = int(os.environ.get("SPORTS_NEWS_MAX_GAMES", "20"))


def db_path() -> Path:
    return Path(os.environ.get("SPORTS_NEWS_DB") or config.DATA_DIR / "pregame_news.db")


def _ath_id(ath):
    if ath.get("id") and str(ath["id"]).isdigit():
        return str(ath["id"])
    href = " ".join(x.get("href") or "" for x in ath.get("links") or [])
    m = _ATH_ID.search(href)
    return m.group(1) if m else None


def parse_injuries(body, league):
    """ESPN league injury report -> {(player_id, ''): payload} or None when the body is not a report."""
    if not isinstance(body, dict) or not isinstance(body.get("injuries"), list):
        return None
    out = {}
    for t in body["injuries"]:
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            aid = _ath_id(ath)
            if not aid:
                continue
            det = i.get("details") or {}
            out[(identity.player_id(league, aid), "")] = {
                "name": ath.get("displayName"), "team": t.get("displayName"),
                "position": (ath.get("position") or {}).get("abbreviation"),
                "status": i.get("status"), "type": det.get("type"), "side": det.get("side"),
                "detail": det.get("detail"), "fantasy_status": (det.get("fantasyStatus") or {}).get("abbreviation"),
                "return_date": det.get("returnDate"), "espn_date": i.get("date")}
    return out


def parse_depth(idx, league):
    """espn.depth_index rows -> {team_abbr: {(player_id, ''): payload}} (one complete list per team read)."""
    teams = {}
    for row in (idx or {}).values():
        if not row.get("id"):
            continue
        teams.setdefault(row.get("team") or "?", {})[(identity.player_id(league, str(row["id"])), "")] = {
            "name": row.get("name"), "team": row.get("team"), "pos": row.get("pos"), "depth": row.get("depth"),
            "role": row.get("role")}
    return teams


def parse_summary(body, league):
    """ESPN game summary -> (game injuries {(pid, event): payload} or None, lineup flags {(pid, event): payload})."""
    inj = None
    if isinstance(body.get("injuries"), list):
        inj = {}
        for t in body["injuries"]:
            team = (t.get("team") or {}).get("abbreviation")
            for i in t.get("injuries") or []:
                ath = i.get("athlete") or {}
                aid = _ath_id(ath)
                if aid:
                    det = i.get("details") or {}
                    inj[aid] = {"name": ath.get("displayName"), "team": team, "status": i.get("status"),
                                "type": det.get("type"), "detail": det.get("detail"), "espn_date": i.get("date")}
    lineup = {}
    for team in (body.get("boxscore") or {}).get("players") or []:
        abbr = (team.get("team") or {}).get("abbreviation")
        for grp in team.get("statistics") or []:
            for a in grp.get("athletes") or []:
                ath = a.get("athlete") or {}
                aid = _ath_id(ath)
                if not aid or all(a.get(k) is None for k in ("starter", "active", "didNotPlay")):
                    continue
                lineup[aid] = {"name": ath.get("displayName"), "team": abbr, "starter": a.get("starter"),
                               "active": a.get("active"), "did_not_play": a.get("didNotPlay"),
                               "reason": a.get("reason") or None}
    for r in body.get("rosters") or []:                 # football summaries can carry game rosters
        abbr = (r.get("team") or {}).get("abbreviation")
        for a in r.get("roster") or []:
            ath = a.get("athlete") or {}
            aid = _ath_id(ath)
            if aid and aid not in lineup and any(a.get(k) is not None for k in ("starter", "active", "didNotPlay")):
                lineup[aid] = {"name": ath.get("displayName"), "team": abbr, "starter": a.get("starter"),
                               "active": a.get("active"), "did_not_play": a.get("didNotPlay"), "reason": None}
    return inj, lineup


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def run_league(con, lg, now=None) -> dict:
    from . import espn, worker
    from .games import espn_game
    league = lg["key"]
    now = now or datetime.now(timezone.utc)
    out = {}
    try:
        items = parse_injuries(worker._espn_injuries_raw(lg), league)
        if items is None:
            out["injury"] = {"ok": False, "error": "not an injury report"}
        else:
            out["injury"] = core.observe(con, SPORT, league, "injury", f"league:{league}", items,
                                         source="espn injuries", full=True)
    except Exception as e:      # noqa: BLE001 - a failed read writes nothing
        out["injury"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if league in espn.DEPTH_LEAGUES:
        try:
            agg = {"teams": 0}
            for abbr, items in parse_depth(espn.depth_index(lg), league).items():
                r = core.observe(con, SPORT, league, "depth", f"team:{league}:{abbr}", items,
                                 source="espn depth chart", full=True)
                agg["teams"] += 1
                for k, v in r.items():
                    if isinstance(v, int):
                        agg[k] = agg.get(k, 0) + v
            out["depth"] = agg
        except Exception as e:  # noqa: BLE001
            out["depth"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    games = {"due": 0, "read": 0, "errors": 0}
    try:
        due = []
        for e in espn.slate(lg, hours=max(GAME_H, 1), now=now):
            s = _dt(e.get("start"))
            if s and now < s <= now + timedelta(hours=GAME_H) and e.get("state") in (None, "pre"):
                due.append((s, e))
        games["due"] = len(due)
        for s, e in sorted(due, key=lambda x: x[0])[:MAX_GAMES]:
            try:
                body = espn_game.summary_raw(lg, e["id"])
            except Exception:   # noqa: BLE001
                games["errors"] += 1
                continue
            games["read"] += 1
            at, start = core.now_iso(), s.strftime("%Y-%m-%dT%H:%M:%SZ")
            if datetime.now(timezone.utc) >= s:
                continue                                  # read after the start: not pre-game news
            inj, lineup = parse_summary(body, league)
            scope = f"event:{league}:{e['id']}"
            if inj is not None:
                core.observe(con, SPORT, league, "game_injury", scope,
                             {(identity.player_id(league, a), e["id"]): p for a, p in inj.items()},
                             observed_at=at, source="espn summary", start_at=start, full=True)
            if lineup:
                core.observe(con, SPORT, league, "lineup", scope,
                             {(identity.player_id(league, a), e["id"]): p for a, p in lineup.items()},
                             observed_at=at, source="espn summary", start_at=start, full=False)
    except Exception as e:      # noqa: BLE001
        games["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["games"] = games
    return out


def run(leagues, now=None) -> dict:
    if not core.enabled():
        return {"skipped": "PREGAME_NEWS=0"}
    con = core.connect(db_path())
    try:
        res = {k: run_league(con, config.league(k), now) for k in leagues}
        res["bytes"] = core._size(con)
        return res
    finally:
        con.close()


def forecast_news(con, league, player_id, forecast_at, event_id=None):
    """What a forecast made at forecast_at could have known about this player (point-in-time, tombstones dropped)."""
    return core.as_of(con, SPORT, player_id, forecast_at, event=event_id)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sports.pregame_news")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("leagues", nargs="+")
    sub.add_parser("stats")
    a = sub.add_parser("as-of")
    a.add_argument("league")
    a.add_argument("player_id")
    a.add_argument("at")
    a.add_argument("event_id", nargs="?")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        from . import flags
        flags.require("SPORTS_INGEST")
        print(json.dumps(run(args.leagues), default=str))
    elif args.cmd == "stats":
        con = core.connect(db_path())
        print(json.dumps(core.stats(con), indent=1))
    else:
        con = core.connect(db_path())
        print(json.dumps(forecast_news(con, args.league, args.player_id, args.at, args.event_id), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
