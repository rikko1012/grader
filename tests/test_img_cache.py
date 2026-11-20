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
    "https://a.espncdn.com.evil.test/i/x.png", "https://evil.test/?u=https://a.espncdn.com/x.png", "javascript:alert(1)",
    "https://static.prizepicks.com/images/players/placeholder.png", "https://static.prizepicks.com/x/Player_Avatar.png",
    "https://static.prizepicks.com/images/teams/nfl/jersey_12.png", "https://a.espncdn.com/i/default_headshot.png", None, 7, ""])
def test_only_allowed_https_hosts_and_never_placeholder_art(url):
    assert ic.safe_url(url) is None


def test_allowed_urls_are_kept_and_encoded():
    assert ic.safe_url("https://a.espncdn.com/i/headshots/nba/players/full/1.png") == "https://a.espncdn.com/i/headshots/nba/players/full/1.png"
    assert ic.safe_url("http://static.lolesports.com/x.png") is None                       # not approved (owner decision pending)
    assert ic.safe_url("http://files.bo3.gg/uploads/a.webp") == "https://files.bo3.gg/uploads/a.webp"   # http upgraded on an allowed host
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
def test_bo3_rosters_give_pictures_but_never_to_benched_or_coaches():
    players, teams = {}, {}
    ix._bo3_take({"id": 7, "slug": "t", "name": "Team Seven", "image_url": "https://files.bo3.gg/t7.webp",
                  "players": [{"id": 1, "image_url": "https://files.bo3.gg/p1.webp", "status": 1},
                              {"id": 2, "image_url": "https://files.bo3.gg/p2.webp", "status": 2},
                              {"id": 3, "image_url": "https://files.bo3.gg/p3.webp", "status": 1, "is_coach": True}]},
                 10.0, players, teams)
    ix._BO3.update(players=players, teams=teams)
    try:
        assert ix.bo3_player(1) == ("https://files.bo3.gg/p1.webp", 7)
        assert ix.bo3_player("2") == (None, None) and ix.bo3_player(3) == (None, None) and ix.bo3_player("x") == (None, None)
        assert ix.bo3_team(7) == ("https://files.bo3.gg/t7.webp", "Team Seven")
    finally:
        ix.reset()


def test_bo3_scan_reads_the_saved_answers_incrementally(tmp_path):
    import sqlite3
    import zlib
    db = tmp_path / "cache.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE kv (bucket TEXT, name TEXT, at REAL, data BLOB)")
    team = {"id": 9, "slug": "nine", "name": "Nine", "image_url": "https://files.bo3.gg/t9.webp",
            "players": [{"id": 5, "image_url": "https://files.bo3.gg/p5.webp", "status": 1}]}
    con.execute("INSERT INTO kv VALUES ('bo3','a',1.0,?)", (zlib.compress(json.dumps(team).encode()),))
    con.execute("INSERT INTO kv VALUES ('games','b',2.0,?)", (zlib.compress(json.dumps(team).encode()),))
    con.commit()
    ix.reset()
    try:
        assert ix.bo3_scan(db) == 1
        assert ix.bo3_player(5)[0] == "https://files.bo3.gg/p5.webp"
        assert ix.bo3_scan(db) == 0                    # nothing newer: nothing read again
        team["players"][0]["status"] = 2               # benched later
        con.execute("INSERT INTO kv VALUES ('bo3','c',3.0,?)", (zlib.compress(json.dumps(team).encode()),))
        con.commit()
        assert ix.bo3_scan(db) == 1 and ix.bo3_player(5) == (None, None)
    finally:
        con.close()
        ix.reset()


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


def test_cs2_team_logo_only_when_the_bo3_team_name_matches_the_row(cache, monkeypatch):
    ix._BO3.update(players={1: (1.0, "https://files.bo3.gg/p1.webp", 1, 7, False), 2: (1.0, "https://files.bo3.gg/p2.webp", 1, 8, False)},
                   teams={7: (1.0, "https://files.bo3.gg/t7.webp", "Team Seven"), 8: (1.0, "https://files.bo3.gg/t8.webp", "Eight")})
    recs = [_rec("cs2", 1, "one", "Team Seven"), _rec("cs2", 2, "two", "Eight Academy")]
    body = json.dumps({"records": recs}).encode()
    monkeypatch.setattr(ix, "_records", lambda srv, sport: (body, json.loads(body)["records"], {"body": body}))
    ix.build(None, "cs2")
    assert ("t", "cs2", "7") in ic._SRC and ("t", "cs2", "8") not in ic._SRC   # "Eight Academy" is not "Eight": no logo
    for k in list(ic._SRC):
        ic.fetch_one(k, get=lambda u: png(354, 455))
    out = json.loads(ix.build(None, "cs2")[1])
    assert out["p"]["1"][1] and out["p"]["2"][1] == "" and out["tn"] == {"Team Seven": out["p"]["1"][1]}


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
    assert get(env.base, "/img/index/cs2.json")[0] == 403              # the board's player list: members only
    monkeypatch.setattr(ix, "build", lambda srv, sport: (b"x", b'{"n":{},"p":{},"tn":{}}'))
    monkeypatch.setattr(ix, "_body_id", lambda srv, sport: None)
    assert get(env.base, "/img/index/cs2.json", "good-u1")[0] == 404    # SCOUT_UPGRADE=owner: a member has no hub board here
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    st, body, h = get(env.base, "/img/index/cs2.json", "good-u1")
    assert st == 200 and json.loads(body) == {"n": {}, "p": {}, "tn": {}} and h.get("ETag")
    for sport in ("nba", "soccer"):                                     # sports this member cannot see on the board: 404 here too
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
