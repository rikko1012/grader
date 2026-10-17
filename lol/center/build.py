"""
Canonical LoL tables from the stored Leaguepedia rows: internal ids with source mappings, deterministic and repeatable.

- teams    one id per team page (TeamRedirects resolve name variants only when they point at a known team page);
           academy and main teams are different pages, so different ids - never merged by name similarity
- players  one id per player page; a scoreboard link is resolved through PlayerRedirects (renames); a link that maps to
           two pages, or a rename onto a page that already has its own id, goes to quarantine instead of being guessed
- series   one id per Leaguepedia MatchId; a MatchId that disappears and reappears under a new id (page restructured)
           keeps its series id only when exactly one gone series has the same tournament, team pair and date
- games    one id per game (Riot platform game id when known, else the Leaguepedia GameId); game order comes from
           the schedule's N_GameInMatch (cross-checked against the scoreboard), never from API return order
- player games: one row per player per game; checks: 10 players, each team's player kills = team kills

c_series / c_games / c_player_games are rebuilt from scratch on every build; ids, mappings, history and quarantine persist.
"""
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import store
from .names import norm, page_key, role

SRC_PAGE, SRC_MATCH, SRC_GAME, SRC_RIOT = "leaguepedia:page", "leaguepedia:match", "leaguepedia:game", "riot:platform_game"


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "yes", "true")


