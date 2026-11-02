"""Real cached-feed adapter and member-page flow; invented fixtures only."""
import copy
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import urlencode

import pytest

import line_advantage_live as live
import server
from tests.test_audit_h_web import run, needs_node
from tests.test_api_moneyline_slips import env, call  # noqa: F401


def world(now=None):
    now = now or datetime.now(timezone.utc)
    start = (now + timedelta(hours=2)).isoformat()
    row = dict(id="ud-1", book="underdog", player="Fixture", nick="Fixture", player_id=51,
               match_id="hltv:901", team="Alpha", opponent="Beta", prop="kills", scope="12",
               start=start, match_start=start, line=28.5, graded_line=28.5,
               side="More", higher=1, lower=1, over_available=True, under_available=True,
               alt=False, odds_type="standard")
    other = dict(row, id="pp:123", book="prizepicks", line=27.5, graded_line=27.5)
    rows = [row, other]
    feeds = {r["book"]: dict(at=now.timestamp(), ingested_at=now.timestamp(), state="ok",
                             error=None, rows=[copy.deepcopy(r)]) for r in rows}
    leg = dict(row, row_id=row["id"])
    return now, [leg], rows, feeds


def test_adapter_reads_both_current_quotes_without_mutating_selection():
    now, legs, rows, feeds = world()
    before = copy.deepcopy((legs, rows, feeds))
    result = live.recommendations(legs, rows, feeds, now)
    assert len(result) == 1 and result[0]["line"] == 27.5
    assert result[0]["book"] == "prizepicks" and result[0]["review"]["side"] == "More"
    assert len(result[0]["quote_signature"]) == 20
    assert (legs, rows, feeds) == before


@pytest.mark.parametrize("case", ["moved", "withdrawn", "stale", "old_provider_copy", "future", "identity",
                                "period", "side", "promo", "shadow", "wrong_id", "started", "duplicate", "new_event"])
def test_adapter_refuses_incompatible_or_unavailable_quotes(case):
    now, legs, rows, feeds = world()
    alt = feeds["prizepicks"]["rows"][0]
    if case == "moved": alt["line"] = 29.5
    elif case == "withdrawn": feeds["prizepicks"]["rows"] = []
    elif case == "stale": feeds["prizepicks"]["ingested_at"] -= 601
    elif case == "old_provider_copy": feeds["prizepicks"]["at"] -= 601
    elif case == "future": feeds["prizepicks"]["ingested_at"] += 1
    elif case == "identity": rows[1]["player_id"] = 999
    elif case == "period": rows[1]["scope"] = alt["scope"] = "1"
    elif case == "side": alt["over_available"] = False
    elif case == "promo": alt["odds_type"] = "promo"
    elif case == "shadow": rows[1]["side"] = None
    elif case == "wrong_id": legs[0]["player_id"] = 999
    elif case == "started": alt["start"] = now.isoformat()
    elif case == "duplicate": feeds["prizepicks"]["rows"].append(dict(alt, line=26.5))
    elif case == "new_event": alt["start"] = (now + timedelta(days=1)).isoformat()
    assert live.recommendations(legs, rows, feeds, now) == []


def test_under_prefers_higher_threshold_and_missing_evidence_is_not_best():
    now, legs, rows, feeds = world()
    legs[0]["side"] = "Less"
    assert not live.recommendations(legs, rows, feeds, now)
    rows[1]["line"] = rows[1]["graded_line"] = feeds["prizepicks"]["rows"][0]["line"] = 30
    assert live.recommendations(legs, rows, feeds, now)[0]["side"] == "under"
    assert not live.recommendations(legs, rows, {}, now)


def test_http_line_recheck_returns_advice_only_after_existing_verification(env, monkeypatch):
    _, legs, rows, feeds = world()
    monkeypatch.setattr(server, "_bs_rows", lambda: rows)
    monkeypatch.setattr(server.underdog, "board", lambda **k: feeds["underdog"])
    monkeypatch.setattr(server.prizepicks, "board", lambda **k: feeds["prizepicks"])
    monkeypatch.setattr(server, "_SLIP_IDX", {})
    q = urlencode(dict(player="Fixture", prop="kills", maps="12", line=28.5, team="Alpha",
                       opponent="Beta", start=rows[0]["start"], side="More", book="underdog", rid="ud-1"))
    st, d = call(env, "GET", "/api/line_check?" + q, member="alice")
    assert st == 200 and d["status"] == "verified"
    assert d["line_advantages"][0]["line"] == 27.5
    assert call(env, "GET", "/api/line_check?" + q)[0] == 401
    feeds["underdog"]["rows"][0]["line"] = 29.5
    _, d = call(env, "GET", "/api/line_check?" + q, member="alice")
    assert d["status"] != "verified" and not d.get("line_advantages")


