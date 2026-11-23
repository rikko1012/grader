"""The v3 signed-out landing (landing_v3.py + web/v3/landing.html): the Prop Grader page built to the owner's Nov 23 brief and
DESIGN_RULES.md. No network, no production data: real-shaped board / record / review documents built here.

- view-only (owner access rule): every product picture (browser, phone, Inside panels, Slip stage) is aria-hidden or role="img" and
  holds no link, button, input, tabindex or toggle; the page's only buttons are the five FAQ triggers;
- every number comes from the shared bodies (board snapshot, /api/record, /api/reviews); the only typed-in ones are the owner's
  attributed +46U post and 6-man story; the template carries no player stat, percentage or price;
- frame rows: only markets graded B or better whose model estimate clears their own entry's break-even, ranked grade tier then
  estimate, never padded (fewer than FRAME_MIN live qualifiers: the dated saved board); lines printed raw; the phone reads the first
  row and leaves a missing value out (never 0); the line-gap panel is a real two-app row (break-even clearers first) or absent; the
  Slip stage has one real CS2 card and labelled placeholders (role + an untruncated 'placeholder' label) whose blurred values are
  glyphs (no real numbers in the source);
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
                '<div class="ss-fig" role="img"'):
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
def test_frame_rows_clear_their_break_even_and_rank_by_grade(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<div class="br"><span class="b-who"><b>([^<]+)</b>', s)
    assert rows == ["Mover", "Zeta"]                                      # B+ before B; Gap (B-) is never padded in
    assert "NoLetter" not in s.split('class="bt"')[1].split("</div></div></div>")[0] and ">Past<" not in s
    assert '<p class="bfoot">Showing 2 of 3 graded props on the board.</p>' in s
    mover = re.search(r'(?s)<div class="br"><span class="b-who"><b>Mover</b>.*?</div>', s).group(0)
    assert "<b>64.0%</b>" in mover and 'scaleX(0.640)' in mover
    assert '<span class="b-pk">Under</span>' in mover and '<i class="gb gc">B+</i>' in mover
    cap = re.search(r'<p class="cap hero-cap">([^<]+)</p>', s).group(1)
    today = landing_v3._date(landing_v3._ts(board()["board_at"]))
    assert cap == f"Example rows from the public sample board, {today}. A model estimate is a chance, not a promise."
    sr = html.unescape(re.search(r'<p class="sr-only">(Example rows from the member board:[^<]+)</p>', s).group(1))
    assert sr.startswith("Example rows from the member board: Mover, Kills Maps 1-2, Underdog 32.5, Under, model estimate 64.0%, grade B+;")


def test_frame_rule_tier_before_estimate_break_even_and_lines_by_app(srv, monkeypatch):
    b = board()
    for r in b["records"]:
        if r["player"]["name"] == "Gap":
            r["scout"]["grade"] = "A-" if r["offer"]["platform"] == "underdog" else None    # A- at 58.3% outranks B+ at 64.0%
        if r["player"]["name"] == "Zeta":
            r["scout"]["p_side"] = .52                                    # below its own 53.45% break-even: left out
    b["records"].append(rec("NoBe", "kills", 22.5, "underdog", .700, "A", datetime.now(timezone.utc) + timedelta(hours=3), be=None))
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert re.findall(r'<div class="br"><span class="b-who"><b>([^<]+)</b>', s) == ["Gap", "Mover"]   # no break-even = never shown
    gap = re.search(r'(?s)<div class="br"><span class="b-who"><b>Gap</b>.*?</div>', s).group(0)
    assert '<span class="b-ln"><span>PP 13.5</span><span>UD 12.5</span></span>' in gap   # each app's line, raw, PrizePicks first
    assert "<b>58.3%</b>" in gap and '<i class="gb gc">A-</i>' in gap


def test_too_few_live_qualifiers_show_the_dated_saved_board_but_inside_stays_live(srv, monkeypatch):
    b = board()
    b["records"] = [r for r in b["records"] if r["player"]["name"] != "Zeta"]                    # one qualifier left (Mover)
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "public sample board, Nov 22, 2026." in s and ">Mover<" not in s.split('class="bt"')[1].split("</div></div></div>")[0]
    assert "Gap · Headshots · Maps 1-2" in s                              # the gap panel still reads the live board
    assert "2 CS2 props are graded on the board right now." in s          # the live count, not the saved board's
    assert 'class="bfoot"' not in s                                       # the saved file is not a board: no count under it


def test_frame_picks_in_rule_order_never_the_boards_best_grades(srv, monkeypatch):
    """Owner access rule: the free page is not a top-picks feed. Qualifiers are chosen in the neutral rule order (gaps, moved, the
    soonest) and only then shown best grade first, so a later A+ never jumps into the frame over four sooner qualifiers."""
    b = board()
    s0 = datetime.now(timezone.utc) + timedelta(minutes=30)
    b["records"] += [rec(f"Soon{i}", "kills", 20.5, "underdog", .600, "B", s0 + timedelta(minutes=i)) for i in range(3)]
    b["records"].append(rec("Late", "kills", 20.5, "underdog", .800, "A+", datetime.now(timezone.utc) + timedelta(hours=9)))
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<div class="br"><span class="b-who"><b>([^<]+)</b>', s)
    assert "Late" not in rows and rows[0] == "Mover" and len(rows) == 4    # Mover (moved) leads by rule and by grade; then the soonest B


def test_lines_print_as_the_member_board_prints_them(srv, monkeypatch):
    b = board()
    b["records"][0]["offer"]["line"] = 30.0
    b["records"][3]["offer"]["line"] = 32
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "<span>UD 30</span>" in s and "<span>UD 32</span>" in s and "UD 30.0" not in s


def test_phone_reads_the_first_row_and_hides_missing_values(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    ph = block(build(srv).decode("utf-8"), '<div class="phone"', '<p class="cap hero-cap">')
    assert "<p class=\"ph-who\">Mover</p>" in ph and '<p class="ph-prop">Under 32.5 Kills · Maps 1-2</p>' in ph
    assert '<p class="ph-m">Underdog · Alpha vs Beta</p>' in ph
    assert '<b>64.0%</b><i class="gb gc">B+</i>' in ph and "Model estimate at the 32.5 line</p>" in ph and "exactly" not in ph
    why = re.findall(r"<li><span>([^<]+)</span><b>([^<]+)</b></li>", ph)
    assert why == [("Last 10 at this line", "4 / 10 Under"), ("Break-even, standard 2-pick", "53.4%")]   # no projection, no other app
    assert "Projection" not in ph and ">0<" not in ph and "0.0 vs" not in ph
    assert '<div class="ph-add">Add to Slip Studio</div>' in ph and "<button" not in ph and "<a " not in ph


def test_phone_lists_projection_and_the_other_apps_line_when_the_row_has_them(srv, monkeypatch):
    b = board()
    for r in b["records"]:
        if r["player"]["name"] == "Gap":
            r["scout"]["p_side"] = .7 if r["offer"]["platform"] == "underdog" else r["scout"]["p_side"]
            r["scout"]["grade"] = "A" if r["offer"]["platform"] == "underdog" else None
            r["scout"]["projection"] = 13.9
    seed(srv, monkeypatch, b)
    ph = block(build(srv).decode("utf-8"), '<div class="phone"', '<p class="cap hero-cap">')
    why = dict(re.findall(r"<li><span>([^<]+)</span><b>([^<]+)</b></li>", ph))
    assert why["Projection"] == "13.9 vs 12.5" and why["Prizepicks line"] == "13.5" and "Gap" in ph


def test_line_gap_panel_is_a_real_two_app_row_else_left_out(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    p = block(s, '<div class="irow">', '<div class="irow flip">')
    assert "Gap · Headshots · Maps 1-2" in p
    assert '<div class="lt"><span>Prizepicks</span><b>13.5</b></div><div class="lt"><span>Underdog</span><b>12.5</b></div>' in p
    assert '<p class="gd"><b>1.0</b><span>Over 12.5 on Underdog</span></p>' in p and "58.3% model estimate" in p
    b = board()
    b["records"] = [r for r in b["records"] if r["player"]["name"] != "Gap"]
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert "When the apps disagree" not in s and 'class="lt"' not in s and "{{" not in s


def test_slip_stage_has_one_real_row_and_placeholders_with_no_real_numbers(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    stage = block(s, '<div class="stage" id="ss">', '<p class="cap">Example animation.')
    cards = re.findall(r'<div class="sc(?: ph)?" data-i="\d">(.*?)</div>', stage)
    assert len(cards) == 4 and "<b>Mover</b>" in cards[0] and "<b>64.0%</b>" in cards[0]     # the highest real estimate
    assert stage.count('<div class="sc ph" data-i=') == 3 and "ss-cta" not in s and 'id="ss-cta"' not in s
    for c, who in zip(cards[1:], ("NBA guard", "LoL mid", "Tennis")):
        assert f'<b>{who}<em class="phl">placeholder</em></b>' in c and '<span class="sv bl"><b>00.0%</b><i class="gb gg">B</i></span>' in c and 'class="mt"' not in c
    assert stage.count('class="slot"') == 4 and '<span class="bl">00.0% vs 00.0%</span>' in stage
    blurred = re.findall(r'<span class="(?:sv )?bl">(.*?)</span>', stage)
    assert blurred and all(not re.search(r"[1-9]", re.sub(r"<[^>]+>", "", x)) for x in blurred)    # glyphs only behind every blur
    assert 'role="img" aria-label="Example animation: four graded props from different sports move from the board into a 4-pick slip."' in stage
    assert "Mover is from the public sample board; the other cards are placeholders" in s


def test_view_only_nothing_inside_a_frame_is_usable(srv, monkeypatch):
    """Owner access rule: no link, button, input, tabindex, toggle or handler inside any product picture."""
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    fs = frames(s)
    assert len(fs) >= 4                                                  # browser, phone, gap panel, Slip figure (no HLTV here)
    for f in fs:
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
    assert "%" not in text(rec.encode()) and "From the public record, Nov 22, 2026. Each sport is counted by its own written rule." in text(rec.encode())


def test_proof_strip_is_live_attributed_and_the_score_never_ticks(srv, monkeypatch):
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com")
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    proof = block(s, '<section class="proof"')
    assert '<span class="tk" aria-hidden="true" data-to="46" data-pre="+" data-suf="U" style="min-width:4ch">+46U</span><span class="sr-only">+46U</span>' in proof
    assert '<p class="pl">&ldquo;+46U on Grader 4 mans in the past week&nbsp;…&rdquo;</p>' in proof and "only been live" not in s
    assert "Trey on X, Nov 20, 2026" in proof and 'href="/record">propsgrader.com/record<' in proof and 'href="/reviews">propsgrader.com/reviews<' in proof
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
    assert s.count(f'href="{JOIN.replace("&", "&amp;")}"') == 6


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
    assert len(quotes) == 3                                              # 2 member reviews (the 8-word cut stub is skipped) + the story
    assert quotes[0].startswith("The cards explain every lean") and quotes[1] == "Reasons behind each prop are great for me"
    caps = re.findall(r"<figcaption>([^<]+)</figcaption>", m)
    assert caps[:2] == ["Paying member, Nov 16, 2026."] * 2 and not any(n in m for n in ("longform", "slipper", "earner"))
    assert "Hit big" not in m and "profitable" not in m and "YALL" not in m and "guaranteed" not in m and "…" not in m
    assert m.count("Paying member, Nov 16, 2026") == 2 and "Paying member ·" not in m
    assert '<p class="rvh"><a class="tl" href="/reviews"><span class="star" aria-hidden="true">★</span>&nbsp;4.8 from 12 reviews, all published</a></p>' in m
    assert m.index('class="rqs"') < m.index('class="story"')            # members' own words lead; the owner's post follows
    assert "A member ran his first ever 6&nbsp;man with Grader and cashed." in m
    assert "Shared by Prop Grader on X. One member, one night: a result, not a promise." in m


def test_review_order_cut_rule_and_curly_apostrophes():
    rv = {"summary": {"count": 3}, "reviews": [
        review(1, "a", "Love the stats and the reasons. Paid me big last week, money every day."),            # cut, 6 words left: skipped
        review(2, "b", "The breakdowns on every card are clear and I can read the map data quickly before I build. "
                       "Made money all week with it."),                                                    # cut, 18 words left
        review(3, "c", "You've got the stats right there, it's the best board."),                          # complete
    ]}
    got = landing_v3.featured(rv)
    assert [r["id"] for r, _q, _c, _o in got] == [3, 2] and [c for _r, _q, c, _o in got] == [False, True]
    _h, cards, _n, _a = landing_v3.wall(rv)
    assert "You’ve got the stats right there, it’s the best board." in cards and "&#x27;" not in cards
    assert 'build.&nbsp;…&rdquo;' not in cards and "build. …&rdquo;" in cards and 'Paying member, Nov 16, 2026. <a class="tl" href="/reviews">Full review</a>' in cards
    assert cards.count("Full review") == 1


def test_rating_shows_only_from_ten_reviews(srv, monkeypatch):
    few = dict(REVIEWS, summary={"count": 4, "average": 5.0})
    seed(srv, monkeypatch, board(), rv=few)
    s = build(srv).decode("utf-8")
    assert "★" not in s and "4 reviews, all published" in s


def test_reviews_naming_the_old_brand_are_never_featured(srv, monkeypatch):
    rv = dict(REVIEWS, reviews=REVIEWS["reviews"] + [review(9, "brand", "The CS2 Scout cards explain every stat and the map data clearly")])
    seed(srv, monkeypatch, board(), rv=rv)
    assert "CS2 Scout" not in build(srv).decode("utf-8")


# ---------------------------------------------------------------------------------------------------------- copy + structure
SECTION_ORDER = ['class="nav"', 'class="hero"', 'class="proof"', 'id="how"', 'id="inside"', 'id="slip"', 'id="record"',
                 'id="members"', 'id="pricing"', 'id="faq"', 'id="final"', 'class="foot"']


def test_sections_follow_the_brief_order_and_wording(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    pos = [s.index(k) for k in SECTION_ORDER]
    assert pos == sorted(pos)
    t = text(s.encode())
    for line in ("For " + html.unescape(landing_v3._sport_sub(landing_v3.sports_on())), "Grade every prop before you lock it in.",
                 "Every PrizePicks and Underdog line gets a letter grade and a model estimate, with the reason in plain English.",
                 "Then $24.99/month. Cancel anytime.", "Open the board. Read the grade. Play your line.",
                 "What members see when they log in.", "When the apps disagree, you see it first.", "Built for CS2, not bolted on.",
                 "Slips, built from graded lines.",
                 "Saved before the game. Scored after.", "See every pick on the record", "Members and results.",
                 "The board is already graded.",
                 "Start with a free day.", "One plan with everything. Cancel anytime from your Whop account.",
                 "Your first day is free. Then $24.99/month until you cancel.", "Secure checkout on Whop.", "Questions.",
                 "Prop Grader gives chances, not certainties. Play only what you can afford to lose."):
        assert line in t, line
    assert s.count("<h1") == 1 and t.count("lock it in.") == 1 and '<h2 id="final-title">The board is already graded.</h2>' in s
    assert "six apps" not in t and 'class="sec why"' not in s and "By hand" not in t
    assert re.findall(r'<p class="eb">([^<]+)</p>', s) == ["How it works", "Inside Prop Grader", "Slip Studio", "The public record"]


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
    for frag in ("· ", " ·"):                                            # middle dots only inside product pictures
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
    assert len(btns) == 6 and {h for h, _t in btns} == {trial}          # nav, hero, Slip, pricing, final, phone bar (no stage CTA, D15)
    assert sorted(t for _h, t in btns) == sorted(["Try free", "Try free for 1&nbsp;day"] + ["Try Prop Grader free for 1&nbsp;day"] * 4)
    whop = re.findall(r'href="(https://whop\.com/[^"]+)"', s)
    assert set(whop) == {trial, week} and whop.count(week) == 1 and f'<a href="{week}">Weekly plan</a>: $9.99/week' in s
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
    want = sorted(((nm, tot[nm][0] / tot[nm][1]) for nm in ("Mirage", "Dust 2", "Inferno", "Ancient")), key=lambda x: (-x[1], x[0]))
    assert bars(pool) == [(nm, f"{v:.1f}") for nm, v in want]          # top 4 pool maps by maps played, drawn highest kills/map first
    assert pool.count("<li class=top>") == 1 and "scaleX(1.000)" in pool
    assert "Example: Mover, HLTV kills per map over the past year: " in pool


def test_map_module_pool_is_the_sites_current_pool(srv, monkeypatch, maps_loader):
    import hltv_maps
    pool7 = ["ancient", "anubis", "dust2", "mirage", "nuke", "overpass", "train"]
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (pool7, "recent"))
    seed(srv, monkeypatch, pm_board())
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    assert sorted(n for n, _v in bars(pool)) == sorted(["Mirage", "Overpass", "Dust 2", "Ancient"]) and "Inferno" not in pool


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
    assert ".motion .irow:has(.rv) .itxt{opacity:0" in css and ".irow.flip:not(:has(.ipanel)) .itxt" in css


def test_a_sport_with_nothing_decided_is_left_out_never_zero(srv, monkeypatch):
    seed(srv, monkeypatch, board(), recs=False)
    for sp, t in (("cs2", (100, 90, 2)), ("lol", (50, 40, 1)), ("nba", (300, 280, 3)), ("nfl", (0, 0, 0))):
        srv._store_result(landing_v3.record_key(sp), 200, json.dumps(record_doc(sp, *t)).encode(), 600, 600)
    s = build(srv).decode("utf-8")
    rec = block(s, 'id="record"')
    assert "<span>NFL</span>" not in rec and ">0<" not in rec and 'data-to="860"' in s
