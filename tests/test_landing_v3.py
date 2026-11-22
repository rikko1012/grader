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

JOIN = "https://whop.com/checkout/plan_test/?a=1&b=2"


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


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    monkeypatch.setenv("SESSION_SECRET", "landing-v3-secret")
    monkeypatch.setenv("LANDING_JOIN_URL", JOIN)
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
    assert "Written on Scout's reviews page by paying members" in html.unescape(t) and "1 to 3 stars" not in t
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
    assert "as of Nov 22, 17:53 UTC" in s and 'data-at="2026-11-22T17:53:44+00:00"' in s and 'class="gate"' not in s
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
    assert '<span class="mark" aria-hidden="true">1T</span><b>Scout</b>' in raw


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
    assert 'Scout&#x27;s chance at exactly <span data-k="line">12.5</span>' in rows[0] or "Scout's chance at exactly <span data-k=\"line\">12.5</span>" in rows[0]
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
    assert "Open the board" in s
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
    assert 'id="gw"' in s and "projection is <b>32.4</b>" in s
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


def test_page_is_pinned_dark_and_every_join_goes_to_the_checkout(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    assert '<html lang="en" data-ui-v3="1" data-v3-page="landing" data-sc-sport="cs2" data-theme="dark">' in s
    assert "scout-theme" not in s and "prefers-color-scheme: light" not in landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    joins = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(?:Get today\'s graded board|Get the board|Open the board|See plans on Whop)</a>', s)
    assert len(joins) >= 6 and set(joins) == {JOIN.replace("&", "&amp;")}
    assert "Join on Whop" not in s and "Membership on Whop · cancel anytime from your Whop account" in s
    assert "See every pick, then join" in s and "Membership includes" in s
    assert s.count("1-800-GAMBLER") == 2 and "21 and over" in s and "<details>" in s


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
    assert f"<b>{p * 100:.1f}<i>%</i></b><span>chance all {len(doc['legs'])} hit</span>" in body
    assert "shown as built" in cap and "Nov 20" not in cap and "<img" not in body       # no date older than the record


def test_disclosure_text_is_at_least_13px():
    css = landing_v3.ASSETS["PAGE_CSS"].read_text(encoding="utf-8")
    for sel in (".fine", ".rule", ".micro", ".rg", ".rg-close", ".term-foot", ".slip figcaption", ".rv figcaption small", ".pool .cap"):
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
