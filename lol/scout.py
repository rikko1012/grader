#!/usr/bin/env python3
"""lol scout - everything the LoL grader needs, the way cs2_scout.py does it for CS2.

Commands (python -m lol <command> ..., or python lol_scout.py <command> ...):
  slate    LoL matches in the next 36 hours (LoL Esports schedule)
  sync     fill the game history: LoL Esports feed (no login) and/or Leaguepedia (bot password)
  status   what the history holds (sources, leagues, dates) and which sources are configured
  player   a player's stored games: role, team, per-game averages, Maps 1-2 / Map 1 / Maps 1-3 totals, last games
  h2h      the player's series against an opponent, hit rate vs a line
  odds     Polymarket: series price, game winners, how long the series runs, total-kills lines
  lines    the PrizePicks / Underdog LoL board (or one player's lines)
  full     everything above for one player, plus the projection and P(over / under) for each line

Results print to the screen and save to <LOL_DATA_DIR>/output/<player>_<command>_<date>.json.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

from . import config, elo, history, lines, lolesports, model, odds, sync, teams

SCOPE_NAME = {"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"}
BO_SCOPE = {1: "1", 3: "12", 5: "123"}


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _day(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date()


# ------------------------------------------------------------------ history -> model inputs
def match_days(rows):
    """{match_key: day ordinal of its first game} - every game of a series counts on the series' day (as in the backtest)."""
    out = {}
    for r in rows:
        d = _day(r["played_at"]).toordinal()
        mk = r.get("match_key") or r.get("game_key")
        out[mk] = min(out.get(mk, d), d)
    return out


def player_inputs(rows):
    md = match_days(rows)
    return [(md[r.get("match_key") or r.get("game_key")], r["kills"], r["deaths"], r["assists"], r["team_kills"],
             r["opp_kills"], r.get("win")) for r in rows if None not in (r.get("kills"), r.get("team_kills"), r.get("opp_kills"))]


def team_inputs(rows):
    md = match_days(rows)
    return [(md[r.get("match_key") or r.get("game_key")], r["team_kills"], r["opp_kills"]) for r in rows
            if r.get("team_kills") is not None and r.get("opp_kills") is not None]


def series_totals(rows, prop, scope):
    """Per match the player's total over games 1..n (only matches where he played all of them), newest first."""
    n = model.SCOPE_GAMES[scope]
    by = defaultdict(dict)
    meta = {}
    for r in rows:
        if r.get("game_number"):
            by[r["match_key"]][int(r["game_number"])] = r
            meta.setdefault(r["match_key"], r)
    out = []
    for mk, games in by.items():
        if all(g in games for g in range(1, n + 1)):
            m = meta[mk]
            out.append({"match": mk, "date": str(m["played_at"])[:10], "opponent": games[1]["opponent"],
                        "league": m.get("league"), "best_of": m.get("best_of"),
                        "total": sum(games[g][prop] for g in range(1, n + 1)),
                        "per_game": [games[g][prop] for g in range(1, n + 1)],
                        "won_series_games": sum(1 for g in range(1, n + 1) if games[g].get("win") == 1)})
    return sorted(out, key=lambda x: x["date"], reverse=True)


# ------------------------------------------------------------------ commands
def resolve_player(db, name, team=None):
    """The stored player for this name: by --team when given, else the most recently active namesake."""
    cands = history.find_players(db, name)
    if team:
        on_team = [c for c in cands if teams.same_team(team, c["team"])]
        if not on_team:                 # never a namesake on another team in his place
            return None, cands
        cands = on_team
    if not cands:
        return None, []
    best = cands[0]
    return best, cands


