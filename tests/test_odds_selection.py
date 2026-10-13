"""market_odds odds-source-2 (2026-10-13): ONE selection policy (Thunderpick -> Polymarket -> OddsPapi by the network flag ->
no market), its provenance, the odds signature, the re-check at every regrade, immutability, fallback reasons, never 50%.
ALL quotes, ids and teams here are LABELLED TEST FIXTURES (the ALLE / ex-Keyd Stars / Galorys names follow the audited
UNO MILLE chain; the prices are invented for the test)."""
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import market_odds as M
import polymarket


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


NOW = datetime.now(timezone.utc)
START = iso(NOW + timedelta(hours=6))


def tp_found(**over):
    """A Thunderpick reader answer in the WS1 contract shape (TEST FIXTURE)."""
    pv = {"source": "thunderpick", "policy": "tp-first-2", "market": "Match Winner", "market_id": "event-level main market",
          "scope": "series winner", "series_format": "bo3", "draws_possible": False, "match_id": "hltv:2399204",
          "source_event_id": "thunderpick:fx:2816445", "team_is_a": False, "flipped": True, "odds_team": 2.47,
          "odds_opp": 1.48, "p_team": 0.374684, "retrieved_at": iso(NOW - timedelta(seconds=30)), "quote_age_s": 30,
          "availability": "current", "snapshot_sha256": "a" * 64, "canonical_start": START, "label": "TEST FIXTURE"}
    pv.update(over)
    return {"found": True, "team": "ex-Keyd Stars", "opponent": "Galorys", "start": START, "url": "https://thunderpick.io/match/2816445",
            "title": "ex-Keyd Stars vs Galorys", "book": "thunderpick", "team_prob": pv["p_team"], "opp_prob": round(1 - pv["p_team"], 6),
            "team_ml": 167, "opp_ml": -167, "maps": {}, "flags": [], "provenance": pv}


class TP:
    """Injectable Thunderpick reader with the WS1 signature (accepts bo=); records its calls."""
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, team, opponent, start_iso, now=None, poller=None, canonical_fn=None, bo=None):
        self.calls.append({"team": team, "opponent": opponent, "start": start_iso, "bo": bo})
        a = self.answer() if callable(self.answer) else self.answer
        return copy.deepcopy(a)


PM = {"found": True, "team": "ex-Keyd Stars", "opponent": "Galorys", "team_prob": 0.325, "opp_prob": 0.675, "team_ml": 208,
      "opp_ml": -208, "maps": {"map1": {"team_prob": 0.4}}, "liquidity": 5000, "source": "midpoint", "flags": [],
      "url": "https://polymarket.com/event/cs2-keydst-gls1-2026-10-13", "title": "TEST FIXTURE", "start": START}
NOT_PM = {"found": False, "query": "x", "note": "No open Polymarket market for x - TEST FIXTURE"}
TP_EMPTY = {"found": False, "reason": "Thunderpick's latest answer listed no CS2 markets (TEST FIXTURE)", "availability": "endpoint_empty"}
TP_STALE = {"found": False, "reason": "Thunderpick quote stale (TEST FIXTURE)", "availability": "stale"}
OP = {"found": True, "team": "ex-Keyd Stars", "opponent": "Galorys", "book": "pinnacle", "team_prob": 0.4, "opp_prob": 0.6,
      "team_ml": 150, "opp_ml": -150, "start": START, "title": "TEST FIXTURE", "maps": {}, "flags": [], "decimal": [2.4, 1.6],
      "fixture_id": "fx-test-1", "retrieved_at": iso(NOW)}

COMMON = ("selection", "odds_policy", "selected", "source", "fallback_reason", "tp_availability", "mapping_rev", "sig",
          "selected_at", "query", "network", "oddspapi_mode")


