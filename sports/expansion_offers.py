"""Source-native soccer/tennis offers. No cross-sport resolver or grading fallback.

All timestamps are observation times; provider timestamps are retained separately.
These records do not become canonical forecast inputs until event/player identity
and settlement scope have been verified against their own sport's result store.
"""
from datetime import datetime, timezone
import hashlib
import json
import math

from .contracts import utc

PP_MARKETS = {
    "soccer": {"Shots": "shots", "Shots On Target": "shots_on_target", "Passes Attempted": "passes_attempted",
               "Tackles": "tackles", "Goalie Saves": "saves", "Goals": "goals", "Assists": "assists",
               "Crosses": "crosses", "Clearances": "clearances", "Fouls": "fouls",
               "Attempted Dribbles": "attempted_dribbles", "Shots Assisted": "shots_assisted"},
    "tennis": {"Aces": "aces", "Double Faults": "double_faults", "Total Games": "games_played",
               "Games Won": "games_won", "Sets Won": "sets_won", "Break Points Won": "breakpoints_won"},
}
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
        if actual not in ({"SOCCER", "EPL"} if sport == "soccer" else {"TENNIS"}):
            continue
        stat = PP_MARKETS[sport].get(a.get("stat_type"))
        duration = item("duration").get("name")
        scope = {"Full": "full_game", "1H": "first_half", "2H": "second_half"}.get(duration)
        line, start = number(a.get("line_score")), timestamp(a.get("start_time"))
        if (not stat or not scope or line is None or line < 0 or not start or start <= now
                or not p.get("id") or not rid("game") or not rid("new_player") or player.get("combo")
                or a.get("is_live") or a.get("in_game") or a.get("is_promo")
                or a.get("flash_sale_line_score") is not None or a.get("status") != "pre_game"
                or a.get("projection_type") != "Single Stat"):
            continue
        allowed = a.get("allowed_wager_types")
        sides = {"over": ["over"], "under": ["under"], "under_or_over": ["over", "under"]}.get(allowed)
        if not sides:
            continue
        teams = ((game.get("metadata") or {}).get("game_info") or {}).get("teams") or {}
        away, home = (teams.get("away") or {}).get("abbreviation"), (teams.get("home") or {}).get("abbreviation")
        rows.append(finish({"sport": sport, "platform": "prizepicks", "offer_id": str(p["id"]),
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
        opponent = players.get(next(x for x in participants if x != p['id'])) or {}
        group = str(game.get('sport_group_name') or '')
        tour = 'wta' if group.startswith('WTA ') else 'atp' if group.startswith('ATP ') else None
        rows.append(finish({"sport": "tennis", "platform": "underdog", "offer_id": str(line["id"]),
            "event_id": f"tennis:underdog:{game['id']}", "player_id": f"tennis:underdog:{p['id']}",
            "player": " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
            "role": None, "team": None, "match": game.get("title"), "competition": game.get("sport_group_name"),
            "tour": tour, "opponent": ' '.join(x for x in (opponent.get('first_name'), opponent.get('last_name')) if x),
            "stat": market[0], "label": st.get("display_stat") or market[0], "scope": market[1], "line": value,
            "variant": line.get("line_type") or "unknown", "start": start, "sides": sides,
            "observed_at": now, "source_updated_at": min(updates) if len(updates) == len(sides) else None,
            "identity_status": "provider_only", "forecast": None}))
    return rows
