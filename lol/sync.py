"""Fill the LoL game history (lol/history.py) from the sources. Run on a schedule (like the HLTV scraper); the scout only
reads what this wrote.

  sync_lolesports  every completed match of the chosen leagues since a date, from the LoL Esports API + livestats feed.
                   No login. A match already held in full is skipped, so a re-run only fetches new games.
  sync_leaguepedia every scoreboard row since a date, from Leaguepedia's Cargo tables (needs the bot password). Covers
                   events the LoL Esports feed doesn't broadcast, with the official winner and exact game length.
"""
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from . import cache, history, lolesports


DEFAULT_DAYS = 240        # ~8 months: enough recent history for the current props (older seasons: --since)


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def game_winners(played, stats, score):
    """{game_id: (winning team id or None, basis)} for a match's played games (in game order). Certain first: in a sweep
    every game went to the series winner, and the last game of any series is the series winner's. The other games use
    the inferred winner (lolesports.winner) only when every inferred win adds up to the official series score."""
    if not played:
        return {}
    total = sum(v or 0 for v in score.values())
    ranked = sorted(score, key=lambda t: score[t] or 0, reverse=True)
    tied = len(ranked) < 2 or (score[ranked[0]] or 0) == (score[ranked[1]] or 0)      # e.g. a best-of-2 drawn 1-1
    series_winner = None if tied else ranked[0]
    if series_winner and sum(score[t] or 0 for t in ranked[1:]) == 0:
        return {g["id"]: (series_winner, "series sweep") for g in played}            # even with games missing
    if len(played) != total:                    # a game missing from the feed: only the last game is still certain
        return {g["id"]: ((series_winner, "last game of the series") if series_winner and g.get("number") == total
                          else (None, "series incomplete in the feed")) for g in played}
    inferred, basis = {}, {}
    for g in played:
        s = stats[g["id"]]
        side, basis[g["id"]] = lolesports.winner(s["sides"]["blue"], s["sides"]["red"])   # today's rule, also on
        inferred[g["id"]] = (s["sides"].get(side) or {}).get("team_id") if side else None  # games cached earlier
    wins = Counter(inferred.values())
    consistent = None not in inferred.values() and all(wins.get(t, 0) == (n or 0) for t, n in score.items())
    last = played[-1]["id"]
    out = {}
    for g in played:
        if g["id"] == last and series_winner:
            out[g["id"]] = (series_winner, "last game of the series")
        elif consistent:
            out[g["id"]] = (inferred[g["id"]], basis[g["id"]] + ", matches the series score")
        else:
            out[g["id"]] = (None, "inferred wins disagree with the series score")
    return out


def lolesports_rows(match, details, stats):
    """One match -> [(game_row, player_rows)] for history.save_game. `stats` = {game_id: lolesports.game_stats()}.
    Game winners from game_winners(): certain ones (sweep, last game) always; inferred ones only when they add up to the
    series score the API reports, otherwise winner = None."""
    m = details.get("match") or {}
    tinfo = {t.get("id"): t for t in m.get("teams") or []}
    played = sorted([g for g in m.get("games") or [] if g.get("state") == "completed" and stats.get(g.get("id"))],
                    key=lambda g: g.get("number") or 0)
    score = {tid: (t.get("result") or {}).get("gameWins") for tid, t in tinfo.items()}
    winners = game_winners(played, stats, score)
    out = []
    for g in played:
        s = stats[g["id"]]
        sides = s["sides"]
        name = {side: (tinfo.get(sides[side]["team_id"]) or {}).get("name") for side in ("blue", "red")}
        code = {side: (tinfo.get(sides[side]["team_id"]) or {}).get("code") for side in ("blue", "red")}
        w_team, basis = winners[g["id"]]
        w_side = next((side for side in ("blue", "red") if w_team and sides[side]["team_id"] == w_team), None)
        game = {"game_key": f"le:{g['id']}", "source": "le", "match_key": f"le:{match['match_id']}",
                "game_number": g.get("number"), "best_of": match.get("best_of") or (m.get("strategy") or {}).get("count"),
                "played_at": s.get("start") or match.get("start"), "league": match.get("league"),
                "tournament": match.get("tournament"), "blue_team": name["blue"], "red_team": name["red"],
                "blue_team_id": sides["blue"]["team_id"], "red_team_id": sides["red"]["team_id"],
                "blue_kills": sides["blue"].get("kills"), "red_kills": sides["red"].get("kills"),
                "winner": name.get(w_side) if w_side else None, "winner_basis": basis,
                "duration_s": s.get("duration_s"), "patch": s.get("patch")}
        players = []
        for p in s["players"]:
            side, other = p["side"], ("red" if p["side"] == "blue" else "blue")
            nm = lolesports.clean_name(p.get("summoner"), code[side])
            players.append({
                "game_key": game["game_key"], "player": nm, "player_key": history.player_key(nm),
                "player_id": p.get("player_id"), "team": name[side], "team_id": sides[side]["team_id"],
                "opponent": name[other], "opponent_id": sides[other]["team_id"], "side": side, "role": p.get("role"),
                "champion": p.get("champion"), "kills": p.get("kills"), "deaths": p.get("deaths"),
                "assists": p.get("assists"), "cs": p.get("cs"), "gold": p.get("gold"),
                "team_kills": sides[side].get("kills"), "opp_kills": sides[other].get("kills"),
                "win": None if not w_side else int(w_side == side), "duration_s": s.get("duration_s"),
                "match_key": game["match_key"], "game_number": game["game_number"], "best_of": game["best_of"],
                "played_at": game["played_at"], "league": game["league"], "source": "le"})
        out.append((game, players))
    return out