def _dt(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _iso(s):
    d = _dt(s)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def team_resolver(con, at):
    teams = {page_key(r["OverviewPage"]): r for r in store.current_rows(con, "Teams") if r.get("OverviewPage")}
    redirect = defaultdict(set)
    for r in store.current_rows(con, "TeamRedirects"):
        tgt = page_key(r.get("Page"))
        if tgt in teams and r.get("AllName"):
            redirect[page_key(r["AllName"])].add(tgt)

    def resolve(name):
        n = page_key(name)
        if not n or n.upper() == "TBD":
            return None
        if n in teams:
            return n
        tg = redirect.get(n) or set()
        if len(tg) == 1:
            return next(iter(tg))
        if len(tg) > 1:
            store.quarantine(con, "team", n, "redirect_ambiguous", sorted(tg), at)
            return None
        return n                              # scoreboard names are team page names; unresolved ones stay as written

    cache = {}

    def tid(name):
        page = resolve(name)
        if page is None:
            return None
        if page not in cache:
            cache[page] = store.entity(con, "team", SRC_PAGE, page, label=page, at=at)
            info = teams.get(page) or {}
            for a in {page, info.get("Name"), info.get("Short")} - {None, ""}:
                store.add_alias(con, "team", a, cache[page], "leaguepedia", at)
            if page not in teams:
                store.quarantine(con, "team", page, "no_team_page_row", None, at)
        return cache[page]

    for alias, tgts in redirect.items():
        if len(tgts) == 1:
            t = tid(next(iter(tgts)))
            if t:
                store.add_alias(con, "team", alias, t, "leaguepedia:redirect", at)
    return tid, teams


def player_resolver(con, at):
    pages = {page_key(r["OverviewPage"]): r for r in store.current_rows(con, "Players") if r.get("OverviewPage")}
    redirect = defaultdict(set)
    ids = {}
    for r in store.current_rows(con, "PlayerRedirects"):
        if r.get("AllName") and r.get("OverviewPage"):
            redirect[page_key(r["AllName"])].add(page_key(r["OverviewPage"]))
            if r.get("ID"):
                ids[page_key(r["OverviewPage"])] = r["ID"]
    checked = {r[0] for r in con.execute("SELECT name FROM ref_state WHERE kind = 'PlayerRedirects'")}
    names_of = defaultdict(set)                  # page -> every name that redirects only to it
    for a, t in redirect.items():
        if len(t) == 1:
            names_of[next(iter(t))].add(a)
    cache = {}

    def resolve(link):
        n = page_key(link)
        if not n:
            return None, "empty"
        tg = redirect.get(n) or set()
        if len(tg) == 1:
            return next(iter(tg)), None
        if len(tg) > 1:
            store.quarantine(con, "player", n, "redirect_ambiguous", sorted(tg), at)
            return None, "ambiguous"
        if n in pages:
            return n, None
        if n not in checked:
            return None, "not_checked_yet"
        store.quarantine(con, "player", n, "no_player_page", None, at)
        return n, None

    def pid(link):
        page, why = resolve(link)
        if page is None:
            return None, why
        if page in cache:
            return cache[page], None
        eid = store.find_entity(con, "player", SRC_PAGE, page)
        if eid is None:
            # a rename: an earlier page of this player (now a redirect to `page`) already has an id
            olds = names_of.get(page, set()) - {page}
            prior = {store.find_entity(con, "player", SRC_PAGE, o) for o in olds} - {None}
            if len(prior) == 1:
                eid = store.link_source(con, next(iter(prior)), "player", SRC_PAGE, page, "renamed: the earlier page now redirects here", at)
            elif len(prior) > 1:
                store.quarantine(con, "player", page, "rename_onto_two_ids", sorted(prior), at)
                return None, "ambiguous"
            else:
                eid = store.entity(con, "player", SRC_PAGE, page, label=page, at=at)
        else:
            store.entity(con, "player", SRC_PAGE, page, at=at)
        cache[page] = eid
        info = pages.get(page) or {}
        for a in {page, info.get("ID"), ids.get(page)} | names_of.get(page, set()):
            if a:
                store.add_alias(con, "player", a, eid, "leaguepedia", at)
        return eid, None

    return pid, pages


_PG_COLS = ("gid", "pid", "tid", "opp_tid", "sid", "game_n", "start_utc", "tournament_page", "patch", "side", "role", "champion",
            "kills", "deaths", "assists", "team_kills", "gold", "cs", "damage", "vision", "win", "name_shown")


def _flush(con, pgs):
    n = len(pgs)
    con.executemany(f"INSERT OR REPLACE INTO c_player_games ({', '.join(_PG_COLS)}) VALUES ({', '.join('?' * len(_PG_COLS))})",
                    [tuple(p[c] for c in _PG_COLS) for p in pgs])
    pgs.clear()
    return n


SRC_LE_TEAM, SRC_LE_PLAYER, SRC_LE_MATCH, SRC_LE_GAME = "lolesports:team", "lolesports:player", "lolesports:match", "lolesports:game"


def le_patch(version):
    """The livestats game version '16.20.826.3891' -> the patch name Leaguepedia uses, '26.20' (Riot's year-numbered
    patches since 2025: game version 15.x = patch 25.x, 16.x = 26.x). Anything else is kept as given."""
    try:
        a, b = str(version).split(".")[:2]
        a, b = int(a), int(b)
        return f"{a + 10}.{b:02d}" if a >= 15 else f"{a}.{b}"
    except (ValueError, TypeError):
        return version


def le_winners(games, score, side_team):
    """Game id -> winning Riot team id. Certain first (as the lol scout's sync): a sweep's games and a series' last game
    go to the series winner; the other games keep the inferred winner only when the inferred wins add up to the score."""
    if not games:
        return {}
    ranked = sorted(score, key=lambda t: score[t] or 0, reverse=True)
    sw = ranked[0] if len(ranked) == 2 and (score[ranked[0]] or 0) > (score[ranked[1]] or 0) else None
    if sw and not (score[ranked[1]] or 0):
        return {g["game_id"]: sw for g in games}
    inferred = {g["game_id"]: side_team[g["game_id"]].get(g.get("winner_side")) for g in games}
    wins = Counter(inferred.values())
    ok = None not in inferred.values() and all(wins.get(t, 0) == (n or 0) for t, n in score.items())
    out = {g["game_id"]: (inferred[g["game_id"]] if ok else None) for g in games}
    if sw:
        out[games[-1]["game_id"]] = sw
    return out


def _z(s):
    return (str(s)[:19] + "Z") if s else None


def _attach(con, kind, source, skey, target, at):
    """Map one more source id onto `target`; when that source id already has its own internal id, the two ids are
    merged (the older id survives, so ids already saved elsewhere stay valid). -> 1 when anything changed."""
    cur = store.find_entity(con, kind, source, str(skey))
    target = store.canonical_id(con, target)
    if cur == target:
        return 0
    if cur is None:
        store.link_source(con, target, kind, source, str(skey), "same series held by both sources", at)
        return 1
    keep, drop = sorted([cur, target], key=lambda e: int(e.rsplit(":", 1)[1]))
    store.merge(con, keep, drop, f"{kind} identified in a series both sources hold", at)
    return 1


def crosscheck(con, overlaps, at):
    """Data check on every real series both sources hold: game numbers, winners, team kills, and each player's kills /
    deaths / assists (players paired by game number, team and role). Every comparison is a row in `crosscheck`
    (rewritten each build); disagreements are kept, never "fixed" - the history uses Leaguepedia's copy."""
    con.execute("DELETE FROM crosscheck")
    if not overlaps:
        return {}
    lg, lpg = defaultdict(list), defaultdict(list)
    for r in store.current_rows(con, "LE:Game"):
        lg[r["match_id"]].append(r)
    for r in store.current_rows(con, "LE:PlayerGame"):
        lpg[r["match_id"]].append(r)
    rows = []
    for mid, m, s in overlaps:
        tmap = {}
        for t in m.get("teams") or []:
            e = store.find_entity(con, "team", SRC_LE_TEAM, str(t.get("id")))
            if e:
                tmap[t["id"]] = store.canonical_id(con, e)
        lp_games = {r["game_n"]: dict(r) for r in con.execute("SELECT * FROM c_games WHERE sid = ?", (s["sid"],))}
        le_games = {g.get("number"): g for g in lg.get(mid, [])}
        rows.append((s["sid"], mid, "games_count", None, None, str(len(lp_games)), str(len(le_games)), int(len(lp_games) == len(le_games))))
        for n in sorted(set(lp_games) & set(le_games)):
            a, b = lp_games[n], le_games[n]
            le_blue = tmap.get(b.get("blue_team_id"))
            if le_blue and a["team1"]:
                rows.append((s["sid"], mid, "blue_side_team", n, None, a["team1"], le_blue, int(a["team1"] == le_blue)))
            lk = {a["team1"]: a["team1_kills"], a["team2"]: a["team2_kills"]}
            ek = {tmap.get(b.get("blue_team_id")): b.get("blue_kills"), tmap.get(b.get("red_team_id")): b.get("red_kills")}
            for t in (a["team1"], a["team2"]):
                if t in ek:
                    rows.append((s["sid"], mid, "team_kills", n, t, str(lk.get(t)), str(ek.get(t)), int(lk.get(t) == ek.get(t))))
            lw = a["winner_tid"]
            ew = tmap.get(b.get("blue_team_id") if b.get("winner_side") == "blue" else b.get("red_team_id") if b.get("winner_side") == "red" else None)
            if lw and ew:
                rows.append((s["sid"], mid, "winner", n, None, lw, ew, int(lw == ew)))
            slot = {}
            for r in con.execute("SELECT game_n, tid, role, pid, kills, deaths, assists FROM c_player_games WHERE sid = ? AND game_n = ?",
                                 (s["sid"], n)):
                slot[(r[1], r[2])] = r
            for p in lpg.get(mid, []):
                if p.get("number") != n:
                    continue
                q = slot.get((tmap.get(p.get("team_id")), role(p.get("role"))))
                if not q:
                    rows.append((s["sid"], mid, "player_unpaired", n, None, None, str(p.get("player_id")), 0))
                    continue
                for stat, i in (("kills", 4), ("deaths", 5), ("assists", 6)):
                    rows.append((s["sid"], mid, f"player_{stat}", n, q[3], str(q[i]), str(p.get(stat)), int(q[i] == p.get(stat))))
    con.executemany("INSERT INTO crosscheck (sid, le_match_id, item, game_n, pid, lp_value, le_value, agree, checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [r + (at,) for r in rows])
    summary = {}
    for item, n, ok in con.execute("SELECT item, COUNT(*), SUM(agree) FROM crosscheck GROUP BY item"):
        summary[item] = {"compared": n, "agree": ok or 0, "share": round((ok or 0) / n, 4) if n else None}
    summary["series"] = len(overlaps)
    return summary


def link_overlaps(con, overlaps, at):
    """For every real series held by both sources (the LoL Esports copy was left out): its two LoL Esports team ids are
    the two Leaguepedia teams with exactly those names / codes, and each LoL Esports player id is the Leaguepedia
    player at the same (game number, team, role) - in every game, or the pair is quarantined, never guessed."""
    if not overlaps:
        return 0
    lpg = defaultdict(list)
    for r in store.current_rows(con, "LE:PlayerGame"):
        lpg[r["match_id"]].append(r)
    changed = 0
    for mid, m, s in overlaps:
        lp_tids = {s["team1"], s["team2"]}
        tmap = {}
        for t in m.get("teams") or []:
            hits = {r[0] for n in (t.get("name"), t.get("code")) if n
                    for r in con.execute("SELECT eid FROM aliases WHERE kind = 'team' AND norm = ? AND source NOT LIKE 'lolesports%'",
                                         (norm(n),))} & lp_tids
            if len(hits) == 1:
                tmap[t["id"]] = next(iter(hits))
        if len(tmap) != 2 or len(set(tmap.values())) != 2:
            store.quarantine(con, "series", f"lolesports:{mid}", "cross_source_teams_not_matched", {"lp_series": s["sid"]}, at)
            continue
        for le_tid, lp_tid in tmap.items():
            changed += _attach(con, "team", SRC_LE_TEAM, le_tid, lp_tid, at)
        at_slot = {(r[0], r[1], r[2]): r[3] for r in con.execute("SELECT game_n, tid, role, pid FROM c_player_games WHERE sid = ?", (s["sid"],))}
        votes = defaultdict(Counter)
        for p in lpg.get(mid, []):
            lp_pid = at_slot.get((p.get("number"), tmap.get(p.get("team_id")), role(p.get("role"))))
            if lp_pid and p.get("player_id"):
                votes[p["player_id"]][lp_pid] += 1
        for le_pid, c in votes.items():
            if len(c) == 1:
                changed += _attach(con, "player", SRC_LE_PLAYER, le_pid, next(iter(c)), at)
            else:
                store.quarantine(con, "player", f"lolesports:{le_pid}", "cross_source_player_conflict", dict(c), at)
    return changed


def _lolesports(con, at, series, games, pgs, checks, overlaps=None):
    """LoL Esports rows -> canonical series / games / player games (Riot ids, schedule game numbers). An LE match is
    left out when a Leaguepedia series with the same two teams (exact aliases) is on the same day: one source per
    real series. Remakes and forfeits are not reported by this source (flagged, left unknown)."""
    matches = {r["match_id"]: r for r in store.current_rows(con, "LE:Match") if r.get("match_id")}
    out = Counter()
    if not matches:
        return dict(out)
    lg = defaultdict(list)
    for r in store.current_rows(con, "LE:Game"):
        lg[r["match_id"]].append(r)
    lpg = defaultdict(list)
    for r in store.current_rows(con, "LE:PlayerGame"):
        lpg[r["game_id"]].append(r)
    lp_by_day = defaultdict(list)
    for s in series.values():
        when = s.get("scheduled_utc") or s.get("first_game_utc")
        if when and s.get("source") == "leaguepedia":
            lp_by_day[when[:10]].append(s)
    tcache, pcache = {}, {}

    def tid(t):
        if not t or not t.get("id"):
            return None
        if t["id"] not in tcache:
            e = store.entity(con, "team", SRC_LE_TEAM, str(t["id"]), label=t.get("name"), at=at)
            for a in (t.get("name"), t.get("code")):
                if a:
                    store.add_alias(con, "team", a, e, "lolesports", at)
            tcache[t["id"]] = e
        return tcache[t["id"]]

    def pid(p):
        if not p.get("player_id"):
            return None
        if p["player_id"] not in pcache:
            e = store.entity(con, "player", SRC_LE_PLAYER, str(p["player_id"]), label=p.get("name"), at=at)
            if p.get("name"):
                store.add_alias(con, "player", p["name"], e, "lolesports", at)
            pcache[p["player_id"]] = e
        return pcache[p["player_id"]]

    def alias_eids(*names):
        return {r[0] for n in names if n for r in con.execute("SELECT eid FROM aliases WHERE kind = 'team' AND norm = ? "
                                                               "AND source NOT LIKE 'lolesports%'", (norm(n),))}

    for mid, m in matches.items():
        teams = m.get("teams") or []
        if len(teams) != 2 or not all(t.get("id") for t in teams):
            out["skipped_not_two_teams"] += 1
            continue
        start = _z(m.get("start"))
        a, b = alias_eids(teams[0].get("name"), teams[0].get("code")), alias_eids(teams[1].get("name"), teams[1].get("code"))
        same = [s for s in lp_by_day.get(start[:10], []) if {s["team1"], s["team2"]} & a and {s["team1"], s["team2"]} & b] if start else []
        if same:
            out["left_out_leaguepedia_has_it"] += 1
            if overlaps is not None and len(same) == 1:
                overlaps.append((mid, m, same[0]))
            continue
        t1, t2 = tid(teams[0]), tid(teams[1])
        sid = store.entity(con, "series", SRC_LE_MATCH, str(mid), label=f"{teams[0].get('code')} vs {teams[1].get('code')}", at=at)
        score = {teams[0]["id"]: teams[0].get("game_wins"), teams[1]["id"]: teams[1].get("game_wins")}
        slots = sorted(m.get("games") or [], key=lambda g: g.get("number") or 99)
        number_of = {str(g.get("id")): g.get("number") for g in slots}
        done = bool(slots) and all(g.get("state") in ("completed", "unneeded") for g in slots)
        w1, w2 = score[teams[0]["id"]] or 0, score[teams[1]["id"]] or 0
        winner = (1 if w1 > w2 else 2) if done and w1 != w2 else None
        gl = sorted(lg.get(mid, []), key=lambda g: number_of.get(g["game_id"], g.get("number")) or 99)
        flags = ["source_lolesports", "remake_and_forfeit_not_reported"]
        ns = [number_of.get(g["game_id"], g.get("number")) for g in gl]
        if len(ns) != len(set(ns)) or None in ns:
            flags.append("game_order_invalid")
            store.quarantine(con, "series", sid, "game_order_invalid", [str(x) for x in ns], at)
            checks["le_series_game_order_invalid"] += 1
        completed_slots = sum(1 for g in slots if g.get("state") == "completed")
        if done and completed_slots != len(gl):
            flags.append("games_missing_from_feed")
            checks["le_series_games_missing"] += 1
        side_team = {g["game_id"]: {"blue": g.get("blue_team_id"), "red": g.get("red_team_id")} for g in gl}
        winners = le_winners(gl, score, side_team) if done and completed_slots == len(gl) else {}
        series[f"LE:{mid}"] = dict(sid=sid, tournament_page=f"LE:{m.get('league_slug')}", tournament=m.get("league"),
                                   best_of=m.get("best_of"), team1=t1, team2=t2, team1_name=teams[0].get("name"),
                                   team2_name=teams[1].get("name"), score1=teams[0].get("game_wins"), score2=teams[1].get("game_wins"),
                                   winner=winner, ff=None, nullified=0, scheduled_utc=start,
                                   first_game_utc=min((_z(g["start"]) for g in gl if g.get("start")), default=None),
                                   n_games=len(gl), n_remakes=0, status="complete" if done else "scheduled", flags=flags,
                                   source="lolesports", source_match_id=str(mid))
        out["series"] += 1
        tmap = {teams[0]["id"]: t1, teams[1]["id"]: t2}
        for g in gl:
            n = number_of.get(g["game_id"], g.get("number"))
            gid = store.entity(con, "game", SRC_LE_GAME, g["game_id"], label=g["game_id"], at=at)
            blue, red = tmap.get(g.get("blue_team_id")), tmap.get(g.get("red_team_id"))
            wt = tmap.get(winners.get(g["game_id"]))
            gflags = [] if wt else ["winner_unknown"]
            kills, seen = Counter(), set()
            for p in lpg.get(g["game_id"], []):
                who = pid(p)
                if who is None or who in seen:
                    checks["le_player_row_unusable"] += 1
                    continue
                seen.add(who)
                pt = tmap.get(p.get("team_id"))
                if p.get("kills") is not None:
                    kills[p.get("side")] += p["kills"]
                pgs.append(dict(gid=gid, pid=who, tid=pt, opp_tid=red if pt == blue else blue, sid=sid, game_n=n,
                                start_utc=_z(g.get("start")), tournament_page=f"LE:{m.get('league_slug')}", patch=le_patch(g.get("patch")),
                                side={"blue": "Blue", "red": "Red"}.get(p.get("side")), role=role(p.get("role")),
                                champion=p.get("champion"), kills=p.get("kills"), deaths=p.get("deaths"), assists=p.get("assists"),
                                team_kills=g.get("blue_kills") if p.get("side") == "blue" else g.get("red_kills"),
                                gold=p.get("gold"), cs=p.get("cs"), damage=None, vision=None,
                                win=(1 if wt == pt else 0) if wt else None, name_shown=p.get("name")))
            if len(seen) != 10:
                gflags.append("players_incomplete")
                checks["le_games_players_incomplete"] += 1
            elif kills["blue"] != (g.get("blue_kills") or 0) or kills["red"] != (g.get("red_kills") or 0):
                gflags.append("kills_mismatch")
                checks["le_games_kills_mismatch"] += 1
            games.append(dict(gid=gid, sid=sid, game_n=n, lp_game_id=None, riot_game_id=None, start_utc=_z(g.get("start")),
                              patch=le_patch(g.get("patch")), length_s=round(g["duration_s"], 1) if g.get("duration_s") else None,
                              team1=blue, team2=red, team1_side="Blue", winner_tid=wt, team1_kills=g.get("blue_kills"),
                              team2_kills=g.get("red_kills"), remake=0, ff=None, chronobreak=0, n_players=len(seen),
                              tournament_page=f"LE:{m.get('league_slug')}", flags=gflags, source="lolesports", source_game_id=g["game_id"]))
            out["games"] += 1
            if len(pgs) >= 5000:
                out["flushed"] += _flush(con, pgs)
    return dict(out)


def _players_by_game(con):
    """ScoreboardPlayers rows streamed in GameId order, grouped: (GameId, [rows]) - never all in memory at once."""
    cur = con.execute("SELECT json_extract(r.data, '$.GameId') AS g, r.data FROM lp_current c JOIN lp_rows r ON r.tbl = c.tbl "
                      "AND r.skey = c.skey AND r.rev = c.rev WHERE c.tbl = 'ScoreboardPlayers' AND c.gone_at IS NULL "
                      "AND g IS NOT NULL ORDER BY g")
    gid, rows = None, []
    for g, data in cur:
        if g != gid and rows:
            yield gid, rows
            rows = []
        gid = g
        rows.append(json.loads(data))
    if rows:
        yield gid, rows


def build(con, at=None, _again=True):
    """Rebuild the canonical tables. Returns counts and check results. When the cross-source pass links or merges ids,
    the tables are rebuilt once more so every row carries the final ids."""
    t0 = time.time()
    at = at or store.now_iso()
    tid, teams = team_resolver(con, at)
    pid, players = player_resolver(con, at)
    tours = {page_key(r["OverviewPage"]): r for r in store.current_rows(con, "Tournaments") if r.get("OverviewPage")}
    ms = {r["MatchId"]: r for r in store.current_rows(con, "MatchSchedule") if r.get("MatchId")}
    msg = defaultdict(list)
    for r in store.current_rows(con, "MatchScheduleGame"):
        if r.get("MatchId"):
            msg[r["MatchId"]].append(r)
    sg = [r for r in store.current_rows(con, "ScoreboardGames") if r.get("GameId")]
    checks = Counter()

    # ---- series: one id per MatchId; a re-keyed MatchId keeps its id when exactly one gone series matches it
    gone = defaultdict(list)
    for r in con.execute("SELECT r.data FROM lp_current c JOIN lp_rows r ON r.tbl = c.tbl AND r.skey = c.skey AND r.rev = c.rev "
                         "WHERE c.tbl = 'MatchSchedule' AND c.gone_at IS NOT NULL"):
        d = json.loads(r[0])
        gone[(page_key(d.get("OverviewPage")), frozenset({page_key(d.get("Team1")), page_key(d.get("Team2"))}))].append(d)

    def sid_for(match_id, row):
        eid = store.find_entity(con, "series", SRC_MATCH, match_id)
        if eid:
            return store.entity(con, "series", SRC_MATCH, match_id, at=at)
        if row:
            k = (page_key(row.get("OverviewPage")), frozenset({page_key(row.get("Team1")), page_key(row.get("Team2"))}))
            d0 = _dt(row.get("DateTime_UTC"))
            cands = {store.find_entity(con, "series", SRC_MATCH, g["MatchId"]) for g in gone.get(k, [])
                     if d0 and _dt(g.get("DateTime_UTC")) and abs(_dt(g["DateTime_UTC"]) - d0) <= timedelta(days=3)} - {None}
            if len(cands) == 1:
                return store.link_source(con, next(iter(cands)), "series", SRC_MATCH, match_id, "MatchId changed upstream", at)
            if len(cands) > 1:
                store.quarantine(con, "series", match_id, "rekey_ambiguous", sorted(cands), at)
        return store.entity(con, "series", SRC_MATCH, match_id, label=match_id, at=at)

    series = {}
    for mid, r in ms.items():
        series[mid] = dict(sid=sid_for(mid, r), lp_match_id=mid, tournament_page=page_key(r.get("OverviewPage")),
                           best_of=_int(r.get("BestOf")), team1=tid(r.get("Team1")), team2=tid(r.get("Team2")),
                           team1_name=r.get("Team1"), team2_name=r.get("Team2"), score1=_int(r.get("Team1Score")),
                           score2=_int(r.get("Team2Score")), winner=_int(r.get("Winner")), ff=_int(r.get("FF")),
                           nullified=int(_truthy(r.get("IsNullified"))), scheduled_utc=_iso(r.get("DateTime_UTC")), flags=[],
                           source="leaguepedia", source_match_id=mid)
    for r in sg:                                           # scoreboards without a schedule row
        mid = r.get("MatchId")
        if mid and mid not in series:
            series[mid] = dict(sid=sid_for(mid, None), lp_match_id=mid, tournament_page=page_key(r.get("OverviewPage")),
                               best_of=None, team1=tid(r.get("Team1")), team2=tid(r.get("Team2")), team1_name=r.get("Team1"),
                               team2_name=r.get("Team2"), score1=None, score2=None, winner=None, ff=None, nullified=0,
                               scheduled_utc=None, flags=["no_schedule_row"], source="leaguepedia", source_match_id=mid)
            checks["series_without_schedule"] += 1

    # ---- games (player rows are written in chunks as they are produced)
    con.execute("DELETE FROM c_player_games")
    games, pgs, n_pg = [], [], 0
    by_series = defaultdict(list)
    sched_game = {}
    for mid, rows in msg.items():
        for g in rows:
            if g.get("GameId"):
                sched_game[g["GameId"]] = g
    sg.sort(key=lambda r: r["GameId"])
    sp_iter = _players_by_game(con)
    nxt = next(sp_iter, None)
    for r in sg:
        gid_lp = r["GameId"]
        while nxt is not None and nxt[0] < gid_lp:          # player rows of a game without a scoreboard game row
            checks["player_rows_without_game"] += len(nxt[1])
            nxt = next(sp_iter, None)
        players = nxt[1] if nxt is not None and nxt[0] == gid_lp else []
        if players:
            nxt = next(sp_iter, None)
        sg_n, sch = _int(r.get("N_GameInMatch")), sched_game.get(gid_lp)
        n = _int(sch.get("N_GameInMatch")) if sch else sg_n
        flags = []
        if sch and sg_n is not None and n is not None and sg_n != n:
            flags.append("order_conflict")
            checks["order_conflict"] += 1
        riot = (r.get("RiotPlatformGameId") or (sch or {}).get("RiotPlatformGameId") or "").strip()
        gid = store.find_entity(con, "game", SRC_RIOT, riot) if riot else None
        gid = gid or store.find_entity(con, "game", SRC_GAME, gid_lp) or store.entity(con, "game", SRC_GAME, gid_lp, label=gid_lp, at=at)
        if store.find_entity(con, "game", SRC_GAME, gid_lp) is None:
            store.link_source(con, gid, "game", SRC_GAME, gid_lp, "Leaguepedia GameId", at)
        if riot and store.find_entity(con, "game", SRC_RIOT, riot) is None:
            store.link_source(con, gid, "game", SRC_RIOT, riot, "Riot platform game id", at)
        t1, t2 = tid(r.get("Team1")), tid(r.get("Team2"))
        kills = {1: 0, 2: 0}
        seen = set()
        for p in players:
            who, why = pid(p.get("Link"))
            if who is None:
                checks[f"player_unresolved_{why}"] += 1
                continue
            if who in seen:
                store.quarantine(con, "player_game", f"{gid_lp}|{who}", "duplicate_player_in_game", None, at)
                checks["duplicate_player_in_game"] += 1
                continue
            seen.add(who)
            side = _int(p.get("Side"))
            if side in kills and _int(p.get("Kills")) is not None:
                kills[side] += _int(p["Kills"])
            pt = tid(p.get("Team"))
            pgs.append(dict(gid=gid, pid=who, tid=pt, opp_tid=tid(p.get("TeamVs")),
                            sid=series[r["MatchId"]]["sid"] if r.get("MatchId") in series else None, game_n=n,
                            start_utc=_iso(r.get("DateTime_UTC")), tournament_page=page_key(r.get("OverviewPage")),
                            patch=r.get("Patch"), side={1: "Blue", 2: "Red"}.get(side), role=role(p.get("Role")),
                            champion=p.get("Champion"), kills=_int(p.get("Kills")), deaths=_int(p.get("Deaths")),
                            assists=_int(p.get("Assists")), team_kills=_int(p.get("TeamKills")), gold=_int(p.get("Gold")),
                            cs=_int(p.get("CS")), damage=_int(p.get("DamageToChampions")), vision=_int(p.get("VisionScore")),
                            win=1 if _truthy(p.get("PlayerWin")) else 0, name_shown=p.get("Name")))
            if p.get("Name"):
                store.add_alias(con, "player", p["Name"], who, "leaguepedia:scoreboard_name", at)
        if len(pgs) >= 5000:
            n_pg += _flush(con, pgs)
        if len(seen) != 10:
            flags.append("players_incomplete")
            checks["games_players_incomplete"] += 1
        tk1, tk2 = _int(r.get("Team1Kills")), _int(r.get("Team2Kills"))
        if len(seen) == 10 and tk1 is not None and tk2 is not None and (kills[1] != tk1 or kills[2] != tk2):
            flags.append("kills_mismatch")
            checks["games_kills_mismatch"] += 1
        w = _int(r.get("Winner"))
        g = dict(gid=gid, sid=series[r["MatchId"]]["sid"] if r.get("MatchId") in series else None, game_n=n, lp_game_id=gid_lp,
                 riot_game_id=riot or None, start_utc=_iso(r.get("DateTime_UTC")), patch=r.get("Patch"),
                 length_s=round(_float(r.get("Gamelength_Number")) * 60, 1) if _float(r.get("Gamelength_Number")) else None,
                 team1=t1, team2=t2, team1_side="Blue", winner_tid={1: t1, 2: t2}.get(w), team1_kills=tk1, team2_kills=tk2,
                 remake=int(_truthy((sch or {}).get("IsRemake"))), ff=_int((sch or {}).get("FF")),
                 chronobreak=int(_truthy((sch or {}).get("IsChronobreak"))), n_players=len(seen),
                 tournament_page=page_key(r.get("OverviewPage")), flags=flags, source="leaguepedia", source_game_id=gid_lp)
        games.append(g)
        if r.get("MatchId"):
            by_series[r["MatchId"]].append(g)
    # schedule games without a scoreboard (remakes, forfeits, missing data) still count for game order
    for mid, rows in msg.items():
        have = {g["lp_game_id"] for g in by_series.get(mid, [])}
        for s in rows:
            if s.get("GameId") in have or mid not in series:
                continue
            if _truthy(s.get("IsRemake")) or _int(s.get("FF")):
                series[mid]["flags"].append("remake" if _truthy(s.get("IsRemake")) else "forfeit_game")

    # ---- series checks: unique game order, games vs score
    for mid, gs in by_series.items():
        s = series.get(mid)
        if not s:
            continue
        ns = [g["game_n"] for g in gs if not g["remake"]]
        if len(ns) != len(set(ns)) or None in ns:
            s["flags"].append("game_order_invalid")
            store.quarantine(con, "series", s["sid"], "game_order_invalid", sorted(str(x) for x in ns), at)
            checks["series_game_order_invalid"] += 1
        s["n_games"] = sum(1 for g in gs if not g["remake"])
        s["n_remakes"] = sum(1 for g in gs if g["remake"])
        s["first_game_utc"] = min((g["start_utc"] for g in gs if g["start_utc"]), default=None)
        if s.get("score1") is not None and s.get("score2") is not None and s["n_games"] and s["score1"] + s["score2"] != s["n_games"] and not s.get("ff"):
            s["flags"].append("games_vs_score_mismatch")
            checks["series_games_vs_score_mismatch"] += 1
    n_pg += _flush(con, pgs)
    overlaps = []
    le_counts = _lolesports(con, at, series, games, pgs, checks, overlaps)
    n_pg += _flush(con, pgs) + le_counts.pop("flushed", 0)

    # ---- write the canonical tables
    con.execute("DELETE FROM c_series")
    con.execute("DELETE FROM c_games")
    for mid, s in series.items():
        status = "complete" if (s.get("winner") or (s.get("score1") or 0) + (s.get("score2") or 0) > 0) else "scheduled"
        tn = tours.get(s["tournament_page"]) or {}
        con.execute("INSERT OR REPLACE INTO c_series (sid, lp_match_id, tournament_page, tournament, best_of, team1, team2, team1_name, "
                    "team2_name, score1, score2, winner, ff, nullified, scheduled_utc, first_game_utc, n_games, n_remakes, status, flags, "
                    "source, source_match_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s["sid"], mid if s.get("source") == "leaguepedia" else None, s["tournament_page"], tn.get("Name") or s.get("tournament"),
                     s["best_of"], s["team1"], s["team2"], s["team1_name"], s["team2_name"], s["score1"], s["score2"], s["winner"], s["ff"],
                     s["nullified"], s["scheduled_utc"], s.get("first_game_utc"), s.get("n_games") or 0, s.get("n_remakes") or 0,
                     s.get("status") or status, json.dumps(sorted(set(s["flags"]))), s.get("source"), s.get("source_match_id")))
    for g in games:
        con.execute("INSERT OR REPLACE INTO c_games (gid, sid, game_n, lp_game_id, riot_game_id, start_utc, patch, length_s, team1, team2, "
                    "team1_side, winner_tid, team1_kills, team2_kills, remake, ff, chronobreak, n_players, tournament_page, flags, source, "
                    "source_game_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g["gid"], g["sid"], g["game_n"], g.get("lp_game_id"), g.get("riot_game_id"), g["start_utc"], g["patch"], g["length_s"],
                     g["team1"], g["team2"], g["team1_side"], g["winner_tid"], g["team1_kills"], g["team2_kills"], g["remake"],
                     g["ff"], g["chronobreak"], g["n_players"], g["tournament_page"], json.dumps(g["flags"]), g.get("source"),
                     g.get("source_game_id")))
    store.set_meta(con, "built_at", at)
    store.set_meta(con, "dirty", None)
    con.commit()
    out = {"built_at": at, "seconds": round(time.time() - t0, 1), "series": len(series), "games": len(games),
           "player_games": n_pg, "teams": len(teams), "players_with_page_rows": len(players), "lolesports": le_counts,
           "checks": dict(checks)}
    linked = link_overlaps(con, overlaps, at)
    out["cross_source_links"] = linked
    out["crosscheck"] = crosscheck(con, overlaps, at) if not (linked and _again) else {"pending": "after the re-build"}
    store.set_meta(con, "last_build", out)
    con.commit()
    if linked and _again:
        return dict(build(con, at, _again=False), cross_source_links=linked)
    return out
