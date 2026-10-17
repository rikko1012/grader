"""
Leaguepedia ingestion: what to fetch and a checkpointed job queue (newest data first).

Every query names its fields, orders deterministically (time, then the row's unique key) and pages by at most 500.
Time-windowed tables are fetched in fixed 14-day windows aligned to EPOCH, so a window means the same thing on every
run; a completed window is checkpointed and a restart resumes where it stopped. Names seen in game rows (players,
teams, tournaments) are resolved in batches afterwards.

Order of work (one unit at a time, through the shared pacer):
  1. live sweep: games/players/schedule games of the last 3 days and the schedule of the next 15 days (every 30 min)
  2. pending names (players, redirects, teams, tournaments, rosters), batched
  3. the backfill of the core tables (8 months, LOL_BACKFILL_DAYS), newest window first
  4. secondary tables (ScoreboardTeams, PicksAndBansS7, RosterChanges, Tenures), newest first
  5. correction re-sweeps of the last 35 days (weekly), so later upstream fixes become new revisions

    python -m lol.center.ingest run [--max-units N] [--until-done]   (local backfill; LOL_CENTER_DB / CS2_DATA_DIR choose the file)
    python -m lol.center.ingest status
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

from . import cargo as lp
from . import store
from .names import page_key

EPOCH = date(2024, 1, 1)                 # window alignment (a Monday)
WINDOW_DAYS = 14
BACKFILL_DAYS = int(os.environ.get("LOL_BACKFILL_DAYS", "245"))     # the agreed 8-month window first; longer later
LIVE_EVERY_S = int(os.environ.get("LOL_LIVE_EVERY", "1800"))
RESWEEP_DAYS, RESWEEP_EVERY_S = 35, 7 * 86400
REF_BATCH = 40
REF_RECHECK_S = 7 * 86400


def _f(alias, names):
    return [f"{alias}.{n}={n}" for n in names]


def _page(alias):
    return [f"{alias}._pageName=Page", f"{alias}._pageID=PageID"]


WINDOWED = {
    "ScoreboardGames": dict(
        tables="ScoreboardGames=SG", time="SG.DateTime_UTC", tcol="DateTime_UTC", order="SG.DateTime_UTC, SG.GameId",
        fields=_page("SG") + _f("SG", ["GameId", "MatchId", "OverviewPage", "Tournament", "Team1", "Team2", "WinTeam",
                                       "LossTeam", "DateTime_UTC", "Team1Score", "Team2Score", "Winner", "Gamelength",
                                       "Gamelength_Number", "Team1Kills", "Team2Kills", "Team1Gold", "Team2Gold",
                                       "Team1Towers", "Team2Towers", "Team1Dragons", "Team2Dragons", "Team1Barons",
                                       "Team2Barons", "Team1Picks", "Team2Picks", "Team1Bans", "Team2Bans",
                                       "Team1Players", "Team2Players", "Patch", "N_GameInMatch", "RiotPlatformGameId",
                                       "UniqueLine"]),
        key=lambda r: r.get("GameId"), tier=1),
    "ScoreboardPlayers": dict(
        tables="ScoreboardPlayers=SP", time="SP.DateTime_UTC", tcol="DateTime_UTC", order="SP.DateTime_UTC, SP.UniqueLine",
        fields=_page("SP") + _f("SP", ["Link", "Name", "Team", "TeamVs", "Role", "Role_Number", "IngameRole", "Side",
                                       "Champion", "Kills", "Deaths", "Assists", "TeamKills", "Gold", "CS",
                                       "DamageToChampions", "VisionScore", "PlayerWin", "DateTime_UTC", "GameId",
                                       "MatchId", "OverviewPage", "Tournament", "UniqueLine", "GameTeamId"]),
        key=lambda r: r.get("UniqueLine") or "|".join(str(r.get(k) or "") for k in ("GameId", "Team", "Role")), tier=1),
    "MatchSchedule": dict(
        tables="MatchSchedule=MS", time="MS.DateTime_UTC", tcol="DateTime_UTC", order="MS.DateTime_UTC, MS.MatchId",
        fields=_page("MS") + _f("MS", ["MatchId", "OverviewPage", "Team1", "Team2", "Team1Final", "Team2Final", "Winner",
                                       "Team1Score", "Team2Score", "FF", "IsNullified", "DateTime_UTC", "HasTime",
                                       "BestOf", "Round", "Phase", "Tab", "N_MatchInTab", "N_TabInPage", "N_Page",
                                       "Patch", "IsTiebreaker", "UniqueMatch", "GroupName"]),
        key=lambda r: r.get("MatchId"), tier=1),
    "MatchScheduleGame": dict(
        tables="MatchScheduleGame=MSG, MatchSchedule=MS", join_on="MSG.MatchId=MS.MatchId", time="MS.DateTime_UTC",
        tcol="Match_DateTime_UTC", order="MS.DateTime_UTC, MSG.MatchId, MSG.N_GameInMatch",
        fields=_page("MSG") + _f("MSG", ["GameId", "MatchId", "OverviewPage", "N_GameInMatch", "Blue", "Red", "Winner",
                                         "BlueFinal", "RedFinal", "IsRemake", "IsChronobreak", "FF",
                                         "RiotPlatformGameId", "Selection", "FirstPick"])
               + ["MS.DateTime_UTC=Match_DateTime_UTC", "MS.BestOf=BestOf"],
        key=lambda r: r.get("GameId") or f"{r.get('MatchId')}|{r.get('N_GameInMatch')}", tier=1),
    "ScoreboardTeams": dict(
        tables="ScoreboardTeams=ST, ScoreboardGames=SG", join_on="ST.GameId=SG.GameId", time="SG.DateTime_UTC",
        tcol="Game_DateTime_UTC", order="SG.DateTime_UTC, ST.GameId, ST.Number",
        fields=_page("ST") + _f("ST", ["Team", "Side", "Number", "IsWinner", "Score", "Kills", "Gold", "Towers",
                                       "Dragons", "Barons", "Roster", "Picks", "Bans", "GameId", "MatchId",
                                       "OverviewPage", "UniqueTeam"]) + ["SG.DateTime_UTC=Game_DateTime_UTC"],
        key=lambda r: r.get("UniqueTeam") or f"{r.get('GameId')}|{r.get('Number')}", tier=2),
    "PicksAndBansS7": dict(
        tables="PicksAndBansS7=PB, ScoreboardGames=SG", join_on="PB.GameId=SG.GameId", time="SG.DateTime_UTC",
        tcol="Game_DateTime_UTC", order="SG.DateTime_UTC, PB.GameId",
        fields=_page("PB") + _f("PB", ["GameId", "MatchId", "Team1", "Team2", "Winner", "Team1PicksByRoleOrder",
                                       "Team2PicksByRoleOrder"]
                                + [f"Team{t}{k}{i}" for t in (1, 2) for k in ("Ban", "Pick", "Role") for i in range(1, 6)])
               + ["SG.DateTime_UTC=Game_DateTime_UTC"],
        key=lambda r: r.get("GameId"), tier=2),
    "RosterChanges": dict(
        tables="RosterChanges=RC", time="RC.Date_Sort", tcol="Date_Sort", order="RC.Date_Sort, RC.RosterChangeId",
        fields=_page("RC") + _f("RC", ["Date_Sort", "Player", "Direction", "Team", "Role", "RoleModifier", "Status",
                                       "RosterChangeId", "NewsId", "N_LineInNews", "Tournaments"]),
        key=lambda r: r.get("RosterChangeId") or f"{r.get('NewsId')}|{r.get('N_LineInNews')}|{r.get('Player')}", tier=2),
}
CORE = ("ScoreboardGames", "ScoreboardPlayers", "MatchSchedule", "MatchScheduleGame")
SECONDARY = ("ScoreboardTeams", "PicksAndBansS7", "RosterChanges")

# name lookups: (cargo table spec, where template over a list of names, key)
REFERENCE = {
    "PlayerRedirects": dict(tables="PlayerRedirects=PR", order="PR.OverviewPage, PR.AllName",
                            fields=_f("PR", ["AllName", "OverviewPage", "ID"]),
                            where=lambda q: f"PR.AllName IN ({q}) OR PR.OverviewPage IN ({q})",
                            key=lambda r: f"{r.get('AllName')}|{r.get('OverviewPage')}"),
    "Players": dict(tables="Players=P", order="P.OverviewPage",
                    fields=_f("P", ["ID", "OverviewPage", "Player", "Country", "Residency", "Role", "Team", "Team2",
                                    "IsRetired", "IsSubstitute", "IsTrainee", "IsLowContent"]),
                    where=lambda q: f"P.OverviewPage IN ({q})", key=lambda r: r.get("OverviewPage")),
    "Teams": dict(tables="Teams=T", order="T.OverviewPage",
                  fields=_f("T", ["Name", "OverviewPage", "Short", "Region", "Location", "OrganizationPage", "RenamedTo",
                                  "IsDisbanded"]),
                  where=lambda q: f"T.OverviewPage IN ({q})", key=lambda r: r.get("OverviewPage")),
    "TeamRedirects": dict(tables="TeamRedirects=TR", order="TR._pageName, TR.AllName",
                          fields=["TR._pageName=Page"] + _f("TR", ["AllName", "OtherName", "UniqueLine"]),
                          where=lambda q: f"TR.AllName IN ({q})",      # never filter on _pageName (Leaguepedia docs)
                          key=lambda r: f"{r.get('Page')}|{r.get('AllName')}|{r.get('OtherName')}"),
    "Tournaments": dict(tables="Tournaments=TN", order="TN.OverviewPage",
                        fields=_f("TN", ["Name", "OverviewPage", "DateStart", "Date", "League", "Region", "EventType",
                                         "StandardName", "Split", "TournamentLevel", "IsQualifier", "IsPlayoffs",
                                         "IsOfficial", "Year"]),
                        where=lambda q: f"TN.OverviewPage IN ({q})", key=lambda r: r.get("OverviewPage")),
    "TournamentPlayers": dict(tables="TournamentPlayers=TP", order="TP.OverviewPage, TP.Team, TP.N_PlayerInTeam, TP.Link",
                              fields=_f("TP", ["OverviewPage", "Team", "Link", "Player", "Role", "N_PlayerInTeam",
                                               "TeamOrder", "IsDistribution"]),
                              where=lambda q: f"TP.OverviewPage IN ({q})",
                              key=lambda r: f"{r.get('OverviewPage')}|{r.get('Team')}|{r.get('Link')}|{r.get('Role')}"),
    "Tenures": dict(tables="Tenures=TE", order="TE.Player, TE.DateJoin, TE.Team",
                    fields=_f("TE", ["Player", "Team", "DateJoin", "DateLeave", "IsCurrent", "NextTeam",
                                     "RosterChangeIdJoin", "RosterChangeIdLeave"]),
                    where=lambda q: f"TE.Player IN ({q})", key=lambda r: f"{r.get('Player')}|{r.get('Team')}|{r.get('DateJoin')}"),
}


def _d(s):
    return s.strftime("%Y-%m-%d 00:00:00")


def windows(today=None, days=BACKFILL_DAYS):
    """Fixed 14-day windows covering [today - days, today + 1 day), newest first: [(lo, hi), ...]."""
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    k0 = (start - EPOCH).days // WINDOW_DAYS
    k1 = (today - EPOCH).days // WINDOW_DAYS
    out = []
    for k in range(k1, k0 - 1, -1):
        lo = EPOCH + timedelta(days=k * WINDOW_DAYS)
        out.append((_d(lo), _d(min(lo + timedelta(days=WINDOW_DAYS), today + timedelta(days=1)))))
    return out


def _ck(con, job):
    r = con.execute("SELECT state, detail, updated_at FROM checkpoints WHERE job = ?", (job,)).fetchone()
    return dict(r) if r else None


def _set_ck(con, job, state, detail=None):
    con.execute("INSERT INTO checkpoints (job, state, detail, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(job) DO UPDATE SET "
                "state = excluded.state, detail = excluded.detail, updated_at = excluded.updated_at",
                (job, state, json.dumps(detail, default=str) if detail is not None else None, store.now_iso()))


def _log(con, job, ok, t0, req, counts, detail=None):
    con.execute("INSERT INTO job_log (job, at, ok, requests, rows, new_rows, changed_rows, seconds, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job, store.now_iso(), int(ok), req, counts.get("rows", 0), counts.get("new", 0), counts.get("changed", 0),
                 round(time.time() - t0, 1), json.dumps(detail, default=str)[:2000] if detail is not None else None))


def sweep(con, tbl, lo, hi, max_pages=200):
    """Fetch every row of one windowed table with time in [lo, hi): all pages, each raw page kept, rows stored with
    revisions. Only a COMPLETE sweep marks unseen rows of the window as gone. Returns counts."""
    s = WINDOWED[tbl]
    where = f"{s['time']} >= {lp.quote(lo)} AND {s['time']} < {lp.quote(hi)}"
    started = store.now_iso()
    tot = {"rows": 0, "new": 0, "changed": 0, "same": 0, "pages": 0}
    offset = 0
    while True:
        rows, raw, params = lp.cargo(s["tables"], s["fields"], s["order"], where=where, join_on=s.get("join_on"), offset=offset)
        at = store.now_iso()
        snap = store.save_raw(con, "leaguepedia", f"cargoquery:{tbl}", params, raw, n_rows=len(rows), fetched_at=at)
        c = store.put_rows(con, tbl, rows, s["key"], lambda r: r.get(s["tcol"]), snap, at)
        con.commit()
        tot["pages"] += 1
        tot["rows"] += len(rows)
        for k in ("new", "changed", "same"):
            tot[k] += c[k]
        if len(rows) < lp.PAGE:
            break
        offset += lp.PAGE
        if tot["pages"] >= max_pages:
            raise RuntimeError(f"{tbl} {lo}: more than {max_pages} pages - window too large")
    tot["gone"] = store.mark_gone(con, tbl, lo, hi, started, store.now_iso())
    con.commit()
    return tot


def lookup(con, tbl, names):
    """One batch of names through a reference table (all pages). Returns counts and the names found."""
    s = REFERENCE[tbl]
    q = ", ".join(lp.quote(n) for n in names)
    tot = {"rows": 0, "new": 0, "changed": 0, "same": 0, "pages": 0}
    offset = 0
    while True:
        rows, raw, params = lp.cargo(s["tables"], s["fields"], s["order"], where=s["where"](q), offset=offset)
        at = store.now_iso()
        snap = store.save_raw(con, "leaguepedia", f"cargoquery:{tbl}", params, raw, n_rows=len(rows), fetched_at=at)
        c = store.put_rows(con, tbl, rows, s["key"], None, snap, at)
        tot["pages"] += 1
        tot["rows"] += len(rows)
        for k in ("new", "changed", "same"):
            tot[k] += c[k]
        if len(rows) < lp.PAGE:
            break
        offset += lp.PAGE
    for n in names:
        con.execute("INSERT INTO ref_state (kind, name, checked_at, found) VALUES (?, ?, ?, NULL) ON CONFLICT(kind, name) DO "
                    "UPDATE SET checked_at = excluded.checked_at", (tbl, n, store.now_iso()))
    con.commit()
    return tot


# ---------------------------------------------------------------- what still needs fetching
_SEEN = {"v": None, "names": None}


def _seen_names(con):
    """Names referenced by stored rows, per reference table (cached until a source row is added)."""
    v = con.execute("SELECT MAX(rowid) FROM lp_rows").fetchone()[0]
    if _SEEN["v"] == v and _SEEN["names"] is not None:
        return _SEEN["names"]
    _SEEN["names"] = _seen_names_now(con)
    _SEEN["v"] = v
    return _SEEN["names"]


def _seen_names_now(con):
    q = lambda sql: {page_key(r[0]) for r in con.execute(sql) if r[0]}
    cur = ("SELECT json_extract(r.data, '$.{f}') FROM lp_current c JOIN lp_rows r ON r.tbl = c.tbl AND r.skey = c.skey "
           "AND r.rev = c.rev WHERE c.tbl = '{t}' AND c.gone_at IS NULL")
    links = q(cur.format(f="Link", t="ScoreboardPlayers")) | q(cur.format(f="Link", t="TournamentPlayers"))
    teams = set()
    for t, fs in (("ScoreboardGames", ("Team1", "Team2")), ("MatchSchedule", ("Team1", "Team2")), ("ScoreboardPlayers", ("Team",))):
        for f in fs:
            teams |= q(cur.format(f=f, t=t))
    teams -= {"TBD", "Tbd"}
    tours = q(cur.format(f="OverviewPage", t="ScoreboardGames")) | q(cur.format(f="OverviewPage", t="MatchSchedule"))
    pages = q(cur.format(f="OverviewPage", t="PlayerRedirects"))
    return {"PlayerRedirects": links, "Players": pages | links, "Teams": teams, "TeamRedirects": teams,
            "Tournaments": tours, "TournamentPlayers": tours, "Tenures": pages}


def _recent_tournaments(con, days=30):
    lo = _d(datetime.now(timezone.utc).date() - timedelta(days=days))
    hi = _d(datetime.now(timezone.utc).date() + timedelta(days=days))
    return {page_key(r[0]) for r in con.execute(
        "SELECT DISTINCT json_extract(r.data, '$.OverviewPage') FROM lp_current c JOIN lp_rows r ON r.tbl = c.tbl AND "
        "r.skey = c.skey AND r.rev = c.rev WHERE c.tbl = 'MatchSchedule' AND c.gone_at IS NULL AND c.event_time >= ? AND "
        "c.event_time < ?", (lo, hi)) if r[0]}


def pending_names(con, tbl, now=None):
    now = now or time.time()
    seen = _seen_names(con)[tbl]
    checked = {r[0]: r[1] for r in con.execute("SELECT name, checked_at FROM ref_state WHERE kind = ?", (tbl,))}
    recheck = store.now_iso(now - REF_RECHECK_S)
    live = _recent_tournaments(con) if tbl == "TournamentPlayers" else set()
    daily = store.now_iso(now - 86400)
    out = []
    for n in sorted(seen):
        c = checked.get(n)
        if c is None or c < recheck or (n in live and c < daily):
            out.append(n)
    return out


def _complete(ck, hi, now):
    """A window is complete once swept AFTER it closed (hi + 1 day). A window still open counts as done once swept -
    the live sweep (last 3 days, every 30 min) keeps it current - and is swept again after it closes."""
    if not ck or ck.get("state") != "done":
        return False
    closed = datetime.strptime(hi, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) + timedelta(days=1)
    closed_iso = store.now_iso(closed.timestamp())
    return ck["updated_at"] >= closed_iso or now < closed.timestamp()


def next_unit(con, now=None):
    """The next unit of work, or None when everything is done and fresh."""
    now = now or time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date()
    live = _ck(con, "live")
    if not live or live["updated_at"] < store.now_iso(now - LIVE_EVERY_S):
        return ("live", None)
    for tbl in ("PlayerRedirects", "Players", "Teams", "TeamRedirects", "Tournaments", "TournamentPlayers"):
        names = pending_names(con, tbl, now)
        if names:
            return ("ref", (tbl, names[:REF_BATCH if tbl != "TournamentPlayers" else 8]))
    wins = windows(today)
    for tier_tables in (CORE, SECONDARY):
        for lo, hi in wins:
            for tbl in tier_tables:
                if not _complete(_ck(con, f"w|{tbl}|{lo}"), hi, now):
                    return ("window", (tbl, lo, hi))
    names = pending_names(con, "Tenures", now)
    if names:
        return ("ref", ("Tenures", names[:REF_BATCH]))
    cut = _d(today - timedelta(days=RESWEEP_DAYS))
    for lo, hi in wins:
        if hi <= cut:
            break
        for tbl in CORE:
            ck = _ck(con, f"w|{tbl}|{lo}")
            if ck and ck["updated_at"] < store.now_iso(now - RESWEEP_EVERY_S):
                return ("window", (tbl, lo, hi))
    return None


def run_unit(con, unit):
    kind, arg = unit
    t0, r0 = time.time(), lp.PACER.stats["requests"]
    job = "live" if kind == "live" else (f"w|{arg[0]}|{arg[1]}" if kind == "window" else f"ref|{arg[0]}")
    try:
        if kind == "live":
            today = datetime.now(timezone.utc).date()
            counts = {}
            for tbl in ("ScoreboardGames", "ScoreboardPlayers", "MatchScheduleGame"):
                counts[tbl] = sweep(con, tbl, _d(today - timedelta(days=3)), _d(today + timedelta(days=1)))
            counts["MatchSchedule"] = sweep(con, "MatchSchedule", _d(today - timedelta(days=3)), _d(today + timedelta(days=15)))
            agg = {k: sum(c.get(k, 0) for c in counts.values()) for k in ("rows", "new", "changed")}
            _set_ck(con, "live", "done", counts)
        elif kind == "window":
            tbl, lo, hi = arg
            agg = sweep(con, tbl, lo, hi)
            _set_ck(con, job, "done", agg)
        else:
            tbl, names = arg
            agg = lookup(con, tbl, names)
        if agg.get("new") or agg.get("changed") or agg.get("gone"):
            store.set_meta(con, "dirty", store.now_iso())
        _log(con, job, True, t0, lp.PACER.stats["requests"] - r0, agg, {"unit": unit[1] if kind != "ref" else [arg[0], len(arg[1])]})
        con.commit()
        return True, agg
    except Exception as e:
        con.rollback()
        _log(con, job, False, t0, lp.PACER.stats["requests"] - r0, {}, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
        con.commit()
        return False, {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def progress(con, today=None):
    wins = windows(today)
    done = {r[0] for r in con.execute("SELECT job FROM checkpoints WHERE state = 'done' AND job LIKE 'w|%'")}
    by = {}
    for tbl in CORE + SECONDARY:
        n = sum(1 for lo, _ in wins if f"w|{tbl}|{lo}" in done)
        by[tbl] = {"windows_done": n, "windows": len(wins)}
    rows = {r[0]: r[1] for r in con.execute("SELECT tbl, COUNT(*) FROM lp_current WHERE gone_at IS NULL GROUP BY tbl")}
    last = con.execute("SELECT job, at, ok, detail FROM job_log ORDER BY id DESC LIMIT 1").fetchone()
    fails = con.execute("SELECT COUNT(*) FROM job_log WHERE ok = 0 AND at >= ?", (store.now_iso(time.time() - 86400),)).fetchone()[0]
    return {"backfill_days": BACKFILL_DAYS, "window_days": WINDOW_DAYS, "tables": by, "rows": rows,
            "live_sweep": (_ck(con, "live") or {}).get("updated_at"), "failed_units_24h": fails,
            "last_unit": dict(last) if last else None, "pacer": lp.PACER.view()}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Leaguepedia ingestion (read-only, paced)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max-units", type=int, default=0)
    r.add_argument("--until-done", action="store_true")
    r.add_argument("--build-every", type=int, default=1800)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    con = store.connect()
    if a.cmd == "status":
        print(json.dumps(progress(con), indent=1, default=str))
        return 0
    from . import build
    n, t_build = 0, time.time()
    while True:
        u = next_unit(con)
        if u is None:
            if a.until_done:
                break
            time.sleep(60)
            continue
        ok, agg = run_unit(con, u)
        n += 1
        print(store.now_iso(), "ok" if ok else "FAIL", u[0], (u[1] if u[0] != "ref" else (u[1][0], len(u[1][1]))),
              {k: agg.get(k) for k in ("rows", "new", "changed", "gone", "pages", "error") if agg.get(k) is not None},
              "interval", round(lp.PACER.interval, 1), flush=True)
        if not ok:
            time.sleep(min(600, lp.PACER.interval * 3))
        if time.time() - t_build > a.build_every:
            print("build:", json.dumps(build.build(con), default=str)[:400], flush=True)
            t_build = time.time()
        if a.max_units and n >= a.max_units:
            break
    print("build:", json.dumps(build.build(con), default=str)[:400], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
