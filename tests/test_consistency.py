"""2026-10-10 consistency audit: the projection's day is the match day; the card carries the board's same-match stake
for the exact same pick; member wording is Over / Under with chances to 0.1%."""
import os, sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cs2_scout, server


def test_match_day():
    today = datetime.now(timezone.utc).date()
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=13, minute=45)
    assert cs2_scout.match_day(tomorrow.isoformat()) == today.toordinal() + 1        # maps played today count
    assert cs2_scout.match_day(tomorrow.strftime("%Y-%m-%dT%H:%M:%SZ")) == today.toordinal() + 1
    assert cs2_scout.match_day("2020-01-01T10:00:00Z") == today.toordinal()          # past start: today
    assert cs2_scout.match_day(None) == today.toordinal() and cs2_scout.match_day("garbage") == today.toordinal()


def test_perf_input_uses_match_day(monkeypatch):
    import perf_live
    seen = {}
    def fake(nick, team, opp, lan, scope, prop, day=None):
        seen["day"] = day
        return {"lambda": 30.0}
    monkeypatch.setattr(perf_live, "projection", fake)
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    args = SimpleNamespace(scope="12", prop="kills", opponent="MOUZ", match_start=start)
    cs2_scout._perf_input({"player": {"nick": "donk"}, "current_team": "Spirit", "_env": {"tonight": "lan"}}, args)
    assert seen["day"] == cs2_scout.match_day(start) == datetime.now(timezone.utc).date().toordinal() + 2


def _g(p_more, lam, line=38.5, **kw):
    return dict({"p_more": p_more, "p_less": round(1 - p_more, 3), "lambda_final": lam, "line": line, "line_type": "standard",
                 "prop": "kills", "grade": "C", "grade_less": "B", "units": 0.0, "units_less": 0.5, "flags": []}, **kw)


def test_reason_wording():
    v = server.verdict(_g(0.401, 36.31))
    assert v["side"] == "Less" and v["reason"] == "Projection 36.3 vs line 38.5 (-2.2 kills)"
    lean = server.verdict(_g(0.485, 38.2, grade_less="C", units_less=0.0))
    assert "leans Under 51.5%, 0 units" in lean["reason"] and "Less" not in lean["reason"]
    gate = server.verdict(_g(0.4, 36.0, zero_reason="AWPer on a headshot prop"))
    assert gate["grade"] == "LEAN" and gate["reason"].startswith("Leans Under 60.0%") and gate["reason"].endswith("0 units: AWPer on a headshot prop")


def test_board_stake_on_card(monkeypatch):
    start = "2026-10-11T12:00:00Z"
    rows = [{"nick": "donk", "player": "donk", "prop": "kills", "scope": "12", "line": 38.5, "side": "Less", "units": 0.75,
             "units_full": 1.5, "cap_n": 5, "team_name": "Spirit", "start": start}]
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": rows})
    server._BS_CACHE[:] = [0.0, []]
    res = {"verdict": {"side": "Less", "units": 1.5, "grade": "A-", "p": 0.7}, "player_block": {"player": {"nick": "donk"}},
           "next_match": {"start": "2026-10-11T12:00:00.000+00:00"}}
    v = server.board_stake(res, "kills", "12", 38.5)
    assert v["units"] == 0.75 and v["units_full"] == 1.5 and v["cap_n"] == 5 and v["cap_team"] == "Spirit"
    assert res["verdict"]["units"] == 1.5                                     # the logged verdict object is untouched
    for other in ({"line": 37.5}, {"side": "More"}, {"units_full": 1.0}):   # a different pick or stake: no cap carried over
        server._BS_CACHE[:] = [0.0, []]
        monkeypatch.setattr(server, "board_payload", lambda o=other: {"rows": [dict(rows[0], **o)]})
        assert server.board_stake(res, "kills", "12", 38.5)["units"] == 1.5
    server._BS_CACHE[:] = [0.0, []]
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": [dict(rows[0], start="2026-10-12T12:00:00Z")]})
    assert server.board_stake(res, "kills", "12", 38.5)["units"] == 1.5      # another match


def test_stake_caps_match_grader():
    from grader import CONFIG
    c = server.stake_caps()
    assert c["market"] == CONFIG["soft_market_cap"] and c["tape"] == CONFIG["tape_fight_cap"] and c["kills_chance"] == CONFIG["p_cap_kills"]


def test_cached_context_keeps_match_day_and_format(monkeypatch):
    """A reused (cached) context must grade with the cached match's day and format, like a fresh one."""
    import cs2_scout
    seen = {}
    def fake_perf(pb, args):
        seen["start"], seen["bo"] = getattr(args, "match_start", None), getattr(args, "match_bo", None)
        return None
    monkeypatch.setattr(cs2_scout, "_perf_input", fake_perf)
    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    args = SimpleNamespace(prop="kills", line=42.5, line_type="standard", scope="123", opponent="Vitality")
    res = {"next_match": {"start": start, "bo": 5, "opponent": "Vitality"}, "player_block": {}}
    try:
        cs2_scout.finish_line(res, args)
    except Exception:
        pass                                           # the rest of finish_line needs a full context; only the args matter here
    assert args.match_start == start and args.match_bo == 5
