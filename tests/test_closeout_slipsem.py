"""closeout slipsem 2026-10-23: (1) one lock rule and canonical identity for every slip surface, (2) the slip's combined chance
uses each leg's SHOWN chance (the FORM FIGHT halving / caps included), (3) no unavailable or ineligible selection enters a
slip through any path. Every row is a TEST FIXTURE; each test failed on origin/main 7d22556 unless marked 'rule kept'."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import server
import slip_builder as sb
from slip_probability import summary
from tests.test_audit13_site import post, site  # noqa: F401 - site is a fixture
from tests.test_audit13_site_ui import NODE
from tests.test_night_consistency import run_page
from tests.test_slip_builder import M, NOW, _boards, board, private_db, row  # noqa: F401 - private_db is an autouse fixture

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
iso = lambda t: t.isoformat(timespec="seconds")
REAL = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _real_now():
    """REAL is 'now' for each test, not for module import: in a 20+ minute full run the +2 min start had already passed."""
    global REAL
    REAL = datetime.now(timezone.utc)


# ================================================================ 1. the lock rule (server side)
def test_lock_time_is_the_earlier_of_platform_and_graded_match_start():
    plat, graded = NOW + timedelta(minutes=10), NOW
    assert sb.lock_time({"start": iso(plat), "match_start": iso(graded)}) == graded       # platform listed later (Underdog +10)
    assert sb.lock_time({"start": iso(graded), "match_start": iso(plat)}) == graded       # platform listed earlier
    assert sb.lock_time({"start": iso(plat)}) == plat and sb.lock_time({}) is None


def test_studio_excludes_a_line_whose_graded_match_started_before_the_platform_start():
    r = row(1, 1, start=iso(NOW + timedelta(minutes=10)), match_start=iso(NOW - timedelta(minutes=1)))
    assert sb.why_excluded(r, sb.normalize_settings({"book": "underdog"}), M, NOW) == "started / locked"
    later = row(2, 2, start=iso(NOW + timedelta(hours=1)), match_start=iso(NOW + timedelta(hours=3)))   # platform earlier
    assert sb.why_excluded(later, sb.normalize_settings({"book": "underdog"}), M, NOW + timedelta(minutes=61)) == "started / locked"
    assert sb.why_excluded(later, sb.normalize_settings({"book": "underdog"}), M, NOW) is None


def test_studio_leg_keeps_canonical_identity_and_its_lock():
    r = row(1, 1, match_id="hltv:7", start=iso(NOW + timedelta(hours=3, minutes=10)), match_start=iso(NOW + timedelta(hours=3)))
    rec = sb.pick_record(r)
    assert rec["match_id"] == "hltv:7" and rec["match_slug"] == "m1" and rec["start"] == r["start"]   # identity untouched
    assert rec["lock_at"] == iso(NOW + timedelta(hours=3))
    # the same match keyed from the platform start or from the graded start is ONE match (match id / slug decide)
    other = dict(r, start=r["match_start"])
    assert sb.match_keys(r) & sb.match_keys(other) >= {"mid:hltv:7", "slug:m1"}


def test_verify_before_copy_locks_a_rescheduled_match_at_its_new_earlier_start():
    rows = board(2, per_match=1, seed=3)
    slip = sb.build(rows, {"sizes": [2], "count": 1}, model=M, now=NOW)["slips"][2][0]
    assert sb.verify_before_copy(slip, _boards(rows, NOW.timestamp()), now=NOW, model=M)["ok"]
    moved = [dict(rows[0], start=iso(NOW - timedelta(minutes=5)))] + rows[1:]          # rescheduled to before now
    out = sb.verify_before_copy(slip, _boards(moved, NOW.timestamp()), now=NOW, model=M)
    assert not out["ok"] and any("started / locked" in c for c in out["changes"])
    graded_early = [dict(rows[0], match_start=iso(NOW - timedelta(minutes=1)))] + rows[1:]   # graded match already began
    assert any("started / locked" in c for c in sb.verify_before_copy(slip, _boards(graded_early, NOW.timestamp()), now=NOW, model=M)["changes"])


def _feed(start):
    return {"at": REAL.timestamp(), "error": None, "rows": [
        {"id": "x1", "player": "ZywOo", "prop": "kills", "scope": "12", "line": 35.5, "team": "Vitality", "opponent": "MOUZ",
         "start": start, "alt": False, "higher": 1.0, "lower": 1.0, "ud_player_id": "u1"}]}


def test_board_hides_a_line_once_the_graded_match_started_and_carries_lock_at(monkeypatch):
    import prizepicks
    import underdog
    plat = iso(REAL + timedelta(minutes=10))
    monkeypatch.setattr(underdog, "board", lambda wait=False: _feed(plat))
    monkeypatch.setattr(prizepicks, "board", lambda wait=False: dict(_feed(plat), rows=[]))
    g = {"side": "More", "grade": "B", "p": 0.6, "units": 1.0, "graded_line": 35.5, "team_name": "Vitality", "opp_name": "MOUZ",
         "nick": "ZywOo", "match_start": iso(REAL + timedelta(minutes=2))}
    monkeypatch.setitem(server.BOARD, "x1", g)
    r = server.board_payload()["rows"][0]
    assert r["match_start"] == g["match_start"] and r["lock_at"] == g["match_start"] and r["start"] == plat
    monkeypatch.setitem(server.BOARD, "x1", dict(g, match_start=iso(REAL - timedelta(minutes=1))))
    assert server.board_payload()["rows"] == []                       # before: shown until the platform's 10 min later start


def test_card_identity_names_the_lock_of_its_line(monkeypatch):
    res = {"player_block": {"player": {"nick": "bnc"}, "current_team": "ALKA"},
           "next_match": {"slug": "alka-vs-los-ninos", "start": "2026-10-23T22:00:00Z", "opponent": "Los Ninos"}}
    monkeypatch.setattr(server, "_card_match_id", lambda r: "hltv:9")
    rows = [{"book": "underdog", "nick": "bnc", "prop": "kills", "scope": "12", "line": 27.5, "match_slug": "alka-vs-los-ninos",
             "start": "2026-10-23T22:10:00+00:00", "match_id": "hltv:9"}]
    monkeypatch.setattr(server, "_bs_rows", lambda: rows)
    v = server.card_identity(res, {"side": "More"}, "kills", "12", 27.5, "underdog")
    assert v["match_id"] == "hltv:9" and v["lock_at"] == "2026-10-23T22:00:00+00:00"   # the earlier: the graded start
    rows[0]["start"] = "2026-10-23T21:50:00+00:00"
    assert server.card_identity(res, {"side": "More"}, "kills", "12", 27.5, "underdog")["lock_at"] == "2026-10-23T21:50:00+00:00"
    assert server.card_identity(res, {"side": "More"}, "kills", "12", 26.5, "underdog")["lock_at"] == "2026-10-23T22:00:00+00:00"


def _ud(monkeypatch, start):
    row_ = {"player": "TEST", "team": "Alpha", "opponent": "Bravo", "start": start, "prop": "kills", "scope": "1", "line": 10.5,
            "higher": 1, "lower": 1, "alt": False}
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": [row_], "at": REAL.timestamp()})
    return {"nick": "TEST", "team": "Alpha", "opponent": "Bravo", "start": start, "book": "underdog", "prop": "kills", "scope": "1",
            "line": 10.5, "side": "Less"}


def test_server_recheck_uses_the_same_lock(monkeypatch):
    leg = _ud(monkeypatch, iso(REAL + timedelta(minutes=10)))
    assert server.slip_leg_problem(leg) is None
    assert "started" in server.slip_leg_problem(dict(leg, match_start=iso(REAL - timedelta(minutes=1))))
    assert "started" in server.slip_leg_problem(dict(leg, lock_at=iso(REAL - timedelta(minutes=1))))
    # rescheduled: the leg was built for 20 min from now; the platform now lists it 5 min ago (within the join window)
    leg2 = _ud(monkeypatch, iso(REAL - timedelta(minutes=5)))
    assert "started" in server.slip_leg_problem(dict(leg2, start=iso(REAL + timedelta(minutes=20))))
    chk = server.slip_lock({"status": "verified", "rows": [{"start": iso(REAL + timedelta(hours=1))}]}, iso(REAL + timedelta(hours=2)), None)
    assert chk["status"] == "verified" and chk["lock_at"] == iso(REAL + timedelta(hours=1))
    chk = server.slip_lock({"status": "changed", "new_line": 9.5, "rows": [{"start": iso(REAL - timedelta(minutes=1))}]}, None)
    assert chk["status"] == "unavailable" and chk["note"] == server.LOCKED_NOTE and "new_line" not in chk


def test_slip_log_refuses_two_platforms(site, monkeypatch):
    got = []
    monkeypatch.setattr(server.slips, "log_loaded", lambda d: got.append(d) or True)
    monkeypatch.setattr(server, "slip_leg_check", lambda leg, feeds=None, now=None: None)        # every leg's own line is up
    body = lambda bk, legs: json.dumps({"cid": "c" + bk + str(len(got)), "book": bk, "legs": legs}).encode()
    h = post("/api/slip_log", body=body("underdog", [{"book": "underdog"}, {"book": "prizepicks"}]))
    assert h.code == 409 and not got                                           # before: 200, logged as a valid copy
    assert post("/api/slip_log", body=body("prizepicks", [{"book": "underdog"}, {"book": "underdog"}])).code == 409
    assert post("/api/slip_log", body=body("underdog", [{"book": "underdog"}, {"book": "underdog"}])).code == 200 and len(got) == 1


# ================================================================ 1. the lock rule + identity (page)
CARD = lambda start, ms, lock=None, book=None, slug="alka-vs-los-ninos", mid=None: {
    "book": book, "grade": {"line": 27.5, "prop": "kills", "scope": "12", "line_type": "standard", "probs_raw": {"over": .6, "under": .4, "push": 0}},
    "verdict": {"side": "More", "grade": "B", "model_grade": "B", "p": 0.6, "units": 0.5, "match_id": mid, "lock_at": lock},
    "player_block": {"player": {"nick": "bnc", "id": 7}, "current_team": "ALKA"},
    "next_match": {"opponent": "Los Ninos", "start": ms, "slug": slug}, **({"line_start": start} if start else {})}


@needs_node
def test_card_leg_locks_at_the_earlier_start_and_keeps_its_identity(tmp_path):
    now = datetime.now(timezone.utc)                               # not the module's REAL: the full suite reaches this > 1 min later
    soon, later = iso(now + timedelta(minutes=30)), iso(now + timedelta(minutes=40))
    past = iso(now - timedelta(minutes=1))
    out = run_page(tmp_path, """(() => {
      const a = legFrom(INPUT.later_platform), b = legFrom(INPUT.earlier_platform), c = legFrom(INPUT.graded_started);
      return {a: [a.st, lockIso(a)], b: lockIso(b), started: [legStarted(a), legStarted(c)], ids: [a.m, a.mid, c.m]};
    })()""", {"later_platform": CARD(later, soon), "earlier_platform": CARD(soon, later), "graded_started": CARD(later, past)})
    assert out["a"][0] == later                                    # the platform's start is kept for display / matching
    assert out["a"][1] == out["b"] == datetime.fromisoformat(soon).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert out["started"] == [False, True]                         # before: the graded match had begun, the leg was not locked
    assert out["ids"][0] == out["ids"][2] == "alka-vs-los-ninos"


@needs_node
def test_one_match_is_one_slip_event_without_a_match_id(tmp_path):
    """Before: 'slug@17:10' from the board, 'slug@17:00' from a typed card - one pick twice, teammates unpaired."""
    plat, graded = iso(REAL + timedelta(hours=2, minutes=10)), iso(REAL + timedelta(hours=2))
    rw = {"id": "u:1", "book": "underdog", "nick": "bnc", "player": "bnc", "team": "ALKA", "team_name": "ALKA", "opponent": "Los Ninos",
          "opp_name": "Los Ninos", "start": plat, "match_start": graded, "prop": "kills", "scope": "12", "line": 27.5, "side": "More",
          "p": 0.6, "grade": "B", "units": 0.5, "match_slug": "alka-vs-los-ninos", "match_id": None, "model_outcomes": {"over": .6, "under": .4, "push": 0}}
    hl = dict(rw, match_slug=None, settle_slug="hltv-2399908")
    out = run_page(tmp_path, """(() => {
      const b = legFrom(boardCard(INPUT.rw)), c = legFrom(INPUT.card);
      const hb = legFrom(boardCard(INPUT.hl)), hc = legFrom(Object.assign({}, INPUT.card, {next_match: {opponent: "Los Ninos", start: INPUT.card.next_match.start, slug: null, hltv_match: 2399908}}));
      return {k: [legKey(b), legKey(c)], row: [slipKeyRow(INPUT.rw), slipKeyLeg(b)], h: [legKey(hb), legKey(hc)], locks: [lockIso(b), lockIso(c)]};
    })()""", {"rw": rw, "hl": hl, "card": CARD(None, graded)})
    assert out["k"][0] == out["k"][1] and out["row"][0] == out["row"][1] and out["h"][0] == out["h"][1]
    assert out["locks"][0] == out["locks"][1]                     # one lock for the board leg and the card leg


@needs_node
def test_board_top_plays_share_link_and_studio_use_the_lock(tmp_path):
    fut, past = iso(REAL + timedelta(minutes=10)), iso(REAL - timedelta(minutes=1))
    out = run_page(tmp_path, """(() => {
      const r = {start: INPUT.fut, match_start: INPUT.past}, s = {start: INPUT.fut, match_start: INPUT.past, legs: []};
      const sl = {slip_id: "b/b/s1", build_id: "b/b", n: 2, book: "underdog", status: "live", all_hit: .3,
                  legs: [{nick: "a", side: "More", line: 1.5, prop: "kills", scope: "12", start: INPUT.fut, match_start: INPUT.past, grade: "B", p: .6}]};
      return {row: hasStarted(r), leg: legStarted({st: INPUT.fut, ms: INPUT.past}), studio: /Started – locked/.test(studioSlip(sl)),
              first: firstLock([{st: INPUT.fut, lk: INPUT.past}])};
    })()""", {"fut": fut, "past": past})
    assert out["row"] and out["leg"] and out["studio"] and out["first"] == ""


# ================================================================ 2. the slip's chance = the leg's shown chance
def test_slip_chance_uses_the_shown_chance_of_each_leg():
    # an old-pipeline FORM FIGHT leg: raw Over 64% (no push), shown chance halved to 57%; a whole-line leg with a 10% push
    fight = {"side": "More", "p": 0.57, "line": 30.5, "model_outcomes": {"over": .64, "under": .36, "push": 0.0}}
    whole = {"side": "More", "p": 0.6, "line": 10, "model_outcomes": {"over": .54, "under": .36, "push": .1}}
    out = summary([fight, whole])
    assert out["all_hit_basis"] == "shown_chance"
    assert out["all_hit"] == pytest.approx(0.57 * 0.6 * 0.9)       # before: .64 x .54 (the raw mass, not the shown chances)
    capped = {"side": "Less", "p": 0.75, "line": 20.5, "model_outcomes": {"over": .15, "under": .85, "push": 0.0}}   # kills cap
    assert summary([capped, capped])["all_hit"] == pytest.approx(.5625)
    old = summary([{"side": "More", "p": .6, "line": 10}] * 2)        # an older record without outcomes: unchanged
    assert old["all_hit"] == pytest.approx(.36) and old["all_hit_basis"] == "conditional_no_push"


def test_studio_slip_all_hit_is_the_product_of_its_legs_shown_chances():
    rows = board(2, per_match=1, seed=5, model_outcomes={"over": .7, "under": .3, "push": 0.0})
    slip = sb.build(rows, {"sizes": [2], "count": 1}, model=M, now=NOW)["slips"][2][0]
    assert slip["all_hit_basis"] == "shown_chance"
    assert slip["all_hit"] == pytest.approx(slip["legs"][0]["p"] * slip["legs"][1]["p"], abs=1e-6)


@needs_node
def test_manual_slip_chance_matches_the_server_rule(tmp_path):
    legs = [{"side": "More", "p": 0.57, "line": 30.5, "model_outcomes": {"over": .64, "under": .36, "push": 0.0}},
            {"side": "More", "p": 0.6, "line": 10, "model_outcomes": {"over": .54, "under": .36, "push": .1}}]
    out = run_page(tmp_path, """(() => { const ls = INPUT.map(l => ({s: l.side, pc: l.p, l: l.line, model_outcomes: l.model_outcomes}));
      const e = manualProbability(ls); return {e, w: studioChance({n: 2, legs: [{line: 30.5}, {line: 10}], ...e})}; })()""", legs)
    assert out["e"]["all_hit"] == pytest.approx(summary(legs)["all_hit"]) and out["e"]["all_hit_basis"] == "shown_chance"
    assert "shown chances, each times its chance of not pushing" in out["w"] and "if none push" not in out["w"]


# ================================================================ 3. every path refuses an ineligible selection (page)
FLOW = """
const T = [], calls = []; toast = m => T.push(m); ask = async () => "cancel";
let LC = INPUT.lc || {status: "verified", note: null, rows: [{start: INPUT.fut}]};
fetch = async (u, o) => { calls.push(String(u)); if (String(u).startsWith("/api/line_check")) return {ok: true, json: async () => LC};
  return {ok: false, status: 599, json: async () => ({})}; };
