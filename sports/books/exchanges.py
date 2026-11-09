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
on the board) - a transient failure never changes a grade. A step of the light books job every 5 min (run_due() below;
never inside ingest-<league>). Kill switches: NOVIG=0 / GEMINI=0 (default on; no key is needed).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

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


class Stop(Exception):
    """A venue read stopped before it finished (time budget, memory guard): nothing of it is written."""


CONNECT_S = 5.0
READ_S = 15.0                        # longest wait for any one socket read; the whole answer is bounded by the deadline


class _Body:
    __slots__ = ("status_code", "_buf")

    def __init__(self, status_code, buf):
        self.status_code, self._buf = status_code, buf

    def json(self):
        return json.loads(self._buf)


def _get(source, url, params=None, timeout=20.0, deadline=None):
    """One GET inside the step's deadline (time.monotonic()): nothing is sent with under a second left, a Retry-After
    hold on the host fails at once (never slept inside the step), the ledger's pacing wait and every socket wait are
    cut to the time left, and the answer is streamed so a slow or trickling body stops at the deadline."""
    def rem():
        return float("inf") if deadline is None else deadline - time.monotonic()
    if rem() < 1.0:
        raise Stop("time budget")
    nb = net.holds().get(urlsplit(url).netloc)
    if nb:
        raise net.HttpError(429, url, "", not_before=nb, held=True)
    ok, why = ledger.reserve(source, 1.0, url.split("?")[0][-80:], wait_s=max(0.0, min(5.0, rem() - 1.0)))
    if not ok:
        raise net.HttpError(None, url, f"budget: {why}")
    if rem() < 1.0:
        raise Stop("time budget")
    t = (min(CONNECT_S, rem()), max(1.0, min(timeout, READ_S, rem())))
    hdr = {"Accept": "application/json"}
    try:
        r = net._request(url, params, hdr, t, 1, (200,),
                         lambda: net.session().get(url, params=params, headers=hdr, timeout=t, stream=True))
    except net.HttpError as e:
        ledger.report(source, e.status)
        raise
    ledger.report(source, r.status_code)
    buf = bytearray()
    try:
        for chunk in r.iter_content(65536):
            buf += chunk
            if rem() <= 0:
                raise Stop("time budget (answer still arriving)")
    finally:
        r.close()
    return _Body(r.status_code, buf)


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


def _guard(deadline, mem_ok):
    """Before every request: stop the venue read when the budget is spent or the process nears its memory cap."""
    if deadline is not None and deadline - time.monotonic() < 1.0:
        raise Stop("time budget")
    if mem_ok is not None and not mem_ok():
        raise Stop("memory")


def _dicts(xs):
    return [x for x in xs if isinstance(x, dict)] if isinstance(xs, list) else []


def _nv_pages(path, params, timeout=20.0, deadline=None, mem_ok=None, state=None):
    """Pages of items; state["complete"] = False when the page limit is reached with a `next` cursor still set."""
    after = None
    for _ in range(NV_MAX_PAGES):
        _guard(deadline, mem_ok)
        body = _get("novig", f"{NOVIG}{path}", params={**params, "limit": NV_PAGE, **({"after": after} if after else {})},
                    timeout=timeout, deadline=deadline)
        data = body.json()
        del body
        if not isinstance(data, dict):
            raise ValueError(f"novig {path}: the answer is not an object")
        items = _dicts(data.get("items"))
        yield items
        after = data.get("next")
        if not after or not items:
            return
    if state is not None:
        state["complete"] = False


def novig_raw(league, timeout=20.0, deadline=None, mem_ok=None):
    """{"events": {eventId: event}, "markets": [trimmed prop market], "complete", "items"} of the league's pre-game
    catalog; complete is False when a page limit left part of it unread, items = markets read before the type filter."""
    lg, stats = NV_LEAGUE[league], NV_STAT[league]
    state = {"complete": True}
    events = {}
    for items in _nv_pages("/events", {"league": lg, "status": "OPEN_PREGAME"}, timeout, deadline, mem_ok, state):
        for e in items:
            if e.get("eventId"):
                events[str(e["eventId"])] = {k: e.get(k) for k in ("eventId", "description", "league", "status",
                                                                    "startsTs")}
    markets, n = [], 0
    for items in _nv_pages("/markets", {"league": lg, "eventStatus": "OPEN_PREGAME"}, timeout, deadline, mem_ok, state):
        n += len(items)
        markets += [{k: m.get(k) for k in _NV_KEEP} for m in items if m.get("marketType") in stats]
    return {"events": events, "markets": markets, "complete": state["complete"], "items": n}