def _fetch_match(m, now):
    """(details, {game_id: stats}) for one match - network only, safe to run in a worker thread."""
    det = lolesports.event_details(m["match_id"])
    stats = {}
    for g in (det.get("match") or {}).get("games") or []:
        if g.get("state") == "completed":
            stats[g["id"]] = lolesports.game_stats(g["id"], now=now)
    return det, stats


def sync_lolesports(db, leagues=None, since=None, max_matches=None, now=None, workers=4, force=False):
    """Fetch every completed match not yet held in full. Returns {"matches", "games", "skipped", "no_feed", "errors"}.
    `workers` matches are fetched at once; the per-host pacing in lol/net.py still caps the request rate (4 / s per
    host), and only this thread writes to the database. force=True re-derives matches already held (from the cache:
    finished games are never downloaded twice) - after a change to how rows are built."""
    now = now or datetime.now(timezone.utc)
    since = since or (now - timedelta(days=DEFAULT_DAYS)).date()
    known = {} if force else history.known_matches(db, "le")
    todo = sorted(lolesports.matches_since(leagues, since), key=lambda m: m.get("start") or "")
    done = Counter()
    pending = []
    for m in todo:
        expected = sum(t.get("game_wins") or 0 for t in m["teams"])
        if expected and known.get(f"le:{m['match_id']}", 0) >= expected:
            done["skipped"] += 1
        elif cache.get("le_nofeed", str(m["match_id"]), 30 * 86400) is not None:
            done["no_feed"] += 1
        else:
            pending.append(m)
    if max_matches:
        pending = pending[:max_matches]
    _log(f"  LoL Esports: {len(pending)} matches to fetch ({done['skipped']} already held)")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_fetch_match, m, now): m for m in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            m = futures[fut]
            try:
                det, stats = fut.result()
            except Exception as e:      # noqa: BLE001 - one bad match never stops the sync
                _log(f"  match {m['match_id']}: {type(e).__name__}: {e}")
                done["errors"] += 1
                continue
            rows = lolesports_rows(m, det, stats)
            label = " vs ".join(t.get("name") or "?" for t in (det.get("match") or {}).get("teams") or [])
            for g in (det.get("match") or {}).get("games") or []:
                if g.get("state") == "completed" and not stats.get(g.get("id")):
                    history.note_missing(db, f"le:{g['id']}", "le", f"le:{m['match_id']}", g.get("number"),
                                         m.get("league"), m.get("start"), label,
                                         "feed has no final frame (stopped before the game ended, or no feed)")
                    done["games_missing"] += 1
            if not rows:
                db.commit()
                st = _when(m.get("start"))
                if st and now - st > timedelta(days=3):
                    cache.put("le_nofeed", str(m["match_id"]), True)   # old match, no feed: not asked again for 30 days
                done["no_feed"] += 1
                continue
            for game, players in rows:
                history.save_game(db, game, players)
            db.commit()
            done["matches"] += 1
            done["games"] += len(rows)
            if i % 50 == 0:
                _log(f"  {i}/{len(pending)} fetched - {done['matches']} saved ({done['games']} games)")
    history.set_state(db, "le:last_sync", now.isoformat(timespec="seconds"))
    return dict(done)


def sync_leaguepedia(db, since=None, until=None, where=None, now=None):
    """Every Leaguepedia scoreboard game since `since`, a week at a time. Needs LEAGUEPEDIA_USER / _BOT_PASSWORD."""
    from . import leaguepedia
    now = now or datetime.now(timezone.utc)
    since = since or (now - timedelta(days=DEFAULT_DAYS)).date()
    until = until or (now + timedelta(days=1)).date()
    site = leaguepedia.Leaguepedia()
    done, failed = Counter(), []
    day = since
    while day < until:
        nxt = min(day + timedelta(days=7), until)
        try:
            rows = site.player_games(day, nxt, where=where)
        except Exception as e:          # noqa: BLE001 - one bad week never stops the sync; it is reported, not hidden
            failed.append(f"{day}..{nxt}: {type(e).__name__}: {str(e)[:120]}")
            _log(f"  Leaguepedia {day} .. {nxt}: FAILED ({type(e).__name__}) - re-run sync for this week")
            day = nxt
            continue
        for game, players in leaguepedia.to_history(rows):
            history.save_game(db, game, players)
            done["games"] += 1
        db.commit()
        _log(f"  Leaguepedia {day} .. {nxt}: {len(rows)} player rows")
        day = nxt
    if not failed:
        history.set_state(db, "lp:last_sync", now.isoformat(timespec="seconds"))
    return dict(done, failed_weeks=failed)


def parse_date(s):
    return date.fromisoformat(s) if s else None
