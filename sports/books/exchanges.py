"""Display-only exchanges (owner 2026-11-09): Novig and Gemini Predictions player props -> canonical OfferSnapshot records
(worker only; every request through ledger.reserve, per-host pacing in net.py).

DISPLAY ONLY. These quotes are shown as priced exchange lines (board, Compare / best prices, the card's odds table)
and nothing else: sports/books SOURCES marks them display_only, so they never enter the mc-1 / mi-1 forecasts
(market.quote_prob refuses them and eligible_quotes skips them), the mv-1 display consensus (market_view.source_status)
or the hub's fair-price consensus (workspace_ev). Their own lines follow cap-3 like every exchange (board_view.own_chance:
the YES midpoint inside the 5-cent spread). READ-ONLY public market data: no account, no order, no trade.

Novig (api.novig.com, read 2026-11-09). The public catalog needs NO key: GET /v3/public/catalog/markets?league=NBA|NFL
  &eventStatus=OPEN_PREGAME&limit=5000 (+ `after` = the answer's `next` to page) and /v3/public/catalog/events (team
  names, start). A player prop is a market with marketType in NV_STAT, description "<Player> <strike> <TYPE>",
  strike "11.5", two outcomes "Over 11.5" / "Under 11.5", each with its best resting BID {price, qty (1-cent
  contracts)}. One book serves both sides: Over bid = the Over outcome's bid, Over ask = 1 - the Under outcome's bid
  (synpath/novig.py, MIT, read as reference only). A ladder: several strikes per player and stat. Event description
  "<Away full name> @ <Home full name>", startsTs (epoch ms). The *_PROTECTED types are a different contract (not
  stored). voids "FMV": a void settles at fair market value. The owner's Ed25519 key (Novig_api) is NOT used: it signs
  trading / history routes only, which also need the key's id (Novig-Key-Id) - read-only market data needs neither.
Gemini Predictions (api.gemini.com, read 2026-11-09, anonymous): GET /v1/prediction-markets/events?status=active
  &category=Sports&sport=american_football|basketball&sports_market_type=prop&limit=100&offset=N (pagination.total).
  One event per (game, stat): ticker "NFL-2611100115-BUF-MIN-PPRECY" (league, UTC start, away, home), participants
  with abbreviation + side, startTime, sportsMarket {metric, scope}. One contract per (player, threshold):
  label "Justin Jefferson 30+ Receiving Yards", strike {"type": "over", "value": "29.5"}, marketState open,
  prices.bestBid / bestAsk = the YES (Over) contract; Under = 1 - ask / 1 - bid.

Kept only: an open, pre-game, two-sided market inside the frozen exchange spread (MAX_SPREAD, as Kalshi / Polymarket).
At most MAX_RUNGS lines per player and stat (nearest even). A wider or one-sided market is not stored and a successful
read withdraws a stored one ('removed' revision, hidden); a failed read writes nothing, so the last quote stays (and ages
on the board) - a transient failure never changes a grade. A step of the light books job every 10 min (run_due() below;
never inside ingest-<league>). Kill switches: NOVIG=0 / GEMINI=0 (default on; no key is needed).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from .. import adapters, identity, names, net
from ..contracts import OfferSnapshot, SideQuote
from ..model.market import MAX_SPREAD
from . import ledger

VENUES = ("novig", "gemini")
LABEL = {"novig": "Novig", "gemini": "Gemini"}


def enabled(venue, env=None):
    """NOVIG / GEMINI (default on): '0' / 'false' / 'no' / 'off' switches that venue off (nothing is read)."""
    v = ((os.environ if env is None else env).get(venue.upper()) or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def novig_key_present(env=None):
    """Whether the owner's Novig key is set (Novig_api / NOVIG_API / NOVIG_API_KEY, any case). Presence only - the value
    is never read here: the public catalog needs no key."""
    env = os.environ if env is None else env
    return any(k.upper() in ("NOVIG_API", "NOVIG_API_KEY") and str(v).strip() for k, v in env.items())


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _iso_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _get(source, url, params=None, timeout=20.0):
    ok, why = ledger.reserve(source, 1.0, url.split("?")[0][-80:], wait_s=5.0)
    if not ok:
        raise net.HttpError(None, url, f"budget: {why}")
    try:
        r = net.get(url, params=params, headers={"Accept": "application/json"}, timeout=timeout, tries=1)
    except net.HttpError as e:
        ledger.report(source, e.status)
        raise
    ledger.report(source, r.status_code)
    return r


def _priced(bid, ask):
    """(bid, ask) of the Over contract when the market is two-sided and inside the exchange spread, else None."""
    if bid is None or ask is None or not (0 < bid < ask < 1):
        return None
    if ask - bid > MAX_SPREAD + 1e-9:
        return None
    return round(bid, 6), round(ask, 6)


def _sides(bid, ask):
    """Prices only: book sizes are part of an offer's content hash, and sizes move on almost every read - storing them
    made a new revision per quote per read (the collector-cost regression of 2026-11-09). Sizes stay out."""
    over = SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask)
    under = SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 6), ask=round(1 - bid, 6))
    return over, under


def _resolve(resolver, start, away, home, name, extras):
    """(event_id, player_id, extras) - exact team codes on the game's US date, the player on either roster."""
    if resolver is None:
        return None, None, extras + (("unresolved", "no resolver"),)
    day = identity.us_date(start) if start else None
    if not (day and away and home):
        return None, None, extras + (("unresolved_event", "no date or teams"),)
    eid, why = resolver.event(day, away, home)
    if eid is None:
        return None, None, extras + (("unresolved_event", why),)
    pid, why_p = resolver.player(name, None, eid, day)
    if pid is None:
        extras += (("unresolved_player", why_p),)
    return eid, pid, extras