def test_thunderpick_first_with_the_full_provenance_and_the_match_format():
    tp = TP(tp_found())
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=tp, pm=lambda *a: dict(PM), bo=3)
    pv = o["provenance"]
    assert o["found"] and pv["selected"] == "thunderpick" and pv["fallback_reason"] is None and o["maps"] == {}
    assert all(k in pv for k in COMMON)
    assert tp.calls == [{"team": "ex-Keyd Stars", "opponent": "Galorys", "start": START, "bo": 3}]   # bo passed through
    assert pv["query"] == {"team": "ex-Keyd Stars", "opponent": "Galorys", "start": START, "bo": 3}
    assert pv["tp_availability"] == "current" and pv["mapping_rev"].startswith("map-") and len(pv["sig"]) == 40
    assert pv["odds_policy"] == M.odds_policy() and M.odds_policy().startswith("odds-source-2|")
    assert M.p_team(o) == 0.374684                                    # the exact selected probability, no ML round trip
    for k in ("match_id", "source_event_id", "odds_team", "snapshot_sha256", "retrieved_at", "label"):
        assert pv[k] == tp_found()["provenance"][k]                   # Thunderpick's own provenance kept as given


def test_odds_policy_names_the_selection_rule_and_the_thunderpick_policy():
    import thunderpick
    assert M.odds_policy() == f"odds-source-2|{thunderpick.POLICY_VERSION}"


def test_old_readers_without_bo_still_work():
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a: tp_found(), pm=lambda *a: dict(PM), bo=3)
    assert o["provenance"]["selected"] == "thunderpick"

    def old_reader(team, opponent, start_iso, now=None):
        return tp_found()
    wrapped = lambda *a, **k: old_reader(*a, **k)                       # looks like it takes bo=, does not
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=wrapped, pm=lambda *a: dict(PM), bo=3)
    assert o["provenance"]["selected"] == "thunderpick"


def test_fallback_records_thunderpicks_reason_and_availability_and_polymarket_ids():
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_EMPTY), pm=lambda *a: dict(PM))
    pv = o["provenance"]
    assert pv["selected"] == "polymarket" and o["team_prob"] == 0.325 and o["maps"] == PM["maps"]
    assert pv["fallback_reason"] == TP_EMPTY["reason"] + " [availability: endpoint_empty]" and pv["tp_availability"] == "endpoint_empty"
    assert pv["event_slug"] == "cs2-keydst-gls1-2026-10-13" and pv["price_source"] == "midpoint" and pv["liquidity"] == 5000
    # what match_odds did not return is None - never invented
    assert pv["market_id"] is None and pv["condition_id"] is None and pv["retrieved_at"] is None
    assert pv["source_updated_at"] is None