"""
FUT = lambda m=120: iso(REAL + timedelta(minutes=m))
BROW = lambda **kw: dict({"id": "u:1", "book": "underdog", "nick": "bnc", "player": "bnc", "team": "ALKA", "team_name": "ALKA",
                          "opponent": "Los Ninos", "opp_name": "Los Ninos", "start": FUT(), "match_start": FUT(), "prop": "kills",
                          "scope": "12", "line": 27.5, "graded_line": 27.5, "side": "More", "p": 0.6, "grade": "B", "units": 0.5,
                          "match_slug": "alka-vs-los-ninos", "match_id": "hltv:1", "graded_model": "M1",
                          "model_outcomes": {"over": .6, "under": .4, "push": 0}}, **kw)


def _add(tmp_path, card_js, data):
    return run_page(tmp_path, FLOW + """(async () => { localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.slip || []));
      await addToSlip(%s); const s = JSON.parse(localStorage.getItem("cs2s_slip") || "[]");
      return {n: s.length, st: s.map(x => x.now && x.now.status), bk: s.map(x => x.bk), T}; })()""" % card_js, data)


@needs_node
@pytest.mark.parametrize("case,lc,row_kw,added", [
    ("ok", None, {}, 1),
    ("side not offered (board row flag)", None, {"unavailable": "this side is no longer offered"}, 0),
    ("line pulled", {"status": "unavailable", "note": "Underdog has no line for this player / stat / maps right now", "rows": []}, {}, 0),
    ("side listed but not offered", {"status": "unavailable", "note": "Underdog: this side is not offered (or not confirmed) on this line",
                                     "rows": [{"line": 27.5}]}, {}, 0),
    ("line moved (member cancels)", {"status": "changed", "new_line": 28.5, "note": "Underdog now shows 28.5", "rows": [{"line": 28.5}]}, {}, 0),
    ("graded match started (lock rule)", None, {"match_start": iso(REAL - timedelta(minutes=1))}, 0),
    ("platform start passed", None, {"start": iso(REAL - timedelta(minutes=1))}, 0),
    ("server lock passed (rescheduled)", {"status": "verified", "note": None, "rows": [], "lock_at": iso(REAL - timedelta(minutes=1))}, {}, 0),
    ("kept from outage (rule kept: copies as stale)", None, {"hltv_retry": True}, 1),
    ("0U (rule kept: manual slips hold 0U legs)", None, {"units": 0, "grade": "LEAN"}, 1),
])
def test_board_plus_slip_and_top_plays(tmp_path, case, lc, row_kw, added):
    """Board '+ Slip' and Top plays both call addToSlip(boardCard(row)) (data-bslip)."""
    out = _add(tmp_path, "boardCard(INPUT.row)", {"row": BROW(**row_kw), "lc": lc, "fut": FUT()})
    assert out["n"] == added, (case, out)
    if case.startswith("kept"):
        assert out["st"] == ["stale"]


@needs_node
def test_board_plus_slip_refuses_another_platform(tmp_path):
    other = {"p": "x", "t": "kills", "sc": "12", "s": "More", "l": 20.5, "bk": "prizepicks", "mid": "hltv:2", "st": FUT()}
    out = _add(tmp_path, "boardCard(INPUT.row)", {"row": BROW(), "slip": [other], "fut": FUT()})
    assert out["n"] == 1 and any("one platform per slip" in t for t in out["T"])


@needs_node
@pytest.mark.parametrize("case,lc,book,added,status", [
    ("offer card ok", None, "underdog", 1, "verified"),
    ("offer card, side listed but not offered", {"status": "unavailable", "note": "Underdog: this side is not offered (or not confirmed) on this line",
                                                 "rows": [{"line": 27.5}]}, "underdog", 0, None),
    ("offer card, line pulled", {"status": "unavailable", "note": "Underdog has no line for this player / stat / maps right now", "rows": []}, "underdog", 0, None),
    ("typed card, line pulled (rule kept: your own line)", {"status": "unavailable", "note": "no line", "rows": []}, None, 1, "research"),
    ("typed card, side listed but not offered", {"status": "unavailable", "note": "Underdog: this side is not offered (or not confirmed) on this line",
                                                 "rows": [{"line": 27.5}]}, None, 0, None),
])
def test_card_add_to_slip(tmp_path, case, lc, book, added, status):
    """The card's 'Add to slip' (and previous / next, Matchroom and match-table cards, which open the same card)."""
    out = _add(tmp_path, "INPUT.card", {"card": CARD(FUT(), FUT(), book=book, mid="hltv:1"), "lc": lc, "fut": FUT()})
    assert out["n"] == added, (case, out)
    if status:
        assert out["st"] == [status]