# ------------------------------------------------------------------------------------------------------------ Novig
NOVIG = "https://api.novig.com/v3/public/catalog"
NV_LEAGUE = {"nba": "NBA", "nfl": "NFL"}
NV_STAT = {"nba": {"POINTS": "points", "REBOUNDS": "rebounds", "ASSISTS": "assists", "THREE_POINTERS_MADE": "threes",
                   "POINTS_REBOUNDS_ASSISTS": "pra", "POINTS_REBOUNDS": "pr", "POINTS_ASSISTS": "pa",
                   "REBOUNDS_ASSISTS": "ra", "STEALS": "steals", "BLOCKS": "blocks", "STEALS_BLOCKS": "stocks"},
           "nfl": {"PASSING_YARDS": "pass_yds", "PASSING_ATTEMPTS": "pass_att", "PASSING_COMPLETIONS": "pass_cmp",
                   "PASSING_TOUCHDOWNS": "pass_td", "INTERCEPTIONS_THROWN": "pass_int", "RUSHING_YARDS": "rush_yds",
                   "RUSHING_ATTEMPTS": "rush_att", "RECEPTIONS": "rec", "RECEIVING_YARDS": "rec_yds",
                   "RUSHING_AND_RECEIVING_YARDS": "rush_rec_yds", "PASSING_AND_RUSHING_YARDS": "pass_rush_yds"}}
NV_PAGE = 5000                       # the catalog's largest page
NV_MAX_PAGES = 6
RULE_NV = "novig-catalog: Over > strike, Under < strike, full game; void settles at fair market value (voids FMV)"
_NV_DESC = re.compile(r"^(?P<name>.+) (?P<line>-?\d+(?:\.\d+)?) (?P<type>[A-Z_]+)$")
_NV_KEEP = ("marketId", "description", "eventId", "marketType", "strike", "status", "voids", "startsTs", "outcomes",
            "volume", "openInterest")


def _nv_pages(path, params, timeout=20.0):
    after = None
    for _ in range(NV_MAX_PAGES):
        body = _get("novig", f"{NOVIG}{path}", params={**params, "limit": NV_PAGE, **({"after": after} if after else {})},
                    timeout=timeout)
        data = body.json()
        del body
        yield data.get("items") or []
        after = data.get("next")
        if not after or not data.get("items"):
            return


def novig_raw(league, timeout=20.0):
    """{"events": {eventId: event}, "markets": [trimmed prop market]} of the league's pre-game catalog."""
    lg, stats = NV_LEAGUE[league], NV_STAT[league]
    events = {}
    for items in _nv_pages("/events", {"league": lg, "status": "OPEN_PREGAME"}, timeout):
        for e in items:
            if e.get("eventId"):
                events[str(e["eventId"])] = {k: e.get(k) for k in ("eventId", "description", "league", "status",
                                                                    "startsTs")}
    markets = []
    for items in _nv_pages("/markets", {"league": lg, "eventStatus": "OPEN_PREGAME"}, timeout):
        markets += [{k: m.get(k) for k in _NV_KEEP} for m in items if m.get("marketType") in stats]
    return {"events": events, "markets": markets}


