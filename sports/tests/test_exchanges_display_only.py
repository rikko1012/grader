"""Display-only exchanges (owner 2026-11-09, sports/books/exchanges.py): Novig + Gemini Predictions player props.

Parsing from recorded public answers (no key, no network), event / player resolution, the spread / one-sided / started
filters, kill switches, transient failures (nothing written), and the display-only rules: never a source of mc-1 / mi-1,
of the mv-1 display consensus or of the hub's fair price; their own lines follow cap-3 like every exchange."""
import json
from pathlib import Path

import pytest

from sports import board_view, identity, market_view, net, scheduler
from sports.adapters import Resolver
from sports.books import SOURCES, display_only, exchanges as x
from sports.contracts import Event, SideQuote
from sports.model import market
from sports.tests import helpers as h

FX = Path(__file__).resolve().parent / "fixtures" / "books"
OBS = "2026-11-09T18:00:00Z"
LAL_ATL = "nba:espn:401"
BUF_MIN = "nfl:espn:501"


def _fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, body):
        self._b, self.status_code, self.headers = body, 200, {}

    def json(self):
        return self._b


def _nba_resolver():
    teams = identity.Teams("nba", [{"id": "13", "abbr": "LAL", "name": "Los Angeles Lakers"},
                                   {"id": "1", "abbr": "ATL", "name": "Atlanta Hawks"}])
    lal, atl = identity.team_id("nba", "13"), identity.team_id("nba", "1")
    ev = h.event(eid=LAL_ATL, home=atl, away=lal, start="2026-11-10T00:05:00Z")
    r = identity.Rosters()
    for t, pid, name in ((lal, "nba:player:espn:1", "Luka Doncic"), (atl, "nba:player:espn:2", "Onyeka Okongwu"),
                         (lal, "nba:player:espn:3", "Matisse Thybulle")):
        r.add(t, pid, name)
    return Resolver("nba", teams, [ev], r), [{"name": "Los Angeles Lakers", "abbr": "LAL"},
                                            {"name": "Atlanta Hawks", "abbr": "ATL"}]


def _nfl_resolver():
    teams = identity.Teams("nfl", [{"id": "2", "abbr": "BUF", "name": "Buffalo Bills"},
                                   {"id": "16", "abbr": "MIN", "name": "Minnesota Vikings"}])
    buf, mn = identity.team_id("nfl", "2"), identity.team_id("nfl", "16")
    ev = Event(event_id=BUF_MIN, league="nfl", season=2026, season_type="regular", home_team_id=mn, away_team_id=buf,
               start_utc="2026-11-10T01:15:00Z", start_source="espn", status="scheduled", observed_at=OBS, known_at=OBS)
    r = identity.Rosters()
    r.add(mn, "nfl:player:espn:10", "Justin Jefferson")
    r.add(mn, "nfl:player:espn:11", "Jordan Addison")
    return Resolver("nfl", teams, [ev], r)


@pytest.fixture
def novig_answers(monkeypatch):
    fx = _fx("novig_nba_catalog.json")
    calls = []

    def fake_get(source, url, params=None, timeout=20.0, deadline=None):
        calls.append((source, url, dict(params or {})))
        assert source == "novig" and url.startswith(x.NOVIG)
        return _Resp(fx["events_answer"] if url.endswith("/events") else fx["markets_answer"])
    monkeypatch.setattr(x, "_get", fake_get)
    return calls


# ------------------------------------------------------------------------------------------------------- parsing
def test_novig_raw_reads_the_public_catalog_and_keeps_only_mapped_prop_types(novig_answers):
    raw = x.novig_raw("nba")
    assert [c[1].rsplit("/", 1)[1] for c in novig_answers] == ["events", "markets"]       # two requests per league
    assert all(c[2]["league"] == "NBA" and c[2]["limit"] == x.NV_PAGE for c in novig_answers)
    assert novig_answers[1][2]["eventStatus"] == "OPEN_PREGAME"
    assert {m["marketType"] for m in raw["markets"]} == {"POINTS", "REBOUNDS", "ASSISTS"}  # DOUBLE_DOUBLE / TOTAL out
    assert len(raw["events"]) == 2


