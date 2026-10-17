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
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

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


def _variants_of(con, page):
    return [r[0][len("page_variant:"):] for r in con.execute("SELECT k FROM meta WHERE k LIKE 'page_variant:%' AND v = ?",
                                                             (json.dumps(page),))]


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
    # 2026-10-17 audit: a scoreboard link spelled with other capitals than Leaguepedia's page ("Ksaez" / page "KSAEZ",
    # "SteeelBack" / "Steeelback") has no page of its own and became a second id. Such a link resolves to a page only on
    # source evidence: Leaguepedia's redirect table (or page list) holds exactly one page for that name ignoring capitals,
    # AND the link and that page's names played for a common team (scoreboards).
    fold = lambda x: page_key(x).casefold()
    ci = defaultdict(set)
    for a, t in redirect.items():
        ci[fold(a)] |= t
    for p in pages:
        ci[fold(p)].add(p)
    teams_of = defaultdict(set)
    for r in store.current_rows(con, "ScoreboardPlayers"):
        if r.get("Link") and r.get("Team"):
            teams_of[page_key(r["Link"])].add(page_key(r["Team"]))
    cache = {}

    def variant(n):
        tg = ci.get(fold(n)) or set()
        if len(tg) != 1:
            return None
        page = next(iter(tg))
        if page == n:
            return None
        names = {page} | names_of.get(page, set())
        common = teams_of.get(n, set()) & set().union(*(teams_of.get(x, set()) for x in names))
        if not common:
            return None
        if store.get_meta(con, f"page_variant:{n}") != page:
            store.set_meta(con, f"page_variant:{n}", page)
            store.quarantine(con, "player", n, "spelling_variant_resolved", {"page": page, "common_teams": sorted(common),
                                                                               "evidence": "redirect table + common team"}, at)
        return page

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
        v = variant(n)
        if v:
            return v, None
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
        # ids created earlier for page-less spellings of this page (before the variant rule) join it - the older id survives
        for v in _variants_of(con, page):
            old = store.find_entity(con, "player", SRC_PAGE, v)
            if old and store.canonical_id(con, old) != store.canonical_id(con, eid):
                a, b = store.canonical_id(con, old), store.canonical_id(con, eid)
                keep, drop = sorted([a, b], key=lambda e: int(e.rsplit(":", 1)[1]))
                store.merge(con, keep, drop, f"spelling variant {v!r} of Leaguepedia page {page!r} (redirect table + common team)", at)
                eid = keep
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
    """Game id -> (verified winner or None, status, inferred winner). LoL Esports records no per-game winner, so a game's
    winner is VERIFIED only when it follows with certainty from the official series score - and only when every played
    game is present (games present = sum of the score): every game of a sweep ("derived_sweep"), and the last game of
    any series ("derived_last_game"). Otherwise the winner inferred from the final frame (inhibitors / towers / gold) is
    kept as "inferred_unverified" - never certified, not even when the inferred wins add up to the score (two wrong
    labels can still add up; 2026-10-17 owner)."""
    if not games:
        return {}
    inferred = {g["game_id"]: side_team[g["game_id"]].get(g.get("winner_side")) for g in games}
    out = {g["game_id"]: (None, "inferred_unverified" if inferred[g["game_id"]] else "unknown", inferred[g["game_id"]]) for g in games}
    total = sum((v or 0) for v in score.values())
    ranked = sorted(score, key=lambda t: score[t] or 0, reverse=True)
    sw = ranked[0] if len(ranked) == 2 and (score[ranked[0]] or 0) > (score[ranked[1]] or 0) else None
    if not sw or total != len(games):
        return out
    if not (score[ranked[1]] or 0):
        return {g["game_id"]: (sw, "derived_sweep", inferred[g["game_id"]]) for g in games}
    last = games[-1]["game_id"]
    out[last] = (sw, "derived_last_game", inferred[last])
    return out


def _z(s):
    return (str(s)[:19] + "Z") if s else None


