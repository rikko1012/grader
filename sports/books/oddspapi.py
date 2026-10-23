"""OddsPapi (oddspapi.io, v4): DraftKings / BetMGM / Caesars player props through the OddsPapi account the site already
holds for CS2. **Disabled by default**, and it can never spend CS2's share of that account's quota.

Why a guard: the account (key ODDSPAPI_API_KEY, Railway name odds_papi_api - read exactly as the CS2 module reads it,
never copied, never printed) is shared with CS2's oddspapi.py, and its free tier is small (250 requests a month per the
OddsPapi site; the real limit is whatever GET /v4/account reports). Every billable call Sports makes must pass:
  1. SPORTS_ODDSPAPI=1 and the key present (otherwise the provider is skipped and says which variable is missing);
  2. an explicit Sports allocation SPORTS_ODDSPAPI_MONTHLY_REQUESTS (default 0 = nothing), reserved ATOMICALLY in the
     Sports store (one conditional UPDATE per call), so Sports workers together never exceed it;
  3. a fresh look at the WHOLE account (GET /v4/account, which the docs say is not metered): the call is made only if
     the account still has more requests left than CS2's floor = max(SPORTS_ODDSPAPI_CS2_FLOOR (default 50),
     CS2's unused share = limit - Sports allocation - CS2's use this month). Sports stops first; CS2 is never starved.
A 429 / Retry-After from OddsPapi is a hold (net.py): no request before the provider's time, persisted by the worker.

Calls (docs read 2026-10-23; sportId 11 basketball, 14 American football):
  GET /v4/account                                   unmetered: limit, count, per-book has_player_props, sport_ids
  GET /v4/tournaments?sportId=                      billable, cached 30 days (NBA / NFL tournament ids)
  GET /v4/participants?sportId=                     billable, cached 30 days (team names by participant id)
  GET /v4/markets                                   billable, cached 7 days (market id -> name, playerProp, period,
                                                    outcomes); only player-prop markets of sports 11 / 14 are kept
  GET /v4/odds-by-tournaments?tournamentIds=&bookmakers=a,b,c   billable: EVERY upcoming game of the league with up
                                                    to 3 books in ONE request - the only data call
Offer fields: price (decimal), priceAmerican, playerName, active, bookmakerOutcomeId ("25.5/over" - line and side),
changedAt / bookmakerChangedAt (provider time), mainLine. NOT verified until the owner's coverage check (README):
which books carry NBA / NFL props on this account, the player-prop market names, and whether request_count resets
monthly. Unknown market names map to nothing and are listed by `books-oddspapi catalog`, never guessed into a stat.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from .. import net
from ..lines import utc_iso
from .base import ALTERNATE, STANDARD, Event, FetchResult, Provider, make_offer
from .store import dt, iso

BASE = "https://api.oddspapi.io/v4"
SPORT = {"basketball": 11, "football": 14}
TOURNAMENT = {"nba": "NBA", "nfl": "NFL"}                 # tournamentName, matched whole (category USA preferred)
BOOK_PRIORITY = ("draftkings", "betmgm", "caesars", "fanduel", "betrivers")   # the 3 refused books first
MAX_BOOKS_PER_CALL = 3                                     # odds-by-tournaments limit (docs)
CACHE_DAYS = {"tournaments": 30, "participants": 30, "markets": 7}
ACCOUNT_TTL_S = 60
MIN_INTERVAL_MIN = 180                                     # never read a league more often than every 3 h
PERIODS = {"fulltime": "game", "full time": "game", "ft": "game"}   # anything else (halves, quarters) is not graded
_OUTCOME = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(over|under)\s*$", re.I)
_NORM = re.compile(r"[^a-z0-9+ ]+")

# Player-prop market names, normalised (lower case, "player " prefix and "(incl. overtime)" / "over/under" dropped),
# matched WHOLE. These are the stat words books use; OddsPapi's own names are confirmed by `books-oddspapi catalog`
# against a real key before the provider is enabled - an unmatched name grades nothing.
MARKET_NAMES = {
    "basketball": {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes made": "threes",
                   "3 pointers made": "threes", "three pointers made": "threes", "steals": "steals",
                   "blocks": "blocks", "turnovers": "turnovers", "points + rebounds + assists": "pra",
                   "points + rebounds": "pr", "points + assists": "pa", "rebounds + assists": "ra",
                   "steals + blocks": "stocks", "blocks + steals": "stocks"},
    "football": {"passing yards": "pass_yds", "passing attempts": "pass_att", "passing completions": "pass_cmp",
                 "passing touchdowns": "pass_td", "interceptions": "pass_int", "passing interceptions": "pass_int",
                 "rushing yards": "rush_yds", "rushing attempts": "rush_att", "receptions": "rec",
                 "receiving yards": "rec_yds", "rushing + receiving yards": "rush_rec_yds",
                 "passing + rushing yards": "pass_rush_yds"},
}


def api_key():
    """The CS2 module's key lookup, verbatim: ODDSPAPI_API_KEY, or the Railway name odds_papi_api."""
    return os.environ.get("ODDSPAPI_API_KEY") or os.environ.get("odds_papi_api")


