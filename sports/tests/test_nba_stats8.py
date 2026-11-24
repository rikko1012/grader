"""NBA_STATS8 (round 2 2026-11-21, track nba-stats): the nba8-1 joint model for PRA, P+R, P+A, R+A, steals, blocks,
Blks+Stls and turnovers. NBA_STATS8 defaults OFF (round-2 review): unset = cap-2 exactly, nothing of nba8-1 on any member
file; the forward shadow (NBA_STATS8_SHADOW, default on) records all 8 stats in shadow.db only. NBA_STATS8=1 letters the
walk-forward-passed stats on the tested population only (PrizePicks standard / Underdog balanced, both sides offered).
Targeted tests only (no full suite on the shared machine)."""
import json

import numpy as np
import pytest

from sports import board_view, capabilities, markets, store
from sports.model import datasets, forward, nba8
from sports.tests import helpers as h
from sports.tests.test_model import _hist, _store_world

EIGHT = ("pra", "pr", "pa", "ra", "steals", "blocks", "stocks", "turnovers")
CAP2 = {"pra": "research_only", "pr": "research_only", "pa": "research_only", "ra": "research_only",
        "steals": "research_only", "blocks": "research_only", "stocks": "research_only",
        "turnovers": "research_only"}


def _cap2_reason(stat):
    return (capabilities.HANDLERS["combo"].reason if stat in capabilities.COMBOS
            else capabilities.HANDLERS["single_research"].reason)


# ------------------------------------------------------------------------------------------------- capabilities
@pytest.mark.parametrize("val", [None, "0", "", "maybe", "off"])
def test_flag_off_is_cap2_exactly(monkeypatch, val):
    if val is None:
        monkeypatch.delenv("NBA_STATS8", raising=False)                         # DEFAULT = OFF
    else:
        monkeypatch.setenv("NBA_STATS8", val)
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset(EIGHT))     # even if everything had passed
    assert not capabilities.nba_stats8_on() and capabilities.nba_stats8_markets() == frozenset()
    for st in EIGHT:
        c = capabilities.classify("nba", st)
        assert (c.status, c.reason) == (CAP2[st], _cap2_reason(st))
        ok, why = capabilities.gate(h.offer(stat=st, line=1.5))
        assert not ok and why == [_cap2_reason(st)]
    assert capabilities.gradable_stats("nba") == capabilities.SUPPORTED["nba"]
    assert capabilities.SUPPORTED["nba"] == ("points", "rebounds", "assists", "threes")
    assert capabilities.CAPABILITY_VERSION == "cap-2"


def test_flag_off_model_lists_equal_main(monkeypatch):
    """Rollback / default: every model list a member file reads is exactly the pre-nba8 tuple."""
    from sports import results_public, scout_chance
    from sports.model import learning
    monkeypatch.delenv("NBA_STATS8", raising=False)
    assert board_view.scout_models() == board_view.SCOUT_MODELS == ("sm-1", "mi-1")
    assert scout_chance.models() == scout_chance.MODELS == ("sm-1", "mi-1")
    assert results_public._scout() == results_public.SCOUT == ("sm-1", "mi-1")
    from sports import snapshots
    assert snapshots.DEFAULT_MODELS == ("sm-1", "mi-1", "sharp-2")
    sc = [{"model_version": "nba8-1", "league": "nba", "stat": "steals", "graded": 400, "games": 150}]
    assert learning.forward_gate(scored=sc) == []
    monkeypatch.setenv("NBA_STATS8", "1")
    assert board_view.scout_models() == ("sm-1", "mi-1", "nba8-1")
    assert scout_chance.models() == results_public._scout() == ("sm-1", "mi-1", "nba8-1")


