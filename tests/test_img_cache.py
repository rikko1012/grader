"""v3 card pictures (img_cache.py / img_index.py / sports/expansion_images.py / server /img routes). No network: every upstream
answer is a stub. Covers the host allowlist (no open proxy), placeholder and shared-art refusal, re-encoding and its caps, the
disk cache (atomic, bounded), the miss memory, one fetch per key (single flight), the HTTP answer (ETag / 304 / long cache,
fast 404 when not ready), the v3 gate (off = the unknown-route answer), exact-id indexes and the unchanged offer revision."""
import io
import json
import threading
import time

import pytest
from PIL import Image

import img_cache as ic
import img_index as ix
from tests.test_upgrade_gate import env  # noqa: F401 - the upgrade gate's server fixture


def png(w=300, h=300, color=(200, 30, 30, 255), mode="RGBA"):
    b = io.BytesIO()
    Image.new(mode, (w, h), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setenv("SCOUT_IMG_WARM", "0")          # no background thread: tests drive fetch_one themselves
    monkeypatch.setattr(ic, "HOST_GAP_S", 0)
    ic.reset()
    ix.reset()
    yield tmp_path / "img"
    ic.reset()
    ix.reset()


# ------------------------------------------------------------------------------------------------ allowlist / URLs
@pytest.mark.parametrize("url", [
    "https://images.fotmob.com/image_resources/playerimages/1.png", "https://i.imgur.com/x.png",
    "https://static.flashscore.com/a.png", "https://www.atptour.com/x.png", "https://www.flashscore.com/x.png",
    "ftp://a.espncdn.com/i/x.png", "https://user:pw@a.espncdn.com/i/x.png", "https://a.espncdn.com:8443/i/x.png",
    "https://a.espncdn.com.evil.test/i/x.png",
    "https://files.bo3.gg/uploads/a.webp", "http://files.bo3.gg/uploads/a.webp", "https://evil.test/?u=https://a.espncdn.com/x.png", "javascript:alert(1)",
    "https://static.prizepicks.com/images/players/placeholder.png", "https://static.prizepicks.com/x/Player_Avatar.png",
    "https://static.prizepicks.com/images/teams/nfl/jersey_12.png", "https://a.espncdn.com/i/default_headshot.png", None, 7, ""])
def test_only_allowed_https_hosts_and_never_placeholder_art(url):
    assert ic.safe_url(url) is None


def test_allowed_urls_are_kept_and_encoded():
    assert ic.safe_url("https://a.espncdn.com/i/headshots/nba/players/full/1.png") == "https://a.espncdn.com/i/headshots/nba/players/full/1.png"
    assert ic.safe_url("http://static.lolesports.com/x.png") is None                       # not approved (owner decision pending)
    assert ic.safe_url("http://a.espncdn.com/i/x.png") == "https://a.espncdn.com/i/x.png"   # http upgraded on an allowed host
    got = ic.safe_url("https://static.prizepicks.com/images/manual/Jørgen Strand Larsen.png")
    assert got == "https://static.prizepicks.com/images/manual/J%C3%B8rgen%20Strand%20Larsen.png"


def test_espn_urls_are_built_from_exact_ids_only():
    assert ic.espn_headshot("nba", "4432194").endswith("/nba/players/full/4432194.png")
    assert ic.espn_headshot("nba", "../x") is None and ic.espn_logo("nba", "la/../x") is None
    assert ic.espn_logo("nfl", "PIT").endswith("/nfl/500/pit.png") and ic.espn_logo("soccer", "359").endswith("/soccer/500/359.png")
    assert ic.espn_logo("soccer", "ars") is None


def test_shared_art_is_counted_per_distinct_id():
    got = ic.shared_urls([("1", "u"), ("2", "u"), ("1", "u"), ("3", "v")])
    assert got == {"u": 2, "v": 1}


def test_http_get_refuses_other_hosts_redirects_and_oversize(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, body=b"", headers=None):
            self.status_code, self._b, self.headers = code, body, headers or {}

        def iter_content(self, n):
            for i in range(0, len(self._b), n):
                yield self._b[i:i + n]

        def close(self):
            pass

    import requests
    answers = {}
    monkeypatch.setattr(requests, "get", lambda url, **kw: (calls.append((url, kw)), answers[url])[1])
    with pytest.raises(ic.Refused):
        ic._http_get("https://evil.test/x.png")
    assert calls == []                                 # never even asked
    answers["https://a.espncdn.com/r"] = R(302, headers={"Location": "https://evil.test/"})
    with pytest.raises(ic.Refused):
        ic._http_get("https://a.espncdn.com/r")
    assert calls[-1][1]["allow_redirects"] is False and calls[-1][1]["timeout"] == ic.TIMEOUT
    answers["https://a.espncdn.com/big"] = R(200, b"x" * (ic.MAX_FETCH_BYTES + 10))
    with pytest.raises(ic.Refused):
        ic._http_get("https://a.espncdn.com/big")
    answers["https://a.espncdn.com/len"] = R(200, b"x", {"Content-Length": str(ic.MAX_FETCH_BYTES + 1)})
    with pytest.raises(ic.Refused):
        ic._http_get("https://a.espncdn.com/len")
    answers["https://a.espncdn.com/404"] = R(404)
    with pytest.raises(ic.Refused):
        ic._http_get("https://a.espncdn.com/404")
    answers["https://a.espncdn.com/503"] = R(503)
    with pytest.raises(IOError):
        ic._http_get("https://a.espncdn.com/503")
    answers["https://a.espncdn.com/ok"] = R(200, b"abc")
    assert ic._http_get("https://a.espncdn.com/ok") == b"abc"


# ------------------------------------------------------------------------------------------------ encoding
def test_encode_makes_small_square_webp_headshots_and_round_logo_discs():
    d = ic.encode(png(600, 436), "p")
    im = Image.open(io.BytesIO(d))
    assert im.format == "WEBP" and im.size == (160, 160) and len(d) < ic.MAX_OUT_BYTES
    white = ic.encode(png(400, 200, (255, 255, 255, 255)), "t")
    dark = ic.encode(png(400, 200, (10, 10, 10, 255)), "t")
    w, k = Image.open(io.BytesIO(white)).convert("RGBA"), Image.open(io.BytesIO(dark)).convert("RGBA")
    assert w.size == (128, 128) and w.getpixel((0, 0))[3] == 0          # outside the disc: transparent
    assert w.getpixel((10, 64))[:3] == ic.DISC_DARK[:3]                 # a white mark sits on the dark disc
    assert k.getpixel((10, 64))[:3] == ic.DISC_LIGHT[:3]                # a dark mark on the light disc


@pytest.mark.parametrize("raw", [b"", b"not an image", png(10, 10), b"GIF89a" + b"\0" * 20])
def test_encode_refuses_what_is_not_a_usable_picture(raw):
    with pytest.raises(ic.Refused):
        ic.encode(raw, "p")


def test_encode_refuses_a_decompression_bomb(monkeypatch):
    monkeypatch.setattr(ic, "MAX_PIXELS", 10_000)
    with pytest.raises(ic.Refused):
        ic.encode(png(200, 200), "p")


# ------------------------------------------------------------------------------------------------ cache, single flight, misses
def test_register_queues_once_and_fetch_stores_atomically(cache):
    key = ("p", "nba", "4432194")
    assert ic.register(key, [ic.espn_headshot("nba", "4432194")])
    assert ic.register(key, [ic.espn_headshot("nba", "4432194")])      # the same key again: still one queued fetch
    assert ic._QUEUE.count(key) == 1
    got = []
    assert ic.fetch_one(key, get=lambda u: (got.append(u), png(600, 436))[1]) == "stored"
    assert got == ["https://a.espncdn.com/i/headshots/nba/players/full/4432194.png"]
    p = ic.path_of(key)
    assert p.exists() and not list(p.parent.glob("*.tmp"))
    meta = ic.ready(key)
    assert meta and ic.url_path(key, meta[0]).startswith("/img/p/nba/4432194.webp?v=")
    assert not ic.register(("p", "nba", "1"), ["https://images.fotmob.com/x.png"])   # nothing allowed: never registered


def test_first_good_candidate_wins_and_misses_are_remembered(cache):
    key = ("p", "soccer", "e9")
    ic.register(key, ["https://a.espncdn.com/i/headshots/soccer/players/full/9.png", "https://static.prizepicks.com/images/players/soccer/a.webp"])

    def get(u):
        if "espncdn" in u:
            raise ic.Refused("404")
        return png(180, 180)
    assert ic.fetch_one(key, get=get) == "stored"
    k2 = ("p", "nba", "5")
    ic.register(k2, [ic.espn_headshot("nba", "5")])
    assert ic.fetch_one(k2, get=lambda u: (_ for _ in ()).throw(ic.Refused("404"))) == "miss"
    assert ic.missing(k2) and ic._MISS[k2] - time.time() > ic.MISS_S - 5
    assert not ic.enqueue(k2)                          # not asked again inside the miss window
    k3 = ("p", "nba", "6")
    ic.register(k3, [ic.espn_headshot("nba", "6")])
    assert ic.fetch_one(k3, get=lambda u: (_ for _ in ()).throw(TimeoutError())) == "fail"
    assert ic._MISS[k3] - time.time() <= ic.FAIL_S + 1   # a transient failure is retried sooner


def test_warmer_fetches_each_key_once_under_concurrent_registration(cache, monkeypatch):
    calls = []
    lock = threading.Lock()

    def slow(u):
        with lock:
            calls.append(u)
        time.sleep(0.05)
        return png(300, 300)
    monkeypatch.setattr(ic, "_http_get", slow)
    key = ("t", "nba", "lal")
    ts = [threading.Thread(target=ic.register, args=(key, [ic.espn_logo("nba", "lal")])) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    while (k := ic._next()) is not None:
        if ic.ready(k) is None:
            ic.fetch_one(k)
    for _ in range(20):                                # the board is read again: already stored, never fetched again
        ic.register(key, [ic.espn_logo("nba", "lal")])
    assert ic._next() is None
    assert len(calls) == 1


def test_prune_keeps_the_newest_within_bounds(cache):
    for i in range(6):
        k = ("p", "nba", str(i))
        ic.register(k, [ic.espn_headshot("nba", str(i))])
        ic.fetch_one(k, get=lambda u: png(200, 200))
        import os
        os.utime(ic.path_of(k), (1000 + i, 1000 + i))
    (cache / "p" / "nba" / "x.webp.1.2.tmp").write_bytes(b"x")
    import os
    os.utime(cache / "p" / "nba" / "x.webp.1.2.tmp", (1, 1))
    assert ic.prune(max_files=3, max_bytes=10**9) == 3
    left = sorted(p.name for p in (cache / "p" / "nba").iterdir())
    assert left == ["3.webp", "4.webp", "5.webp"]


# ------------------------------------------------------------------------------------------------ HTTP answer
def test_answer_serves_only_stored_files_with_long_cache_and_etag(cache):
    assert ic.answer("/img/p/nba/1.webp")[0] == 404                    # not ready: fast 404, short cache, no upstream call
    assert ic.answer("/img/p/nba/1.webp")[3]["Cache-Control"] == "public, max-age=60"
    for bad in ("/img/p/nba/../../server.py", "/img/p/nba/%2e%2e.webp", "/img/x/nba/1.webp", "/img/p/lol/1.webp",
                "/img/p/nba/A.webp", "/img/p/nba/1.png", "/img/p/nba/" + "a" * 60 + ".webp"):
        assert ic.answer(bad) is None, bad
    key = ("p", "nba", "1")
    ic.register(key, [ic.espn_headshot("nba", "1")])
    ic.fetch_one(key, get=lambda u: png(600, 436))
    code, body, ctype, h = ic.answer("/img/p/nba/1.webp")
    assert code == 200 and ctype == "image/webp" and body[:4] == b"RIFF" and "immutable" in h["Cache-Control"]
    assert ic.answer("/img/p/nba/1.webp", h["ETag"])[0] == 304
    assert ic.answer("/img/p/nba/1.webp", 'W/' + h["ETag"])[0] == 304
    ic.reset()                                         # a fresh process: the file on disk is found again
    assert ic.answer("/img/p/nba/1.webp")[0] == 200


def test_answer_never_calls_upstream(cache, monkeypatch):
    monkeypatch.setattr(ic, "_http_get", lambda u: pytest.fail("member request reached upstream"))
    ic.register(("p", "nba", "2"), [ic.espn_headshot("nba", "2")])
    for _ in range(100):
        assert ic.answer("/img/p/nba/2.webp")[0] == 404


# ------------------------------------------------------------------------------------------------ exact-id indexes
def test_cs2_has_no_picture_source():
    """Owner 2026-11-20 "stop using BO3.gg, we have HLTV": no BO3.gg host, no CS2 sport, no BO3.gg reader left; the HLTV scraper
    DB stores no picture URLs, so CS2 cards keep the monogram."""
    assert "files.bo3.gg" not in ic.ALLOWED_HOSTS and "cs2" not in ic.SPORTS
    assert not any(hasattr(ix, n) for n in ("bo3_scan", "bo3_player", "bo3_team", "_bo3_take", "_BO3"))
    assert ic.ROUTE.match("/img/p/cs2/1.webp") is None and ic.ROUTE.match("/img/t/cs2/7.webp") is None
    assert ic.INDEX_ROUTE.match("/img/index/cs2.json") is None
    assert ic.answer("/img/p/cs2/1.webp") is None                      # not a picture route: the unknown-route answer
    assert ix.index(None, "cs2") is None and ix.index(None, "lol") is None


def _rec(sport, pid, name, team="", abbr=None, oid="o1"):
    pl = {"id": pid, "name": name, "team": team}
    if abbr:
        pl["team_abbr"] = abbr
    return {"sport": sport, "player": pl, "offer": {"offer_id": oid}}


def test_index_lists_ready_pictures_by_exact_id_and_unique_name(cache, monkeypatch):
    recs = [_rec("nba", "nba:player:espn:11", "Al Pha", "LAL", "lal"), _rec("nba", "nba:player:espn:12", "Twin", "BKN", "bkn"),
            _rec("nba", "nba:player:espn:13", "Twin", "BKN", "bkn"), _rec("nba", "nba:player:bref:9", "No Espn", "LAL", "lal")]
    body = json.dumps({"records": recs}).encode()
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (body, json.loads(body)["records"], {"body": body}))
    out = json.loads(ix.build(None, "nba")[1])
    assert out == {"p": {}, "n": {}, "tn": {}}                         # nothing READY yet: no picture is promised
    for k in list(ic._SRC):
        ic.fetch_one(k, get=lambda u: png(600, 436) if "headshots" in u else png(300, 300, (20, 20, 20, 255)))
    assert ("p", "nba", "9") not in ic._SRC                            # not an ESPN id: never guessed
    out = json.loads(ix.build(None, "nba")[1])
    assert out["p"]["nba:player:espn:11"][0].startswith("/img/p/nba/11.webp?v=")
    assert out["p"]["nba:player:espn:11"][1].startswith("/img/t/nba/lal.webp?v=")
    assert out["n"] == {"Al Pha": "nba:player:espn:11", "No Espn": "nba:player:bref:9"}   # "Twin" is two players: no name lookup
    assert out["p"]["nba:player:bref:9"][0] == "" and out["p"]["nba:player:bref:9"][1]  # no ESPN id: the team logo only
    assert out["tn"]["LAL"].startswith("/img/t/nba/lal.webp") and out["tn"]["BKN"].startswith("/img/t/nba/bkn.webp")


def test_cs2_records_register_nothing(cache, monkeypatch):
    recs = [_rec("cs2", 1, "one", "Team Seven"), _rec("cs2", 2, "two", "Eight")]
    body = json.dumps({"records": recs}).encode()
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (body, json.loads(body)["records"], {"body": body}))
    out = json.loads(ix.build(None, "cs2")[1])
    assert not ic._SRC and out == {"p": {}, "n": {}, "tn": {}}


