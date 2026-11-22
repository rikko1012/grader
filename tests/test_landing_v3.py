"""The v3 signed-out landing (landing_v3.py + web/v3/landing.html, direction C "line terminal"). No network, no production data:
real-shaped board / record / review documents built here.

- every number comes from the shared bodies (board snapshot, /api/record, /api/reviews); none is typed into the template;
- the terminal picks rows by its stated rule and labels a board older than FRESH_MIN as "as of <time>", never "checked";
- member review text is escaped; the wall features by its stated rule (no photos, no money talk, no shouting);
- a missing input drops its block; the page never fails over one (the terminal falls back to the dated screenshot);
- the template's own text carries none of the owner's banned words and no static percentage;
- the request path never computes (cached bodies only) and 700 visitors at once get one body and one ETag."""
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


def rec(nick, stat, line, book, p, grade, start, moved=None, side="over", be=0.5345, l10=(6, 4)):
    return {"sport": "cs2", "key": f"k.{nick}.{book}",
            "event": {"id": f"hltv:{nick}", "label": "Alpha vs Beta", "start_utc": iso(start), "lock_utc": iso(start)},
            "player": {"key": nick.lower(), "name": nick},
            "market": {"stat": stat, "stat_label": stat.title(), "period": "maps12", "period_label": "Maps 1-2", "line_type": "standard"},
            "offer": {"platform": book, "platform_label": book.title(), "line": line, "moved_from": moved},
            "scout": {"side": side, "p_side": p, "grade": grade},
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
            "totals": {"wins": w, "losses": l, "pushes": p, "matches": 40, "match_word": "matches"},
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
    review(7, "plain", "Reasons behind each prop are great"),
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
    rows = re.findall(r'<li class="tr".*?</li>', s, re.S)
    names = [re.search(r'<div class="who"><b>([^<]+)</b>', r).group(1) for r in rows]
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
    total = 100 + 90 + 50 + 40 + 300 + 280 + 80 + 70                   # decided picks (wins + losses), as /record counts them
    assert f"{total:,} decided picks on the public record" in t and "since" not in t
    for w, l in ((100, 90), (50, 40), (300, 280), (80, 70)):
        assert f"{w:,} - {l:,}" in t
    assert "The cs2 rule." in t and "Second sentence" not in t          # the record's own rule, first sentence
    assert "As of Nov 22, 15:17 UTC" in t


def test_review_wall_escapes_and_features_by_rule(srv, monkeypatch):
    seed(srv, monkeypatch, board())
    s = build(srv).decode("utf-8")
    assert "<script>x</script>" not in s and "&lt;script&gt;x&lt;/script&gt;" in s
    for name in ("longform", "plain"):
        assert f"<b>{name}</b>" in s
    for name in ("slipper", "photo", "shout", "earner", "short"):     # money talk, photos, shouting, too short
        assert f"<b>{name}</b>" not in s
    t = text(s.encode())
    assert "★ 4.8 from 12 member reviews" in t and "Read all 12" in t and "Paying member" in t
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
    assert "/landing/board.webp" in s and 'class="rows"' not in s     # the dated screenshot instead of rows
    assert 'id="record"' not in s and 'id="reviews"' not in s and 'class="proof"' not in s
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
    assert set(re.findall(r'<img src="([^"]+)"', raw)) <= {"/landing/board.webp", "/landing/realslip-scout.webp"} | {
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
