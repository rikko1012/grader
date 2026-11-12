"""Owner decision 2026-11-06 (sports cap-2), workspace side: a STORED one-sided Underdog line (variant one_sided) is shown
with its line and price, labelled "Over only" / "Under only", and never graded, staked, put on a slip, tracked or
priced for EV - even when a forecast, line read or server signal reached the board file. A two-sided line whose other
side is suspended right now keeps its grade. workspace_sports.py never imports sports. Every name / number is a TEST
FIXTURE."""
import time

import workspace_sports as ws

DOC = {"events": {"e1": {"home": {"abbr": "BOS", "team_id": "t-bos"}, "away": {"abbr": "NY", "team_id": "t-ny"},
                         "start_utc": "2099-01-01T00:00:00Z"}},
       "fc": {"f1": {"id": "f1", "m": "sm-1", "po": 0.7, "pu": 0.3, "pp": 0.0, "mu": 24.0, "at": "2026-11-06T18:00:00Z"}},
       "stats": {"points": "Points"}}
MK = {"l": 20.5, "c": {"po": 0.55, "pu": 0.45, "pp": 0.0, "n": 2}, "bk": []}
M = {"k": "e1|nba:player:espn:1|points|full_game", "e": "e1", "pid": "nba:player:espn:1", "n": "Player One", "tm": "NY",
     "tid": "t-ny", "st": "points", "mk": MK, "q": []}
SIG = {"v": "cf-2", "ln": "over", "pl": 0.7, "e": 15.0, "s": 15.0, "gr": "B"}


def _q(**kw):
    q = {"p": "underdog", "l": 20.5, "v": "one_sided", "o": [1, 0], "m": [0.86, None], "r": "r1", "g": 0, "f": "f1",
         "lr": {"f": "f1", "ln": 20.5, "po": 0.7, "pu": 0.3, "pp": 0.0}, "x": SIG, "t": "2026-11-06T18:00:00Z"}
    q.update(kw)
    return q


def test_one_sided_record_is_labelled_and_never_graded():
    rec, x = ws.record_of("nba", DOC, M, _q(), {})
    sc = rec["scout"]
    assert rec["market"]["line_type"] == "over only"
    assert sc["grade"] is None and sc["units"] is None and sc["side"] is None and sc["p_over"] is None
    assert sc["hold"] == {"label": "Over only", "reason": "Over only: Scout grades lines that offer both sides.", "short": "Over only"}
    assert sc["validation"]["label"] == "Over only" and "No bet" not in str(rec)
    assert rec["offer"]["line"] == 20.5 and rec["offer"]["over"] == {"offered": True, "payout": 0.86, "price": None}
    assert rec["market_chance"] == {"p_over": 0.55, "p_under": 0.45, "sources": 2}      # plain chance, as on ungraded rows
    assert x["fid"] is None and x["lean"] is None and x["po"] is None
    rec, _ = ws.record_of("nba", DOC, M, _q(o=[0, 1], m=[None, 1.1]), {})
    assert rec["market"]["line_type"] == "under only" and rec["scout"]["hold"]["label"] == "Under only"


def test_suspended_side_on_a_two_sided_line_keeps_its_grade():
    rec, _ = ws.record_of("nba", DOC, M, _q(v="balanced", g=1, lr=None, m=None), {})
    assert rec["scout"]["grade"] == "B" and rec["scout"]["side"] == "over" and rec["scout"]["units"] == 0
    assert ws.one_sided_label({"v": "balanced", "o": [1, 0]}) is None
    assert ws.one_sided_label({"v": "one_sided", "o": [1, 0]}) == "Over only"


class _Srv:
    pass


def test_slip_check_refuses_a_one_sided_leg(monkeypatch):
    from datetime import datetime, timezone
    rec, x = ws.record_of("nba", DOC, M, _q(), {})
    snap = {"index": {("underdog", x["rid"]): x}, "feeds": {"underdog": {"state": "ok"}}, "at": time.time()}
    monkeypatch.setattr(ws, "snapshot", lambda srv, lg: snap)
    called = []
    monkeypatch.setattr(ws, "live_check", lambda *a, **k: called.append(a) or {"ok": True})
    leg = {"book": "underdog", "side": "over", "row_id": x["rid"], "rev": x["rev"], "line": 20.5}
    out = ws.check_leg(_Srv(), leg, "nba", datetime.now(timezone.utc))
    codes = {p["code"]: p["text"] for p in out["problems"]}
    assert out["ok"] is False and codes.get("not_gradable") == "Over only: Scout grades lines that offer both sides."
    assert not called                                                  # never sent on to be tracked


def test_ev_board_never_prices_a_one_sided_line():
    import workspace_ev
    src = open(workspace_ev.__file__, encoding="utf-8").read()
    assert '(m.get("line_type") or "standard") != "standard"' in src     # "over only" / "under only" never priced