def test_soccer_uses_espn_then_the_prizepicks_picture_of_the_exact_offer_player(cache, monkeypatch):
    recs = [_rec("soccer", "soccer:player:espn:44", "Resolved", oid="e-1"), _rec("soccer", "soccer:prizepicks:77", "Pp Only", oid="e-2"),
            _rec("soccer", "soccer:prizepicks:78", "No Pic", oid="e-3")]
    body = json.dumps({"records": recs}).encode()
    snap = {"body": body, "images": {"soccer:prizepicks:77": "https://static.prizepicks.com/images/players/soccer/77.webp",
                                     "soccer:prizepicks:44": "https://static.prizepicks.com/images/players/soccer/44.webp",
                                     "bad": "https://static.prizepicks.com/x.webp"},
            "index": {("prizepicks", "e-1"): {"offer": {"player_id": "soccer:prizepicks:44"}, "research": {"identity": {"team_id": "soccer:team:espn:359"}}},
                      ("prizepicks", "e-2"): {"offer": {"player_id": "soccer:prizepicks:77"}, "research": {}},
                      ("prizepicks", "e-3"): {"offer": {"player_id": "soccer:prizepicks:78"}, "research": {}}}}
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (body, json.loads(body)["records"], snap))
    ix.build(None, "soccer")
    assert ic._SRC[("p", "soccer", "e44")] == ("https://a.espncdn.com/i/headshots/soccer/players/full/44.png",
                                               "https://static.prizepicks.com/images/players/soccer/44.webp")
    assert ic._SRC[("p", "soccer", "pp77")] == ("https://static.prizepicks.com/images/players/soccer/77.webp",)
    assert ("t", "soccer", "359") in ic._SRC and not any(k[2] == "pp78" for k in ic._SRC)