def enabled():
    return os.environ.get("SPORTS_ODDSPAPI") == "1"


def allocation():
    try:
        return max(0, int(os.environ.get("SPORTS_ODDSPAPI_MONTHLY_REQUESTS", "0")))
    except ValueError:
        return 0


def cs2_floor():
    try:
        return max(0, int(os.environ.get("SPORTS_ODDSPAPI_CS2_FLOOR", "50")))
    except ValueError:
        return 50


def _redact(msg):
    k = api_key() or ""
    return msg.replace(k, "***") if k else msg


def norm_market(name):
    n = (name or "").lower().replace("(incl. overtime)", "").replace("over/under", "").replace("o/u", "")
    n = _NORM.sub(" ", n.replace("&", "+").replace(",", " +"))
    n = re.sub(r"\s*\+\s*", " + ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n[len("player "):] if n.startswith("player ") else n


def map_market(family, name):
    return MARKET_NAMES.get(family, {}).get(norm_market(name))


# ------------------------------------------------------------------------------------------- the quota guard

def _month(now):
    return now.strftime("%Y-%m")


def used_this_month(db, now):
    from . import store
    try:
        return int(store.setting(db, f"oddspapi_used:{_month(now)}", "0"))
    except ValueError:
        return 0


def subscription(account):
    """The active subscription of a /v4/account answer (the current one when named)."""
    subs = [s for s in (account or {}).get("subscriptions") or [] if s.get("is_active")]
    cur = (account or {}).get("current_subscription_id")
    return next((s for s in subs if s.get("subscription_id") == cur), subs[0] if subs else None)


def quota_readable(account):
    """True only for an account answer whose active subscription reports numeric request_limit and request_count."""
    sub = subscription(account) if isinstance(account, dict) else None
    return bool(sub) and all(isinstance(sub.get(k), (int, float)) and not isinstance(sub.get(k), bool)
                             for k in ("request_limit", "request_count"))


def guard(account, used, alloc, floor):
    """-> (ok, reason, numbers). ok = one more Sports request leaves the account above CS2's floor and Sports inside
    its allocation."""
    sub = subscription(account)
    if not sub:
        return False, "no active OddsPapi subscription on the account", {}
    limit, count = sub.get("request_limit"), sub.get("request_count")
    if limit is None or count is None:
        return False, "account did not report request_limit / request_count", {}
    remaining = limit - count
    cs2_used = max(0, count - used)
    cs2_unused_share = max(0, (limit - alloc) - cs2_used)
    need = max(floor, cs2_unused_share)
    nums = {"limit": limit, "count": count, "remaining": remaining, "sports_used": used, "sports_allocation": alloc,
            "cs2_used_est": cs2_used, "cs2_floor": need}
    if used >= alloc:
        return False, f"Sports allocation used ({used}/{alloc} this month)", nums
    if remaining - 1 < need:
        return False, f"account has {remaining} requests left; {need} are kept for CS2", nums
    return True, "", nums


def reserve(db, now, alloc):
    """Atomically take one request of the Sports allocation -> True when taken (never more than alloc a month)."""
    key = f"oddspapi_used:{_month(now)}"
    db.execute("INSERT INTO sports_book_settings (key, value, updated_at) VALUES (?, '0', ?) ON CONFLICT (key) DO "
               "NOTHING", (key, iso(now)))
    cur = db.execute("UPDATE sports_book_settings SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT), updated_at = ? "
                     "WHERE key = ? AND CAST(value AS INTEGER) < ?", (iso(now), key, alloc))
    db.commit()
    return cur.rowcount == 1


# ------------------------------------------------------------------------------------------------------ parsing

def _fixtures(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("fixtures", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        if body.get("fixtureId"):
            return [body]
    return []


def parse_markets(body):
    """/v4/markets -> {market_id: {...}} for player-prop markets of basketball / American football."""
    out = {}
    for m in body if isinstance(body, list) else []:
        if m.get("playerProp") and m.get("sportId") in SPORT.values():
            out[str(m.get("marketId"))] = {
                "name": m.get("marketName"), "sport": m.get("sportId"), "period": m.get("period"),
                "handicap": m.get("handicap"), "type": m.get("marketType"),
                "outcomes": {str(o.get("outcomeId")): o.get("outcomeName") for o in m.get("outcomes") or []}}
    return out


def parse_events(body, lg, participants):
    out = []
    for f in _fixtures(body):
        if f.get("statusId") not in (0, None):              # pre-game only
            continue
        p1, p2 = participants.get(str(f.get("participant1Id"))), participants.get(str(f.get("participant2Id")))
        if not (p1 and p2 and f.get("fixtureId")):
            continue
        # the docs do not say which participant is home: home / away are left empty; games are matched to ESPN by
        # both nicknames, whatever their order (events.match)
        out.append(Event(str(f["fixtureId"]), lg["key"], f"{p1} vs {p2}", None, None, utc_iso(f.get("startTime"))))
    return out


def parse_props(body, lg, catalog, events):
    """odds-by-tournaments -> offers (only player-prop markets whose name and period map exactly)."""
    fam = lg["family"]
    ev_by = {e.provider_event_id: e for e in events}
    out, unmapped = [], set()
    for f in _fixtures(body):
        ev = ev_by.get(str(f.get("fixtureId")))
        if not ev:
            continue
        for slug, bk in (f.get("bookmakerOdds") or {}).items():
            if slug not in BOOK_PRIORITY:
                continue
            for mid, mk in (bk.get("markets") or {}).items():
                info = catalog.get(str(mid))
                if not info or info["sport"] != SPORT[fam]:
                    continue
                stat, period = map_market(fam, info["name"]), PERIODS.get((info.get("period") or "").lower())
                if not stat or not period:
                    unmapped.add(f"{info['name']} [{info.get('period')}]")
                    continue
                for oid, oc in (mk.get("outcomes") or {}).items():
                    for pid, sel in (oc.get("players") or {}).items():
                        m = _OUTCOME.match(sel.get("bookmakerOutcomeId") or "")
                        side = (m.group(2).lower() if m else (info["outcomes"].get(str(oid)) or "").lower())
                        line = float(m.group(1)) if m else info.get("handicap")
                        if side not in ("over", "under") or line is None or not sel.get("playerName") \
                                or sel.get("price") is None:
                            continue
                        active = sel.get("active") and mk.get("marketActive", True) and not bk.get("suspended")
                        out.append(make_offer(
                            provider="oddspapi", book=slug, jurisdiction="us", league=lg["key"],
                            provider_event_id=ev.provider_event_id, event_name=ev.name, home=ev.home, away=ev.away,
                            start=ev.start, player=sel["playerName"], provider_player_id=str(pid), market=stat,
                            stat_label=info["name"], period=period, line=line, side=side,
                            price_american=sel.get("priceAmerican"), price_decimal=sel.get("price"),
                            market_type=STANDARD if sel.get("mainLine") else ALTERNATE,
                            status="open" if active else "suspended", provider_market_id=str(mid),
                            provider_selection_id=sel.get("bookmakerOutcomeId"),
                            provider_updated_at=utc_iso(sel.get("bookmakerChangedAt") or sel.get("changedAt"))))
    return out, sorted(unmapped)


def books_for(account, family):
    """Up to 3 books, priority order, whose subscription entry says has_player_props and whose sport is included."""
    sub = subscription(account) or {}
    if SPORT[family] not in (sub.get("sport_ids") or []):
        return []
    bm = sub.get("bookmakers") or {}
    return [b for b in BOOK_PRIORITY if (bm.get(b) or {}).get("has_player_props")][:MAX_BOOKS_PER_CALL]


# ------------------------------------------------------------------------------------------------------ provider

class OddsPapi(Provider):
    """kind "feed_league": one billable request reads every upcoming game of a league (the worker calls read())."""
    name = "oddspapi"
    books = ("draftkings", "betmgm", "caesars", "fanduel", "betrivers")
    kind = "feed_league"

    def configured(self):
        if not enabled():
            return False, "disabled: SPORTS_ODDSPAPI is not 1 (off until coverage and quota are verified)"
        if not api_key():
            return False, "ODDSPAPI_API_KEY is not set on this service"
        if not allocation():
            return False, "SPORTS_ODDSPAPI_MONTHLY_REQUESTS is 0 (no Sports share of the OddsPapi quota)"
        return True, ""

    # --- HTTP ---------------------------------------------------------------------------------------------
    def _raw(self, path, params, res):
        try:
            r = net.get(f"{BASE}{path}", params={"apiKey": api_key(), **params}, tries=1)
        except net.HttpError as e:
            res.requests += int(not e.held)
            res.http_status, res.error = e.status, _redact(f"{e} {e.body[:120]}")
            res.blocked, res.not_before = e.status in (401, 403), e.not_before_iso
            return None
        res.requests += 1
        res.http_status = r.status_code
        try:
            return r.json()
        except ValueError:
            res.error = f"{path}: answer is not JSON"
            return None

    def account(self, res=None):
        """GET /v4/account (unmetered) - the api_key field of the answer is dropped before anything else sees it."""
        res = res or FetchResult()
        body = self._raw("/account", {}, res)
        if isinstance(body, dict):
            body.pop("api_key", None)
        return body

    def _billable(self, db, path, params, res, now, account):   # noqa: ARG002 (the earlier reading is never used)
        # a FRESH look at the whole account before every billable call - and fail CLOSED: a failed, timed-out or
        # malformed answer (no active subscription with numeric request_limit / request_count) means no request,
        # never a fall back to the earlier reading
        fresh = self.account(res)
        if not quota_readable(fresh):
            res.budget_denied = "fresh /v4/account read failed or carried no quota numbers: no billable request"
            return None
        ok, why, nums = guard(fresh, used_this_month(db, now), allocation(), cs2_floor())
        if not ok:
            res.budget_denied = why
            return None
        if not reserve(db, now, allocation()):
            res.budget_denied = f"Sports allocation used ({allocation()} this month)"
            return None
        res.credits_used = (res.credits_used or 0) + 1      # counted by OddsPapi whatever the answer (docs)
        return self._raw(path, params, res)

    def _cached(self, db, name, path, params, res, now, account):
        from . import store
        key = f"oddspapi_cache:{name}"
        hit = store.setting(db, key)
        if hit:
            d = json.loads(hit)
            if dt(d["at"]) > now - timedelta(days=CACHE_DAYS[name.split(":")[0]]):
                return d["data"]
        body = self._billable(db, path, params, res, now, account)
        if body is None:
            return None
        data = parse_markets(body) if name == "markets" else body
        store.set_setting(db, key, json.dumps({"at": iso(now), "data": data}), now=now)
        return data

    def catalog(self, db, lg, now, account, res):
        """-> (tournament_id, participants {id: name}, markets {id: info}) or None (res says why)."""
        sport = SPORT[lg["family"]]
        tours = self._cached(db, f"tournaments:{sport}", "/tournaments", {"sportId": sport}, res, now, account)
        if tours is None:
            return None
        want = [t for t in tours if t.get("tournamentName") == TOURNAMENT[lg["key"]]]
        want = [t for t in want if (t.get("categoryName") or "").upper() in ("USA", "UNITED STATES")] or want
        if len(want) != 1:
            res.error = f"no single OddsPapi tournament named {TOURNAMENT[lg['key']]} ({len(want)} found)"
            return None
        parts = self._cached(db, f"participants:{sport}", "/participants", {"sportId": sport}, res, now, account)
        mkts = self._cached(db, "markets", "/markets", {}, res, now, account)
        if parts is None or mkts is None:
            return None
        return want[0]["tournamentId"], {str(k): v for k, v in parts.items()}, mkts

    def read(self, db, lg, now=None):
        """One league read -> FetchResult(events, offers). Spends at most 1 request (plus catalog refreshes) and only
        through the guard."""
        now = now or datetime.now(timezone.utc)
        res = FetchResult()
        account = self.account(res)
        if not isinstance(account, dict):
            res.error = res.error or "GET /v4/account failed"
            return res
        if not quota_readable(account):
            res.budget_denied = "/v4/account carried no quota numbers: no billable request"
            return res
        books = books_for(account, lg["family"])
        if not books:
            res.budget_denied = f"the account has no player-prop entitlement for these books / sport {lg['key']}"
            return res
        cat = self.catalog(db, lg, now, account, res)
        if cat is None:
            return res
        tid, parts, mkts = cat
        body = self._billable(db, "/odds-by-tournaments", {"tournamentIds": tid, "bookmakers": ",".join(books)}, res,
                              now, account)
        if body is None:
            return res
        res.events = parse_events(body, lg, parts)
        res.offers, res.unmapped = parse_props(body, lg, mkts, res.events)
        return res

    def interval_min(self, db, leagues_today, now):
        """Minutes until this league's next read: the Sports allocation left this month spread over the days left
        and the leagues with games, never more often than MIN_INTERVAL_MIN."""
        left = allocation() - used_this_month(db, now)
        if left <= 0:
            nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=5, second=0, microsecond=0)
            return max(MIN_INTERVAL_MIN, int((nxt - now).total_seconds() // 60))
        days_left = max(1, ((now.replace(day=1) + timedelta(days=32)).replace(day=1) - now).days)
        per_day = left / days_left / max(1, leagues_today)
        return max(MIN_INTERVAL_MIN, int(24 * 60 / max(per_day, 1e-6)))

    # the per-game interface is not used for this provider (the worker calls read())
    def events(self, lg):
        return FetchResult(error="oddspapi reads whole leagues: use read()")

    def props(self, lg, event):
        return FetchResult(error="oddspapi reads whole leagues: use read()")