def test_polymarket_provenance_from_the_real_reader():
    ev = [{"slug": "cs2-test-fixture", "markets": [{
        "sportsMarketType": "moneyline", "outcomes": json.dumps(["Galorys", "ex-Keyd Stars"]),
        "outcomePrices": json.dumps(["0.675", "0.325"]), "bestBid": "0.67", "bestAsk": "0.68", "liquidityNum": 4000,
        "volumeNum": 900, "id": "558866", "conditionId": "0xTESTFIXTURE", "question": "Counter-Strike: Galorys vs ex-Keyd Stars",
        "gameStartTime": (NOW + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S+00")}]}]
    at = time.time() - 20
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE),
                 pm=lambda t, op: polymarket.match_odds(t, op, events=ev, events_at=at))
    pv = o["provenance"]
    assert pv["selected"] == "polymarket" and pv["market_id"] == "558866" and pv["condition_id"] == "0xTESTFIXTURE"
    assert pv["outcomes"] == ["Galorys", "ex-Keyd Stars"] and pv["outcome_prices"] == ["0.675", "0.325"]
    assert pv["best_bid"] == "0.67" and pv["retrieved_at"] == datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")
    assert o["team"] == "ex-Keyd Stars" and abs(o["team_prob"] - 0.325) < 1e-9


def test_oddspapi_is_live_only_for_member_contexts_and_recorded():
    seen = []

    def op(team, opponent, cache_only=False, max_age=None):
        seen.append((cache_only, max_age))
        return dict(OP)
    member = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(NOT_PM), op=op, network=True)
    board = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(NOT_PM), op=op, network=False)
    assert seen == [(False, None), (True, M.OP_CACHE_MAX_AGE_S)]
    assert member["provenance"]["network"] is True and member["provenance"]["oddspapi_mode"] == "live"
    assert board["provenance"]["network"] is False and board["provenance"]["oddspapi_mode"] == "cache-only"
    for o in (member, board):
        pv = o["provenance"]
        assert pv["selected"] == "oddspapi" and pv["book"] == "pinnacle" and pv["fixture_id"] == "fx-test-1"
        assert pv["fallback_reason"] == TP_STALE["reason"] and pv["polymarket_note"] == NOT_PM["note"]
        assert pv["p_team"] == round((1 / 2.4) / (1 / 2.4 + 1 / 1.6), 6)          # exact de-vig, not the 3-dp display
    assert member["provenance"]["sig"] == board["provenance"]["sig"]         # same price -> same market input


def test_oddspapi_cache_only_never_makes_a_request(monkeypatch):
    import oddspapi
    monkeypatch.setattr(oddspapi, "key", lambda: "TEST-FIXTURE-NOT-A-KEY")
    monkeypatch.setattr(oddspapi, "_get", lambda *a, **k: pytest.fail("request in cache-only mode"))
    fx = {"fixtureId": "fx1", "participant1Name": "ex-Keyd Stars", "participant2Name": "Galorys", "startTime": START,
          "tournamentName": "TEST FIXTURE"}
    odds = {"bookmakerOdds": {"pinnacle": {"markets": {"171": {"outcomes": {
        "171": {"players": {"0": {"price": 2.4}}}, "172": {"players": {"0": {"price": 1.6}}}}}}}}}
    got_at = time.time() - 900                                        # fetched by a member's grade 15 min ago
    monkeypatch.setattr(oddspapi, "_FIX", {"at": got_at, "data": [fx]})
    monkeypatch.setattr(oddspapi, "_ODDS", {"fx1": (got_at, odds)})
    a = oddspapi.match_odds("ex-Keyd Stars", "Galorys", cache_only=True, max_age=M.OP_CACHE_MAX_AGE_S)
    assert a["found"] and a["fixture_id"] == "fx1" and a["cache_only"] is True and a["decimal"] == [2.4, 1.6]
    assert a["retrieved_at"] == datetime.fromtimestamp(got_at, timezone.utc).isoformat(timespec="seconds")  # never refreshed
    assert oddspapi.match_odds("ex-Keyd Stars", "Galorys", cache_only=True)["found"] is False   # older than 10 min default
    monkeypatch.setattr(oddspapi, "_FIX", {"at": 0.0, "data": None})
    assert "cache-only" in oddspapi.match_odds("ex-Keyd Stars", "Galorys", cache_only=True, max_age=1800)["note"]


def test_board_and_member_select_the_same_when_thunderpick_or_polymarket_prices():
    for tp, pm in ((TP(tp_found()), lambda *a: dict(PM)), (TP(TP_STALE), lambda *a: dict(PM))):
        a = M.select("ex-Keyd Stars", "Galorys", START, tp=tp, pm=pm, network=True)
        b = M.select("ex-Keyd Stars", "Galorys", START, tp=tp, pm=pm, network=False)
        assert a["provenance"]["sig"] == b["provenance"]["sig"] and a["provenance"]["selected"] == b["provenance"]["selected"]
        assert (a["provenance"]["network"], b["provenance"]["network"]) == (True, False)
        assert a["provenance"]["oddspapi_mode"] is None                     # OddsPapi never consulted


def test_no_market_is_never_a_50_percent_stand_in():
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_EMPTY), pm=lambda *a: dict(NOT_PM),
                 op=lambda *a, **k: {"found": False, "note": "OddsPapi: no recent fixture list in memory (cache-only)"},
                 network=False)
    pv = o["provenance"]
    assert o["found"] is False and "team_prob" not in o and "team_ml" not in o and M.p_team(o) is None
    assert pv["selected"] is None and pv["fallback_reason"] == TP_EMPTY["reason"] + " [availability: endpoint_empty]"
    assert pv["tp_availability"] == "endpoint_empty"
    assert pv["polymarket_note"] == NOT_PM["note"] and "cache-only" in pv["oddspapi_note"]
    # no opponent: OddsPapi is not consulted at all (as before)
    o2 = M.select("ex-Keyd Stars", None, START, tp=TP(TP_EMPTY), pm=lambda *a: dict(NOT_PM),
                  op=lambda *a, **k: pytest.fail("OddsPapi called without an opponent"))
    assert o2["found"] is False and o2["provenance"]["oddspapi_mode"] is None