def test_tested_population_only():
    """Walk-forward population: PrizePicks standard, Underdog balanced - both sides offered right now."""
    both = (h.SideQuote("over", True), h.SideQuote("under", True))
    one = (h.SideQuote("over", True), h.SideQuote("under", False))
    assert capabilities.nba8_tested(h.offer(stat="steals", line=0.5))
    assert capabilities.nba8_tested(h.offer(platform="underdog", oid="ud:1", stat="steals", line=0.5,
                                            variant="balanced", sides=both))
    assert not capabilities.nba8_tested(h.offer(platform="underdog", oid="ud:1", stat="steals", line=0.5,
                                                variant="balanced", sides=one))       # one side suspended now
    assert not capabilities.nba8_tested(h.offer(platform="underdog", oid="ud:1", stat="steals", line=0.5,
                                                variant="one_sided", sides=one))
    assert not capabilities.nba8_tested(h.offer(platform="fanduel", oid="fd:1", stat="steals", line=0.5,
                                                variant="main"))
    assert not capabilities.nba8_tested(h.offer(platform="polymarket", oid="pm:1", stat="steals", line=0.5,
                                                variant="main"))
    assert not capabilities.nba8_tested(h.offer(stat="steals", line=0.5, variant="demon"))
    assert not capabilities.nba8_tested(h.offer(stat="steals", line=0.5, promotional=True))
    assert not capabilities.nba8_tested(h.offer(stat="steals", line=0.5, scope="first_half"))


def test_committed_pass_list_is_steals_only():
    """Walk-forward 2026-11-21 (round2/nba-stats): steals passed; pra/pr/pa/ra failed, blocks/stocks/turnovers short."""
    assert capabilities.NBA_STATS8_PASSED == frozenset({"steals"})


def test_only_passed_stats_become_gradable(monkeypatch):
    monkeypatch.setenv("NBA_STATS8", "1")
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra", "turnovers"}))
    assert capabilities.gate(h.offer(stat="pra", line=30.5)) == (True, [])
    assert capabilities.gate(h.offer(stat="turnovers", line=2.5)) == (True, [])
    assert capabilities.classify("nba", "pra").handler.name == "joint"
    assert capabilities.classify("nba", "turnovers").handler.name == "single"
    for st in set(EIGHT) - {"pra", "turnovers"}:
        assert capabilities.classify("nba", st).status == "research_only"
    assert capabilities.classify("nfl", "pra").status == "research_only"         # NBA only
    assert capabilities.gradable_stats("nba")[-2:] == ("pra", "turnovers")
    assert capabilities.SUPPORTED["nba"] == ("points", "rebounds", "assists", "threes")    # never mutated


def test_one_sided_and_alternate_lines_stay_ungraded(monkeypatch):
    """Owner 2026-11-06: an Over-only steals / blocks line stays ungraded even when the stat passed."""
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset(EIGHT))
    one = h.offer(platform="underdog", oid="ud:1", stat="steals", line=1.5, variant="one_sided",
                  sides=(h.SideQuote("over", True), h.SideQuote("under", False)))
    ok, why = capabilities.gate(one)
    assert not ok and capabilities.ONE_SIDED_REASON in why
    assert not capabilities.gate(h.offer(stat="blocks", line=0.5, variant="demon"))[0]
    assert not capabilities.gate(h.offer(stat="pra", line=30.5, scope="first_half"))[0]


def test_gate_state_of_a_passed_stat(monkeypatch):
    monkeypatch.setenv("NBA_STATS8", "1")
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    monkeypatch.setattr(board_view, "_registry", lambda: {"versions": {"sm-1": {"validation": {"points": "history-validated"}}}})
    assert board_view.gate_state("pra") == capabilities.NBA_STATS8_STATE == "history-validated"
    assert board_view.gate_state("points") == "history-validated"
    assert board_view.gate_state("steals") == "unvalidated"
    monkeypatch.delenv("NBA_STATS8")                                             # default off: not registered
    assert board_view.gate_state("pra") == "unvalidated"


def test_nba8_chance_is_never_recalibrated_by_the_sm1_layers():
    """The walk-forward tested the raw nba8-1 chance; pc-1 / cal-mi-1 are sm-1 layers and must not change it."""
    assert board_view.calibrated("nba", "pra", 0.6, 0.4, 0.0, "nba8-1", None) is None


# ---------------------------------------------------------------------------------------------------------- model
def _art():
    a = nba8.load()
    assert a is not None and a["sha"] == nba8.artifact_sha(a) == "69e5e27b376e5485"
    return a