def test_novig_offers_midpoint_sides_spread_and_one_sided_filters(novig_answers):
    res, teams = _nba_resolver()
    b = x.novig_offers(x.novig_raw("nba"), "nba", OBS, res, teams)
    by = {o.player_name: o for o in b.records}
    # 3 tight POINTS markets kept; Okongwu REBOUNDS (spread 0.145) and the one-sided ASSISTS market are not stored
    assert set(by) == {"Luka Doncic", "Matisse Thybulle", "Ziaire Williams"} and not b.quarantine
    o = by["Luka Doncic"]
    assert (o.platform, o.stat, o.line, o.variant, o.scope) == ("novig", "points", 35.5, "main", "full_game")
    ov, un = o.side("over"), o.side("under")
    # Over bid = the Over outcome's bid; Over ask = 1 - the Under outcome's bid; Under is the complement
    assert (ov.bid, ov.ask, un.bid, un.ask) == (0.5, 0.52, 0.48, 0.5)
    assert ov.price_format == un.price_format == "exchange_yes_no" and ov.offered and un.offered
    assert ov.bid_size is None and un.ask_size is None   # prices only: sizes would make a revision on every read
    assert o.listed_start_utc == "2026-11-10T00:05:00Z" and o.listed_time_kind == "start"
    assert (o.event_id, o.player_id) == (LAL_ATL, "nba:player:espn:1")
    assert dict(o.extras)["display_only"]
    assert by["Ziaire Williams"].player_id is None                                    # not on the roster: unresolved
    assert dict(by["Ziaire Williams"].extras)["unresolved_player"]


def test_novig_started_or_unreadable_markets_never_become_quotes(novig_answers):
    raw = x.novig_raw("nba")
    assert x.novig_offers(raw, "nba", "2026-11-10T00:05:00Z").records == []          # at / after the start
    raw["markets"][0] = dict(raw["markets"][0], description="Luka Doncic 99.5 POINTS")  # text != strike
    b = x.novig_offers(raw, "nba", OBS)
    assert b.reason_counts() == {("novig", "unreadable_description_or_strike"): 1}


def test_gemini_offers_ladder_complement_and_filters():
    evs = _fx("gemini_nfl_props.json")["data"]
    b = x.gemini_offers(evs, "nfl", OBS, _nfl_resolver())
    got = sorted((o.player_name, o.line) for o in b.records)
    # DET-MIA started; anytime touchdowns not a Scout market; Jefferson 29.5 (0.85 / 0.96) wider than the spread
    assert got == [("Jordan Addison", 14.5), ("Justin Jefferson", 49.5)] and not b.quarantine
    o = next(r for r in b.records if r.line == 49.5)
    assert (o.platform, o.stat, o.event_id, o.player_id) == ("gemini", "rec_yds", BUF_MIN, "nfl:player:espn:10")
    assert (o.side("over").bid, o.side("over").ask, o.side("under").bid, o.side("under").ask) == (0.76, 0.78, 0.22, 0.24)
    assert o.offer_id.startswith("gemini:GEMI-NFL-")


def test_gemini_unseen_metric_is_quarantined_never_guessed():
    ev = dict(_fx("gemini_nfl_props.json")["data"][2], sportsMarket={"metric": "receptions_new", "scope": {}})
    b = x.gemini_offers([ev], "nfl", OBS)
    assert b.records == [] and b.reason_counts() == {("gemini", "metric_not_mapped"): 1}


def test_gemini_raw_pages_by_offset_and_keeps_the_league(monkeypatch):
    fx = _fx("gemini_nfl_props.json")
    seen = []

    def fake_get(source, url, params=None, timeout=20.0, deadline=None):
        seen.append(params["offset"])
        return _Resp({"data": fx["data"] if params["offset"] == 0 else [], "pagination": {"total": 3}})
    monkeypatch.setattr(x, "_get", fake_get)
    assert len(x.gemini_raw("nfl")) == 3 and seen == [0]
    assert x.gemini_raw("nba") == []                       # an NFL ticker is never an NBA event


# ------------------------------------------------------------------------------------- the job: kill switch / failures
@pytest.fixture
def no_resolver(monkeypatch):
    from sports.books import sources
    monkeypatch.setattr(sources, "resolver_from_store", lambda con, lg: (None, ()))


def test_kill_switches_default_on_and_off_reads_nothing(monkeypatch, no_resolver):
    assert x.enabled("novig", {}) and x.enabled("gemini", {"GEMINI": "1"})
    assert not x.enabled("novig", {"NOVIG": "0"}) and not x.enabled("gemini", {"GEMINI": "off"})
    monkeypatch.setenv("NOVIG", "0")
    monkeypatch.setenv("GEMINI", "0")
    monkeypatch.setattr(x, "_get", lambda *a, **k: pytest.fail("a request with NOVIG=0 / GEMINI=0"))
    monkeypatch.setattr(x, "store_venue", lambda *a: pytest.fail("a write with NOVIG=0 / GEMINI=0"))
    out = x.run(None, ["nba", "nfl"], observed_at=OBS)
    assert out["nba"] == out["nfl"] == {"novig": {"ok": True, "skipped": "NOVIG=0"},
                                        "gemini": {"ok": True, "skipped": "GEMINI=0"}}