@needs_node
def test_card_add_refuses_a_line_whose_graded_match_started(tmp_path):
    out = _add(tmp_path, "INPUT.card", {"card": CARD(FUT(), iso(REAL - timedelta(minutes=1)), book="underdog"), "fut": FUT()})
    assert out["n"] == 0 and any("started" in t for t in out["T"])


STUDIO_JS = """(async () => {
  const b = JSON.parse(JSON.stringify(INPUT.rows)); BK.underdog.rows = b; BK.underdog.loaded = true;
  STUDIO.batch = {batch_id: "b", slips: {2: [INPUT.slip]}}; STUDIO.notes = INPUT.notes || {};
  await studioToMySlip(INPUT.slip.slip_id); const s = JSON.parse(localStorage.getItem("cs2s_slip") || "[]");
  return {n: s.length, p: s.map(x => x.pc), T};
})()"""


def _studio(tmp_path, rows, legs_kw=None, notes=None):
    legs = [dict(row_id=r["id"], nick=r["nick"], player=r["player"], team=r["team"], opponent=r["opponent"], start=r["start"],
                 match_start=r["match_start"], match_id=r["match_id"], match_slug=r["match_slug"], prop=r["prop"], scope=r["scope"],
                 line=r["line"], side=r["side"], book="underdog", market_type="standard", grade=r["grade"], p=r["p"], units=r["units"],
                 graded_model=r["graded_model"], model_outcomes=r["model_outcomes"]) for r in rows]
    for l, kw in zip(legs, legs_kw or [{}] * len(legs)):
        l.update(kw)
    sl = {"slip_id": "b/b/s1", "build_id": "b/b", "n": 2, "book": "underdog", "status": "live", "legs": legs}
    return run_page(tmp_path, FLOW + STUDIO_JS, {"rows": rows, "slip": sl, "notes": notes, "fut": FUT()})