def test_a_thunderpick_error_never_blocks_grading():
    def boom(*a, **k):
        raise RuntimeError("fixture failure")
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=boom, pm=lambda *a: dict(PM))
    assert o["provenance"]["selected"] == "polymarket" and "error" in o["provenance"]["fallback_reason"]


def test_the_selection_is_an_independent_deep_copy():
    src = tp_found()
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a: src, pm=lambda *a: dict(PM))
    src["provenance"]["odds_team"] = 99.0
    src["team_prob"] = 0.99
    assert o["provenance"]["odds_team"] == 2.47 and o["team_prob"] == 0.374684
    o2 = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a: tp_found(), pm=lambda *a: dict(PM))
    o2["provenance"]["query"]["team"] = "mutated"
    o3 = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a: tp_found(), pm=lambda *a: dict(PM))
    assert o3["provenance"]["query"]["team"] == "ex-Keyd Stars"


def test_grader_inputs_never_mutate_the_selected_odds(monkeypatch):
    import cs2_scout
    monkeypatch.setattr(cs2_scout, "_perf_input", lambda pb, args: None)
    monkeypatch.setattr(cs2_scout, "hltv_first_gates", lambda pb, d: None)
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM), bo=3)
    before = copy.deepcopy(o)
    pb = {"overall": {}, "per_map": [], "teammate_ratings": {}, "is_igl": False, "sniping_equiv": False, "is_standin": False,
          "roster_confirmed": False, "standin_for": "ex-Keyd Stars"}
    args = SimpleNamespace(prop="kills", line=24.5, line_type="standard", scope="12", match_bo=3, team_ml=None, opp_ml=None, web=True)
    gi = cs2_scout.grader_inputs(pb, args, o)
    assert o == before
    gi["odds_provenance"]["odds_team"] = 1.01                           # a copy: the odds object is untouched
    assert o["provenance"]["odds_team"] == 2.47
    assert gi["p_team_market"] == 0.374684 and gi["odds_policy"] == o["provenance"]["odds_policy"]
    assert gi["mapping_rev"] == o["provenance"]["mapping_rev"] and gi["market_quality"] == "SOFT"
    none = M.select("x", "y", START, tp=TP(TP_EMPTY), pm=lambda *a: dict(NOT_PM), op=lambda *a, **k: {"found": False})
    gi0 = cs2_scout.grader_inputs(pb, args, none)
    assert gi0["p_team_market"] is None and "team_ml" not in gi0 and gi0["market_quality"] == "NONE"


def test_signature_ignores_times_and_snapshots_but_not_prices_source_or_orientation():
    sig = lambda **ov: M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a: tp_found(**ov), pm=lambda *a: dict(PM))["provenance"]["sig"]
    base = sig()
    assert sig(retrieved_at=iso(NOW), quote_age_s=2, snapshot_sha256="b" * 64, event_last_observed=iso(NOW)) == base
    assert sig(odds_team=2.5, p_team=0.371) != base                     # the price moved
    assert sig(team_is_a=True, flipped=False) != base                   # the other side of the same match
    assert sig(match_id="hltv:1") != base
    pm_sig = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(PM))["provenance"]["sig"]
    assert pm_sig != base                                               # another source
    nm = lambda r: M.select("x", "y", START, tp=TP(r), pm=lambda *a: dict(NOT_PM), op=lambda *a, **k: {"found": False})
    assert nm(TP_EMPTY)["provenance"]["sig"] == nm(TP_STALE)["provenance"]["sig"]   # no market is one input


def test_revalidate_returns_the_cached_object_when_nothing_changed():
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM), bo=3)
    snap = copy.deepcopy(cached)
    newer = TP(tp_found(retrieved_at=iso(NOW), snapshot_sha256="c" * 64, quote_age_s=1))   # re-polled, same quote
    out = M.revalidate(cached, tp=newer, pm=lambda *a: dict(PM))
    assert out is cached and cached == snap and newer.calls[0]["bo"] == 3