def _nv_bid(outcome):
    return _f(((outcome or {}).get("bid") or {}).get("price"))


def novig_offers(raw, league, observed_at, resolver=None, teams_raw=()):
    b = adapters.Batch()
    stats = NV_STAT[league]
    full = {t["name"]: t["abbr"] for t in teams_raw or ()}
    for m in raw.get("markets") or []:
        mt = m.get("marketType")
        if mt not in stats:
            continue
        key = f"novig:{m.get('marketId')}"
        d = _NV_DESC.match((m.get("description") or "").strip())
        line = _f(m.get("strike"))
        if not d or line is None or d.group("type") != mt or abs(float(d.group("line")) - line) > 1e-9:
            b.q("offer", "novig", key, "unreadable_description_or_strike", {"description": m.get("description")})
            continue
        outs = {str(o.get("name") or "").split(" ")[0]: o for o in m.get("outcomes") or [] if isinstance(o, dict)}
        if set(outs) != {"Over", "Under"} or len(m.get("outcomes") or []) != 2:
            b.q("offer", "novig", key, "outcomes_not_over_under", {"outcomes": [o.get("name") for o in
                                                                                m.get("outcomes") or []]})
            continue
        ev = (raw.get("events") or {}).get(str(m.get("eventId"))) or {}
        if m.get("status") != "OPEN" or ev.get("status") not in (None, "OPEN_PREGAME"):
            continue
        ob, ub = _nv_bid(outs["Over"]), _nv_bid(outs["Under"])
        px = _priced(ob, round(1 - ub, 6) if ub is not None else None)
        if px is None:
            continue                                  # one-sided / crossed / wider than the exchange spread: not shown
        over, under = _sides(*px)
        start = _iso_ms(ev.get("startsTs") or m.get("startsTs"))
        if not start or start <= observed_at:
            continue                                  # started (or no start): never a pre-game quote
        name = d.group("name").strip()
        away = home = None
        desc = ev.get("description") or ""
        if " @ " in desc:
            a_full, h_full = (x.strip() for x in desc.split(" @ ", 1))
            away, home = full.get(a_full), full.get(h_full)
        extras = (("event_description", desc or None), ("voids", m.get("voids")),
                  ("dnp_rule", "void settles at fair market value (venue voids FMV); not verified for an inactive "
                               "player"), ("display_only", "not in any market chance"))
        if resolver is not None and not (away and home):
            eid, pid, extras = None, None, extras + (("unresolved_event", "team full name not an ESPN name"),)
        else:
            eid, pid, extras = _resolve(resolver, start, away, home, name, extras)
        liq = (("volume", _f(m.get("volume")) / 100 if _f(m.get("volume")) is not None else None),
               ("open_interest", _f(m.get("openInterest")) / 100 if _f(m.get("openInterest")) is not None else None))
        b.add(OfferSnapshot(
            platform="novig", offer_id=key, market_id=str(m.get("eventId")), league=league, event_id=eid,
            event_ref=desc or None, player_id=pid, player_name=name, player_key=names.key(name), team=None,
            stat=stats[mt], source_stat=mt, scope="full_game", line=line, sides=(over, under), variant="main",
            promotional=False, availability="open", rule_version=RULE_NV, listed_start_utc=start,
            listed_time_kind="start" if start else "unknown", source_updated_at=None, observed_at=observed_at,
            source_time_unknown=True, liquidity=liq, extras=extras, known_at=observed_at))
    return _nearest_even(b)


def _nearest_even(b, keep=None):
    """At most MAX_RUNGS lines per (game, player, stat): the ones priced nearest even (the lines a board compares;
    the deep rungs priced 5 / 95 are what cap-3 holds anyway). Bounds the writes per read."""
    keep = MAX_RUNGS if keep is None else keep
    groups = {}
    for r in b.records:
        groups.setdefault((r.market_id, r.player_key, r.stat), []).append(r)
    kept = []
    for rs in groups.values():
        rs.sort(key=lambda r: (abs((r.side("over").bid + r.side("over").ask) / 2 - 0.5), r.line))
        kept += rs[:keep]
    b.records = sorted(kept, key=lambda r: r.offer_id)
    return b