def test_a_failed_read_writes_nothing_so_the_last_quote_stays(monkeypatch, no_resolver):
    def boom(*a, **k):
        raise net.HttpError(503, "https://api.novig.com/v3/public/catalog/events")
    monkeypatch.setattr(x, "_get", boom)
    monkeypatch.setattr(x, "store_venue", lambda *a: pytest.fail("no write on a failed read"))
    out = x.run(None, ["nba"], observed_at=OBS)
    assert out["nba"]["novig"]["ok"] is False and "503" in out["nba"]["novig"]["error"]
    assert out["nba"]["gemini"]["ok"] is False


def test_a_successful_read_is_stored_by_the_venue_only_writer(novig_answers, monkeypatch, no_resolver):
    monkeypatch.delenv("NOVIG", raising=False)
    monkeypatch.setenv("GEMINI", "0")
    calls = []

    def store_venue(con, venue, league, batch, observed_at, **kw):
        calls.append((venue, league, len(batch.records)))
        return {"inserted": len(batch.records)}
    monkeypatch.setattr(x, "store_venue", store_venue)
    out = x.run("con", ["nba"], observed_at=OBS)
    assert calls == [("novig", "nba", 3)] and out["nba"]["novig"]["offers"] == 3 and out["nba"]["novig"]["markets"] == 5


def test_requests_per_cycle_are_bounded(monkeypatch, no_resolver):
    fx_n, fx_g = _fx("novig_nba_catalog.json"), _fx("gemini_nfl_props.json")
    calls = []

    def fake_get(source, url, params=None, timeout=20.0, deadline=None):
        calls.append(source)
        assert timeout <= 20.0
        if source == "gemini":
            return _Resp({"data": fx_g["data"], "pagination": {"total": 3}})
        return _Resp(fx_n["events_answer"] if url.endswith("/events") else fx_n["markets_answer"])
    monkeypatch.setattr(x, "_get", fake_get)
    monkeypatch.setattr(x, "store_venue", lambda *a, **k: {})
    for v in ("NOVIG", "GEMINI"):
        monkeypatch.delenv(v, raising=False)
    x.run(None, ["nba", "nfl"], observed_at=OBS)
    assert calls.count("novig") == 4 and calls.count("gemini") == 2          # 3 requests per league, 6 a cycle


def test_the_time_budget_skips_what_would_start_after_it(novig_answers, monkeypatch, no_resolver):
    t = [0.0]

    def clock():
        t[0] += 25.0                                       # every step "takes" 25 s
        return t[0]
    wrote = []
    monkeypatch.setattr(x, "store_venue", lambda con, v, lg, b, obs, **kw: wrote.append((lg, v)) or {})
    for v in ("NOVIG", "GEMINI"):
        monkeypatch.delenv(v, raising=False)
    out = x.run(None, ["nba", "nfl"], budget_s=60, clock=clock, observed_at=OBS)
    skipped = [(lg, v) for lg in ("nba", "nfl") for v in ("novig", "gemini")
               if str(out[lg].get(v, {}).get("skipped", "")).startswith("time budget")]
    assert ("nfl", "novig") in skipped and ("nfl", "gemini") in skipped
    assert not [w for w in wrote if w[0] == "nfl"]         # nothing starts (or is written) after the budget


def test_run_due_keeps_the_cadence_and_records_the_attempt_first(monkeypatch, tmp_path):
    from sports import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ran = []
    monkeypatch.setattr(x, "run", lambda con, lgs, budget_s=None, mem_ok=None: ran.append(lgs) or {"ran_s": 1.0})
    assert x.run_due(None, ["nba"], now=1000.0)["due"] is True
    assert x.run_due(None, ["nba"], now=1000.0 + x.EVERY_S - 1)["due"] is False
    assert x.run_due(None, ["nba"], now=1000.0 + x.EVERY_S)["due"] is True
    assert ran == [["nba"], ["nba"]]


def test_the_books_job_step_never_fails_the_books_job(monkeypatch):
    from sports import worker
    from sports.books import exchanges
    monkeypatch.setattr(worker, "rss_mb", lambda: 1000.0)
    monkeypatch.setattr(exchanges, "run_due", lambda *a: pytest.fail("no pass over the memory cap"))
    assert worker._exchanges_step(None, ["nba"])["skipped"].startswith("memory")
    monkeypatch.setattr(worker, "rss_mb", lambda: 40.0)
    monkeypatch.setenv("SPORTS_JOB_BOOKS_CAP_MB", "108")
    monkeypatch.setattr(worker, "_check_budget", lambda: None)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(exchanges, "run_due", boom)
    assert worker._exchanges_step(None, ["nba"]) == {"ok": False, "error": "RuntimeError: x"}


