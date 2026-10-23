"""closeout2 copyval 2026-10-23 (owner: "Move authoritative validation before the clipboard write. A rejected or failed
validation must leave the clipboard untouched and show the reason."). Copy slip now asks the server first
(POST /api/slip_log = server.slip_check + the copy log) and writes the clipboard only on its explicit OK.
Every row, name and number is a TEST FIXTURE; every test failed on origin/main 6d6564c (main copied first, logged after,
and its reasons were raw feed notes)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import server
from tests.test_audit13_site import post, site  # noqa: F401 - site is a fixture
from tests.test_audit13_site_ui import NODE
from tests.test_night_consistency import run_page
from tests.test_slip_builder import private_db  # noqa: F401 - private_db is an autouse fixture

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
REAL = datetime.now(timezone.utc)
iso = lambda t: t.isoformat(timespec="seconds")
FUT, PAST = iso(REAL + timedelta(hours=2)), iso(REAL - timedelta(minutes=1))


def _row(rid, player, team, opp, line, start=FUT, **kw):
    return dict({"id": rid, "player": player, "team": team, "opponent": opp, "start": start, "prop": "kills", "scope": "12",
                 "line": line, "higher": 1.0, "lower": 1.0, "alt": False}, **kw)


ROWS = [_row("u1", "alpha1", "Alpha", "Bravo", 27.5), _row("u2", "bravo1", "Bravo", "Alpha", 20.5),
        _row("u3", "alpha2", "Alpha", "Bravo", 18.5), _row("u4", "overonly", "Bravo", "Alpha", 15.5, lower=None)]


def _leg(nick, team, opp, line, side="More", book="underdog", **kw):
    return dict({"nick": nick, "team": team, "opponent": opp, "line": line, "side": side, "book": book, "prop": "kills",
                 "scope": "12", "line_type": "standard", "start": FUT}, **kw)


A, B = _leg("alpha1", "Alpha", "Bravo", 27.5), _leg("bravo1", "Bravo", "Alpha", 20.5)


@pytest.fixture
def feeds(site):
    rows = [dict(r) for r in ROWS]
    site.setattr(server.underdog, "board", lambda wait=False: {"at": datetime.now(timezone.utc).timestamp(), "error": None, "rows": rows})
    site.setattr(server.prizepicks, "board", lambda wait=False: {"at": datetime.now(timezone.utc).timestamp(), "error": None,
                                                                   "state": "ok", "rows": [dict(_row("pp1", "bravo1", "Bravo", "Alpha", 20.5),
                                                                                                over_available=True, under_available=True)]})
    got = []
    site.setattr(server.slips, "log_loaded", lambda d: got.append(d) or True)
    return {"rows": rows, "logged": got}


def _send(legs, book="underdog", cid="c"):
    h = post("/api/slip_log", body=json.dumps({"cid": cid, "book": book, "mode": "member-copy", "legs": legs}).encode())
    return h.code, h.body()


# ================================================================ server: the authoritative check
@pytest.mark.parametrize("case,legs,book,reason", [
    ("moved line", [dict(A, line=26.5), B], "underdog", "alpha1 Over 26.5: Line moved to 27.5"),
    ("platform start (leg's own start passed)", [dict(A, start=PAST), B], "underdog", "alpha1 Over 27.5: Match started"),
    ("graded start (leg's saved graded start passed)", [dict(A, match_start=PAST), B], "underdog", "alpha1 Over 27.5: Match started"),
    ("unavailable side", [A, _leg("overonly", "Bravo", "Alpha", 15.5, side="Less")], "underdog", "overonly Under 15.5: Side not offered"),
    ("pulled line", [A, _leg("ghost", "Bravo", "Alpha", 9.5)], "underdog", "ghost Over 9.5: Line not on the platform now"),
    ("one-team entry", [A, _leg("alpha2", "Alpha", "Bravo", 18.5)], "underdog", "Entries need players from at least 2 different teams"),
    ("two platforms", [A, dict(B, book="prizepicks")], "underdog", "One platform per slip - these picks are on PrizePicks and Underdog"),
    ("one pick", [A], "underdog", "Entries need at least 2 picks"),
])
def test_refused_slip_names_the_reason_and_logs_nothing(feeds, case, legs, book, reason):
    code, d = _send(legs, book)
    assert code == 409 and d["ok"] is False, (case, code, d)
    assert reason in d["legs"], (case, d["legs"])
    assert feeds["logged"] == []


def test_platform_rescheduled_earlier_locks_the_leg(feeds):
    feeds["rows"][0]["start"] = iso(REAL - timedelta(minutes=5))           # the platform now lists it 5 min ago
    code, d = _send([dict(A, start=iso(REAL + timedelta(minutes=20))), B])
    assert code == 409 and "alpha1 Over 27.5: Match started" in d["legs"] and not feeds["logged"]


def test_graded_start_comes_from_the_server_not_the_page(feeds):
    """The page left out the graded start: the server's own graded row (BOARD, by the platform row id) locks the leg."""
    server.BOARD["u1"] = {"side": "More", "match_start": PAST}
    code, d = _send([A, B])                                              # no match_start / lock_at / row_id from the page
    assert code == 409 and "alpha1 Over 27.5: Match started" in d["legs"] and not feeds["logged"]
    server.BOARD.clear()
    server.BOARD["u9"] = {"match_start": PAST}                           # by the leg's own board row id
    code, d = _send([dict(A, row_id="u9"), B], cid="c2")
    assert code == 409 and "alpha1 Over 27.5: Match started" in d["legs"]


