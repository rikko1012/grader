"""Sportsbook ingestion: parsers on real sanitized samples (FanDuel, BetRivers, the blocked books' 403s) and The Odds
API's documented format; storage (immutable history, removals, health, lease); schedule and plan; matching, no-vig
and consensus; the worker's pass logic - all offline."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import config
from sports.books import (base, betrivers, compare, events, fanduel, plan, providers, schedule, store, theoddsapi,
                          worker)
from sports.books import cli as books_cli
from sports.books.base import Event, FetchResult

F = Path(__file__).resolve().parent / "fixtures" / "books"
NBA, NFL = config.league("nba"), config.league("nfl")
NOW = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
KNICKS = Event("36220759", "nba", "New York Knicks @ Boston Celtics", "Boston Celtics", "New York Knicks",
               "2026-10-23T23:10:00Z")


def J(name):
    return json.loads((F / name).read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path):
    d = store.connect(tmp_path / "books.db", dsn="")
    yield d
    d.close()


# ------------------------------------------------------------------------------------------------ offers / parsers

def test_make_offer_normalizes_and_refuses_incomplete():
    o = base.make_offer(provider="p", book="b", league="nba", provider_event_id="1", player="C.J. McCollum",
                        market="points", period="game", line="20.5", side="over", price_american="-141",
                        market_type="standard")
    assert o["price_american"] == -141 and o["price_decimal"] == pytest.approx(1.709220, abs=1e-6)
    assert o["player_key"] == "cj mccollum" and o["line"] == 20.5 and o["status"] == "open" and len(o["offer_key"]) == 24
    with pytest.raises(ValueError):
        base.make_offer(provider="p", book="b", league="nba", provider_event_id="1", player="x", market="points",
                        period="game", line=1.5, side="both", price_american=100, market_type="standard")
    with pytest.raises(ValueError):
        base.make_offer(provider="p", book="b", league="nba", provider_event_id="1", player="x", market="points",
                        period="game", line=None, side="over", price_american=100, market_type="standard")
    assert base.decimal_to_american(2.5) == 150 and base.decimal_to_american(1.5) == -200


def test_fanduel_events_keep_only_the_real_league():
    evs = fanduel.parse_events(J("fanduel_nba_list.json"), NBA)
    assert [(e.provider_event_id, e.home, e.away) for e in evs] == [("36220759", "Boston Celtics", "New York Knicks")]
    # eBasketball "(GRAVITY) @ ... (ARCHITECT)" sims and the futures event are left out


def test_fanduel_props_standard_pairs_alternates_and_exclusions():
    o = fanduel.parse_props(J("fanduel_nba_points.json"), NBA, KNICKS)
    std = [x for x in o if x["market_type"] == "standard"]
    alt = [x for x in o if x["market_type"] == "alternate"]
    pg = sorted((x for x in std if x["player"] == "Paul George"), key=lambda x: x["side"])
    assert [(x["side"], x["line"], x["price_american"]) for x in pg] == [("over", 17.5, -102), ("under", 17.5, -125)]
    assert all(x["period"] == "game" and x["jurisdiction"] == "oh" and x["provider_updated_at"] is None for x in o)
    assert alt and all(x["side"] == "over" for x in alt)                  # ladders: one side, none invented
    assert not any(x["stat_label"].lower().startswith("1st") for x in o)  # quarter markets left out
    assert {x["market"] for x in o} == {"points"}                         # the moneyline is not a prop
    pts = {(x["player"], x["line"]) for x in std}
    for p, line in pts:                                                   # every standard line has both sides
        assert {x["side"] for x in std if (x["player"], x["line"]) == (p, line)} == {"over", "under"}


def test_fanduel_combos_and_nfl_receiving():
    o = fanduel.parse_props(J("fanduel_nba_combos.json"), NBA, KNICKS)
    assert {(x["player"], x["market"], x["line"]) for x in o} == {("Paul George", "pra", 24.5), ("Paul George", "ra", 7.5)}
    ev = Event("35605985", "nfl", "Pittsburgh Steelers @ New Orleans Saints", "New Orleans Saints",
               "Pittsburgh Steelers", "2026-10-25T13:30:00Z")
    n = fanduel.parse_props(J("fanduel_nfl_receiving.json"), NFL, ev)
    dk = sorted((x for x in n if x["player"] == "DK Metcalf" and x["market"] == "rec_yds"), key=lambda x: x["side"])
    assert [(x["line"], x["side"], x["price_american"]) for x in dk] == [(58.5, "over", -114), (58.5, "under", -114)]
    assert {"rec", "rec_yds", "rush_rec_yds"} <= {x["market"] for x in n}


def test_betrivers_events_and_props():
    evs = betrivers.parse_events(J("betrivers_nba_list.json"), NBA)
    assert evs and all(e.home and e.away for e in evs) and "1028736320" in {e.provider_event_id for e in evs}
    body = J("betrivers_nba_event.json")
    ev = Event("1028736320", "nba", "NY Knicks @ BOS Celtics", "BOS Celtics", "NY Knicks", "2026-10-23T23:00:00Z")
    o = betrivers.parse_props(body, NBA, ev)
    pp = sorted((x for x in o if x["player"] == "Payton Pritchard" and x["market_type"] == "standard"
                 and x["market"] == "points"), key=lambda x: x["side"])
    assert [(x["line"], x["side"], x["price_american"]) for x in pp] == [(15.5, "over", -120), (15.5, "under", -114)]
    assert all(x["provider_updated_at"] and x["jurisdiction"] == "oh" and x["provider_player_id"] for x in o)
    ladder = [x for x in o if x["market_type"] == "alternate"]
    assert ladder and all(x["side"] == "over" and x["line"] % 1 == 0.5 for x in ladder)   # "N+" = Over N - 0.5
    assert len({x["provider_market_id"] for x in o}) < len(o)               # O/U sides share their offer id
    labels = {x["stat_label"] for x in o}
    assert not any("Half" in s or "Quarter" in s for s in labels)           # other periods left out


def test_betrivers_nfl_over_only_line_stays_one_sided():
    body = J("betrivers_nfl_event.json")
    ev = Event(str(body["betOffers"][0]["eventId"]), "nfl", "PIT Steelers vs NO Saints", "NO Saints", "PIT Steelers",
               "2026-10-25T13:30:00Z")
    o = betrivers.parse_props(body, NFL, ev)
    rec = [x for x in o if x["market"] == "rec" and x["market_type"] == "standard"]
    assert len(rec) == 1 and rec[0]["side"] == "over"                       # Over listed alone: no Under invented
    assert not any("Touchdown" in x["stat_label"] for x in o)
    assert {x["market"] for x in o} == {"rec", "rec_yds"}


def test_theoddsapi_documented_format(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k-test")
    body = J("theoddsapi_nba_event.json")
    ev = Event(body["id"], "nba", "New York Knicks @ Boston Celtics", "Boston Celtics", "New York Knicks",
               body["commence_time"])
    o = theoddsapi.parse_props(body, NBA, ev)
    assert {x["book"] for x in o} == {"draftkings", "caesars"}              # williamhill_us = Caesars; bovada not asked
    dk_alt = [x for x in o if x["book"] == "draftkings" and x["market_type"] == "alternate"]
    assert len(dk_alt) == 1 and dk_alt[0]["line"] == 19.5 and dk_alt[0]["side"] == "over"
    assert all(x["provider_updated_at"] and x["jurisdiction"] == "us" for x in o)
    assert theoddsapi.credits_per_event(NBA) == len(theoddsapi.market_keys(NBA))
    assert not any(k.endswith("_alternate") for k in theoddsapi.market_keys(NBA))
    monkeypatch.setenv("SPORTS_ODDS_API_ALTERNATES", "1")
    assert "player_points_alternate" in theoddsapi.market_keys(NBA)


def test_theoddsapi_reads_quota_headers_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "secret123")

    class R:
        status_code = 200
        headers = {"x-requests-remaining": "480", "x-requests-used": "20", "x-requests-last": "4"}

        def json(self):
            return J("theoddsapi_nba_event.json")
    monkeypatch.setattr(theoddsapi.net, "get", lambda url, params=None, **kw: R())
    res = theoddsapi.TheOddsAPI().props(NBA, Event("x", "nba", "a @ b", "b", "a", "2026-10-23T23:10:00Z"))
    assert res.error is None and res.credits_remaining == 480 and res.credits_used == 4 and res.offers

    def boom(url, params=None, **kw):
        raise theoddsapi.net.HttpError(401, url + "?apiKey=secret123", "invalid key secret123")
    monkeypatch.setattr(theoddsapi.net, "get", boom)
    res = theoddsapi.TheOddsAPI().events(NBA)
    assert res.error and "secret123" not in res.error and res.blocked
    monkeypatch.delenv("ODDS_API_KEY")
    assert theoddsapi.TheOddsAPI().configured() == (False, "ODDS_API_KEY is not set")


@pytest.mark.parametrize("book,sample", [("draftkings", "draftkings_403.html"), ("caesars", "caesars_403.html"),
                                         ("betmgm", "betmgm_403.html")])
def test_blocked_books_are_reported_not_worked_around(monkeypatch, book, sample):
    calls = []

    def refuse(url, params=None, headers=None, **kw):
        calls.append((url, headers))
        raise providers.net.HttpError(403, url, (F / sample).read_text())
    monkeypatch.setattr(providers.net, "get", refuse)
    res = providers.probe(book)
    assert res.blocked and res.http_status == 403 and len(calls) == 1      # one plain request, no retry
    assert "User-Agent" not in (calls[0][1] or {})                          # the session's honest agent, unchanged


# ------------------------------------------------------------------------------------------------ storage

def _offers(price_over=-110, price_under=-110, line=15.5, status="open", book="fanduel", provider="fanduel",
            player="Payton Pritchard", event="36220759"):
    common = dict(provider=provider, book=book, jurisdiction="oh", league="nba", provider_event_id=event,
                  event_name="New York Knicks @ Boston Celtics", home="Boston Celtics", away="New York Knicks",
                  start="2026-10-23T23:10:00Z", player=player, market="points", stat_label="Points", period="game",
                  line=line, market_type="standard", status=status)
    return [base.make_offer(**common, side="over", price_american=price_over),
            base.make_offer(**common, side="under", price_american=price_under)]


def test_store_writes_history_only_on_change_and_closes_removed(db):
    n = lambda: db.one("SELECT COUNT(*) AS n FROM sports_book_quotes")["n"]
    assert store.save_offers(db, 1, "fanduel", "nba", "36220759", "espn:1", _offers(), True, NOW) == (2, 0)
    assert store.save_offers(db, 2, "fanduel", "nba", "36220759", "espn:1", _offers(), True,
                             NOW + timedelta(minutes=10)) == (0, 0) and n() == 2
    seen = db.one("SELECT last_seen_at FROM sports_book_offer_state LIMIT 1")["last_seen_at"]
    assert seen == "2026-10-23T21:10:00Z"                                   # observation time moves on
    assert store.save_offers(db, 3, "fanduel", "nba", "36220759", "espn:1", _offers(-120, 100), True,
                             NOW + timedelta(minutes=20)) == (2, 0) and n() == 4
    sus = _offers(-120, 100, status="suspended")
    assert store.save_offers(db, 4, "fanduel", "nba", "36220759", "espn:1", sus, True, NOW + timedelta(minutes=30))[0] == 2
    assert store.save_offers(db, 5, "fanduel", "nba", "36220759", "espn:1", sus[:1], False,
                             NOW + timedelta(minutes=40)) == (0, 0)        # incomplete read: nothing removed
    assert store.save_offers(db, 6, "fanduel", "nba", "36220759", "espn:1", sus[:1], True,
                             NOW + timedelta(minutes=50)) == (0, 1)        # complete read: the Under was removed
    rows = db.all("SELECT status, price_american FROM sports_book_quotes ORDER BY id")
    assert [r["status"] for r in rows] == ["open", "open", "open", "open", "suspended", "suspended", "removed"]
    assert rows[-1]["price_american"] is None
    moved = _offers(line=16.5)                                              # a moved line is a new offer
    assert store.save_offers(db, 7, "fanduel", "nba", "36220759", "espn:1", moved, True,
                             NOW + timedelta(hours=1)) == (2, 1)            # the suspended Over 15.5 closes


def test_store_health_backoff_lease_and_settings(db):
    store.note_result(db, "fanduel", FetchResult(error="HTTP 503"), NOW, cooldown_until="2026-10-23T21:01:00Z")
    s = store.provider_state(db, "fanduel")
    assert s["failures"] == 1 and s["cooldown_until"] == "2026-10-23T21:01:00Z" and s["last_error"] == "HTTP 503"
    store.note_result(db, "fanduel", FetchResult(credits_remaining=None), NOW)
    s = store.provider_state(db, "fanduel")
    assert s["failures"] == 0 and s["cooldown_until"] is None and s["last_ok_at"] == "2026-10-23T21:00:00Z"
    assert store.take_lease(db, "a", 60, now=NOW) and not store.take_lease(db, "b", 60, now=NOW)
    assert store.take_lease(db, "a", 60, now=NOW)                           # the holder renews
    assert store.take_lease(db, "b", 60, now=NOW + timedelta(seconds=61))  # an expired lease can be taken
    store.release_lease(db, "b")
    assert store.take_lease(db, "c", 60, now=NOW)
    store.set_setting(db, "x", "1", NOW)
    assert store.setting(db, "x") == "1" and store.setting(db, "y", "d") == "d"
    fid = store.start_fetch(db, "fanduel", "nba", "props", "1", "espn:1", NOW)
    store.finish_fetch(db, fid, FetchResult(requests=5, http_status=200), 10, 4, 0, NOW)
    r = db.one("SELECT * FROM sports_book_fetches WHERE id = ?", (fid,))
    assert r["ok"] == 1 and r["requests"] == 5 and r["n_offers"] == 10 and r["n_new_quotes"] == 4


# ------------------------------------------------------------------------------------------------ canonical games

def test_events_match_by_nicknames_and_start():
    espn_evs = [{"id": "401", "start": "2026-10-23T23:00:00Z", "home": {"nick": "Celtics"}, "away": {"nick": "Knicks"}},
                {"id": "402", "start": "2026-10-24T02:00:00Z", "home": {"nick": "Trail Blazers"},
                 "away": {"nick": "Kings"}}]
    kb = Event("1", "nba", "NY Knicks @ BOS Celtics", "BOS Celtics", "NY Knicks", "2026-10-22T23:00:00Z")
    assert events.match(espn_evs, "betrivers", kb) == "unmatched:betrivers:1"      # a day off: no match
    kb.start = "2026-10-23T22:45:00Z"
    assert events.match(espn_evs, "betrivers", kb) == "espn:401"
    fd = Event("2", "nba", "Sacramento Kings @ Portland Trail Blazers", "Portland Trail Blazers", "Sacramento Kings",
               "2026-10-24T02:10:00Z")
    assert events.match(espn_evs, "fanduel", fd) == "espn:402"
    blazers_only = Event("3", "nba", "Portland Blazers @ X", "Portland Blazers", "X", "2026-10-24T02:10:00Z")
    assert events.match(espn_evs, "fanduel", blazers_only).startswith("unmatched:")


# ------------------------------------------------------------------------------------------------ compare

def _seed(db, at=NOW):
    fd = _offers(-120, 100) + _offers(-150, 110, line=16.5) + _offers(150, -190, line=19.5)[:1]   # 19.5: Over only
    store.save_offers(db, 1, "fanduel", "nba", "36220759", "espn:401", fd, True, at)
    store.save_offers(db, 2, "betrivers", "nba", "1028736320", "espn:401",
                      _offers(-120, -114, book="betrivers", provider="betrivers", event="1028736320"), True, at)


def test_two_sided_needs_both_sides_at_one_line_and_consensus_needs_two_books(db):
    _seed(db)
    q = compare.current(db, "nba", NOW + timedelta(minutes=5))
    pairs = compare.two_sided(q)
    lines = sorted((p["book"], p["line"]) for p in pairs)
    assert lines == [("betrivers", 15.5), ("fanduel", 15.5), ("fanduel", 16.5)]   # 19.5 has one side: no pair
    cons = compare.consensus(pairs)
    assert ("espn:401", "payton pritchard", "points", "game", 16.5) not in cons     # one book is not a consensus
    c = cons[("espn:401", "payton pritchard", "points", "game", 15.5)]
    fd = (1 / base.american_to_decimal(-120)) / (1 / base.american_to_decimal(-120) + 1 / base.american_to_decimal(100))
    kb = (1 / base.american_to_decimal(-120)) / (1 / base.american_to_decimal(-120) + 1 / base.american_to_decimal(-114))
    assert c["books"] == ["betrivers", "fanduel"] and c["p_over"] == pytest.approx((fd + kb) / 2)
    assert len(c["quote_ids"]) == 4                                          # exactly the quotes used


def test_lines_are_never_combined_and_one_side_gets_no_probability(db):
    store.save_offers(db, 1, "fanduel", "nba", "36220759", "espn:401",
                      _offers(-120, 100)[:1] + _offers(-110, -110, line=16.5)[1:], True, NOW)
    q = compare.current(db, "nba", NOW)
    assert compare.two_sided(q) == []                                       # Over 15.5 + Under 16.5 is not a market


def test_staleness_follows_the_schedule(db):
    _seed(db)
    early = compare.current(db, "nba", NOW + timedelta(minutes=10))
    late = compare.current(db, "nba", NOW + timedelta(minutes=70))
    assert all(r["fresh"] for r in early) and not any(r["fresh"] for r in late)
    assert compare.two_sided(late) == [] and compare.two_sided(late, fresh_only=False)


def test_a_book_from_two_providers_counts_once(db):
    _seed(db)
    store.save_offers(db, 9, "theoddsapi", "nba", "oa1", "espn:401",
                      _offers(-130, 105, book="fanduel", provider="theoddsapi", event="oa1"), True,
                      NOW + timedelta(minutes=2))
    cons = compare.consensus(compare.two_sided(compare.current(db, "nba", NOW + timedelta(minutes=5))))
    c = cons[("espn:401", "payton pritchard", "points", "game", 15.5)]
    assert c["books"] == ["betrivers", "fanduel"] and c["by_book"]["fanduel"]["provider"] == "theoddsapi"   # newest pair


def test_board_hook_reads_only_the_exact_line(db):
    _seed(db)
    rows = [{"player_key": "payton pritchard", "market": "points", "line": 15.5, "start": "2026-10-23T23:00:00Z",
             "side": "under"},
            {"player_key": "payton pritchard", "market": "points", "line": 14.5, "start": "2026-10-23T23:00:00Z",
             "side": "over"}]
    books_cli.attach(NBA, rows, db=db, now=NOW + timedelta(minutes=5))
    b = rows[0]["books"]
    assert b["consensus_books"] == ["betrivers", "fanduel"] and b["consensus_side"] == pytest.approx(1 - b["consensus_over"])
    assert set(b["pairs"]) == {"betrivers", "fanduel"} and len(b["quote_ids"]) == 4
    assert rows[1]["books"] is None                                         # 14.5 is not quoted: nothing substituted
    assert books_cli.label(b, "under").endswith("2bk") and books_cli.label(None, "over") == ""


# ------------------------------------------------------------------------------------------------ schedule / plan

def test_ladders():
    start = "2026-10-23T23:10:00Z"
    assert schedule.interval_min("full", start, NOW) == 30                  # 2 h 10 m away
    assert schedule.interval_min("full", start, NOW + timedelta(hours=1)) == 10
    assert schedule.interval_min("full", start, NOW + timedelta(hours=3)) is None   # started: never fetched
    assert schedule.interval_min("minimal", start, NOW - timedelta(days=2)) is None  # beyond the horizon
    assert schedule.fetches_per_game("full") == 12 + 12 + 16 + 16 + 8
    assert schedule.fetches_in("full", start, NOW) == 1 + 10                # one 30-min rung, then ten 10-min rungs
    assert schedule.stale_after_s("full", start, NOW) == 3600


def test_plan_estimates_and_fits_the_budget(monkeypatch):
    starts = {"nba": ["2026-10-23T23:10:00Z", "2026-10-24T23:10:00Z"], "nfl": ["2026-10-25T17:00:00Z"]}
    est = plan.estimate(starts, NOW)
    assert est["games"] == {"nba": 2, "nfl": 1}
    assert est["direct"]["fanduel"] > est["direct"]["betrivers"]            # FanDuel needs one request per tab
    assert est["odds_api"]["full"] >= est["odds_api"]["standard"] >= est["odds_api"]["lean"] >= est["odds_api"]["minimal"]
    monkeypatch.setenv("SPORTS_ODDS_API_MONTHLY_CREDITS", "500")
    assert plan.reserve() == 50 and plan.daily_allowance() == pytest.approx(15.0)
    chosen = plan.choose_ladder(est)
    assert chosen is None or est["odds_api"][chosen] <= 15
    assert plan.choose_ladder(est, 5_000_000) == "full"


# ------------------------------------------------------------------------------------------------ worker

class FakeProvider(base.Provider):
    name = "fake"
    kind = "direct"

    def __init__(self, games, offers=None, error=None, blocked=False):
        self.games, self.offers_by, self.error, self.blocked, self.calls = games, offers or {}, error, blocked, []

    def events(self, lg):
        return FetchResult(events=[g for g in self.games if g.league == lg["key"]], requests=1)

    def props(self, lg, ev):
        self.calls.append(ev.provider_event_id)
        if self.error:
            return FetchResult(requests=1, error=self.error, blocked=self.blocked, http_status=403 if self.blocked else 503)
        return FetchResult(offers=self.offers_by.get(ev.provider_event_id, []), requests=1, http_status=200)


def _game(pid="36220759", start="2026-10-23T23:10:00Z"):
    return Event(pid, "nba", "New York Knicks @ Boston Celtics", "Boston Celtics", "New York Knicks", start)


@pytest.fixture
def no_espn(monkeypatch):
    monkeypatch.setattr(worker.canon, "keys_for", lambda lg, prov, evs: {e.provider_event_id: "espn:401" for e in evs})


def test_worker_fetches_due_games_once_and_stores(db, no_espn):
    p = FakeProvider([_game(), _game("far", "2026-11-05T23:10:00Z")], {"36220759": _offers(event="36220759")})
    s = worker.run_once(db, ("nba",), NOW, {"fake": p}, probe=False)
    assert p.calls == ["36220759"] and s["providers"]["fake"]["new_quotes"] == 2   # the far game is beyond the ladder
    s = worker.run_once(db, ("nba",), NOW + timedelta(minutes=5), {"fake": p}, probe=False)
    assert p.calls == ["36220759"] and s["providers"]["fake"]["fetched"] == 0       # not due again yet
    worker.run_once(db, ("nba",), NOW + timedelta(minutes=31), {"fake": p}, probe=False)
    assert p.calls == ["36220759", "36220759"]
    assert db.one("SELECT event_key FROM sports_book_events WHERE provider_event_id = '36220759'")["event_key"] == "espn:401"


def test_tomorrows_game_without_props_yet_is_rechecked_in_30_minutes(db, no_espn):
    """Owner audit bug 4: next-day props are posted the night before; a free book read that found none is re-read
    after 30 min (not at the 24-72 h rung's 3 h), and once props are there the normal ladder applies."""
    tomorrow = _game("tmw", "2026-10-24T23:10:00Z")                          # 26 h away: the 180-min rung
    p = FakeProvider([tomorrow])
    worker.run_once(db, ("nba",), NOW, {"fake": p}, probe=False)
    worker.run_once(db, ("nba",), NOW + timedelta(minutes=29), {"fake": p}, probe=False)
    assert p.calls == ["tmw"]
    p.offers_by["tmw"] = _offers(event="tmw")                               # the book posts its props
    s = worker.run_once(db, ("nba",), NOW + timedelta(minutes=31), {"fake": p}, probe=False)
    assert p.calls == ["tmw", "tmw"] and s["providers"]["fake"]["new_quotes"] == 2
    worker.run_once(db, ("nba",), NOW + timedelta(minutes=31 + 60), {"fake": p}, probe=False)
    assert p.calls == ["tmw", "tmw"]                                         # posted: back on the 3-hour rung


def test_worker_lease_prevents_overlap(db, no_espn):
    store.take_lease(db, "someone-else", 600, now=NOW)
    p = FakeProvider([_game()])
    assert worker.run_once(db, ("nba",), NOW, {"fake": p}, probe=False) == {"busy": True} and p.calls == []


def test_worker_backs_off_and_waits_long_after_a_refusal(db, no_espn):
    p = FakeProvider([_game()], error="HTTP 503")
    s = worker.run_once(db, ("nba",), NOW, {"fake": p}, probe=False)
    assert s["providers"]["fake"]["cooling_down_until"] == "2026-10-23T21:01:00Z"
    worker.run_once(db, ("nba",), NOW + timedelta(seconds=30), {"fake": p}, probe=False)
    assert len(p.calls) == 1                                                  # cooling down: not called
    b = FakeProvider([_game()], error="HTTP 403", blocked=True)
    b.name = "fake2"
    s = worker.run_once(db, ("nba",), NOW, {"fake2": b}, probe=False)
    assert s["providers"]["fake2"]["cooling_down_until"] == "2026-10-24T03:00:00Z"


def test_worker_skips_an_unconfigured_feed_and_respects_the_credit_budget(db, no_espn, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    s = worker.run_once(db, ("nba",), NOW, {"theoddsapi": theoddsapi.TheOddsAPI()}, probe=False)
    assert s["providers"]["theoddsapi"]["skipped"] == "ODDS_API_KEY is not set"

    class Feed(FakeProvider):
        kind = "feed"
        name = "feedx"
    f = Feed([_game()], {"36220759": _offers(event="36220759")})
    monkeypatch.setattr(worker, "_feed_ladder", lambda db, now: "full")
    monkeypatch.setattr(plan, "daily_allowance", lambda monthly=None: 1)     # less than one call's credits
    s = worker.run_once(db, ("nba",), NOW, {"feedx": f}, probe=False)
    assert f.calls == [] and s["providers"]["feedx"]["budget"] == "daily credit allowance reached"


def test_worker_probes_blocked_books_at_most_every_6_hours(db, no_espn, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "probe", lambda b: calls.append(b) or FetchResult(requests=1, error="refused",
                                                                                       blocked=True, http_status=403))
    worker.run_once(db, ("nba",), NOW, {}, probe=True)
    worker.run_once(db, ("nba",), NOW + timedelta(hours=1), {}, probe=True)
    assert sorted(calls) == ["betmgm", "caesars", "draftkings"]
    rows = {r["book"]: r for r in books_cli.health(db, NOW)}
    assert rows["draftkings"]["direct"] == "blocked" and "refuses this server" in rows["draftkings"]["blocker"]
    assert "paid plans only" in rows["caesars"]["blocker"]


def test_espn_scoreboard_asks_one_day_at_a_time(monkeypatch):
    from datetime import date
    from sports import espn
    asked = []

    def fake(url, params=None, headers=None, **kw):
        asked.append(params["dates"])
        if "-" in params["dates"]:
            raise espn.net.HttpError(400, url, "range refused")           # what ESPN answers to a dates=A-B range
        return {"events": [{"id": params["dates"], "date": "2026-10-23T23:00Z",
                            "competitions": [{"competitors": []}]}]}
    monkeypatch.setattr(espn.net, "get_json", fake)
    evs = espn.scoreboard(NBA, date(2026, 10, 22), date(2026, 10, 24))
    assert asked == ["20261022", "20261023", "20261024"] and [e["id"] for e in evs] == asked


def test_mapping_only_asks_espn_inside_the_horizon(monkeypatch):
    windows = []
    monkeypatch.setattr(events, "espn_window", lambda lg, evs: windows.append([e.provider_event_id for e in evs]) or [])
    near = Event("near", "nba", "a @ b", "b", "a", "2026-10-24T00:00:00Z")
    far = Event("far", "nba", "c @ d", "d", "c", "2026-12-25T17:00:00Z")
    keys = events.keys_for(NBA, "betrivers", [near, far], now=NOW)
    assert windows == [["near"]] and keys["far"] == "unmatched:betrivers:far"


PG = __import__("os").environ.get("SPORTS_TEST_PG_DSN") or __import__("os").environ.get("LOL_TEST_PG_DSN")


@pytest.mark.skipif(not PG, reason="no PostgreSQL test DSN (SPORTS_TEST_PG_DSN / LOL_TEST_PG_DSN)")
def test_postgres_backend_same_behaviour():
    d = store.connect(dsn=PG)
    try:
        for t in ("sports_book_quotes", "sports_book_offer_state", "sports_book_fetches", "sports_book_provider_state",
                  "sports_book_lease", "sports_book_settings", "sports_book_events", "sports_book_schedule"):
            d.execute(f"DELETE FROM {t}")
        d.commit()
        assert store.save_offers(d, 1, "fanduel", "nba", "36220759", "espn:401", _offers(-120, 100), True, NOW) == (2, 0)
        assert store.save_offers(d, 2, "fanduel", "nba", "36220759", "espn:401", _offers(-120, 100), True,
                                 NOW + timedelta(minutes=5)) == (0, 0)
        assert store.save_offers(d, 3, "fanduel", "nba", "36220759", "espn:401", _offers(-120, 100)[:1], True,
                                 NOW + timedelta(minutes=6)) == (0, 1)
        assert store.take_lease(d, "a", 60, now=NOW) and not store.take_lease(d, "b", 60, now=NOW)
        fid = store.start_fetch(d, "fanduel", "nba", "props", now=NOW)
        assert isinstance(fid, int)
        q = compare.current(d, "nba", NOW + timedelta(minutes=7))
        assert len(q) == 1 and q[0]["side"] == "over" and q[0]["fresh"]
    finally:
        d.close()


def test_verify_fetches_the_next_mapped_game_once_per_league(db, no_espn, monkeypatch):
    p = FakeProvider([_game(), _game("later", "2026-10-24T23:10:00Z")], {"36220759": _offers(event="36220759")})
    monkeypatch.setitem(providers.PROVIDERS, "fake", p)
    out = worker.verify(db, ["fake"], ("nba",), now=NOW)
    assert p.calls == ["36220759"] and out["fake:nba"]["offers"] == 2 and out["fake:nba"]["books"] == {"fanduel": ["points"]}
    # The Odds API is not a books-worker provider (its one path is books/sources.oddsapi_ingest); OddsPapi is off
    assert "theoddsapi" not in providers.PROVIDERS
    assert worker.verify(db, ["oddspapi"], ("nba",), now=NOW)["oddspapi"]["skipped"].startswith("disabled")