# ------------------------------------------------------------------------------------------------ the server routes
def test_img_routes_are_unknown_with_v3_off_and_gated_with_it_on(env, monkeypatch, tmp_path):   # noqa: F811
    from tests.test_upgrade_gate import get
    monkeypatch.setenv("SCOUT_IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setenv("SCOUT_IMG_WARM", "0")
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    ic.reset()
    key = ("p", "nba", "1")
    ic.register(key, [ic.espn_headshot("nba", "1")])
    ic.fetch_one(key, get=lambda u: png(600, 436))
    unknown = get(env.base, "/img/no-such-thing", "good-u1")
    for path in ("/img/p/nba/1.webp", "/img/index/nba.json"):
        for who in ("good-u1", None):
            st, body, _ = get(env.base, path, who)
            assert (st, body) == unknown[:2], (path, who)
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    st, body, h = get(env.base, "/img/p/nba/1.webp")                   # public picture (signed-out landing may show one)
    assert st == 200 and h["Content-Type"] == "image/webp" and "immutable" in h["Cache-Control"] and "Content-Encoding" not in h
    assert get(env.base, "/img/p/nba/1.webp", None, {"If-None-Match": h["ETag"]})[0] == 304
    assert get(env.base, "/img/p/nba/99.webp")[0] == 404
    for path in ("/img/index/cs2.json", "/img/p/cs2/1.webp", "/img/t/cs2/7.webp"):   # CS2 has no pictures: no such route
        for who in ("good-u1", None):
            assert get(env.base, path, who)[:2] == unknown[:2], (path, who)
    assert get(env.base, "/img/index/nba.json")[0] == 403              # the board's player list: members only
    monkeypatch.setattr(ix, "build", lambda srv, sport: (b"x", b'{"n":{},"p":{},"tn":{}}'))
    monkeypatch.setattr(ix, "_body_id", lambda srv, sport: None)
    import workspace_api
    monkeypatch.setattr(workspace_api, "access", lambda h, srv, q: {"nba": True})
    assert get(env.base, "/img/index/nba.json", "good-u1")[0] == 404    # SCOUT_UPGRADE=owner: a member has no hub board here
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    st, body, h = get(env.base, "/img/index/nba.json", "good-u1")
    assert st == 200 and json.loads(body) == {"n": {}, "p": {}, "tn": {}} and h.get("ETag")
    for sport in ("nfl", "soccer"):                                     # sports this member cannot see on the board: 404 here too
        assert get(env.base, f"/img/index/{sport}.json", "good-u1")[0] == 404, sport
    ic.reset()
    ix.reset()


def test_a_failing_board_read_builds_once_per_window_not_once_per_member(cache, monkeypatch):
    calls = []

    def boom(srv, sport):
        calls.append(sport)
        raise RuntimeError("sports service down")
    monkeypatch.setattr(ix, "_records", boom)
    monkeypatch.setattr(ix, "_body_id", lambda srv, sport: None)
    ts = [threading.Thread(target=ix.index, args=(None, "nba")) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls == ["nba"] and ix.index(None, "nba") == ix.EMPTY


def test_an_unchanged_board_and_picture_set_is_not_rebuilt(cache, monkeypatch):
    recs = [_rec("nba", "nba:player:espn:11", "Al Pha", "LAL", "lal")]
    body = json.dumps({"records": recs}).encode()
    n = []
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (n.append(1), (body, json.loads(body)["records"], {"body": body}))[1])
    monkeypatch.setattr(ix, "_body_id", lambda srv, sport: id(body))
    first = ix.index(None, "nba")
    ix._MEMO["nba"] = (ix._MEMO["nba"][0], ix._MEMO["nba"][1], 0.0, first)   # the 15 s window has passed
    assert ix.index(None, "nba") is first and len(n) == 1                     # same body, same pictures: no rebuild
    for k in list(ic._SRC):
        ic.fetch_one(k, get=lambda u: png(300, 300))                        # a picture became ready
    ix._MEMO["nba"] = (ix._MEMO["nba"][0], ix._MEMO["nba"][1], 0.0, first)
    assert ix.index(None, "nba") != first and len(n) == 2


# ------------------------------------------------------------------------------------------------ review 2026-11-20 fixes
def test_index_never_waits_on_a_build_in_progress(cache, monkeypatch):
    """One request builds; every other request gets the last list (EMPTY before the first build) at once (cold p99 at 700)."""
    recs = [_rec("nba", "nba:player:espn:11", "Al Pha", "LAL", "lal")]
    body = json.dumps({"records": recs}).encode()
    gate, started, builds = threading.Event(), threading.Event(), []
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (body, json.loads(body)["records"], {"body": body}))
    ix.build(None, "nba")
    for k in list(ic._SRC):
        ic.fetch_one(k, get=lambda u: png(300, 300))        # Al Pha's picture and logo are stored

    def slow_records(srv, sport):
        builds.append(sport)
        started.set()
        gate.wait(10)
        return body, json.loads(body)["records"], {"body": body}
    monkeypatch.setattr(ix, "_records", slow_records)
    monkeypatch.setattr(ix, "_body_id", lambda srv, sport: None)
    out = {}
    builder = threading.Thread(target=lambda: out.setdefault("b", ix.index(None, "nba")))
    builder.start()
    assert started.wait(5)
    t0 = time.time()
    got = [ix.index(None, "nba") for _ in range(30)]
    assert time.time() - t0 < 1.0 and all(g is ix.EMPTY for g in got)         # nobody waited; nothing promised yet
    gate.set()
    builder.join(5)
    first = out["b"]
    assert json.loads(first)["p"]["nba:player:espn:11"][0] and len(builds) == 1
    # the next window: one request rebuilds (blocked again), the others get the last list at once
    gate.clear(); started.clear()
    ix._MEMO["nba"] = (None, ix._MEMO["nba"][1], 0.0, first)
    builder = threading.Thread(target=ix.index, args=(None, "nba"))
    builder.start()
    assert started.wait(5)
    t0 = time.time()
    got = [ix.index(None, "nba") for _ in range(30)]
    assert time.time() - t0 < 1.0 and all(g is first for g in got) and len(builds) == 2
    gate.set()
    builder.join(5)