def test_feed_down_is_a_plain_retry_reason(feeds, site):
    site.setattr(server.underdog, "board", lambda wait=False: {"at": 0, "error": "timeout", "rows": []})
    code, d = _send([A, B])
    assert code == 409 and d["legs"] == ["alpha1 Over 27.5: " + server.SLIP_RETRY, "bravo1 Over 20.5: " + server.SLIP_RETRY]
    assert not feeds["logged"]


def test_slip_check_reads_each_feed_once_per_request(feeds, site):
    n = []
    rows = feeds["rows"]
    site.setattr(server.underdog, "board", lambda wait=False: n.append(1) or {"at": datetime.now(timezone.utc).timestamp(), "error": None, "rows": rows})
    assert server.slip_check({"book": "underdog", "legs": [A, B, _leg("overonly", "Bravo", "Alpha", 15.5)]})["ok"]
    assert len(n) == 1                                                   # before: one feed copy per leg
    code, d = _send([A, B])                                              # a valid slip: OK and logged once
    assert code == 200 and d["ok"] is True and len(feeds["logged"]) == 1


# ================================================================ page: the clipboard only after the server's OK
PAGE = """(async () => {
  const EV = [], A = []; let LOGS = 0;
  toast = () => {}; ask = async (t, body) => { A.push([t, body]); return "review"; };
  copy = async t => { EV.push("copy"); };
  const SL = INPUT.sl;
  if (INPUT.timeout) setTimeout = (f) => { Promise.resolve().then(f); return 1; };
  fetch = async (u, o) => {
    u = String(u);
    if (u.startsWith("/api/line_check")) return {ok: true, status: 200, json: async () => ({status: "verified", note: null, rows: []})};
    if (u === "/api/slip_log") { LOGS++; EV.push("check"); EV.push(JSON.parse(o.body).legs.map(l => l.row_id).join(","));
      if (SL === "hang") return new Promise(() => {});
      if (SL === "throw") throw new Error("offline");
      return {ok: SL.status === 200, status: SL.status, json: async () => SL.body}; }
    return {ok: false, status: 599, json: async () => ({})};
  };
  localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.legs));
  slipClick({target: {closest: s => s === "[data-slipcopy]" ? {} : null}});
  for (let i = 0; i < 400; i++) await Promise.resolve();
  return {EV, A, LOGS};
})()"""
LEGS = lambda: [{"p": "alpha1", "t": "kills", "sc": "12", "l": 27.5, "s": "More", "bk": "underdog", "mid": "hltv:1", "st": FUT, "tm": "Alpha",
                 "vs": "Bravo", "rid": "u1", "g": "B", "pc": 0.6, "u": 1},
                {"p": "bravo1", "t": "kills", "sc": "12", "l": 20.5, "s": "More", "bk": "underdog", "mid": "hltv:2", "st": FUT, "tm": "Bravo",
                 "vs": "Alpha", "rid": "u2", "g": "B", "pc": 0.6, "u": 1}]
