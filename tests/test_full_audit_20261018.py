"""Behavioral regressions from the full audit. All players, prices and outcomes below are TEST fixtures."""
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import comparison
import perf
import perf_live
import picklog
import scope
import server
import slip_builder as sb
from slip_probability import summary
from tests.test_audit13_site_ui import needs_node, run_page, _outage_card, KEPT
from tests.test_scope import _fake_hltv, _row, _pick
from tests.test_slip_builder import board, _boards, NOW, M
from tests.test_backfill_pin import db, D


def test_unknown_history_environment_is_not_online_history(db):
    db.player = [(D, 20, 24, 10, None, 10, 100)]
    _, rows = perf_live.player_maps("x", "y", "2025-08-01")
    assert len(rows) == 1 and rows[0][4] is None
    p = perf.project(rows, [], False, D.toordinal() + 10)
    assert p["env_rounds_weighted"] == 0
    assert p["history_rounds_weighted"] > 0 and p["rate"] == p["rate_all_envs"]
    known = perf.project([(*rows[0][:4], False, True)], [], False, D.toordinal() + 10)
    assert known["env_rounds_weighted"] > 0


def test_opponent_query_rejects_incomplete_or_duplicated_scoreboards():
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE matches(matchid INT, mapid INT, teamid INT, sideid INT, score INT);
      CREATE TABLE match_overview(matchid INT, date TEXT);
      CREATE TABLE match_maps(matchid INT, mapid INT);
      CREATE TABLE player_stats(matchid INT, mapid INT, teamid INT, sideid INT, playerid INT, k INT);
    """)
    for mid, count in ((1, 5), (2, 4), (3, 6), (4, 5), (5, 5)):
        con.execute("INSERT INTO match_overview VALUES (?, '2026-10-01')", (mid,))
        con.execute("INSERT INTO match_maps VALUES (?, 1)", (mid,))
        con.executemany("INSERT INTO matches VALUES (?, 1, ?, 0, ?)", [(mid, 1, 13), (mid, 2, 9)])
        for p in range(count):
            con.execute("INSERT INTO player_stats VALUES (?, 1, 2, 0, ?, ?)",
                        (mid, 0 if mid == 4 else p, None if mid == 5 and p == 0 else 10))
    sql = perf_live._OPP_SQL.replace("%(tid)s", ":tid").replace("%(since)s", ":since")
    assert con.execute(sql, {"tid": 1, "since": "2026-01-01"}).fetchall() == [("2026-10-01", 50, 22, 1)]
    con.close()


@pytest.mark.parametrize("fmt,expected", [("bo3", "void"), ("best of 3", "void"), ("bo5", "wait"), (None, "wait")])
def test_maps123_two_maps_do_not_prove_a_bo3_sweep(monkeypatch, fmt, expected):
    _fake_hltv(monkeypatch, [_row(1, 11, 6, True), _row(2, 9, 6, True)], best_of=fmt)
    assert picklog.scope_result(_pick("123", 30.5, "More"))["status"] == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 1.25, True])
def test_invalid_actual_total_never_becomes_a_win_or_loss(bad):
    assert scope.scope_total([_row(1, bad, 6, True)], "1", "kills")[0] is None
    if not math.isfinite(bad) or isinstance(bad, bool):
        with pytest.raises(ValueError):
            scope.outcome(bad, 10.5, "More")
    with pytest.raises(ValueError):
        scope.outcome(10, 10.5, "TYPO")


def test_slip_all_win_is_not_a_no_push_probability_and_zero_stays_zero():
    legs = [{"side": "More", "p": .6, "line": 10, "model_outcomes": {"over": .54, "under": .36, "push": .1}}] * 2
    out = summary(legs)
    assert out["all_hit"] == .2916 and out["all_hit_basis"] == "model_unconditional"
    old = summary([{k: v for k, v in l.items() if k != "model_outcomes"} for l in legs])
    assert old["all_hit"] == .36 and old["all_hit_basis"] == "conditional_no_push"
    assert summary([{"p": 0, "line": 10.5}])["all_hit"] == 0


def test_builder_revalidates_raw_outcomes_even_when_displayed_chance_does_not_change(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "slips.db")
    rows = board(2, per_match=1, model_outcomes={"over": .54, "under": .36, "push": .1}, line=30, graded_line=30)
    slip = sb.build(rows, {"sizes": [2], "count": 1}, now=NOW, model=M)["slips"][2][0]
    assert slip["all_hit"] == .2916 and slip["builder_version"] == "studio-4"
    assert sb.verify_before_copy(slip, _boards(rows, NOW.timestamp()), now=NOW, model=M)["ok"]
    rows[0] = dict(rows[0], model_outcomes={"over": .51, "under": .34, "push": .15})
    assert not sb.verify_before_copy(slip, _boards(rows, NOW.timestamp()), now=NOW, model=M)["ok"]


def test_board_carries_its_own_raw_distribution(monkeypatch):
    now = datetime.now(timezone.utc)
    r = board(1, per_match=1)[0]
    r.update(start=(now + timedelta(hours=3)).isoformat(), model_outcomes={"over": .54, "under": .36, "push": .1})
    monkeypatch.setattr(server, "BOARD", {r["id"]: r})
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": [r], "at": now.timestamp(), "error": None})
    monkeypatch.setattr(server, "_row_match_id", lambda r: "hltv:test")
    monkeypatch.setattr(server, "awp_role", lambda *a: (False, "test"))
    monkeypatch.setattr(server.roles, "igl_role", lambda *a: (False, "test"))
    assert server.board_payload()["rows"][0]["model_outcomes"] == r["model_outcomes"]


def test_raw_probabilities_are_coherent_and_monotone_across_all_scopes():
    for maps in ("1", "12", "123"):
        for lam in (8.0, 25.0, 50.0):
            for stat in ("kills", "hs"):
                previous = 1.0
                for line in (0, .5, 5, 5.5, 15, 15.5, 30, 30.5, 60, 60.5):
                    ps = perf.probs(lam, 1.45 * math.sqrt(lam), line) if stat == "kills" else perf.hs_probs(lam, maps, .53, line)
                    assert sum(ps) == pytest.approx(1, abs=1e-10)
                    assert all(math.isfinite(p) and -1e-12 <= p <= 1 for p in ps)
                    assert ps[0] <= previous + 1e-10
                    assert line == int(line) or ps[2] == pytest.approx(0, abs=1e-12)
                    previous = ps[0]


def test_loaded_slip_rejects_another_match_and_accepts_raw_underdog_sides(monkeypatch):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat()
    row = {"player": "TEST", "team": "Alpha", "opponent": "Bravo", "start": start,
           "prop": "kills", "scope": "1", "line": 10.5, "higher": 1, "lower": 1, "alt": False}
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": [row], "at": now.timestamp()})
    leg = {"nick": "TEST", "team": "Alpha", "opponent": "Bravo", "start": start, "book": "underdog",
           "prop": "kills", "scope": "1", "line": 10.5, "side": "Less"}
    assert server.slip_leg_problem(leg) is None
    assert server.slip_leg_problem(dict(leg, opponent="Charlie")) is not None
    assert server.slip_leg_problem(dict(leg, start=(now + timedelta(days=1)).isoformat())) is not None


@pytest.mark.parametrize("book", ["underdog", "prizepicks"])
def test_copy_verification_checks_side_match_and_freshness(book):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat()
    r = {"id": "test", "player": "TEST", "team": "Alpha", "opponent": "Bravo", "start": start, "prop": "kills", "scope": "1",
         "line": 10.5, "alt": False, "higher": 1, "lower": 0, "over_available": True, "under_available": False}
    feed = {"rows": [r], "at": now.timestamp(), "state": "ok"}
    def check(**kw):
        args = dict(board=feed, player="TEST", prop="kills", scope="1", line=10.5, team="Alpha", start=start,
                    opponent="Bravo", book=book, now=now, side="More")
        args.update(kw)
        return comparison.line_check(**args)
    assert check()["status"] == "verified"
    assert check(side="Less")["status"] == "unavailable"
    assert check(opponent="Charlie")["status"] == "unavailable"
    assert check(team="Alpha Academy")["status"] == "unavailable"
    assert check(start=(now + timedelta(days=1)).isoformat())["status"] == "unavailable"
    assert check(board=dict(feed, at=now.timestamp() - 3600))["status"] == "stale"
    assert check(start=None)["status"] == "failed"
    assert comparison.offer_side({"higher": 1}, "More", "underdog") is True
    assert comparison.offer_side({"higher": 1}, "More", "prizepicks") is False


@needs_node
def test_manual_slip_matches_backend_probability_semantics(tmp_path):
    legs = [{"side": "More", "p": .6, "line": 10, "model_outcomes": {"over": .54, "under": .36, "push": .1}}] * 2
    result = run_page(tmp_path, """(() => {
      const legs = INPUT.map(l => ({s:l.side, pc:l.p, l:l.line, model_outcomes:l.model_outcomes}));
      return {fresh:manualProbability(legs), old:manualProbability(legs.map(x => ({...x, model_outcomes:null}))),
        zero:manualProbability([{pc:0,l:10.5},{pc:.6,l:12.5}]), bad:manualProbability([{pc:null,l:10.5}]),
        wording:studioChance({n:2, legs:[{line:10}], all_hit:.36})};
    })()""", legs)
    assert result["fresh"]["all_hit"] == pytest.approx(summary(legs)["all_hit"])
    assert result["old"]["all_hit_basis"] == "conditional_no_push" and "if none push" in result["wording"]
    assert result["zero"]["all_hit"] == 0 and result["bad"] is None


@needs_node
def test_manual_entry_identity_and_side_check_reach_the_server(tmp_path):
    out = run_page(tmp_path, """(async () => {
      const a = {p:'TEST',t:'kills',sc:'1',l:10.5,s:'Less',bk:'underdog',tm:'A',vs:'B',st:'2099-01-01T12:00:00Z',m:'match1'};
      const b = {...a,st:'2099-01-02T12:00:00Z',m:'match2'};
      let url; fetch = async u => {url=u; return {ok:true,json:async()=>({status:'verified'})};};
      await lineCheck(a);
      return {url, payoutSplit:manualKey([a])!==manualKey([b]), propSplit:legKey(a)!==legKey(b),
        badgeSplit:slipKeyLeg(a)!==slipKeyLeg(b)};
    })()""")
    assert "side=Less" in out["url"] and "opponent=B" in out["url"]
    assert out["payoutSplit"] and out["propSplit"] and out["badgeSplit"]


@needs_node
def test_compare_never_promotes_a_side_that_is_not_offered(tmp_path):
    out = run_page(tmp_path, """(() => {
      const base={player:'TEST',nick:'TEST',team:'A',opponent:'B',match_slug:'m',start:'2099-01-01T12:00:00Z',
        prop:'kills',scope:'1',side:'Less',p:.61,grade:'B',units:.5,proj:12,state:'GRADE',higher:1,lower:1};
      BK.underdog.rows=[{...base,book:'underdog',line:14.5}];
      BK.prizepicks.rows=[{...base,book:'prizepicks',line:15.5,under_available:false,over_available:true,unavailable:'Unavailable—side not offered'}];
      assignMatchKeys([...BK.underdog.rows,...BK.prizepicks.rows]); bprop='kills';bscope='1';bside='both';
      drawCompare(); return $('#boardout').innerHTML;
    })()""")
    assert "Unavailable—side not offered" in out and "Easier Under:" not in out and "Easier Over:" not in out


@needs_node
def test_line_move_dialog_cannot_choose_a_new_unavailable_side(tmp_path):
    out = run_page(tmp_path, """(async () => {
      let choices=[];ask=async(t,b,c)=>{choices=c.map(x=>x.k);return 'cancel';};
      fetch=async u=>({ok:true,json:async()=>String(u).includes('/api/line_check') ? {status:'changed',new_line:12.5} :
        {verdict:{side:'Less',p:.6,grade:'B',units:0,side_unavailable:'Unavailable—side not offered'}}});
      await addToSlip({book:'prizepicks',from:'board',grade:{line:13.5,prop:'kills',scope:'1',line_type:'standard'},
        verdict:{side:'Less',p:.6,grade:'B',units:.5},player_block:{player:{nick:'TEST'},current_team:'Alpha'},
        next_match:{opponent:'Bravo',start:'2099-01-01T12:00:00Z',slug:'test-match'}});
      return choices;
    })()""")
    assert "new" not in out and "cancel" in out


@needs_node
def test_map1_card_uses_map1_h2h_and_does_not_show_map2(tmp_path):
    card = _outage_card(dict(KEPT, kept_from_board=False, stale=False), {})
    card["grade"].update(scope="1", line=15.5)
    card["player_block"]["map_prediction"] = {"map1": {"de_mirage": .5}, "map2": {"de_inferno": .5}}
    card["next_match"]["bo"] = 1
    card["h2h"] = {"series": [{"date": "2026-09-01", "opponent": "MOUZ", "values": [10, 22], "total": 32,
                                "maps": ["de_mirage", "de_inferno"], "result": "L", "same_roster": True}]}
    out = run_page(tmp_path, """(() => {try {render(INPUT);} catch(e) {if(!/nextSibling/.test(e.message))throw e;} return $('#out').innerHTML;})()""", card)
    assert "Likely Map 1" in out and "Likely Map 2" not in out
    h2h = out.split("Head-to-head vs")[1].split("</section>")[0]
    assert "over 15.5 in 0/1" in h2h and "<b>10</b>" in h2h and "<b>32</b>" not in h2h