def _ts(s):
    try:
        return datetime.fromisoformat(str(s)[:19].replace(" ", "T")).replace(tzinfo=timezone.utc).timestamp() if s else None
    except ValueError:
        return None


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
    pages = lambda e: {store.page_variant(con, r[0]) for r in con.execute(
        "SELECT skey FROM entity_sources WHERE eid = ? AND source = 'leaguepedia:page'", (e,))}
    if pages(cur) and pages(target) and pages(cur) != pages(target):
        # both ids are Leaguepedia pages: two different players / teams - one LoL Esports id paired with both is a
        # conflict to look at, never a merge (2026-10-17 review)
        store.quarantine(con, kind, f"{source}:{skey}", "cross_source_conflict", {"ids": sorted([cur, target])}, at)
        return 0
    keep, drop = sorted([cur, target], key=lambda e: int(e.rsplit(":", 1)[1]))
    store.merge(con, keep, drop, f"{kind} identified in a series both sources hold", at)
    return 1


def crosscheck(con, overlaps, at):
    """Data check on every real series both sources hold: game numbers, winners, team kills, and each player's kills /
    deaths / assists (players paired by game number, team and role). Every comparison is a row in `crosscheck`
    (rewritten each build); disagreements are kept, never "fixed" - the history uses Leaguepedia's copy."""
    con.execute("DELETE FROM crosscheck")
    con.execute("DELETE FROM reconciliation")
    recon = []
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
            side_of = {tmap.get(b.get("blue_team_id")): "blue", tmap.get(b.get("red_team_id")): "red"}
            le_k = defaultdict(int)
            le_d = defaultdict(int)
            for p in lpg.get(mid, []):
                if p.get("number") == n:
                    le_k[p.get("side")] += p.get("kills") or 0
                    le_d[p.get("side")] += p.get("deaths") or 0
            for t, raw_lp, canon_lp in ((a["team1"], a.get("team1_kills_raw"), a["team1_kills"]), (a["team2"], a.get("team2_kills_raw"), a["team2_kills"])):
                sd = side_of.get(t)
                if not sd:
                    continue
                raw_le = b.get(f"{sd}_kills")
                rows.append((s["sid"], mid, "team_kills_raw", n, t, str(raw_lp), str(raw_le), int(raw_lp == raw_le)))
                rows.append((s["sid"], mid, "team_kills_player_sum", n, t, str(canon_lp), str(le_k[sd]), int(canon_lp == le_k[sd])))
                if raw_lp != raw_le or canon_lp != le_k[sd]:
                    other = "red" if sd == "blue" else "blue"
                    ev = {"lp_raw_total": raw_lp, "lp_player_sum": canon_lp, "le_raw_total": raw_le, "le_player_sum": le_k[sd],
                          "le_opponent_deaths": le_d[other]}
                    if canon_lp == le_k[sd] and raw_le == le_d[other] and raw_le != le_k[sd]:
                        res, status = ("player kills agree in both sources; LoL Esports' team total equals the opposing deaths, i.e. "
                                       f"it also counts {raw_le - le_k[sd]} death(s) no champion was credited with (execution). Canonical "
                                       "team kills = player-kill sum (both sources)"), "resolved"
                    elif canon_lp != le_k[sd]:
                        res, status = ("player-level kills differ between the sources (see player_kills rows); Leaguepedia's "
                                       "official scoreboard is kept for this series; no third source to settle it"), "unresolved"
                    else:
                        res, status = "team totals differ, player sums agree; canonical = player-kill sum", "resolved"
                    recon.append((s["sid"], mid, n, t, "team_kills", str(raw_lp), str(raw_le), json.dumps(ev), res, status))
            lw = a["winner_tid"]
            ew = tmap.get(b.get("blue_team_id") if b.get("winner_side") == "blue" else b.get("red_team_id") if b.get("winner_side") == "red" else None)
            if lw and ew:
                rows.append((s["sid"], mid, "winner_official_vs_le_inferred", n, None, lw, ew, int(lw == ew)))
                if lw != ew:
                    recon.append((s["sid"], mid, n, None, "winner", lw, ew, json.dumps({"le_basis": b.get("winner_basis")}),
                                  "Leaguepedia's official result kept; LoL Esports' frame-inferred winner was the other team",
                                  "resolved"))
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
                    if q[i] != p.get(stat):
                        recon.append((s["sid"], mid, n, q[3], f"player_{stat}", str(q[i]), str(p.get(stat)), None,
                                      "player stat differs between the sources; Leaguepedia's official scoreboard kept for this "
                                      "series (LP-sourced); no third source", "unresolved"))
    con.executemany("INSERT INTO crosscheck (sid, le_match_id, item, game_n, pid, lp_value, le_value, agree, checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [r + (at,) for r in rows])
    con.executemany("INSERT INTO reconciliation (sid, le_match_id, game_n, tid, item, lp_value, le_value, evidence, resolution, status, "
                    "checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [r + (at,) for r in recon])
    summary = {}
    for item, n, ok in con.execute("SELECT item, COUNT(*), SUM(agree) FROM crosscheck GROUP BY item"):
        summary[item] = {"compared": n, "agree": ok or 0, "share": round((ok or 0) / n, 4) if n else None}
    summary["series"] = len(overlaps)
    summary["reconciliation"] = {f"{i}:{st}": n for i, st, n in con.execute(
        "SELECT item, status, COUNT(*) FROM reconciliation GROUP BY item, status")}
    summary["note"] = ("agreement on the series BOTH sources hold (Leaguepedia + LoL Esports' major leagues) - it measures source "
                       "agreement there; it does not establish completeness or accuracy of the rest of the history")
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
    team_votes, player_votes = defaultdict(Counter), defaultdict(Counter)
    for mid, m, s in overlaps:
        lp_tids = {s["team1"], s["team2"]}
        slot = {(r[0], r[1], r[2]): (r[3], r[4], r[5], r[6]) for r in con.execute(
            "SELECT game_n, tid, role, pid, kills, deaths, assists FROM c_player_games WHERE sid = ?", (s["sid"],))}
        rows = lpg.get(mid, [])

        def agree(le_tid, lp_tid):
            """Box-score corroboration: this LE team's player rows equal the LP rows of `lp_tid` at the same (game,
            role) in kills, deaths and assists - at least 5 rows and 80% of them."""
            mine = [p for p in rows if p.get("team_id") == le_tid]
            ok = sum(1 for p in mine if (slot.get((p.get("number"), lp_tid, role(p.get("role")))) or (None,))[1:] ==
                     (p.get("kills"), p.get("deaths"), p.get("assists")))
            return len(mine) >= 5 and ok >= 0.8 * len(mine)
        tmap = {}
        for t in m.get("teams") or []:
            hits = {r[0] for n in (t.get("name"), t.get("code")) if n
                    for r in con.execute("SELECT eid FROM aliases WHERE kind = 'team' AND norm = ? AND source NOT LIKE 'lolesports%'",
                                         (norm(n),))} & lp_tids
            if len(hits) == 1:
                tmap[t["id"]] = next(iter(hits))
        for t in m.get("teams") or []:                         # already linked through an earlier shared series
            e = store.find_entity(con, "team", SRC_LE_TEAM, str(t.get("id")))
            if t["id"] not in tmap and e and store.canonical_id(con, e) in lp_tids:
                tmap[t["id"]] = store.canonical_id(con, e)
        ids = [t["id"] for t in m.get("teams") or []]
        if len(ids) == 2 and len(tmap) == 1:
            # one team identified and the series is the same real series: the other team is the series' other team
            known_le, = tmap
            rest = lp_tids - {tmap[known_le]}
            if len(rest) == 1:
                tmap[ids[1] if ids[0] == known_le else ids[0]] = next(iter(rest))
        if len(tmap) != 2 or len(set(tmap.values())) != 2:
            store.quarantine(con, "series", f"lolesports:{mid}", "cross_source_teams_not_matched", {"lp_series": s["sid"]}, at)
            continue
        if not all(agree(le_tid, lp_tid) for le_tid, lp_tid in tmap.items()):
            # no link without corroboration: the box scores do not show the same two teams (or the feed has no rows)
            store.quarantine(con, "series", f"lolesports:{mid}", "cross_source_box_scores_differ", {"lp_series": s["sid"]}, at)
            continue
        for le_tid, lp_tid in tmap.items():
            team_votes[str(le_tid)][lp_tid] += 1
        for p in rows:
            hit = slot.get((p.get("number"), tmap.get(p.get("team_id")), role(p.get("role"))))
            if hit and p.get("player_id") and hit[1:] == (p.get("kills"), p.get("deaths"), p.get("assists")):
                player_votes[p["player_id"]][hit[0]] += 1
    for kind, src, votes in (("team", SRC_LE_TEAM, team_votes), ("player", SRC_LE_PLAYER, player_votes)):
        for le_id, c in votes.items():
            targets = {store.canonical_id(con, t) for t in c}
            if len(targets) != 1:
                store.quarantine(con, kind, f"lolesports:{le_id}", f"cross_source_{kind}_conflict", dict(c), at)
                continue
            changed += _verify_and_attach(con, kind, src, le_id, next(iter(targets)), sum(c.values()), at)
    return changed


def _verify_and_attach(con, kind, source, skey, target, n_votes, at):
    """Attach a LoL Esports id where every corroborated shared game puts it. When it is currently part of an id that
    holds a DIFFERENT Leaguepedia page (an earlier wrong link), it first moves off that id (store.detach_source)."""
    cur = store.find_entity(con, kind, source, str(skey))
    pages = lambda e: {store.page_variant(con, r[0]) for r in con.execute(
        "SELECT skey FROM entity_sources WHERE eid = ? AND source = 'leaguepedia:page'", (e,))}
    if cur is not None:
        curc = store.canonical_id(con, cur)
        if curc != target and pages(curc) and pages(target) and pages(curc) != pages(target):
            store.detach_source(con, kind, source, str(skey), "every corroborated shared game puts it on another Leaguepedia page",
                                {"was_with": sorted(pages(curc)), "now": sorted(pages(target)), "corroborated_games": n_votes}, at)
    return _attach(con, kind, source, skey, target, at)


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
    lp_times = defaultdict(dict)                       # LP series id -> {game number: start}
    for g in games:
        if g.get("source") == "leaguepedia" and g.get("sid") and _ts(g.get("start_utc")) is not None:
            lp_times[g["sid"]][g["game_n"]] = _ts(g["start_utc"])
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

    def known(t):
        """The team's ids known without guessing: Leaguepedia aliases of its name / code, and the id its LoL Esports team
        id already resolves to (linked through an earlier series both sources hold)."""
        e = store.find_entity(con, "team", SRC_LE_TEAM, str(t.get("id")))
        return alias_eids(t.get("name"), t.get("code")) | ({store.canonical_id(con, e)} if e else set())

    for mid, m in matches.items():
        teams = m.get("teams") or []
        if len(teams) != 2 or not all(t.get("id") for t in teams):
            out["skipped_not_two_teams"] += 1
            continue
        start = _z(m.get("start"))
        a, b = known(teams[0]), known(teams[1])
        num = {str(x.get("id")): x.get("number") for x in m.get("games") or []}
        le_t = {num.get(str(g.get("game_id")), g.get("number")): _ts(g.get("start")) for g in lg.get(mid, []) if _ts(g.get("start"))}
        t0 = min(le_t.values(), default=None)

        def times_agree(s, tol):
            """Games of the same number start within `tol` seconds (all comparable games); unknown -> not refuted."""
            pairs = [(t, lp_times[s["sid"]][n]) for n, t in le_t.items() if n in lp_times.get(s["sid"], {})]
            return all(abs(x - y) <= tol for x, y in pairs) if pairs else None
        same = [s for s in lp_by_day.get(start[:10], []) if {s["team1"], s["team2"]} & a and {s["team1"], s["team2"]} & b] if start else []
        # 2026-10-17 audit: an academy match ("NS Challengers vs DRX Challengers", codes NS / DRX) matched its main teams'
        # series on the same day by code. Both names on the same day also need the same-numbered games to start within
        # 60 minutes when the times are known.
        same = [s for s in same if times_agree(s, 3600) is not False]
        if not same:
            # 2026-10-17 audit: a team named differently in the two sources ("kt Challengers" / "KT Rolster Challengers")
            # hid real overlaps. The same series = one team known AND game 1 starting within 20 minutes of the
            # Leaguepedia series' game 1 (a team never plays two series at once).
            if t0 is not None:
                # same-numbered games (an LE copy may lack game 1) within 20 minutes; one team already known
                days = {datetime.fromtimestamp(t0 + d, timezone.utc).strftime("%Y-%m-%d") for d in (-86400, 0, 86400)}
                same = [s for d in days for s in lp_by_day.get(d, []) if {s["team1"], s["team2"]} & (a | b)
                        and times_agree(s, 1200)]
                if same:
                    out["left_out_same_start_one_team_known"] += 1
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
        winners = le_winners(gl, score, side_team) if done else {}
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
            wv, wstatus, winf = winners.get(g["game_id"], (None, "unknown", None))
            wt = tmap.get(wv)
            gflags = [] if wt else ["winner_not_verified"]
            # canonical team kills = the sum of this source's own player kills (all of its rows, resolved or not); the raw
            # team total is kept; when it equals the opposing deaths it counts deaths no champion was credited with
            # (executions) - checked on the raw final frames 2026-10-17
            src = lpg.get(g["game_id"], [])
            ksum, dsum = Counter(), Counter()
            for p in src:
                ksum[p.get("side")] += p.get("kills") or 0
                dsum[p.get("side")] += p.get("deaths") or 0
            raw = {"blue": g.get("blue_kills"), "red": g.get("red_kills")}
            complete = len(src) == 10 and all(p.get("kills") is not None and p.get("side") in ("blue", "red") for p in src)
            canon = {s: (ksum[s] if complete else raw[s]) for s in ("blue", "red")}
            st = []
            for s, o in (("blue", "red"), ("red", "blue")):
                if not complete:
                    st.append("raw_total_source_rows_incomplete")
                elif raw[s] == ksum[s]:
                    st.append("consistent")
                elif raw[s] is not None and raw[s] == dsum[o] and raw[s] > ksum[s]:
                    st.append(f"raw_counts_opponent_deaths_incl_{raw[s] - ksum[s]}_execution")
                else:
                    st.append("raw_total_unexplained")
            kstatus = "consistent" if st == ["consistent", "consistent"] else ";".join(st)
            kills, seen = Counter(), set()
            for p in src:
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
                                team_kills=canon.get(p.get("side")),
                                gold=p.get("gold"), cs=p.get("cs"), damage=None, vision=None,
                                win=(1 if wt == pt else 0) if wt else None, name_shown=p.get("name")))
            if len(seen) != 10:
                gflags.append("players_incomplete")
                checks["le_games_players_incomplete"] += 1
            if kstatus != "consistent":
                gflags.append("team_total_differs_from_player_sum")
                checks["le_games_team_total_" + ("executions" if "execution" in kstatus else "other")] += 1
            checks[f"le_winner_{wstatus}"] += 1
            games.append(dict(gid=gid, sid=sid, game_n=n, lp_game_id=None, riot_game_id=None, start_utc=_z(g.get("start")),
                              patch=le_patch(g.get("patch")), length_s=round(g["duration_s"], 1) if g.get("duration_s") else None,
                              team1=blue, team2=red, team1_side="Blue", winner_tid=wt, team1_kills=canon["blue"],
                              team2_kills=canon["red"], team1_kills_raw=raw["blue"], team2_kills_raw=raw["red"],
                              team_kills_status=kstatus, winner_status=wstatus if wt else ("inferred_unverified" if winf else "unknown"),
                              winner_inferred_tid=tmap.get(winf), remake=0, ff=None, chronobreak=0, n_players=len(seen),
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


def split_wrong_merges(con, at):
    """Repair (2026-10-17): an id holding two or more different Leaguepedia pages (two different players / teams, merged
    through a wrong cross-source link) is split by SOURCE EVIDENCE: the page the id was created for (or, for an id
    created from a LoL Esports key, the page named like the id) stays; every other page returns to the id that was first
    created for that page (entity history), or to a new id when there is none. LoL Esports keys then go wherever their
    corroborated shared games put them (link_overlaps). Recorded in entity history + quarantine (wrong_merge_split).
    -> number of pages moved."""
    n = 0
    for kind in ("player", "team"):
        multi = [r[0] for r in con.execute("SELECT eid FROM entity_sources WHERE kind = ? AND source = 'leaguepedia:page' "
                                           "GROUP BY eid HAVING COUNT(*) > 1", (kind,))]
        for keep in multi:
            groups = defaultdict(list)                         # canonical page -> its spellings on this id
            for (sk, seen) in con.execute("SELECT skey, first_seen FROM entity_sources WHERE eid = ? AND source = 'leaguepedia:page' "
                                          "ORDER BY first_seen", (keep,)).fetchall():
                groups[store.page_variant(con, sk)].append(sk)
            if len(groups) == 1:                               # spelling variants of one page (player_resolver) - one player
                continue
            label = ((con.execute("SELECT label FROM entities WHERE eid = ?", (keep,)).fetchone() or [""])[0] or "").casefold()
            origin = {pg: store.created_from(con, kind, SRC_PAGE, pg) for pg in groups}
            stay = (next((pg for pg in groups if origin[pg] == keep), None)
                    or next((pg for pg in groups if pg.casefold() == label), None) or next(iter(groups)))
            for pg, spellings in groups.items():
                if pg == stay:
                    continue
                x = origin[pg]
                if x and x != keep and store.canonical_id(con, x) == keep:
                    target = x
                    con.execute("DELETE FROM meta WHERE k = ?", (f"merged:{x}",))
                else:
                    target = store._next_id(con, kind)
                    con.execute("INSERT INTO entities (eid, kind, label, created_at) VALUES (?, ?, ?, ?)", (target, kind, pg, at))
                for sk in spellings:
                    con.execute("UPDATE entity_sources SET eid = ?, note = substr(COALESCE(note || '; ', '') || ?, -1000) "
                                "WHERE eid = ? AND source = ? AND skey = ?", (target, f"split from {keep} (two Leaguepedia pages)",
                                                                                   keep, SRC_PAGE, sk))
                con.execute("DELETE FROM aliases WHERE eid IN (?, ?)", (keep, target))   # re-added by the resolvers
                for eid, other in ((keep, target), (target, keep)):
                    con.execute("INSERT INTO entity_history (eid, at, change, detail) VALUES (?, ?, 'split', ?)",
                                (eid, at, json.dumps({"other": other, "page": pg, "kept_page": stay,
                                                      "reason": "two Leaguepedia pages on one id - a wrong merge reversed"})))
                store.quarantine(con, kind, f"split:{keep}|{pg}", "wrong_merge_split", {"kept": keep, "kept_page": stay,
                                                                                        "page": pg, "to": target}, at)
                n += 1
    return n

def build(con, at=None, _again=True):
    """Rebuild the canonical tables. Returns counts and check results. When the cross-source pass links or merges ids,
    the tables are rebuilt once more so every row carries the final ids."""
    t0 = time.time()
    at = at or store.now_iso()
    wrong_merges = split_wrong_merges(con, at)
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
    ident_excl = {}
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
        # canonical team kills = the sum of all of this game's player rows by side (resolved or not); raw totals kept
        src_k = {1: 0, 2: 0}
        usable = 0
        for p in players:
            if _int(p.get("Side")) in src_k and _int(p.get("Kills")) is not None:
                src_k[_int(p.get("Side"))] += _int(p.get("Kills"))
                usable += 1
        lg_name = (tours.get(page_key(r.get("OverviewPage"))) or {}).get("League") or page_key(r.get("OverviewPage"))
        for p in players:
            who, why = pid(p.get("Link"))
            if who is None:
                checks[f"player_unresolved_{why}"] += 1
                ident = ident_excl.setdefault(lg_name, {})
                k = f"{str(r.get('DateTime_UTC'))[:7]} {why}"
                ident[k] = ident.get(k, 0) + 1
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
                            assists=_int(p.get("Assists")), team_kills=None, gold=_int(p.get("Gold")),
                            cs=_int(p.get("CS")), damage=_int(p.get("DamageToChampions")), vision=_int(p.get("VisionScore")),
                            win=1 if _truthy(p.get("PlayerWin")) else 0, name_shown=p.get("Name")))
            if p.get("Name"):
                store.add_alias(con, "player", p["Name"], who, "leaguepedia:scoreboard_name", at)
        if len(seen) != 10:
            flags.append("players_incomplete")
            checks["games_players_incomplete"] += 1
        tk1, tk2 = _int(r.get("Team1Kills")), _int(r.get("Team2Kills"))
        complete = len(players) == 10 and usable == 10
        c1, c2 = (src_k[1], src_k[2]) if complete else (tk1, tk2)
        if not complete:
            kstatus = "raw_total_source_rows_incomplete"
        elif (tk1, tk2) == (c1, c2):
            kstatus = "consistent"
        else:
            kstatus = "raw_total_differs_from_player_sum"
            flags.append("team_total_differs_from_player_sum")
            checks["lp_games_team_total_differs"] += 1
        for q in pgs[len(pgs) - len(seen):]:                           # this game's rows: the canonical team kills
            q["team_kills"] = c1 if q["side"] == "Blue" else c2
        w = _int(r.get("Winner"))
        g = dict(gid=gid, sid=series[r["MatchId"]]["sid"] if r.get("MatchId") in series else None, game_n=n, lp_game_id=gid_lp,
                 riot_game_id=riot or None, start_utc=_iso(r.get("DateTime_UTC")), patch=r.get("Patch"),
                 length_s=round(_float(r.get("Gamelength_Number")) * 60, 1) if _float(r.get("Gamelength_Number")) else None,
                 team1=t1, team2=t2, team1_side="Blue", winner_tid={1: t1, 2: t2}.get(w), team1_kills=c1, team2_kills=c2,
                 team1_kills_raw=tk1, team2_kills_raw=tk2, team_kills_status=kstatus,
                 winner_status="official" if {1: t1, 2: t2}.get(w) else "unknown", winner_inferred_tid=None,
                 remake=int(_truthy((sch or {}).get("IsRemake"))), ff=_int((sch or {}).get("FF")),
                 chronobreak=int(_truthy((sch or {}).get("IsChronobreak"))), n_players=len(seen),
                 tournament_page=page_key(r.get("OverviewPage")), flags=flags, source="leaguepedia", source_game_id=gid_lp)
        games.append(g)
        if r.get("MatchId"):
            by_series[r["MatchId"]].append(g)
        if len(pgs) >= 5000:
            n_pg += _flush(con, pgs)
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
        need = (s["best_of"] // 2 + 1) if s.get("best_of") else None
        won = max(s.get("score1") or 0, s.get("score2") or 0)
        done = bool(s.get("winner")) or bool(s.get("ff")) or (need is not None and won >= need)
        status = "complete" if done else ("in_progress" if (s.get("score1") or 0) + (s.get("score2") or 0) > 0 else "scheduled")
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
                    "source_game_id, team1_kills_raw, team2_kills_raw, team_kills_status, winner_status, winner_inferred_tid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g["gid"], g["sid"], g["game_n"], g.get("lp_game_id"), g.get("riot_game_id"), g["start_utc"], g["patch"], g["length_s"],
                     g["team1"], g["team2"], g["team1_side"], g["winner_tid"], g["team1_kills"], g["team2_kills"], g["remake"],
                     g["ff"], g["chronobreak"], g["n_players"], g["tournament_page"], json.dumps(g["flags"]), g.get("source"),
                     g.get("source_game_id"), g.get("team1_kills_raw"), g.get("team2_kills_raw"), g.get("team_kills_status"),
                     g.get("winner_status"), g.get("winner_inferred_tid")))
    store.set_meta(con, "built_at", at)
    store.set_meta(con, "dirty", None)
    con.commit()
    out = {"built_at": at, "seconds": round(time.time() - t0, 1), "series": len(series), "games": len(games),
           "player_games": n_pg, "teams": len(teams), "players_with_page_rows": len(players), "lolesports": le_counts,
           "checks": dict(checks), "identity_exclusions_by_league_month": ident_excl, "wrong_merges_split": wrong_merges}
    linked = link_overlaps(con, overlaps, at)
    out["cross_source_links"] = linked
    out["crosscheck"] = crosscheck(con, overlaps, at) if not (linked and _again) else {"pending": "after the re-build"}
    store.set_meta(con, "last_build", out)
    con.commit()
    if linked and _again:
        return dict(build(con, at, _again=False), cross_source_links=linked)
    return out