def _logs(n=30, seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        m = float(rng.normal(32, 4))
        out.append({"start": f"2026-10-{i % 28 + 1:02d}T00:00:00Z", "min": m, "pts": float(rng.poisson(0.6 * m)),
                    "reb": float(rng.poisson(0.2 * m)), "ast": float(rng.poisson(0.15 * m)),
                    "stl": float(rng.poisson(0.04 * m)), "blk": float(rng.poisson(0.03 * m)),
                    "tov": float(rng.poisson(0.06 * m))})
    return out


@pytest.mark.parametrize("market", EIGHT)
def test_pmf_is_a_distribution_and_chances_add_up(market):
    a = _art()
    f = nba8.forecast(a, market, _logs(), "G", 2.5 if market in ("steals", "blocks", "turnovers", "stocks") else 25.5)
    assert f is not None and abs(f["p_over"] + f["p_under"] + f["p_push"] - 1) < 1e-9
    assert f["p_push"] == 0.0                                                   # half-point line
    assert f["mean"] > 0 and f["reason_codes"] == []


def test_combo_is_joint_not_independent():
    """PRA variance carries the fitted within-player correlation (positive for G pts|ast) - wider than independent."""
    a = _art()
    feat = nba8.features(_logs(), a)
    mus = nba8.component_means(a, feat, "G")
    ks = np.arange(nba8.SUPPORT["pra"] + 1)
    joint = nba8.pmf(a, "pra", {c: [mus[c]] for c in ("pts", "reb", "ast")}, "G")[0]
    ind = nba8.pmf(a, "pra", {c: [mus[c]] for c in ("pts", "reb", "ast")}, "G", independent=True)[0]
    var = lambda p: float(p @ ks ** 2 - (p @ ks) ** 2)                         # noqa: E731
    assert abs(joint @ ks - ind @ ks) < 1e-6 and var(joint) > var(ind)


def test_settlement_adds_the_box_score_parts():
    assert markets.value("basketball", "pra", {"pts": 20, "reb": 7, "ast": 5}) == 32
    assert markets.value("basketball", "stocks", {"blk": 1, "stl": 2}) == 3
    assert markets.market("basketball", "turnovers")["parts"] == ("tov",)


# ------------------------------------------------------------------------------------------------ forward / replay
def _world(tmp_path, monkeypatch):
    hcon = _hist(tmp_path)
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "history.db")
    art = tmp_path / "artifacts"
    art.mkdir()
    monkeypatch.setattr(forward, "ART_DIR", art)
    cfg = {"markets": {mk: {"market": mk, "half_lives": [5, 20], "features": ["nba.home"], "family": fam,
                            "cal": [1.0, 1.05]} for mk, fam in (("points", "negbin"), ("rebounds", "mixture"),
                                                                ("assists", "poisson"), ("threes", "ratio"))}}
    (art / "model_config_nba.json").write_text(json.dumps(cfg))
    forward.fit("nba", hcon, as_of="2026-01-31T20:00:00Z")
    return hcon


def _pra_offer(scon):
    o = h.offer(oid="pp:pra1", stat="pra", line=25.5, player_id="nba:player:espn:0", source_stat="Pts+Rebs+Asts",
                observed_at="2026-01-31T20:00:00Z", listed_start_utc="2026-02-01T00:00:00Z")
    return store.put(scon, o)["record_id"]


