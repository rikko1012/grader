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
A wider or one-sided market is not stored and a successful read withdraws a stored one ('removed' revision, hidden);
a failed read writes nothing, so the last quote stays (and ages on the board) - a transient failure never changes a
grade. Kill switches: NOVIG=0 / GEMINI=0 (default on; no key is needed).
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


def _get(source, url, params=None):
    ok, why = ledger.reserve(source, 1.0, url.split("?")[0][-80:])
    if not ok:
        raise net.HttpError(None, url, f"budget: {why}")
    try:
        r = net.get(url, params=params, headers={"Accept": "application/json"}, timeout=30, tries=1)
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


def _sides(bid, ask, bid_size=None, ask_size=None):
    over = SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size)
    under = SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 6), ask=round(1 - bid, 6),
                      bid_size=ask_size, ask_size=bid_size)
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


def _nv_pages(path, params):
    after = None
    for _ in range(NV_MAX_PAGES):
        body = _get("novig", f"{NOVIG}{path}", params={**params, "limit": NV_PAGE, **({"after": after} if after else {})})
        data = body.json()
        del body
        yield data.get("items") or []
        after = data.get("next")
        if not after or not data.get("items"):
            return


def novig_raw(league):
    """{"events": {eventId: event}, "markets": [trimmed prop market]} of the league's pre-game catalog."""
    lg, stats = NV_LEAGUE[league], NV_STAT[league]
    events = {}
    for items in _nv_pages("/events", {"league": lg, "status": "OPEN_PREGAME"}):
        for e in items:
            if e.get("eventId"):
                events[str(e["eventId"])] = {k: e.get(k) for k in ("eventId", "description", "league", "status",
                                                                    "startsTs")}
    markets = []
    for items in _nv_pages("/markets", {"league": lg, "eventStatus": "OPEN_PREGAME"}):
        markets += [{k: m.get(k) for k in _NV_KEEP} for m in items if m.get("marketType") in stats]
    return {"events": events, "markets": markets}


def _nv_bid(outcome):
    b = (outcome or {}).get("bid") or {}
    p = _f(b.get("price"))
    q = _f(b.get("qty"))
    return p, (round(q / 100, 2) if q is not None else None)


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
        ob, ob_size = _nv_bid(outs["Over"])
        ub, ub_size = _nv_bid(outs["Under"])
        px = _priced(ob, round(1 - ub, 6) if ub is not None else None)
        if px is None:
            continue                                  # one-sided / crossed / wider than the exchange spread: not shown
        over, under = _sides(px[0], px[1], ob_size, ub_size)
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
        eid, pid, extras = _resolve(resolver, start, away, home, name, extras) if (away and home) or resolver is None \
            else (None, None, extras + (("unresolved_event", "team full name not an ESPN name"),))
        liq = (("volume", _f(m.get("volume")) / 100 if _f(m.get("volume")) is not None else None),
               ("open_interest", _f(m.get("openInterest")) / 100 if _f(m.get("openInterest")) is not None else None))
        b.add(OfferSnapshot(
            platform="novig", offer_id=key, market_id=str(m.get("eventId")), league=league, event_id=eid,
            event_ref=desc or None, player_id=pid, player_name=name, player_key=names.key(name), team=None,
            stat=stats[mt], source_stat=mt, scope="full_game", line=line, sides=(over, under), variant="main",
            promotional=False, availability="open", rule_version=RULE_NV, listed_start_utc=start,
            listed_time_kind="start" if start else "unknown", source_updated_at=None, observed_at=observed_at,
            source_time_unknown=True, liquidity=liq, extras=extras, known_at=observed_at))
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


def gemini_raw(league):
    """Every active prop event of the league's sport (trimmed), its ticker starting with the league."""
    out, offset, total = [], 0, None
    for _ in range(GM_MAX_PAGES):
        r = _get("gemini", GEMINI, params={"status": "active", "category": "Sports", "sport": GM_SPORT[league],
                                           "sports_market_type": "prop", "limit": GM_PAGE, "offset": offset})
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
    return b


# ----------------------------------------------------------------------------------------------------------- ingest
def ingest(con, venue, league, observed_at, resolver, teams_raw, store_offers):
    """One read of one venue + league -> stored offers. store_offers = worker._store_offers (a successful read
    withdraws what it no longer lists; a failed read writes nothing, so the last quote stays)."""
    if not enabled(venue):
        return {"ok": True, "skipped": f"{venue.upper()}=0"}
    if venue == "novig" and league not in NV_LEAGUE or venue == "gemini" and league not in GM_SPORT:
        return {"ok": True, "skipped": "league not listed"}
    try:
        if venue == "novig":
            raw = novig_raw(league)
            b = novig_offers(raw, league, observed_at, resolver, teams_raw)
            seen = {"events": len(raw["events"]), "markets": len(raw["markets"])}
        else:
            evs = gemini_raw(league)
            b = gemini_offers(evs, league, observed_at, resolver)
            seen = {"events": len(evs)}
    except (net.HttpError, ValueError) as e:
        return {"ok": False, "error": str(e)[:160]}
    out = store_offers(con, venue, league, b, observed_at, True)
    out.update(seen, offers=len(b.records), resolved_players=sum(1 for r in b.records if r.player_id))
    if venue == "novig":
        out["key"] = "set (not needed: public catalog)" if novig_key_present() else "not set (not needed)"
    return out
