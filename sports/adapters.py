"""Source adapters: raw source answers (fetched by the data branch's lines / kalshi / espn / oddsapi modules) ->
canonical contract records (sports/contracts) + quarantine entries with reasons.

Every adapter is a pure function of (raw body, league, observed_at, resolver): no network, no storage, deterministic.
The worker (sports/worker.py) fetches, calls the adapter and stores the Batch.

What is typed vs quarantined
- typed (OfferSnapshot, even when not gradable): every line we can read reliably, with its real scope, variant,
  promotional flag, per-side availability and unknown stats as stat=None. capabilities.gate() decides gradability.
- quarantined (never stored as canonical, counted by reason): unreadable line / player / start, in-game (live) lines,
  multi-player offers, unknown scope, unknown wager-type flags, conflicting scope signals, invalid contract records.
Nothing is ever substituted: an unknown stat is not mapped to a neighbour, a missing side is NOT OFFERED (not 50/50).

Times: observed_at = our server read time (the same for the whole batch); source_updated_at = the source's own update
time when it gives one (PrizePicks projection updated_at, Underdog option updated_at, The Odds API last_update);
Kalshi's market updated_time does NOT move with quotes or trades (2026-10-23: 21 h old on markets with 24 h volume),
so Kalshi quotes are source_time_unknown and the field is kept only as extras["kalshi_updated_time"].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import identity, kalshi as kalshi_src, markets, names
from .contracts import (ContractError, ContextSnapshot, Event, Membership, OfferSnapshot, Player, SideQuote, SourceId,
                        StatObservation, Team, Reschedule, utc)
from .payouts import UD_TABLE_VERSION

ADAPTER_VERSION = "ad-1"
RULE_VERSIONS = {
    "prizepicks": "pp-help-2026-10-19; payouts unverified (in-app)",
    "underdog": f"ud-help-10982402/{UD_TABLE_VERSION}",
    "kalshi": "kalshi-contract-terms (series contract_terms_url; DNP settles to a fair price)",
    "oddsapi": "book house rules (not read)",
}
FAMILY = {"nba": "basketball", "wnba": "basketball", "cbb": "basketball", "nfl": "football", "cfb": "football"}


@dataclass
class Batch:
    records: list = field(default_factory=list)
    quarantine: list = field(default_factory=list)          # dicts: kind, source, source_key, reason, payload

    def q(self, kind, source, key, reason, payload=None):
        self.quarantine.append({"kind": kind, "source": source, "source_key": str(key) if key is not None else None,
                                "reason": reason, "payload": payload})

    def add(self, rec):
        errs = rec.validate()
        if errs:
            src, key = rec.identity()
            self.q(rec.KIND, src, key, "invalid: " + "; ".join(errs)[:160], rec.to_dict())
            return None
        self.records.append(rec)
        return rec

    def extend(self, other):
        self.records += other.records
        self.quarantine += other.quarantine
        return self

    def reason_counts(self):
        out = {}
        for x in self.quarantine:
            k = (x["source"], x["reason"].split(":")[0])
            out[k] = out.get(k, 0) + 1
        return out


class Resolver:
    """Canonical event + player lookups for one league (built from stored ESPN events and rosters)."""

    def __init__(self, league, teams: identity.Teams, events=(), rosters: identity.Rosters | None = None):
        self.league, self.teams = league, teams
        self.index = identity.EventIndex(list(events), teams)
        self.rosters = rosters or identity.Rosters()

    def event(self, day, away, home):
        return self.index.resolve(day, away, home)

    def player(self, name, team_abbr, event_id, day=None):
        ev = self.index.events.get(event_id) if event_id else None
        if ev is None:
            return None, "event unresolved"
        if team_abbr:
            t = self.teams.resolve(team_abbr, day)
            if t is None:
                return None, f"team {team_abbr} not mapped"
            if t not in (ev.home_team_id, ev.away_team_id):
                return None, f"team {team_abbr} is not in this event (trade or wrong game)"
            return self.rosters.resolve(name, [t])
        return self.rosters.resolve(name, [ev.home_team_id, ev.away_team_id])


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ts(x):
    try:
        return utc(x)
    except ContractError:
        return None


def _resolve(resolver, lg, start, away, home, name, team):
    """-> (event_id, player_id, extras)."""
    extras = []
    if resolver is None:
        return None, None, (("unresolved", "no resolver"),)
    day = identity.us_date(start)
    eid, why = resolver.event(day, away, home)
    if eid is None:
        extras.append(("unresolved_event", why))
        return None, None, tuple(extras)
    pid, why = resolver.player(name, team, eid, day)
    if pid is None:
        extras.append(("unresolved_player", why))
    return eid, pid, tuple(extras)


# ------------------------------------------------------------------------------------------------------- PrizePicks
_PP_SCOPE = {None: "full_game", "Full": "full_game", "Full Game": "full_game", "1H": "first_half", "2H": "second_half",
             "1Q": "q1", "2Q": "q2", "3Q": "q3", "4Q": "q4", "1st Half": "first_half", "1st Quarter": "q1"}
_STAT_PREFIX = {"1H ": "first_half", "2H ": "second_half", "1Q ": "q1", "2Q ": "q2", "3Q ": "q3", "4Q ": "q4"}
_PP_VARIANT = {"standard": "standard", "goblin": "goblin", "demon": "demon"}


def pp_offers(body, lg, observed_at, resolver=None) -> Batch:
    fam, league = FAMILY[lg["key"]], lg["key"]
    b = Batch()
    inc = {(i.get("type"), i.get("id")): i.get("attributes") or {} for i in body.get("included") or []}
    for p in body.get("data") or []:
        a, rel = p.get("attributes") or {}, p.get("relationships") or {}
        rid = lambda k: ((rel.get(k) or {}).get("data") or {}).get("id")      # noqa: E731
        key = p.get("id")
        player = inc.get(("new_player", rid("new_player")), {})
        name = player.get("display_name") or player.get("name")
        ptype = inc.get(("projection_type", rid("projection_type")), {}).get("name") or a.get("projection_type")
        dur = inc.get(("duration", rid("duration")), {}).get("name")
        line = _f(a.get("line_score"))
        start = _ts(a.get("start_time"))
        stat_label = a.get("stat_type") or ""
        if a.get("in_game") or a.get("is_live") or a.get("status") not in (None, "pre_game"):
            b.q("offer", "prizepicks", key, "in_game_or_not_pre_game", {"status": a.get("status")})
            continue
        if player.get("combo") or (ptype and ptype != "Single Stat"):
            b.q("offer", "prizepicks", key, "multi_player_or_non_single_stat", {"projection_type": ptype})
            continue
        if line is None or not name or not start:
            b.q("offer", "prizepicks", key, "missing_line_player_or_start",
                {"line": a.get("line_score"), "name": name, "start": a.get("start_time")})
            continue
        scope = _PP_SCOPE.get(dur, "unknown")
        base_label = stat_label
        for pre, sc in _STAT_PREFIX.items():
            if stat_label.startswith(pre):
                if scope not in ("full_game", sc) and dur is not None:
                    scope = "conflict"
                else:
                    scope, base_label = sc, stat_label[len(pre):]
        if scope in ("unknown", "conflict"):
            b.q("offer", "prizepicks", key, f"scope_{scope}", {"duration": dur, "stat": stat_label})
            continue
        wager = a.get("allowed_wager_types")
        if wager not in (None, "under_or_over", "over", "under"):
            b.q("offer", "prizepicks", key, "unknown_wager_types", {"allowed_wager_types": wager})
            continue
        stat = markets.PP.get(fam, {}).get(base_label if scope == "full_game" else base_label)
        game = inc.get(("game", rid("game")), {})
        teams = ((game.get("metadata") or {}).get("game_info") or {}).get("teams") or {}
        away, home = (teams.get("away") or {}).get("abbreviation"), (teams.get("home") or {}).get("abbreviation")
        team = player.get("team")
        eid, pid, extras = _resolve(resolver, lg, start, away, home, name, team)
        promo = bool(a.get("is_promo")) or a.get("flash_sale_line_score") is not None
        if a.get("flash_sale_line_score") is not None:
            extras += (("flash_sale_line_score", _f(a.get("flash_sale_line_score"))),)
        upd = _ts(a.get("updated_at"))
        b.add(OfferSnapshot(
            platform="prizepicks", offer_id=f"pp:{key}", market_id=a.get("group_key"), league=league, event_id=eid,
            event_ref=f"{away} @ {home}" if away and home else a.get("description"), player_id=pid,
            player_name=name, player_key=names.key(name), team=team, stat=stat, source_stat=stat_label, scope=scope,
            line=line,
            sides=(SideQuote("over", wager in (None, "under_or_over", "over")),
                   SideQuote("under", wager in (None, "under_or_over", "under"))),
            variant=_PP_VARIANT.get(a.get("odds_type") or "standard", "unknown"), promotional=promo,
            availability="open", rule_version=RULE_VERSIONS["prizepicks"], listed_start_utc=start,
            listed_time_kind="start", source_updated_at=upd, observed_at=observed_at, source_time_unknown=upd is None,
            extras=extras, known_at=observed_at))
    return b


# --------------------------------------------------------------------------------------------------------- Underdog
_UD_PERIOD = re.compile(r"^period_(\d)(?:_(\d))?_(.+)$")
_UD_HALF = re.compile(r"^(first_half|second_half|half_1|half_2)_(.+)$")


def _ud_scope(stat):
    """Underdog period prefixes: period_1_ = 1st quarter, period_1_2_ = 1st + 2nd quarter (first half),
    period_3_4_ = second half. Anything else with a period prefix is 'unknown' (quarantined), never full game."""
    s = stat or ""
    m = _UD_PERIOD.match(s)
    if m:
        a, b, rest = m.group(1), m.group(2), m.group(3)
        if b is None:
            return {"1": "q1", "2": "q2", "3": "q3", "4": "q4"}.get(a, "unknown"), rest
        return {("1", "2"): "first_half", ("3", "4"): "second_half"}.get((a, b), "unknown"), rest
    m = _UD_HALF.match(s)
    if m:
        return ("first_half" if m.group(1) in ("first_half", "half_1") else "second_half"), m.group(2)
    if s.startswith("period_") and not s.startswith("period_first_") and not s.startswith("period_last_"):
        return "unknown", s
    return "full_game", s


def ud_offers(body, lg, observed_at, resolver=None) -> Batch:
    fam, league = FAMILY[lg["key"]], lg["key"]
    b = Batch()
    apps = {x["id"]: x for x in body.get("appearances") or []}
    players = {x["id"]: x for x in body.get("players") or []}
    games = {x["id"]: x for x in (body.get("games") or []) + (body.get("solo_games") or [])}
    for ln in body.get("over_under_lines") or []:
        key = ln.get("id")
        ou = ln.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        value = _f(ln.get("stat_value"))
        if ln.get("live_event"):
            b.q("offer", "underdog", key, "in_game_or_not_pre_game", {"live_event": True})
            continue
        if ou.get("category") != "player_prop":
            b.q("offer", "underdog", key, "not_a_player_prop", {"category": ou.get("category")})
            continue
        app = apps.get(st.get("appearance_id")) or {}
        pl = players.get(app.get("player_id")) or {}
        gm = games.get(app.get("match_id")) or {}
        name = " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x)
        start = _ts(gm.get("scheduled_at"))
        if value is None or not name or not start:
            b.q("offer", "underdog", key, "missing_line_player_or_start",
                {"stat_value": ln.get("stat_value"), "name": name, "start": gm.get("scheduled_at")})
            continue
        scope, base = _ud_scope(st.get("stat"))
        if scope == "unknown":
            b.q("offer", "underdog", key, "scope_unknown", {"stat": st.get("stat")})
            continue
        stat = markets.UD.get(fam, {}).get(base)
        sides, extras, upd = [], [], []
        for side_name, choice in (("over", "higher"), ("under", "lower")):
            o = next((x for x in ln.get("options") or [] if x.get("choice") == choice), None)
            if o and o.get("status") in (None, "active"):
                sides.append(SideQuote(side_name, True, "payout_multiplier", multiplier=_f(o.get("payout_multiplier"))))
                if o.get("decimal_price") is not None:
                    extras.append((f"ud_undocumented_decimal_{side_name}", _f(o.get("decimal_price"))))
                if _ts(o.get("updated_at")):
                    upd.append(_ts(o.get("updated_at")))
            else:
                sides.append(SideQuote(side_name, False))
        title = gm.get("abbreviated_title") or gm.get("title") or ""
        away, home = identity.parse_game(title)
        team = None
        if app.get("team_id") and app.get("team_id") == gm.get("home_team_id"):
            team = home
        elif app.get("team_id") and app.get("team_id") == gm.get("away_team_id"):
            team = away
        eid, pid, ex2 = _resolve(resolver, lg, start, away, home, name, team)
        if gm.get("rescheduled_from"):
            extras.append(("rescheduled_from", str(gm.get("rescheduled_from"))))
        promo = ln.get("non_discounted_stat_value") is not None
        if promo:
            extras.append(("non_discounted_stat_value", _f(ln.get("non_discounted_stat_value"))))
        lt = ln.get("line_type") or "balanced"
        listed = {x.get("choice") for x in ln.get("options") or []} & {"higher", "lower"}
        variant = "balanced" if lt == "balanced" else "alternate"
        if variant == "balanced" and len(listed) == 1:
            variant = "one_sided"              # S1/S2: a higher-only (or lower-only) line is its own market, not Standard
        b.add(OfferSnapshot(
            platform="underdog", offer_id=f"ud:{key}", market_id=ln.get("entry_stable_id"), league=league,
            event_id=eid, event_ref=title or None, player_id=pid, player_name=name, player_key=names.key(name),
            team=team, stat=stat, source_stat=st.get("stat") or "", scope=scope, line=value, sides=tuple(sides),
            variant=variant, promotional=promo,
            availability="open" if ln.get("status") == "active" else "suspended",
            rule_version=RULE_VERSIONS["underdog"], listed_start_utc=start, listed_time_kind="start",
            source_updated_at=max(upd) if upd else None, observed_at=observed_at, source_time_unknown=not upd,
            extras=tuple(extras) + ex2 + (("line_type", lt),), known_at=observed_at))
    return b


# ----------------------------------------------------------------------------------------------------------- Kalshi
_K_TITLE = re.compile(r"^(?P<name>.+): (?P<n>\d+)\+$")
_K_STATUS = {"active": "open", "inactive": "suspended", "initialized": "suspended"}


def kalshi_offers(events, lg, observed_at, series_market, resolver=None) -> Batch:
    """events: Kalshi /events?with_nested_markets=true events for ONE series; series_market: our market key."""
    league = lg["key"]
    b = Batch()
    for ev in events or []:
        day = kalshi_src.event_date(ev.get("event_ticker"))
        away, home = identity.parse_game(ev.get("sub_title"))
        codes = [c for c in (away, home) if c]
        for m in ev.get("markets") or []:
            key = m.get("ticker")
            t = _K_TITLE.match((m.get("yes_sub_title") or "").strip())
            strike = _f(m.get("floor_strike"))
            if not t or not day or m.get("strike_type") != "greater" or strike is None \
                    or abs(strike - (int(t.group("n")) - 0.5)) > 1e-9:
                b.q("offer", "kalshi", key, "unreadable_market_title_or_strike",
                    {"yes_sub_title": m.get("yes_sub_title"), "strike_type": m.get("strike_type"),
                     "floor_strike": m.get("floor_strike")})
                continue
            name = t.group("name")
            part = (key or "").split("-")[2] if len((key or "").split("-")) > 2 else ""
            team = next((c for c in sorted(codes, key=len, reverse=True) if part.startswith(c)), None)
            yb, ya, nb, na = (_f(m.get(k)) for k in ("yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars",
                                                       "no_ask_dollars"))
            over = SideQuote("over", bool(ya and 0 < ya < 1), "exchange_yes_no", bid=yb if yb else None,
                             ask=ya if ya else None, bid_size=_f(m.get("yes_bid_size_fp")),
                             ask_size=_f(m.get("yes_ask_size_fp")))
            under = SideQuote("under", bool(na and 0 < na < 1), "exchange_yes_no", bid=nb if nb else None,
                              ask=na if na else None)
            if day and resolver is not None:
                eid, why = resolver.event(day, away, home)
                pid, why_p = (resolver.player(name, team, eid, day) if eid else (None, why))
                extras = () if eid else (("unresolved_event", why),)
                if eid and pid is None:
                    extras += (("unresolved_player", why_p),)
            else:
                eid, pid, extras = None, None, (("unresolved", "no resolver"),)
            liq = tuple((k, _f(m.get(src))) for k, src in (("volume", "volume_fp"), ("volume_24h", "volume_24h_fp"),
                                                            ("open_interest", "open_interest_fp"),
                                                            ("last_price", "last_price_dollars"),
                                                            ("previous_price", "previous_price_dollars")))
            b.add(OfferSnapshot(
                platform="kalshi", offer_id=f"kalshi:{key}", market_id=ev.get("event_ticker"), league=league,
                event_id=eid, event_ref=ev.get("sub_title"), player_id=pid, player_name=name,
                player_key=names.key(name), team=team, stat=series_market, source_stat=ev.get("series_ticker") or "",
                scope="full_game", line=strike, sides=(over, under), variant="ladder_rung", promotional=False,
                availability=_K_STATUS.get(m.get("status"), "removed"), rule_version=RULE_VERSIONS["kalshi"],
                listed_start_utc=_ts(m.get("expected_expiration_time")), listed_time_kind="expected_expiration",
                source_updated_at=None, observed_at=observed_at, source_time_unknown=True, liquidity=liq,
                extras=extras + (("kalshi_updated_time", m.get("updated_time")), ("ticker_date", day),
                                 ("dnp_rule", "inactive / never enters: settles to a fair price (not void)")),
                known_at=observed_at))
    return b


# ------------------------------------------------------------------------------------------------------ The Odds API
def oddsapi_offers(ev, lg, observed_at, resolver=None) -> Batch:
    """One /events/{id}/odds answer -> one OfferSnapshot per (book, market, player, point)."""
    fam, league = FAMILY[lg["key"]], lg["key"]
    b = Batch()
    start = _ts(ev.get("commence_time"))
    away, home = ev.get("away_team"), ev.get("home_team")
    for bk in ev.get("bookmakers") or []:
        for mkt in bk.get("markets") or []:
            mk = markets.ODDS_API.get(fam, {}).get(mkt.get("key"))
            pairs = {}
            for o in mkt.get("outcomes") or []:
                side = (o.get("name") or "").lower()
                if side in ("over", "under") and o.get("point") is not None and o.get("description"):
                    pairs.setdefault((o["description"], float(o["point"])), {})[side] = _f(o.get("price"))
            upd = _ts(mkt.get("last_update") or bk.get("last_update"))
            for (player, point), sd in pairs.items():
                key = f"{ev.get('id')}:{mkt.get('key')}:{names.key(player)}:{point:g}"
                sides = tuple(SideQuote(s, s in sd and sd[s] is not None and sd[s] > 1, "decimal",
                                        price=sd.get(s) if sd.get(s) and sd[s] > 1 else None) for s in ("over", "under"))
                b.add(OfferSnapshot(
                    platform=f"oddsapi:{bk.get('key')}", offer_id=key, market_id=mkt.get("key"), league=league,
                    event_id=None, event_ref=f"{away} @ {home}", player_id=None, player_name=player,
                    player_key=names.key(player), team=None, stat=mk, source_stat=mkt.get("key") or "",
                    scope="full_game", line=point, sides=sides,
                    variant="alternate" if "alternate" in (mkt.get("key") or "") else "main", promotional=False,
                    availability="open", rule_version=RULE_VERSIONS["oddsapi"], listed_start_utc=start,
                    listed_time_kind="start", source_updated_at=upd, observed_at=observed_at,
                    source_time_unknown=upd is None,
                    extras=(("unresolved_event", "The Odds API names teams in full; mapping not built (no key)"),),
                    known_at=observed_at))
    return b


# ------------------------------------------------------------------------------------------------------------- ESPN
_STATUS = {"STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "in_progress", "STATUS_HALFTIME": "in_progress",
           "STATUS_END_PERIOD": "in_progress", "STATUS_FINAL": "final", "STATUS_FINAL_OT": "final",
           "STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "cancelled", "STATUS_SUSPENDED": "suspended",
           "STATUS_DELAYED": "scheduled", "STATUS_RAIN_DELAY": "in_progress"}
_SEASON_TYPE = {1: "preseason", 2: "regular", 3: "postseason"}


def espn_events(body, lg, observed_at, previous=None) -> Batch:
    """ESPN scoreboard body -> Event + Team records. previous: {event_id: Event} last stored, so a moved start becomes
    a Reschedule entry on the new revision (the first start is never overwritten silently)."""
    league = lg["key"]
    b = Batch()
    for e in body.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        side = {}
        for x in c.get("competitors") or []:
            t = x.get("team") or {}
            if t.get("id") and t.get("abbreviation"):
                tid = identity.team_id(league, t["id"])
                side[x.get("homeAway")] = tid
                b.add(Team(team_id=tid, league=league, abbr=t["abbreviation"], name=t.get("displayName") or "",
                           source_ids=(SourceId("espn", str(t["id"])),), observed_at=observed_at, known_at=observed_at))
        st = (c.get("status") or e.get("status") or {}).get("type") or {}
        status = _STATUS.get(st.get("name"), "final" if st.get("completed") else
                             {"pre": "scheduled", "in": "in_progress", "post": "final"}.get(st.get("state"), "unknown"))
        start = _ts(e.get("date"))
        eid = f"{league}:espn:{e.get('id')}"
        if not e.get("id") or "home" not in side or "away" not in side:
            b.q("event", "espn", e.get("id"), "missing_id_or_teams", {"name": e.get("name")})
            continue
        prev = (previous or {}).get(eid)
        resched = tuple(prev.reschedules) if prev else ()
        if prev and prev.start_utc and start and prev.start_utc != start:
            resched += (Reschedule(prev.start_utc, start, observed_at, "espn"),)
        season = (e.get("season") or {})
        b.add(Event(event_id=eid, league=league, season=season.get("year"),
                    season_type=_SEASON_TYPE.get(season.get("type"), "unknown"), home_team_id=side["home"],
                    away_team_id=side["away"], start_utc=start, start_source="espn", status=status,
                    reschedules=resched, source_ids=(SourceId("espn", str(e["id"])),), settlement_source="espn",
                    observed_at=observed_at, known_at=observed_at))
    return b


def _unit(part):
    return "yards" if part.endswith("_yds") else ("minutes" if part == "min" else "count")


def espn_box(summary, lg, event_id, observed_at) -> Batch:
    """espn.parse_summary() output for one event -> StatObservation per player per box-score part (FINAL games only:
    an in-progress box score is not an observation of the result). Players with played None (football: in no box
    group) are typed participation unknown, never 0."""
    league = lg["key"]
    b = Batch()
    if not summary.get("completed"):
        return b
    for pk, rows in (summary.get("players") or {}).items():
        if len(rows) > 1:
            for r in rows:
                b.q("stat", "espn", f"{event_id}|{r.get('id')}", "two_box_rows_one_name_key", {"name": r.get("name")})
        for r in rows:
            if not r.get("id"):
                b.q("stat", "espn", f"{event_id}|{pk}", "missing_athlete_id", {"name": r.get("name")})
                continue
            pid = identity.player_id(league, r["id"])
            part = {True: "played", False: "did_not_play", None: "unknown"}[r.get("played")]
            # Player records come from the roster job only (a second, thinner Player from the box score would make a
            # new revision every run - measured 2026-10-23)
            parts = r.get("parts") or {}
            if part == "did_not_play":
                parts = {k: 0.0 for k in parts} or {"min": 0.0}
            for stat, val in sorted(parts.items()):
                b.add(StatObservation(
                    event_id=event_id, league=league, participant_id=pid, participant_type="player",
                    scope="full_game", includes_overtime="yes", stat=stat, unit=_unit(stat),
                    value=None if part == "unknown" else val, participation=part, source="espn",
                    source_ref=f"espn:summary:{event_id.split(':')[-1]}:{r['id']}", final=True,
                    observed_at=observed_at, known_at=observed_at))
    return b


_ATH_ID = re.compile(r"/id/(\d+)/")


def espn_roster(body, lg, team, observed_at, rosters=None, previous=None) -> Batch:
    """ESPN team roster -> Player + Membership records (and fills an identity.Rosters for resolution).
    previous: {player_id: Membership open on another team} -> that membership gets a closing revision (trade)."""
    league = lg["key"]
    b = Batch()
    groups = body.get("athletes") or []
    flat = []
    for g in groups:
        if isinstance(g, dict) and "items" in g:
            flat += [dict(x, _group=g.get("position")) for x in g.get("items") or []]
        else:
            flat.append(g)
    day = observed_at[:10] + "T00:00:00Z"
    for a in flat:
        if not a.get("id") or not a.get("displayName"):
            continue
        pid = identity.player_id(league, a["id"])
        st = ((a.get("status") or {}).get("type") or "unknown").lower()
        if a.get("_group") == "practiceSquad":
            st = "practice_squad"
        elif a.get("_group") == "injuredReserveOrOut":
            st = "injured_reserve_or_out"
        b.add(Player(player_id=pid, league=league, name=a["displayName"],
                     position=((a.get("position") or {}).get("abbreviation")), status=st,
                     source_ids=(SourceId("espn", str(a["id"])),), observed_at=observed_at, known_at=observed_at))
        if rosters is not None:                  # identity only (practice-squad players get elevated to play)
            rosters.add(team, pid, a["displayName"])
        old = (previous or {}).get(pid)
        if old and old.team_id != team and old.valid_to is None:
            b.add(Membership(player_id=pid, team_id=old.team_id, valid_from=old.valid_from, valid_to=day,
                             source="espn", observed_at=observed_at, known_at=observed_at))
        if not old or old.team_id != team or old.valid_to is not None:
            b.add(Membership(player_id=pid, team_id=team, valid_from=day, source="espn", observed_at=observed_at,
                             known_at=observed_at))
    return b


_INJ = {"out": "out", "doubtful": "doubtful", "questionable": "questionable", "probable": "probable",
        "day-to-day": "day_to_day", "injured reserve": "injured_reserve", "suspension": "suspended",
        "suspended": "suspended", "active": "active"}


def espn_injuries(body, lg, observed_at) -> Batch:
    """ESPN injury report -> ContextSnapshot(kind injury). Only the status, its date and ESPN's short type; ESPN's
    news text (long/short comments) is NOT stored (third-party editorial content). A player missing from the report
    is NOT 'healthy' - no record means unknown."""
    league = lg["key"]
    b = Batch()
    for t in body.get("injuries") or []:
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            href = " ".join(x.get("href") or "" for x in ath.get("links") or [])
            m = _ATH_ID.search(href)
            if not m:
                b.q("context", "espn", ath.get("displayName"), "injury_without_athlete_id", None)
                continue
            raw = (i.get("status") or "").strip().lower()
            det = i.get("details") or {}
            b.add(ContextSnapshot(
                league=league, kind="injury", subject_id=identity.player_id(league, m.group(1)), event_id=None,
                status=_INJ.get(raw, "unknown"), present="no" if raw == "active" else "yes",
                detail=" ".join(x for x in (det.get("side"), det.get("type")) if x) or None, source="espn",
                source_updated_at=_ts(i.get("date")), as_of=_ts(i.get("date")), observed_at=observed_at,
                known_at=observed_at, source_time_unknown=_ts(i.get("date")) is None))
    return b
