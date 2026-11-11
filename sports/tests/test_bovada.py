"""Public coupon schema, all-game coverage, failure atomicity and UI price path."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from sports import config, market_view, net
from sports.books import bovada, bridge, providers
from sports.model import market
from sports.tests import helpers as h
from sports.tests.test_market_view import book, items, NOW, EV

CLOCK = datetime(2026, 10, 23, 20, tzinfo=timezone.utc)


def coupon(eid="1", league="nba"):
    stat, player, team = ("Total Points", "Jalen Brunson", "NYK") if league == "nba" else (
        "Total Receiving Yards", "Malik Nabers", "NYG")
    return [{"events": [{"id": eid, "description": "Away @ Home", "type": "GAMEEVENT",
        "status": "U", "live": False, "startTime": int(CLOCK.timestamp() + 3600) * 1000,
        "link": f"/football/nfl/a-b-{eid}",
        "competitors": [{"id": "home", "name": "Home", "home": True},
                        {"id": "away", "name": "Away", "home": False}],
        "displayGroups": [{"markets": [{"id": "m1", "key": "2W-OU", "status": "O",
            "description": f"{stat} - {player} ({team})", "period": {"description": "Game"},
            "outcomes": [{"id": "o1", "type": "O", "status": "O", "competitorId": "player1",
                          "price": {"handicap": "20.5", "decimal": "1.8", "american": "-125"}},
                         {"id": "u1", "type": "U", "status": "O", "competitorId": "player1",
                          "price": {"handicap": "20.5", "decimal": "2.0", "american": "EVEN"}}]}]}]}]}]


def test_exact_identity_prices_and_no_fabricated_source_time():
    events, offers = bovada.parse(coupon(), "nba", CLOCK)
    assert len(events) == 1 and len(offers) == 2
    assert offers[0]["player"] == "Jalen Brunson"
    assert offers[0]["market"] == "points" and offers[0]["period"] == "game"
    assert offers[0]["price_decimal"] == 1.8 and offers[1]["price_american"] == 100
    assert offers[0]["provider_updated_at"] is None
    assert offers[0]["jurisdiction"] == "bovada.lv"


@pytest.mark.parametrize("mutation", ["live", "past", "quarter", "team", "closed"])
def test_ineligible_outcomes_not_collected(mutation):
    body = coupon()
    e = body[0]["events"][0]
    m = e["displayGroups"][0]["markets"][0]
    if mutation == "live": e["live"] = True
    if mutation == "past": e["startTime"] = int(CLOCK.timestamp() - 1) * 1000
    if mutation == "quarter": m["period"]["description"] = "1st Quarter"
    if mutation == "team":
        for o in m["outcomes"]: o["competitorId"] = "home"
    if mutation == "closed": m["status"] = "C"
    assert bovada.parse(body, "nba", CLOCK)[1] == []


def test_each_alternate_line_stays_a_separate_pair_without_inventing_a_main():
    body = coupon()
    outcomes = body[0]["events"][0]["displayGroups"][0]["markets"][0]["outcomes"]
    alt = deepcopy(outcomes[0])
    alt["id"], alt["price"]["handicap"] = "alt", "21.5"
    outcomes.append(alt)
    events, offers = bovada.parse(body, "nba", CLOCK)
    assert {o["market_type"] for o in offers} == {"alternate"}
    batch = bridge.to_offers(offers, config.league("nba"), "espn:1", CLOCK.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert len(batch.records) == 2
    assert {o.variant for o in batch.records} == {"ladder_rung"}
    higher = next(o for o in batch.records if o.line == 21.5)
    assert higher.platform == "bovada" and not higher.side("under").offered


def test_nfl_reads_every_listed_event_not_only_two(monkeypatch):
    bodies = [coupon(str(i), "nfl") for i in range(16)]
    listing = [{"events": [b[0]["events"][0] for b in bodies]}]
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if url.endswith("/football/nfl"): return listing
        return bodies[int(url.rsplit("-", 1)[-1])]
    monkeypatch.setattr(net, "get_json", fetch)
    result = bovada.Bovada().read(None, {"key": "nfl"}, CLOCK)
    assert not result.error
    assert len(result.events) == 16 and len(result.offers) == 32
    assert result.requests == len(calls) == 17


def test_partial_nfl_failure_never_declares_a_complete_empty_read(monkeypatch):
    bodies = [coupon(str(i), "nfl") for i in range(3)]
    def fetch(url, **kw):
        if url.endswith("/football/nfl"): return [{"events": [b[0]["events"][0] for b in bodies]}]
        if url.endswith("-1"): raise net.HttpError(403, url)
        return bodies[0]
    monkeypatch.setattr(net, "get_json", fetch)
    result = bovada.Bovada().read(None, {"key": "nfl"}, CLOCK)
    assert result.blocked and result.http_status == 403
    assert result.events == result.offers == []
    assert result.requests == 3


@pytest.mark.parametrize("value", ["nan", "inf", "1.0", "bad"])
def test_bad_price_fails_closed(monkeypatch, value):
    body = coupon()
    body[0]["events"][0]["displayGroups"][0]["markets"][0]["outcomes"][0]["price"]["decimal"] = value
    monkeypatch.setattr(net, "get_json", lambda *a, **kw: body)
    result = bovada.Bovada().read(None, {"key": "nba"}, CLOCK)
    assert result.error and not result.offers and not result.events


def test_bovada_reaches_market_display_without_changing_registered_forecast_sources():
    pp, bv = h.offer(), book("bovada", "bv:1")
    its = items(pp, bv)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    assert mv["bk"][0]["p"] == "bovada" and mv["bk"][0]["in"]
    assert mv["c"]["n"] == 1
    assert market.quote_prob(bv, EV, NOW)[0] is None
    assert not market.in_consensus("bovada")


def test_catalogue_and_kill_switch(monkeypatch):
    monkeypatch.delenv("SPORTS_INGEST", raising=False)
    monkeypatch.delenv("SPORTS_BOVADA", raising=False)
    assert not providers.PROVIDERS["bovada"].configured()[0]
    monkeypatch.setenv("SPORTS_INGEST", "1")
    assert providers.PROVIDERS["bovada"].configured()[0]
    monkeypatch.setenv("SPORTS_BOVADA", "1")
    assert providers.PROVIDERS["bovada"].configured()[0]
    monkeypatch.setenv("SPORTS_BOVADA", "0")
    assert not providers.PROVIDERS["bovada"].configured()[0]


def test_unpublished_nfl_detail_does_not_remove_saved_event_or_block_other_games(monkeypatch):
    bodies = [coupon(str(i), "nfl") for i in range(3)]
    def fetch(url, **kw):
        if url.endswith("/football/nfl"): return [{"events": [b[0]["events"][0] for b in bodies]}]
        if url.endswith("-1"): return {}
        return bodies[int(url.rsplit("-", 1)[-1])]
    monkeypatch.setattr(net, "get_json", fetch)
    result = bovada.Bovada().read(None, {"key": "nfl"}, CLOCK)
    assert not result.error and len(result.events) == 2
    assert {e.provider_event_id for e in result.events} == {"0", "2"}
    assert result.unmapped == ["event 1: detail unavailable"]


def test_worker_persists_each_game_before_requesting_the_next_and_backs_off_on_failure(monkeypatch, tmp_path):
    from sports.books import store, worker
    bodies = [coupon(str(i), "nfl") for i in range(4)]
    steps = []
    def fetch(url, **kw):
        if url.endswith("/football/nfl"): return [{"events": [b[0]["events"][0] for b in bodies]}]
        i = int(url.rsplit("-", 1)[-1]); steps.append(f"fetch:{i}")
        if i == 2: raise net.HttpError(403, url)
        return bodies[i]
    monkeypatch.setattr(net, "get_json", fetch)
    monkeypatch.setenv("SPORTS_BOVADA", "1")
    monkeypatch.setattr(worker.canon, "keys_for", lambda lg, p, evs, now=None: {
        e.provider_event_id: f"espn:{e.provider_event_id}" for e in evs})
    db = store.connect(tmp_path / "books.db", dsn="")
    try:
        result = worker.run_once(db, ("nfl",), CLOCK, {"bovada": bovada.Bovada()}, probe=False,
            sink=lambda provider, lg, key, offers, complete, at: steps.append(f"save:{key.split(':')[-1]}"))
        assert steps == ["fetch:0", "save:0", "fetch:1", "save:1", "fetch:2"]
        state = store.provider_state(db, "bovada")
        assert state["failures"] > 0
        assert store.due(db, "bovada", "nfl", "league") is None
    finally:
        db.close()