def _nv_bid(outcome):
    bid = outcome.get("bid") if isinstance(outcome, dict) else None
    return _f(bid.get("price")) if isinstance(bid, dict) else None


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
    kept, capped = [], set()
    for rs in groups.values():
        rs.sort(key=lambda r: (abs((r.side("over").bid + r.side("over").ask) / 2 - 0.5), r.line))
        kept += rs[:keep]
        capped.update(r.offer_id for r in rs[keep:])
    b.records = sorted(kept, key=lambda r: r.offer_id)
    # still listed and priced, only past the cap: never withdrawn (a 3rd / 4th rung swap would otherwise write a
    # 'removed' revision plus a re-insert each pass); not re-sighted, it ages off the board by the 15-minute rule
    b.capped = capped
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


class Events(list):
    """gemini_raw's events. complete is False when the page limit was reached or the listing's total changed between
    pages (offset paging over a moving list can skip an event); items = events read before the league filter."""
    complete = True
    items = 0


def gemini_raw(league, timeout=20.0, deadline=None, mem_ok=None):
    """Every active prop event of the league's sport (trimmed), its ticker starting with the league."""
    out, offset, total, first = Events(), 0, None, None
    for _ in range(GM_MAX_PAGES):
        _guard(deadline, mem_ok)
        r = _get("gemini", GEMINI, params={"status": "active", "category": "Sports", "sport": GM_SPORT[league],
                                           "sports_market_type": "prop", "limit": GM_PAGE, "offset": offset},
                    timeout=timeout, deadline=deadline)
        body = r.json()
        del r
        if not isinstance(body, dict):
            raise ValueError("gemini: the answer is not an object")
        data = _dicts(body.get("data"))
        pg = body.get("pagination")
        total = pg.get("total") if isinstance(pg, dict) else None
        first = total if first is None else first
        if total != first:
            out.complete = False
        out.items += len(data)
        for e in data:
            if str(e.get("ticker") or "").startswith(league.upper() + "-"):
                t = {k: e.get(k) for k in _GM_EVENT}
                t["contracts"] = [{k: c.get(k) for k in _GM_CONTRACT} for c in _dicts(e.get("contracts"))]
                out.append(t)
        offset += GM_PAGE
        if not data or total is None or offset >= int(total):
            break
    else:
        out.complete = False
    return out


def gemini_offers(events, league, observed_at, resolver=None):
    b = adapters.Batch()
    stats = GM_STAT[league]
    for e in events or []:
        sm = e.get("sportsMarket") if isinstance(e.get("sportsMarket"), dict) else {}
        metric = sm.get("metric")
        if metric in GM_SKIP:
            continue
        if metric not in stats:
            b.q("offer", "gemini", f"gemini:{e.get('ticker')}", "metric_not_mapped", {"metric": metric})
            continue
        # isLive is NOT in-play (2026-11-09: true on a game a day away) - a started game is left out by its startTime
        scope = sm.get("scope")
        if (scope.get("type") if isinstance(scope, dict) else scope) not in (None, "full_contest") or \
                e.get("status") != "active":
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
            st = c.get("strike") if isinstance(c.get("strike"), dict) else {}
            line = _f(st.get("value"))
            lab = _GM_LABEL.match(str(c.get("label") or ""))
            if st.get("type") != "over" or line is None or not lab or abs(int(lab.group("n")) - 0.5 - line) > 1e-9:
                b.q("offer", "gemini", key, "unreadable_label_or_strike", {"label": c.get("label"), "strike": st})
                continue
            if c.get("marketState") != "open":
                continue
            p = c.get("prices") if isinstance(c.get("prices"), dict) else {}
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
#   - a hard time budget (BUDGET_S): one deadline checked before every request, page and write chunk; the ledger's
#     pacing wait, connect / read timeouts and SQLite's busy wait are cut to the time left; an answer is streamed and
#     stopped at the deadline; a Retry-After hold is never slept inside the step (that venue fails at once);
#   - requests bounded: Novig 2 per league (events + markets; NV_MAX_PAGES pages at most), Gemini 1 per league
#     (GM_MAX_PAGES at most);
#   - the withdrawal read is the venue's OWN identities through the (kind, source, ...) index - never a league scan;
#   - writes in short batches (WRITE_CHUNK records per BEGIN IMMEDIATE), prices only (no book sizes), at most MAX_RUNGS
#     lines per player and stat, so a read writes only the quotes whose price moved.
MAX_RUNGS = 3
BUDGET_S = float(os.environ.get("SPORTS_EXCHANGES_BUDGET_S", "30"))   # measured pass: 4.3 s (both leagues)
# the board shows a book row while its last sighting is under 15 min old (web/sports/stats.js STALE_S), and a new
# sighting reaches members only with the next ui-snapshots build (up to ~6-7 min later): a 10-min cadence left no
# margin (rows blinked out between passes, and for 5-10+ min after one failed pass). Every 5 min, and a pass in which
# a venue failed or was skipped is retried once after RETRY_S instead of the full cadence.
EVERY_S = float(os.environ.get("SPORTS_EXCHANGES_EVERY_S", "300"))
RETRY_S = float(os.environ.get("SPORTS_EXCHANGES_RETRY_S", "120"))
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


