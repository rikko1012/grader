"""The v3 signed-out landing (landing_v3.py + web/v3/landing.html): the Prop Grader page built to the owner's Nov 23 brief and
DESIGN_RULES.md. No network, no production data: real-shaped board / record / review documents built here.

- view-only (owner access rule): every product picture (browser, phone, Inside panels, Slip stage) is aria-hidden or role="img" and
  holds no link, button, input, tabindex or toggle; the page's only buttons are the five FAQ triggers;
- every number comes from the shared bodies (board snapshot, /api/record, /api/reviews); the only typed-in ones are the owner's
  attributed +46U post and 6-man story; the template carries no player stat, percentage or price;
- frame rows (critic round 2): one lettered market per player, ANY letter, in the neutral rule order with two-app markets first;
  ONE highlighted row leads, tinted - since critic round 3 simply the first row of that order (never chosen for clearing its
  break-even, and the board row prints no break-even; fewer than FRAME_MIN live rows: the dated saved board); the subtitle names
  one app when one app shows; lines printed raw; the phone reads the highlighted row and leaves a missing value out (never 0); the
  line-gap panel is the first real two-app row in rule order of another player than the phone's when there is one, or absent; the
  Slip stage has one real CS2 card (the first other frame row in rule order, its value labelled 'model estimate') and examples
  (the role on the name line, no per-card chip) whose value slot is a lock (no number in the source); each flown board card stays
  in its box dimmed to 30% (brief 6b, critic round 5: no 'Added to slip' outlines, no summary line) and the slip panel ends with its
  own Try CTA;
- record = Won / Lost / Push counts only; the decided count = the table's Won + Lost; the review score never ticks;
- copy / CSS hard rules: no banned words, no arrows or middle-dot strings in page copy, curly apostrophes, one uppercase tag, green
  on one selector, no gradient / blur / shadow except the Slip flight token; every Try button (six) opens the free-trial checkout,
  the weekly link once;
- the request path never computes (cached bodies only) and 700 visitors at once get one body and one ETag."""
import html
import http.client
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import landing_v3
import reviews
import server
import whop_auth
import workspace_api

JOIN = "https://whop.com/checkout/plan_test/?a=1&b=2"           # the free-trial checkout (LANDING_TRIAL_URL) in these tests
WEEK = "https://whop.com/checkout/plan_week_test/?w=1"          # the Weekly checkout (LANDING_WEEKLY_URL)
OLD_JOIN = "https://whop.com/checkout/plan_monthly_old/"       # a stale LANDING_JOIN_URL: drives the flag-off page only, never v3


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def rec(nick, stat, line, book, p, grade, start, moved=None, side="over", be=0.5345, l10=(6, 4), proj=None):
    return {"sport": "cs2", "key": f"k.{nick}.{book}",
            "event": {"id": f"hltv:{nick}", "label": "Alpha vs Beta", "start_utc": iso(start), "lock_utc": iso(start)},
            "player": {"key": nick.lower(), "name": nick},
            "market": {"stat": stat, "stat_label": stat.title(), "period": "maps12", "period_label": "Maps 1-2", "line_type": "standard"},
            "offer": {"platform": book, "platform_label": book.title(), "line": line, "moved_from": moved},
            "scout": {"side": side, "p_side": p, "grade": grade, "projection": proj, "forecast_at": "2026-11-22T10:01:00+00:00"},
            "history": {"l10": {"over": l10[0], "under": l10[1], "n": 10}},
            "parity": {"be": be, "entry": "Underdog standard 2-pick"}, "state": {"kind": "ok"}}


def board(at=None):
    at = at or datetime.now(timezone.utc) - timedelta(minutes=3)
    s = at + timedelta(hours=2)
    return {"board_at": iso(at), "records": [
        rec("Zeta", "kills", 30.5, "underdog", .611, "B", s + timedelta(hours=1)),          # plain, later
        rec("Gap", "headshots", 12.5, "underdog", .583, "B-", s + timedelta(hours=2)),     # a line gap (two apps)
        rec("Gap", "headshots", 13.5, "prizepicks", .522, None, s + timedelta(hours=2)),
        rec("Mover", "kills", 32.5, "underdog", .640, "B+", s, moved=31.5, side="under"),  # a moved line
        rec("NoLetter", "kills", 20.5, "underdog", .505, None, s),                          # no letter: never leads a row
        rec("Past", "kills", 20.5, "underdog", .700, "A-", at - timedelta(hours=1)),        # locked before the board's time
    ]}


def old_board(at):
    """board(at) read long ago, its rows (Past aside) for matches that have not started yet: the live board keeps rows only while
    their match is still ahead of now."""
    b = board(at)
    shift = datetime.now(timezone.utc) - at
    for r in b["records"]:
        if r["player"]["name"] != "Past":
            r["event"]["start_utc"] = r["event"]["lock_utc"] = iso(_ts(r["event"]["lock_utc"]) + shift)
    return b


def _ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def record_doc(sp, w, l, p):
    return {"sport": sp, "as_of": "2026-11-22T15:17:18+00:00", "rule": f"The {sp} rule. Second sentence.",
            "totals": {"wins": w, "losses": l, "pushes": p, "picks": w + l + p, "matches": 40, "match_word": "matches"},
            "calendar": {"days": [{"date": "2026-10-24", "wins": 5, "losses": 4}, {"date": "2026-10-25", "wins": 3, "losses": 6}]}}


def review(i, name, body, title="", photos=(), stars=5):
    return {"id": i, "name": name, "stars": stars, "title": title, "body": body, "photos": list(photos),
            "created": "2026-11-16T19:30:00Z", "edited": None, "verified": True}


REVIEWS = {"summary": {"count": 12, "average": 4.8, "breakdown": {"5": 10, "4": 2, "3": 0, "2": 0, "1": 0}}, "reviews": [
    review(1, "longform", "The cards explain every lean and the map numbers are easy to read on my phone. <script>x</script>", "Clean tool"),
    review(2, "slipper", "Hit big with this a couple of times", "Lit"),
    review(3, "photo", "Love the grader and the reasons", photos=[{"id": "a"}]),
    review(4, "shout", "YALL KNOW WHAT IT IS STOP PLAYING"),
    review(5, "earner", "Extremely profitable and reliable tool"),
    review(6, "short", "Best ever"),
    review(7, "plain", "Reasons behind each prop are great for me"),
    review(8, "mixed", "Love the stats and reasons behind each prop. Nothing guaranteed but my go to when you need a slip!"),
]}


def hltv_off(monkeypatch, loader=None):
    """No HLTV database in tests: fresh hltv caches, nothing saved to / loaded from disk, the map totals from `loader` only (None: no
    database configured, so hltv_maps.for_player answers "unavailable" without a read)."""
    import hltv
    import hltv_maps
    import hltv_store
    for name in ("_CACHE", "_META", "_FLIGHTS"):
        monkeypatch.setattr(hltv, name, {})
    monkeypatch.setattr(hltv_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(hltv_store, "load", lambda *a, **k: None)
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    monkeypatch.setattr(hltv_maps, "LOADER", loader)


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    monkeypatch.setenv("SESSION_SECRET", "landing-v3-secret")
    monkeypatch.setenv("LANDING_JOIN_URL", OLD_JOIN)
    monkeypatch.setenv("LANDING_TRIAL_URL", JOIN)
    monkeypatch.setenv("LANDING_WEEKLY_URL", WEEK)
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("REVIEWS_PAGE", raising=False)
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0])):
        monkeypatch.setattr(server, name, val)
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=None))
    monkeypatch.setattr(workspace_api, "snapshot", lambda s: workspace_api._SNAP if workspace_api._SNAP.get("body") is not None else None)
    import workspace_expansion
    import workspace_lol
    import workspace_sports
    monkeypatch.setattr(workspace_sports, "_SNAP", {lg: workspace_sports._empty() for lg in workspace_sports.LEAGUES})
    monkeypatch.setattr(workspace_lol, "_SNAP", workspace_lol._empty())
    monkeypatch.setattr(workspace_expansion, "_SNAP", {})
    monkeypatch.delenv("LANDING_PUBLIC_URL", raising=False)
    hltv_off(monkeypatch)
    landing_v3.reset()
    yield server
    landing_v3.reset()