def test_revalidate_drops_a_quote_that_is_no_longer_current():
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM))
    snap = copy.deepcopy(cached)
    for state in (TP_STALE, TP_EMPTY, {"found": False, "reason": "match started (TEST FIXTURE)", "availability": "started"}):
        out = M.revalidate(cached, tp=TP(state), pm=lambda *a: dict(PM))
        assert out is not cached and out["provenance"]["selected"] == "polymarket"
        assert out["provenance"]["tp_availability"] == state["availability"]
    assert cached == snap                                               # the cached selection is never mutated
    # and a moved Thunderpick price is a new input
    out = M.revalidate(cached, tp=TP(tp_found(odds_team=2.6, odds_opp=1.44, p_team=0.355)), pm=lambda *a: dict(PM))
    assert out["provenance"]["selected"] == "thunderpick" and out["provenance"]["sig"] != cached["provenance"]["sig"]


def test_revalidate_sees_an_availability_change_with_the_same_input():
    nf = lambda *a, **k: {"found": False}
    cached = M.select("x", "y", START, tp=TP({"found": False, "reason": "not linked", "availability": "unlinked"}),
                      pm=lambda *a: dict(NOT_PM), op=nf)
    out = M.revalidate(cached, tp=TP(TP_EMPTY), pm=lambda *a: dict(NOT_PM), op=nf)
    assert out is not cached and out["provenance"]["sig"] == cached["provenance"]["sig"]
    assert out["provenance"]["tp_availability"] == "endpoint_empty"


def test_a_failing_recheck_keeps_the_cached_input():
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(PM))

    def down(*a):
        raise ConnectionError("Polymarket down (TEST FIXTURE)")
    assert M.revalidate(cached, tp=TP(TP_STALE), pm=down) is cached
    assert M.revalidate({"found": True, "team_prob": 0.5}) == {"found": True, "team_prob": 0.5}   # not from select(): as is


def test_shared_recheck_is_one_evaluation_and_never_outlives_the_quote(monkeypatch):
    """700 members at once: identical re-checks within REVAL_TTL_S share one evaluation - but never past the selected
    Thunderpick quote's eligibility, and never one made before the cached selection itself."""
    M._REVAL.clear()
    tp = TP(lambda: tp_found(retrieved_at=iso(datetime.now(timezone.utc) - timedelta(seconds=30))))
    monkeypatch.setattr(M, "_tp_default", lambda: tp)
    monkeypatch.setattr(polymarket, "cached_events", lambda: ([], time.time()))
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=tp, pm=lambda *a: dict(PM), bo=3)
    n0 = len(tp.calls)
    for _ in range(25):
        assert M.revalidate(cached) is cached
    assert len(tp.calls) == n0 + 1                                    # one evaluation for 25 re-checks
    # a quote retrieved 179.5 s ago expires inside the window: the shared result is not reused past it
    M._REVAL.clear()
    tp.answer = lambda: tp_found(retrieved_at=iso(datetime.now(timezone.utc) - timedelta(seconds=179.5)))
    M.revalidate(cached)
    key = next(iter(M._REVAL))
    assert M._REVAL[key][0] <= datetime.now(timezone.utc).timestamp() + 1.0
    import thunderpick
    assert M._REVAL[key][0] <= M._ts(tp.answer()["provenance"]["retrieved_at"]) + thunderpick.QUOTE_MAX_AGE_S + 1


def test_cached_events_recheck_makes_no_request(monkeypatch):
    """The re-check reads Polymarket's list already in memory - never a download on a member's clock."""
    monkeypatch.setattr(polymarket, "upcoming_events", lambda *a, **k: pytest.fail("download on a re-check"))
    monkeypatch.setattr(polymarket, "cached_events", lambda: ([], time.time()))
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(PM))
    out = M.revalidate(cached, tp=TP(TP_STALE))                       # Polymarket's market gone from the list in memory
    assert out["found"] is False and out["provenance"]["selected"] is None