def test_picture_version_is_coalesced_while_the_warmer_works_and_always_lands(cache, monkeypatch):
    monkeypatch.setattr(ic, "_warming", lambda: True)
    v0 = ic.version()
    with ic._LOCK:
        ic._QUEUE.append(("p", "nba", "1"))                 # the warmer still has work
    ic.bump()
    v1 = ic.version()
    assert v1 != v0                                         # the first change after a quiet spell is seen at once
    ic.bump(); ic.bump()
    assert ic.version() == v1                               # more stores inside VERSION_GAP_S: coalesced
    monkeypatch.setattr(ic, "VERSION_GAP_S", 0)
    assert ic.version() != v1                               # the window passed: seen
    monkeypatch.setattr(ic, "VERSION_GAP_S", 30)
    ic.bump()
    v2 = ic.version()
    ic.bump()
    assert ic.version() == v2
    with ic._LOCK:
        ic._QUEUE.clear()                                   # the warmer's queue is empty: the last store is seen
    assert ic.version() == ic._VERSION[0]


def _jpeg(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 160)).save(b, "JPEG", quality=70)
    return b.getvalue()


def test_pixel_cap_is_about_4m_and_refuses_from_the_header_before_decoding(monkeypatch):
    from PIL import ImageFile
    assert 4_000_000 <= ic.MAX_PIXELS <= 4_200_000
    loads = []
    real = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: (loads.append(self.size), real(self))[1])
    with pytest.raises(ic.Refused) as e:
        ic.encode(png(2100, 2100), "t")                     # 4.41 M pixels PNG: no reduced decode exists
    assert str(e.value) == "dims" and loads == []           # refused from the header: no pixel decoded
    assert Image.open(io.BytesIO(ic.encode(png(2048, 2048), "t"))).size == (128, 128)   # 2048 x 2048 still fits


