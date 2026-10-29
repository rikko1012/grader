"""Underdog line links (PP_OPEN_LINK, 2026-10-29): Underdog's own OneLink opens ONE line's market screen in the Underdog app
(owner-verified on a phone 2026-10-29: XANTARES kills, over_under_id cc625c31-...); on a PC it lands on the match's
Underdog board (af_web_dp). No pre-fill: the member taps Higher / Lower. CS2 cards / board rows / manual slips / Slip
Studio, LoL cards + manual slip, owner tail links on Studio slips. Default OFF: with the flag off every answer and every
drawn page is exactly main's. The XANTARES ids are Underdog's public board ids (the owner's test line); every other row,
name and id is a TEST FIXTURE."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import pplink
import server
import underdog
import ud_tails
from tests.test_audit13_site import H, post, site  # noqa: F401 - site is a fixture
from tests.test_audit13_site_ui import NODE, ROOT
from tests.test_audit_h_web import H2
from tests.test_pplink import (UD_ROWS, _leg, _send, feeds, run_file, CUR, STUDIO, SLIPS, DRAW, LDRAW, LROWS, LSLIPS, _lrow,  # noqa: F401
                               _cs2_leg, OPEN, STUDIO_OPEN)
from tests.test_slip_builder import private_db  # noqa: F401 - private_db is an autouse fixture

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
MAIN = "034e25f"                         # origin/main this branched from (PR #32 merged): flag off = exactly these pages
REAL = datetime.now(timezone.utc)
iso = lambda t: t.isoformat(timespec="seconds")
FUT, PAST = iso(REAL + timedelta(hours=2)), iso(REAL - timedelta(minutes=1))

# the owner's verified line (Underdog's public board, 2026-10-29): XANTARES 28.5 kills Maps 1-2, Aurora vs MOUZ
XAN_LINE, XAN_OU, XAN_MATCH = "11bcbaf8-1d2f-46d0-888c-7b74d529dca6", "cc625c31-9790-4a65-b814-766222f427d2", 277401
XAN_APP = "https://underdogfantasy.onelink.me/vgwg?af_force_deeplink=true&af_dp=underdogfantasy%3A%2F%2Fmarket-details%2Fcc625c31-9790-4a65-b814-766222f427d2"
XAN_FULL = XAN_APP + ("&af_web_dp=https%3A%2F%2Fapp.underdogsports.com%2Fpick-em%2Fhigher-lower%2Fall%2Ffeatured%3Fmatch_id%3D277401"
                      "%26match_type%3DGame")
OU2 = "0f6a3b2e-1111-4c2d-9e8f-123456789abc"                   # fixture
LINK2 = pplink.ud_link(OU2)
SHARE = "5b1f0c7e-2222-4a3b-8c9d-abcdefabcdef"                 # fixture share id
TAIL = f"https://underdogfantasy.onelink.me/vgwg?deep_link_value=entry_slip&deep_link_sub1={SHARE}"


# ================================================================ the link (pure)
def test_ud_link_exact_strings():
    assert pplink.ud_link(XAN_OU) == XAN_APP
    assert pplink.ud_link(XAN_OU, XAN_MATCH, "Game") == XAN_FULL
    assert pplink.ud_link(XAN_OU, "277401", "Game") == XAN_FULL
    assert pplink.ud_link(XAN_OU.upper(), XAN_MATCH, "Game") == XAN_FULL          # one canonical spelling
    assert pplink.ud_web_link(XAN_MATCH, "Game") == "https://app.underdogsports.com/pick-em/higher-lower/all/featured?match_id=277401&match_type=Game"
    # an unknown match / type only drops the PC landing, never the app link
    for mid, mt in ((None, None), (XAN_MATCH, None), (XAN_MATCH, "Season"), ("27a", "Game"), (True, "Game"), (-1, "Game"), (2.5, "Game")):
        assert pplink.ud_link(XAN_OU, mid, mt) == XAN_APP, (mid, mt)
    for bad in (None, "", "cc625c31", "pp:16360820", "16360820", XAN_OU + "x", " " * 36, 12345, "cc625c31-9790-4a65-b814-766222f427dg"):
        assert pplink.ud_link(bad, XAN_MATCH, "Game") is None, bad
    assert "pre" not in XAN_FULL and "entry" not in XAN_FULL


def test_ud_link_ok_is_exactly_the_built_shape():
    assert pplink.ud_link_ok(XAN_APP) and pplink.ud_link_ok(XAN_FULL) and pplink.ud_link_ok(LINK2)
    for bad in (None, "", pplink.UD_HOME, XAN_APP + "&x=1", XAN_APP.replace("vgwg", "abcd"), XAN_APP.replace("https", "http"),
                XAN_FULL.replace("Game", "Evil"), XAN_FULL.replace("app.underdogsports.com", "evil.example"),
                XAN_APP.replace("market-details", "entry"), "javascript:alert(1)", XAN_APP + "\n"):
        assert not pplink.ud_link_ok(bad), bad


def test_ids_of_reads_the_market_id_not_the_line_id():
    body = {"appearances": [{"id": "a1", "match_id": XAN_MATCH, "match_type": "Game", "player_id": "p1"}],
            "over_under_lines": [{"id": XAN_LINE, "over_under_id": XAN_OU, "over_under": {"id": XAN_OU, "appearance_stat": {"appearance_id": "a1"}},
                                  "options": [{"id": "opt-1", "over_under_line_id": XAN_LINE}]},
                                 {"id": "L2", "over_under": {"id": OU2, "appearance_stat": {"appearance_id": "nope"}}}]}
    ids = underdog.ids_of(body)
    assert ids == {XAN_LINE: (XAN_OU, XAN_MATCH, "Game"), "L2": (OU2, None, None)}
    assert pplink.ud_link(*ids[XAN_LINE]) == XAN_FULL and pplink.ud_link(*ids["L2"]) == LINK2


def test_board_rows_unchanged_by_the_ids(monkeypatch):
    # the ids live beside the board (underdog._IDS): parse() rows are exactly what they were
    import inspect
    assert "over_under_id" not in inspect.getsource(underdog.parse)
    monkeypatch.setattr(underdog, "_IDS", {XAN_LINE: (XAN_OU, XAN_MATCH, "Game")})
    assert underdog.ud_ids(XAN_LINE) == (XAN_OU, XAN_MATCH, "Game") and underdog.ud_ids("x") is None and underdog.ud_ids(None) is None
    assert server.ud_row_link(XAN_LINE) == XAN_FULL and server.ud_row_link("x") is None


# ================================================================ owner tail links: the strict share-link rule
@pytest.mark.parametrize("url,want", [
    (TAIL, TAIL), (f"https://underdogfantasy.onelink.me/vgwg?af_force_deeplink=true&deep_link_value=entry_slip&deep_link_sub1={SHARE}", TAIL),
    (f"https://underdogfantasy.onelink.me/vgwg?deep_link_value=entry_slip&deep_link_sub1={SHARE}&pid=share&c=x&af_xp=social", TAIL),
    (f"https://app.underdogsports.com/pick-em/higher-lower/all/home?entrySlipShareId={SHARE}",) * 2,
    (f"https://play.underdogsports.com/pick-em/share/{SHARE}",) * 2, (f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}",) * 2,
    (f"https://play.underdogsports.com/x?deep_link_value=entry_slip&deep_link_sub1={SHARE.upper()}",
     f"https://play.underdogsports.com/x?deep_link_value=entry_slip&deep_link_sub1={SHARE}"),
    (f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}&utm_source=y", f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}"),
])
def test_tail_link_accepted(url, want):
    # 2026-10-29 review: the stored link is REBUILT from the checked pieces - never the pasted text
    canon, sid = pplink.tail_share_id(url)
    assert canon == want and sid == SHARE
    assert pplink.tail_share_id(canon) == (canon, SHARE)       # canonical is a fixed point (the serve-time re-check)


# 2026-10-29 review: redirect overrides on the ALLOWED hosts (AppsFlyer af_r / af_web_dp / af_dp, to= / redirect_uri= ...)
TAIL_REDIRECTS = [
    f"{TAIL}&af_r=https://evil.example", f"{TAIL}&af_web_dp=https%3A%2F%2Fevil.example%2Fphish", f"{TAIL}&af_dp=evilapp%3A%2F%2Fx",
    f"{TAIL}&af_android_url=https%3A%2F%2Fevil.example", f"{TAIL}&af_ios_url=https%3A%2F%2Fevil.example",
    f"https://play.underdogsports.com/redirect?to=https://evil.example&entrySlipShareId={SHARE}",
    f"https://play.underdogsports.com/redirect?url=https%3A%2F%2Fevil.example&entrySlipShareId={SHARE}",
    f"https://app.underdogsports.com/?entrySlipShareId={SHARE}&redirect_uri=https://evil.example",
    f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}&redirect=https://evil.com",
    f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}&q=https://evil.com",
    f"https://play.underdogsports.com/a.b/{SHARE}", f"https://play.underdogsports.com/a%2F%2Fevil.example/{SHARE}",
]


@pytest.mark.parametrize("url", TAIL_REDIRECTS)
def test_tail_link_redirect_params_refused(url):
    canon, why = pplink.tail_share_id(url)
    assert canon is None and why, url


@pytest.mark.parametrize("url", [
    None, "", "x", f"http://play.underdogsports.com/share/{SHARE}", f"https://evil.example/share/{SHARE}",
    f"https://play.underdogsports.com.evil.example/share/{SHARE}", f"https://user@play.underdogsports.com/share/{SHARE}",
    f"https://play.underdogsports.com:444/share/{SHARE}", f"https://play.underdogsports.com/share/{SHARE}#x",
    "https://play.underdogsports.com/share/not-a-uuid", f"https://underdogfantasy.onelink.me/vgwg?deep_link_value=market&deep_link_sub1={SHARE}",
    f"https://underdogfantasy.onelink.me/vgwg?entrySlipShareId={SHARE}", f"https://underdogfantasy.onelink.me/a/b?deep_link_value=entry_slip&deep_link_sub1={SHARE}",
    f"https://app.underdogsports.com/share/{SHARE}",                       # app host: only the entrySlipShareId form
    f"https://play.underdogsports.com/x?entrySlipShareId={SHARE}&entrySlipShareId={OU2}",
    f"https://play.underdogsports.com/share/{SHARE}\"><script>", "https://play.underdogsports.com/" + "a" * 700 + SHARE,
    f"javascript://play.underdogsports.com/{SHARE}",
])
def test_tail_link_refused(url):
    canon, why = pplink.tail_share_id(url)
    assert canon is None and why, url


# ================================================================ server: CS2 slip check gives each leg its line link
UDL = {"u1": (XAN_OU, XAN_MATCH, "Game"), "u2": (OU2, None, None)}
UD2L = [_leg("alpha1", "Alpha", "Bravo", 27.5, book="underdog", row_id="u1"), _leg("bravo1", "Bravo", "Alpha", 20.5, book="underdog", row_id="u2")]


@pytest.fixture
def udfeeds(feeds):
    feeds["site"].setattr(underdog, "_IDS", dict(UDL))
    return feeds


def test_server_underdog_copy_answer_carries_each_legs_link(udfeeds):
    code, d = _send(UD2L, "underdog", None)
    assert code == 200 and d == {"ok": True, "duplicate": False, "legs_open": [XAN_FULL, LINK2]}
    code, d = _send(UD2L, "underdog", "underdog", cid="c2")
    assert code == 200 and d == {"ok": True, "duplicate": False, "open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, LINK2]}


def test_server_leg_without_known_ids_gets_none_and_no_ids_gives_the_old_answer(udfeeds):
    udfeeds["site"].setattr(underdog, "_IDS", {"u1": (XAN_OU, XAN_MATCH, "Game")})
    code, d = _send(UD2L, "underdog", None)
    assert code == 200 and d["legs_open"] == [XAN_FULL, None]
    udfeeds["site"].setattr(underdog, "_IDS", {})
    code, d = _send(UD2L, "underdog", "underdog", cid="c3")
    assert code == 200 and d == {"ok": True, "duplicate": False, "open_url": pplink.UD_HOME}      # main's answer


def test_server_refused_underdog_check_has_no_links(udfeeds):
    code, d = _send([dict(UD2L[0], line=26.5), UD2L[1]], "underdog", None)
    assert code == 409 and "legs_open" not in d and udfeeds["logged"] == []
    code, d = _send([dict(UD2L[0], start=PAST), UD2L[1]], "underdog", "underdog")
    assert code == 409 and "legs_open" not in d and "open_url" not in d


def test_server_flag_off_answers_are_mains(udfeeds):
    udfeeds["site"].setattr(server, "PP_OPEN_LINK", False)
    assert _send(UD2L, "underdog", None) == (200, {"ok": True, "duplicate": False})
    assert _send(UD2L, "underdog", "underdog", cid="c4") == (200, {"ok": True, "duplicate": False})


def test_studio_open_link_underdog_legs():
    feed = {"rows": [dict(r) for r in UD_ROWS]}
    rec = {"book": "underdog", "legs": [{"row_id": "u1", "line": 27.5, "side": "More", "book": "underdog"},
                                        {"row_id": "u2", "line": 20.5, "side": "More", "book": "underdog"}]}
    old = dict(underdog._IDS)
    try:
        underdog._IDS = dict(UDL)
        assert server.studio_open_link(rec, feed) == {"open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, LINK2]}
        moved = dict(rec, legs=[dict(rec["legs"][0], line=26.5), rec["legs"][1]])      # a leg not at its feed line: no link
        assert server.studio_open_link(moved, feed) == {"open_url": pplink.UD_HOME, "legs_open": [None, LINK2]}
        assert server.studio_open_link({"book": "underdog", "legs": []}, feed) == {"open_url": pplink.UD_HOME}
        underdog._IDS = {}
        assert server.studio_open_link(rec, feed) == {"open_url": pplink.UD_HOME}       # main's answer
    finally:
        underdog._IDS = old


def test_board_endpoint_links_only_with_the_flag(site):
    pay = {"book": "underdog", "rows": [{"id": "u1", "book": "underdog", "line": 27.5}, {"id": "zz", "book": "underdog", "line": 1.5}], "total": 2}
    site.setattr(server, "board_payload", lambda book="underdog": json.loads(json.dumps(pay)))
    site.setattr(underdog, "_IDS", dict(UDL))
    site.setattr(server, "PP_OPEN_LINK", False)
    h = H("/api/board"); h.do_GET()
    assert h.wfile.getvalue().split(b"\r\n\r\n")[-1].endswith(json.dumps(pay, default=str).encode()) or h.body() == pay
    assert "ud_app_link" not in json.dumps(h.body())
    site.setattr(server, "_RESULTS", {})
    site.setattr(server, "PP_OPEN_LINK", True)
    h = H("/api/board"); h.do_GET()
    rows = h.body()["rows"]
    assert rows[0]["ud_app_link"] == XAN_FULL and "ud_app_link" not in rows[1]
    assert "ud_app_link" not in pay["rows"][0]                                  # the shared board itself never carries it


# ================================================================ owner tail links: store, visibility, endpoints
def _rec(sid="aaaaaaaaaaaa-1/b1/s1", line=27.5, start=FUT):
    return {"slip_id": sid, "batch_id": sid.split("/")[0], "book": "underdog", "n": 2,
            "legs": [{"row_id": "u1", "nick": "alpha1", "side": "More", "line": line, "prop": "kills", "scope": "12", "start": start, "book": "underdog"},
                     {"row_id": "u2", "nick": "bravo1", "side": "More", "line": 20.5, "prop": "kills", "scope": "12", "start": FUT, "book": "underdog"}]}


def _live():
    return {r["id"]: dict(r, over_available=True, under_available=True) for r in UD_ROWS}


def test_tail_attach_validate_bound_and_hide(monkeypatch):
    monkeypatch.setattr(server, "PP_OPEN_LINK", True)
    rec = _rec()
    ent, why = ud_tails.attach(rec, TAIL)
    assert why is None and ent["share_id"] == SHARE and ud_tails.get(rec["slip_id"])["url"] == TAIL
    assert ud_tails.attach(rec, "https://evil.example/x")[0] is None
    assert ud_tails.attach(dict(rec, book="prizepicks"), TAIL) == (None, "Only Underdog slips take an Underdog share link")
    t = ud_tails.get(rec["slip_id"])
    assert server.tail_visible(rec, t, _live()) == TAIL
    assert server.tail_visible(_rec(start=PAST), t, _live()) is None                      # a leg's match started
    assert server.tail_visible(_rec(line=26.5), t, _live()) is None                       # the slip's lines changed (signature)
    live = _live(); live["u1"]["line"] = 28.5
    assert server.tail_visible(rec, t, live) is None                                      # a line moved on the board
    live = _live(); live["u1"]["over_available"] = False
    assert server.tail_visible(rec, t, live) is None                                      # the side is no longer offered
    live = _live(); live.pop("u2")
    assert server.tail_visible(rec, t, live) is None                                      # a line pulled
    assert server.tail_visible(dict(rec, status="changed"), t, _live()) is None
    assert server.tail_visible(rec, dict(t, url="https://evil.example/" + SHARE), _live()) is None
    for i in range(ud_tails.TAILS_MAX + 5):                                               # bounded
        ud_tails.attach(_rec(sid=f"aaaaaaaaaaaa-1/b2/s{i}"), TAIL, now=1_700_000_000 + i)
    assert len(ud_tails.all_tails()) == ud_tails.TAILS_MAX
    assert ud_tails.detach("aaaaaaaaaaaa-1/b2/s54") and not ud_tails.detach("aaaaaaaaaaaa-1/b2/s54")


def test_tail_endpoints(site):
    import slip_builder
    rec = _rec()
    site.setattr(slip_builder, "get_slip", lambda sid: rec if sid == rec["slip_id"] else None)
    site.setattr(slip_builder, "slip_status", lambda b, s: None)
    site.setattr(server.underdog, "board", lambda wait=False: {"at": 1.0, "error": None, "rows": [dict(r) for r in _live().values()]})
    site.setattr(server, "ADMIN_TOKEN", "tok")
    form = lambda **k: "&".join(f"{a}={__import__('urllib.parse').parse.quote(str(b), safe='')}" for a, b in k.items()).encode()
    site.setattr(server, "PP_OPEN_LINK", False)                         # flag off: nothing exists
    assert post("/admin/ud_tail", body=form(token="tok", slip_id=rec["slip_id"], url=TAIL)).code == 403
    h = H("/api/slip_studio/tails"); h.do_GET()
    assert h.code == 404
    site.setattr(server, "PP_OPEN_LINK", True)
    assert post("/admin/ud_tail", body=form(token="bad", slip_id=rec["slip_id"], url=TAIL)).code == 403
    assert post("/admin/ud_tail", body=form(token="tok", slip_id="nope", url=TAIL)).code == 400
    assert post("/admin/ud_tail", body=form(token="tok", slip_id=rec["slip_id"], url="https://evil.example/" + SHARE)).code == 400
    h = post("/admin/ud_tail", body=form(token="tok", slip_id=rec["slip_id"], url=TAIL))
    assert h.code == 200 and h.body()["showing"] is True and h.body()["share_id"] == SHARE
    h = H("/api/slip_studio/tails"); h.do_GET()
    d = h.body()
    assert h.code == 200 and d["slips"][0]["tail_url"] == TAIL and d["slips"][0]["slip_id"] == rec["slip_id"]
    assert "batch_id" not in d["slips"][0] and set(d["slips"][0]["legs"][0]) <= set(server._TAIL_LEG)
    g = H("/admin/ud_tails?token=tok"); g.do_GET()
    assert g.code == 200 and SHARE.encode() in g.wfile.getvalue()
    b = server.studio_tail_annotate({"slips": {2: [dict(rec, status="live")]}})
    assert b["slips"][2][0]["tail_url"] == TAIL
    assert post("/admin/ud_tail", body=form(token="tok", slip_id=rec["slip_id"], clear="1")).body()["removed"] is True
    site.setattr(server, "_RESULTS", {})
    h = H("/api/slip_studio/tails"); h.do_GET()
    assert h.body() == {"slips": []}


# ================================================================ LoL: board ids + member rows
@pytest.mark.parametrize("on", [False, True])
def test_lol_member_rows_ud_link(tmp_path, monkeypatch, on):
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    monkeypatch.setattr(server, "PP_OPEN_LINK", on)
    base = {"market_type": "single", "competition": "L", "best_of": 3, "series_start": "2099-01-01T18:00:00Z", "stat": "kills",
            "scope": "Maps 1-2", "odds_type": "balanced", "line": 4.5, "sides": {"higher": {"status": "active"}, "lower": {"status": "active"}},
            "quote_seen_at": "2026-10-19T10:00:00Z", "link": {"status": "matched", "sid": "lol:series:1"},
            "forecast": {"id": 1, "at": "2026-10-19T10:01:00Z", "line": 4.5, "projection": 5.0, "p_over": 0.6, "p_under": 0.4,
                         "p_push": 0, "model_side": "over", "current": True, "grade": "B", "inputs": {}}}
    rows = [dict(base, platform="underdog", market_key=f"ud:{XAN_OU}|balanced", line_id=XAN_LINE, player="U1", team="T1", opponent="T2",
                 ud_over_under_id=XAN_OU, ud_match_id="277401", ud_match_type="Game"),
            dict(base, platform="underdog", market_key="ud:x|", line_id="L", player="U2", team="T2", opponent="T1",
                 ud_over_under_id=None, ud_match_id=None, ud_match_type=None),
            dict(base, platform="prizepicks", market_key="pp:a", line_id="9001", player="P1", team="T1", opponent="T2")]
    (tmp_path / "board.json").write_text(json.dumps({"at": "2026-10-19T10:02:00Z", "rows": rows, "match_tables": []}))
    server._LOL_MEMBER.update(mtime=None, body=None)
    got = {r["player"]: r for r in json.loads(server.lol_member_board())["rows"]}
    server._LOL_MEMBER.update(mtime=None, body=None)
    if not on:
        assert not any("ud_app_link" in r for r in got.values())
    else:
        assert got["U1"]["ud_app_link"] == XAN_FULL and "ud_app_link" not in got["U2"] and "ud_app_link" not in got["P1"]


# ================================================================ pages (node, stub DOM)
def _main_page(tmp_path, name):
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{MAIN}:web/{name}"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if src.returncode != 0:
        pytest.skip("main commit not available")
    p = tmp_path / ("main_" + name)
    p.write_text(src.stdout, encoding="utf-8")
    return p


BOARDROWS = """(() => {
  const r = {id: "u1", book: "underdog", nick: "alpha1", player: "alpha1", line: 27.5, prop: "kills", scope: "12", side: "More", grade: "B", p: 0.6,
             units: 1, team: "Alpha", opponent: "Bravo", start: INPUT.fut, ud_app_link: INPUT.link};
  const d = {grade: {line: 27.5, prop: "kills", scope: "12", line_type: "standard"}, verdict: {side: "More"},
             underdog: {id: "u1", line: 27.5, alt: false, scope: "12", ud_app_link: INPUT.link}};
  return {row: udRowLink(r), bad: udRowLink(Object.assign({}, r, {ud_app_link: "https://evil.example/x"})), pp: udRowLink(Object.assign({}, r, {book: "prizepicks"})),
          card: udCardLink(d), cardOther: udCardLink(Object.assign({}, d, {underdog: Object.assign({}, d.underdog, {line: 26.5})})),
          cardAlt: udCardLink(Object.assign({}, d, {underdog: Object.assign({}, d.underdog, {alt: true})})),
          fromRow: udCardLink(boardCard(r)), js: udLink(INPUT.ou, 277401, "Game"), js2: udLink(INPUT.ou), js3: udLink("nope"), js4: udLink(INPUT.ou, 277401, "Season"),
          tails: INPUT.tails.map(udTailOk), cardPP: udCardLink(Object.assign({}, d, {book: "prizepicks"}))};
})()"""
TAIL_CASES = TAIL_REDIRECTS + [f"https://app.underdogsports.com/x?deep_link_value=entry_slip&deep_link_sub1={SHARE}", f"{TAIL}&pid=share", f"https://play.underdogsports.com/x?deep_link_value=entry_slip&deep_link_sub1={SHARE}",
              f"https://play.underdogsports.com/pick-em/share/{SHARE}/", f"https://app.underdogsports.com/?entrySlipShareId={SHARE}",
              TAIL, f"https://app.underdogsports.com/pick-em/higher-lower/all/home?entrySlipShareId={SHARE}", f"https://play.underdogsports.com/pick-em/share/{SHARE}",
              f"https://evil.example/{SHARE}", f"https://underdogfantasy.onelink.me/vgwg?entrySlipShareId={SHARE}",
              f"https://play.underdogsports.com:444/share/{SHARE}", f"https://app.underdogsports.com/share/{SHARE}", "https://play.underdogsports.com/share/x"]


@needs_node
def test_cs2_js_twin_and_row_card_links(tmp_path):
    data = {"fut": FUT, "link": XAN_FULL, "ou": XAN_OU, "tails": TAIL_CASES}
    out = run_file(tmp_path, CUR("index.html"), "PPL.on = true;\n" + BOARDROWS, data)
    assert out["js"] == XAN_FULL == pplink.ud_link(XAN_OU, XAN_MATCH, "Game") and out["js2"] == XAN_APP and out["js3"] is None and out["js4"] == XAN_APP
    want = f'<a class="bslip udopen" href="{XAN_FULL}" target="_blank" rel="noopener noreferrer" data-udopen="1">Open in Underdog</a>'
    assert out["row"] == want.replace("&", "&amp;") and out["bad"] == "" and out["pp"] == ""
    assert out["cardPP"] == ""                     # review 2026-10-29: a PrizePicks card never shows Open in Underdog
    assert "Open in Underdog</a>" in out["card"] and 'class="chipbtn udopen"' in out["card"] and out["cardOther"] == "" and out["cardAlt"] == ""
    assert "Open in Underdog" in out["fromRow"]
    # the page draws only the server's canonical form (a pasted link with extra keys is rebuilt on the server first)
    assert out["tails"] == [pplink.tail_share_id(u)[0] == u for u in TAIL_CASES]
    assert sum(out["tails"]) >= 6 and not any(out["tails"][:len(TAIL_REDIRECTS)])
    off = run_file(tmp_path, CUR("index.html"), BOARDROWS, data)
    assert off["row"] == off["card"] == off["fromRow"] == ""
    assert "pre-fill" not in json.dumps(out).lower() and "prefill" not in json.dumps(out).lower()


@needs_node
def test_cs2_flag_off_draws_exactly_main(tmp_path):
    ud_studio = dict(STUDIO, book="underdog", tail_url=TAIL, legs=[dict(l, book="underdog") for l in STUDIO["legs"]])
    data = {"slips": SLIPS, "studio": STUDIO}
    body = DRAW.replace("return o; })()", "o.tail = studioSlip(INPUT.ud); return o; })()")
    now = run_file(tmp_path, CUR("index.html"), body, dict(data, ud=ud_studio))
    base = run_file(tmp_path, _main_page(tmp_path, "index.html"), body, dict(data, ud=ud_studio))
    assert now == base and "udopen" not in json.dumps(now) and "Tail on Underdog" not in json.dumps(now)
    on = run_file(tmp_path, CUR("index.html"), "PPL.on = true;\n" + body, dict(data, ud=ud_studio))
    assert "Tail on Underdog</a>" in on["tail"] and 'rel="noopener noreferrer"' in on["tail"]
    assert "Tail on Underdog" not in run_file(tmp_path, CUR("index.html"), "PPL.on = true;\n" + body,
                                              dict(data, ud=dict(ud_studio, tail_url="https://evil.example/" + SHARE)))["tail"]


SLIP_OPEN = OPEN.replace('return {EV, A, OPENED, open: BODIES.map(b => b.open || null), modes: BODIES.map(b => b.mode)};',
                         'return {EV, A, OPENED, open: BODIES.map(b => b.open || null), legs: JSON.parse(localStorage.getItem("cs2s_slip")).map((x, i) => legRow(x, i, false))};')
UDLEGS = [dict(_cs2_leg("alpha1", 27.5, "More", bk="underdog", tm="Alpha"), rid="u1"), dict(_cs2_leg("bravo1", 20.5, "More", bk="underdog", tm="Bravo"), rid="u2")]


@needs_node
@pytest.mark.parametrize("act", ["copy", "underdog"])
def test_cs2_slip_leg_links_after_the_server_ok_only(tmp_path, act):
    body = SLIP_OPEN
    if act == "copy":
        body = body.replace('slipClick({target: {closest: s => s === "[data-slipopen]" ? {dataset: {slipopen: INPUT.act}} : null}});',
                            'slipClick({target: {closest: s => s === "[data-slipcopy]" ? {} : null}});')
    ok = {"status": 200, "body": {"ok": True, "duplicate": False, "open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, LINK2]}}
    out = run_file(tmp_path, CUR("index.html"), body, {"legs": UDLEGS, "act": act, "sl": ok})
    assert "copy" in out["EV"] and len(out["legs"]) == 2
    assert f'href="{XAN_FULL.replace("&", "&amp;")}"' in out["legs"][0] and ">Open</a>" in out["legs"][0] and f'href="{LINK2.replace("&", "&amp;")}"' in out["legs"][1]
    # a forged answer link is never drawn; a refusal draws none
    forged = {"status": 200, "body": dict(ok["body"], legs_open=["https://evil.example/x", LINK2])}
    out = run_file(tmp_path, CUR("index.html"), body, {"legs": UDLEGS, "act": act, "sl": forged})
    assert "evil" not in out["legs"][0] and ">Open</a>" not in out["legs"][0] and ">Open</a>" in out["legs"][1]
    bad = {"status": 409, "body": {"ok": False, "legs": ["alpha1 Over 27.5: Line moved to 28.5"]}}
    out = run_file(tmp_path, CUR("index.html"), body, {"legs": UDLEGS, "act": act, "sl": bad})
    assert not any(">Open</a>" in x for x in out["legs"]) and "copy" not in out["EV"]


# review 2026-10-29: links from an earlier server OK go away on the page's own refusal (changed line) and when legs change
SLIP_TWICE = (SLIP_OPEN
    .replace('if (u.startsWith("/api/line_check")) return {ok: true, status: 200, json: async () => ({status: "verified", note: null, rows: []})};',
             'if (u.startsWith("/api/line_check")) return {ok: true, status: 200, json: async () => (window.LC || {status: "verified", note: null, rows: []})};')
    .replace('slipClick({target: {closest: s => s === "[data-slipopen]" ? {dataset: {slipopen: INPUT.act}} : null}});\n  for (let i = 0; i < 400; i++) await Promise.resolve();',
             'const CLICK = () => slipClick({target: {closest: s => s === "[data-slipcopy]" ? {} : null}});\n'
             '  CLICK(); for (let i = 0; i < 400; i++) await Promise.resolve();\n'
             '  const first = JSON.parse(localStorage.getItem("cs2s_slip")).map((x, i) => legRow(x, i, false));\n'
             '  if (INPUT.then === "changed") { window.LC = {status: "changed", new_line: 29.5, note: "line changed to 29.5", rows: []}; CLICK(); }\n'
             '  if (INPUT.then === "add") { const s = JSON.parse(localStorage.getItem("cs2s_slip")); s.push(Object.assign({}, s[0], {p: "charlie1", rid: "u3", tm: "Charlie"})); localStorage.setItem("cs2s_slip", JSON.stringify(s)); }\n'
             '  for (let i = 0; i < 400; i++) await Promise.resolve();\n'
             '  EV.push(first.filter(x => x.includes(">Open</a>")).length);'))


@needs_node
@pytest.mark.parametrize("then", ["changed", "add"])
def test_cs2_slip_leg_links_cleared_by_a_later_page_refusal(tmp_path, then):
    assert "window.LC" in SLIP_TWICE and "CLICK()" in SLIP_TWICE
    ok = {"status": 200, "body": {"ok": True, "duplicate": False, "open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, LINK2]}}
    out = run_file(tmp_path, CUR("index.html"), SLIP_TWICE, {"legs": UDLEGS, "sl": ok, "then": then})
    assert out["EV"][-1] == 2                      # the first OK drew both legs' links
    assert out["legs"] and not any(">Open</a>" in x or "udopen" in x for x in out["legs"]), out["legs"]
    if then == "changed":
        assert any(a[0] == "Check these picks first" for a in out["A"]) and out["EV"].count("copy") == 1


def _studio_twice():                              # STUDIO_UD is defined below
    return STUDIO_UD.replace(
    '  for (let i = 0; i < 50; i++) await Promise.resolve();\n  return studioSlip(s);',
    '  for (let i = 0; i < 50; i++) await Promise.resolve();\n  const first = studioSlip(s);\n'
    '  studioCall = async (m, u, b) => u === "/api/slip_studio/verify" ? (INPUT.d2 === "throw" ? Promise.reject(new Error("offline")) : {status: INPUT.st2 || 200, d: INPUT.d2}) : {status: 200, d: {}};\n'
    '  await studioVerify("b1/s1", {textContent: "", disabled: false}, INPUT.open2 || undefined);\n'
    '  for (let i = 0; i < 50; i++) await Promise.resolve();\n  return [first, studioSlip(s)];')


@needs_node
@pytest.mark.parametrize("st2,d2,open2", [(429, {"error": "Too many checks"}, None), (200, "throw", None),
                                          (200, "OKBADURL", "underdog"), (200, {"ok": False, "book": "underdog", "changes": ["x"]}, None)])
def test_studio_leg_links_cleared_by_a_later_failed_verify(tmp_path, st2, d2, open2):
    STUDIO_UD_TWICE = _studio_twice()
    assert "INPUT.d2" in STUDIO_UD_TWICE
    slip = dict(STUDIO, book="underdog", legs=[dict(l, book="underdog") for l in STUDIO["legs"]])
    ok = {"ok": True, "book": "underdog", "batch_id": "b1", "copy_text": "t", "open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, None]}
    if d2 == "OKBADURL":                           # verify OK but its open link fails the page check: 'Not opened', no links
        d2 = dict(ok, open_url="https://evil.example/")
    first, second = run_file(tmp_path, CUR("index.html"), STUDIO_UD_TWICE, {"d": ok, "slip": slip, "st2": st2, "d2": d2, "open2": open2})
    assert first.count(">Open</a>") == 1 and ">Open</a>" not in second

STUDIO_UD = """(async () => {
  PPL.on = true; toast = () => {}; copy = async () => {}; window.open = () => ({});
  studioCall = async (m, u, b) => u === "/api/slip_studio/verify" ? {status: 200, d: INPUT.d} : {status: 200, d: {}};
  studioBuild = async () => {};
  const s = Object.assign({}, INPUT.slip, {slip_id: "b1/s1"});
  STUDIO.batch = {slips: {2: [s]}};
  await studioVerify("b1/s1", {textContent: "", disabled: false}, INPUT.open || undefined);
  for (let i = 0; i < 50; i++) await Promise.resolve();
  return studioSlip(s);
})()"""


@needs_node
def test_studio_leg_links_after_verify_ok_only(tmp_path):
    slip = dict(STUDIO, book="underdog", legs=[dict(l, book="underdog") for l in STUDIO["legs"]])
    ok = {"ok": True, "book": "underdog", "batch_id": "b1", "copy_text": "t", "open_url": pplink.UD_HOME, "legs_open": [XAN_FULL, None]}
    for op in (None, "underdog"):
        card = run_file(tmp_path, CUR("index.html"), STUDIO_UD, {"d": ok, "slip": slip, "open": op})
        assert card.count(">Open</a>") == 1 and XAN_FULL.replace("&", "&amp;") in card, op
    card = run_file(tmp_path, CUR("index.html"), STUDIO_UD, {"d": {"ok": False, "book": "underdog", "changes": ["line moved"]}, "slip": slip})
    assert ">Open</a>" not in card


# ---------------------------------------------------------------- LoL page
ULROWS = LROWS[:4] + [dict(_lrow("u1", "Ud1", "T1", None, platform="underdog"), ud_app_link=XAN_FULL),
                      dict(_lrow("u2", "Ud2", "GEN", None, platform="underdog"), ud_app_link=LINK2), LROWS[6]]


@needs_node
def test_lol_flag_off_draws_exactly_main(tmp_path):
    data = {"d": {"rows": ULROWS, "match_tables": [], "slips": {}, "stake_note": "n"}, "slips": LSLIPS}
    now = run_file(tmp_path, CUR("lol.html"), LDRAW, data)
    base = run_file(tmp_path, _main_page(tmp_path, "lol.html"), LDRAW, data)
    assert now == base and "Open in Underdog" not in json.dumps(now)


LUD = """(async () => {
  PPL.on = !INPUT.off; D = INPUT.d; F.view = "slip";
  window.open = () => ({}); navigator.clipboard.writeText = async () => {};
  fetch = async u => ({ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(INPUT.fresh))});
  SLIP.legs = ["u1", "u2"].map(x => legOf(D.rows.find(r => r.key === x)));
  const r = INPUT.act === "copy" ? await copySlip() : await openSlip("underdog");
  render(); const slip = $("#app").innerHTML;
  return {r: !!r, slip, card: udCardLink(D.rows.find(x => x.key === "u1"))};
})()"""


@needs_node
@pytest.mark.parametrize("act", ["copy", "open"])
def test_lol_leg_links_after_the_recheck(tmp_path, act):
    d = {"rows": ULROWS, "match_tables": [], "slips": {}, "stake_note": "n"}
    out = run_file(tmp_path, CUR("lol.html"), LUD, {"d": d, "fresh": d, "act": act})
    assert out["r"] and out["slip"].count(">Open</a>") == 2 and XAN_FULL.replace("&", "&amp;") in out["slip"]
    assert "Open in Underdog</a>" in out["card"]
    moved = dict(d, rows=[dict(r, line=5.5) if r["key"] == "u1" else r for r in ULROWS])
    out = run_file(tmp_path, CUR("lol.html"), LUD, {"d": d, "fresh": moved, "act": act})
    assert not out["r"] and ">Open</a>" not in out["slip"]
    out = run_file(tmp_path, CUR("lol.html"), LUD, {"d": d, "fresh": d, "act": act, "off": True})
    assert ">Open</a>" not in out["slip"] and out["card"] == ""


# ---------------------------------------------------------------- Sports page (closure-private code: static wiring)
def test_sports_page_wiring():
    js = (ROOT / "web" / "sports" / "slips.js").read_text(encoding="utf-8")
    fn = js.split("function udLegLink")[1].split("function pplSync")[0]
    assert "auth.pp_open_link === true" in fn and "r.ok" in fn and "r.current[i].offer_id === l.offer_id" in fn and "UD_LINK_RE.test(u)" in fn
    assert ">Open</a>" in fn and "pre-fill" not in fn.split("*/")[-1]
    # review 2026-10-29: a failed check (network / 5xx) drops the earlier OK's links and redraws the legs
    ck = js.split("async function check(mode)")[1].split("async function copy()")[0].split("} catch (e) {")[1]
    assert "auth.pp_open_link === true && lastCheck) { lastCheck = null; renderDraft(); }" in ck
    tb = (ROOT / "web" / "sports" / "table.js").read_text(encoding="utf-8")
    assert 'q.p === "underdog" && typeof q.ud === "string" && UD_LINK_RE.test(q.ud)' in tb and ">Open in Underdog</a>" in tb
    for f in ("index.html", "lol.html", "sports/slips.js", "sports/table.js"):
        src = (ROOT / "web" / f).read_text(encoding="utf-8")
        assert src.count("market-details%2F[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}") == 1, f
