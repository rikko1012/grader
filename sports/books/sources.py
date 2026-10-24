"""Market-source adapters -> canonical OfferSnapshot records (worker only; every request through ledger.reserve).

Polymarket (gamma API, public) - read 2026-10-23: events by tag_slug nba / nfl; a player prop is a market with
  sportsMarketType in PM_STAT, question "<Player>: <Stat> O/U <line>", outcomes ["Yes", "No"] (NBA) or ["Over",
  "Under"] (NFL), bestBid / bestAsk of the FIRST (Yes / Over) token, line, gameStartTime ("2026-10-23 23:00:00+00"), liquidityNum, volumeNum, updatedAt (a MARKET update
  time, not a quote time -> source_time_unknown, kept in extras). Event slug "nba-nyk-bos-2026-10-23" = league, away,
  home, US date -> resolved with the foundation's exact team aliases + ESPN event index. Contract text (description):
  YES / Over = more than the line; NO / Under = the line or fewer; full game incl. OT; a player who is inactive or
  does not play resolves NO / Under (NBA and NFL texts) -> a Polymarket YES price is UNCONDITIONAL on playing (the
  consensus rule mc-1 handles it, REGISTER.md 10).
The Odds API - foundation adapters.oddsapi_offers (documented shape) + this module's event resolution by full team
  names (exact ESPN display names) and the credit ledger. Key: ODDS_API_KEY (Render only).
Thunderpick / OddsPapi - CAPTURE-ONLY until verified (books/__init__.py): the answer's inventory (paths / market
  names / counts) and a sha256 are recorded; no offer is produced from an unseen format. OddsPapi's inventory is the
  unmetered account read of the guarded provider (books/oddspapi.py) - the account is shared with CS2, so capture
  never makes a billable OddsPapi request; offers come only from that provider once enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .. import adapters, config, identity, names, net, store
from ..contracts import OfferSnapshot, SideQuote, now_utc, utc
from . import SOURCES, ledger

GAMMA = "https://gamma-api.polymarket.com"
PM_STAT = {"nba": {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes",
                   "steals": "steals", "blocks": "blocks"},
           "nfl": {"passing_yards": "pass_yds", "rushing_yards": "rush_yds", "receiving_yards": "rec_yds",
                   "receptions": "rec", "passing_attempts": "pass_att", "passing_completions": "pass_cmp",
                   "rushing_attempts": "rush_att"}}
_Q = re.compile(r"^(?P<name>.+?): (?P<stat>[^:]+?) O/U (?P<line>-?\d+(?:\.\d+)?)$")
_SLUG = re.compile(r"^(?P<lg>[a-z]+)-(?P<away>[a-z0-9]+)-(?P<home>[a-z0-9]+)-(?P<day>\d{4}-\d{2}-\d{2})")
RULE_PM = ("polymarket-market-description: YES > line, NO <= line, full game incl. OT, inactive / DNP resolves NO, "
           "cancelled 50-50")
# Gamma pages of 20 events: one 100-event NBA page was ~29 MB of JSON / ~110 MB of Python objects (measured
# 2026-10-24), the ingest child's largest peak. Explicit stable order (id ascending, verified live 2026-10-24) and pages
# that overlap by PM_OVERLAP events, de-duplicated by id, so an event that closes mid-read and shifts the list cannot
# push another across a page boundary unseen. A read that reaches PM_MAX_PAGES with a full last page reports
# `truncated` (never a silent cut). end_date_max was tested and rejected - it dropped real game events (NFL 83 -> 66).
PM_PAGE = 20
PM_OVERLAP = 2
PM_MAX_PAGES = 70                                   # 70 x 18 new events = 1,260 >= the former 12 x 100 per league
_PM_EVENT = ("id", "slug", "title")
_PM_MARKET = ("id", "sportsMarketType", "question", "line", "outcomes", "bestBid", "bestAsk", "active", "closed",
              "acceptingOrders", "gameStartTime", "updatedAt", "liquidityNum", "volumeNum", "lastTradePrice")


def key_state():
    return {s: ("no key needed" if not v["key_env"] else ("key set" if os.environ.get(v["key_env"]) else
                                                          "key not set here")) for s, v in SOURCES.items()}


def _get(source, url, params=None, headers=None, cost=1.0):
    ok, why = ledger.reserve(source, cost, url.split("?")[0][-80:])
    if not ok:
        e = net.HttpError(None, url, f"budget: {why}")
        e.args = (f"budget refused ({why}) for {url.split('?')[0]}",)
        raise e
    try:
        r = net.get(url, params=params, headers=headers, timeout=30, tries=1)
    except net.HttpError as e:
        ledger.report(source, e.status)
        raise
    rem = r.headers.get("x-requests-remaining")
    ledger.report(source, r.status_code, float(rem) if rem not in (None, "") else None)
    return r


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------------- resolver
def resolver_from_store(con, league):
    """Teams + events + rosters from the canonical store (no network)."""
    teams_raw = []
    for row in store._latest_rows(con, "team", "league = ?", [league], None, 1000):
        t = store._rec(row)
        teams_raw.append({"id": t.team_id.split(":")[-1], "abbr": t.abbr, "name": t.name})
    teams = identity.Teams(league, teams_raw)
    events = [e for _, e in store.events(con, league)]
    rosters = identity.Rosters()
    players = {p.player_id: p for _, p in ((r["record_id"], store._rec(r)) for r in
                                            store._latest_rows(con, "player", "league = ?", [league], None, 100000))}
    rows = con.execute("SELECT r.payload FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records "
                       "WHERE late = 0 AND kind='membership' AND player_id LIKE ? GROUP BY source, source_key) z ON "
                       "r.source=z.source AND r.source_key=z.source_key AND r.revision=z.rv WHERE r.kind='membership'",
                       (f"{league}:%",)).fetchall()
    for row in rows:
        m = store._rec(row)
        if m.valid_to is None and m.player_id in players:
            rosters.add(m.team_id, m.player_id, players[m.player_id].name)
    return adapters.Resolver(league, teams, events, rosters), teams_raw


# ----------------------------------------------------------------------------------------------------- Polymarket
def _pm_trim(e, stats):
    """Only what polymarket_offers reads: the event's id / slug / title and its markets of this league's stats (the
    adapter skips every other sportsMarketType), each with the fields it uses."""
    out = {k: e.get(k) for k in _PM_EVENT}
    out["markets"] = [{k: m.get(k) for k in _PM_MARKET} for m in e.get("markets") or []
                      if m.get("sportsMarketType") in stats]
    return out


def polymarket_raw(league, info=None):
    """Every active Gamma event of the league's tag (game events kept, trimmed). info (dict) receives pages /
    events_seen / truncated."""
    out, seen, stats = [], set(), PM_STAT[league]
    step = PM_PAGE - PM_OVERLAP
    pages, full_last = 0, False
    for page in range(PM_MAX_PAGES):
        r = _get("polymarket", f"{GAMMA}/events", params={"tag_slug": league, "active": "true", "closed": "false",
                                                           "order": "id", "ascending": "true", "limit": PM_PAGE,
                                                           "offset": page * step})
        ev = r.json()
        del r
        pages += 1
        for e in ev:
            if e.get("id") in seen:
                continue
            seen.add(e.get("id"))
            if _SLUG.match(e.get("slug") or ""):
                out.append(_pm_trim(e, stats))
        full_last = len(ev) >= PM_PAGE
        del ev                                      # never two parsed pages alive at once
        if not full_last:
            break
    if info is not None:
        info.update(pages=pages, events_seen=len(seen), truncated=full_last and pages >= PM_MAX_PAGES)
    return out


def polymarket_offers(events, league, observed_at, resolver=None):
    b = adapters.Batch()
    stats = PM_STAT[league]
    for e in events:
        sm = _SLUG.match(e.get("slug") or "")
        if not sm or sm.group("lg") != league:
            continue
        away, home, day = sm.group("away").upper(), sm.group("home").upper(), sm.group("day")
        for m in e.get("markets") or []:
            smt = m.get("sportsMarketType")
            if smt not in stats:
                continue
            q = _Q.match((m.get("question") or "").strip())
            key = f"polymarket:{m.get('id')}"
            line = _f(m.get("line"))
            if not q or line is None or abs(float(q.group("line")) - line) > 1e-9:
                b.q("offer", "polymarket", key, "unreadable_question_or_line", {"question": m.get("question")})
                continue
            try:
                outs = json.loads(m.get("outcomes") or "[]")
            except ValueError:
                outs = []
            if outs not in (["Yes", "No"], ["Over", "Under"]):     # NBA props Yes/No, NFL props Over/Under
                b.q("offer", "polymarket", key, "outcomes_not_yes_no", {"outcomes": m.get("outcomes")})
                continue
            name = q.group("name").strip()
            bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
            open_ = bool(m.get("active")) and not m.get("closed") and bool(m.get("acceptingOrders"))
            over = SideQuote("over", bool(ask and 0 < ask < 1), "exchange_yes_no", bid=bid if bid else None,
                             ask=ask if ask and ask < 1 else None)
            under = SideQuote("under", bool(bid and 0 < bid < 1), "exchange_yes_no",
                              bid=round(1 - ask, 6) if ask and ask < 1 else None,
                              ask=round(1 - bid, 6) if bid and bid > 0 else None)
            start = None
            try:
                start = utc((m.get("gameStartTime") or "").replace(" ", "T").replace("+00", "+00:00"))
            except Exception:                                  # noqa: BLE001 - kept unknown, not invented
                start = None
            eid = pid = None
            extras = [("market_updated_at", m.get("updatedAt")), ("slug", e.get("slug")),
                      ("dnp_rule", "inactive / does not take the court resolves NO (price unconditional on playing)")]
            if resolver is not None:
                eid, why = resolver.event(day, away, home)
                # the slug's date is the UTC date: a game starting after midnight UTC (US evening) carries the NEXT
                # day. Same two teams, on the US date of the market's own gameStartTime (exact, never fuzzy).
                us_day = identity.us_date(start) if start else None
                if eid is None and us_day and us_day != day:
                    eid, why2 = resolver.event(us_day, away, home)
                    why = why if eid is None else None
                    if eid is not None:
                        day = us_day
                if eid is None:
                    extras.append(("unresolved_event", why))
                else:
                    pid, why_p = resolver.player(name, None, eid, day)
                    if pid is None:
                        extras.append(("unresolved_player", why_p))
            liq = (("liquidity", _f(m.get("liquidityNum"))), ("volume", _f(m.get("volumeNum"))),
                   ("last_price", _f(m.get("lastTradePrice"))))
            b.add(OfferSnapshot(
                platform="polymarket", offer_id=key, market_id=str(e.get("id")), league=league, event_id=eid,
                event_ref=e.get("title"), player_id=pid, player_name=name, player_key=names.key(name), team=None,
                stat=stats[smt], source_stat=smt, scope="full_game", line=line, sides=(over, under),
                variant="main", promotional=False, availability="open" if open_ else "suspended",
                rule_version=RULE_PM, listed_start_utc=start, listed_time_kind="start" if start else "unknown",
                source_updated_at=None, observed_at=observed_at, source_time_unknown=True, liquidity=liq,
                extras=tuple(extras), known_at=observed_at))
    return b


# ---------------------------------------------------------------------------------------------------- The Odds API
ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_MARKETS = {"nba": ["player_points", "player_rebounds", "player_assists", "player_threes"],
                "nfl": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions",
                        "player_rush_attempts"]}
ODDS_MAX_EVENTS = int(os.environ.get("SPORTS_ODDSAPI_MAX_EVENTS", "6"))      # per collection cycle
ODDS_HORIZON_H = float(os.environ.get("SPORTS_ODDSAPI_HORIZON_H", "36"))     # games starting within this window


def _oddsapi_order(evs, now_iso):
    """Not-started games within ODDS_HORIZON_H, the least recently fetched first (never fetched first of all), then the
    soonest - so a paced budget rotates through every game instead of re-reading the same six."""
    from datetime import datetime, timedelta
    end = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) + timedelta(hours=ODDS_HORIZON_H)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    up = [e for e in evs if e.get("id") and now_iso < (e.get("commence_time") or "") <= end]
    last = ledger.last_sent("oddsapi", [e["id"] for e in up])
    return sorted(up, key=lambda e: (last.get(e["id"]) or 0, e.get("commence_time") or ""))


def oddsapi_ingest(con, league, observed_at, resolver, teams_raw):
    k = os.environ.get("ODDS_API_KEY")
    if not k:
        return {"ok": False, "status": "key not set here"}
    lg = config.league(league)
    out = {"events": 0, "offers": 0, "credits": 0}
    try:
        evs = _get("oddsapi", f"{ODDS_API}/sports/{lg['odds_api']}/events", params={"apiKey": k}, cost=0).json()
    except net.HttpError as e:
        return {"ok": False, "error": str(e).replace(k, "***")[:160]}
    full = {t["name"]: t["abbr"] for t in teams_raw}
    b = adapters.Batch()
    for ev in _oddsapi_order(evs, observed_at)[:ODDS_MAX_EVENTS]:
        mk = ODDS_MARKETS[league]
        ok, why = ledger.paced("oddsapi", len(mk))
        if not ok:                                           # even spread of the month's credits: wait for the next hour
            out["paced"] = why
            break
        try:
            body = _get("oddsapi", f"{ODDS_API}/sports/{lg['odds_api']}/events/{ev['id']}/odds",
                        params={"apiKey": k, "regions": "us", "oddsFormat": "decimal", "markets": ",".join(mk)},
                        cost=len(mk)).json()
        except net.HttpError as e:
            out.setdefault("errors", []).append(str(e).replace(k, "***")[:120])
            break                                            # budget or provider refusal: stop this cycle
        out["credits"] += len(mk)
        out["events"] += 1
        bb = adapters.oddsapi_offers(body, lg, observed_at)
        start = adapters._ts(body.get("commence_time"))
        day = identity.us_date(start)
        away, home = full.get(body.get("away_team")), full.get(body.get("home_team"))
        eid, why = resolver.event(day, away, home) if away and home else (None, "team full name not an ESPN name")
        for o in bb.records:
            pid, why_p = resolver.player(o.player_name, None, eid, day) if eid else (None, why)
            extras = tuple(x for x in o.extras if x[0] != "unresolved_event")
            if eid is None:
                extras += (("unresolved_event", why),)
            elif pid is None:
                extras += (("unresolved_player", why_p),)
            d = {k2: v for k2, v in o.to_dict().items() if k2 != "kind"}
            d.update(event_id=eid, player_id=pid, extras=extras, sides=o.sides, liquidity=o.liquidity)
            b.add(OfferSnapshot(**d))
    res = store.put_many(con, b.records)
    out["offers"] = res["inserted"] + res["revision"]
    out["ok"] = True
    return out


# ------------------------------------------------------------------------------------- Thunderpick / OddsPapi capture
TP_BASE = "https://aggr.mightykiwi.xyz/api/partners-odds"
TP_PATHS = {"nba": ("sport/basketball", "basketball/nba", "basketball"), "nfl": ("sport/american-football",
                                                                                   "american-football/nfl",
                                                                                   "american-football")}
OP_BASE = "https://api.oddspapi.io/v4"


def capture(source, league):
    """Capture-only inventory of an unverified source (no offers). -> dict for coverage."""
    env = SOURCES[source]["key_env"]
    k = os.environ.get(env or "")
    if not k:
        return {"source": source, "status": "key not set here"}
    inv = {"source": source, "status": "captured (unverified format)", "answers": []}
    try:
        if source == "thunderpick":
            for p in TP_PATHS[league]:
                try:
                    r = _get("thunderpick", f"{TP_BASE}/{p}", headers={"api-key": k, "Accept": "application/json"})
                    body = r.json()
                    names_ = sorted({m.get("name") for e in (body or []) for m in (e.get("markets") or [])})[:80]
                    inv["answers"].append({"path": p, "events": len(body or []), "market_names": names_,
                                           "sha256": hashlib.sha256(r.content).hexdigest()})
                except net.HttpError as e:
                    inv["answers"].append({"path": p, "error": f"HTTP {e.status}"})
        else:
            # the OddsPapi account is SHARED with CS2: the inventory is the unmetered GET /v4/account through the
            # guarded provider (books/oddspapi.py) - no billable request, so nothing here can spend CS2's quota
            from .base import FetchResult
            from .oddspapi import BOOK_PRIORITY, OddsPapi, books_for, quota_readable, subscription
            res = FetchResult()
            acc = OddsPapi().account(res)
            if not isinstance(acc, dict):
                inv["status"] = f"error: {res.error}"
            else:
                sub = subscription(acc) or {}
                inv["answers"].append({"path": "/account (unmetered)", "sport_ids": sub.get("sport_ids"),
                                       "player_props": {b: bool(((sub.get("bookmakers") or {}).get(b) or {})
                                                                .get("has_player_props")) for b in BOOK_PRIORITY},
                                       "books_nba": books_for(acc, "basketball"),
                                       "books_nfl": books_for(acc, "football"), "quota_readable": quota_readable(acc)})
    except net.HttpError as e:
        inv["status"] = f"error: {str(e).replace(k, '***')[:120]}"
    return inv


# --------------------------------------------------------------------------------------------------------- ingest
def polymarket_ingest(con, league, observed_at, resolver):
    """One Polymarket read of a league -> stored offers (the one implementation; the canonical ingest loop and the
    manual `train books` job both call it)."""
    try:
        info = {}
        evs = polymarket_raw(league, info)
        b = polymarket_offers(evs, league, observed_at, resolver)
        res = store.put_many(con, b.records)
        store.quarantine_many(con, b.quarantine, league, observed_at)
        return {"events": len(evs), "offers": len(b.records), "pages": info.get("pages"),
                "events_seen": info.get("events_seen"), "truncated": info.get("truncated"),
                **{k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")},
                "quarantined": len(b.quarantine), "resolved_players": sum(1 for r in b.records if r.player_id)}
    except net.HttpError as e:
        return {"ok": False, "error": str(e)[:160]}


def ingest(con, league, sources=("polymarket", "oddsapi", "thunderpick", "oddspapi")):
    """One cycle for the extra market sources of one league. Stores offers; returns per-source coverage."""
    observed_at = now_utc()
    resolver, teams_raw = resolver_from_store(con, league)
    out = {"league": league, "observed_at": observed_at, "keys": key_state()}
    if "polymarket" in sources:
        out["polymarket"] = polymarket_ingest(con, league, observed_at, resolver)
    if "oddsapi" in sources:
        out["oddsapi"] = oddsapi_ingest(con, league, observed_at, resolver, teams_raw)
    for s in ("thunderpick", "oddspapi"):
        if s in sources:
            out[s] = capture(s, league)
    out["budget"] = ledger.usage()
    return out