def seed(srv, monkeypatch, b=None, rv=REVIEWS, recs=True):
    if b is not None:
        workspace_api._SNAP["body"] = json.dumps(b).encode()
    if recs:
        for sp, t in (("cs2", (100, 90, 2)), ("lol", (50, 40, 1)), ("nba", (300, 280, 3)), ("nfl", (80, 70, 0))):
            srv._store_result(landing_v3.record_key(sp), 200, json.dumps(record_doc(sp, *t)).encode(), 600, 600)
    if rv is not None:
        monkeypatch.setattr(reviews, "page_on", lambda: True)
        srv._store_result(landing_v3._reviews_key(), 200, json.dumps(rv).encode(), 600, 600)


def text(page):
    s = page.decode("utf-8")
    s = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))


def build(srv):
    return landing_v3.build(srv, JOIN, fetch=False)


def wait_builder():
    for _ in range(100):
        if not landing_v3._BUILDER["busy"]:
            return
        time.sleep(0.05)


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def block(s, start, end="</section>"):
    i = s.index(start)
    return s[i:s.index(end, i)]


def frames(s):
    """Every product picture on the page: browser, phone, Inside panels and the Slip stage figure."""
    out = []
    for cls in ('<div class="browser" aria-hidden="true">', '<div class="phone" aria-hidden="true">', '<div class="panel" aria-hidden="true">',
                '<div class="ss-fig">'):
        i = 0
        while True:
            i = s.find(cls, i)
            if i < 0:
                break
            depth, j = 0, i
            for t in re.finditer(r"<(/?)div\b", s[i:]):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    j = i + t.end()
                    break
            out.append(s[i:j])
            i = j
    return out


