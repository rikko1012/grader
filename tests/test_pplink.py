"""PP_OPEN_LINK (2026-10-28): "Open in PrizePicks" (PrizePicks' own ?projections= lineup link, owner-verified on the web)
and "Open Underdog" (pick'em home, no pre-fill) on checked slips - CS2 manual slip + Slip Studio, LoL. The link is built
only after the server's slip check passed (CS2: POST /api/slip_log / Studio verify; LoL: the fresh member-board recheck),
from rows already on the board (no upstream call). Default OFF: with the flag off every page draws exactly what the base
commit drew. Every row, name and number is a TEST FIXTURE except the two projection ids of the owner's sample lineup."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import pplink
import server
from tests.test_audit13_site import H, post, site  # noqa: F401 - site is a fixture
from tests.test_audit13_site_ui import NODE, ROOT
from tests.test_audit_h_web import H2, _lol_row
from tests.test_slip_builder import private_db  # noqa: F401 - private_db is an autouse fixture

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
BASE = "370d736"                         # origin/main the feature branched from: the pages as they were
REAL = datetime.now(timezone.utc)
iso = lambda t: t.isoformat(timespec="seconds")
FUT, PAST = iso(REAL + timedelta(hours=2)), iso(REAL - timedelta(minutes=1))
SAMPLE = "https://app.prizepicks.com/board?projections=16360820-u-31.5,16360825-o-29.5"


# ================================================================ the builder (pure)
def test_builder_exact_urls():
    legs = [{"projection_id": "16360820", "side": "Less", "line": 31.5}, {"row_id": "pp:16360825", "side": "More", "line": 29.5}]
    assert pplink.pp_link(legs) == (SAMPLE, None)
    url, why = pplink.pp_link([{"projection_id": "1", "side": "More", "line": 30.0}, {"projection_id": "2", "side": "Less", "line": 0.5},
                               {"projection_id": "3", "side": "More", "line": 25, "odds_type": "demon"}])
    assert url == "https://app.prizepicks.com/board?projections=1-o-30,2-u-0.5,3-o-25" and why is None
    assert "invite_code" not in url and "wager_id" not in url
    six = [{"projection_id": str(i), "side": "More", "line": 10.5} for i in range(1, 7)]
    assert pplink.pp_link(six)[0].count(",") == 5


@pytest.mark.parametrize("case,legs,why", [
    ("one pick", [{"projection_id": "1", "side": "More", "line": 1.5}], "2-6 picks"),
    ("seven picks", [{"projection_id": str(i), "side": "More", "line": 1.5} for i in range(7)], "2-6 picks"),
    ("not a list", None, "2-6 picks"),
    ("underdog leg", [{"projection_id": "1", "side": "More", "line": 1.5, "book": "underdog"}, {"projection_id": "2", "side": "More", "line": 1.5}], "Only PrizePicks"),
    ("missing id", [{"side": "More", "line": 1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "id for a pick"),
    ("non-digit id", [{"projection_id": "pp:abc", "side": "More", "line": 1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "id for a pick"),
    ("underdog-style id", [{"row_id": "u1", "side": "More", "line": 1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "id for a pick"),
    ("no side", [{"projection_id": "1", "side": "", "line": 1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "no side"),
    ("odd side word", [{"projection_id": "1", "side": "Higher", "line": 1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "no side"),
    ("goblin Less", [{"projection_id": "1", "side": "Less", "line": 1.5, "odds_type": "goblin"}, {"projection_id": "2", "side": "More", "line": 1.5}], "More only"),
    ("demon Less", [{"projection_id": "1", "side": "Less", "line": 1.5, "odds_type": "Demon"}, {"projection_id": "2", "side": "More", "line": 1.5}], "More only"),
    ("negative line", [{"projection_id": "1", "side": "More", "line": -1.5}, {"projection_id": "2", "side": "More", "line": 1.5}], "no current line"),
    ("NaN line", [{"projection_id": "1", "side": "More", "line": float("nan")}, {"projection_id": "2", "side": "More", "line": 1.5}], "no current line"),
    ("same projection twice", [{"projection_id": "1", "side": "More", "line": 1.5}, {"projection_id": "1", "side": "Less", "line": 1.5}], "twice"),
])
def test_builder_refuses(case, legs, why):
    url, reason = pplink.pp_link(legs)
    assert url is None and why in reason, (case, reason)


def test_flag_default_off():
    assert pplink.enabled({}) is False and pplink.enabled({"PP_OPEN_LINK": "0"}) is False
    assert pplink.enabled({"PP_OPEN_LINK": "true"}) is False and pplink.enabled({"PP_OPEN_LINK": "1"}) is True
    assert pplink.line_token(30.0) == "30" and pplink.line_token(31.5) == "31.5" and pplink.side_token("Higher") is None


# ================================================================ server: POST /api/slip_log with "open"
def _pp(rid, player, team, opp, line, ot="standard", under=True, start=FUT):
    return {"id": rid, "book": "prizepicks", "market_id": rid[3:], "player": player, "team": team, "opponent": opp, "start": start,
            "prop": "kills", "scope": "12", "line": line, "higher": None, "lower": None, "alt": ot not in ("standard", "promo"),
            "odds_type": ot, "over_available": True, "under_available": under}


PP_ROWS = [_pp("pp:16360820", "SunPayus", "FUT", "Nemiga", 31.5), _pp("pp:16360825", "KaiR0N-", "Nemiga", "FUT", 29.5),
           _pp("pp:777", "demonguy", "Nemiga", "FUT", 40.5, ot="demon", under=False),
           _pp("pp:888", "overonly", "Nemiga", "FUT", 12.5, under=False), _pp("xx9", "noid", "Nemiga", "FUT", 14.5),
           _pp("pp:901", "twin", "Nemiga", "FUT", 18.5), _pp("pp:902", "twin", "Nemiga", "FUT", 18.5),
           _pp("pp:16360899", "promoguy", "FUT", "Nemiga", 22.5, ot="promo"), _pp("pp:16360898", "promoguy", "FUT", "Nemiga", 22.5),
           _pp("pp:555", "solo", "FUT", "Nemiga", 20.5)]
UD_ROWS = [{"id": "u1", "player": "alpha1", "team": "Alpha", "opponent": "Bravo", "start": FUT, "prop": "kills", "scope": "12",
            "line": 27.5, "higher": 1.0, "lower": 1.0, "alt": False},
           {"id": "u2", "player": "bravo1", "team": "Bravo", "opponent": "Alpha", "start": FUT, "prop": "kills", "scope": "12",
            "line": 20.5, "higher": 1.0, "lower": 1.0, "alt": False}]


def _leg(nick, team, opp, line, side="More", book="prizepicks", **kw):
    return dict({"nick": nick, "team": team, "opponent": opp, "line": line, "side": side, "book": book, "prop": "kills",
                 "scope": "12", "line_type": "standard", "start": FUT}, **kw)


SUN, KAI = _leg("SunPayus", "FUT", "Nemiga", 31.5, side="Less"), _leg("KaiR0N-", "Nemiga", "FUT", 29.5)


@pytest.fixture
def feeds(site):
    site.setattr(server.prizepicks, "board", lambda wait=False: {"at": datetime.now(timezone.utc).timestamp(), "error": None,
                                                                   "state": "ok", "rows": [dict(r) for r in PP_ROWS]})
    site.setattr(server.underdog, "board", lambda wait=False: {"at": datetime.now(timezone.utc).timestamp(), "error": None,
                                                                 "rows": [dict(r) for r in UD_ROWS]})
    got = []
    site.setattr(server.slips, "log_loaded", lambda d: got.append(d) or True)
    site.setattr(server, "PP_OPEN_LINK", True)
    return {"logged": got, "site": site}


def _send(legs, book="prizepicks", open_="prizepicks", cid="c"):
    body = {"cid": cid, "book": book, "mode": "member-open", "legs": legs}
    if open_:
        body["open"] = open_
    h = post("/api/slip_log", body=json.dumps(body).encode())
    return h.code, h.body()


def test_server_link_for_the_owner_sample(feeds):
    code, d = _send([SUN, KAI])
    assert code == 200 and d["ok"] is True and d["pp_url"] == SAMPLE and len(feeds["logged"]) == 1
    assert feeds["logged"][0]["mode"] == "member-open"


def test_server_demon_more_and_standard_over_promo(feeds):
    code, d = _send([_leg("demonguy", "Nemiga", "FUT", 40.5, line_type="demon"), _leg("promoguy", "FUT", "Nemiga", 22.5, side="Less")])
    # the demon is More (o); the standard projection (not the promo one at the same line) is the pick
    assert code == 200 and d["pp_url"] == "https://app.prizepicks.com/board?projections=777-o-40.5,16360898-u-22.5"


@pytest.mark.parametrize("side,tok", [("More", "o"), ("Less", "u")])
def test_server_link_keeps_the_members_own_row(feeds, side, tok):
    # review 2026-10-28: a promo row at a standard's line - the member's own row id (the promo) is the link, not the standard
    code, d = _send([_leg("promoguy", "FUT", "Nemiga", 22.5, side=side, row_id="pp:16360899"), KAI])
    assert code == 200 and d["pp_url"] == f"https://app.prizepicks.com/board?projections=16360899-{tok}-22.5,16360825-o-29.5"
    code, d = _send([_leg("promoguy", "FUT", "Nemiga", 22.5, side=side, row_id="pp:16360898"), KAI])
    assert code == 200 and d["pp_url"] == f"https://app.prizepicks.com/board?projections=16360898-{tok}-22.5,16360825-o-29.5"
    # two standard projections at one line: the row id names exactly one of them
    code, d = _send([_leg("twin", "Nemiga", "FUT", 18.5, row_id="pp:902"), SUN])
    assert code == 200 and d["pp_url"] == "https://app.prizepicks.com/board?projections=902-o-18.5,16360820-u-31.5"
    # a row id no longer in the feed (re-posted projection): the old rule - the single standard one
    code, d = _send([_leg("promoguy", "FUT", "Nemiga", 22.5, side=side, row_id="pp:123"), KAI])
    assert code == 200 and d["pp_url"].startswith(f"https://app.prizepicks.com/board?projections=16360898-{tok}-22.5,")
    code, d = _send([_leg("twin", "Nemiga", "FUT", 18.5, row_id="pp:123"), SUN])
    assert code == 409 and "not known" in json.dumps(d)


@pytest.mark.parametrize("case,legs,book,reason", [
    ("moved line", [dict(SUN, line=30.5), KAI], "prizepicks", "SunPayus Under 30.5: Line moved to 31.5"),
    ("started", [dict(SUN, start=PAST), KAI], "prizepicks", "SunPayus Under 31.5: Match started"),
    ("mixed platform", [SUN, _leg("alpha1", "Alpha", "Bravo", 27.5, book="underdog")], "prizepicks", "One platform per slip"),
    ("1 pick", [SUN], "prizepicks", "Entries need at least 2 picks"),
    ("goblin/demon Less", [_leg("demonguy", "Nemiga", "FUT", 40.5, side="Less", line_type="demon"), SUN], "prizepicks", "Side not offered"),
    ("under not offered", [_leg("overonly", "Nemiga", "FUT", 12.5, side="Less"), SUN], "prizepicks", "Side not offered"),
    ("missing id", [_leg("noid", "Nemiga", "FUT", 14.5), SUN], "prizepicks", "PrizePicks' id for a pick on this slip is not known"),
    ("two projections fit", [_leg("twin", "Nemiga", "FUT", 18.5), SUN], "prizepicks", "PrizePicks' id for a pick on this slip is not known"),
    ("one team", [SUN, _leg("solo", "FUT", "Nemiga", 20.5)], "prizepicks", "at least 2 different teams"),
    ("underdog slip asks PrizePicks", [_leg("alpha1", "Alpha", "Bravo", 27.5, book="underdog"), _leg("bravo1", "Bravo", "Alpha", 20.5, book="underdog")],
     "underdog", "Only PrizePicks slips open in PrizePicks"),
])
def test_server_refuses_without_a_link_and_logs_nothing(feeds, case, legs, book, reason):
    code, d = _send(legs, book)
    assert code == 409 and d["ok"] is False and "pp_url" not in d, (case, code, d)
    assert any(reason in t for t in d["legs"]), (case, d["legs"])
    assert feeds["logged"] == []


def test_server_seven_picks_refused(feeds):
    legs = [_leg(f"p{i}", "FUT" if i % 2 else "Nemiga", "Nemiga" if i % 2 else "FUT", 10.5) for i in range(7)]
    code, d = _send(legs)
    assert code == 400 and "pp_url" not in d and feeds["logged"] == []


def test_server_underdog_open_returns_the_home_link_only(feeds):
    ud = [_leg("alpha1", "Alpha", "Bravo", 27.5, book="underdog"), _leg("bravo1", "Bravo", "Alpha", 20.5, book="underdog")]
    code, d = _send(ud, "underdog", "underdog")
    assert code == 200 and d["open_url"] == pplink.UD_HOME and "pp_url" not in d
    code, d = _send([SUN, KAI], "prizepicks", "underdog", cid="c2")
    assert code == 409 and "open_url" not in d


def test_server_flag_off_answer_is_the_copy_answer(feeds):
    feeds["site"].setattr(server, "PP_OPEN_LINK", False)
    code, d = _send([SUN, KAI])
    assert code == 200 and d == {"ok": True, "duplicate": False}            # "open" ignored: exactly the copy answer
    code, d = _send([_leg("noid", "Nemiga", "FUT", 14.5), SUN], cid="c3")
    assert code == 200 and d == {"ok": True, "duplicate": False}            # a copy never needs the id


def test_server_copy_answer_unchanged_with_flag_on(feeds):
    code, d = _send([SUN, KAI], open_=None)
    assert code == 200 and d == {"ok": True, "duplicate": False}


def test_auth_me_flag(site):
    site.setattr(server, "PP_OPEN_LINK", False)
    h = H("/auth/me"); h.do_GET()
    assert "pp_open_link" not in h.body()
    site.setattr(server, "PP_OPEN_LINK", True)
    h = H("/auth/me"); h.do_GET()
    assert h.body()["pp_open_link"] is True


# ================================================================ Slip Studio
def test_studio_open_link():
    feed = {"rows": [dict(r) for r in PP_ROWS]}
    rec = {"book": "prizepicks", "legs": [{"row_id": "pp:16360820", "side": "Less", "line": 31.5, "book": "prizepicks", "market_type": "standard"},
                                          {"row_id": "pp:16360825", "side": "More", "line": 29.5, "book": "prizepicks", "market_type": "standard"}]}
    assert server.studio_open_link(rec, feed) == {"pp_url": SAMPLE}
    gone = dict(rec, legs=[dict(rec["legs"][0], row_id="pp:1"), rec["legs"][1]])           # re-posted under a new id: no link
    assert "pp_reason" in server.studio_open_link(gone, feed) and "pp_url" not in server.studio_open_link(gone, feed)
    gob = dict(rec, legs=[{"row_id": "pp:777", "side": "Less", "line": 40.5, "book": "prizepicks", "market_type": "demon"}, rec["legs"][1]])
    assert server.studio_open_link(gob, feed) == {"pp_reason": "Goblin and demon picks are More only on PrizePicks"}
    assert server.studio_open_link({"book": "underdog", "legs": []}, feed) == {"open_url": pplink.UD_HOME}


# ================================================================ LoL member board carries the projection id (flag on only)
def _lol_board(tmp_path):
    base = {"market_type": "single", "competition": "L", "best_of": 3, "series_start": "2099-01-01T18:00:00Z", "stat": "kills",
            "scope": "Maps 1-2", "odds_type": "standard", "line": 4.5, "sides": {"over": {"status": "open"}, "under": {"status": "open"}},
            "quote_seen_at": "2026-10-19T10:00:00Z", "link": {"status": "matched", "sid": "lol:series:1"},
            "forecast": {"id": 1, "at": "2026-10-19T10:01:00Z", "line": 4.5, "projection": 5.0, "p_over": 0.6, "p_under": 0.4,
                         "p_push": 0, "model_side": "over", "current": True, "grade": "B", "inputs": {}}}
    rows = [dict(base, platform="prizepicks", market_key="pp:a", line_id="9001", player="P1", team="T1", opponent="T2"),
            dict(base, platform="prizepicks", market_key="pp:b", line_id="not-a-number", player="P2", team="T2", opponent="T1"),
            dict(base, platform="underdog", market_key="ud:c", line_id="ud-uuid", player="P3", team="T1", opponent="T2")]
    (tmp_path / "board.json").write_text(json.dumps({"at": "2026-10-19T10:02:00Z", "rows": rows, "match_tables": []}))


@pytest.mark.parametrize("on", [False, True])
def test_lol_member_rows_projection_id(tmp_path, monkeypatch, on):
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    monkeypatch.setattr(server, "PP_OPEN_LINK", on)
    _lol_board(tmp_path)
    server._LOL_MEMBER.update(mtime=None, body=None)
    rows = {r["player"]: r for r in json.loads(server.lol_member_board())["rows"]}
    if not on:
        assert not any("pp_projection_id" in r for r in rows.values())
    else:
        assert rows["P1"]["pp_projection_id"] == "9001" and rows["P2"]["pp_projection_id"] is None
        assert "pp_projection_id" not in rows["P3"]
    server._LOL_MEMBER.update(mtime=None, body=None)


# ================================================================ pages (node, stub DOM)
def _base_page(tmp_path, name):
    try:
        src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:web/{name}"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except Exception:
        src = None
    if src is None or src.returncode != 0:
        if os.environ.get("CI"):                        # CI checks out full history: a missing base is a failure, never a skip
            pytest.fail(f"base commit {BASE} not available in CI (checkout needs fetch-depth: 0)")
        pytest.skip("base commit not available")
    p = tmp_path / ("base_" + name)
    p.write_text(src.stdout, encoding="utf-8")
    return p


def run_file(tmp_path, path, body, data=None):
    # Fixtures below are built at collection. A long full-suite run must not age their
    # quotes past the real 15-minute protection before these tests even start.
    clock = int(REAL.timestamp() * 1000)
    harness = (f"class FixtureDate extends Date {{ constructor(...a) {{ super(...(a.length ? a : [{clock}])); }} "
               f"static now() {{ return {clock}; }} }}\n" +
               H2.replace("URL, URLSearchParams, Date,", "URL, URLSearchParams, Date: FixtureDate,"))
    (tmp_path / "h2.js").write_text(harness, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps(data or {}), encoding="utf-8")
    (tmp_path / "body.js").write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "h2.js"), str(path), str(tmp_path / "in.json"), str(tmp_path / "body.js")],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


CUR = lambda name: ROOT / "web" / name


def _cs2_leg(p, line, side, bk="prizepicks", status="verified", tm="FUT", lt="standard"):
    return {"p": p, "t": "kills", "sc": "12", "l": line, "s": side, "bk": bk, "lt": lt, "st": FUT, "tm": tm, "vs": "X", "rid": "pp:1",
            "g": "B", "pc": 0.6, "u": 1, "now": {"status": status, "at": 1}}


PP2 = [_cs2_leg("SunPayus", 31.5, "Less"), _cs2_leg("KaiR0N-", 29.5, "More", tm="Nemiga")]
UD2 = [_cs2_leg("alpha1", 27.5, "More", bk="underdog", tm="Alpha"), _cs2_leg("bravo1", 20.5, "More", bk="underdog", tm="Bravo")]
SLIPS = {"pp": PP2, "ud": UD2, "mixed": [PP2[0], UD2[1]], "unverified": [PP2[0], dict(PP2[1], now={"status": "changed"})],
         "one": PP2[:1], "seven": [dict(PP2[i % 2], p=f"x{i}") for i in range(7)], "empty": [],
         "noid": [PP2[0], dict(PP2[1], rid="xx9")], "blankid": [PP2[0], dict(PP2[1], rid="")]}
STUDIO = {"slip_id": "b1/s1", "book": "prizepicks", "n": 2, "status": "live", "legs": [
    {"row_id": "pp:16360820", "nick": "SunPayus", "side": "Less", "line": 31.5, "prop": "kills", "scope": "12", "grade": "B", "p": 0.6,
     "team": "FUT", "opponent": "Nemiga", "start": FUT, "book": "prizepicks", "market_type": "standard"},
    {"row_id": "pp:16360825", "nick": "KaiR0N-", "side": "More", "line": 29.5, "prop": "kills", "scope": "12", "grade": "B", "p": 0.6,
     "team": "Nemiga", "opponent": "FUT", "start": FUT, "book": "prizepicks", "market_type": "standard"}]}
DRAW = """(() => { const o = {};
  for (const [k, s] of Object.entries(INPUT.slips)) o[k] = s.length ? slipHtml(s) : "";
  o.studio = studioSlip(INPUT.studio); o.studio_ud = studioSlip(Object.assign({}, INPUT.studio, {book: "underdog"}));
  return o; })()"""


@needs_node
def test_cs2_flag_off_draws_exactly_the_base_page(tmp_path):
    data = {"slips": SLIPS, "studio": STUDIO}
    now = run_file(tmp_path, CUR("index.html"), DRAW, data)
    base = run_file(tmp_path, _base_page(tmp_path, "index.html"), DRAW, data)
    assert now == base and "Open in PrizePicks" not in json.dumps(now) and "Open Underdog" not in json.dumps(now)


@needs_node
def test_cs2_buttons_only_for_checked_single_platform_slips(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), "PPL.on = true;\n" + DRAW, {"slips": SLIPS, "studio": STUDIO})
    assert out["pp"].count('data-slipopen="prizepicks">Open in PrizePicks</button>') == 1
    assert 'data-slipopen="underdog">Open Underdog</button>' in out["ud"] and "Open in PrizePicks" not in out["ud"]
    for k in ("mixed", "unverified", "one", "seven", "noid", "blankid"):   # noid/blankid: a projection id not known
        assert "data-slipopen" not in out[k], k
    assert 'data-studioopen="b1/s1">Open in PrizePicks</button>' in out["studio"]
    assert 'data-studioopen="b1/s1">Open Underdog</button>' in out["studio_ud"]
    assert "0U" not in out["pp"].split("data-slipopen")[1][:80]


OPEN = """(async () => {
  const EV = [], A = [], OPENED = [], BODIES = [];
  PPL.on = !INPUT.off;
  toast = () => {}; ask = async (t, body) => { A.push([t, body]); return "review"; };
  copy = async t => { EV.push("copy"); };
  window.open = u => { OPENED.push(u); return {}; };
  const SL = INPUT.sl;
  if (INPUT.timeout) setTimeout = (f) => { Promise.resolve().then(f); return 1; };
  fetch = async (u, o) => {
    u = String(u);
    if (u.startsWith("/api/line_check")) return {ok: true, status: 200, json: async () => ({status: "verified", note: null, rows: []})};
    if (u === "/api/slip_log") { EV.push("check"); BODIES.push(JSON.parse(o.body));
      if (SL === "hang") return new Promise(() => {});
      if (SL === "throw") throw new Error("offline");
      return {ok: SL.status === 200, status: SL.status, json: async () => SL.body}; }
    return {ok: false, status: 599, json: async () => ({})};
  };
  localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.legs));
  slipClick({target: {closest: s => s === "[data-slipopen]" ? {dataset: {slipopen: INPUT.act}} : null}});
  for (let i = 0; i < 400; i++) await Promise.resolve();
  return {EV, A, OPENED, open: BODIES.map(b => b.open || null), modes: BODIES.map(b => b.mode)};
})()"""
OK_PP = {"status": 200, "body": {"ok": True, "duplicate": False, "pp_url": SAMPLE}}


@needs_node
def test_cs2_open_opens_only_after_the_server_ok(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": PP2, "act": "prizepicks", "sl": OK_PP})
    assert out["EV"] == ["check"] and out["OPENED"] == [SAMPLE] and out["A"] == []          # no clipboard write for PrizePicks
    assert out["open"] == ["prizepicks"] and out["modes"] == ["member-open"]


@needs_node
@pytest.mark.parametrize("case,sl,reason", [
    ("refused (moved line)", {"status": 409, "body": {"ok": False, "legs": ["SunPayus Under 31.5: Line moved to 32.5"]}}, "Line moved to 32.5"),
    ("refused (no id)", {"status": 409, "body": {"ok": False, "legs": ["PrizePicks' id for a pick on this slip is not known - copy the slip instead"]}}, "not known"),
    ("server 5xx", {"status": 502, "body": {}}, "Could not check the lines right now"),
    ("network failure", "throw", "Could not check the lines right now"),
    ("ok without a link", {"status": 200, "body": {"ok": True}}, "Could not check the lines right now"),
    ("ok with a foreign link", {"status": 200, "body": {"ok": True, "pp_url": "https://evil.example/board?projections=1-o-2,3-o-4"}}, "Could not check"),
    ("ok with a mangled link", {"status": 200, "body": {"ok": True, "pp_url": "https://app.prizepicks.com/board?projections=1-x-2,3-o-4&invite_code=z"}}, "Could not check"),
])
def test_cs2_open_never_opens_on_refusal_or_failure(tmp_path, case, sl, reason):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": PP2, "act": "prizepicks", "sl": sl})
    assert out["OPENED"] == [] and "copy" not in out["EV"], (case, out)
    assert out["A"] and out["A"][0][0] == "Not opened" and reason in out["A"][0][1], (case, out)


@needs_node
def test_cs2_open_timeout_opens_nothing(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": PP2, "act": "prizepicks", "sl": "hang", "timeout": True})
    assert out["OPENED"] == [] and out["A"][0][0] == "Not opened"


@needs_node
def test_cs2_open_refused_before_the_server_for_a_mixed_or_wrong_slip(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": UD2, "act": "prizepicks", "sl": OK_PP})
    assert out["EV"] == [] and out["OPENED"] == [] and "Only PrizePicks slips open PrizePicks" in out["A"][0][1]
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": PP2[:1], "act": "prizepicks", "sl": OK_PP})
    assert out["EV"] == [] and out["OPENED"] == [] and out["A"][0][0] == "Add another pick"


@needs_node
def test_cs2_flag_off_open_click_does_nothing(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": PP2, "act": "prizepicks", "sl": OK_PP, "off": True})
    assert out == {"EV": [], "A": [], "OPENED": [], "open": [], "modes": []}


@needs_node
def test_cs2_open_underdog_copies_then_opens_home(tmp_path):
    out = run_file(tmp_path, CUR("index.html"), OPEN, {"legs": UD2, "act": "underdog",
                                                       "sl": {"status": 200, "body": {"ok": True, "open_url": pplink.UD_HOME}}})
    assert out["EV"] == ["check", "copy"] and out["OPENED"] == [pplink.UD_HOME]


@needs_node
def test_cs2_open_blocked_popup_offers_a_real_link(tmp_path):
    body = OPEN.replace("window.open = u => { OPENED.push(u); return {}; };", "window.open = u => { OPENED.push(u); return null; };")
    out = run_file(tmp_path, CUR("index.html"), body, {"legs": PP2, "act": "prizepicks", "sl": OK_PP})
    assert out["A"] and out["A"][0][0] == "Open in PrizePicks" and f'href="{SAMPLE}"' in out["A"][0][1] and 'rel="noopener noreferrer"' in out["A"][0][1]


STUDIO_OPEN = """(async () => {
  const OPENED = [], T = [], C = [], EVK = [];
  PPL.on = true; toast = m => T.push(m); copy = async t => C.push(t);
  window.open = u => { OPENED.push(u); return {}; };
  studioCall = async (m, u, b) => { if (u === "/api/slip_studio/event") EVK.push(b.kind);
    return u === "/api/slip_studio/verify" ? {status: INPUT.status || 200, d: INPUT.d} : {status: 200, d: {}}; };
  studioBuild = async () => { T.push("rebuild"); };
  const btn = {textContent: "Open in PrizePicks", disabled: false};
  await studioVerify("b1/s1", btn, INPUT.book || "prizepicks");
  for (let i = 0; i < 50; i++) await Promise.resolve();
  STUDIO.batch = {slips: {2: [Object.assign({}, INPUT.slip || {}, {slip_id: "b1/s1"})]}};
  let card = ""; try { card = INPUT.slip ? studioSlip(Object.assign({}, INPUT.slip, {slip_id: "b1/s1"})) : ""; } catch (e) { card = "ERR " + e; }
  return {OPENED, T, C, EVK, label: btn.textContent, notes: STUDIO.notes, opened: Object.keys(STUDIO.opened || {}), card};
})()"""


@needs_node
def test_studio_open_after_verify_ok_only(tmp_path):
    ok = {"ok": True, "book": "prizepicks", "batch_id": "b1", "copy_text": "x", "pp_url": SAMPLE}
    out = run_file(tmp_path, CUR("index.html"), STUDIO_OPEN, {"d": ok, "slip": STUDIO})
    assert out["OPENED"] == [SAMPLE] and out["C"] == []
    # review 2026-10-28: a PrizePicks open is recorded ('load' event) and the card says so, like every other way out
    assert out["EVK"] == ["load"] and out["opened"] == ["b1/s1"] and "opened in PrizePicks" in out["card"] and "0U" not in out["card"]
    out = run_file(tmp_path, CUR("index.html"), STUDIO_OPEN, {"d": {"ok": False, "book": "prizepicks", "changes": ["SunPayus kills 31.5 Under: line moved"]}})
    assert out["OPENED"] == [] and "rebuild" in out["T"] and out["notes"]["b1/s1"] == ["SunPayus kills 31.5 Under: line moved"]
    out = run_file(tmp_path, CUR("index.html"), STUDIO_OPEN, {"d": dict(ok, pp_url=None, pp_reason="Goblin and demon picks are More only on PrizePicks")})
    assert out["OPENED"] == [] and any("More only" in t for t in out["T"]) and out["label"] == "Open in PrizePicks"
    out = run_file(tmp_path, CUR("index.html"), STUDIO_OPEN, {"d": ok, "status": 500})
    assert out["OPENED"] == [] and out["label"] == "Open in PrizePicks"
    out = run_file(tmp_path, CUR("index.html"), STUDIO_OPEN, {"d": {"ok": True, "book": "underdog", "batch_id": "b1", "copy_text": "slip text",
                                                                    "open_url": pplink.UD_HOME}, "book": "underdog"})
    assert out["OPENED"] == [pplink.UD_HOME] and out["C"] == ["slip text"] and out["EVK"] == ["copy"] and out["opened"] == []


# ---------------------------------------------------------------- LoL page
def _lrow(key, player, team, pid, platform="prizepicks", line=4.5, lt="Standard", sides=(True, True)):
    r = _lol_row(key, player, "2099-01-01T18:00:00Z")
    r.update(platform=platform, platform_label="PrizePicks" if platform == "prizepicks" else "Underdog", team=team, match=f"{team} vs Z",
             line=line, line_type=lt, line_checked=iso(REAL),
             sides=[{"side": "Over", "offered": sides[0]}, {"side": "Under", "offered": sides[1]}])
    if pid is not None:
        r["pp_projection_id"] = pid
    return r


LROWS = [_lrow("k1", "Faker", "T1", "9001"), _lrow("k2", "Chovy", "GEN", "9002", line=3.0), _lrow("k3", "NoId", "HLE", None),
         _lrow("k4", "Demon", "DK", "9004", lt="Demon", sides=(True, False)), _lrow("u1", "Ud1", "T1", None, platform="underdog"),
         _lrow("u2", "Ud2", "GEN", None, platform="underdog"), _lrow("k5", "Mate", "T1", "9005")]
LDRAW = """(() => { D = INPUT.d; F.view = "slip"; const o = {};
  for (const [k, keys] of Object.entries(INPUT.slips)) { SLIP.legs = keys.map(x => legOf(D.rows.find(r => r.key === x))); render(); o[k] = $("#app").innerHTML; }
  return o; })()"""
LSLIPS = {"pp": ["k1", "k2"], "noid": ["k1", "k3"], "one": ["k1"], "ud": ["u1", "u2"]}


@needs_node
def test_link_fixture_clock_preserves_real_stale_quote_protection(tmp_path):
    data = {"fresh": LROWS[0], "stale": dict(LROWS[0], line_checked=iso(REAL - timedelta(minutes=16)))}
    out = run_file(tmp_path, CUR("lol.html"),
                   "({now: Date.now(), fresh: quoteIssue(INPUT.fresh), stale: quoteIssue(INPUT.stale)})", data)
    assert out["now"] == int(REAL.timestamp() * 1000)
    assert out["fresh"] is None
    assert "15 minutes" in out["stale"]


@needs_node
def test_lol_flag_off_draws_exactly_the_base_page(tmp_path):
    data = {"d": {"rows": LROWS, "match_tables": [], "slips": {}, "stake_note": "n"}, "slips": LSLIPS}
    now = run_file(tmp_path, CUR("lol.html"), LDRAW, data)
    base = run_file(tmp_path, _base_page(tmp_path, "lol.html"), LDRAW, data)
    from tests.test_qol_lol_page import qol_1107_norm      # the 2026-11-07 QoL Slip-tab changes, folded out of both sides
    now, base = ({k: qol_1107_norm(v) for k, v in x.items()} for x in (now, base))
    assert now == base and "openslip" not in json.dumps(now)


@needs_node
def test_lol_buttons(tmp_path):
    data = {"d": {"rows": LROWS, "match_tables": [], "slips": {}, "stake_note": "n"}, "slips": LSLIPS}
    out = run_file(tmp_path, CUR("lol.html"), "PPL.on = true;\n" + LDRAW, data)
    assert 'data-open="prizepicks">Open in PrizePicks</button>' in out["pp"]
    assert 'data-open="underdog">Open Underdog</button>' in out["ud"]
    assert "openslip" not in out["noid"] and "openslip" not in out["one"]


LOPEN = """(async () => {
  const OPENED = [], C = [];
  PPL.on = true; D = INPUT.d; F.view = "slip";
  window.open = u => { OPENED.push(u); return {}; };
  navigator.clipboard.writeText = async t => { C.push(t); };
  fetch = async u => (INPUT.down ? {ok: false, status: 503} : {ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(INPUT.fresh))});
  SLIP.legs = INPUT.keys.map(x => legOf(D.rows.find(r => r.key === x)));
  const r = await openSlip(INPUT.pl || "prizepicks");
  return {r, OPENED, C, msg: $("#slipmsg").textContent};
})()"""


def _lopen(tmp_path, keys, fresh=None, **kw):
    d = {"rows": LROWS, "match_tables": [], "slips": {}, "stake_note": "n"}
    return run_file(tmp_path, CUR("lol.html"), LOPEN, dict({"d": d, "fresh": fresh or d, "keys": keys}, **kw))


@needs_node
def test_lol_open_exact_link_after_the_fresh_recheck(tmp_path):
    out = _lopen(tmp_path, ["k1", "k2"])
    assert out["OPENED"] == ["https://app.prizepicks.com/board?projections=9001-o-4.5,9002-o-3"] and out["r"] == out["OPENED"][0]
    # a projection id that changed under the same line is followed: the FRESH row's id, never the one seen at add time
    fresh = {"rows": [dict(LROWS[0], pp_projection_id="9101")] + LROWS[1:], "match_tables": [], "slips": {}, "stake_note": "n"}
    assert _lopen(tmp_path, ["k1", "k2"], fresh)["OPENED"] == ["https://app.prizepicks.com/board?projections=9101-o-4.5,9002-o-3"]


@needs_node
@pytest.mark.parametrize("case,keys,fresh_rows,why", [
    ("moved line", ["k1", "k2"], [dict(LROWS[0], line=5.5)] + LROWS[1:], "The line moved"),
    ("pulled", ["k1", "k2"], LROWS[1:], "no longer on the board"),
    ("started", ["k1", "k2"], [dict(LROWS[0], start="2020-01-01T18:00:00Z")] + LROWS[1:], "has started"),
    ("missing id", ["k1", "k3"], LROWS, "is not known"),
    ("one pick", ["k1"], LROWS, "2-6 picks"),
    ("one team", ["k1", "k5"], LROWS, "2 different teams"),
    ("id removed on the fresh board", ["k1", "k2"], [dict(LROWS[0], pp_projection_id=None)] + LROWS[1:], "is not known"),
])
def test_lol_open_refusals_open_nothing(tmp_path, case, keys, fresh_rows, why):
    fresh = {"rows": fresh_rows, "match_tables": [], "slips": {}, "stake_note": "n"}
    out = _lopen(tmp_path, keys, fresh)
    assert out["OPENED"] == [] and out["r"] is False and why in out["msg"] and "Nothing opened" in out["msg"], (case, out)


@needs_node
def test_lol_open_board_down_opens_nothing(tmp_path):
    out = _lopen(tmp_path, ["k1", "k2"], down=True)
    assert out["OPENED"] == [] and "nothing opened" in out["msg"]


@needs_node
def test_lol_builder_never_builds_a_demon_under(tmp_path):
    out = run_file(tmp_path, CUR("lol.html"), """(() => {
      const d = INPUT.rows, legs = [{key: "k4", platform: "prizepicks", side: "Under", team: "DK", player: "Demon"}, {key: "k1", platform: "prizepicks", side: "Over", team: "T1", player: "Faker"}];
      const off = ppLink(legs, d);
      d.find(r => r.key === "k4").sides[1].offered = true;      // even a row that says Under is offered
      return {off, demon: ppLink(legs, d), over: ppLink([Object.assign({}, legs[0], {side: "Over"}), legs[1]], d)};
    })()""", {"rows": LROWS})
    assert out["off"]["why"] and out["demon"]["why"] == "Goblin and demon picks are More only on PrizePicks."
    assert out["over"]["url"] == "https://app.prizepicks.com/board?projections=9004-o-4.5,9001-o-4.5"


@needs_node
def test_lol_open_underdog_copies_and_opens_home(tmp_path):
    out = _lopen(tmp_path, ["u1", "u2"], pl="underdog")
    assert out["OPENED"] == [pplink.UD_HOME] and len(out["C"]) == 1 and "Ud1" in out["C"][0]
    assert "picks copied, Underdog opened" in out["msg"]


@needs_node
def test_lol_open_underdog_copy_failure_is_said(tmp_path):
    # review 2026-10-28: a blocked clipboard never reads 'picks copied'
    body = LOPEN.replace("navigator.clipboard.writeText = async t => { C.push(t); };",
                         "navigator.clipboard.writeText = async t => { throw new Error('denied'); }; document.execCommand = () => false;")
    d = {"rows": LROWS, "match_tables": [], "slips": {}, "stake_note": "n"}
    out = run_file(tmp_path, CUR("lol.html"), body, {"d": d, "fresh": d, "keys": ["u1", "u2"], "pl": "underdog"})
    assert out["OPENED"] == [pplink.UD_HOME] and "copied," not in out["msg"] and "Copy failed" in out["msg"], out


# ---------------------------------------------------------------- Sports page (closure-private code: static wiring)
def test_sports_page_wiring():
    js = (ROOT / "web" / "sports" / "slips.js").read_text(encoding="utf-8")
    assert 'auth.pp_open_link === true' in js and 'r = await check("copy")' in js
    assert "if (!r || !r.ok) return;" in js.split("async function openSlip")[1].split("}")[0] + "}"
    assert 'id="ss-open"' not in js                     # never in the template: inserted only when the flag is on
    op = js.split("async function openSlip")[1].split("async function postApi")[0]
    # review 2026-10-28: 'Picks copied' only after writeText resolved; a rejected write says the clipboard was blocked
    assert "copied = true; } catch (e) { copied = false; }" in op and 'var udMsg = copied ? "Picks copied' in op
    assert "The browser blocked the clipboard" in op and op.count("Picks copied") == 1