def test_a_big_jpeg_is_decoded_at_reduced_scale(monkeypatch):
    from PIL import ImageFile
    loads = []
    real = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: (loads.append(self.size), real(self))[1])
    d = ic.encode(_jpeg(4000, 3000), "p")                   # 12 M pixels on the wire
    assert Image.open(io.BytesIO(d)).size == (160, 160)
    assert loads and loads[0][0] * loads[0][1] <= 600_000   # decoded at 1/8 scale (500 x 375), never the full 12 M pixels


def _jpeg_sized(w, h, progressive=False, sof=None, subsampling=0):
    """A real small JPEG whose header claims w x h (the SOF size field rewritten; no big image is ever built here). sof rewrites
    the frame type too (0xC0 on a progressive file = a multi-scan sequential JPEG)."""
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (90, 120, 160)).save(b, "JPEG", quality=70, progressive=progressive, subsampling=subsampling)
    raw = bytearray(b.getvalue())
    k = next(j for j in range(2, len(raw) - 9) if raw[j] == 0xFF and raw[j + 1] in (0xC0, 0xC1, 0xC2))
    if sof is not None:
        raw[k + 1] = sof
    raw[k + 5:k + 7] = h.to_bytes(2, "big")
    raw[k + 7:k + 9] = w.to_bytes(2, "big")
    return bytes(raw)