# ------------------------------------------------------------------------------------------------------------ the hero frames
def test_frame_rows_any_letter_in_rule_order_with_the_first_row_highlighted(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<div class="br(?: hl)?"><span class="b-who"><b>([^<]+)</b>', s)
    assert rows == ["Gap", "Mover", "Zeta"]           # rule order, two-app first; the first row is the highlight (a B- here)
    assert s.count('<div class="br hl">') == 1 and '<div class="br hl"><span class="b-who"><b>Gap</b>' in s
    assert "NoLetter" not in s.split('class="bt"')[1].split('<p class="cap hero-cap">')[0] and ">Past<" not in s
    assert 'class="bfoot"' not in s                   # 3 graded props < MORE_MIN: never a small "of N" count
    mover = re.search(r'(?s)<div class="br"><span class="b-who"><b>Mover</b>.*?</span></div>', s).group(0)
    assert "<b>64.0%</b>" in mover and 'scaleX(0.640)' in mover
    assert '<span class="b-pk">Under</span>' in mover and '<i class="gb gc">B+</i>' in mover
    gap = re.search(r'(?s)<div class="br hl"><span class="b-who"><b>Gap</b>.*?</div>', s).group(0)
    assert '<i class="gb gg">B-</i>' in gap
    frame = s.split('class="bt"')[1].split('<p class="cap hero-cap">')[0]
    assert "break-even" not in frame and "b-pe" not in s           # critic round 3: no board row prints its break-even
    assert "<small>Prizepicks + Underdog, graded at each app’s own line</small>" in s and "<span>Line by app</span>" in s
    cap = re.search(r'<p class="cap hero-cap"><span class="cap-d">([^<]+)</span><span class="cap-m">([^<]+)</span></p>', s)
    today = landing_v3._date(landing_v3._ts(board()["board_at"]))
    assert cap.group(1) == f"Example rows from the public sample board, {today}. A model estimate is a chance, not a promise."
    # phones show the phone frame only (one prop): their caption says "Example prop" (critic round 6; CSS picks one per width)
    assert cap.group(2) == f"Example prop from the public sample board, {today}. A model estimate is a chance, not a promise."
    sr = html.unescape(re.search(r'<p class="sr-only">(Example rows from the member board:[^<]+)</p>', s).group(1))
    assert sr.startswith("Example rows from the member board: Gap, Headshots Maps 1-2, Prizepicks 13.5, Underdog 12.5, Over, model estimate 58.3%, grade B-;")
def test_highlight_is_the_first_two_app_market_by_position_never_merit(srv, monkeypatch):
    """Critic round 3 (access rule): the highlight is not chosen for clearing its break-even. A later one-app market that clears
    its break-even by far never jumps ahead of the first two-app market, even one that does not clear its own."""
    b = board()
    for r in b["records"]:
        if r["player"]["name"] == "Gap":
            r["scout"]["p_side"] = .51 if r["offer"]["platform"] == "underdog" else r["scout"]["p_side"]   # under its 53.45% break-even
    b["records"].append(rec("NoBe", "kills", 22.5, "underdog", .700, "A", datetime.now(timezone.utc) + timedelta(hours=3), be=None))
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert re.findall(r'<div class="br(?: hl)?"><span class="b-who"><b>([^<]+)</b>', s) == ["Gap", "Mover", "Zeta", "NoBe"]
    gap = re.search(r'(?s)<div class="br hl"><span class="b-who"><b>Gap</b>.*?</span></div>', s).group(0)
    assert '<span class="b-ln"><span>PP 13.5</span><span>UD 12.5</span></span>' in gap   # each app's line, raw, PrizePicks first
    assert "<b>51.0%</b>" in gap and "break-even" not in gap
    assert '<p class="ph-who">Gap</p>' in s                                               # the phone mirrors the highlight


def test_one_app_frames_never_say_each_apps_own_line():
    one = [([rec("Solo", "kills", 20.5, "underdog", .6, "B", datetime.now(timezone.utc))], False, False)]
    assert landing_v3.feeds_label(one) == ("Graded at Underdog’s line", "Line")
    assert landing_v3.feeds_label([]) == ("", "Line")
def test_a_live_board_with_one_row_shows_the_dated_saved_board_but_inside_stays_live(srv, monkeypatch):
    b = board()
    b["records"] = [r for r in b["records"] if r["player"]["name"] not in ("Zeta", "Mover")]   # one live row < FRAME_MIN
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    frame = s.split('class="bt"')[1].split('<p class="cap hero-cap">')[0]
    assert "public sample board, Nov 22, 2026." in s and ">Gap<" not in frame and s.count('<div class="br hl">') == 1
    assert "<small>Graded at Underdog’s line</small>" in s and "<span>Line</span>" in s and "each app" not in frame
    assert "Gap · Headshots · Maps 1-2" in s                              # the gap panel still reads the live board
    assert "graded on the board right now" not in s and 'class="bfoot"' not in s
def test_frame_picks_in_rule_order_never_the_boards_best_grades(srv, monkeypatch):
    """Owner access rule: the free page is not a top-picks feed. Rows are chosen in the neutral rule order (two-app markets, gaps,
    moved, the soonest) with any letter, so a later A+ never jumps into the frame over sooner rows."""
    b = board()
    s0 = datetime.now(timezone.utc) + timedelta(minutes=30)
    b["records"] += [rec(f"Soon{i}", "kills", 20.5, "underdog", .600, "B", s0 + timedelta(minutes=i)) for i in range(3)]
    b["records"].append(rec("Late", "kills", 20.5, "underdog", .800, "A+", datetime.now(timezone.utc) + timedelta(hours=9)))
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<div class="br(?: hl)?"><span class="b-who"><b>([^<]+)</b>', s)
    assert rows == ["Gap", "Mover", "Soon0", "Soon1", "Soon2", "Zeta"] and "Late" not in rows     # HERO_ROWS = 6 (critic round 6)
def test_lines_print_as_the_member_board_prints_them(srv, monkeypatch):
    b = board()
    b["records"][0]["offer"]["line"] = 30.0
    b["records"][3]["offer"]["line"] = 32
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "<span>UD 30</span>" in s and "<span>UD 32</span>" in s and "UD 30.0" not in s


def test_phone_reads_the_first_row_and_hides_missing_values(srv, monkeypatch):
    b = board()
    b["records"] = [r for r in b["records"] if r["player"]["name"] != "Gap"]      # Mover is then the first row in rule order
    seed(srv, monkeypatch, b)
    ph = block(build(srv).decode("utf-8"), '<div class="phone"', '<p class="sr-only">')
    assert "<p class=\"ph-who\">Mover</p>" in ph and '<p class="ph-prop">Under 32.5 Kills · Maps 1-2</p>' in ph
    assert '<p class="ph-m">CS2 · Underdog · Alpha vs Beta</p>' in ph     # the example names its sport (one of six)
    assert '<b>64.0%</b><i class="gb gc">B+</i>' in ph and "Model estimate at the 32.5 line</p>" in ph and "exactly" not in ph
    why = re.findall(r"<li><span>([^<]+)</span><b>([^<]+)</b></li>", ph)
    assert why == [("Last 10 at this line", "4 / 10 Under"), ("Break-even, standard 2-pick", "53.4%")]   # no projection, no other app
    assert "Projection" not in ph and ">0<" not in ph and "0.0 vs" not in ph
    assert "Slip Studio is for members</p>" in ph and "Add to Slip Studio" not in ph and "<button" not in ph and "<a " not in ph


def test_phone_lists_projection_and_the_other_apps_line_when_the_row_has_them(srv, monkeypatch):
    b = board()
    for r in b["records"]:
        if r["player"]["name"] == "Gap":
            r["scout"]["p_side"] = .7 if r["offer"]["platform"] == "underdog" else r["scout"]["p_side"]
            r["scout"]["grade"] = "A" if r["offer"]["platform"] == "underdog" else None
            r["scout"]["projection"] = 13.9
    seed(srv, monkeypatch, b)
    ph = block(build(srv).decode("utf-8"), '<div class="phone"', '<p class="sr-only">')
    why = dict(re.findall(r"<li(?: class=oa)?><span>([^<]+)</span><b>([^<]+)</b></li>", ph))
    assert why["Projection"] == "13.9 vs 12.5" and why["Prizepicks line"] == "13.5" and "Gap" in ph
    assert "<li class=oa><span>Prizepicks line</span>" in ph and ph.count("class=oa") == 1    # hidden in the >=1024 phone (D21), shown on phones


def test_line_gap_panel_is_a_real_two_app_row_else_left_out(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    p = block(s, '<div class="icol">', '<h3>Built for CS2')
    assert "Gap · Headshots · Maps 1-2" in p
    assert '<div class="lt"><span>Prizepicks</span><b>13.5</b></div><div class="lt"><span>Underdog</span><b>12.5</b></div>' in p
    assert '<p class="gd"><b>1.0</b><span>Over 12.5 on Underdog</span></p>' in p and "58.3% model estimate" in p
    b = board()
    b["records"] = [r for r in b["records"] if r["player"]["name"] != "Gap"]
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "When the apps disagree" not in s and 'class="lt"' not in s and "{{" not in s


def test_line_gap_panel_prefers_another_player_than_the_phone():
    t = datetime.now(timezone.utc) + timedelta(hours=2)
    a = ([rec("Phone", "kills", 20.5, "underdog", .6, "B", t), rec("Phone", "kills", 21.5, "prizepicks", .5, None, t)], True, False)
    o = ([rec("Other", "kills", 30.5, "underdog", .55, "B-", t), rec("Other", "kills", 29.5, "prizepicks", .5, None, t)], True, False)
    assert landing_v3.gap_panel([a, o], skip="phone")["GAP_WHO"].startswith("Other")
    assert landing_v3.gap_panel([a], skip="phone")["GAP_WHO"].startswith("Phone")     # the only gap: still shown
def test_slip_stage_has_one_real_row_and_examples_with_no_real_numbers(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    stage = block(s, '<div class="stage" id="ss">', '<p class="cap">Example animation.')
    cards = re.findall(r'<div class="sbx" data-i="\d"><div class="sc(?: ph)?">(.*?)</div></div>(?=<div class="sbx"|</div>)', stage)
    # the first frame row in rule order other than the phone's (Gap) and the gap panel's (Gap): Mover - never the best estimate
    assert len(cards) == 4 and "<b>Mover</b>" in cards[0] and "<b>64.0%</b>" in cards[0] and "<small>model estimate</small>" in cards[0]
    assert stage.count('<div class="sc ph">') == 3 * 2 and s.count('class="btn btn-md ss-cta"') == 0
    assert '<p class="ss-cta-p"><a class="tl ss-cta" href="' in s       # critic round 6: the text column's Try is a text link (one pill per view)
    assert "Added to slip" not in stage and "ss-done" not in stage and "All 4 legs" not in stage and 'class="sad"' not in s
    # the one Slip pill: outside role=img AND outside the slip card (critic round 6), a child of the stage grid
    assert re.search(r'</div>\s*</div>\s*<a class="btn ss-go" href="[^"]+">Try Prop Grader free for 1&nbsp;day</a>\s*</div>\s*</div>', stage)
    assert stage.index('role="img"') < stage.index('class="ss-all"') < stage.index('class="btn ss-go"')
    assert "phl" not in s and "Members only" not in stage and "ss-ft" not in s
    for c, who in zip(cards[1:], ("NBA guard", "LoL mid", "Tennis")):
        assert f'<b>{who}</b>' in c and '<span class="sv mo">' in c and "Example" not in c
        assert 'class="mt"' not in c and not re.search(r"\d+(\.\d)?%", c) and "placeholder" not in c
    assert stage.count('class="slot"') == 4 and '<span class="bl">00.0% vs 00.0%</span>' in stage
    blurred = re.findall(r'<span class="(?:sv )?bl">(.*?)</span>', stage)
    assert blurred and all(not re.search(r"[1-9]", re.sub(r"<[^>]+>", "", x)) for x in blurred)    # glyphs only behind every blur
    assert ('<div class="ss-pic" role="img" aria-label="Example animation: four graded props from different sports move from the board into a '
            '4-pick slip; the chance that all legs hit is members only.">') in stage and '<div class="ss-col ss-board" aria-hidden="true">' in stage
    assert ("Example animation. Mover is a real row from the public sample board; the other legs are placeholders, and members-only "
            "numbers are blurred.") in s
    assert len(re.findall(r"(?i)example", block(s, 'id="slip"', '</section>'))) <= 4      # never a disclaimer wall
def test_view_only_nothing_inside_a_frame_is_usable(srv, monkeypatch):
    """Owner access rule: no link, button, input, tabindex, toggle or handler inside any product picture."""
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    fs = frames(s)
    assert len(fs) >= 4                                                  # browser, phone, gap panel, Slip figure (no HLTV here)
    for f in fs:
        f = re.sub(r'<a class="btn ss-go" href="[^"]+">Try Prop Grader free for 1&nbsp;day</a>', "", f)   # the slip CTA (brief 6b), outside role=img
        assert not re.search(r"<(a|button|input|select|textarea|summary|details)\b|tabindex=|aria-expanded|onclick|href=", f), f[:80]
    assert s.count("<button") == 5                                        # the five FAQ triggers are the page's only buttons
    js = landing_v3.ASSETS["PAGE_JS"].read_text(encoding="utf-8")
    assert "pointermove" not in js and ".light" not in js                 # the cursor light is gone (D2)


# ------------------------------------------------------------------------------------------------------------ proof + record
def test_record_table_and_decided_count_come_from_the_record_answers(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    rec = block(s, 'id="record"')
    rows = re.findall(r'<tr><th scope="row"><span>([^<]+)</span><small>([^<]+)</small></th><td class="w">([\d,]+)</td><td class="l">([\d,]+)</td><td class="p">([\d,]+)</td></tr>', rec)
    assert [r[0] for r in rows] == ["CS2", "League of Legends", "NBA", "NFL"] and rows[0][1:] == ("40 matches", "100", "90", "2")
    decided = sum(int(w) + int(l) for _n, _m, w, l, _p in rows)
    assert decided == 1010
    proof = block(s, '<section class="proof"')
    assert f'data-to="{decided}"' in proof and f'>{decided:,}</span><span class="sr-only">{decided:,}</span>' in proof
    assert "%" not in text(rec.encode()) and "From the public record, Nov 22, 2026." in text(rec.encode())
    assert "Each sport is counted" not in s                                           # one caption (brief 7: per-sport rules on /record)


def test_proof_strip_is_live_attributed_and_the_score_never_ticks(srv, monkeypatch):
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com")
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    proof = block(s, '<section class="proof"')
    assert '<span class="tk" aria-hidden="true" data-to="46" data-pre="+" data-suf="U" style="min-width:4ch">+46U</span><span class="sr-only">+46U</span>' in proof
    assert ('<p class="pl">on Grader 4 mans in the past week, and Grader has only been live 2 weeks.'
            '<span class="sr-only"> (4 mans are 4-pick slips.)</span></p>') in proof and proof.count("46U") == 2   # brief verbatim
    assert '<p class="cap">Trey on X, Nov 20, 2026</p>' in proof                       # attribution only (hard rule 0.6)
    assert 'href="/record">See the record<' in proof and 'href="/reviews">Read all reviews<' in proof and '<div class="pf">' in proof
    assert '<span class="star" aria-hidden="true">★</span>&nbsp;4.8</span><span class="sr-only">4.8 out of 5</span>' in proof
    assert proof.count("data-to=") == 2 and "from 12 member reviews. Every review is published, whatever its rating." in proof
    assert "#5BD69A" not in proof and "good" not in proof


def test_missing_inputs_drop_their_blocks_and_the_page_still_serves(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "0")
    monkeypatch.setattr(reviews, "page_on", lambda: False)
    s = build(srv).decode("utf-8")
    assert 'class="browser"' in s and "Nov 22, 2026" in s                  # the saved board, dated, instead of live rows
    assert 'id="record"' not in s and "decided picks" not in s and "member reviews" not in s and 'class="rqs"' not in s
    assert 'id="inside"' not in s and 'href="#inside"' not in s           # one-app saved board, no map panel: no Inside section at all
    assert '<ul class="kbars">' not in s                    # the saved board never drives the map panel
    assert "{{" not in s and "<!--if:" not in s and ">0<" not in s
    assert s.count(f'href="{JOIN.replace("&", "&amp;")}"') == 7


def test_every_in_page_link_has_its_section(srv, monkeypatch):
    def check(s):
        ids = set(re.findall(r'\sid="([^"]+)"', s))
        missing = sorted({h for h in re.findall(r'href="#([^"]+)"', s)} - ids)
        assert not missing, missing
    seed(srv, monkeypatch, board(), rv=None, recs=False)
    s = build(srv).decode("utf-8")
    check(s)
    assert 'href="#record"' not in s and 'href="#slip"' in s
    seed(srv, monkeypatch, board())
    check(build(srv).decode("utf-8"))
    monkeypatch.setattr(landing_v3, "fallback_board", lambda: None)
    workspace_api._SNAP["body"] = None
    s = build(srv).decode("utf-8")
    check(s)
    assert 'href="#slip"' not in s and 'class="browser"' not in s


# ------------------------------------------------------------------------------------------------------------------- reviews
def test_review_cards_escape_feature_by_rule_and_leave_handles_off(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    m = block(s, 'id="members"')
    assert "<script>x</script>" not in s and "&lt;script&gt;" in m
    quotes = re.findall(r"<blockquote><p>&ldquo;(.*?)&rdquo;</p></blockquote>", m)
    assert len(quotes) == 3                       # complete reviews first, then ONE excerpt fills the row (critic round 4, reverses D38)
    assert quotes[0].startswith("The cards explain every lean") and quotes[1] == "Reasons behind each prop are great for me"
    assert quotes[2] == "Love the stats and reasons behind each prop.\u00a0…"     # whole sentences + an ellipsis where the rest was left out (round 6)
    assert '<p class="st-t">A member ran his first ever 6&nbsp;man with Grader and cashed.</p>' in m   # Prop Grader's post: no quote marks
    caps = re.findall(r"<figcaption>(Paying member[^<]*)", m)
    assert [c.strip() for c in caps] == ["Paying member, Nov 16, 2026"] * 3 and not any(n in m for n in ("longform", "slipper", "earner"))
    assert "Hit big" not in m and "profitable" not in m and "YALL" not in m and "guaranteed" not in m and "Paying member ·" not in m
    assert m.count('<a class="tl" href="/reviews">Full review</a>') == 1 and m.count("…") == 1 and "2026.<" not in m   # the excerpt links its review
    assert ('<p class="rv-line"><a href="/reviews"><span aria-hidden="true"><span class="star">★</span>&nbsp;4.8 ·&nbsp;</span>'
            '<span class="sr-only">Rated 4.8 out of 5 from </span>12 reviews, all published</a></p>') in m
    assert "st-r" not in m and m.count("4.8") == 2                    # one rating line beside the H2; the 6-man card is the post only
    # critic round 6: the members' reviews sit under the H2; Prop Grader's own post follows with its own source label
    assert m.index('class="rqs n3"') < m.index('class="story"') and '<p class="st-k">From Prop Grader on X</p>' in m
    assert '<h2 id="rv-title">Members, in their words.</h2>' in m
    assert "Shared by Prop Grader on X. One member, one night: a result, not a promise." in m
def test_review_order_cut_rule_and_curly_apostrophes():
    rv = {"summary": {"count": 3}, "reviews": [
        review(1, "a", "Love the stats and the reasons. Paid me big last week, money every day."),            # cut, 6 words left: fill
        review(2, "b", "The breakdowns on every card are clear and I can read the map data quickly before I build. "
                       "Made money all week with it."),                                                    # cut, 18 words left
        review(3, "c", "You've got the stats right there, it's the best board."),                          # complete
        review(4, "d", "Love the stats. Made money."),                                                     # cut, 3 words left: never
    ]}
    got = landing_v3.featured(rv)
    # complete first; excerpts fill the row (product talk, then length); a 3-word excerpt never
    assert [r["id"] for r, _q, _c, _o in got] == [3, 2, 1] and [c for _r, _q, c, _o in got] == [False, True, True]
    assert [q for _r, q, _c, _o in got][1:] == ["The breakdowns on every card are clear and I can read the map data quickly before I build.",
                                                 "Love the stats and the reasons."]
    _h, cards, _n, _a = landing_v3.wall(rv)
    assert "You’ve got the stats right there, it’s the best board." in cards and "&#x27;" not in cards
    # an ellipsis on the side that was cut (both excerpts here keep their first sentence and drop the rest)
    assert "I build.\u00a0…&rdquo;" in cards and "the reasons.\u00a0…&rdquo;" in cards and cards.count("…") == 2 and cards.count("Full review") == 2


def test_an_excerpt_is_one_run_of_consecutive_sentences():
    rv = {"summary": {"count": 1}, "reviews": [
        review(1, "a", "Love the stats on every card. Made money all week. The map data and reasons are clear too.")]}
    (_r, q, cut, _o), = landing_v3.featured(rv)
    assert cut and q == "The map data and reasons are clear too."          # never two sentences joined over the cut one
def test_rating_shows_only_from_ten_reviews(srv, monkeypatch):
    few = dict(REVIEWS, summary={"count": 4, "average": 5.0})
    seed(srv, monkeypatch, board(), rv=few)
    s = build(srv).decode("utf-8")
    assert "★" not in s and "4 reviews, all published" in text(s.encode())


def test_reviews_naming_the_old_brand_are_never_featured(srv, monkeypatch):
    rv = dict(REVIEWS, reviews=REVIEWS["reviews"] + [review(9, "brand", "The CS2 Scout cards explain every stat and the map data clearly")])
    seed(srv, monkeypatch, board(), rv=rv)
    assert "CS2 Scout" not in build(srv).decode("utf-8")


# ---------------------------------------------------------------------------------------------------------- copy + structure
SECTION_ORDER = ['class="nav"', 'class="hero"', 'class="proof"', 'id="how"', 'id="inside"', 'id="why"', 'id="slip"', 'id="record"',
                 'id="members"', 'id="pricing"', 'id="faq"', 'id="final"', 'class="foot"']


def test_sections_follow_the_brief_order_and_wording(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    pos = [s.index(k) for k in SECTION_ORDER]
    assert pos == sorted(pos)
    t = text(s.encode())
    for line in (" · ".join(landing_v3.LABEL[x] for x in landing_v3.sports_on()), "Grade every prop before you lock it in.",
                 "Every PrizePicks and Underdog line gets a letter grade and a model estimate, with the reason in plain English.",
                 "One row instead of six apps.", "Open each app and write the numbers down", "Payout table, then a calculator",
                 "Then $24.99/month. Cancel anytime.", "Open the board. Read the grade. Play your line.",
                 "What members see when they log in.", "When the apps disagree, you see it first.", "Built for CS2, not bolted on.",
                 "Slips, built from graded lines.",
                 "Saved before the game. Scored after.", "See every pick on the record", "Members, in their words.",
                 "Start with a free day.", "One plan with everything. Cancel anytime from your Whop account.",
                 "Your first day is free. Then $24.99/month until you cancel.", "Secure checkout on Whop", "Questions.",
                 "Prop Grader gives chances, not certainties. Play only what you can afford to lose."):
        assert line in t, line
    assert s.count("<h1") == 1 and t.count("lock it in.") == 2                              # the final call to action repeats the H1
    assert '<h2 id="final-title">Grade every prop before you <span class="ac nw">lock it in.</span></h2>' in s
    assert "graded on the board right now" not in s and "already graded" not in s and 'class="hero-proof"' not in s
    assert "model likes" not in t and 'class="l2"' not in s                            # the owner's sub, verbatim at every width
    # rulebook 7.5 / 7.7 (critic round 6): Inside carries its eyebrow, Why gives its own up (the budget is 4)
    assert re.findall(r'<p class="eb[^"]*">([^<]+)</p>', s) == ["How it works", "Inside Prop Grader", "Slip Studio", "The public record"]


def test_faq_is_a_five_item_button_accordion_with_the_live_answers(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    faq = block(s, 'id="faq"')
    qs = re.findall(r'<h3><button type="button" id="(q\d)" aria-expanded="(true|false)" aria-controls="(a\d)">([^<]+)<i aria-hidden="true"></i></button></h3>', faq)
    assert [q[3] for q in qs] == ["Is this a picks group?", "What happens after my free day?", "How do I know the grades are honest?",
                                  "Will I win every day?", "Can I cancel?"]
    assert [q[1] for q in qs] == ["true", "false", "false", "false", "false"]
    for _q, _e, a, _t in qs:
        assert f'<div class="ap" id="{a}">' in faq
    assert "Your membership renews at $24.99/month unless you cancel from your Whop account before the free day ends." in faq
    assert "<details" not in s


def test_template_copy_follows_the_rulebook():
    raw = landing_v3.TEMPLATE.read_text(encoding="utf-8")
    t = text(re.sub(r"\{\{[A-Z_0-9]+\}\}", " ¤ ", raw).encode())         # a fill is never read as a digit beside the copy
    low = t.lower().replace("00.0%", "")                                 # the Slip stage's blurred placeholder glyphs
    for bad in (r"guarantee", r"hit rate", r"win[- ]rate", r"\block(?! it in)", r"21\+", r"gambler", r"\bodds\b", r"\beasy\b", r"\bsimple\b",
                r"\bquick\b", r"\bvery\b", r"\bjust\b", r"\breally\b", r"seamless", r"unlock", r"supercharge", r"game-changer",
                r"\d+(\.\d+)?\s*%", r"\$", r"profit", r"\bunits?\b"):
        assert not re.search(bad, low), bad
    page_copy = re.sub(r'(?s)<div class="(browser|phone|panel)" aria-hidden="true">.*?</div>\s*</div>|<div class="stage" id="ss">.*?<p class="cap">', "", raw)
    assert not re.search(r"[A-Za-z]'[A-Za-z]", re.sub(r"<[^>]+>", " ", page_copy)), "straight apostrophe in copy"
    assert "→" not in raw and "&rarr;" not in raw
    head = raw.split("<body>")[1]
    # middle dots only inside product pictures, plus the three lines the brief itself writes with them (critic round 5): the hero pill,
    # the rating line beside the Members H2 and the pricing note
    head = re.sub(r'(?s)<p class="hero-for">.*?</p>|<p class="rv-line">.*?</p>|<p class="wk">Prefer weekly\? .*?</p>|<th scope="col" class="nc">.*?</th>', "", head)
    for frag in ("· ", " ·"):
        copy = re.sub(r"(?s)<div class=\"(browser|phone|panel)\".*?<p class=\"sr-only\">|<div class=\"stage\" id=\"ss\">.*?<p class=\"cap\">", "", head)
        assert frag not in re.sub(r"<[^>]+>", "", copy), frag
    assert "<script src" not in raw and "<img" not in raw
    del page_copy


def test_css_follows_the_hard_rules():
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    body = re.sub(r"(?s)/\*.*?\*/", "", css)
    for bad in ("gradient", "backdrop-filter", "grain", "purple", "#8", "ease-in;", "ease-in ", " linear", "transition:all", "transition: all"):
        assert bad not in body, bad
    shadows = re.findall(r"box-shadow:([^;}]+)", body)
    assert shadows == ["var(--flight-shadow)"] and "--flight-shadow:0 8px 24px rgba(0,0,0,.35)" in body
    assert body.count("#5BD69A") == 1 and body.count("var(--color-win)") == 1 and ".rec td.w{color:var(--color-win)" in body
    assert body.count("text-transform:uppercase") == 1 and ".tag{" in body.split("text-transform:uppercase")[0].rsplit("}", 1)[-1] + ".tag{"
    for sel in (".cap", ".terms", ".foot-in", ".tag", ".br-h"):
        m = re.search(re.escape(sel) + r"\{[^}]*font(?:-size)?:(?:\d00 )?(\d+)px", body)
        assert m and int(m.group(1)) >= (11 if sel == ".tag" else 12), sel
    assert "prefers-reduced-motion:reduce" in body and ":focus-visible{outline:2px solid var(--sc-accent)" in body


def test_links_go_to_the_trial_and_weekly_checkouts(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = landing_v3.page(srv, server.landing_trial_url()).decode("utf-8")
    assert '<html lang="en" data-ui-v3="1" data-v3-page="landing" data-sc-sport="cs2" data-theme="dark" style="color-scheme:dark">' in s
    trial, week = JOIN.replace("&", "&amp;"), WEEK.replace("&", "&amp;")
    btns = re.findall(r'<a class="btn[^"]*"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', s)
    assert len(btns) == 6 and {h for h, _t in btns} == {trial}          # nav, hero, the Slip pill (brief 6b), pricing, final, phone bar
    assert sorted(t for _h, t in btns) == sorted(["Try free", "Try free for 1&nbsp;day"] + ["Try Prop Grader free for 1&nbsp;day"] * 4)
    assert re.findall(r'<a class="tl ss-cta" href="([^"]+)">Try Prop Grader free for 1&nbsp;day</a>', s) == [trial]   # the Slip text link
    whop = re.findall(r'href="(https://whop\.com/[^"]+)"', s)
    assert set(whop) == {trial, week} and whop.count(week) == 1 and (f'<p class="wk">Prefer weekly? <a href="{week}">$9.99/week</a><span class="wk-dot"> · </span>'
                                                                       '<span class="nw wk-s">Secure checkout on Whop</span></p>') in s
    assert OLD_JOIN not in s
    hero = block(s, '<section class="hero"')
    assert re.search(r'id="hero-cta" href="[^"]+">Try Prop Grader free for 1&nbsp;day</a>\s*<p class="terms">Then \$24\.99/month\. Cancel anytime\.</p>', hero)


def test_trial_and_weekly_urls_default_to_the_verified_plans(monkeypatch):
    for k in ("LANDING_TRIAL_URL", "LANDING_WEEKLY_URL", "LANDING_JOIN_URL"):
        monkeypatch.delenv(k, raising=False)
    assert server.landing_trial_url() == "https://whop.com/checkout/plan_llXQ8ebwMzfRd"
    assert server.landing_weekly_url() == "https://whop.com/checkout/plan_4zOzRl7roBDFf"


def test_saved_board_is_real_rows_with_their_own_time():
    doc = json.loads(landing_v3.FALLBACK.read_text(encoding="utf-8"))
    assert doc["board_at"] == "2026-11-22T17:53:44+00:00" and len(doc["records"]) >= 6
    for r in doc["records"]:
        assert r["market"]["line_type"] == "standard" and r["offer"]["line"] is not None and r["scout"]["p_side"] is not None
        assert "units" not in r["scout"] and "stake_reason" not in r["scout"]
    rows, _at, n = landing_v3._rows(doc, n=landing_v3.HERO_ROWS)
    assert rows and n >= len(rows)


def test_an_old_live_board_never_shows_a_match_that_has_started(srv, monkeypatch):
    b = board(datetime.now(timezone.utc) - timedelta(hours=5))           # a kept board: its rows' matches started an hour ago... except none
    for r in b["records"]:
        r["event"]["lock_utc"] = r["event"]["start_utc"] = iso(datetime.now(timezone.utc) - timedelta(minutes=10))
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "Mover" not in s and "Zeta" not in s and "Nov 22, 2026" in s   # the saved board instead


# ------------------------------------------------------------------------------------------------------- capacity (700 at once)
def test_an_idle_page_is_rebuilt_before_it_is_served(srv, monkeypatch):
    seed(srv, monkeypatch, old_board(datetime(2026, 11, 19, 16, 20, tzinfo=timezone.utc)))
    a = landing_v3.page(srv, JOIN)
    assert b"public sample board, Nov 19, 2026." in a
    wait_builder()
    workspace_api._SNAP["body"] = json.dumps(old_board(datetime(2026, 11, 21, 9, 5, tzinfo=timezone.utc))).encode()
    landing_v3._STATE["made"] = landing_v3._STATE["built"] = time.time() - landing_v3.STALE_S - 1
    b = landing_v3.page(srv, JOIN)
    assert b"public sample board, Nov 21, 2026." in b and b"Nov 19, 2026" not in b


def test_page_bytes_are_one_object_per_build_and_request_path_never_computes(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    a = landing_v3.page(srv, JOIN)
    b = landing_v3.page(srv, JOIN)
    assert a is b and b"Mover" in a
    wait_builder()
    assert landing_v3.page(srv, JOIN) is a


def test_700_signed_out_visitors_get_one_body_and_one_etag(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        want = get(port, "/")[2]
        assert b'class="browser"' in want and b"Mover" in want

        def one(_):
            for attempt in range(3):
                try:
                    return get(port, "/")
                except OSError:
                    time.sleep(0.2 * (attempt + 1))
            return get(port, "/")

        with ThreadPoolExecutor(max_workers=700) as pool:
            out = list(pool.map(one, range(700)))
        assert all(st == 200 for st, _h, _b in out)
        assert len({b for _s, _h, b in out}) == 1 and len({h["etag"] for _s, h, _b in out}) == 1
        st, _h, _b = get(port, "/", {"If-None-Match": out[0][1]["etag"]})
        assert st == 304
    finally:
        httpd.shutdown()


def test_one_changed_input_makes_exactly_one_new_page(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    landing_v3.page(srv, JOIN)
    wait_builder()
    a = landing_v3.page(srv, JOIN)
    builds = []
    real = landing_v3.build
    monkeypatch.setattr(landing_v3, "build", lambda *x, **k: builds.append(1) or real(*x, **k))
    assert landing_v3.page(srv, JOIN) is a
    b2 = board()
    b2["records"] = [r for r in b2["records"] if r["player"]["name"] != "Zeta"]
    workspace_api._SNAP["body"] = json.dumps(b2).encode()
    landing_v3._STATE["built"] = 0.0
    with ThreadPoolExecutor(max_workers=200) as pool:
        list(pool.map(lambda _: landing_v3.page(srv, JOIN), range(200)))
    wait_builder()
    new = landing_v3.page(srv, JOIN)
    assert len(builds) == 1 and new is not a and b"Zeta" not in new and landing_v3.page(srv, JOIN) is new


def test_a_landing_v3_failure_serves_the_previous_v3_page(srv, monkeypatch):
    monkeypatch.setattr(landing_v3, "page", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    body = server.landing_v3_page()
    assert b'data-v3-page="landing"' in body and b"/v3/landing.css" in body


def test_seo_tags_use_the_configured_public_url_only(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    s = build(srv).decode("utf-8")
    assert "og:image" not in s and "canonical" not in s and 'property="og:title"' in s and "summary_large_image" in s
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com/")
    s = build(srv).decode("utf-8")
    assert '<link rel="canonical" href="https://cs2-scout.onrender.com/">' in s
    monkeypatch.setenv("PUBLIC_URL", "javascript:alert(1)")
    assert "og:image" not in build(srv).decode("utf-8")


def test_seo_tags_prefer_the_landing_domain_and_keep_public_url_for_sign_in(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com/")
    s = build(srv).decode("utf-8")
    for tag in ('<link rel="canonical" href="https://propsgrader.com/">', '<meta property="og:image" content="https://propsgrader.com/landing/og.png">'):
        assert tag in s, tag
    assert "cs2-scout" not in s.split("</head>")[0]
    monkeypatch.delenv("LANDING_PUBLIC_URL")
    landing_v3.reset()
    a = landing_v3.page(srv, JOIN)
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com")               # a changed setting rebuilds the cached page
    assert b'href="https://propsgrader.com/"' in landing_v3.page(srv, JOIN) and b'href="https://propsgrader.com/"' not in a


def test_og_image_is_served_as_png(srv, monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        st, h, body = get(httpd.server_address[1], "/landing/og.png")
        assert st == 200 and h["content-type"] == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        httpd.shutdown()


def test_a_failed_sign_in_sends_a_fixed_code_never_its_text(srv, monkeypatch):
    msgs = {"Sign-in expired - please try again": "expired",
            "Whop sign-in failed at the token step (400: invalid_grant)": "whop",
            "Whop didn't return your account - please try again": "no_account", "anything else": "failed", None: "failed"}
    for m, code in msgs.items():
        assert whop_auth.error_code(m) == code and code in whop_auth.SIGNIN_ERROR_CODES
    js = landing_v3.ASSETS["PAGE_JS"].read_text(encoding="utf-8")
    assert "el.textContent = code" not in js and "hasOwnProperty.call(msg, code) ? msg[code]" in js


# ------------------------------------------------------------------------------------------- the CS2 map module (HLTV, live)
from datetime import date  # noqa: E402

MOVER_MAPS = {"Mirage": 21, "Dust 2": 18, "Inferno": 16, "Ancient": 11, "Nuke": 8, "Anubis": 5, "Overpass": 20}   # Overpass: outside the pool
EARLY_MAPS = {"Mirage": 14, "Inferno": 12, "Nuke": 9, "Ancient": 7, "Anubis": 6}                                   # 3 pool maps with 8+


def map_rows(spec, today=None):
    """Real-shaped hltv_maps rows (name, date, matchid, k, d, hs, own, opp, rating, adr, kast), newest first, all inside 12 months."""
    today = today or date.today()
    out, i = [], 0
    for name, n in spec.items():
        for _j in range(n):
            k = 12 + (i * 7) % 11
            own, opp = (13, 5 + i % 8) if i % 3 else (9 + i % 4, 13)
            out.append((name, (today - timedelta(days=2 + (i * 3) % 340)).isoformat(), 9000 + i, k, 10 + i % 7, k // 2, own, opp,
                        round(0.9 + (i % 5) * 0.06, 2), 70.0 + i % 20, 68.0 + i % 9))
            i += 1
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def map_totals(spec):
    by = {}
    for r in map_rows(spec):
        t = by.setdefault(r[0], [0, 0])
        t[0] += r[3]
        t[1] += 1
    return by


def pm_board():
    """board() + a sooner lettered kills row whose HLTV maps fall short (Early), a PrizePicks line on Mover's kills, an unlettered
    sooner kills row (NoLetter2) and a sooner lettered headshots row (never a kills candidate)."""
    b = board()
    at = landing_v3._ts(b["board_at"])
    s = at + timedelta(hours=2)
    b["records"] += [rec("Early", "kills", 28.5, "underdog", .600, "B", s - timedelta(minutes=30)),
                     rec("Mover", "kills", 31.5, "prizepicks", .560, "B", s, side="under"),
                     rec("NoLetter2", "kills", 25.5, "underdog", .505, None, s - timedelta(minutes=50)),
                     rec("HsOnly", "headshots", 10.5, "underdog", .600, "B", s - timedelta(minutes=40))]
    return b


@pytest.fixture
def maps_loader(monkeypatch):
    import hltv_maps
    calls = []
    data = {"Mover": map_rows(MOVER_MAPS), "Early": map_rows(EARLY_MAPS), "Zeta": map_rows(MOVER_MAPS)}

    def loader(nick, team, since):
        calls.append(nick)
        rows = data.get(nick)
        return (77000 + len(nick), [r for r in rows if r[1] >= since]) if rows else (None, [])
    hltv_off(monkeypatch, loader)
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (list(hltv_maps.ACTIVE_DUTY), "constant"))
    loader.calls = calls
    loader.data = data
    return loader


def pool_html(page):
    m = re.search(r'(?s)<div class="ipanel rv">\s*<div class="panel" aria-hidden="true">\s*<p class="pnh"><span>[^<]+ · Kills per map, past year</span>.*?<p class="sr-only">[^<]*</p>', page.decode("utf-8"))
    return m.group(0) if m else None


def bars(pool):
    return [(n, v) for n, v in re.findall(r'<li(?: class=top)?><span>([^<]+)</span><span class="kb"><span><i style="[^"]+"></i></span></span><b>([^<]+)</b></li>', pool)]


def test_map_module_renders_the_rule_subject_from_hltv_totals(srv, monkeypatch, maps_loader):
    seed(srv, monkeypatch, pm_board())
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    assert pool is not None and "<span>Mover · Kills per map, past year</span>" in pool
    assert maps_loader.calls == ["Early", "Mover"]                    # soonest lettered kills first; Early falls short; Zeta never read
    tot = map_totals(MOVER_MAPS)
    import hltv_maps
    inpool = {hltv_maps.display(k) for k in hltv_maps.ACTIVE_DUTY}
    played = sorted((nm for nm in tot if nm in inpool and tot[nm][1] >= landing_v3.POOL_MIN_N), key=lambda nm: (-tot[nm][1], -tot[nm][0] / tot[nm][1], nm))
    want = sorted(((nm, tot[nm][0] / tot[nm][1]) for nm in played[:landing_v3.POOL_SHOW]), key=lambda x: (-x[1], x[0]))
    assert landing_v3.POOL_SHOW == 7 and len(want) >= 4                 # the played pool, up to 7 maps (critic round 6)
    assert bars(pool) == [(nm, f"{v:.1f}") for nm, v in want]          # pool maps by maps played, drawn highest kills/map first
    assert pool.count("<li class=top>") == 1 and "scaleX(1.000)" in pool
    assert "Example: Mover, HLTV kills per map over the past year: " in pool


def test_map_module_pool_is_the_sites_current_pool(srv, monkeypatch, maps_loader):
    import hltv_maps
    pool7 = ["ancient", "anubis", "dust2", "mirage", "nuke", "overpass", "train"]
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (pool7, "recent"))
    seed(srv, monkeypatch, pm_board())
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    # every played pool map with 8+ maps (up to 7, critic round 6); Inferno is outside this pool
    assert sorted(n for n, _v in bars(pool)) == sorted(["Mirage", "Overpass", "Dust 2", "Ancient", "Nuke"]) and "Inferno" not in pool


def test_map_module_visitor_request_never_reads_hltv(srv, monkeypatch, maps_loader):
    seed(srv, monkeypatch, pm_board())
    first = build(srv)
    assert pool_html(first) is None and maps_loader.calls == []
    assert "Built for CS2, not bolted on." in first.decode()
    landing_v3.build(srv, JOIN, fetch=True)
    n = len(maps_loader.calls)
    with ThreadPoolExecutor(max_workers=200) as pool:
        pages = list(pool.map(lambda _: build(srv), range(200)))
    assert len(maps_loader.calls) == n and all(pool_html(p) for p in pages)


def test_map_module_reads_are_bounded_and_single_flight(srv, monkeypatch, maps_loader):
    b = pm_board()
    at = landing_v3._ts(b["board_at"])
    b["records"] += [rec(f"Extra{i}", "kills", 20.5, "underdog", .6, "B", at + timedelta(minutes=10 + i)) for i in range(20)]
    seed(srv, monkeypatch, b)
    assert pool_html(landing_v3.build(srv, JOIN, fetch=True)) is None
    assert len(maps_loader.calls) == landing_v3.POOL_TRIES
    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(lambda _: landing_v3.build(srv, JOIN, fetch=True), range(50)))
    assert len(maps_loader.calls) == len(set(maps_loader.calls))


@pytest.mark.parametrize("case", ["error", "none", "no_letter", "saved_board", "stale_read", "short"])
def test_map_module_is_omitted_cleanly(srv, monkeypatch, maps_loader, case):
    import hltv
    import hltv_maps
    b = pm_board()
    calls = 0
    if case == "error":
        def down(*a):
            nonlocal calls
            calls += 1
            raise RuntimeError("db down")
        monkeypatch.setattr(hltv_maps, "LOADER", down)
    elif case == "none":
        maps_loader.data.clear()
    elif case == "no_letter":
        for r in b["records"]:
            if r["market"]["stat"] == "kills":
                r["scout"]["grade"] = None
    elif case == "short":
        for k in ("Mover", "Zeta"):
            maps_loader.data[k] = map_rows(EARLY_MAPS)
    seed(srv, monkeypatch, b if case != "saved_board" else None)
    if case == "saved_board":
        workspace_api._SNAP["body"] = None
    page = landing_v3.build(srv, JOIN, fetch=True)
    if case == "stale_read":
        assert pool_html(page) is not None
        maps_loader.data.pop("Zeta")
        for k in list(hltv._META):
            hltv._META[k]["as_of"] = time.time() - (landing_v3.POOL_MAX_AGE_H * 3600 + 60)
        page = landing_v3.build(srv, JOIN, fetch=True)
    s = page.decode("utf-8")
    if case == "error":
        assert landing_v3.POOL_TRIES > 1 and calls == 1
    assert pool_html(page) is None and 'class="kbars"' not in s and not re.search(r"\{\{[A-Z_0-9]+\}\}", s)
    assert 'class="browser"' in s
    if case == "saved_board":                                            # one-app saved board, no panel: no Inside section
        assert 'id="inside"' not in s
    else:
        assert "Built for CS2, not bolted on." in s


def test_map_module_never_shows_zero_for_a_missing_value(srv, monkeypatch, maps_loader):
    import hltv
    import hltv_maps
    doc = hltv_maps.build(map_rows(MOVER_MAPS), date.today(), 77005)
    win = doc["windows"]["12m"]["maps"]
    win["mirage"]["kills"] = None
    win["dust2"] = dict(win["dust2"], maps=0, kills=0)
    win["anubis"] = dict(win["anubis"], maps=9, kills=0)
    hltv._remember(hltv_maps._key("Mover", None), dict(doc, state="ok"))
    seed(srv, monkeypatch, pm_board())
    assert pool_html(landing_v3.build(srv, JOIN, fetch=False)) is None   # only Inferno, Ancient, Nuke remain: under 4, no panel
    hltv._remember(hltv_maps._key("Mover", None), dict(doc, state="ok"))
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "overpass"], "match"))
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=False))
    assert sorted(n for n, _v in bars(pool)) == ["Ancient", "Inferno", "Nuke", "Overpass"]
    assert not re.search(r"<b>0(\.0+)?</b>", pool)


def test_template_carries_no_hardcoded_player_stats():
    raw = landing_v3.TEMPLATE.read_text(encoding="utf-8")
    for name in ("POOL", "GAP", "PHONE", "FRAME"):
        blk = re.search(rf"(?s)<!--if:{name}-->(.*?)<!--/if:{name}-->", raw).group(1)
        bare = re.sub(r"\{\{[A-Z_0-9]+\}\}", "", blk)
        bare = re.sub(r'(style|class|id|viewBox|d|x|y|width|height|rx)="[^"]*"', "", bare)
        bare = bare.replace("Maps 1-2", "").replace("CS2", "").replace("9:41", "").replace("5G", "").replace("12 months", "")
        assert not re.search(r"\d", re.sub(r"<[^>]+>", "", bare)), (name, re.findall(r"[^<>]*\d[^<>]*", re.sub(r"<[^>]+>", "|", bare))[:3])


def test_map_module_survives_an_idle_rebuild_after_the_hltv_cache_lapses(srv, monkeypatch, maps_loader):
    import hltv
    seed(srv, monkeypatch, pm_board())
    a = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    n = len(maps_loader.calls)
    hltv._CACHE.clear()
    assert pool_html(build(srv)) == a and len(maps_loader.calls) == n


def test_map_module_pool_reads_the_rows_own_match_id(srv, monkeypatch, maps_loader):
    import hltv_maps
    b = pm_board()
    for r in b["records"]:
        if r["player"]["name"] == "Mover":
            r["offer"]["offer_id"] = f"id-{r['offer']['platform']}"
    seen = []
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: seen.append(mid) or (list(hltv_maps.ACTIVE_DUTY), "match"))
    seed(srv, monkeypatch, b)
    monkeypatch.setitem(workspace_api._SNAP, "index", {("underdog", "id-underdog"): {"match_id": "2400644", "nick": "Mover"}})
    assert pool_html(landing_v3.build(srv, JOIN, fetch=True)) is not None
    assert seen[-1] == "2400644"


def test_map_module_never_picks_a_match_that_has_locked(srv, monkeypatch, maps_loader):
    b = pm_board()
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    b["board_at"] = iso(old)
    for r in b["records"]:
        if r["player"]["name"] in ("Early", "Mover", "Zeta"):
            r["event"]["lock_utc"] = r["event"]["start_utc"] = iso(old + timedelta(hours=1))
    seed(srv, monkeypatch, b)
    assert pool_html(landing_v3.build(srv, JOIN, fetch=True)) is None and maps_loader.calls == []


def test_inside_keeps_the_cs2_row_as_text_when_only_the_gap_panel_exists(srv, monkeypatch):
    seed(srv, monkeypatch, board())                                       # no HLTV here: row B has no panel
    s = build(srv).decode("utf-8")
    assert 'id="inside"' in s and 'href="#inside"' in s and "Built for CS2, not bolted on." in s and '<ul class="kbars">' not in s
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    assert ".motion .icol:has(.rv) .itxt{opacity:0" in css and ".icol.solo>.itxt{" in css and '<div class="icol solo">' in s
    # brief 6 / rulebook 7.5 (critic round 6): two alternating rows, text 5 + panel 7, then panel 7 + text 5, 96 px apart
    assert ".ipair{display:grid;row-gap:96px;margin-top:64px}" in css and ".icol+.icol>.ipanel{grid-column:1 / span 7}" in css


def test_record_caption_ties_to_the_proof_number_and_names_the_missing_sports(srv, monkeypatch):
    """Critic round 6: the table caption prints the proof strip's decided count; pushes are labelled not counted; the board's sports
    that the record does not show get one plain line (never a 0 / 0 row, never a sport whose read merely failed)."""
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    rec = block(s, 'id="record"')
    n = re.findall(r'data-to="(\d+)"', s)[1]                                 # [0] is +46U, [1] the record's decided count
    assert f"<caption>{int(n):,} decided picks, by sport</caption>" in rec and "Every decided pick" not in rec
    assert '<th scope="col" class="nc">Push<span class="nc-d"> · </span><span class="nc-s">not counted</span></th>' in rec
    rec_sp = {k: {"totals": {"wins": 3, "losses": 2}} for k in ("cs2", "lol")}
    assert landing_v3.ledger_gap(rec_sp, ["cs2", "lol", "soccer", "tennis"]) == "Soccer and tennis are graded on the board, not on the public record yet."
    assert landing_v3.ledger_gap(rec_sp, ["cs2", "tennis"]) == "Tennis is graded on the board, not on the public record yet."
    assert landing_v3.ledger_gap(rec_sp, ["cs2", "lol", "nba"]) == ""                    # nba's read failed: an outage, never named
    rec_sp["nfl"] = {"totals": {"wins": 0, "losses": 0, "pushes": 1}}
    assert landing_v3.ledger_gap(rec_sp, ["cs2", "nfl"]) == "NFL has no decided picks yet."
    assert landing_v3.ledger_gap({}, ["soccer"]) == ""                                   # no record at all: no table, no line


def test_a_sport_with_nothing_decided_is_left_out_never_zero(srv, monkeypatch):
    seed(srv, monkeypatch, board(), recs=False)
    for sp, t in (("cs2", (100, 90, 2)), ("lol", (50, 40, 1)), ("nba", (300, 280, 3)), ("nfl", (0, 0, 0))):
        srv._store_result(landing_v3.record_key(sp), 200, json.dumps(record_doc(sp, *t)).encode(), 600, 600)
    s = build(srv).decode("utf-8")
    rec = block(s, 'id="record"')
    assert "<span>NFL</span>" not in rec and ">0<" not in rec and 'data-to="860"' in s


def test_phone_why_list_is_the_briefs_four_rows_never_a_stake_or_verdict():
    """Critic round 6 (access rule 0.4) removes round 5's Stake / Verdict row: a members-only stake on a live row is product given
    away, and "Below break-even" ended the free showcase on "skip this". The Why list is exactly Projection, Last 10 at this line,
    Break-even for the entry and the other app's line, in that order, in the list and in the sr text."""
    def row(p, be, units=None):
        sc = {"side": "over", "p_side": p, "grade": "B-", "projection": 33.8}
        if units is not None:
            sc["units"] = units
        return [([{"event": {"label": "A vs B"}, "player": {"name": "X"}, "market": {"stat_label": "Kills", "period_label": "Maps 1-2"},
                   "offer": {"platform": "prizepicks", "platform_label": "PrizePicks", "line": 32.5}, "scout": sc,
                   "parity": {"be": be, "entry": "PrizePicks Power 2-pick"}}], True, False)]
    keys = lambda ph: [k for k, _v in re.findall(r"<li(?: class=oa)?><span>([^<]+)</span><b>([^<]+)</b></li>", ph["PH_WHY"])]   # noqa: E731
    for args in ((.56, .577), (.56, .577, 0.0), (.62, .577, 1.5), (.62, .577)):
        ph = landing_v3.phone(row(*args))
        assert keys(ph) == ["Projection", "Break-even, Power 2-pick"]
        for bad in ("Verdict", "Stake", "below break-even", "Below break-even", "1.5U", "0U"):
            assert bad not in ph["PH_WHY"] and bad not in ph["PH_SR"]


def test_hero_shows_up_to_six_rows_in_rule_order():
    """Critic round 6: the board reaches the phone (6 rows, one per player, rule order); a thinner board is held to a 48 px phone
    overhang by the stretched browser column in CSS."""
    assert landing_v3.HERO_ROWS == 6
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    assert ".pcol{grid-area:1 / 1;align-self:stretch;display:flex;flex-direction:column;min-width:0;margin:0 16px 48px 0}" in css
    assert ".pcol>.browser{flex:1}" in css and ".ph-why li.oa{display:none}" in css


def test_members_heading_says_in_their_words_only_over_member_reviews(srv, monkeypatch):
    """Critic round 6: with no review card the band holds Prop Grader's own post alone, so the H2 never labels it a member's words."""
    seed(srv, monkeypatch, board(), rv={"summary": {"count": 0}, "reviews": []})
    m = block(build(srv).decode("utf-8"), 'id="members"')
    assert '<h2 id="rv-title">Members.</h2>' in m and "in their words" not in m and 'class="rqs' not in m
    assert '<p class="st-k">From Prop Grader on X</p>' in m and "Shared by Prop Grader on X." in m