def test_key_presence_only_never_its_value():
    pem = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"
    for k in ("Novig_api", "NOVIG_API", "novig_api_key", "NOVIG_API_KEY"):
        assert x.novig_key_present({k: pem})
    assert not x.novig_key_present({"OTHER": pem}) and not x.novig_key_present({"NOVIG_API": "  "})


# ------------------------------------------------------------------------------------------------ display only
def _nv(line=20.5, bid=0.50, ask=0.53, platform="novig"):
    return h.offer(platform=platform, oid=f"{platform}:{line}", line=line, variant="main",
                   sides=(SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask),
                          SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 4), ask=round(1 - bid, 4))))


def _poly(line=20.5, bid=0.40, ask=0.43):
    return h.offer(platform="polymarket", oid=f"pm:{line}", line=line, variant="main",
                   sides=(SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask),
                          SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 4), ask=round(1 - bid, 4))),
                   liquidity=(("liquidity", 500.0),))


def test_registry_marks_both_display_only_and_out_of_mc1():
    for v in ("novig", "gemini"):
        assert SOURCES[v]["display_only"] and not SOURCES[v]["consensus"] and display_only(v)
        assert not market.in_consensus(v)
        ok, why = market_view.source_status(v)
        assert not ok and why.startswith("display only")
    assert not display_only("polymarket") and not display_only("kalshi") and not display_only(None)


def test_never_a_source_of_the_registered_forecasts():
    target = h.offer()
    ev = {target.event_id: h.event()}
    q = [("rid:nv", _nv(), h.T0), ("rid:gm", _nv(platform="gemini"), h.T0)]
    per, reasons = market.eligible_quotes(target, q, ev, "2026-10-23T20:05:00Z", 0.0)
    assert per == {} and reasons == []                     # skipped before any rule: a forecast's reasons are unchanged
    p, why = market.quote_prob(_nv(), h.event(), "2026-10-23T20:05:00Z", h.T0)
    assert p is None and "not in mc-1" in why


def test_mv1_lists_its_price_but_the_market_chance_is_unchanged():
    pp, pm, nv = h.offer(), _poly(), _nv(bid=0.80, ask=0.82)
    now = "2026-10-23T20:05:00Z"

    def view(*offers):
        its = [(f"rid:{o.platform}", o, {"last_seen_at": o.observed_at}) for o in offers]
        return market_view.row_view([(its[0][0], pp)], its, h.event(), now, "nba", 0.0)
    with_nv, without = view(pp, pm, nv), view(pp, pm)
    assert with_nv["c"] == without["c"] and with_nv["c"]["n"] == 1 and with_nv["c"]["used"] == ["polymarket"]
    b = next(b for b in with_nv["bk"] if b["p"] == "novig")
    assert b["kind"] == "mid" and b["c"] == pytest.approx(0.81) and b["over"] == [0.8, 0.82] and b["in"] is False
    assert b["why"].startswith("display only")


def test_its_own_lines_follow_cap3():
    near, far, wide = _nv(bid=0.50, ask=0.53), _nv(bid=0.80, ask=0.82), _nv(bid=0.30, ask=0.60)
    snap = lambda o: {"sides": [dict(side=s.side, bid=s.bid, ask=s.ask, price_format=s.price_format, offered=s.offered)
                                for s in o.sides]}
    assert board_view.price_cap("novig", "over", board_view.own_chance(snap(near))) is None
    assert board_view.price_cap("novig", "over", board_view.own_chance(snap(far)))["v"] == "cap-3"   # Over at 81%
    assert board_view.price_cap("gemini", "under", board_view.own_chance({"k": [0.80, 0.82]})) is None
    assert board_view.own_chance(snap(wide)) is None                      # wider than 5 cents: no own price
    assert board_view.price_cap("novig", "over", None, None)["v"] == "cap-3"
    assert not board_view.is_pickem("novig") and not board_view.is_pickem("gemini")


def test_never_inside_the_league_collector_and_no_job_table_change():
    """The 2026-11-09 regression: inside the EXCLUSIVE ingest-<league> they doubled it. Now a step of the books job: the
    scheduler's job table is the one before (no new job, no new reservation)."""
    from sports import worker
    jobs = scheduler.default_jobs()
    for lg in ("nba", "nfl"):
        argv = jobs[f"ingest-{lg}"]["argv"]
        srcs = argv[argv.index("--sources") + 1].split(",")
        assert "novig" not in srcs and "gemini" not in srcs and "polymarket" in srcs
    assert "exchanges" not in jobs and jobs["books"]["cls"] == scheduler.LIGHT
    assert "novig" not in worker.SOURCES and "gemini" not in worker.SOURCES


def test_spread_limit_is_the_frozen_exchange_rule():
    assert x.MAX_SPREAD == market.MAX_SPREAD == board_view.EXCHANGE_MAX_SPREAD