def _one_component_scan(raw):
    """A sequential JPEG whose only scan covers 1 of its 3 components (libjpeg then buffers full-size coefficients: measured +97 MB
    at 4000 x 4000 4:4:4 when it was given the baseline allowance)."""
    raw = bytes(raw)
    k = raw.find(b"\xff\xda")
    n = int.from_bytes(raw[k + 2:k + 4], "big")
    return raw[:k] + bytes.fromhex("ffda0008010100003f00") + raw[k + 2 + n:]


def _no_load(monkeypatch):
    from PIL import ImageFile
    loads = []
    real = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: (loads.append(self.size), real(self))[1])
    return loads


@pytest.mark.parametrize("w,h,prog,sof", [
    (13300, 13300, True, None),        # review 2026-11-20: 1 MB file, +544 MB before (draft-then-check)
    (7000, 7000, True, None),          # +152 MB before
    (2100, 2100, True, None),          # progressive just over the normal cap
    (5000, 5000, False, 0xC2),         # header says progressive
    (5000, 5000, True, 0xC0),          # sequential frame but ten scans: full-size coefficient buffers, normal cap
    (4001, 4000, False, None),         # single-scan baseline just over its 16 M pixel bound
    (8000, 6000, False, None),         # 48 M pixel baseline (allowed before)
    (4001, 4000, False, "ns1"),        # one SOS but covering 1 of 3 components: normal cap
    (2100, 2100, False, "ns1"),
])
def test_jpegs_over_their_cap_are_refused_from_the_header_before_any_decode(monkeypatch, w, h, prog, sof):
    loads = _no_load(monkeypatch)
    raw = _jpeg_sized(w, h, progressive=prog, sof=None if sof == "ns1" else sof)
    if sof == "ns1":
        raw = _one_component_scan(raw)
    assert Image.open(io.BytesIO(raw)).size == (w, h)
    with pytest.raises(ic.Refused) as e:
        ic.encode(raw, "p")
    assert str(e.value) == "dims" and loads == []