R409 = lambda *t: {"status": 409, "body": {"ok": False, "error": "slip_has_unavailable_legs", "legs": list(t)}}


@needs_node
def test_page_valid_slip_is_checked_then_copied_once(tmp_path):
    out = run_page(tmp_path, PAGE, {"legs": LEGS(), "sl": {"status": 200, "body": {"ok": True, "duplicate": False}}})
    assert out["EV"] == ["check", "u1,u2", "copy"] and out["LOGS"] == 1 and out["A"] == []   # before: copy, then the log


@needs_node
@pytest.mark.parametrize("case,sl,reason", [
    ("moved line", R409("alpha1 Over 27.5: Line moved to 28.5"), "Line moved to 28.5"),
    ("locked (platform start)", R409("alpha1 Over 27.5: Match started"), "Match started"),
    ("locked (graded start)", R409("bravo1 Over 20.5: Match started"), "bravo1 Over 20.5: Match started"),
    ("unavailable side", R409("bravo1 Over 20.5: Side not offered"), "Side not offered"),
    ("one-team entry", R409("Entries need players from at least 2 different teams"), "at least 2 different teams"),
    ("two platforms", R409("One platform per slip - these picks are on PrizePicks and Underdog"), "One platform per slip"),
    ("server 5xx", {"status": 502, "body": {}}, "Could not check the lines right now - try again"),
    ("server 500 with a body", {"status": 500, "body": {"ok": True}}, "Could not check the lines right now - try again"),
    ("200 without an explicit OK", {"status": 200, "body": {}}, "Could not check the lines right now - try again"),
    ("network failure", "throw", "Could not check the lines right now - try again"),
    ("rate limited", {"status": 429, "body": {"error": "Too many"}}, "Too many copies in a minute"),
])
def test_page_refused_or_failed_check_leaves_the_clipboard_untouched(tmp_path, case, sl, reason):
    out = run_page(tmp_path, PAGE, {"legs": LEGS(), "sl": sl})
    assert "copy" not in out["EV"], (case, out)                     # before: copied before the server answered
    assert out["LOGS"] == 1 and out["A"] and out["A"][0][0] == "Not copied" and reason in out["A"][0][1], (case, out)


@needs_node
def test_page_timeout_leaves_the_clipboard_untouched(tmp_path):
    out = run_page(tmp_path, PAGE, {"legs": LEGS(), "sl": "hang", "timeout": True})
    assert "copy" not in out["EV"] and out["A"] and out["A"][0][0] == "Not copied"
    assert "Could not check the lines right now - try again" in out["A"][0][1]


@needs_node
def test_page_one_pick_slip_is_not_copied(tmp_path):
    out = run_page(tmp_path, PAGE, {"legs": LEGS()[:1], "sl": {"status": 200, "body": {"ok": True}}})
    assert out["EV"] == [] and out["A"][0][0] == "Add another pick"   # before: a 1-pick slip copied, never checked


@needs_node
def test_page_body_meets_the_server_rules(tmp_path, feeds):
    """End to end: the body the page posts is the one the server checks (row id carried for the graded start)."""
    body = run_page(tmp_path, "slipLogBody(JSON.parse(JSON.stringify(INPUT.legs)))", {"legs": LEGS()})
    d = json.loads(body)
    assert [l["row_id"] for l in d["legs"]] == ["u1", "u2"]
    h = post("/api/slip_log", body=body.encode())
    assert h.code == 200 and h.body()["ok"] is True and len(feeds["logged"]) == 1
    server.BOARD["u2"] = {"match_start": PAST}
    d["cid"] = "again"
    h = post("/api/slip_log", body=json.dumps(d).encode())
    assert h.code == 409 and "bravo1 Over 20.5: Match started" in h.body()["legs"] and len(feeds["logged"]) == 1
