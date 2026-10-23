"""Pick'em lines for NBA / NFL / WNBA / college: PrizePicks and Underdog.

PrizePicks - partner-api.prizepicks.com/projections?league_id=<pp> (the partner API the CS2 and LoL readers use; the app
             host api.prizepicks.com shows a CAPTCHA and is never used). Kept: Single Stat, Full game, pre-game,
             non-promotional, one player (combo players and "(Combo)" stats left out). odds_type standard / goblin /
             demon; allowed_wager_types "over" = More only, "under_or_over" or missing = both sides.
Underdog   - api.underdogfantasy.com/v1/over_under_lines?sport_id=<ud> (public). Each side's payout_multiplier is the
             documented individual-pick payout modifier (help article 13780101), never a probability. A side not listed
             is NOT OFFERED (not a 50/50).

Every offer: {platform, league, offer_id, player, player_key, team, game, start, market, stat, line, odds_type, sides}
where sides = {"over": {"offered", "multiplier"}, "under": {...}}. A book's line only sets the threshold a chance is
read at; it never changes a chance. load() also appends what changed (new / moved / removed) to lines.jsonl, so every
forecast can later be checked against a line that was really offered.
"""
import json
from datetime import datetime, timezone

from . import cache, config, markets, names, net

PP_URL = "https://partner-api.prizepicks.com/projections"
PP_HEADERS = {"Origin": "https://app.prizepicks.com", "Referer": "https://app.prizepicks.com/",
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/140.0.0.0 Safari/537.36"}
UD_URL = "https://api.underdogfantasy.com/v1/over_under_lines"
UD_HEADERS = {"Origin": "https://underdogfantasy.com", "Referer": "https://underdogfantasy.com/",
              "User-Agent": PP_HEADERS["User-Agent"]}
REFRESH_S = 3 * 60


def utc_iso(s):
    """Any ISO time with an offset -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC); None when unreadable."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pp_parse(body, lg):
    fam = lg["family"]
    inc = {(i["type"], i["id"]): i.get("attributes") or {} for i in body.get("included") or []}
    out = []
    for p in body.get("data") or []:
        a, rel = p.get("attributes") or {}, p.get("relationships") or {}
        rid = lambda k: ((rel.get(k) or {}).get("data") or {}).get("id")
        stat = a.get("stat_type")
        mk = markets.PP.get(fam, {}).get(stat)
        ptype = inc.get(("projection_type", rid("projection_type")), {}).get("name")
        dur = inc.get(("duration", rid("duration")), {}).get("name")
        player = inc.get(("new_player", rid("new_player")), {})
        if (not mk or ptype not in (None, "Single Stat") or dur not in (None, "Full") or a.get("is_promo")
                or a.get("flash_sale_line_score") is not None or a.get("in_game") or a.get("is_live")
                or a.get("status") not in (None, "pre_game") or player.get("combo") or a.get("line_score") is None):
            continue
        game = inc.get(("game", rid("game")), {})
        teams = ((game.get("metadata") or {}).get("game_info") or {}).get("teams") or {}
        away, home = (teams.get("away") or {}).get("abbreviation"), (teams.get("home") or {}).get("abbreviation")
        wager = a.get("allowed_wager_types")
        name = player.get("display_name") or player.get("name")
        out.append({"platform": "prizepicks", "league": lg["key"], "offer_id": f"pp:{p.get('id')}", "player": name,
                    "player_key": names.key(name), "team": player.get("team"), "position": player.get("position"),
                    "game": f"{away} @ {home}" if away and home else a.get("description"),
                    "start": utc_iso(a.get("start_time") or game.get("start_time")), "market": mk, "stat": stat,
                    "line": float(a["line_score"]), "odds_type": a.get("odds_type") or "standard",
                    "sides": {"over": {"offered": True, "multiplier": None},
                              "under": {"offered": wager in (None, "under_or_over"), "multiplier": None}},
                    "updated_at": utc_iso(a.get("updated_at"))})
    return out


def ud_parse(body, lg):
    fam = lg["family"]
    apps = {x["id"]: x for x in body.get("appearances") or []}
    players = {x["id"]: x for x in body.get("players") or []}
    games = {x["id"]: x for x in (body.get("games") or []) + (body.get("solo_games") or [])}
    out = []
    for ln in body.get("over_under_lines") or []:
        ou = ln.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        mk = markets.UD.get(fam, {}).get(st.get("stat"))
        try:
            value = float(ln.get("stat_value"))
        except (TypeError, ValueError):
            continue                                   # one unreadable line never drops the whole board
        if not mk or ln.get("status") != "active" or ln.get("live_event") or ou.get("category") != "player_prop":
            continue
        app = apps.get(st.get("appearance_id")) or {}
        pl = players.get(app.get("player_id")) or {}
        gm = games.get(app.get("match_id")) or {}
        sides = {"over": {"offered": False, "multiplier": None}, "under": {"offered": False, "multiplier": None}}
        for o in ln.get("options") or []:
            side = {"higher": "over", "lower": "under"}.get(o.get("choice"))
            if side and o.get("status") in (None, "active"):
                try:
                    mult = float(o.get("payout_multiplier"))
                except (TypeError, ValueError):
                    mult = None
                sides[side] = {"offered": True, "multiplier": mult}
        if not (sides["over"]["offered"] or sides["under"]["offered"]):
            continue
        title = gm.get("abbreviated_title") or gm.get("title") or ""
        away, _, home = title.partition(" @ ")
        team = home if app.get("team_id") and app.get("team_id") == gm.get("home_team_id") else (
            away if app.get("team_id") and app.get("team_id") == gm.get("away_team_id") else None)
        name = " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x)
        out.append({"platform": "underdog", "league": lg["key"], "offer_id": f"ud:{ln.get('id')}", "player": name,
                    "player_key": names.key(name), "team": team or None, "position": pl.get("position_name"),
                    "game": title or None, "start": utc_iso(gm.get("scheduled_at")), "market": mk,
                    "stat": st.get("stat"), "line": value, "odds_type": ln.get("line_type"),
                    "sides": sides, "updated_at": None})
    return out


def _fetch_pp(lg):
    return net.get_json(PP_URL, params={"league_id": lg["pp"], "per_page": 1000, "single_stat": "true",
                                        "game_mode": "pickem"}, headers=PP_HEADERS, timeout=60)


def _fetch_ud(lg):
    return net.get_json(UD_URL, params={"sport_id": lg["ud"]}, headers=UD_HEADERS, timeout=60)


def load(lg, refresh=False, archive=True):
    """-> (offers, status) for one league from both platforms. A platform that fails is reported in status, never fatal."""
    offers, status = [], {}
    for plat, fetch, parse in (("prizepicks", _fetch_pp, pp_parse), ("underdog", _fetch_ud, ud_parse)):
        try:
            body = cache.fetch(f"lines:{plat}", lg["key"], REFRESH_S, lambda: fetch(lg), refresh=refresh)
            rows = parse(body, lg)
            offers += rows
            status[plat] = {"ok": True, "offers": len(rows)}
        except (net.HttpError, ValueError, KeyError, TypeError) as e:
            status[plat] = {"ok": False, "error": str(e)[:200]}
    if archive:
        archive_changes(lg["key"], offers, status)
    return offers, status


def _state_file(league):
    return config.DATA_DIR / f"lines_state_{league}.json"


def archive_changes(league, offers, status, now=None):
    """Append new / moved / removed offers to lines.jsonl. Removal is only recorded for a platform read successfully."""
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _state_file(league)
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    cur = {o["offer_id"]: {"line": o["line"], "sides": o["sides"], "odds_type": o["odds_type"]} for o in offers}
    events = []
    for oid, o in ((x["offer_id"], x) for x in offers):
        p = prev.get(oid)
        if p is None or p["line"] != o["line"] or p["sides"] != o["sides"]:
            events.append({"at": now, "event": "new" if p is None else "moved", "league": league, **o})
    ok = {plat for plat, s in status.items() if s.get("ok")}
    pref = {"pp": "prizepicks", "ud": "underdog"}
    for oid in prev:
        if oid not in cur and pref.get(oid.split(":", 1)[0]) in ok:
            events.append({"at": now, "event": "removed", "league": league, "offer_id": oid})
    keep = {oid: v for oid, v in prev.items() if pref.get(oid.split(":", 1)[0]) not in ok}
    keep.update(cur)
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        if events:
            with open(config.LINES_ARCHIVE, "a", encoding="utf-8") as fh:
                for e in events:
                    fh.write(json.dumps(e, separators=(",", ":")) + "\n")
        path.write_text(json.dumps(keep), encoding="utf-8")
    except OSError:
        pass
    return events