def test_an_absurd_header_is_refused_before_any_decode(monkeypatch):
    loads = _no_load(monkeypatch)
    with pytest.raises(ic.Refused):
        ic.encode(_jpeg_sized(60000, 60000), "p")             # Pillow's own bomb guard answers first; still no decode
    assert loads == []


def test_only_single_scan_baseline_jpegs_get_the_reduced_scale_allowance():
    def ok(raw):
        return ic._jpeg_single_scan_baseline(raw, Image.open(io.BytesIO(raw)))
    assert ok(_jpeg(300, 300)) and ok(_jpeg_sized(4000, 4000))
    ex = Image.new("RGB", (300, 300)).getexif()
    ex[0x010F] = "cam"
    b = io.BytesIO()
    Image.new("RGB", (300, 300)).save(b, "JPEG", exif=ex)
    assert ok(b.getvalue())                                              # APP1 segments are skipped, not scanned
    assert not ok(_jpeg_sized(300, 300, progressive=True))
    assert not ok(_jpeg_sized(300, 300, progressive=True, sof=0xC0))     # multi-scan sequential
    assert not ok(_one_component_scan(_jpeg(300, 300)))                 # single SOS, fewer components than the frame
    stub = type("Im", (), {"info": {}})()
    for bad in (b"\xff\xd8\xff\xc0\x00\x01", b"\xff\xd8\xff\xe0", b"\x00\x00", b"\xff\xd8\xff\xc0\x00\x02\xff\xc0\x00\x02"):
        assert not ic._jpeg_single_scan_baseline(bad, stub)              # malformed / truncated / two frames: the normal cap


def test_progressive_jpeg_within_the_normal_cap_and_real_sizes_still_encode():
    for w, h, prog in ((2048, 2048, True), (1319, 1681, True), (1319, 1681, False), (4000, 3000, False)):
        b = io.BytesIO()
        Image.new("RGB", (w, h), (90, 120, 160)).save(b, "JPEG", quality=60, progressive=prog)
        assert Image.open(io.BytesIO(ic.encode(b.getvalue(), "p"))).size == (160, 160)
    assert Image.open(io.BytesIO(ic.encode(png(1319, 1681), "t"))).size == (128, 128)


