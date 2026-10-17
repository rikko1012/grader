"""
LoL pick'em offers from Underdog and PrizePicks -> an append-only, timestamped quote archive.

Inventory verified 2026-10-16/17 (one read-only request per feed):
- Underdog  api.underdogfantasy.com/v1/over_under_lines?sport_id=LOL: "Kills on Maps 1+2+3" (kills_on_maps_1_2_3),
  "Assists on Maps 1+2+3" (assists_on_maps_1_2_3), "Fantasy Points on Maps 1+2+3" (period_1_2_3_fantasy_points);
  line_type "balanced"; each line lists the sides offered (higher and/or lower - some lines offer one side only) with
  payout multipliers and prices.
- PrizePicks partner-api.prizepicks.com league 121 ("LoL"): "MAPS 1-3 Kills" (standard / goblin / demon) and
  "MAPS 1-3 Kills (Combo)" (two or three players combined; archived, never graded until explicitly mapped).

Every poll is recorded (market_polls: time, ok, HTTP status, count, error). An offer seen for the first time is an
"open" event; any change of line, sides, payouts, status, time or ids is a "change" event with the full offer as then
shown; an offer missing from a successful poll is a "removed" event (two empty polls in a row are needed before a
whole board is called removed); one that returns is "reopen". Events are never updated or deleted; market_state is
only the cache used to detect changes. The raw response is kept (compressed) for every poll that produced an event.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import store
from .names import role

UD_URL = "https://api.underdogfantasy.com/v1/over_under_lines?sport_id=LOL"
PP_URL = "https://partner-api.prizepicks.com/projections?league_id=121&per_page=1000&single_stat=true&game_mode=pickem"
_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
HEADERS = {"underdog": {"Origin": "https://underdogfantasy.com", "Referer": "https://underdogfantasy.com/", "User-Agent": _BROWSER,
                        "Accept": "application/json"},
           "prizepicks": {"Origin": "https://app.prizepicks.com", "Referer": "https://app.prizepicks.com/", "User-Agent": _BROWSER,
                          "Accept": "application/json"}}
URLS = {"underdog": UD_URL, "prizepicks": PP_URL}
EVERY_S = int(os.environ.get("LOL_ARCHIVE_EVERY", "120"))
RAW_SAMPLE_S = 6 * 3600                  # a raw copy at least this often even when nothing changed
STATS = {"kills": "kills", "kill": "kills", "assists": "assists", "assist": "assists", "deaths": "deaths",
         "fantasy_points": "fantasy_points", "fantasy_score": "fantasy_points", "fantasy": "fantasy_points"}


def _scope(nums):
    nums = [int(x) for x in nums]
    return "maps_" + "_".join(str(n) for n in nums) if nums else None


def ud_stat(code, label):
    """'kills_on_maps_1_2_3' -> ('kills', 'maps_1_2_3'); 'period_1_2_3_fantasy_points' -> ('fantasy_points', 'maps_1_2_3').
    Anything else -> (code, None): archived, flagged unmapped, never graded."""
    c = str(code or "").lower()
    m = re.search(r"(?:maps?|period|game)s?_((?:\d_?)+)", c)
    nums = re.findall(r"\d", m.group(1)) if m else []
    word = re.sub(r"(?:_on)?_?(?:maps?|period|games?)s?_(?:\d_?)+", "_", c).strip("_")
    stat = STATS.get(word)
    return (stat, _scope(nums)) if stat and nums else (c or str(label or ""), None)


def pp_stat(stat_type):
    """'MAPS 1-3 Kills' -> ('kills', 'maps_1_2_3', False); '... (Combo)' -> combo True."""
    s = str(stat_type or "").strip()
    combo = bool(re.search(r"\(combo\)\s*$", s, re.I))
    s = re.sub(r"\s*\(combo\)\s*$", "", s, flags=re.I)
    m = re.match(r"^maps?\s*(\d)(?:\s*[-+]\s*(\d))?\s+(.+)$", s, re.I)
    if not m:
        return (s.lower(), None, combo)
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    stat = STATS.get(re.sub(r"[^a-z]+", "_", m.group(3).lower()).strip("_"))
    return (stat or m.group(3).lower(), _scope(range(a, b + 1)) if stat else None, combo)


def _split(title, sep=" @ "):
    parts = str(title or "").split(sep)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (None, None)


def _pair(title):
    """'A vs B' / 'A vs. B' -> (A, B) with NO side meaning (which is home is not stated) - used only as the two names."""
    import re as _re
    parts = _re.split(r"\s+vs\.?\s+", str(title or ""), maxsplit=1)
    return [p.strip() for p in parts] if len(parts) == 2 else None


def parse_underdog(body):
    apps = {a.get("id"): a for a in body.get("appearances") or []}
    players = {p.get("id"): p for p in body.get("players") or []}
    games = {g.get("id"): g for g in (body.get("games") or []) + (body.get("solo_games") or [])}
    out = []
    for ln in body.get("over_under_lines") or []:
        ou = ln.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        app = apps.get(st.get("appearance_id")) or {}
        pl = players.get(app.get("player_id")) or {}
        g = games.get(app.get("match_id")) or {}
        # "Away @ Home" says which id is which name; "A vs B" does not (2026-10-17: "MVK Esports vs FURIA" with a
        # placeholder short title "TBD-2 vs TBD-1") - then team / opponent stay unknown and only the two names are kept
        away_n, home_n = _split(g.get("full_team_names_title"))
        away_a, home_a = _split(g.get("short_title"))
        if away_a and "TBD" in (away_a + (home_a or "")).upper():
            away_a = home_a = None
        home = bool(away_n) and app.get("team_id") and app.get("team_id") == g.get("home_team_id")
        away = bool(away_n) and app.get("team_id") and app.get("team_id") == g.get("away_team_id")
        stat, scope = ud_stat(st.get("stat"), st.get("display_stat"))
        sides = {}
        for o in ln.get("options") or []:
            fo = ((o.get("odds") or {}).get("fantasy")) or {}
            sides[str(o.get("choice") or "")] = {
                "status": o.get("status"), "payout_multiplier": o.get("payout_multiplier"),
                "american_price": o.get("american_price"), "decimal_price": o.get("decimal_price"),
                "fantasy_american": fo.get("american"), "fantasy_decimal": fo.get("decimal"),
                "fantasy_probability": fo.get("probability"), "option_id": o.get("id")}
        try:
            line = float(ln.get("stat_value"))
        except (TypeError, ValueError):
            line = None
        out.append(dict(
            market_key=f"ud:{ln.get('over_under_id') or ou.get('id')}|{ln.get('line_type') or ''}", line_id=ln.get("id"),
            player=" ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x).strip() or None,
            player_source_id=app.get("player_id"), team=home_n if home else (away_n if away else None),
            opponent=away_n if home else (home_n if away else None), match_source_id=str(app.get("match_id") or ""),
            match_title=g.get("title"), start_time=g.get("scheduled_at"), stat_label=st.get("display_stat"), stat=stat,
            scope=scope, market_type="single", odds_type=ln.get("line_type"), line=line, sides=sides, status=ln.get("status"),
            source_updated_at=ln.get("updated_at"),
            extra={"team_abbr": home_a if home else (away_a if away else None), "opponent_abbr": away_a if home else (home_a if away else None),
                   "team_source_id": app.get("team_id"), "stat_code": st.get("stat"), "graded_by": st.get("graded_by"),
                   "live_event": ln.get("live_event"), "game_status": g.get("status"), "abbreviated_title": g.get("abbreviated_title"),
                   "full_team_names_title": g.get("full_team_names_title"), "position": pl.get("position_name"),
                   "has_alternates": ou.get("has_alternates"), "title": (ou.get("title") or "").strip(),
                   "matchup_names": None if (home or away) and away_n else (_pair(g.get("full_team_names_title")) or _pair(g.get("title")))}))
    return out


def pp_sides(wagers, odds_type, adjusted):
    """Sides the PrizePicks feed offers (same reading as the CS2 prizepicks.directions): allowed_wager_types when sent;
    a standard / promo line without it -> both; a goblin / demon (adjusted odds) line without it -> More offered, Less
    not confirmed."""
    w = {str(x).lower() for x in (wagers or [])}
    if w:
        return {"higher": {"offered": "over" in w}, "lower": {"offered": "under" in w}, "basis": "allowed_wager_types"}
    if odds_type in ("goblin", "demon") or adjusted:
        return {"higher": {"offered": True}, "lower": {"offered": None}, "basis": "adjusted-odds line, no allowed sides sent"}
    return {"higher": {"offered": True}, "lower": {"offered": True}, "basis": "standard line, no restriction sent"}


def parse_prizepicks(body):
    inc = {(r.get("type"), str(r.get("id"))): r for r in body.get("included") or []}

    def linked(res, rel):
        ref = ((res.get("relationships") or {}).get(rel) or {}).get("data")
        return inc.get((ref.get("type"), str(ref.get("id"))), {}) if isinstance(ref, dict) else {}

    out = []
    for p in body.get("data") or []:
        a = p.get("attributes") or {}
        pres = linked(p, "new_player")
        pl = pres.get("attributes") or {}
        game = linked(p, "game")
        ga = game.get("attributes") or {}
        stat, scope, combo = pp_stat(a.get("stat_type") or a.get("stat_display_name"))
        combo = combo or bool(pl.get("combo")) or a.get("event_type") == "combo"
        ot = str(a.get("odds_type") or "standard").lower()
        if a.get("is_promo") and ot == "standard":
            ot = "promo"
        wagers = a.get("allowed_wager_types")
        wagers = [wagers] if isinstance(wagers, str) else list(wagers or [])
        dur = (linked(p, "duration").get("attributes") or {}).get("name")
        opp = re.sub(r"\s+maps?\s*\d+(?:\s*[-+]\s*\d+)*\s*$", "", str(a.get("description") or ""), flags=re.I).strip() or None
        try:
            line = float(a.get("line_score"))
        except (TypeError, ValueError):
            line = None
        name = pl.get("display_name") or pl.get("name")
        out.append(dict(
            market_key=f"pp:{pres.get('id')}|{a.get('stat_type')}|{ot}|{game.get('id') or a.get('game_id')}", line_id=str(p.get("id")),
            player=name, player_source_id=str(pres.get("id") or ""), team=pl.get("team") or pl.get("team_name"), opponent=opp,
            match_source_id=str(game.get("id") or a.get("game_id") or ""), match_title=None, start_time=a.get("start_time") or ga.get("start_time"),
            stat_label=a.get("stat_type") or a.get("stat_display_name"), stat=stat, scope=scope,
            market_type="combo" if combo else "single", odds_type=ot, line=line,
            sides=pp_sides(wagers, ot, bool(a.get("adjusted_odds"))), status=a.get("status"), source_updated_at=a.get("updated_at"),
            extra={"position": pl.get("position"), "role": role(pl.get("position")), "combo_players": [x.strip() for x in str(name or "").split("+")] if combo else None,
                   "external_game_id": ga.get("external_game_id") or a.get("game_id"), "game_status": ga.get("status"),
                   "board_time": a.get("board_time"), "duration": dur, "description": a.get("description"),
                   "is_promo": a.get("is_promo"), "flash_sale_line_score": a.get("flash_sale_line_score"),
                   "adjusted_odds": a.get("adjusted_odds"), "refundable": a.get("refundable"), "is_live": a.get("is_live"),
                   "allowed_wager_types": wagers or None, "team_source_id": ((pres.get("relationships") or {}).get("team_data") or {}).get("data", {}).get("id") if isinstance(((pres.get("relationships") or {}).get("team_data") or {}).get("data"), dict) else None}))
    return out


PARSERS = {"underdog": parse_underdog, "prizepicks": parse_prizepicks}
_STATE_FIELDS = ("line_id", "player", "player_source_id", "team", "opponent", "match_source_id", "start_time", "stat_label",
                 "stat", "scope", "market_type", "odds_type", "line", "sides", "status")


def state_sha(o):
    ex = o.get("extra") or {}
    return store.sha({**{k: o.get(k) for k in _STATE_FIELDS},
                      "x": {k: ex.get(k) for k in ("is_promo", "flash_sale_line_score", "live_event", "allowed_wager_types", "game_status")}})


def _insert_event(con, platform, o, event, observed_at, poll_id, raw_id, h):
    cur = con.execute(
        "INSERT INTO market_events (sport, platform, market_key, event, observed_at, poll_id, raw_id, source_updated_at, line_id, "
        "player, player_source_id, team, opponent, match_source_id, match_title, start_time, stat_label, stat, scope, market_type, "
        "odds_type, line, sides, status, state_sha, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (store.SPORT, platform, o["market_key"], event, observed_at, poll_id, raw_id, o.get("source_updated_at"), o.get("line_id"),
         o.get("player"), o.get("player_source_id"), o.get("team"), o.get("opponent"), o.get("match_source_id"), o.get("match_title"),
         o.get("start_time"), o.get("stat_label"), o.get("stat"), o.get("scope"), o.get("market_type"), o.get("odds_type"),
         o.get("line"), json.dumps(o.get("sides"), sort_keys=True), o.get("status"), h,
         json.dumps(o, sort_keys=True, default=str, ensure_ascii=False)))
    return cur.lastrowid


def pending(con, platform, offers):
    """How many events this poll would append (no writes): decides whether its raw copy is kept."""
    state = {r["market_key"]: dict(r) for r in con.execute("SELECT * FROM market_state WHERE platform = ?", (platform,))}
    keys = {o["market_key"] for o in offers}
    n = sum(1 for o in {o["market_key"]: o for o in offers}.values()
            if not (state.get(o["market_key"]) and state[o["market_key"]]["open"] and state[o["market_key"]]["state_sha"] == state_sha(o)))
    return n + sum(1 for k, st in state.items() if st["open"] and k not in keys)


def record(con, platform, offers, observed_at, poll_id, raw_id):
    """Append the events for one successful poll. Returns the number of events."""
    state = {r["market_key"]: dict(r) for r in con.execute("SELECT * FROM market_state WHERE platform = ?", (platform,))}
    seen, n = set(), 0
    for o in offers:
        k = o["market_key"]
        if k in seen:
            continue
        seen.add(k)
        h = state_sha(o)
        st = state.get(k)
        if st and st["open"] and st["state_sha"] == h:
            con.execute("UPDATE market_state SET last_seen = ? WHERE platform = ? AND market_key = ?", (observed_at, platform, k))
            continue
        ev = "open" if not st else ("reopen" if not st["open"] else "change")
        eid = _insert_event(con, platform, o, ev, observed_at, poll_id, raw_id, h)
        con.execute("INSERT INTO market_state (platform, market_key, state_sha, open, last_event_id, first_seen, last_seen) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?) ON CONFLICT(platform, market_key) DO UPDATE SET state_sha = excluded.state_sha, "
                    "open = 1, last_event_id = excluded.last_event_id, last_seen = excluded.last_seen",
                    (platform, k, h, eid, observed_at, observed_at))
        n += 1
    open_now = [k for k, st in state.items() if st["open"] and k not in seen]
    empty_key = f"empty_polls:{platform}"
    if not offers and len(open_now) > 3:
        empties = (store.get_meta(con, empty_key, 0) or 0) + 1
        store.set_meta(con, empty_key, empties)
        if empties < 2:                       # one empty board after a full one: wait for a second poll to confirm
            return n
    else:
        store.set_meta(con, empty_key, 0)
    for k in open_now:
        last = con.execute("SELECT data FROM market_events WHERE id = ?", (state[k]["last_event_id"],)).fetchone()
        o = json.loads(last[0]) if last else {"market_key": k}
        eid = _insert_event(con, platform, o, "removed", observed_at, poll_id, raw_id, state[k]["state_sha"])
        con.execute("UPDATE market_state SET open = 0, last_event_id = ?, last_seen = ? WHERE platform = ? AND market_key = ?",
                    (eid, observed_at, platform, k))
        n += 1
    return n


def fetch(platform, timeout=30):
    req = urllib.request.Request(URLS[platform], headers=HEADERS[platform])
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def poll(con, platform, fetcher=None):
    """One poll of one platform: fetch, parse, append events. Failures are recorded and change nothing else."""
    t0 = time.time()
    at = store.now_iso()
    http, raw, err = None, None, None
    try:
        http, raw = (fetcher or fetch)(platform)
        body = json.loads(raw)
        offers = PARSERS[platform](body)
        if platform == "underdog" and body.get("over_under_lines") and not offers:
            raise ValueError("lines present but none parsed - format change?")
    except urllib.error.HTTPError as e:
        http, err = e.code, f"HTTP {e.code}"
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
    body_sha = store.sha(raw.decode("utf-8", "replace")) if raw else None
    if err:
        last_raw = store.get_meta(con, f"raw_err_at:{platform}")
        raw_id = None
        if raw and (not last_raw or last_raw < store.now_iso(time.time() - 3600)):
            raw_id = store.save_raw(con, platform, URLS[platform], {}, raw, status="error", fetched_at=at)
            store.set_meta(con, f"raw_err_at:{platform}", at)
        con.execute("INSERT INTO market_polls (platform, polled_at, ok, http, n_markets, n_events, error, body_sha, raw_id, seconds) "
                    "VALUES (?, ?, 0, ?, NULL, 0, ?, ?, ?, ?)", (platform, at, http, err, body_sha, raw_id, round(time.time() - t0, 2)))
        con.commit()
        return {"ok": False, "error": err, "http": http}
    pid = con.execute("INSERT INTO market_polls (platform, polled_at, ok, http, n_markets, n_events, error, body_sha, raw_id, seconds) "
                      "VALUES (?, ?, 1, ?, ?, 0, NULL, ?, NULL, NULL)", (platform, at, http, len(offers), body_sha)).lastrowid
    # the raw copy goes in first so every event can point at the exact response it came from; a poll with no event
    # keeps a copy only every RAW_SAMPLE_S (its offers are identical to the last kept copy's except volatile fields)
    last_sample = store.get_meta(con, f"raw_at:{platform}")
    keep_raw = pending(con, platform, offers) > 0 or not last_sample or last_sample < store.now_iso(time.time() - RAW_SAMPLE_S)
    raw_id = store.save_raw(con, platform, URLS[platform], {}, raw, n_rows=len(offers), fetched_at=at) if keep_raw else None
    n = record(con, platform, offers, at, pid, raw_id)
    if raw_id:
        store.set_meta(con, f"raw_at:{platform}", at)
    con.execute("UPDATE market_polls SET n_events = ?, raw_id = ?, seconds = ? WHERE id = ?", (n, raw_id, round(time.time() - t0, 2), pid))
    con.commit()
    return {"ok": True, "markets": len(offers), "events": n}


def status(con):
    out = {}
    for platform in URLS:
        last = con.execute("SELECT polled_at, ok, http, n_markets, n_events, error FROM market_polls WHERE platform = ? "
                           "ORDER BY id DESC LIMIT 1", (platform,)).fetchone()
        last_ok = con.execute("SELECT polled_at, n_markets FROM market_polls WHERE platform = ? AND ok = 1 ORDER BY id DESC LIMIT 1",
                              (platform,)).fetchone()
        day = store.now_iso(time.time() - 86400)
        polls = con.execute("SELECT COUNT(*), SUM(ok) FROM market_polls WHERE platform = ? AND polled_at >= ?", (platform, day)).fetchone()
        ev = {r[0]: r[1] for r in con.execute("SELECT event, COUNT(*) FROM market_events WHERE platform = ? GROUP BY event", (platform,))}
        first = con.execute("SELECT MIN(observed_at) FROM market_events WHERE platform = ?", (platform,)).fetchone()[0]
        open_ = con.execute("SELECT COUNT(*) FROM market_state WHERE platform = ? AND open = 1", (platform,)).fetchone()[0]
        by = [dict(r) for r in con.execute(
            "SELECT stat, scope, market_type, odds_type, COUNT(*) AS markets FROM (SELECT DISTINCT market_key, stat, scope, "
            "market_type, odds_type FROM market_events WHERE platform = ?) GROUP BY stat, scope, market_type, odds_type ORDER BY markets DESC",
            (platform,))]
        out[platform] = {"last_poll": dict(last) if last else None, "last_ok": dict(last_ok) if last_ok else None,
                         "polls_24h": polls[0], "ok_24h": polls[1] or 0, "events": ev, "archive_since": first,
                         "open_markets": open_, "markets_by_kind": by}
    return out