def test_forward_writes_nba8_for_a_passed_stat_and_replays_identically(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_STATS8", "1")
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    hcon = _world(tmp_path, monkeypatch)
    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        rid = _pra_offer(scon)
        monkeypatch.setattr(store, "iter_offers", lambda *a, **k: pytest.fail("full-history offer scan"))
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["written"].get("sm-1") == 1 and res["written"].get("nba8-1") == 1, res
        assert "nba8_error" not in res
        fc = [f for _, f in store.forecasts(scon, model_version="nba8-1")][0]
        assert fc.offer_snapshot_ids == (rid,) and fc.stat == "pra" and fc.research_only
        assert fc.stake_units == 0.0 and fc.artifact_version == "nba8-1:69e5e27b376e5485"
        assert abs(fc.p_over + fc.p_under + fc.p_push - 1) < 1e-9
        assert "gp:history-validated" in fc.reason_codes and fc.grade is None   # same policy state as sm-1 NBA
        orig, again = forward.replay(fc.forecast_id, scon)
        assert again == orig


def _shadow_rows():
    from sports.model import shadow
    con = shadow.db()
    try:
        cur = con.execute("SELECT * FROM shadow_forecasts WHERE model_version = 'nba8-1'")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()


@pytest.mark.parametrize("val", [None, "0"])
def test_flag_off_writes_no_canonical_forecast_only_the_shadow(tmp_path, monkeypatch, val):
    if val is None:
        monkeypatch.delenv("NBA_STATS8", raising=False)
    else:
        monkeypatch.setenv("NBA_STATS8", val)
    monkeypatch.delenv("NBA_STATS8_SHADOW", raising=False)                       # shadow default on
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    hcon = _world(tmp_path, monkeypatch)
    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        rid = _pra_offer(scon)
        monkeypatch.setattr(store, "iter_offers", lambda *a, **k: pytest.fail("full-history offer scan"))
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["written"].get("sm-1") == 1 and "nba8-1" not in res["written"]
        assert not [f for _, f in store.forecasts(scon, model_version="nba8-1")]
        assert not any(k.endswith("|pra") for k in res["why_none"])              # the board reads why_none
        sh = res["shadow_nba8"]
        assert sh["written"] == 1 and sh["markets"] == list(EIGHT) and sh["display"].startswith("never"), sh
        rows = _shadow_rows()
        assert len(rows) == 1 and rows[0]["offer_record_id"] == rid and rows[0]["stat"] == "pra"
        assert rows[0]["platform"] == "prizepicks" and rows[0]["sides"] == "over,under"
        assert rows[0]["chosen_side"] in ("over", "under") and abs(rows[0]["p_over"] + rows[0]["p_under"]
                                                                   + rows[0]["p_push"] - 1) < 1e-9
        # a second run with the same inputs keeps ONE row with the same id (shadow_id has no forecast_at). In this
        # Jan-2026 world the sm-2 recorder's 180-day retention may drop and re-insert it; in production it is ignored.
        sid = rows[0]["shadow_id"]
        forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:05:00Z", log=lambda *x: None)
        again = _shadow_rows()
        assert [r["shadow_id"] for r in again] == [sid]


def test_shadow_kill_switch_and_population(tmp_path, monkeypatch):
    monkeypatch.delenv("NBA_STATS8", raising=False)
    hcon = _world(tmp_path, monkeypatch)
    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        kw = dict(stat="pra", line=25.5, player_id="nba:player:espn:0", source_stat="Pts+Rebs+Asts",
                  observed_at="2026-01-31T20:00:00Z", listed_start_utc="2026-02-01T00:00:00Z")
        store.put(scon, h.offer(oid="pp:demon", variant="demon", **kw))
        store.put(scon, h.offer(platform="underdog", oid="ud:one", variant="balanced",
                                sides=(h.SideQuote("over", True), h.SideQuote("under", False)), **kw))
        monkeypatch.setenv("NBA_STATS8_SHADOW", "0")
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert "shadow_nba8" not in res and not _shadow_rows()
        monkeypatch.delenv("NBA_STATS8_SHADOW")
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["shadow_nba8"]["written"] == 0 and not _shadow_rows()          # neither line is in the population


def test_nba8_logs_are_point_in_time(tmp_path):
    hcon = _hist(tmp_path)
    lg, pos = forward.nba8_logs(hcon, "nba:player:espn:0", "2025-11-11T00:00:00Z")
    assert len(lg) == 5 and all(g["start"] < "2025-11-11T00:00:00Z" for g in lg) and pos == "F"
    assert set(lg[0]) == {"start", "min", "pts", "reb", "ast", "stl", "blk", "tov"}


# ------------------------------------------------------------------------------------------- board build / rollback
def _steals_board(con):
    store.put(con, h.event())
    rid = store.put(con, h.offer(oid="pp:stl", stat="steals", line=1.5, source_stat="Steals"))["record_id"]
    store.put(con, h.forecast(line=1.5, stat="steals", offer_snapshot_ids=(rid,), model_version="nba8-1",
                              artifact_version="nba8-1:69e5e27b376e5485", projection_mean=1.1, p_over=0.32,
                              p_under=0.68, p_push=0.0, p_over_given_no_push=0.32, p_under_given_no_push=0.68,
                              chosen_side="under"))
    return rid


def _board_quote(base, rid):
    for part in ("board.json", "board_more.json"):
        f = base / "nba" / part
        if f.exists():
            for m in json.loads(f.read_text()).get("markets") or []:
                for q in m.get("q") or []:
                    if q["r"] == rid:
                        return m, q
    return None, None


def test_board_letters_a_passed_stat_and_the_flag_rolls_it_back(tmp_path, monkeypatch):
    from sports import snapshots
    monkeypatch.setenv("NBA_STATS8", "1")
    with store.opened() as con:
        rid = _steals_board(con)
        snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path / "on", history=False, results=False)
        monkeypatch.setenv("NBA_STATS8", "0")
        snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path / "off", history=False, results=False)
    m_on, q_on = _board_quote(tmp_path / "on", rid)
    assert q_on is not None and q_on.get("f") and q_on["g"] == 1, q_on
    assert (q_on.get("x") or {}).get("ln") == "under" and (q_on.get("x") or {}).get("gr") == "A", q_on
    m_off, q_off = _board_quote(tmp_path / "off", rid)
    assert q_off is not None and not q_off.get("f") and not q_off.get("g"), q_off
    assert not (q_off.get("x") or {}).get("ln") and not (q_off.get("x") or {}).get("gr"), q_off


