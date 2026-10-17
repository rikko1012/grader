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


def build(con, at=None):
    """Rebuild the canonical tables. Returns counts and check results."""
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
                           nullified=int(_truthy(r.get("IsNullified"))), scheduled_utc=_iso(r.get("DateTime_UTC")), flags=[])
    for r in sg:                                           # scoreboards without a schedule row
        mid = r.get("MatchId")
        if mid and mid not in series:
            series[mid] = dict(sid=sid_for(mid, None), lp_match_id=mid, tournament_page=page_key(r.get("OverviewPage")),
                               best_of=None, team1=tid(r.get("Team1")), team2=tid(r.get("Team2")), team1_name=r.get("Team1"),
                               team2_name=r.get("Team2"), score1=None, score2=None, winner=None, ff=None, nullified=0,
                               scheduled_utc=None, flags=["no_schedule_row"])
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
                 tournament_page=page_key(r.get("OverviewPage")), flags=flags)
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

    # ---- write the canonical tables
    con.execute("DELETE FROM c_series")
    con.execute("DELETE FROM c_games")
    for mid, s in series.items():
        status = "complete" if (s.get("winner") or (s.get("score1") or 0) + (s.get("score2") or 0) > 0) else "scheduled"
        tn = tours.get(s["tournament_page"]) or {}
        con.execute("INSERT OR REPLACE INTO c_series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s["sid"], mid, s["tournament_page"], tn.get("Name"), s["best_of"], s["team1"], s["team2"], s["team1_name"],
                     s["team2_name"], s["score1"], s["score2"], s["winner"], s["ff"], s["nullified"], s["scheduled_utc"],
                     s.get("first_game_utc"), s.get("n_games") or 0, s.get("n_remakes") or 0, status, json.dumps(sorted(set(s["flags"])))))
    for g in games:
        con.execute("INSERT OR REPLACE INTO c_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g["gid"], g["sid"], g["game_n"], g["lp_game_id"], g["riot_game_id"], g["start_utc"], g["patch"], g["length_s"],
                     g["team1"], g["team2"], g["team1_side"], g["winner_tid"], g["team1_kills"], g["team2_kills"], g["remake"],
                     g["ff"], g["chronobreak"], g["n_players"], g["tournament_page"], json.dumps(g["flags"])))
    store.set_meta(con, "built_at", at)
    store.set_meta(con, "dirty", None)
    con.commit()
    out = {"built_at": at, "seconds": round(time.time() - t0, 1), "series": len(series), "games": len(games),
           "player_games": n_pg, "teams": len(teams), "players_with_page_rows": len(players), "checks": dict(checks)}
    store.set_meta(con, "last_build", out)
    con.commit()
    return out