@needs_node
def test_studio_send_to_my_slip(tmp_path):
    r1, r2 = BROW(), BROW(id="u:2", nick="mate", player="mate", match_id="hltv:2", match_slug="m2", p=0.62)
    assert _studio(tmp_path, [r1, r2])["n"] == 2
    # the board's grade changed since the build: before, the old grade / chance went on the slip
    out = _studio(tmp_path, [r1, dict(r2, grade="B+", p=0.66)], [{}, {"grade": "B", "p": 0.62}])
    assert out["n"] == 0 and any("grade changed" in t for t in out["T"])
    out = _studio(tmp_path, [r1, dict(r2, unavailable="this side is no longer offered")])
    assert out["n"] == 1                                            # that leg refused by the usual add (side not offered)
    out = _studio(tmp_path, [r1, dict(r2, hltv_retry=True)])
    assert out["n"] == 0 and any("saved one" in t for t in out["T"])   # the Studio never sends a kept-from-outage grade
    out = _studio(tmp_path, [r1, dict(r2, units=0)])
    assert out["n"] == 0 and any("0 units" in t for t in out["T"])
    out = _studio(tmp_path, [r1, r2], [{}, {"match_start": iso(REAL - timedelta(minutes=1))}])
    assert out["n"] == 0 and any("started" in t for t in out["T"])
    out = _studio(tmp_path, [r1, r2], notes={"b/b/s1": ["a line changed"]})
    assert out["n"] == 0 and any("changed since it was built" in t for t in out["T"])


