"""The v3 signed-out landing (landing_v3.py + web/v3/landing.html, direction C "line terminal"). No network, no production data:
real-shaped board / record / review documents built here.

- every number comes from the shared bodies (board snapshot, /api/record, /api/reviews); none is typed into the template;
- the terminal picks rows by its stated rule and labels a board older than FRESH_MIN as "as of <time>", never "checked";
- member review text is escaped; the wall features by its stated rule (no photos, no money talk, no shouting);
- a missing input drops its block; the page never fails over one (the terminal falls back to the saved, dated board);
- a terminal row opens in place (all closed at first): chance at exactly the line, projection vs line, last 10, break-even and a link
  to the grade's tier on /record - never a tier hit rate beside a break-even; the gate counts the other letter-graded markets (live only);
- the record totals wins + losses (ties to the cards); soccer / tennis say "not on the public record yet"; reviews say where they were
  written, show no star histogram and quote product-use sentences only; the Slip Studio tile is a real slip in v3 markup;
- "Move the line" is drawn only when its formula reproduces the seed row's own chance; its cut points match grader.grade_from_p;
- the template's own text carries none of the owner's banned words and no static percentage;
- the request path never computes (cached bodies only) and 700 visitors at once get one body and one ETag.
Owner blueprint 2026-11-22 ("Prop Grader"): every primary button opens the 1-day free trial checkout, the price and cancel terms sit
under the hero button, a Weekly card links the weekly plan, the brand is Prop Grader, the proof tiles come from live data (the only
typed-in numbers are the owner's attributed +46U post and 6-man story), the FAQ answers the free day / bankroll / apps questions, and
a phone bar ("Try free for 1 day") never covers content."""
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


def test_terminal_rows_follow_the_stated_rule_with_real_values(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<li class="tr[^"]*".*?</li>', s, re.S)
    names = [re.search(r'class="who"[^>]*><b>([^<]+)<i', r).group(1) for r in rows]
    assert names == ["Gap", "Mover", "Zeta"]                         # two-app gaps, then moved lines, then the rest
    assert "NoLetter" not in s and "Past" not in s                      # no letter never leads; a locked match never shows
    gap = rows[0]
    assert 'data-p="58.3"' in gap and 'data-p="52.2"' in gap and "Line gap" in gap and ">13.5<" in gap
    assert "64.0" in rows[1] and "▲31.5" in rows[1] and ">Under<" in rows[1]
    assert "checked" not in s.split('id="term-at"')[1][:60] and 'data-at="' in s     # freshness is the browser's to say


def test_an_old_board_is_dated_never_checked(srv, monkeypatch):
    at = datetime(2026, 11, 19, 16, 20, tzinfo=timezone.utc)
    seed(srv, monkeypatch, board(at))
    s = build(srv).decode("utf-8")
    assert "as of Nov 19, 16:20 UTC" in s and "checked" not in text(s.encode()).split("CS2 board")[1][:80]
    assert 'data-state="dated"' in s