# ----------------------------------------------------------------------------------------------------------- Gemini
GEMINI = "https://api.gemini.com/v1/prediction-markets/events"
GM_SPORT = {"nba": "basketball", "nfl": "american_football"}
# metric names seen in the live answer (2026-11-09: NFL only; Gemini listed no NBA props). An unseen metric is
# quarantined (inventory), never guessed.
GM_STAT = {"nba": {}, "nfl": {"passing_yards": "pass_yds", "rushing_yards": "rush_yds", "receiving_yards": "rec_yds",
                              "passing_touchdowns": "pass_td"}}
GM_SKIP = {"touchdowns"}             # anytime touchdown: not a market Scout settles
GM_PAGE = 100
GM_MAX_PAGES = 10
RULE_GM = "gemini-predictions: YES = at least the label's number (over the strike), full contest"
_GM_LABEL = re.compile(r"^(?P<name>.+?) (?P<n>\d+)\+ ")
_GM_TICKER = re.compile(r"^(?P<lg>[A-Z]+)-\d{10}-(?P<away>[A-Z0-9]+)-(?P<home>[A-Z0-9]+)-")
_GM_EVENT = ("id", "ticker", "title", "status", "isLive", "startTime", "participants", "sportsMarket")
_GM_CONTRACT = ("id", "label", "ticker", "instrumentSymbol", "marketState", "status", "strike", "prices")


def gemini_raw(league, timeout=20.0):
    """Every active prop event of the league's sport (trimmed), its ticker starting with the league."""
    out, offset, total = [], 0, None
    for _ in range(GM_MAX_PAGES):
        r = _get("gemini", GEMINI, params={"status": "active", "category": "Sports", "sport": GM_SPORT[league],
                                           "sports_market_type": "prop", "limit": GM_PAGE, "offset": offset},
                    timeout=timeout)
        body = r.json()
        del r
        data = body.get("data") or []
        total = (body.get("pagination") or {}).get("total")
        for e in data:
            if str(e.get("ticker") or "").startswith(league.upper() + "-"):
                t = {k: e.get(k) for k in _GM_EVENT}
                t["contracts"] = [{k: c.get(k) for k in _GM_CONTRACT} for c in e.get("contracts") or []]
                out.append(t)
        offset += GM_PAGE
        if not data or total is None or offset >= int(total):
            break
    return out


def gemini_offers(events, league, observed_at, resolver=None):
    b = adapters.Batch()
    stats = GM_STAT[league]
    for e in events or []:
        sm = e.get("sportsMarket") or {}
        metric = sm.get("metric")
        if metric in GM_SKIP:
            continue
        if metric not in stats:
            b.q("offer", "gemini", f"gemini:{e.get('ticker')}", "metric_not_mapped", {"metric": metric})
            continue
        # isLive is NOT in-play (2026-11-09: true on a game a day away) - a started game is left out by its startTime
        if (sm.get("scope") or {}).get("type") not in (None, "full_contest") or e.get("status") != "active":
            continue
        tk = _GM_TICKER.match(str(e.get("ticker") or ""))
        sides_ = {p.get("side"): p.get("abbreviation") for p in e.get("participants") or [] if isinstance(p, dict)}
        away, home = (sides_.get("away") or (tk and tk.group("away"))), (sides_.get("home") or (tk and tk.group("home")))
        start = None
        try:
            start = datetime.fromisoformat(str(e.get("startTime")).replace("Z", "+00:00")).astimezone(timezone.utc) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            start = None
        if not start or start <= observed_at:
            continue                                  # started (or no start): never a pre-game quote
        for c in e.get("contracts") or []:
            key = f"gemini:{c.get('instrumentSymbol') or c.get('id')}"
            st = c.get("strike") or {}
            line = _f(st.get("value"))
            lab = _GM_LABEL.match(str(c.get("label") or ""))
            if st.get("type") != "over" or line is None or not lab or abs(int(lab.group("n")) - 0.5 - line) > 1e-9:
                b.q("offer", "gemini", key, "unreadable_label_or_strike", {"label": c.get("label"), "strike": st})
                continue
            if c.get("marketState") != "open":
                continue
            p = c.get("prices") or {}
            px = _priced(_f(p.get("bestBid")), _f(p.get("bestAsk")))
            if px is None:
                continue
            over, under = _sides(*px)
            name = lab.group("name").strip()
            extras = (("event_ticker", e.get("ticker")), ("display_only", "not in any market chance"))
            eid, pid, extras = _resolve(resolver, start, away, home, name, extras)
            b.add(OfferSnapshot(
                platform="gemini", offer_id=key, market_id=str(e.get("ticker")), league=league, event_id=eid,
                event_ref=e.get("title"), player_id=pid, player_name=name, player_key=names.key(name), team=None,
                stat=stats[metric], source_stat=metric, scope="full_game", line=line, sides=(over, under),
                variant="main", promotional=False, availability="open", rule_version=RULE_GM, listed_start_utc=start,
                listed_time_kind="start" if start else "unknown", source_updated_at=None, observed_at=observed_at,
                source_time_unknown=True, liquidity=(), extras=extras, known_at=observed_at))
    return _nearest_even(b)