def run_player(db, args):
    pl, cands = resolve_player(db, args.name, args.team)
    if not pl:
        note = (f"No stored player {args.name} on {args.team} - stored under that name: "
                + ", ".join(f"{c['player']} ({c['team']})" for c in cands)) if cands else \
            ("Not in the stored history - run `sync` (LoL Esports), or add Leaguepedia for leagues the feed "
             "doesn't carry (LPL).")
        return {"found": False, "query": args.name, "note": note}
    rows = history.player_games(db, pl["player_key"], pid=pl["pid"])
    team_rows = [r for r in rows if teams.same_team(r["team"], pl["team"])]
    last = rows[-1]
    out = {"found": True, "player": last["player"], "player_key": pl["player_key"], "pid": pl["pid"], "team": last["team"],
           "role": last["role"],
           "games": len(rows), "games_on_team": len(team_rows), "first": rows[0]["played_at"][:10],
           "last": last["played_at"][:10], "sources": sorted({r["source"] for r in rows}),
           "namesakes": [c for c in cands if c["pid"] != pl["pid"]]}
    today = datetime.now(timezone.utc).date()
    for label, days in (("last_3m", 90), ("last_6m", 180), ("last_12m", 365)):
        rs = [r for r in rows if (today - _day(r["played_at"])).days <= days]
        if rs:
            out[label] = {"games": len(rs), **{p: round(sum(r[p] for r in rs) / len(rs), 2) for p in ("kills", "assists", "deaths")},
                          "kill_share": round(sum(r["kills"] for r in rs) / max(1, sum(r["team_kills"] for r in rs)), 3),
                          "win_rate": _rate([r.get("win") for r in rs])}
    wins = [r for r in rows[-40:] if r.get("win") == 1]
    losses = [r for r in rows[-40:] if r.get("win") == 0]
    out["last_40_split"] = {"kills_in_wins": _avg(wins, "kills"), "kills_in_losses": _avg(losses, "kills"),
                            "wins": len(wins), "losses": len(losses)}
    out["last_games"] = [{k: r.get(k) for k in ("played_at", "league", "opponent", "game_number", "champion", "kills",
                                                 "deaths", "assists", "team_kills", "win")} for r in rows[-10:][::-1]]
    for scope in ("1", "12", "123"):
        out[f"series_{scope}"] = series_totals(rows, "kills", scope)[:10]
    # lineup check: did his team play a later game without him? (benched / substituted / transferred)
    later = [g for g in history.team_games(db, pl["team"]) if g["played_at"] > last["played_at"]]
    out["lineup"] = ("ok - he played his team's latest stored game" if not later else
                     f"WARNING: his team played {len(later)} later game(s) without him (benched, substitute or transfer?)")
    return out


def _avg(rows, k):
    return round(sum(r[k] for r in rows) / len(rows), 2) if rows else None


