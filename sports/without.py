"""'Games without' teammate index (gw-1): for every team, which rostered players did NOT play each of its final games -
point-in-time, from the stored box scores and game logs. Built in the WORKER (python -m sports.sheets build), served
by sports/api_sheets.py; the player page module web/sports/without.js joins it to the player's own history.

File: <snapshots>/<league>/sheets/without.json
  {"players": {pid: name}, "teams": {team_id: {"abbr", "games": {event_id: {"s": start, "se": season,
                                                                           "out": {pid: "dnp" | "absent"}}}}}}

WHO WAS ON THE TEAM AT A GAME (team_at - no inference beyond these rules, a player not placed is simply not listed):
  1. a game-log membership dated exactly at that game (ESPN game log: the team he played for);
  2. a roster membership whose interval covers the game start;
  3. bracketed: his game-log memberships just before AND just after the game (same season: seasons start after
     August, by date) are both with the team;
     or just before (same season) with the team and his current roster membership is still the team;
  4. a player whose only known team is the team, listed in that game's box score.
WHO DID NOT PLAY: on the team at that game (above) and either
  "dnp"     the box score / game log lists him as did not play or inactive (explicit), or
  "absent"  no stat line of his for that game while the game's box score or his teammates' game logs exist
            (placed on the team by rule 1-3 only - an unplaced player is never counted absent).
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from . import snapshots, store
from .contracts import now_utc

VERSION = "gw-1"
REFRESH_S = 3600
HISTORY_SOURCE = getattr(snapshots, "HISTORY_SOURCE", "espn_gamelog")


def path(league, base=None) -> Path:
    from .sheets import sheet_dir
    return sheet_dir(league, base) / "without.json"


def _participation(con, league):
    """(event_id, player_id) -> "played" | "dnp" from the stored stat lines (latest revision per identity)."""
    rep = "min" if league == "nba" else None
    q = ("SELECT source, source_key, revision, event_id, player_id, payload FROM records WHERE kind='stat' AND "
         f"league=? AND {store.CURRENT_SQL}" + (" AND stat=?" if rep else ""))
    best = {}
    for r in con.execute(q, (league, rep) if rep else (league,)):
        k = (r["source"], r["source_key"])
        cur = best.get(k)
        if cur is None or r["revision"] > cur[0]:
            best[k] = (r["revision"], r["event_id"], r["player_id"], r["payload"])
    out = {}
    for _, eid, pid, payload in best.values():
        if not eid or not pid:
            continue
        part = store._unpack(payload).get("participation")
        val = "played" if part == "played" else ("dnp" if part in ("did_not_play", "inactive") else None)
        if val is None:
            continue
        cur = out.get((eid, pid))
        if cur != "played":                             # any "played" line wins (box score and game log agree)
            out[(eid, pid)] = val
    return out


SAME_GAME_S = 12 * 3600                         # a game-log date and the scoreboard start of the same game


def ts(iso):
    from datetime import datetime, timezone
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def season_of(t):
    """Season key by date (NBA and NFL seasons both start after August): Oct 2026 and Feb 2027 -> 2026."""
    import time as _t
    g = _t.gmtime(t)
    return g.tm_year if g.tm_mon >= 8 else g.tm_year - 1


def team_at(mems, start, event_teams, in_box):
    """Rules 1-4 of the module docstring. mems: [(valid_from, valid_to, team_id, is_gamelog)] of one player, times as
    epoch seconds (valid_to None = open)."""
    season = season_of(start)
    for vf, vt, tid, gl in mems:
        if gl and abs(vf - start) <= SAME_GAME_S:
            return tid, 1
    for vf, vt, tid, gl in mems:
        if not gl and vf <= start and (vt is None or vt >= start):
            return tid, 2
    gls = sorted((vf, tid) for vf, vt, tid, gl in mems if gl)
    before = [x for x in gls if x[0] < start - SAME_GAME_S and season_of(x[0]) == season]
    after = [x for x in gls if x[0] > start + SAME_GAME_S and season_of(x[0]) == season]
    if before:
        tb = before[-1][1]
        if after and after[0][1] == tb:
            return tb, 3
        if not after:
            cur = [tid for vf, vt, tid, gl in mems if not gl and vt is None]
            if cur and cur[-1] == tb:
                return tb, 3
    teams = {tid for _, _, tid, _ in mems}
    if in_box and len(teams) == 1:
        t = next(iter(teams))
        if t in event_teams:
            return t, 4
    return None, None


def build(con, league, now=None, base=None):
    t0 = time.time()
    now = now or now_utc()
    events = {}
    for _, e, _ in snapshots._latest(con, "event", "league = ?", [league], now):
        if not e.start_utc or e.start_utc > now or e.status != "final" or not e.home_team_id or not e.away_team_id:
            continue
        cur = events.get(e.event_id)
        if cur is None or (e.start_source == "espn" and cur.start_source != "espn"):
            events[e.event_id] = e
    part = _participation(con, league)
    by_event = defaultdict(dict)
    for (eid, pid), v in part.items():
        if eid in events:
            by_event[eid][pid] = v
    mems = defaultdict(list)
    for _, m, _ in snapshots._latest(con, "membership", "player_id LIKE ?", [league + ":%"], now):
        mems[m.player_id].append((ts(m.valid_from), ts(m.valid_to) if m.valid_to else None, m.team_id,
                                  m.source == HISTORY_SOURCE))
    teams = {t.team_id: t for _, t, _ in snapshots._latest(con, "team", "league = ?", [league], now)}
    team_players = defaultdict(set)
    for pid, ms in mems.items():
        for _, _, tid, _ in ms:
            team_players[tid].add(pid)
    out_teams = {}
    named = set()
    for tid in sorted(team_players):
        games = {}
        for eid, e in events.items():
            if tid not in (e.home_team_id, e.away_team_id) or eid not in by_event:
                continue
            seen = by_event[eid]
            out = {}
            for pid in team_players[tid]:
                st = seen.get(pid)
                if st == "played":
                    continue
                t, rule = team_at(mems[pid], ts(e.start_utc), (e.home_team_id, e.away_team_id), pid in seen)
                if t != tid:
                    continue
                if st == "dnp":
                    out[pid] = "dnp"
                elif rule in (1, 2, 3):
                    out[pid] = "absent"
            games[eid] = {"s": e.start_utc, "se": e.season, "out": out}
            named.update(out)
        if games:
            out_teams[tid] = {"abbr": teams[tid].abbr if tid in teams else None, "games": games}
    names = {}
    ids = sorted(named)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for _, p, _ in snapshots._latest(con, "player", f"player_id IN ({','.join('?' * len(chunk))})", chunk, now):
            names[p.player_id] = p.name
    doc = {"version": VERSION, "league": league, "built_at": now_utc(), "as_of": now, "players": names,
           "teams": out_teams,
           "method": {"on_team": "game-log membership at the game, roster interval covering it, game logs with the team "
                                 "just before and after (same season), or only known team + in the box score",
                      "dnp": "listed as did not play / inactive in the box score or game log",
                      "absent": "on the team by membership but no stat line for that game"}}
    size = snapshots._write(path(league, base), doc)
    return {"league": league, "ok": True, "teams": len(out_teams), "games": sum(len(t["games"]) for t in out_teams.values()),
            "bytes": size, "build_s": round(time.time() - t0, 2)}


def build_if_stale(con, league, force=False, base=None):
    p = path(league, base)
    try:
        if not force and time.time() - p.stat().st_mtime < REFRESH_S:
            return {"league": league, "ok": True, "skipped": "fresh"}
    except OSError:
        pass
    return build(con, league, base=base)


def team_slice(doc, team_id):
    t = (doc.get("teams") or {}).get(team_id)
    if t is None:
        return None
    pids = {p for g in t["games"].values() for p in g["out"]}
    return {"version": doc.get("version"), "league": doc.get("league"), "built_at": doc.get("built_at"),
            "team_id": team_id, "abbr": t.get("abbr"), "games": t["games"],
            "players": {p: (doc.get("players") or {}).get(p) for p in sorted(pids)}, "method": doc.get("method")}


def load(league, base=None):
    try:
        return json.loads(path(league, base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