ADVICE = {"book": "prizepicks", "nick": "Fixture", "side": "over", "line": 27.5,
          "original_line": 28.5, "row_id": "pp:123", "expires_at": 9999999999,
          "review": {"player": "Fixture", "prop": "kills", "scope": "12", "line": 27.5,
                     "book": "prizepicks", "side": "More", "team": "Alpha", "opponent": "Beta"}}


@needs_node
def test_ui_renders_easier_line_and_reviews_without_replacing_leg(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      store.set('cs2s_slip', [{p:'Fixture', l:28.5, pc:.6, g:'B'}]);
      const before = store.get('cs2s_slip'); const calls=[];
      gradeNow = (...a) => calls.push(a);
      const html = lineAdvantageHtml([INPUT]); reviewLineAdvantage(INPUT.review);
      return {html, calls, unchanged: JSON.stringify(before)===JSON.stringify(store.get('cs2s_slip'))};
    })()""", ADVICE)
    assert "27.5" in out["html"] and "Compare entry terms" in out["html"]
    assert out["unchanged"] and out["calls"][0][1] == "27.5"


@needs_node
def test_ui_drops_expired_and_unverified_advice_and_escapes_names(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      return {expired: lineAdvantageHtml([{...INPUT, expires_at:1}]),
        failed: checkNow({status:'failed', line_advantages:[INPUT]}).line_advantages,
        html: lineAdvantageHtml([{...INPUT, review:{...INPUT.review, player:'" onclick="bad()'}}])};
    })()""", ADVICE)
    assert out["expired"] == "" and out["failed"] == []
    assert '" onclick="bad()' not in out["html"]


@needs_node
@pytest.mark.parametrize("choice, copies, checks", [("review", 0, 1), ("keep", 1, 2)])
def test_studio_review_never_copies_and_keep_rechecks_after_dialog(tmp_path, choice, copies, checks):
    out = run(tmp_path, "index.html", """(async () => {
      let checks=0, copies=0; const reviews=[];
      ask = async () => INPUT.choice; gradeNow=(...a)=>reviews.push(a);
      copy=async()=>{copies++}; studioRender=()=>{};
      studioCall=async (method,url) => { if (!url.endsWith('/verify')) return {};
        checks++; return {status:200,d:{ok:true,book:'underdog',copy_text:'original',line_advantages:[INPUT.advice]}}; };
      await studioVerify('fixture', {textContent:'Copy', disabled:false});
      return {checks,copies,reviews};
    })()""", {"advice": ADVICE, "choice": choice})
    assert out["copies"] == copies and out["checks"] == checks


@needs_node
def test_studio_refuses_copy_if_original_moves_while_review_is_open(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      let checks=0,copies=0,rebuild=0;
      ask=async()=> 'keep'; copy=async()=>{copies++}; studioRender=()=>{}; studioBuild=async()=>{rebuild++};
      studioCall=async()=>({status:200,d:++checks===1
        ? {ok:true,book:'underdog',copy_text:'old',line_advantages:[INPUT]}
        : {ok:false,action:'rebuild',changes:['Line moved']}});
      await studioVerify('fixture',{textContent:'Copy',disabled:false});
      return {checks,copies,rebuild};
    })()""", ADVICE)
    assert out == {"checks": 2, "copies": 0, "rebuild": 1}


def test_advisory_expires_with_provider_observation_not_newer_ingestion():
    now, legs, rows, feeds = world()
    feeds["prizepicks"]["at"] -= 599
    result = live.recommendations(legs, rows, feeds, now)
    assert len(result) == 1
    assert result[0]["expires_at"] == pytest.approx(now.timestamp() + 1)


@pytest.mark.parametrize("duplicate", [False, True])
def test_http_card_without_board_id_resolves_only_unique_verified_offer(env, monkeypatch, duplicate):
    _, legs, rows, feeds = world()
    if duplicate:
        feeds["underdog"]["rows"].append(dict(feeds["underdog"]["rows"][0], id="another-offer"))
    monkeypatch.setattr(server, "_bs_rows", lambda: rows)
    monkeypatch.setattr(server.underdog, "board", lambda **k: feeds["underdog"])
    monkeypatch.setattr(server.prizepicks, "board", lambda **k: feeds["prizepicks"])
    monkeypatch.setattr(server, "_SLIP_IDX", {})
    q = urlencode(dict(player="Fixture", prop="kills", maps="12", line=28.5, team="Alpha",
                       opponent="Beta", start=rows[0]["start"], side="More", book="underdog"))
    st, d = call(env, "GET", "/api/line_check?" + q, member="alice")
    assert st == 200
    if duplicate:
        assert not d.get("line_advantages")
    else:
        assert d["status"] == "verified"
        assert d["line_advantages"][0]["line"] == 27.5
