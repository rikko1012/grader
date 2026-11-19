"""Source-native soccer/tennis offers. No cross-sport resolver or grading fallback.

All timestamps are observation times; provider timestamps are retained separately.
These records do not become canonical forecast inputs until event/player identity
and settlement scope have been verified against their own sport's result store.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from .contracts import utc

PP_MARKETS = {
    "soccer": {"Shots": "shots", "Shots On Target": "shots_on_target", "Passes Attempted": "passes_attempted",
               "Tackles": "tackles", "Goalie Saves": "saves", "Goals": "goals", "Assists": "assists",
               "Crosses": "crosses", "Clearances": "clearances", "Fouls": "fouls",
               "Attempted Dribbles": "attempted_dribbles", "Shots Assisted": "shots_assisted"},
    "tennis": {"Aces": "aces", "Double Faults": "double_faults", "Total Games": "games_played",
               "Games Won": "games_won", "Sets Won": "sets_won", "Break Points Won": "breakpoints_won",
               # PrizePicks' own labels (raw board 2026-11-18); same stats the result stores already hold.
               "Total Games Won": "games_won", "Total Sets": "sets_played", "Total Tie Breaks": "tie_breakers_played",
               "1st Set Total Games": "games_played"},
}
# PrizePicks league per player: soccer board 82 (SOCCER / EPL) and MLS on its own league 6.
PP_LEAGUES = {"soccer": {"SOCCER", "EPL", "MLS"}, "tennis": {"TENNIS"}}
PP_SCOPES = {"Full": "full_game", "1H": "first_half", "2H": "second_half", "1st Set": "set1"}
# Underdog soccer (sport_id FIFA): full-match stats the ESPN box score holds. Goals + Assists, 1H goals and cards
# stay unmapped (no summed stat / first-half scope / combined-cards house rule registered).
UD_SOCCER_MARKETS = {"period_1_2_goals": "goals", "period_1_2_assists": "assists",
                     "period_1_2_shots_on_target": "shots_on_target", "period_1_2_shots_attempted": "shots",
                     "period_1_2_saves": "saves"}
UD_MARKETS = {s: (s, "full_game") for s in (
    "aces", "double_faults", "breakpoints_won", "games_played", "games_won", "sets_played", "sets_won", "tie_breakers_played")}
UD_MARKETS.update({"period_1_games_played": ("games_played", "set1"), "period_1_games_won": ("games_won", "set1")})


def number(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def timestamp(v):
    try:
        return utc(v)
    except ValueError:
        return None


def finish(row):
    # Immutable quote revision: collection time never changes price identity.
    content = {k:v for k,v in row.items() if k not in ("observed_at", "source_updated_at")}
    row["revision"] = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return row


def prizepicks(body, sport, observed_at):
    now = timestamp(observed_at)
    if now is None or sport not in PP_MARKETS:
        raise ValueError("sport and observation time required")
    included = {(x.get("type"), str(x.get("id"))): x.get("attributes") or {} for x in body.get("included") or []}
    rows = []
    for p in body.get("data") or []:
        a, rel = p.get("attributes") or {}, p.get("relationships") or {}
        def item(key):
            r = (rel.get(key) or {}).get("data") or {}
            return included.get((r.get("type"), str(r.get("id"))), {})
        def rid(key):
            return ((rel.get(key) or {}).get("data") or {}).get("id")
        player, game = item("new_player"), item("game")
        if not (player.get("display_name") or player.get("name")):
            continue
        actual = str(player.get("league") or "").upper()
        if actual not in PP_LEAGUES[sport]:
            continue
        stat = PP_MARKETS[sport].get(a.get("stat_type"))
        duration = item("duration").get("name")
        scope = PP_SCOPES.get(duration)
        if a.get("stat_type") == "1st Set Total Games" and scope != "set1":
            continue                                   # the label and the period must agree
        if scope == "set1" and sport != "tennis":
            continue
        line, start = number(a.get("line_score")), timestamp(a.get("start_time"))
        if (not stat or not scope or line is None or line < 0 or not start or start <= now
                or not p.get("id") or not rid("game") or not rid("new_player") or player.get("combo")
                or a.get("is_live") or a.get("in_game") or a.get("is_promo")
                or a.get("flash_sale_line_score") is not None or a.get("status") != "pre_game"
                or a.get("projection_type") != "Single Stat"):
            continue
        allowed = a.get("allowed_wager_types")
        sides = {"over": ["over"], "under": ["under"], "under_or_over": ["over", "under"]}.get(allowed)
        if allowed is None and a.get("odds_type") == "standard":
            sides = ["over", "under"]                  # sports/lines.py rule: missing = both sides (standard lines)
        if not sides:
            continue                                   # demon / goblin without the field: side unknown, left out
        teams = ((game.get("metadata") or {}).get("game_info") or {}).get("teams") or {}
        away, home = (teams.get("away") or {}).get("abbreviation"), (teams.get("home") or {}).get("abbreviation")
        extra = {}
        if sport == "tennis":
            # PrizePicks tennis "teams" are the two players' full names; some games only carry the opponent in the
            # projection description. Both must agree when both exist; doubles pairs ("A / B") never resolve.
            me = player.get("display_name") or player.get("name")
            pair = [x for x in (away, home) if x]
            by_game = next((x for x in pair if x != me), None) if len(pair) == 2 and me in pair else None
            by_text = a.get("description") if a.get("description") and a.get("description") != me else None
            opponent = by_game or by_text
            if (by_game and by_text and by_game != by_text) or not opponent or "/" in opponent or "/" in str(me):
                opponent = None
            extra = {"opponent": opponent, "tour": None}
        rows.append(finish({**extra, "sport": sport, "platform": "prizepicks", "offer_id": str(p["id"]),
            "event_id": f"{sport}:prizepicks:{rid('game')}", "player_id": f"{sport}:prizepicks:{rid('new_player')}",
            "player": player.get("display_name") or player.get("name"), "role": player.get("position"),
            "team": player.get("team"), "match": f"{away} · {home}" if away and home else a.get("description"),
            "teams": [away, home] if away and home else [],
            "stat": stat, "label": a.get("stat_type"), "scope": scope, "line": line,
            "variant": a.get("odds_type") or "unknown", "start": start, "sides": {s: {"multiplier": None} for s in sides},
            "observed_at": now, "source_updated_at": timestamp(a.get("updated_at")),
            "identity_status": "provider_only", "forecast": None}))
    return rows


def underdog(body, observed_at):
    now = timestamp(observed_at)
    if not now:
        raise ValueError("observation time required")
    players = {x["id"]: x for x in body.get("players") or []}
    apps = {x["id"]: x for x in body.get("appearances") or []}
    games = {x["id"]: x for x in body.get("solo_games") or []}
    rows = []
    for line in body.get("over_under_lines") or []:
        ou = line.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        app = apps.get(st.get("appearance_id")) or {}
        p = players.get(app.get("player_id")) or {}
        game = games.get(app.get("match_id")) or {}
        if "doubles" in str(game.get("sport_group_name") or "").lower() or not p.get("last_name"):
            continue
        market = UD_MARKETS.get(st.get("stat"))
        value, start = number(line.get("stat_value")), timestamp(game.get("scheduled_at"))
        participants = {game.get("home_player_id"), game.get("away_player_id")}
        if (p.get("sport_id") != "TENNIS" or p.get("id") not in participants or None in participants or len(participants) != 2
                or not market or value is None or value < 0 or not start or start <= now
                or line.get("status") != "active" or line.get("live_event") or game.get("status") != "scheduled"
                or ou.get("category") != "player_prop" or not line.get("id")):
            continue
        sides = {}
        updates = []
        for option in line.get("options") or []:
            side = {"higher": "over", "lower": "under"}.get(option.get("choice"))
            if side and option.get("status") == "active":
                sides[side] = {"multiplier": number(option.get("payout_multiplier"))}
                update = timestamp(option.get("updated_at"))
                if update:
                    updates.append(update)
        if not sides:
            continue
        other = next(x for x in participants if x != p['id'])
        opponent = players.get(other) or {}
        rival = ' '.join(x for x in (opponent.get('first_name'), opponent.get('last_name')) if x)
        if not rival:
            # An opponent with no line of their own is absent from `players` (2 of 37 singles games on 2026-11-18);
            # the game still names both sides, with an optional seed prefix ("(2) Kyrian Jacquet"). Empty: unlinked.
            side = 'home' if game.get('home_player_id') == other else 'away'
            rival = re.sub(r'^\(\d+\)\s*', '', str(game.get(f'{side}_player_name') or '').strip())
        group = str(game.get('sport_group_name') or '')
        tour = 'wta' if group.startswith('WTA ') else 'atp' if group.startswith('ATP ') else None
        rows.append(finish({"sport": "tennis", "platform": "underdog", "offer_id": str(line["id"]),
            "event_id": f"tennis:underdog:{game['id']}", "player_id": f"tennis:underdog:{p['id']}",
            "player": " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
            "role": None, "team": None, "match": game.get("title"), "competition": game.get("sport_group_name"),
            "tour": tour, "opponent": rival,
            "stat": market[0], "label": st.get("display_stat") or market[0], "scope": market[1], "line": value,
            "variant": line.get("line_type") or "unknown", "start": start, "sides": sides,
            "observed_at": now, "source_updated_at": min(updates) if len(updates) == len(sides) else None,
            "identity_status": "provider_only", "forecast": None}))
    return rows


def underdog_soccer(body, observed_at):
    """Underdog soccer (sport_id FIFA) player lines from body['games']. Only the sides Underdog lists are kept
    (2026-11-18: every line is 'higher' only); no Under is ever invented."""
    now = timestamp(observed_at)
    if not now:
        raise ValueError("observation time required")
    players = {x["id"]: x for x in body.get("players") or []}
    apps = {x["id"]: x for x in body.get("appearances") or []}
    games = {x["id"]: x for x in body.get("games") or []}
    rows = []
    for line in body.get("over_under_lines") or []:
        ou = line.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        app = apps.get(st.get("appearance_id")) or {}
        p = players.get(app.get("player_id")) or {}
        game = games.get(app.get("match_id")) or {}
        stat = UD_SOCCER_MARKETS.get(st.get("stat"))
        value, start = number(line.get("stat_value")), timestamp(game.get("scheduled_at"))
        home, away = game.get("home_team_id"), game.get("away_team_id")
        full = str(game.get("full_team_names_title") or "").split(" vs ")
        short = str(game.get("short_title") or "").split(" vs ")
        abbr = str(game.get("abbreviated_title") or "").split(" vs ")
        team = app.get("team_id") or p.get("team_id")
        if (p.get("sport_id") != "FIFA" or game.get("sport_id") != "FIFA" or not stat or value is None or value < 0
                or not start or start <= now or line.get("status") != "active" or line.get("live_event")
                or game.get("status") != "scheduled" or ou.get("category") != "player_prop" or not line.get("id")
                or not home or not away or home == away or team not in (home, away) or len(full) != 2
                or not all(x.strip() for x in full) or not p.get("last_name")):
            continue
        sides, updates = {}, []
        for option in line.get("options") or []:
            side = {"higher": "over", "lower": "under"}.get(option.get("choice"))
            if side and option.get("status") == "active":
                sides[side] = {"multiplier": number(option.get("payout_multiplier"))}
                update = timestamp(option.get("updated_at"))
                if update:
                    updates.append(update)
        if not sides:
            continue
        # "X vs Y" titles list the home team first (abbreviated_title BAY vs KOE: Bayern home_team_id).
        aliases = [[x for x in (full[i], short[i] if len(short) == 2 else None, abbr[i] if len(abbr) == 2 else None)
                    if x] for i in range(2)]
        mine = 0 if team == home else 1
        rows.append(finish({"sport": "soccer", "platform": "underdog", "offer_id": str(line["id"]),
            "event_id": f"soccer:underdog:{game['id']}", "player_id": f"soccer:underdog:{p['id']}",
            "player": " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
            "role": p.get("position_display_name"), "team": full[mine], "match": game.get("title"),
            "competition": game.get("sport_group_name"), "teams": [full[0], full[1]], "team_aliases": aliases,
            "stat": stat, "label": st.get("display_stat") or stat, "scope": "full_game", "line": value,
            "variant": line.get("line_type") or "unknown", "start": start, "sides": sides,
            "observed_at": now, "source_updated_at": min(updates) if len(updates) == len(sides) else None,
            "identity_status": "provider_only", "forecast": None}))
    return rows
