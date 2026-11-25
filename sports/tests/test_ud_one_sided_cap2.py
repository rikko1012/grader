"""cap-2, owner decision 2026-11-06: "Stop grading full-game lines that only offer one side. Keep them labeled 'Over
only' (or 'Under only') but do not grade or stake them."

A stored one-sided Underdog line (adapters.ud_offers: variant one_sided = Underdog LISTS one side) stays on the board
row with its line and price, labelled by its side, and gets no forecast, Grader chance, grade, letter, stake, slip leg,
tracked pick or record entry. A two-sided line whose other side is suspended right now is NOT one-sided (its stored
type decides) and keeps its grading. Every name, line and probability is a TEST FIXTURE."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import adapters, capabilities, picks, snapshots, store, worker
from sports.contracts import SideQuote
from sports.slips import pool
from sports.tests import helpers as h
from sports.tests import slip_world as w

KAT = "nba:player:espn:3134907"
ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "sports"
OVER_ONLY = (SideQuote("over", True, "payout_multiplier", multiplier=0.86), SideQuote("under", False))
SUSPENDED_UNDER = (SideQuote("over", True, "payout_multiplier", multiplier=1.0), SideQuote("under", False))


def _ud(oid, variant, sides, line=20.5, **kw):
    return h.offer(platform="underdog", oid=oid, variant=variant, sides=sides, line=line, **kw)


# ------------------------------------------------------------------------------------------------ the gate
def test_gate_refuses_a_full_game_one_sided_line_by_its_stored_type():
    one = _ud("ud:one", "one_sided", OVER_ONLY)
    ok, why = capabilities.gate(one)
    assert not ok and why == [capabilities.ONE_SIDED_REASON]
    assert capabilities.is_ordinary(one) and capabilities.is_one_sided(one) and capabilities.only_side(one) == "over"
    under = _ud("ud:under", "one_sided", (SideQuote("over", False), SideQuote("under", True)))
    assert capabilities.only_side(under) == "under" and not capabilities.gate(under)[0]
    # a two-sided line whose Under is suspended right now keeps its grading (stored 'balanced')
    susp = _ud("ud:susp", "balanced", SUSPENDED_UNDER)
    assert capabilities.gate(susp) == (True, []) and not capabilities.is_one_sided(susp)
    assert capabilities.only_side(susp) is None
    # PrizePicks standard / goblin unchanged
    assert capabilities.gate(h.offer())[0]
    assert not capabilities.gate(h.offer(variant="goblin"))[0]
    assert capabilities.CAPABILITY_VERSION == "cap-2"


def test_adapter_fixture_full_game_one_sided_lines_are_refused():
    from sports import config
    from sports.tests.conftest import fixture
    b = adapters.ud_offers(fixture("ud_nba_period_20261105.json"), config.league("nba"), h.T0, None)
    full_one = [r for r in b.records if r.scope == "full_game" and r.variant == "one_sided"]
    assert full_one
    assert all(capabilities.ONE_SIDED_REASON in capabilities.gate(r)[1] for r in full_one)
    two = [r for r in b.records if r.scope == "full_game" and r.variant == "balanced"]
    assert two and all(capabilities.ONE_SIDED_REASON not in capabilities.gate(r)[1] for r in two)


def test_pick_refusal_is_plain_words():
    t = picks.plain_gate_reason([capabilities.ONE_SIDED_REASON])
    assert t == "Grader doesn't grade or track this pick: the line offers one side only."
    assert "one_sided" not in t and "variant" not in t and "No bet" not in t


# ------------------------------------------------------------------------------------------------ slip pool
@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


def test_slip_pool_never_offers_a_one_sided_leg(con):
    w.world(con, [("g1", "H1", 1, "over", 0.6), ("g2", "A2", 2, "under", 0.6)])
    one = w.offer("oneside", "H3", 3, sides=("over",), variant="one_sided", multiplier=0.86)
    w.put_leg(con, one, side="over", p=0.7)                    # a forecast saved before cap-2
    susp = w.offer("susp", "H3", 3, stat="rebounds", sides=("over",))   # stored balanced, Under suspended
    w.put_leg(con, susp, side="over", p=0.62)
    p = pool.build(con, "nba", "underdog", now=w.NOW)
    got = {l["player_id"].split(":")[-1] for l in p["legs"]}
    assert "oneside" not in got and {"g1", "g2", "susp"} <= got
    assert p["excluded"] == {"not gradable": 1}, p["excluded"]           # the one-sided leg, refused at the gate


# ------------------------------------------------------------------------------------------------ board build
def _board_world(con):
    store.put(con, h.event())
    pp = store.put(con, h.offer(oid="pp:1", line=20.5))["record_id"]
    one = store.put(con, _ud("ud:one", "one_sided", OVER_ONLY))["record_id"]
    susp = store.put(con, _ud("ud:susp", "balanced", SUSPENDED_UNDER, stat="rebounds", line=8.5))["record_id"]
    for orid, line, stat, sides in ((pp, 20.5, "points", ("over", "under")), (one, 20.5, "points", ("over",)),
                                    (susp, 8.5, "rebounds", ("over",))):
        store.put(con, h.forecast(line=line, stat=stat, offer_snapshot_ids=(orid,), model_version="sm-1",
                                  sides_available=sides, chosen_side="over", p_over=0.6, p_under=0.4, p_push=0.0,
                                  p_over_given_no_push=0.6, p_under_given_no_push=0.4))
    return pp, one, susp


def _quotes(base):
    out = {}
    for part in ("board.json", "board_more.json"):
        f = base / "nba" / part
        if f.exists():
            for m in json.loads(f.read_text()).get("markets") or []:
                for q in m.get("q") or []:
                    out[q["r"]] = q
    return out


def test_board_shows_the_one_sided_line_with_no_forecast_or_grade(tmp_path):
    with store.opened() as con:
        pp, one, susp = _board_world(con)
        snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path, history=False, results=False)
    qs = _quotes(tmp_path)
    assert {pp, one, susp} <= set(qs), sorted(qs)
    q1 = qs[one]
    assert q1["v"] == "one_sided" and q1["o"] == [1, 0] and q1["m"] == [0.86, None] and q1["l"] == 20.5
    assert q1["g"] == 0 and not q1.get("f") and not q1.get("lr") and not q1.get("x") and not q1.get("sc")
    assert q1.get("nf", "").startswith("Over only line")
    assert qs[pp].get("f")                                     # the PrizePicks line at the same number keeps its forecast
    assert qs[susp].get("f") and qs[susp]["g"] == 1            # suspended Under: still graded (stored type)


def test_same_line_join_skips_one_sided_quotes():
    fc = {"f1": {"forecast_id": "f1", "model_version": "sm-1", "forecast_at": "2026-10-23T20:00:00Z"}}
    row = {"quotes": [{"line": 20.5, "forecast_id": "f1", "variant": "standard", "sides": []},
                      {"line": 20.5, "forecast_id": None, "variant": "one_sided",
                       "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": False}]},
                      {"line": 20.5, "forecast_id": None, "variant": "standard", "sides": []}]}
    assert snapshots.same_line_forecasts(row, fc) == 1
    assert row["quotes"][1]["forecast_id"] is None and row["quotes"][2]["forecast_id"] == "f1"


# ------------------------------------------------------------------------------------------------ record / ledger
def _fake_espn(monkeypatch, value):
    from sports import espn
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: ({"completed": True}, None))

    def box(summ, lg, event_id, observed_at):
        b = adapters.Batch()
        for part in ("pts", "reb"):
            b.add(h.stat(pid=KAT, part=part, value=value, eid=event_id, observed_at=observed_at))
        return b
    monkeypatch.setattr(adapters, "espn_box", box)


def test_record_and_ledger_leave_out_one_sided_forecasts(monkeypatch, tmp_path):
    from sports import results_public
    _fake_espn(monkeypatch, 25.0)
    with store.opened() as con:
        pp, one, susp = _board_world(con)
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        setts = [s for _, s in store.settlements(con)]
        assert {s.offer_id for s in setts} >= {"pp:1", "ud:one", "ud:susp"}
        led = snapshots.results_view(con, "nba", setts, "2026-10-24T06:00:00Z", {}, rows_only=True)
        rows = results_public._rows_from(con, "nba", setts)
    led_rows = led if isinstance(led, list) else led.get("rows", [])
    assert {r["offer_id"] for r in led_rows} == {"pp:1", "ud:susp"}
    assert len(rows) == 2 and all(set(r["platforms"]) != {"underdog"} or r["stat"] == "rebounds" for r in rows)


# ------------------------------------------------------------------------------------------------ gradecheck
def test_gradecheck_never_counts_a_one_sided_row_as_shown():
    from sports.model import gradecheck
    base = dict(po=0.62, pu=0.38, pp=0.0, mean=22.0, line=20.5, status="win", value=25.0, fid="f", l10=None, iv=None)
    rows = gradecheck.score_rows([dict(base, variant="one_sided"), dict(base, variant="balanced"), dict(base)])
    assert [r["shown"] for r in rows] == [False, True, True]


# ------------------------------------------------------------------------------------------------ pages (node)
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_board_row_card_and_actions_label_the_side_and_never_grade():
    r = subprocess.run(["node", str(Path(__file__).with_name("ud_one_sided_cap2_test.js")), str(WEB)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_pages_never_print_the_internal_type_name():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "q.v !== 'one_sided'" in app and '"ladder_rung", "one_sided"].indexOf(v)' in app
    lab = (WEB / "labels.js").read_text(encoding="utf-8")
    assert "One side only (not graded)" in lab