def test_finish_line_rechecks_the_odds_before_the_grader_inputs():
    import inspect, cs2_scout
    src = inspect.getsource(cs2_scout.finish_line)
    assert src.index('res["odds"], res["odds_check"] = _revalidated_odds(res.get("odds"))') < src.index('res["grader_inputs"] = grader_inputs(')
    run_src = inspect.getsource(cs2_scout.run)
    assert 'bo=getattr(args, "match_bo", None), network=not b.background' in run_src
    # the odds team is the team he plays THIS match for (playing_for), then his team now
    assert 'team = args.name if args.cmd == "odds" else (args.team or pb.get("playing_for") or pb.get("current_team"))' in run_src


def test_revalidated_odds_passes_through_objects_it_cannot_recheck(monkeypatch):
    import cs2_scout
    assert cs2_scout._revalidated_odds(None) == (None, None)
    legacy = {"found": True, "team_prob": 0.6, "provenance": {"selected": "polymarket"}}       # no query: old context
    assert cs2_scout._revalidated_odds(legacy)[0] is legacy
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM))
    monkeypatch.setattr(M, "recheck", lambda od, **k: (_ for _ in ()).throw(RuntimeError("x")))
    od, chk = cs2_scout._revalidated_odds(cached)
    assert od is cached and chk["result"].startswith("failed")      # a failing re-check keeps the context's odds


def test_cli_override_replaces_the_market_input_and_never_applies_to_web_grades():
    import cs2_scout
    o = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(PM))
    web = SimpleNamespace(team_ml=-150, opp_ml=130, web=True)
    assert cs2_scout._cli_odds(web, o) is o
    cli = cs2_scout._cli_odds(SimpleNamespace(team_ml=-150, opp_ml=130), o)
    assert cli["maps"] == {} and cli["provenance"]["selected"] == "manual" and cs2_scout.market_quality(cli) == "SOFT"
    assert (cli["team_ml"], cli["opp_ml"]) == (-150, 130) and abs(cli["provenance"]["p_team"] - (0.6 / (0.6 + 100 / 230))) < 1e-6
    assert cli["provenance"]["replaced"]["selected"] == "polymarket"  # what it replaced is recorded, not mixed in
    assert cs2_scout._cli_odds(SimpleNamespace(team_ml=None, opp_ml=None), o) is o


def test_cli_summary_prints_any_source(capsys):
    import cs2_scout
    for o in (M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM)),
              M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(NOT_PM),
                       op=lambda *a, **k: dict(OP)),
              M.select("x", "y", START, tp=TP(TP_EMPTY), pm=lambda *a: dict(NOT_PM), op=lambda *a, **k: {"found": False})):
        cs2_scout.print_summary({"odds": o})                              # KeyError 'liquidity' before 2026-10-13
    out = capsys.readouterr().out
    assert "== Thunderpick:" in out and "== OddsPapi (pinnacle):" in out and "== No market:" in out


def test_compare_swaps_the_exact_probability_too():
    import grader
    from pathlib import Path
    sample = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))
    seen = {}
    real = grader.grade_leg

    def spy(d, *a, **k):
        seen.setdefault("inputs", []).append(dict(d))
        return real(d, *a, **k)
    grader.grade_leg = spy
    try:
        rep = M.compare([{"inputs": dict(sample["inputs"], p_team_market=0.9), "team": "a", "opponent": "b", "start": None}],
                        lambda *a: tp_found())
    finally:
        grader.grade_leg = real
    assert rep["compared"] == 1 and seen["inputs"][1]["p_team_market"] == 0.374684