def _read_all(base):
    return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def test_flag_unset_board_is_byte_identical_with_or_without_a_stored_nba8_forecast(tmp_path, monkeypatch):
    """Default (NBA_STATS8 unset): a stored nba8-1 forecast changes no member file at all."""
    from sports import snapshots
    monkeypatch.delenv("NBA_STATS8", raising=False)
    now = "2026-10-23T21:00:00Z"
    for name, with_fc in (("without", False), ("with", True)):
        with store.opened(tmp_path / f"{name}.db") as con:
            if with_fc:
                _steals_board(con)
            else:
                store.put(con, h.event())
                store.put(con, h.offer(oid="pp:stl", stat="steals", line=1.5, source_stat="Steals"))
            store.put(con, h.offer(oid="pp:pts", line=20.5))
            snapshots.build(con, "nba", now=now, base=tmp_path / name, history=False, results=False)
    a, b = _read_all(tmp_path / "without"), _read_all(tmp_path / "with")
    assert a.keys() == b.keys()
    diff = [k for k in a if a[k] != b[k]]
    for k in diff:                                   # the manifest carries the build's own timing / memory only
        assert k.endswith("manifest.json"), k
        ja, jb = json.loads(a[k]), json.loads(b[k])
        for j in (ja, jb):
            for t in ("build_s", "build_rss_mb", "build_stage_s"):
                j.pop(t, None)
        assert ja == jb, k
    assert not any(b"nba8" in v for v in b.values())


def _row_quotes(base, stat):
    out = []
    for part in ("board.json", "board_more.json"):
        f = base / "nba" / part
        if f.exists():
            for m in json.loads(f.read_text()).get("markets") or []:
                if m.get("st") == stat:
                    out.extend(m.get("q") or [])
    return out


def test_flag_on_letters_only_the_tested_population(tmp_path, monkeypatch):
    """Round-2 review: FanDuel / Polymarket at the same line and a balanced Underdog line with one side suspended get
    NO nba8-1 letter (never tested); the PrizePicks standard and two-sided Underdog quotes with their own forecast do."""
    from sports import snapshots
    monkeypatch.setenv("NBA_STATS8", "1")
    both = (h.SideQuote("over", True, "decimal", price=1.9), h.SideQuote("under", True, "decimal", price=1.9))
    with store.opened(tmp_path / "b.db") as con:
        pp = _steals_board(con)
        ud2 = store.put(con, h.offer(platform="underdog", oid="ud:2", stat="steals", line=1.5, source_stat="Steals",
                                     variant="balanced"))["record_id"]
        ud1 = store.put(con, h.offer(platform="underdog", oid="ud:1s", stat="steals", line=1.5, source_stat="Steals",
                                     variant="balanced", player_id="nba:player:espn:3134907",
                                     sides=(h.SideQuote("over", True), h.SideQuote("under", False))))["record_id"]
        fd = store.put(con, h.offer(platform="fanduel", oid="fd:stl", stat="steals", line=1.5, source_stat="Steals",
                                    variant="main", sides=both))["record_id"]
        pm = store.put(con, h.offer(platform="polymarket", oid="pm:stl", stat="steals", line=1.5, source_stat="Steals",
                                    variant="main", sides=both))["record_id"]
        alt = store.put(con, h.offer(platform="underdog", oid="ud:alt", stat="steals", line=2.5, source_stat="Steals",
                                     variant="balanced"))["record_id"]       # tested line, no forecast of its own
        # each Underdog line has its OWN nba8-1 forecast (the suspended-Under line: one made for its Over side only)
        for rid, sides, side in ((ud2, ("over", "under"), "under"), (ud1, ("over",), "over")):
            store.put(con, h.forecast(line=1.5, stat="steals", offer_snapshot_ids=(rid,), model_version="nba8-1",
                                      artifact_version="nba8-1:69e5e27b376e5485", projection_mean=1.9, p_over=0.68,
                                      p_under=0.32, p_push=0.0, p_over_given_no_push=0.68,
                                      p_under_given_no_push=0.32, chosen_side=side, sides_available=sides))
        snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path / "on", history=False, results=False)
    qs = {q["r"]: q for q in _row_quotes(tmp_path / "on", "steals")}
    assert qs[pp].get("f") and (qs[pp].get("x") or {}).get("gr"), qs[pp]
    assert qs[ud2].get("f") and (qs[ud2].get("x") or {}).get("gr"), qs[ud2]
    assert alt in qs and not qs[alt].get("line_read") and not qs[alt].get("f"), qs.get(alt)   # never read at a new line
    for rid in (ud1, fd, pm):                                    # all three sit in the steals row (not vacuous)
        assert rid in qs, (rid, sorted(qs))
        assert not qs[rid].get("f") and not (qs[rid].get("x") or {}).get("gr"), qs[rid]
        assert not qs[rid].get("line_read"), qs[rid]