def test_ledger_and_proof_line_come_from_the_record_answers(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    t = text(build(srv))
    total = 100 + 90 + 50 + 40 + 300 + 280 + 80 + 70                   # decided picks (wins + losses), never the API's picks (+ pushes)
    assert f"{total:,} decided picks on the public record" in t and "since" not in t
    for w, l in ((100, 90), (50, 40), (300, 280), (80, 70)):
        assert f"{w:,} - {l:,}" in t
    assert "The cs2 rule." in t and "Second sentence" not in t          # the record's own rule, first sentence
    assert "As of Nov 22, 15:17 UTC" in t and "Why the rules differ" in t
    led = re.search(r'(?s)<section class="ledger.*?</section>', build(srv).decode()).group(0)
    assert "%" not in text(led.encode()) and "hit rate" not in led.lower() and "break-even" not in led.lower()   # a log, not a pitch
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    t = text(build(srv))
    assert "Soccer Graded on the board Not on the public record yet" in t and "Tennis Graded on the board" in t
    assert "across CS2, LoL, NBA and NFL" in t and "League" not in t.split("FAQ")[1].split("League of Legends")[0]
    assert "record yet. Soccer and tennis are graded on the board but not on the public record yet." in t or \
        "anyone can read it without an account. Soccer and tennis are graded on the board but not on the public record yet." in t
    monkeypatch.delenv("SCOUT_TENNIS_ENABLED")
    assert "Soccer is graded on the board but not on the public record yet." in text(build(srv))


def test_review_wall_escapes_and_features_by_rule(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    assert "<script>x</script>" not in s and "&lt;script&gt;x&lt;/script&gt;" in s
    for name in ("longform", "plain", "mixed"):                       # product-use reviews
        assert f"<b>{name}</b>" in s
    for name in ("slipper", "photo", "shout", "earner", "short"):     # money talk, photos, shouting, no product words
        assert f"<b>{name}</b>" not in s
    t = text(s.encode())
    assert "★ 4.8 · 12 member reviews" in t and "Read all 12" in t and "Paying member" in t
    assert "Written on the site's reviews page by paying members" in html.unescape(t) and "1 to 3 stars" not in t
    assert 'class="brk"' not in s                                       # no star histogram
    assert "Love the stats and reasons behind each prop. …" in t and "guaranteed" not in t      # the hype sentence is cut, marked
    assert s.count('<a href="/reviews">') >= 3                          # every card links to the reviews page
    assert "rvq" not in s and "carousel" not in s.lower()               # a static wall: no carousel, arrows or dots


def test_rating_shows_only_from_ten_reviews(srv, monkeypatch):
    few = dict(REVIEWS, summary={"count": 7, "average": 5.0, "breakdown": {"5": 7, "4": 0, "3": 0, "2": 0, "1": 0}})
    seed(srv, monkeypatch, board(), rv=few)
    t = text(build(srv))
    assert "7 member reviews" in t and "★ 5.0" not in t


def test_missing_inputs_drop_their_blocks_and_the_page_still_serves(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "0")
    monkeypatch.setattr(reviews, "page_on", lambda: False)
    s = build(srv).decode("utf-8")
    assert "/landing/board.webp" not in s and 'data-state="saved"' in s   # the saved board, dated, instead of live rows
    assert "as of Nov 22, 17:53 UTC" in s and 'data-at="2026-11-22T17:53:44+00:00"' in s
    assert "more letter-graded" not in s and "Open the full board, free for 1 day" in s      # no count off the saved board
    js = landing_v3.ASSETS["PAGE_JS"].read_text(encoding="utf-8")       # the browser keeps an old saved board relative, never a bare date
    assert "'Saved board from '" in js and "' days old'" in js
    assert 'id="record"' not in s and 'id="reviews"' not in s and "decided picks" not in s and "member reviews" not in s
    assert "{{" not in s and "<!--if:" not in s
    assert s.count(f'href="{JOIN.replace("&", "&amp;")}"') >= 4 and s.count('href="/auth/login"') >= 3


def test_ev_and_price_check_tiles_only_with_their_flags(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    assert "t-ev" not in s and "t-arb" not in s
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    s = build(srv).decode("utf-8")
    assert "t-ev" in s and "t-arb" in s


def test_template_text_has_no_banned_words_or_static_numbers():
    """The owner's ad-safe list, on the page's own words (template + CSS/JS), never on member text or live data values."""
    raw = landing_v3.TEMPLATE.read_text(encoding="utf-8")
    t = text(re.sub(r"\{\{[A-Z_0-9]+\}\}", " ", raw).encode()).lower()
    for bad in (r"guarantee", r"\block", r"win[- ]rate", r"accura", r"profit", r"\broi\b", r"\bbeat\b", r"\+ev\b", r"\bstake",
                r"bovada", r"thunderpick", r"free money", r"sure thing", r"can'?t lose", r"\bunits?\b", r"\d+(\.\d+)?\s*%",
                r"financial advice", r"not advice", r"\bheld\b", r"\bmaybe\b", r"provisional", r"unconfirmed", r"\$"):
        assert not re.search(bad, t), bad
    assert "realslip-pp" not in raw and "<script src" not in raw
    assert set(re.findall(r'<img src="([^"]+)"', raw)) <= {"/landing/realslip-scout.webp"} | {
        f"/v3/art/{m}.svg" for m in ("anubis", "dust2", "ancient", "cache")}
    assert raw.count('<span class="mark" aria-hidden="true"><i></i></span><b>Prop<em>Grader</em></b>') == 2   # header + footer


def test_an_idle_page_is_rebuilt_before_it_is_served(srv, monkeypatch):
    """No visitors for STALE_S: the next request gets a page rebuilt from cached bodies, never the old one (old rows, old as-of)."""
    seed(srv, monkeypatch, board(datetime(2026, 11, 19, 16, 20, tzinfo=timezone.utc)))
    a = landing_v3.page(srv, JOIN)
    assert b"as of Nov 19, 16:20 UTC" in a
    for _ in range(100):                                                # the first visit's background build
        if not landing_v3._BUILDER["busy"]:
            break
        time.sleep(0.05)
    workspace_api._SNAP["body"] = json.dumps(board(datetime(2026, 11, 22, 9, 5, tzinfo=timezone.utc))).encode()
    landing_v3._STATE["made"] = landing_v3._STATE["built"] = time.time() - landing_v3.STALE_S - 1
    b = landing_v3.page(srv, JOIN)
    assert b"as of Nov 22, 09:05 UTC" in b and b"Nov 19, 16:20" not in b


def test_page_bytes_are_one_object_per_build_and_request_path_never_computes(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    calls = []
    real = srv.shared
    monkeypatch.setattr(srv, "shared", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    a = landing_v3.page(srv, JOIN)
    b = landing_v3.page(srv, JOIN)
    assert a is b and b"Mover" in a
    for _ in range(50):                                                 # the background build (cached answers: same bytes, same object)
        if not landing_v3._BUILDER["busy"]:
            break
        time.sleep(0.05)
    assert landing_v3.page(srv, JOIN) is a


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def test_700_signed_out_visitors_get_one_body_and_one_etag(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        want = get(port, "/")[2]
        assert b'id="terminal"' in want and b"Mover" in want

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


def test_a_landing_v3_failure_serves_the_previous_v3_page(srv, monkeypatch):
    monkeypatch.setattr(landing_v3, "page", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    body = server.landing_v3_page()
    assert b'data-v3-page="landing"' in body and b"/v3/landing.css" in body


# ---------------------------------------------------------------------------------------------------- final: grafts and SEO
BANDS = [{"name": "B", "wins": 1137, "losses": 1026, "pushes": 19, "picks": 2163},
         {"name": "B+", "wins": 214, "losses": 192, "pushes": 5, "picks": 406}]


def seed_bands(srv):
    doc = record_doc("cs2", 100, 90, 2)
    doc["bands"] = BANDS
    srv._store_result(landing_v3.record_key("cs2"), 200, json.dumps(doc).encode(), 600, 600)


def wait_builder():
    for _ in range(100):
        if not landing_v3._BUILDER["busy"]:
            return
        time.sleep(0.05)


def test_rows_open_in_place_with_the_card_read_and_the_letters_own_record(srv, monkeypatch):
    b = board()
    b["records"][0]["scout"]["projection"] = 32.4                       # Zeta, kills 30.5
    seed(srv, monkeypatch, b)
    seed_bands(srv)
    s = build(srv).decode("utf-8")
    rows = re.findall(r'<li class="tr[^"]*".*?</li>', s, re.S)
    assert all('aria-expanded="false"' in r and ' hidden>' in r for r in rows)        # closed: the hero shows its rows
    assert 'Model estimate at exactly <span data-k="line">12.5</span>' in rows[0]
    assert 'data-k="p">58.3<' in rows[0]
    assert 'data-k="l10">6 of 10<' in rows[0] and 'Break-even, <span data-k="entry">Underdog standard 2-pick</span>' in rows[0]
    assert "Projection" not in rows[0]                                  # no projection on the record: no "why" line, never a guess
    zeta = rows[2]
    assert "Projection <b>32.4</b> vs line" in zeta
    assert 'See the <b data-k="g">B</b> tier on the public record, losses included' in zeta and '<a href="/record#cs2">' in zeta
    assert "1,137" not in s and "data-band" not in s and "On the record, CS2 calls graded" not in s   # no tier W-L beside a break-even
    assert "Graded <span data-k=\"graded\">Nov 22, 10:01 UTC</span>" in s
    for r in rows:                                                     # the member card's stake never shows on the public page
        assert "Stake" not in r and "0.5U" not in r and "units" not in r.lower()


def test_the_board_gate_counts_the_other_graded_markets_live_only(srv, monkeypatch):
    b = board()
    s0 = datetime.now(timezone.utc) + timedelta(hours=3)
    b["records"] += [rec(f"Extra{i}", "kills", 20.5, "underdog", .6, "B", s0) for i in range(7)]
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    assert len(re.findall(r'<li class="tr[^"]*"', s)) == 4
    # 3 + 7 graded markets - 4 shown (desktop); phones show 3 rows, so their count is 1 higher
    assert '<span class="n-d">6</span><span class="n-m">7</span> more letter-graded CS2 props on this board' in s
    assert "Open the full board, free for 1 day" in s
    assert re.search(r'<a class="gate" href="([^"]+)"', s).group(1) == JOIN.replace("&", "&amp;")


def test_move_the_line_only_when_the_formula_reproduces_the_row(srv, monkeypatch):
    import grader
    for p in (0.5, 0.549, 0.55, 0.569, 0.57, 0.629, 0.63, 0.679, 0.68, 0.739, 0.74, 0.799, 0.8, 0.9):
        got = next((g for c, g in landing_v3.CUTS if p >= c), "C")
        assert got == grader.grade_from_p(p), p                        # the widget's cut points are the grader's
    b = board()
    lam, line = 32.4, 30.5
    p = landing_v3.kills_chance(line, lam)
    b["records"][0]["scout"].update(side="over", p_side=round(p, 4), grade="B", projection=lam)
    seed(srv, monkeypatch, b)
    seed_bands(srv)
    s = build(srv).decode("utf-8")
    assert 'id="gw"' in s and "projection for Zeta is <b>32.4</b>" in s
    cfg = json.loads(html.unescape(re.search(r'data-gw="([^"]+)"', s).group(1)))
    assert cfg["proj"] == 32.4 and cfg["line"] == 30.5 and "bands" not in cfg and cfg["cuts"][0] == [0.74, "A"]
    import grader as g
    assert g.CONFIG["p_cap_kills"] == landing_v3.KILLS_CAP
    assert 'data-g="A+"' not in s and 'data-g="A"' in s                # kills are capped at 78%: the ladder stops at A
    assert f'id="gw-p">{round(p, 4) * 100:.1f}<' in s
    b["records"][0]["scout"]["p_side"] = round(p + 0.02, 4)              # the board's number differs (another model): no widget
    workspace_api._SNAP["body"] = json.dumps(b).encode()
    assert 'id="gw"' not in build(srv).decode("utf-8")


def test_seo_tags_use_the_configured_public_url_only(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    s = build(srv).decode("utf-8")
    assert "og:image" not in s and "canonical" not in s and 'property="og:title"' in s and "summary_large_image" in s
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com/")
    s = build(srv).decode("utf-8")
    assert '<link rel="canonical" href="https://cs2-scout.onrender.com/">' in s
    assert '<meta property="og:image" content="https://cs2-scout.onrender.com/landing/og.png">' in s
    monkeypatch.setenv("PUBLIC_URL", "javascript:alert(1)")
    assert "og:image" not in build(srv).decode("utf-8")


def test_seo_tags_prefer_the_landing_domain_and_keep_public_url_for_sign_in(srv, monkeypatch):
    """LANDING_PUBLIC_URL (the brand's domain) names the share / SEO tags; PUBLIC_URL (Whop sign-in's callback origin) is untouched."""
    seed(srv, monkeypatch, board())
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com/")
    s = build(srv).decode("utf-8")
    for tag in ('<link rel="canonical" href="https://propsgrader.com/">', '<meta property="og:url" content="https://propsgrader.com/">',
                '<meta property="og:image" content="https://propsgrader.com/landing/og.png">',
                '<meta name="twitter:image" content="https://propsgrader.com/landing/og.png">'):
        assert tag in s, tag
    assert "cs2-scout" not in s.split("</head>")[0]
    monkeypatch.setenv("LANDING_PUBLIC_URL", "http://propsgrader.com")                 # not https: falls back to PUBLIC_URL
    assert '<link rel="canonical" href="https://cs2-scout.onrender.com/">' in build(srv).decode("utf-8")
    landing_v3.reset()
    a = landing_v3.page(srv, JOIN)
    monkeypatch.setenv("LANDING_PUBLIC_URL", "https://propsgrader.com")               # a changed setting rebuilds the cached page
    assert b'href="https://propsgrader.com/"' in landing_v3.page(srv, JOIN) and b"propsgrader" not in a


def test_og_image_is_served_as_png(srv, monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        st, h, body = get(httpd.server_address[1], "/landing/og.png")
        assert st == 200 and h["content-type"] == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n" and len(body) < 200_000
        assert "public" in h["cache-control"]
    finally:
        httpd.shutdown()


def test_page_is_pinned_dark_and_every_primary_button_opens_the_free_trial(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = landing_v3.page(srv, server.landing_trial_url()).decode("utf-8")
    assert '<html lang="en" data-ui-v3="1" data-v3-page="landing" data-sc-sport="cs2" data-theme="dark">' in s
    assert "scout-theme" not in s and "prefers-color-scheme: light" not in landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    trial, week = JOIN.replace("&", "&amp;"), WEEK.replace("&", "&amp;")
    primary = re.findall(r'<a class="btn primary[^"]*" href="([^"]+)"[^>]*>([^<]+)</a>', s)
    assert len(primary) >= 7 and {h for h, _t in primary} == {trial}, primary          # every primary button: the free trial
    for _h, label in primary:                                                            # and each one says so
        assert re.search(r"free", label, re.I), label
    whop = re.findall(r'href="(https://whop\.com/[^"]+)"', s)
    assert set(whop) == {trial, week} and whop.count(week) == 1                         # Weekly: its one card only
    assert OLD_JOIN not in s and "plan_8d2GVuU7671mD" not in s                          # a stale LANDING_JOIN_URL never reaches v3
    assert "Join on Whop" not in s and "See plans on Whop" not in s
    assert s.count("1-800-GAMBLER") >= 3 and s.count("21 and over") >= 3 and "<details>" in s


def test_saved_board_is_real_rows_with_their_own_time():
    doc = json.loads(landing_v3.FALLBACK.read_text(encoding="utf-8"))
    assert doc["board_at"] == "2026-11-22T17:53:44+00:00" and len(doc["records"]) >= 6
    for r in doc["records"]:
        assert r["market"]["line_type"] == "standard" and r["offer"]["line"] is not None and r["scout"]["p_side"] is not None
        assert "units" not in r["scout"] and "stake_reason" not in r["scout"]
    rows, meta, n, _t = landing_v3.terminal(doc)
    assert n == 4 and meta == "as of Nov 22, 17:53 UTC" and landing_v3.feeds_label(doc) == "Underdog"


def test_slip_tile_is_a_real_slip_in_v3_markup():
    doc = json.loads(landing_v3.SLIP.read_text(encoding="utf-8"))
    assert "member" not in doc and "id" not in doc and "result" not in doc and all(set(l) <= {"player", "team", "prop", "scope", "line", "side", "p", "grade"} for l in doc["legs"])
    body, cap = landing_v3.slip_card()
    p = 1.0
    for l in doc["legs"]:
        p *= l["p"]
        assert f'{l["p"] * 100:.1f}<i>%</i>' in body and f'>{l["grade"]}</b>' in body
    assert f"<b>{p * 100:.1f}<i>%</i></b><span>model estimate, all {len(doc['legs'])} hit</span>" in body
    assert "shown as built" in cap and "Nov 20" not in cap and "<img" not in body       # no date older than the record


def test_disclosure_text_is_at_least_13px():
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    for sel in (".fine", ".rule", ".micro", ".rg", ".rg-close", ".term-foot", ".slip figcaption", ".rv figcaption small", ".pool .cap",
                ".terms", ".secure", ".story figcaption", ".pf-post small", ".proof span"):
        sizes = re.findall(r"(?:^|[},])\s*" + re.escape(sel) + r"\{[^}]*?font-size:([\d.]+)px", css, re.M)
        assert sizes and all(float(x) >= 13 for x in sizes), (sel, sizes)


def test_one_changed_input_makes_exactly_one_new_page(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    a = landing_v3.page(srv, JOIN)
    wait_builder()
    a = landing_v3.page(srv, JOIN)
    builds = []
    real = landing_v3.build
    monkeypatch.setattr(landing_v3, "build", lambda *x, **k: builds.append(1) or real(*x, **k))
    assert landing_v3.page(srv, JOIN) is a                               # unchanged inputs: the same object, no build
    b2 = board()
    b2["records"] = [r for r in b2["records"] if r["player"]["name"] != "Zeta"]
    workspace_api._SNAP["body"] = json.dumps(b2).encode()
    landing_v3._STATE["built"] = 0.0
    with ThreadPoolExecutor(max_workers=200) as pool:                  # 200 visitors at the moment the page is due: one rebuild
        list(pool.map(lambda _: landing_v3.page(srv, JOIN), range(200)))
    wait_builder()
    new = landing_v3.page(srv, JOIN)
    assert len(builds) == 1 and new is not a and b"Zeta" not in new and landing_v3.page(srv, JOIN) is new


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
    m = re.search(r'(?s)<div class="pool" id="pool".*?<p class="cap">.*?</p>\s*</div>', page.decode("utf-8"))
    return m.group(0) if m else None


def test_map_module_renders_the_rule_subject_from_hltv_totals(srv, monkeypatch, maps_loader):
    seed(srv, monkeypatch, pm_board())
    page = landing_v3.build(srv, JOIN, fetch=True)
    pool = pool_html(page)
    assert pool is not None and 'class="wrap cs2-in"' in page.decode()
    assert maps_loader.calls == ["Early", "Mover"]                    # soonest lettered kills first; Early falls short; Zeta never read
    assert 'data-line="32.5"' in pool and "Mover · Kills · Maps 1-2 · line 32.5 on Underdog" in pool      # the lettered Underdog line
    tot = map_totals(MOVER_MAPS)
    names = re.findall(r'<span class="mn">([^<]+)</span>', pool)
    assert names == ["Mirage", "Dust 2", "Inferno", "Ancient"]          # top 4 pool maps by maps played (Overpass outside, Nuke 5th)
    for nm in names:
        k, n = tot[nm]
        assert f'data-k="{k / n:.6f}" data-n="{n}"' in pool and f"<b>{k / n:.2f}</b>" in pool and f"<span>Maps</span><b>{n}</b>" in pool
    assert pool.count('aria-pressed="true"') == 2                       # the two most played start chosen
    (k1, n1), (k2, n2) = tot["Mirage"], tot["Dust 2"]
    v, pace = (k1 + k2) / (n1 + n2), 32.5 / 2
    assert f'<b id="pool-v">{v:.2f}</b>' in pool and f"line pace of <b>{pace:.2f}</b>" in pool
    d = v - pace
    assert f'class="{"up" if d >= 0 else "dn"}">{"+" if d >= 0 else "\u2212"}{abs(d):.2f}</i>' in pool
    assert '<span id="pool-names">Mirage + Dust 2</span>' in pool
    newest = date.today() - timedelta(days=2)
    cap = html.unescape(re.search(r'<p class="cap">([^<]+)</p>', pool).group(1))
    assert f"(newest map {newest.strftime('%b')} {newest.day}, read " in cap and re.search(r"read [A-Z][a-z]{2} \d{1,2}, \d\d:\d\d UTC\)", cap)
    assert "Mover's HLTV numbers for the last 12 months" in cap and "on the four maps of the current pool Mover has played most." in cap
    assert cap.endswith("The pool today: Ancient, Anubis, Cache, Dust 2, Inferno, Mirage and Nuke.")
    assert "sml" not in page.decode()


def test_map_module_pool_is_the_sites_current_pool(srv, monkeypatch, maps_loader):
    import hltv_maps
    pool7 = ["ancient", "anubis", "dust2", "mirage", "nuke", "overpass", "train"]       # a pool without Inferno (and with Overpass)
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (pool7, "recent"))
    seed(srv, monkeypatch, pm_board())
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    assert re.findall(r'<span class="mn">([^<]+)</span>', pool) == ["Mirage", "Overpass", "Dust 2", "Ancient"]
    assert "Inferno" not in pool and "The pool today: Ancient, Anubis, Dust 2, Mirage, Nuke, Overpass and Train." in html.unescape(pool)


def test_map_module_visitor_request_never_reads_hltv(srv, monkeypatch, maps_loader):
    seed(srv, monkeypatch, pm_board())
    first = build(srv)
    assert pool_html(first) is None and maps_loader.calls == []         # fetch=False with nothing cached: no read, no module
    assert 'class="wrap cs2-in no-pool"' in first.decode()
    landing_v3.build(srv, JOIN, fetch=True)                             # the background builder reads (bounded)
    n = len(maps_loader.calls)
    with ThreadPoolExecutor(max_workers=200) as pool:                  # 200 request-path builds: cached answers only
        pages = list(pool.map(lambda _: build(srv), range(200)))
    assert len(maps_loader.calls) == n and all(pool_html(p) for p in pages)


def test_map_module_reads_are_bounded_and_single_flight(srv, monkeypatch, maps_loader):
    b = pm_board()
    at = landing_v3._ts(b["board_at"])
    b["records"] += [rec(f"Extra{i}", "kills", 20.5, "underdog", .6, "B", at + timedelta(minutes=10 + i)) for i in range(20)]
    seed(srv, monkeypatch, b)
    assert pool_html(landing_v3.build(srv, JOIN, fetch=True)) is None   # 20 sooner players without HLTV maps: POOL_TRIES reads, no module
    assert len(maps_loader.calls) == landing_v3.POOL_TRIES
    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(lambda _: landing_v3.build(srv, JOIN, fetch=True), range(50)))
    assert len(maps_loader.calls) == len(set(maps_loader.calls))         # each player read once (cached "none" answers)


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
        workspace_api._SNAP["body"] = None                              # no live board: the saved board shows, never the module
        maps_loader.data["sml"] = map_rows(MOVER_MAPS)
    page = landing_v3.build(srv, JOIN, fetch=True)
    if case == "stale_read":
        assert pool_html(page) is not None
        maps_loader.data.pop("Zeta")                                    # the next candidate has no HLTV maps either
        for k in list(hltv._META):
            hltv._META[k]["as_of"] = time.time() - (landing_v3.POOL_MAX_AGE_H * 3600 + 60)
        page = landing_v3.build(srv, JOIN, fetch=True)
    s = page.decode("utf-8")
    if case == "error":
        assert landing_v3.POOL_TRIES > 1 and calls == 1                  # a failed HLTV read stops this build's reads
    assert pool_html(page) is None and '<div class="pool"' not in s and "Pick two maps" not in text(page) and not re.search(r"\{\{[A-Z_0-9]+\}\}", s)
    assert 'class="wrap cs2-in no-pool"' in s and "Built for CS2, not bolted on." in s and 'id="terminal"' in s


def test_map_module_never_shows_zero_for_a_missing_value(srv, monkeypatch, maps_loader):
    import hltv
    import hltv_maps
    doc = hltv_maps.build(map_rows(MOVER_MAPS), date.today(), 77005)
    win = doc["windows"]["12m"]["maps"]
    win["mirage"]["kills"] = None                                       # a missing total: the map is left out, never 0
    win["dust2"] = dict(win["dust2"], maps=0, kills=0)
    win["anubis"] = dict(win["anubis"], maps=9, kills=0)                # no kills recorded on 9 maps: left out, never 0.00
    hltv._remember(hltv_maps._key("Mover", None), dict(doc, state="ok"))
    seed(srv, monkeypatch, pm_board())
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=False))
    assert pool is None                                                  # only Inferno, Ancient, Nuke remain: under 4, no module
    hltv._remember(hltv_maps._key("Mover", None), dict(doc, state="ok"))
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "overpass"], "match"))
    pool = pool_html(landing_v3.build(srv, JOIN, fetch=False))
    assert re.findall(r'<span class="mn">([^<]+)</span>', pool) == ["Overpass", "Inferno", "Ancient", "Nuke"]
    assert ">Mirage<" not in pool and ">Dust 2<" not in pool and ">Anubis<" not in pool.split('class="cap"')[0]
    assert not re.search(r"<b>0(\.0+)?</b>|data-k=\"0(\.0+)?\"|data-n=\"0\"", pool)


def test_template_carries_no_hardcoded_player_stats():
    raw = landing_v3.TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"(?s)<!--if:POOL-->(.*?)<!--/if:POOL-->", raw).group(1)
    bare = re.sub(r"\{\{[A-Z_0-9]+\}\}", "", block)
    assert not re.search(r"\d", re.sub(r'(style|class|id)="[^"]*"', "", bare).replace("Maps 1-2", "")), "a number typed into the map module"
    assert "data-k=" not in block and "sml" not in raw and "33.5" not in raw and "Mirage" not in block
    assert all(tok in block for tok in ("{{POOL_LINE}}", "{{POOL_WHO}}", "{{POOL_APP}}", "{{POOL_MAPS}}", "{{POOL_V}}", "{{POOL_CAP}}"))


def test_map_module_survives_an_idle_rebuild_after_the_hltv_cache_lapses(srv, monkeypatch, maps_loader):
    import hltv
    seed(srv, monkeypatch, pm_board())
    a = pool_html(landing_v3.build(srv, JOIN, fetch=True))
    n = len(maps_loader.calls)
    hltv._CACHE.clear()                                                  # hltv's 15-min cache lapsed while nobody visited
    assert pool_html(build(srv)) == a and len(maps_loader.calls) == n    # the request path keeps the subject, still no read


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
    assert seen[-1] == "2400644"                                         # the board row's own match: veto-1's pool for that match


def test_map_module_never_picks_a_match_that_has_locked(srv, monkeypatch, maps_loader):
    b = pm_board()
    old = datetime.now(timezone.utc) - timedelta(hours=6)                # a kept board from before the feeds failed
    b["board_at"] = iso(old)
    for r in b["records"]:
        if r["player"]["name"] in ("Early", "Mover", "Zeta"):
            r["event"]["lock_utc"] = r["event"]["start_utc"] = iso(old + timedelta(hours=1))
    seed(srv, monkeypatch, b)
    assert pool_html(landing_v3.build(srv, JOIN, fetch=True)) is None and maps_loader.calls == []


# ------------------------------------------------------------------------- owner blueprint 2026-11-22: Prop Grader, trial-first
def hero(s):
    return re.search(r'(?s)<section class="hero.*?</section>', s).group(0)


def test_hero_button_is_the_free_trial_with_price_and_cancel_terms_right_under_it(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    h = hero(build(srv).decode("utf-8"))
    m = re.search(r'(?s)<div class="ctas">\s*<a class="btn primary" href="([^"]+)">([^<]+)</a>\s*</div>\s*<p class="terms">([^<]+)</p>', h)
    assert m, "the price and cancel line sits directly under the hero button"
    assert m.group(1) == JOIN.replace("&", "&amp;") and m.group(2) == "Try Prop Grader free for 1 day"
    assert m.group(3) == "Then $24.99/month. Cancel anytime from your Whop account."
    assert '<a class="tlnk" href="/record">See the public record' in h and "Sign in with Whop" in h
    assert "Know the chance <span class=\"ac\">before you pick.</span>" in h
    t = text(h.encode())
    assert ("Prop Grader gives every PrizePicks and Underdog line a model estimate and a letter grade, with the reason in plain English. "
            "CS2, LoL, NBA and NFL.") in t
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    assert "with the reason in plain English. CS2, LoL, NBA, NFL, soccer and tennis." in text(build(srv))
    s = build(srv).decode("utf-8")                                      # the phone bar: trial-first, with the price
    assert '<div class="dock" id="dock"><a class="btn primary" href="' + JOIN.replace("&", "&amp;") + '">Try free for 1 day · then $24.99/mo</a></div>' in s


def test_pricing_cards_featured_monthly_trial_and_weekly(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    sec = re.search(r'(?s)<section class="pricing sec" id="pricing".*?</section>', s).group(0)
    feat = re.search(r'(?s)<article class="plan-card feat-plan">.*?</article>', sec).group(0)
    wk = re.search(r'(?s)<article class="plan-card wk-plan">.*?</article>', sec).group(0)
    tf, tw = text(feat.encode()), text(wk.encode())
    assert "1-day free trial" in tf and "Monthly" in tf and "$24.99 /month" in tf
    assert f'<a class="btn primary" href="{JOIN.replace("&", "&amp;")}">Start free, then $24.99/mo</a>' in feat
    for item in ("The board", "Grades at every line", "Line gaps and moves", "Slip Studio", "Player pages", "The public record"):
        assert f"<b>{item}</b>" in feat, item
    assert "Weekly" in tw and "$9.99 /week" in tw and f'href="{WEEK}"' in wk and "btn primary" not in wk
    assert "Secure checkout on Whop · cancel anytime" in text(sec.encode())
    assert "$" not in landing_v3.TEMPLATE.read_text(encoding="utf-8")                 # prices come from PRICE_M / PRICE_W only
    assert set(re.findall(r"\$[\d.]+", text(s.encode()))) == {"$24.99", "$9.99"}


def test_trial_and_weekly_urls_default_to_the_verified_plans(monkeypatch):
    for k in ("LANDING_TRIAL_URL", "LANDING_WEEKLY_URL", "LANDING_JOIN_URL"):
        monkeypatch.delenv(k, raising=False)
    assert server.landing_trial_url() == "https://whop.com/checkout/plan_llXQ8ebwMzfRd"        # 1 day free, then $24.99/month
    assert server.landing_weekly_url() == "https://whop.com/checkout/plan_4zOzRl7roBDFf"       # $9.99/week
    assert server.landing_join_url() == server.landing_trial_url()                              # the flag-off page: the trial too
    monkeypatch.setenv("LANDING_TRIAL_URL", "javascript:alert(1)")                             # only a whop.com link overrides
    assert server.landing_trial_url() == "https://whop.com/checkout/plan_llXQ8ebwMzfRd"
    monkeypatch.setenv("LANDING_JOIN_URL", "https://whop.com/checkout/plan_8d2GVuU7671mD")      # an old Monthly override: flag-off only
    assert server.landing_trial_url() == "https://whop.com/checkout/plan_llXQ8ebwMzfRd"


def test_brand_is_prop_grader_in_title_meta_and_copy(srv, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    assert "<title>Prop Grader · Know the chance before you pick</title>" in s
    assert '<meta property="og:site_name" content="Prop Grader">' in s
    assert '<meta property="og:title" content="Prop Grader · Know the chance before you pick">' in s
    assert '<meta name="twitter:title" content="Prop Grader · Know the chance before you pick">' in s
    assert 'content="Prop Grader: know the chance before you pick"' in s
    head = s.split("</head>")[0]
    head = re.sub(r'https://[^"]+', "", re.sub(r"(?s)<style>.*?</style>|<script>.*?</script>", "", head))   # the site's own URL aside
    assert not re.search(r"scout|1time|\b1T\b", head, re.I)
    body = re.sub(r'(?s)<figure class="rv">(?:(?!</figure>).)*?class=rv-old>.*?</figure>', " ", s.split("</head>", 1)[1])
    t = text(body.encode())                                                    # review cards included, unless marked pre-rename
    assert not re.search(r"\bScout\b|1time|\b1T\b", t), re.findall(r".{30}(?:Scout|1time|1T).{30}", t)
    assert t.count("Prop Grader") >= 8


def test_proof_strip_numbers_come_from_live_data(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    p = re.search(r'(?s)<ul class="proof" id="proof".*?</ul>', build(srv).decode("utf-8")).group(0)
    t = html.unescape(text(p.encode()))
    total = 100 + 90 + 50 + 40 + 300 + 280 + 80 + 70
    assert f"{total:,} decided picks on the public record, losses included" in t
    assert "★ 4.8 from 12 member reviews" in t
    assert "2 apps Lines from PrizePicks and Underdog" in t                             # the CS2 board's own apps (no books read)
    assert "“" not in p and "Grader 4 mans: roughly <b>+46U</b> in the past week, and Grader has only been live 2 weeks" in p
    assert "Prop Grader on X, Nov 20" in t
    srv._store_result(landing_v3.record_key("nba"), 200, json.dumps(record_doc("nba", 10, 5, 0)).encode(), 600, 600)
    more = dict(REVIEWS, summary={"count": 31, "average": 4.9, "breakdown": {}})
    srv._store_result(landing_v3._reviews_key(), 200, json.dumps(more).encode(), 600, 600)
    t2 = html.unescape(text(re.search(r'(?s)<ul class="proof".*?</ul>', build(srv).decode("utf-8")).group(0).encode()))
    assert f"{total - 580 + 15:,} decided picks" in t2 and "★ 4.9 from 31 member reviews" in t2     # the tiles follow the data
    # no number typed into the proof strip's template other than the owner's attributed post (a constant, shown with its source)
    raw = landing_v3.TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r'(?s)<ul class="proof" id="proof".*?</ul>', raw).group(0)
    assert not re.search(r"\d", re.sub(r"\{\{[A-Z_0-9]+\}\}", "", block))
    assert "{{OWNER_POST}}" in block and "{{OWNER_POST_BY}}" in block and "roughly" in landing_v3.OWNER_POST
    page = text(build(srv))
    assert page.count("+46U") == 1 and page.count("46") == 1                           # the owner's post, once, nowhere else


def test_proof_tiles_drop_without_their_data(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "0")
    monkeypatch.setattr(reviews, "page_on", lambda: False)
    s = build(srv).decode("utf-8")
    p = re.search(r'(?s)<ul class="proof" id="proof".*?</ul>', s).group(0)
    assert "decided picks" not in p and "member reviews" not in p and "★" not in p
    assert "+46U" in p and "Lines from" not in p                                    # no board read: no app count, no "and more"


def test_members_section_has_the_attributed_six_man_story_and_real_reviews_only(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    sec = re.search(r'(?s)<section class="proofwall sec" id="members".*?</section>', s).group(0)
    st = re.search(r'(?s)<figure class="story">.*?</figure>', sec).group(0)
    assert "Shared by Prop Grader on X" in st and "A member ran his first ever 6 man with Grader and cashed." in st
    assert len(re.findall(r'<figure class="rv">', sec)) == 3                             # three real reviews (reviews.py answer)
    names = re.findall(r"<figcaption><b>([^<]+)</b>", sec)
    assert set(names) <= {r["name"] for r in REVIEWS["reviews"]}                         # no invented reviewer
    monkeypatch.setattr(reviews, "page_on", lambda: False)                              # no reviews: the story stays, attributed
    sec = re.search(r'(?s)<section class="proofwall sec" id="members".*?</section>', build(srv).decode("utf-8")).group(0)
    assert '<figure class="rv">' not in sec and "Shared by Prop Grader on X" in sec and 'id="reviews"' not in sec
    assert "in their words" not in sec and "From the members." in sec                 # no member quotes: never "in their words"


def test_faq_answers_the_free_day_bankroll_and_apps_questions(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    t = text(re.search(r'(?s)<section class="faq.*?</section>', build(srv).decode("utf-8")).group(0).encode())
    assert ("What happens after my free day? Your membership renews at $24.99/month unless you cancel from your Whop account "
            "before the free day ends.") in t
    assert "Do I need a big bankroll? No. Grades are chances, not promises" in t and "21 and over" in t and "1-800-GAMBLER" in t
    assert ("Which apps does it cover? The boards carry lines from PrizePicks and Underdog, the pick'em apps Slip Studio builds "
            "slips for, and each line is graded at the number that app lists.") in html.unescape(t)
    for q in ("Is this a picks group?", "How do I know the grades are honest?", "Will I win every day?", "Is esports an afterthought?",
              "Can I cancel?"):
        assert q in t, q


def test_sections_follow_the_blueprint_order_and_wording(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    order = ['id="hero-title"', 'id="proof"', 'id="terminal"', 'id="how"', 'id="tools"', 'id="hand-title"', 'id="record"',
             'id="members"', 'id="pricing"', 'id="faq"', 'id="close-title"']
    at = [s.index(x) for x in order]
    assert at == sorted(at), order
    t = text(s.encode())
    assert "Pick your app and sport" in t and "See the grade and model estimate at your exact line, with the why" in t
    assert "Build your slip in Slip Studio and check the record after" in t
    assert "One row instead of 2 apps." in t and "By hand vs Grader" in t
    assert "Saved before the game. Scored after. Losses included." in t
    assert "Start my free day" in t and "Know the chance before you pick. Try it free for 1 day" in t
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")                     # the phone bar's room is static CSS
    assert re.search(r"@media \(max-width:859px\)\{body\{padding-bottom:calc\(68px \+ env\(safe-area-inset-bottom\)\)\}\}", css)


def test_built_page_keeps_the_banned_words_out(srv, monkeypatch):
    """The ad-safe list on the whole built page's own words (Python constants and fills included), member review quotes and the
    record's own rule sentences aside. Grader numbers are a model estimate: never a hit rate, a lock or a guarantee."""
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    s = re.sub(r'(?s)<figure class="rv">.*?</figure>|<p class="rule">.*?</p>', " ", s)
    t = text(s.encode()).lower()
    for bad in (r"guarantee", r"\block", r"win[- ]rate", r"hit rate", r"accura", r"profit", r"\broi\b", r"\bbeat\b", r"\+ev\b",
                r"\bstake", r"bovada", r"thunderpick", r"free money", r"sure thing", r"can'?t lose", r"\bunits?\b",
                r"financial advice", r"not advice", r"\bheld\b", r"\bmaybe\b", r"provisional", r"unconfirmed"):
        assert not re.search(bad, t), bad


OLD_BRAND_REVIEW = review(9, "brandfan", "CS2 Scout is a great platform with a clean design, detailed stats and clear reasons on every card "
                                         "and matchup breakdown.", "Excellent CS2 Tool")


def test_reviews_naming_the_old_brand_come_last_and_are_marked_never_edited(srv, monkeypatch):
    """The landing is Prop Grader: a review that names the old brand would rank first by the product rule, but comes after every other
    qualifying review; with 3 others it is not on the landing at all (it stays on /reviews). When it does fill a slot, its card says it
    was written before the rename, and its words are shown exactly as written."""
    rv = dict(REVIEWS, reviews=[OLD_BRAND_REVIEW] + REVIEWS["reviews"])
    seed(srv, monkeypatch, board(), rv=rv)
    s = build(srv).decode("utf-8")
    sec = re.search(r'(?s)<section class="proofwall sec" id="members".*?</section>', s).group(0)
    assert "brandfan" not in sec and "Scout" not in text(sec.encode()) and len(re.findall(r'<figure class="rv">', sec)) == 3
    assert "renamed" not in sec
    few = dict(REVIEWS, reviews=[OLD_BRAND_REVIEW] + [r for r in REVIEWS["reviews"] if r["name"] != "plain"])   # 2 others qualify
    srv._store_result(landing_v3._reviews_key(), 200, json.dumps(few).encode(), 600, 600)
    sec = re.search(r'(?s)<section class="proofwall sec" id="members".*?</section>', build(srv).decode("utf-8")).group(0)
    names = re.findall(r"<figcaption><b>([^<]+)</b>", sec)
    assert names[-1] == "brandfan" and len(names) == 3                                    # last, never first
    card = re.findall(r'(?s)<figure class="rv">.*?</figure>', sec)[-1]
    assert html.escape(OLD_BRAND_REVIEW["body"]) in card                                 # word for word
    assert "Written before the site was renamed Prop Grader. The member’s words are unchanged." in card
    assert not any("renamed" in c for c in re.findall(r'(?s)<figure class="rv">.*?</figure>', sec)[:-1])
    for body, title in (("Love the stats from 1time on every card here", ""), ("Great stats and reasons on every card", "1T Scout rocks")):
        assert landing_v3.featured({"reviews": [review(10, "x", body, title)]})[0][3] is True
    assert landing_v3.featured({"reviews": [review(11, "x", "Scouting reports and stats on every card are great")]})[0][3] is False


def sports_snap(lg, rows, at=None, counts=None):
    """A real-shaped workspace_sports snapshot: per-platform counts, the props map and each row's saved bytes in the index."""
    index, props = {}, {}
    for i, r in enumerate(rows):
        k = (r["offer"]["platform"], f"rid{i}")
        index[k] = {"lean": r["scout"]["side"], "started": False, "start": r["event"]["start_utc"], "rec": json.dumps(r).encode()}
        props.setdefault((r["event"]["id"], r["player"]["key"], r["market"]["stat"], r["market"]["period"]), []).append(k)
    c = counts or {}
    for r in rows:
        c[r["offer"]["platform"]] = c.get(r["offer"]["platform"], 0) + 1
    return {"at": time.time() if at is None else at, "body": b"{}", "index": index, "props": props, "counts": c, "gen": 1}


def nba_rec(name, book, line, p, grade, start, side="over"):
    r = rec(name, "points", line, book, p, grade, start, side=side)
    r["sport"] = "nba"
    r["event"]["id"] = f"nba:{name}"
    r["market"].update(stat_label="Points", period_label="Full game")
    r["offer"]["platform_label"] = {"underdog": "Underdog", "prizepicks": "PrizePicks"}.get(book, book)
    return r


def test_app_list_is_read_live_and_keeps_pick_apps_apart_from_comparison_books(srv, monkeypatch):
    import workspace_sports
    s0 = datetime.now(timezone.utc) + timedelta(hours=3)
    rows = [nba_rec("Lead", "underdog", 24.5, .61, "B", s0), nba_rec("Lead", "prizepicks", 25.5, .58, "B", s0)]
    workspace_sports._SNAP["nba"] = sports_snap("nba", rows, counts={"fanduel": 40, "bovada": 90, "polymarket": 12, "unibet_au": 5,
                                                                       "betrivers": 0, "pinnacle": 3})
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    t = html.unescape(text(s.encode()))
    proof = html.unescape(text(re.search(r'(?s)<ul class="proof".*?</ul>', s).group(0).encode()))
    assert "5 apps and books Lines from PrizePicks, Underdog and more" in proof                     # 2 apps + FanDuel, Unibet, Polymarket
    assert "One row instead of 5 apps and books." in t
    assert ("The boards carry lines from PrizePicks and Underdog, the pick'em apps Slip Studio builds slips for, and each line is "
            "graded at the number that app lists. The NBA boards also show lines from FanDuel, Unibet and Polymarket beside them for "
            "comparison. Slips cover PrizePicks and Underdog only.") in t
    chips = re.search(r'(?s)<ul class="chips books".*?</ul>', s).group(0)
    assert re.findall(r"<li>([^<]+)</li>", chips) == ["PrizePicks", "Underdog"]
    assert re.findall(r'<li class="cmp">([^<]+)</li>', chips) == ["FanDuel", "Unibet", "Polymarket"] and "For comparison on NBA" in chips
    assert "Open PrizePicks, Underdog and 3 books and write the numbers down" in t
    for gone in ("Bovada", "BetRivers", "Pinnacle", "Unibet (AU)"):                         # unapproved / zero rows: never named
        assert gone not in t, gone
    workspace_sports._SNAP["nba"]["at"] = time.time() - landing_v3.APPS_MAX_AGE_S - 5         # a board nobody has read for a day
    t2 = text(build(srv))
    assert "FanDuel" not in t2 and "One row instead of 2 apps." in t2


def test_by_hand_row_shows_a_real_two_app_row_from_another_sport_when_cs2_has_one_app(srv, monkeypatch):
    import workspace_sports
    b = board()
    b["records"] = [r for r in b["records"] if r["offer"]["platform"] == "underdog"]           # a CS2 board with Underdog rows only
    seed(srv, monkeypatch, b)
    s = build(srv).decode("utf-8")
    hand = re.search(r'(?s)<section class="hand sec".*?</section>', s).group(0)
    assert "The app's line on the row; when a second app lists the prop, its line sits beside it" in html.unescape(text(hand.encode()))
    assert "Every supported app" not in s
    term = text(re.search(r'(?s)<div class="term-foot">.*?</div>\s*</div>', s).group(0).encode())
    assert "two-app line gaps first" not in term and "this sample has Underdog lines only" in term
    s0 = datetime.now(timezone.utc) + timedelta(hours=3)
    workspace_sports._SNAP["nba"] = sports_snap("nba", [
        nba_rec("Later", "underdog", 20.5, .6, "B", s0 + timedelta(hours=1)), nba_rec("Later", "prizepicks", 21.5, .57, "B-", s0 + timedelta(hours=1)),
        nba_rec("Same", "underdog", 18.5, .6, "B", s0), nba_rec("Same", "prizepicks", 18.5, .6, "B", s0),          # no line gap
        nba_rec("NoPP", "underdog", 9.5, .7, "A-", s0),                                                         # one app only
        nba_rec("Past", "underdog", 9.5, .7, "A-", s0 - timedelta(hours=5)), nba_rec("Past", "prizepicks", 10.5, .6, "B", s0 - timedelta(hours=5)),
    ])
    landing_v3.build(srv, JOIN, fetch=True)                                                   # the builder finds and caches the row
    s = build(srv).decode("utf-8")
    hand = re.search(r'(?s)<section class="hand sec".*?</section>', s).group(0)
    find = re.search(r'(?s)Find the line</b>.*?</div>', hand).group(0)
    assert ">UD<" in find and ">PP<" in find and ">20.5<" in find and ">21.5<" in find          # line gap first, then the soonest
    assert "NBA · Later · Points · Full game" in find and "Each app&#x27;s line on one row" in find
    assert landing_v3.sports_pair(["nba"], now=(s0 + timedelta(hours=2)).timestamp()) is None   # locked rows never show


def test_terminal_rule_sentence_keeps_two_app_wording_when_the_board_has_two_apps(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    term = text(re.search(r'(?s)<div class="term-foot">.*?</div>\s*</div>', build(srv).decode("utf-8")).group(0).encode())
    assert "Rows picked by one rule: lettered props, two-app line gaps first, then moved lines, then the soonest." in term