def test_encode_peak_memory_stays_small_for_the_biggest_allowed_inputs(tmp_path):
    """RSS in a fresh process (Pillow / libjpeg buffers are invisible to tracemalloc), one case at a time: the largest PNG the cap
    allows, a progressive JPEG at the cap (4:4:4, the costliest layout), the largest single-scan baseline JPEG (16 M pixels,
    decoded at reduced scale), and progressive / baseline / multi-scan headers far over their caps (13300 x 13300 was +544 MB when
    the cap was checked after draft). Each must add <= 40 MB at peak."""
    import os
    import subprocess
    import sys
    gen = r"""
import sys
from PIL import Image
w, h, fmt, prog, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4] == "1", sys.argv[5]
if fmt == "PNG":
    Image.new("RGBA", (w, h), (200, 30, 30, 255)).save(out, "PNG")
else:
    Image.new("RGB", (w, h), (90, 120, 160)).save(out, "JPEG", quality=60, progressive=prog, subsampling=0)
"""
    cases = {}
    for name, (w, h, fmt, prog) in {"png_2048": (2048, 2048, "PNG", 0), "prog_jpeg_2048_444": (2048, 2048, "JPEG", 1),
                                    "baseline_jpeg_4000": (4000, 4000, "JPEG", 0)}.items():
        f = tmp_path / (name + ".bin")
        subprocess.run([sys.executable, "-c", gen, str(w), str(h), fmt, str(prog), str(f)], check=True, timeout=120)
        cases[name] = (f, True)
    for name, (w, h, prog, sof) in {"prog_jpeg_13300_hdr": (13300, 13300, True, None),
                                    "prog_jpeg_7000_hdr": (7000, 7000, True, None),
                                    "multiscan_jpeg_7000_hdr": (7000, 7000, True, 0xC0),
                                    "baseline_jpeg_13300_hdr": (13300, 13300, False, None)}.items():
        f = tmp_path / (name + ".bin")
        f.write_bytes(_jpeg_sized(w, h, progressive=prog, sof=sof))
        cases[name] = (f, False)
    f = tmp_path / "ns1_jpeg_4000_444.bin"                       # real 16 M pixel data, one scan of 1 of 3 components (+97 MB before)
    f.write_bytes(_one_component_scan(cases["baseline_jpeg_4000"][0].read_bytes()))
    cases["ns1_jpeg_4000_444"] = (f, False)
    code = r"""
import io, sys, psutil, img_cache as ic
from PIL import Image
p = psutil.Process()
def peak():
    m = p.memory_info()
    return getattr(m, "peak_wset", None) or __import__("resource").getrusage(__import__("resource").RUSAGE_SELF).ru_maxrss * 1024
w = io.BytesIO(); Image.new("RGBA", (300, 300), (1, 2, 3, 255)).save(w, "PNG")
j = io.BytesIO(); Image.new("RGB", (300, 300), (1, 2, 3)).save(j, "JPEG", progressive=True)
for raw in (w.getvalue(), j.getvalue()):
    ic.encode(raw, "p"); ic.encode(raw, "t")                        # plugins and encoders loaded before the baseline
raw = open(sys.argv[1], "rb").read()
base = peak()
try:
    ic.encode(raw, "p"); ic.encode(raw, "t"); r = "ok"
except ic.Refused as e:
    r = "refused:" + str(e)
print(r, int(peak() - base))
"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name, (f, accepted) in cases.items():
        r = subprocess.run([sys.executable, "-c", code, str(f)], capture_output=True, text=True, timeout=120, cwd=root)
        assert r.returncode == 0, (name, r.stderr)
        res, grew = r.stdout.strip().splitlines()[-1].split()
        print("encode peak RSS growth", name, res, round(int(grew) / 1e6, 1), "MB")
        assert (res == "ok") == accepted, (name, res)
        assert int(grew) <= 40_000_000, (name, grew)


def test_review_shapes_refused_before_decode_1120():
    """Review 2026-11-20 decode-bomb shapes: big WEBP, strips, PNG text bombs are refused up front; real-sized art passes."""
    import zlib, struct
    from PIL import Image
    import img_cache as ic

    def png(w, h, extra=b""):
        b = io.BytesIO(); Image.new("RGB", (w, h), (20, 40, 60)).save(b, "PNG"); d = b.getvalue()
        return d[:33] + extra + d[33:]                 # after IHDR

    def ztxt(i):
        body = b"k%d\x00\x00" % i + zlib.compress(b"\x00" * 1_000_000, 9)
        return struct.pack(">I", len(body)) + b"zTXt" + body + struct.pack(">I", zlib.crc32(b"zTXt" + body) & 0xffffffff)

    with pytest.raises(ic.Refused) as e:
        ic.encode(png(64, 64, b"".join(ztxt(i) for i in range(70))), "t")
    assert "png text" in str(e.value)
    with pytest.raises(ic.Refused) as e:
        ic.encode(png(24, 2400), "t")
    assert "aspect" in str(e.value)
    b = io.BytesIO(); Image.new("RGB", (1600, 1600)).save(b, "WEBP", lossless=True)
    with pytest.raises(ic.Refused) as e:
        ic.encode(b.getvalue(), "t")
    assert "dims" in str(e.value)
    b = io.BytesIO(); Image.new("RGB", (1319, 1681), (200, 30, 30)).save(b, "WEBP", quality=80)
    assert ic.encode(b.getvalue(), "t") and ic.encode(b.getvalue(), "p")