# ----------------------------------------------------------------------------------------------------------- the job
# Collector-cost regression of 2026-11-09 (ingest-nba 848 s vs ~360 s once these venues joined ingest-<league>): each
# venue there paid worker._store_offers' withdrawal read - a league-wide GROUP BY over every stored offer revision of
# the league (substr(platform) is not indexable; the same scan PrizePicks and Underdog already pay once each per cycle)
# - plus ~1,000 new revisions a read (book sizes are in the content hash), all inside the EXCLUSIVE collector, so every
# heavy job waited behind it. Now:
#   - a step of the light books job (worker._exchanges_step -> run_due, every EVERY_S), never inside ingest-<league>,
#     no 'canonical' scheduler lock, no reservation of its own (a separate light job took budget room from the heavy
#     pair: sched_sim showed the collectors waiting ~3 min longer);
#   - a hard time budget (BUDGET_S): each step starts only inside it, requests time out within it, the rest is skipped;
#   - requests bounded: Novig 2 per league (events + markets; NV_MAX_PAGES pages at most), Gemini 1 per league
#     (GM_MAX_PAGES at most);
#   - the withdrawal read is the venue's OWN identities through the (kind, source, ...) index - never a league scan;
#   - writes in short batches (WRITE_CHUNK records per BEGIN IMMEDIATE), prices only (no book sizes), at most MAX_RUNGS
#     lines per player and stat, so a read writes only the quotes whose price moved.
MAX_RUNGS = 3
BUDGET_S = float(os.environ.get("SPORTS_EXCHANGES_BUDGET_S", "30"))   # measured pass: 4.3 s (both leagues)
# the board shows a book row while its last sighting is under 15 min old (web/sports/stats.js STALE_S)
EVERY_S = float(os.environ.get("SPORTS_EXCHANGES_EVERY_S", "600"))
WRITE_CHUNK = 200
WITHDRAW_WINDOW_H = 72


def _current_of_venue(con, venue, league, since):
    """{offer_id: record row} - the CURRENT revision of each identity of this venue + league seen since `since`, read
    through the records_current index (kind, source, source_key, late, revision): only this venue's rows."""
    from .. import store
    rows = con.execute(
        "SELECT r.* FROM (SELECT rid, MAX(revision) FROM records WHERE kind = 'offer' AND source = ? AND "
        f"{store.CURRENT_SQL} GROUP BY source_key) z JOIN records r ON r.rid = z.rid "
        "WHERE r.league = ? AND COALESCE(r.last_seen_at, r.observed_at, r.known_at) >= ?",
        (venue, league, since)).fetchall()
    return {r["source_key"]: r for r in rows}