@needs_node
def test_share_link_refuses_a_line_whose_graded_match_started(tmp_path):
    out = run_page(tmp_path, """(() => { BK.underdog.rows = [INPUT.row]; BK.underdog.loaded = true;
      return sharedLegs([{p: "bnc", l: 27.5, t: "kills", sc: "12", bk: "underdog", s: "More", lt: "standard", m: "alka-vs-los-ninos", st: INPUT.row.start}]).map(z => z.why); })()""",
                   {"row": BROW(match_start=iso(REAL - timedelta(minutes=1)))})
    assert out == ["the match has started – locked"]


@needs_node
def test_saved_slip_recheck_locks_by_the_rule(tmp_path):
    legs = [{"p": "bnc", "t": "kills", "sc": "12", "l": 27.5, "s": "More", "bk": "underdog", "mid": "hltv:1", "st": FUT(), "ms": FUT(),
             "now": {"status": "verified"}},
            {"p": "old", "t": "kills", "sc": "12", "l": 20.5, "s": "More", "bk": "underdog", "mid": "hltv:3", "st": FUT()}]   # an older leg
    out = run_page(tmp_path, FLOW + """(async () => { localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.legs));
      LC = {status: "verified", note: null, rows: [], lock_at: INPUT.past};
      const s = await recheckSlip(); return s.map(x => [x.now.status, legStarted(x)]); })()""",
                   {"legs": legs, "past": iso(REAL - timedelta(minutes=1)), "fut": FUT()})
    assert out == [["started", True], ["started", True]]            # before: both 'verified' (the platform's start had not passed)


@needs_node
def test_copy_refuses_a_saved_slip_on_two_platforms(tmp_path):
    legs = [{"p": "a", "t": "kills", "sc": "12", "l": 27.5, "s": "More", "bk": "underdog", "mid": "hltv:1", "st": FUT()},
            {"p": "b", "t": "kills", "sc": "12", "l": 20.5, "s": "More", "bk": "prizepicks", "mid": "hltv:3", "st": FUT()}]
    out = run_page(tmp_path, FLOW + """(async () => { localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.legs));
      let asked = ""; ask = async (t) => { asked = t; return "review"; }; let copied = null; copy = async t => { copied = t; };
      slipClick({target: {closest: s => s === "[data-slipcopy]" ? {} : null}});
      for (let i = 0; i < 200; i++) await Promise.resolve();
      return {asked, copied, logged: calls.filter(u => u === "/api/slip_log").length}; })()""", {"legs": legs, "fut": FUT()})
    assert out["asked"] == "One platform per slip" and out["copied"] is None and out["logged"] == 0