class _BusyCap:
    """SQLite's busy wait cut to the time left before each chunk (a BEGIN IMMEDIATE otherwise waits up to the
    connection's 30 s behind ingest-<league> / settle / model-forward); the connection's own value is restored."""

    def __init__(self, con, deadline):
        self.con, self.deadline, self.old = con, deadline, None

    def __enter__(self):
        if self.deadline is not None:
            self.old = int(self.con.execute("PRAGMA busy_timeout").fetchone()[0])
        return self

    def set(self):
        if self.old is not None:
            ms = int(max(0.5, self.deadline - time.monotonic()) * 1000)
            self.con.execute(f"PRAGMA busy_timeout={min(self.old, ms)}")

    def __exit__(self, *exc):
        if self.old is not None:
            self.con.execute(f"PRAGMA busy_timeout={self.old}")
        return False


def store_venue(con, venue, league, batch, observed_at, complete=True, deadline=None):
    """A SUCCESSFUL read of one venue + league -> stored offers (short batched writes), and a 'removed' revision for each
    of its own offers the read no longer lists whose game has not started (hidden from the board). Withdrawals only
    after a COMPLETE read (every page read, a non-empty answer), never for rungs left out only by the MAX_RUNGS cap.
    The deadline (time.monotonic()) is checked before every chunk: what is left waits for the next pass."""
    from datetime import timedelta

    from .. import store
    from ..contracts import parse_utc

    def late():
        return deadline is not None and deadline - time.monotonic() < 0.5
    res = {"inserted": 0, "duplicate": 0, "revision": 0, "invalid": 0}
    recs = batch.records
    with _BusyCap(con, deadline) as busy:
        for i in range(0, len(recs), WRITE_CHUNK):
            if late():
                return {**res, "removed": 0, "stopped": "time budget (the rest next pass)"}
            busy.set()
            got = store.put_many(con, recs[i:i + WRITE_CHUNK])
            for k in res:
                res[k] += got.get(k, 0)
        if batch.quarantine and not late():
            busy.set()
            store.quarantine_many(con, batch.quarantine, league, observed_at)
        if not complete:
            return {**res, "removed": 0, "withdrawals": "skipped: partial or empty read",
                    "quarantined": len(batch.quarantine)}
        if late():
            return {**res, "removed": 0, "stopped": "time budget (withdrawals next pass)"}
        since = (parse_utc(observed_at) - timedelta(hours=WITHDRAW_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
        seen = {r.offer_id for r in recs} | set(getattr(batch, "capped", None) or ())
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
            if late():
                return {**res, "removed": removed, "stopped": "time budget (withdrawals next pass)"}
            busy.set()
            removed += store.put_many(con, stale[i:i + WRITE_CHUNK])["revision"]
    return {**res, "removed": removed, "quarantined": len(batch.quarantine)}


def run(con, leagues, budget_s=None, clock=None, observed_at=None, mem_ok=None):
    """The books job's exchanges step: every enabled venue for every league inside one hard time budget (one deadline
    passed down to every request and write chunk). A step that would start after the budget is skipped (reported); a
    failed read writes nothing (the last quote stays); any error is that venue's alone, the others still run.
    mem_ok() (optional) is checked before every request: False stops that venue's read (nothing written)."""
    from ..contracts import now_utc
    from .sources import resolver_from_store
    clock = clock or time.monotonic
    budget_s = BUDGET_S if budget_s is None else budget_s
    t0 = clock()
    deadline = time.monotonic() + budget_s

    def left():
        return min(budget_s - (clock() - t0), deadline - time.monotonic())
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
            dl = time.monotonic() + max(0.0, left())
            try:
                if v == "novig":
                    raw = novig_raw(league, timeout=20.0, deadline=dl, mem_ok=mem_ok)
                    b = novig_offers(raw, league, obs, resolver, teams_raw)
                    seen = {"events": len(raw["events"]), "markets": len(raw["markets"])}
                    complete = raw["complete"] and raw["items"] > 0
                else:
                    evs = gemini_raw(league, timeout=20.0, deadline=dl, mem_ok=mem_ok)
                    b = gemini_offers(evs, league, obs, resolver)
                    seen = {"events": len(evs)}
                    complete = evs.complete and evs.items > 0
            except Stop as e:
                lo[v] = {"ok": False, "skipped": str(e), "read_s": round(clock() - t, 3)}
                continue
            except (net.HttpError, ValueError) as e:
                lo[v] = {"ok": False, "error": str(e)[:160], "read_s": round(clock() - t, 3)}
                continue
            except Exception as e:                      # noqa: BLE001 - odd answer shape / cut body: this venue only
                lo[v] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}", "read_s": round(clock() - t, 3)}
                continue
            read_s = round(clock() - t, 3)
            if left() <= 0:
                lo[v] = {"ok": False, "skipped": "time budget (read, not written)", "read_s": read_s}
                continue
            t = clock()
            try:
                r = store_venue(con, v, league, b, obs, complete=complete,
                                deadline=time.monotonic() + max(0.0, left()))
            except Exception as e:                      # noqa: BLE001 - e.g. 'database is locked': this venue only
                lo[v] = {"ok": False, "error": f"write: {type(e).__name__}: {str(e)[:140]}", "read_s": read_s}
                continue
            r.update(seen, ok="stopped" not in r, offers=len(b.records),
                     resolved_players=sum(1 for x in b.records if x.player_id), read_s=read_s,
                     write_s=round(clock() - t, 3))
            if v == "novig":
                r["key"] = "set (not needed: public catalog)" if novig_key_present() else "not set (not needed)"
            lo[v] = r
    out["ran_s"] = round(clock() - t0, 3)
    return out


def _state_path():
    from .. import config
    return config.DATA_DIR / "books" / "exchanges_state.json"


def _failed(out):
    """A venue that failed or was skipped (not by its kill switch) in this pass."""
    return any(isinstance(v, dict) and v.get("ok") is False
               for lo in out.values() if isinstance(lo, dict) for v in lo.values())


def _write_state(p, state):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(p)


def run_due(con, leagues, now=None, budget_s=None, mem_ok=None):
    """run() when EVERY_S has passed since the last ATTEMPT (recorded before the pass, so a killed pass is never re-run
    in a tight loop) - or RETRY_S after a pass in which a venue failed or was skipped: ONE early retry (not chained), so
    a single bad pass does not age every row past the 15-minute rule. Else {"due": False, "next_in_s": ...}."""
    now = time.time() if now is None else now
    p = _state_path()
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        last, retry = float(st.get("last_at") or 0), bool(st.get("retry"))
    except (OSError, ValueError, AttributeError, TypeError):
        last, retry = 0.0, False
    every = RETRY_S if retry else EVERY_S
    if now - last < every:
        return {"due": False, "next_in_s": round(every - (now - last), 1)}
    try:
        _write_state(p, {"last_at": now})
    except OSError:
        return {"due": True, "skipped": "state file not writable"}
    out = run(con, leagues, budget_s=budget_s, mem_ok=mem_ok)
    if _failed(out) and not retry:
        try:
            _write_state(p, {"last_at": now, "retry": True})
        except OSError:
            pass
    return {"due": True, **out}
