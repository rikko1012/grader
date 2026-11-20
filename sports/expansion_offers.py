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
               "Attempted Dribbles": "attempted_dribbles", "Shots Assisted": "shots_assisted",
               # sg-stats 2026-11-20 (expansion_stats component models; labels from the PrizePicks pulls of that day)
               "Goal + Assist": "goals_assists", "Outfield Fantasy Score": "fantasy_outfield",
               "Goalie Fantasy Score": "fantasy_goalie"},
    "tennis": {"Aces": "aces", "Double Faults": "double_faults", "Total Games": "games_played",
               "Games Won": "games_won", "Sets Won": "sets_won", "Break Points Won": "breakpoints_won",
               # PrizePicks' own labels (raw board 2026-11-18); same stats the result stores already hold.
               "Total Games Won": "games_won", "Total Sets": "sets_played", "Total Tie Breaks": "tie_breakers_played",
               "1st Set Total Games": "games_played",
               # sg-stats 2026-11-20: set-labelled games (period must agree, below) and the tennis fantasy score.
               "1st Set Total Games Won": "games_won", "2nd Set Total Games": "games_played",
               "Fantasy Score": "fantasy_tennis"},
}
# PrizePicks league per player: soccer board 82 (SOCCER / EPL) and MLS on its own league 6.
PP_LEAGUES = {"soccer": {"SOCCER", "EPL", "MLS"}, "tennis": {"TENNIS"}}
# 2026-11-19 (ppsoccer-1119): PrizePicks moved soccer from one SOCCER tab (league 82) into a tab per league. Exact
# PrizePicks league id -> (its display name in /leagues, our competition code). Ids and names read from PrizePicks'
# /leagues on 2026-11-19; codes are the ESPN slugs the history stores use (expansion_history.SOCCER_LEAGUE_IDS).
# Nothing is fuzzy: an id whose name no longer matches is not fetched, and a soccer tab missing here is only listed
# in ops (PrizePicks' "soccer" icon also marks HANDBALL, so it is never trusted on its own). League 82 is the mixed
# tab (Brazil, A-League, ...): no pin, read exactly as before (prizepicks(..., league=None)).
# Left out on purpose: 1H / 2H child tabs (parent_id set; no first-/second-half history), SOCCERSZN 262 and
# WORLD CUP TRNY 457 (season / tournament-long props, not one match), WORLD CUP 241 (no club competition).
PP_SOCCER_LEAGUES = {82: ("SOCCER", None), 14: ("EPL", "eng.1"), 6: ("MLS", "usa.1"), 536: ("Liga MX", "mex.1"),
                     534: ("Serie A", "ita.1"), 531: ("La Liga", "esp.1"), 533: ("Bundesliga", "ger.1"),
                     535: ("Ligue 1", "fra.1"), 79: ("UCL", "uefa.champions")}
PP_SOCCER_NOT_MATCH = {262, 457}    # season-long / tournament-long soccer tabs: never single-match props
PP_SCOPES = {"Full": "full_game", "1H": "first_half", "2H": "second_half", "1st Set": "set1", "2nd Set": "set2"}
# A set-labelled PrizePicks stat must come with that set's period.
PP_SET_LABELS = {"1st Set Total Games": "set1", "1st Set Total Games Won": "set1", "2nd Set Total Games": "set2"}
# Underdog soccer (sport_id FIFA): full-match stats the ESPN box score holds. Goals + Assists, 1H goals and cards
# stay unmapped (no summed stat / first-half scope / combined-cards house rule registered).
UD_SOCCER_MARKETS = {"period_1_2_goals": "goals", "period_1_2_assists": "assists",
                     "period_1_2_shots_on_target": "shots_on_target", "period_1_2_shots_attempted": "shots",
                     "period_1_2_saves": "saves",
                     # sg-stats 2026-11-20 (expansion_stats): summed / first-half / renamed stats.
                     "period_1_2_goals_assists": "goals_assists", "period_1_2_cards": "cards",
                     "period_1_2_fouls_against": "fouls", "period_1_2_fouls_for": "fouls_suffered"}
UD_SOCCER_FIRST_HALF = {"period_1_goals": "goals"}
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


def _pp_team_names(included, game_rel, side):
    """A game's own team object (home_team_data / away_team_data): its exact name and market (full) names."""
    ref = ((game_rel.get(f"{side}_team_data") or {}).get("data") or {})
    team = included.get((ref.get("type"), str(ref.get("id")))) or {}
    names = []
    for k in ("name", "market"):
        v = team.get(k)
        if isinstance(v, str) and v.strip() and v not in names:
            names.append(v)
    return team.get("abbreviation"), names


def prizepicks(body, sport, observed_at, league=None):
    """league=None: the original reader (tennis, and soccer's mixed tab 82), byte for byte. league=<PrizePicks soccer
    league id in PP_SOCCER_LEAGUES>: keep only that tab's players by numeric league_id (never by name), and add
    pp_league / competition_code (the exact competition pin) / team_aliases (the game's own full team names, exact
    equality only). Those keys exist only on per-league rows, so tab-82 rows and their revisions never change."""
    now = timestamp(observed_at)
    if now is None or sport not in PP_MARKETS:
        raise ValueError("sport and observation time required")
    if league is not None and (sport != "soccer" or league not in PP_SOCCER_LEAGUES or league == 82):
        raise ValueError("unknown PrizePicks soccer league")
    included = {(x.get("type"), str(x.get("id"))): x.get("attributes") or {} for x in body.get("included") or []}
    games = {str(x.get("id")): x.get("relationships") or {} for x in body.get("included") or [] if x.get("type") == "game"}
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
        if league is None:
            actual = str(player.get("league") or "").upper()
            if actual not in PP_LEAGUES[sport]:
                continue
        else:
            own = ((rel.get("league") or {}).get("data") or {}).get("id")
            if str(player.get("league_id")) != str(league) or (own is not None and str(own) != str(league)):
                continue
        stat = PP_MARKETS[sport].get(a.get("stat_type"))
        duration = item("duration").get("name")
        scope = PP_SCOPES.get(duration)
        if a.get("stat_type") in PP_SET_LABELS and scope != PP_SET_LABELS[a.get("stat_type")]:
            continue                                   # the label and the period must agree
        if scope in ("set1", "set2") and sport != "tennis":
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
        if league is not None:
            name, code = PP_SOCCER_LEAGUES[league]
            grel = games.get(str(rid("game"))) or {}
            (a_abbr, a_names), (h_abbr, h_names) = _pp_team_names(included, grel, "away"), _pp_team_names(included, grel, "home")
            # The full names belong to the same two teams only when the team objects carry the same short labels.
            aliases = [a_names, h_names] if (a_abbr, h_abbr) == (away, home) else [[], []]
            extra = {"pp_league": name, "competition_code": code, "team_aliases": aliases}
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
        stat = UD_SOCCER_MARKETS.get(st.get("stat")) or UD_SOCCER_FIRST_HALF.get(st.get("stat"))
        ud_scope = "first_half" if st.get("stat") in UD_SOCCER_FIRST_HALF else "full_game"
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
            "stat": stat, "label": st.get("display_stat") or stat, "scope": ud_scope, "line": value,
            "variant": line.get("line_type") or "unknown", "start": start, "sides": sides,
            "observed_at": now, "source_updated_at": min(updates) if len(updates) == len(sides) else None,
            "identity_status": "provider_only", "forecast": None}))
    return rows