def test_shadow_rows_are_scored_by_shadow_settle(tmp_path):
    """The worker settles every PrizePicks / Underdog offer whose market has a settlement spec (steals included, even
    research_only); shadow.settle then scores the nba8-1 shadow row under its own model_version."""
    from sports import settlement
    from sports.model import shadow
    with store.opened(tmp_path / "s.db") as con:
        ev = h.event(status="final")
        store.put(con, ev)
        o = h.offer(oid="pp:stl", stat="steals", line=1.5, source_stat="Steals")
        rid = store.put(con, o)["record_id"]
        obs = {"stl": h.stat(part="stl", value=3.0)}
        sts = settlement.settle_offer(o, obs, ev, "2026-10-24T03:30:00Z")
        assert sts and all(x.status == "settled" or x.outcome for x in sts), sts
        settlement.record(con, sts)
        sh = shadow.db()
        sh.execute("INSERT INTO shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("sid1", "nba8-1", "69e5e27b376e5485", "nba", "steals", o.event_id, o.player_id, rid, "prizepicks",
                    1.5, "2026-10-23T20:00:00Z", ev.start_utc, 1.2, 0.45, 0.55, 0.0, "over,under", "under", 0.55,
                    "C", "{}", 0, "[]", None))
        sh.commit()
        sh.close()
        out = shadow.settle(con)
    assert out["shadow_scored"] == 1, out
    rows = [r for r in shadow.scorecard_rows() if r["model_version"] == "nba8-1"]
    assert len(rows) == 1 and rows[0]["stat"] == "steals" and rows[0]["status"] == "loss", rows    # 3 > 1.5: Under lost


def test_nba8_forecast_is_never_shared_or_read_at_another_line():
    """Unit check of the two N10 sharing rules: an sm-1 forecast is shared / line-read as before; nba8-1 never is."""
    from sports import snapshots
    pmf = (0, [0.3, 0.4, 0.2, 0.1])
    for mv, shared in (("sm-1", True), ("nba8-1", False)):
        fcs = {"f": {"forecast_id": "f", "model_version": mv, "kind": "scout", "forecast_at": "2026-10-24T10:00:00Z",
                     "line": 1.5, "detail": {"pmf": pmf}}}
        row = {"quotes": [{"platform": "prizepicks", "line": 1.5, "forecast_id": "f", "gradable": True},
                          {"platform": "fanduel", "line": 1.5, "forecast_id": None, "gradable": True},
                          {"platform": "underdog", "line": 2.5, "forecast_id": None, "gradable": True}]}
        assert snapshots.same_line_forecasts(row, fcs) == (1 if shared else 0), mv
        snapshots.scout_line_reads(row, fcs)
        assert bool(row["quotes"][2].get("line_read")) is shared, (mv, row["quotes"][2])