def _rate(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def run_h2h(db, args):
    pl, _ = resolve_player(db, args.name, args.team)
    if not pl:
        return {"found": False, "query": args.name}
    rows = [r for r in history.player_games(db, pl["player_key"], pid=pl["pid"])
            if teams.same_team(args.opponent, r["opponent"])]
    scope = args.scope or "12"
    ser = series_totals(rows, args.prop, scope)
    out = {"found": True, "player": pl["player"], "opponent": args.opponent, "prop": args.prop, "scope": scope,
           "series": ser}
    if args.line is not None and ser:
        over = sum(s["total"] > args.line for s in ser)
        under = sum(s["total"] < args.line for s in ser)
        out["vs_line"] = {"line": args.line, "over": over, "under": under, "push": len(ser) - over - under,
                          "over_rate": round(over / (over + under), 3) if over + under else None}
    return out


def next_match(team, slate_rows):
    for m in slate_rows:
        for i, t in enumerate(m["teams"]):
            if teams.matches(team, t["name"], t["code"]):
                return m, t, m["teams"][1 - i]
    return None, None, None


def grade_lines(proj_by_key, line_rows):
    out = []
    for ln in line_rows:
        pj = proj_by_key.get((ln["prop"], ln["scope"]))
        if not pj:
            out.append(dict(ln, graded=False, note=f"{ln['prop']} {SCOPE_NAME.get(ln['scope'])} not projected"))
            continue
        po, pu, push = model.probs(pj["lambda"], pj["nb_size"], ln["line"])
        side = "over" if po >= pu else "under"
        if not ln.get(f"{side}_available", True):          # the side the model prefers is not offered: no play
            side = f"{side} - not offered on this line (no play)"
        out.append(dict(ln, graded=True, projection=round(pj["lambda"], 2), p_over=round(po, 3),
                        p_under=round(pu, 3), p_push=round(push, 3), model_side=side))
    return out


def run_full(db, args):
    res = {"query": args.name, "model": model.VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    pres = run_player(db, args)
    res["player"] = pres
    if not pres.get("found"):
        # no history (e.g. the LPL before Leaguepedia is set up): still show his lines - ungraded, never a guess
        try:
            res["lines"] = [dict(ln, graded=False, note="no stored history - not projected")
                            for ln in lines.lines_for(args.name, args.team)]
        except Exception as e:      # noqa: BLE001
            res["lines_error"] = f"{type(e).__name__}: {e}"
        return res
    team = pres["team"]
    # the match: the next one on the schedule for his team, unless --opponent says otherwise
    try:
        slate_rows = lolesports.slate(hours=args.hours, league_slugs="all")
    except Exception as e:          # noqa: BLE001 - schedule down: grade on --opponent alone
        slate_rows, res["schedule_error"] = [], f"{type(e).__name__}: {e}"
    m, mine, theirs = next_match(team, slate_rows)
    if args.opponent and (not theirs or not teams.matches(args.opponent, theirs["name"], theirs["code"])):
        # the team's code from the schedule when it lists the team at all: PrizePicks names the opponent by code
        code = next((t["code"] for row in slate_rows for t in row["teams"] if teams.matches(args.opponent, t["name"], t["code"])),
                    None)
        m, theirs = None, {"name": args.opponent, "code": code}
    res["match"] = ({"league": m["league"], "start": m["start"], "best_of": m["best_of"], "match_id": m["match_id"],
                     "team": mine, "opponent": theirs} if m else
                    {"opponent": theirs, "note": "not on the LoL Esports schedule in the next "
                                                 f"{args.hours} h" + (" - opponent from --opponent" if theirs else "")})
    opponent = (theirs or {}).get("name")
    start = m["start"] if m else None
    day = (_day(start) if start else datetime.now(timezone.utc).date()).toordinal()
    # no cut-off by game time here: the model keeps games whose SERIES day is before the match day (as the backtest
    # does) - a cut on game time would drop the late games of an earlier series that ran past midnight UTC
    prow = history.player_games(db, pres["player_key"], pid=pres["pid"])
    trow = history.team_games(db, team)
    orow = history.team_games(db, opponent) if opponent else []
    n_before = lambda rows: sum(1 for d in match_days(rows).values() if d < day)
    res["inputs"] = {"player_series": n_before(prow), "team_series": n_before(trow), "opponent_series": n_before(orow),
                     "opponent_found": bool(n_before(orow))}
    flags = []
    if opponent and not res["inputs"]["opponent_found"]:
        flags.append(f"No stored games for {opponent} - opponent factor = league average")
    if not opponent:
        flags.append("Opponent unknown - opponent factor = league average")
    if pres.get("lineup", "").startswith("WARNING"):
        flags.append(pres["lineup"])
    res["flags"] = flags
    projs = {}
    if model.PARAMS is None:            # no fit yet: still the odds and the lines, ungraded
        res["projection"] = {"error": "no fitted model (python -m lol.backtest --write)"}
    else:
        projs = _project(db, res, pres, team, opponent, day, prow, trow, orow)
    _odds_and_lines(res, args, pres, team, opponent, start, m, mine, theirs, projs, flags)
    return res


def _project(db, res, pres, team, opponent, day, prow, trow, orow):
    pin, tin, oin = player_inputs(prow), team_inputs(trow), team_inputs(orow)
    role = pres["role"]
    p_win = None
    if opponent:                     # the backtest's Elo, from every stored game before the match day
        allrows = history.all_player_games(db)
        md = match_days(allrows)
        p_win = elo.from_rows(allrows, lambda r: md[r.get("match_key") or r.get("game_key")]).p_win(team, opponent, day)
        res["inputs"]["elo_p_win"] = round(p_win, 3)
    projs = {}
    for prop in ("kills", "assists", "deaths"):
        for scope in model.SCOPE_GAMES:
            projs[(prop, scope)] = model.project(pin, tin, oin, role, day, scope, prop, p_win=p_win)
    res["projection"] = {f"{p}|{s}": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in pj.items()}
                         for (p, s), pj in projs.items()}
    return projs


def _same_match(ln, theirs, start):
    """A board line belongs to the graded match: its opponent is that opponent (when both are known) and it starts
    within 6 hours of the match (when both are known) - a line for his NEXT match is never graded with this one."""
    if ln.get("opponent") and theirs and theirs.get("name") and \
            not teams.matches(ln["opponent"], theirs["name"], theirs.get("code")) and \
            (theirs.get("code") or ln.get("book") != "prizepicks"):    # a PrizePicks code with no code known: no verdict
        return False
    a, b = _when(ln.get("start")), _when(start)
    return a is None or b is None or abs((a - b).total_seconds()) <= 6 * 3600


def _when(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _odds_and_lines(res, args, pres, team, opponent, start, m, mine, theirs, projs, flags):
    # the match's odds: series price, game winners, chance a 3rd / 4th game is played
    if opponent:
        try:
            od = odds.match_odds(team, opponent, start)
        except Exception as e:      # noqa: BLE001
            od = {"found": False, "error": f"{type(e).__name__}: {e}"}
        if od.get("found"):
            bo = (m or {}).get("best_of") or od.get("best_of")
            od["p_game3_played"] = 1.0 if bo == 5 else odds.p_game_played(od, 3)
        res["odds"] = od
    # lines: the member's line, else every line on the board for him
    if args.line is not None:
        scope = args.scope or BO_SCOPE.get((m or {}).get("best_of"), "12")
        my = [{"book": "manual", "player": pres["player"], "prop": args.prop, "scope": scope, "line": args.line,
               "odds_type": args.line_type, "under_available": args.line_type == "standard"}]
    else:
        try:
            board = lines.lines_for(pres["player"], team, (mine or {}).get("code"))
        except Exception as e:      # noqa: BLE001
            board, res["lines_error"] = [], f"{type(e).__name__}: {e}"
        my = [ln for ln in board if _same_match(ln, theirs, start)]
        if len(my) < len(board):
            res["lines_other_matches"] = len(board) - len(my)
    res["lines"] = grade_lines(projs, my)
    if any(ln.get("scope") == "123" for ln in my) and (m or {}).get("best_of") == 3:
        flags.append("Maps 1-3 line in a best-of-3: map 3 may not be played (Underdog voids it; check the book's rule)"
                     + (f" - Polymarket P(map 3 played) {res['odds']['p_game3_played']:.0%}"
                        if (res.get("odds") or {}).get("p_game3_played") else ""))
    return res


def run_status(db, args):
    miss = history.missing(db)
    return {"history": history.summary(db),
            "missing_games": {"total": len(miss), "filled_by_leaguepedia": sum(1 for m in miss if m["filled"]),
                              "still_missing_total": sum(1 for m in miss if not m["filled"]),
                              "still_missing": [{k: m[k] for k in ("league", "played_at", "teams", "game_number", "reason")}
                                                for m in miss if not m["filled"]][:50]},
            "le_last_sync": history.get_state(db, "le:last_sync"),
            "lp_last_sync": history.get_state(db, "lp:last_sync"),
            "leaguepedia_configured": bool(config.leaguepedia_credentials()),
            "database": "postgres (LOL_DB_DSN)" if config.db_dsn() else str(config.HISTORY_DB),
            "model_fit": model.FIT_META, "data_dir": str(config.DATA_DIR)}


def run_sync(db, args):
    """One ingestion path (2026-10-17): the data center (lol/center) reads LoL Esports and Leaguepedia - with source
    revisions, internal ids, renames and quarantine - and exports the result to this history. sync.py's direct
    writers are no longer called, so the history has one source of truth."""
    unsupported = [name for name in ("since", "until", "force", "leagues") if getattr(args, name, None)]
    if unsupported:
        raise ValueError("The canonical sync does not support " + ", ".join("--" + x for x in unsupported)
                         + "; set LOL_BACKFILL_DAYS before starting the process instead of silently ignoring a range.")
    from .center import cli
    return cli.sync_once(source=args.source, max_minutes=getattr(args, "max_minutes", 120), log=_log)


# ------------------------------------------------------------------ printing
def print_full(res):
    p = res.get("player") or {}
    if not p.get("found"):
        print(p.get("note") or "player not found")
        for ln in res.get("lines") or []:
            print(f"  {ln['book']:10s} {ln['prop']:7s} {SCOPE_NAME[ln['scope']]:9s} {ln['line']:5.1f} ({ln['odds_type']}) "
                  f"- {ln['note']}")
        return
    print(f"\n{p['player']} ({p['role']}, {p['team']}) - {p['games']} stored games, {p['first']} .. {p['last']}")
    m = res.get("match") or {}
    if m.get("start"):
        print(f"Match: vs {m['opponent']['name']} - {m['league']} BO{m['best_of']} - {m['start']}")
    elif m.get("opponent"):
        print(f"Match: vs {m['opponent']['name']} ({m.get('note')})")
    for f in res.get("flags") or []:
        print(f"  ! {f}")
    pj = res.get("projection") or {}
    if "error" in pj:
        print(pj["error"])
    else:
        for scope in ("1", "12", "123"):
            k = pj.get(f"kills|{scope}")
            if k:
                lo, hi = model.interval(k["lambda"], k["nb_size"])
                print(f"  {SCOPE_NAME[scope]:9s} kills {k['lambda']:5.2f} (80%: {lo}-{hi})  assists "
                      f"{pj[f'assists|{scope}']['lambda']:5.2f}  deaths {pj[f'deaths|{scope}']['lambda']:5.2f}"
                      f"  [{k['data_quality']}]")
    od = res.get("odds") or {}
    if od.get("found"):
        print(f"Polymarket: {od['team']} {od.get('team_prob')} vs {od['opponent']} {od.get('opp_prob')} "
              f"(liquidity ${od['liquidity']:,})" + (f"; P(map 3 played) {od['p_game3_played']}" if od.get("p_game3_played") else ""))
    for ln in res.get("lines") or []:
        if not ln.get("graded"):
            print(f"  {ln['book']:10s} {ln['prop']:7s} {SCOPE_NAME[ln['scope']]:9s} {ln['line']:5.1f} - {ln.get('note')}")
        if ln.get("graded"):
            print(f"  {ln['book']:10s} {ln['prop']:7s} {SCOPE_NAME[ln['scope']]:9s} {ln['line']:5.1f} ({ln['odds_type']}): "
                  f"projection {ln['projection']:.2f}  P(over) {ln['p_over']:.1%}  P(under) {ln['p_under']:.1%}  "
                  f"-> {ln['model_side']}")
    if res.get("lines_other_matches"):
        print(f"  {res['lines_other_matches']} more line(s) on the board are for another match - not graded")
    print("  (model estimate - see lol/EVALUATION.md for its measured accuracy)")


def save(res, name, cmd):
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "all").lower()).strip("_") or "all"
    path = config.OUT_DIR / f"{slug}_{cmd}_{date.today().isoformat()}.json"
    path.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return path


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("name", nargs="?", default="", help="Player name (team name for 'odds')")
    common.add_argument("--team", help="The player's team (picks between namesakes)")
    common.add_argument("--opponent", help="Opponent team")
    common.add_argument("--line", type=float, help="A line to grade (else every line on the board for him)")
    common.add_argument("--prop", choices=["kills", "assists", "deaths"], default="kills")
    common.add_argument("--scope", choices=["1", "12", "123"], help="Map 1 / Maps 1-2 / Maps 1-3 (default: from best-of)")
    common.add_argument("--line-type", choices=["standard", "goblin", "demon"], default="standard")
    common.add_argument("--hours", type=float, default=36)
    common.add_argument("--since", help="legacy: sync rejects it (set LOL_BACKFILL_DAYS before the run instead)")
    common.add_argument("--until", help="legacy: sync rejects it")
    common.add_argument("--leagues", help="slate: comma-separated LoL Esports slugs, or 'all' (default: majors); sync rejects it")
    common.add_argument("--source", choices=["lolesports", "leaguepedia", "both"], default="both")
    common.add_argument("--max-minutes", type=float, default=120, help="sync: bounded ingestion time before exporting")
    common.add_argument("--force", action="store_true", help="legacy: sync rejects it")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("slate", "sync", "status", "player", "h2h", "odds", "lines", "full"):
        sub.add_parser(c, parents=[common])
    a = ap.parse_args(argv)
    if a.cmd == "slate":
        lg = None if not a.leagues else ("all" if a.leagues.strip().lower() == "all" else
                                         [x.strip().lower() for x in a.leagues.split(",") if x.strip()])
        res = lolesports.slate(a.hours, lg)
        for m in res:
            print(f"{m['start']}  {m['league']:12s} BO{m['best_of']}  {m['teams'][0]['name']} vs {m['teams'][1]['name']}")
    elif a.cmd == "odds":
        res = odds.match_odds(a.name, a.opponent)
        print(json.dumps(res, indent=1))
    elif a.cmd == "lines":
        b = lines.board()
        res = {"status": b["status"], "rows": lines.lines_for(a.name, a.team) if a.name else b["rows"]}
        for r in res["rows"]:
            print(f"{r['book']:10s} {r['player']:14s} {r.get('team') or '':18s} {r['prop']:7s} "
                  f"{SCOPE_NAME[r['scope']]:9s} {r['line']:5.1f} {r['odds_type']}")
        print(json.dumps(b["status"]), file=sys.stderr)
    else:
        db = history.DB()
        try:
            res = {"sync": run_sync, "status": run_status, "player": run_player, "h2h": run_h2h,
                   "full": run_full}[a.cmd](db, a)
        finally:
            db.close()
        if a.cmd == "full":
            print_full(res)
        else:
            print(json.dumps(res, indent=1, default=str))
    path = save(res, a.name, a.cmd)
    print(f"saved {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