def store_venue(con, venue, league, batch, observed_at):
    """A SUCCESSFUL read of one venue + league -> stored offers (short batched writes), and a 'removed' revision for each
    of its own offers the read no longer lists whose game has not started (hidden from the board)."""
    from datetime import timedelta

    from .. import store
    from ..contracts import parse_utc
    res = {"inserted": 0, "duplicate": 0, "revision": 0, "invalid": 0}
    recs = batch.records
    for i in range(0, len(recs), WRITE_CHUNK):
        got = store.put_many(con, recs[i:i + WRITE_CHUNK])
        for k in res:
            res[k] += got.get(k, 0)
    if batch.quarantine:
        store.quarantine_many(con, batch.quarantine, league, observed_at)
    since = (parse_utc(observed_at) - timedelta(hours=WITHDRAW_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = {r.offer_id for r in recs}
    stale = []
    for oid, row in _current_of_venue(con, venue, league, since).items():
        if oid in seen:
            continue
        o = store._rec(row)
        if o.availability == "removed":
            continue
        if o.listed_start_utc and o.listed_time_kind == "start" and o.listed_start_utc <= observed_at:
            continue                                  # started: it left because the game began
        stale.append(store.withdrawn(o, observed_at))
    removed = 0
    for i in range(0, len(stale), WRITE_CHUNK):
        removed += store.put_many(con, stale[i:i + WRITE_CHUNK])["revision"]
    return {**res, "removed": removed, "quarantined": len(batch.quarantine)}


def run(con, leagues, budget_s=None, clock=None, observed_at=None):
    """The scheduler's light "exchanges" job: every enabled venue for every league inside one hard time budget. A step
    that would start after the budget is skipped (reported); a failed read writes nothing (the last quote stays)."""
    import time

    from ..contracts import now_utc
    from .sources import resolver_from_store
    clock = clock or time.monotonic
    budget_s = BUDGET_S if budget_s is None else budget_s
    t0 = clock()

    def left():
        return budget_s - (clock() - t0)
    out = {"budget_s": budget_s}
    for league in leagues:
        venues = [v for v in VENUES if enabled(v) and league in (NV_LEAGUE if v == "novig" else GM_SPORT)]
        lo = out[league] = {v: {"ok": True, "skipped": f"{v.upper()}=0"} for v in VENUES if not enabled(v)}
        if not venues:
            continue
        if left() <= 0:
            lo.update({v: {"ok": False, "skipped": "time budget"} for v in venues})
            continue
        t = clock()
        try:
            resolver, teams_raw = resolver_from_store(con, league)
        except Exception as e:                          # noqa: BLE001 - no resolver: nothing is written this cycle
            lo.update({v: {"ok": False, "error": f"resolver: {type(e).__name__}"} for v in venues})
            continue
        lo["resolver_s"] = round(clock() - t, 3)
        for v in venues:
            if left() <= 0:
                lo[v] = {"ok": False, "skipped": "time budget"}
                continue
            t = clock()
            obs = observed_at or now_utc()
            try:
                if v == "novig":
                    raw = novig_raw(league, timeout=max(1.0, min(20.0, left())))
                    b = novig_offers(raw, league, obs, resolver, teams_raw)
                    seen = {"events": len(raw["events"]), "markets": len(raw["markets"])}
                else:
                    evs = gemini_raw(league, timeout=max(1.0, min(20.0, left())))
                    b = gemini_offers(evs, league, obs, resolver)
                    seen = {"events": len(evs)}
            except (net.HttpError, ValueError) as e:
                lo[v] = {"ok": False, "error": str(e)[:160], "read_s": round(clock() - t, 3)}
                continue
            read_s = round(clock() - t, 3)
            if left() <= 0:
                lo[v] = {"ok": False, "skipped": "time budget (read, not written)", "read_s": read_s}
                continue
            t = clock()
            r = store_venue(con, v, league, b, obs)
            r.update(seen, ok=True, offers=len(b.records), resolved_players=sum(1 for x in b.records if x.player_id),
                     read_s=read_s, write_s=round(clock() - t, 3))
            if v == "novig":
                r["key"] = "set (not needed: public catalog)" if novig_key_present() else "not set (not needed)"
            lo[v] = r
    out["ran_s"] = round(clock() - t0, 3)
    return out


def _state_path():
    from .. import config
    return config.DATA_DIR / "books" / "exchanges_state.json"


def run_due(con, leagues, now=None, budget_s=None):
    """run() when EVERY_S has passed since the last ATTEMPT (recorded before the pass, so a failing venue is never
    re-read faster than the cadence), else {"due": False, "next_in_s": ...}."""
    import json
    import time
    now = time.time() if now is None else now
    p = _state_path()
    try:
        last = float(json.loads(p.read_text(encoding="utf-8")).get("last_at") or 0)
    except (OSError, ValueError, AttributeError):
        last = 0.0
    if now - last < EVERY_S:
        return {"due": False, "next_in_s": round(EVERY_S - (now - last), 1)}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_at": now}), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        return {"due": True, "skipped": "state file not writable"}
    return {"due": True, **run(con, leagues, budget_s=budget_s)}