def test_context_key_follows_the_mapping_revision(monkeypatch, tmp_path):
    """An alias change re-evaluates the line: the shared-context key changes, so the next grade rebuilds its context
    (odds re-selected under the new mapping) instead of serving the one linked under the old aliases."""
    import cs2_scout, matchid, mapping
    f = tmp_path / "team_aliases.json"
    f.write_text(json.dumps({"aliases": [{"name": "NIP", "team_id": 4411, "proof": "TEST FIXTURE"}]}), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", f)
    calls = []
    monkeypatch.setattr(cs2_scout, "BO3", lambda **k: SimpleNamespace(close=lambda: None, background=k.get("background")))
    monkeypatch.setattr(cs2_scout, "run", lambda args, res, b, ttl: calls.append(mapping.revision()) or res.update(player_block={}))
    monkeypatch.setattr(cs2_scout, "finish_line", lambda res, args: None)
    monkeypatch.setattr(cs2_scout, "_CONTEXT", {})
    key = lambda: cs2_scout._context_key("ALLE", None, None, "Galorys", "ex-Keyd Stars", None, None)
    k1, r1 = key(), mapping.revision()
    for _ in range(2):
        cs2_scout.grade("ALLE", 24.5, background=True, opponent="Galorys", team_hint="ex-Keyd Stars")
    assert len(calls) == 1                                            # second grade reused the context
    f.write_text(json.dumps({"aliases": [{"name": "NIP", "team_id": 4411, "proof": "TEST FIXTURE"},
                                         {"name": "ex-Keyd Stats", "team_id": 13423, "proof": "TEST FIXTURE"}]}), encoding="utf-8")
    k2, r2 = key(), mapping.revision()
    assert r2 != r1 and k2 != k1 and k1[-1] == r1 and k2[-1] == r2
    cs2_scout.grade("ALLE", 24.5, background=True, opponent="Galorys", team_hint="ex-Keyd Stars")
    assert calls == [r1, r2]                                          # rebuilt under the new mapping


def test_reader_error_keeps_a_cached_thunderpick_quote_only_inside_its_own_window():
    """A Thunderpick READER failure (e.g. sqlite locked) is not a feed state: the cached quote is kept while it is inside
    retrieval + QUOTE_MAX_AGE_S, never past it (eligibility: Thunderpick ONLY when current)."""
    import thunderpick

    def boom(*a, **k):
        raise RuntimeError("database is locked (TEST FIXTURE)")
    fresh = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM))
    out, chk = M.recheck(fresh, tp=boom, pm=lambda *a: dict(PM))
    assert out is fresh and chk["result"].startswith("kept: Thunderpick reader error")
    old = M.select("ex-Keyd Stars", "Galorys", START, pm=lambda *a: dict(PM),
                   tp=TP(tp_found(retrieved_at=iso(NOW - timedelta(seconds=thunderpick.QUOTE_MAX_AGE_S + 5)))))
    out2, chk2 = M.recheck(old, tp=boom, pm=lambda *a: dict(PM))
    assert out2["provenance"]["selected"] == "polymarket" and chk2["result"] == "changed"


def test_polymarket_failure_on_a_recheck_never_keeps_a_non_current_thunderpick_quote():
    def down(*a):
        raise ConnectionError("Polymarket down (TEST FIXTURE)")
    nf = lambda *a, **k: {"found": False, "note": "no cached OddsPapi price (TEST FIXTURE)"}
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM))
    out, chk = M.recheck(cached, tp=TP(TP_EMPTY), pm=down, op=nf)
    assert out is not cached and out["found"] is False and out["provenance"]["pm_error"]
    assert out["provenance"]["tp_availability"] == "endpoint_empty"     # explicit no-market, never a 50% stand-in
    with pytest.raises(ConnectionError):                                # a context build still fails as before
        M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_EMPTY), pm=down, op=nf)


def test_a_stake_cap_class_change_with_the_same_price_is_a_new_input():
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(TP_STALE), pm=lambda *a: dict(PM))
    thin = dict(PM, liquidity=200)                                       # same price, book now thin (TEST FIXTURE)
    q = lambda o: "HARD" if (o.get("liquidity") or 0) >= 1000 else "SOFT"
    out = M.revalidate(cached, tp=TP(TP_STALE), pm=lambda *a: dict(thin), quality=q)
    assert out is not cached and out["provenance"]["sig"] == cached["provenance"]["sig"] and out["liquidity"] == 200


def test_recheck_record_never_refreshes_the_quotes_own_time():
    cached = M.select("ex-Keyd Stars", "Galorys", START, tp=TP(tp_found()), pm=lambda *a: dict(PM))
    later = tp_found(quote_age_s=95)
    out, chk = M.recheck(cached, tp=TP(later), pm=lambda *a: dict(PM))
    assert out is cached and chk["result"] == "confirmed" and chk["quote_age_s"] == 95
    assert out["provenance"]["retrieved_at"] == cached["provenance"]["retrieved_at"]
