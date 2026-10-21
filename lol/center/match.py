"""
Feed offer -> canonical LoL series, team and player. Exact names only, always constrained by the schedule:

1. team and opponent: every exact (normalized) alias of the feed's names - full name and abbreviation - from Leaguepedia
   (page, name, short, redirects) plus the hand-checked lol/team_aliases.json; a name may map to several team ids
2. series: Leaguepedia series scheduled within +-18 h of the offer's start with one team from each side; exactly one
   or the offer is quarantined (series_ambiguous: rematches, duplicates) / unmatched (no_scheduled_series)
3. player: the offer's name against the aliases of players who played for that team in the last 120 days or are on its
   roster for that tournament; exactly one or quarantined (player_ambiguous) / unmatched (player_not_on_team)

The same organization's academy, and the same organization in CS2, are different ids: a LoL id is "lol:..." and every
candidate must sit in the scheduled series. Results are stored in market_links with history.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import store
from .markets import _pair
from .names import norm, page_key

WINDOW_H = 18
ROSTER_DAYS = 120
ALIASES_FILE = Path(__file__).with_name("team_aliases.json")

def _when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _file_aliases():
    try:
        return json.loads(ALIASES_FILE.read_text(encoding="utf-8")).get("teams") or {}
    except (OSError, ValueError):
        return {}


def team_ids(con, names, platform=None):
    """All team ids whose exact alias equals one of the feed names."""
    out = set()
    fa = _file_aliases()
    for n in names:
        k = norm(n)
        if not k:
            continue
        out |= {r[0] for r in con.execute("SELECT DISTINCT eid FROM aliases WHERE kind = 'team' AND norm = ?", (k,))}
        for key in (f"{platform}:{n}", n):
            page = fa.get(key)
            if page:
                eid = store.find_entity(con, "team", "leaguepedia:page", page_key(page))
                if eid:
                    out.add(eid)
    return out


def candidate_players(con, tid, tournament_page, before_iso):
    """Players who played for the team in the last ROSTER_DAYS before the series, or are on its tournament roster."""
    lo = (datetime.strptime(before_iso[:10], "%Y-%m-%d") - timedelta(days=ROSTER_DAYS)).strftime("%Y-%m-%d")
    pids = {r[0] for r in con.execute("SELECT DISTINCT pid FROM c_player_games WHERE tid = ? AND start_utc >= ? AND start_utc < ?",
                                      (tid, lo, before_iso))}
    page = con.execute("SELECT skey FROM entity_sources WHERE eid = ? AND kind = 'team' AND active = 1", (tid,)).fetchall()
    team_pages = {r[0] for r in page}
    for r in store.current_rows(con, "TournamentPlayers", "json_extract(r.data, '$.OverviewPage') = ?", (tournament_page,)):
        if page_key(r.get("Team")) in team_pages and r.get("Link"):
            eid = store.find_entity(con, "player", "leaguepedia:page", page_key(r["Link"]))
            if eid is None:
                for (pg,) in con.execute("SELECT json_extract(r.data, '$.OverviewPage') FROM lp_current c JOIN lp_rows r ON "
                                         "r.tbl = c.tbl AND r.skey = c.skey AND r.rev = c.rev WHERE c.tbl = 'PlayerRedirects' AND "
                                         "c.gone_at IS NULL AND json_extract(r.data, '$.AllName') = ?", (r["Link"],)):
                    eid = eid or store.find_entity(con, "player", "leaguepedia:page", page_key(pg))
            if eid:
                pids.add(eid)
    return pids


def recent_role(con, pid, before_iso):
    rows = con.execute("SELECT role FROM c_player_games WHERE pid = ? AND start_utc < ? AND role IS NOT NULL ORDER BY start_utc DESC LIMIT 10",
                       (pid, before_iso)).fetchall()
    if not rows:
        return None
    roles = [r[0] for r in rows]
    top = max(set(roles), key=roles.count)
    return top


def link(con, offer):
    """One offer (a markets.* normalized dict or a market_events row with its data) -> link result dict."""
    ex = offer.get("extra") or {}
    start = _when(offer.get("start_time"))
    if start is None:
        return {"status": "unmatched", "reason": "no_start_time"}
    pair = (ex.get("matchup_names") or _pair(offer.get("match_title"))) if not offer.get("team") else None
    if pair and len(pair) == 2:
        return _link_by_pair(con, offer, start, pair)
    own = team_ids(con, [offer.get("team"), ex.get("team_abbr")], offer.get("platform"))
    opp = team_ids(con, [offer.get("opponent"), ex.get("opponent_abbr")], offer.get("platform"))
    if not own or not opp:
        return {"status": "unmatched", "reason": "team_not_known" if not own else "opponent_not_known",
                "detail": {"team": offer.get("team"), "opponent": offer.get("opponent")}}
    lo = (start - timedelta(hours=WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (start + timedelta(hours=WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cands = []
    for r in con.execute("SELECT * FROM c_series WHERE COALESCE(scheduled_utc, first_game_utc) >= ? AND COALESCE(scheduled_utc, first_game_utc) <= ?",
                         (lo, hi)):
        pair = {r["team1"], r["team2"]}
        if (r["team1"] in own and r["team2"] in opp) or (r["team2"] in own and r["team1"] in opp):
            cands.append(dict(r))
    if not cands:
        return {"status": "unmatched", "reason": "no_scheduled_series", "detail": {"own": sorted(own), "opp": sorted(opp)}}
    if len(cands) > 1:
        return {"status": "quarantined", "reason": "series_ambiguous", "detail": {"series": [c["sid"] for c in cands]}}
    s = cands[0]
    tid = s["team1"] if s["team1"] in own else s["team2"]
    opp_tid = s["team2"] if tid == s["team1"] else s["team1"]
    if offer.get("market_type") == "combo":
        return {"status": "unmatched", "reason": "combined_market_not_mapped", "sid": s["sid"], "tid": tid, "opp_tid": opp_tid}
    before = s["scheduled_utc"] or s["first_game_utc"] or start.strftime("%Y-%m-%dT%H:%M:%SZ")
    pool = candidate_players(con, tid, s["tournament_page"], before)
    k = norm(offer.get("player"))
    hits = {r[0] for r in con.execute("SELECT DISTINCT eid FROM aliases WHERE kind = 'player' AND norm = ?", (k,))} & pool if k else set()
    if not hits:
        return {"status": "unmatched", "reason": "player_not_on_team", "sid": s["sid"], "tid": tid, "opp_tid": opp_tid,
                "detail": {"player": offer.get("player"), "pool": len(pool)}}
    if len(hits) > 1:
        return {"status": "quarantined", "reason": "player_ambiguous", "sid": s["sid"], "tid": tid, "opp_tid": opp_tid,
                "detail": {"players": sorted(hits)}}
    pid = next(iter(hits))
    return {"status": "matched", "reason": None, "sid": s["sid"], "tid": tid, "opp_tid": opp_tid, "pid": pid,
            "role": recent_role(con, pid, before), "best_of": s["best_of"]}


def _series_between(con, a, b, start):
    lo = (start - timedelta(hours=WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (start + timedelta(hours=WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [dict(r) for r in con.execute("SELECT * FROM c_series WHERE COALESCE(scheduled_utc, first_game_utc) >= ? AND "
                                         "COALESCE(scheduled_utc, first_game_utc) <= ?", (lo, hi))
            if (r["team1"] in a and r["team2"] in b) or (r["team2"] in a and r["team1"] in b)]


def _link_by_pair(con, offer, start, pair):
    """The feed names the two teams but not which one the player is on: the series must be the one scheduled between
    those two teams, and the player's exact name must be on exactly one of the two teams' recent rosters."""
    a, b = team_ids(con, [pair[0]], offer.get("platform")), team_ids(con, [pair[1]], offer.get("platform"))
    if not a or not b:
        return {"status": "unmatched", "reason": "team_not_known", "detail": {"matchup": pair}}
    cands = _series_between(con, a, b, start)
    if not cands:
        return {"status": "unmatched", "reason": "no_scheduled_series", "detail": {"matchup": pair}}
    if len(cands) > 1:
        return {"status": "quarantined", "reason": "series_ambiguous", "detail": {"series": [c["sid"] for c in cands]}}
    s = cands[0]
    before = s["scheduled_utc"] or s["first_game_utc"] or start.strftime("%Y-%m-%dT%H:%M:%SZ")
    k = norm(offer.get("player"))
    named = {r[0] for r in con.execute("SELECT DISTINCT eid FROM aliases WHERE kind = 'player' AND norm = ?", (k,))} if k else set()
    hits = {(t, p) for t in (s["team1"], s["team2"]) for p in candidate_players(con, t, s["tournament_page"], before) & named}
    if not hits:
        return {"status": "unmatched", "reason": "player_not_on_team", "sid": s["sid"], "detail": {"player": offer.get("player")}}
    if len(hits) > 1:
        return {"status": "quarantined", "reason": "player_ambiguous", "sid": s["sid"], "detail": {"hits": sorted(map(list, hits))}}
    tid, pid = next(iter(hits))
    opp_tid = s["team2"] if tid == s["team1"] else s["team1"]
    return {"status": "matched", "reason": None, "sid": s["sid"], "tid": tid, "opp_tid": opp_tid, "pid": pid,
            "role": recent_role(con, pid, before), "best_of": s["best_of"], "basis": "player on exactly one of the two named teams"}


def link_open_markets(con, at=None):
    """Link every OPEN market in the archive (latest state). Changes are written with history. Returns counts. A closed
    market keeps its last stored link: nothing reads the links of closed markets (forecasts, the board and the status
    file read open ones; settlement reads the saved forecast), and relinking the whole archive grew without bound
    (audit 13)."""
    at = at or store.now_iso()
    counts = {}
    rows = con.execute("SELECT e.platform, e.market_key, e.data FROM market_state s JOIN market_events e ON e.id = s.last_event_id "
                       "WHERE s.open = 1").fetchall()
    for platform, key, data in rows:
        o = json.loads(data)
        o["platform"] = platform
        res = link(con, o)
        counts[f"{res['status']}:{res.get('reason')}"] = counts.get(f"{res['status']}:{res.get('reason')}", 0) + 1
        prev = con.execute("SELECT status, reason, sid, tid, opp_tid, pid FROM market_links WHERE platform = ? AND market_key = ?",
                           (platform, key)).fetchone()
        new = (res["status"], res.get("reason"), res.get("sid"), res.get("tid"), res.get("opp_tid"), res.get("pid"))
        if prev is None or tuple(prev) != new:
            con.execute("INSERT INTO market_link_history (platform, market_key, at, status, reason, sid, tid, opp_tid, pid) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (platform, key, at) + new)
        con.execute("INSERT INTO market_links (platform, market_key, status, reason, sid, tid, opp_tid, pid, role, detail, linked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, market_key) DO UPDATE SET status = excluded.status, "
                    "reason = excluded.reason, sid = excluded.sid, tid = excluded.tid, opp_tid = excluded.opp_tid, pid = excluded.pid, "
                    "role = excluded.role, detail = excluded.detail, linked_at = excluded.linked_at",
                    (platform, key) + new + (res.get("role"), json.dumps(res.get("detail"), default=str), at))
        if res["status"] == "quarantined":
            store.quarantine(con, "market", f"{platform}|{key}", res["reason"], res.get("detail"), at)
    con.commit()
    return counts
