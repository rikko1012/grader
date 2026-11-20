"""hltv_maps.py + /api/upgrade/player-maps (v3 player card, HLTV only - owner 2026-11-20 "stop using BO3.gg we have HLTV").

- raw totals per map / window are exact (kills, deaths, rounds, headshots on maps with a headshot row, round-weighted sums, wins);
  the combine of any two maps = summed totals (HLTV maths), never an average of averages;
- the read is by the projections' exact HLTV id (hltv._player_ids) with HLTV-sourced rows only, bounded (LIMIT);
- one database read per player whatever the crowd (700 concurrent callers -> 1 read), a failed read keeps the last good answer and
  is never cached as "no data", the cache stays bounded;
- the current pool: the match's own HLTV veto pool, else the last 7 days of HLTV vetoes, else ACTIVE_DUTY; display names, no 'de_';
- the endpoint: v3 only (off = the unknown-route answer, byte for byte), members only, the player comes from the board row (never
  from the request), 700 concurrent members = one build."""
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from fractions import Fraction as F

import pytest

import hltv
import hltv_maps
from tests import hltv_maps_fixture as HF


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(hltv, "_CACHE", {})
    monkeypatch.setattr(hltv, "_META", {})
    monkeypatch.setattr(hltv, "_FLIGHTS", {})
    import hltv_store
    monkeypatch.setattr(hltv_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(hltv_store, "load", lambda *a, **k: None)
    monkeypatch.setattr(hltv_maps, "LOADER", None)


# ---------------------------------------------------------------- maths
def test_totals_are_exact_raw_sums_per_map_and_window():
    doc = hltv_maps.build(HF.rows(), date.today(), HF.HLTV_ID)
    assert doc["source"] == "HLTV" and doc["hltv_id"] == HF.HLTV_ID
    for label, days in (("12m", 365), ("3m", 91)):
        want = HF.per_map(days)
        got = doc["windows"][label]["maps"]
        assert set(got) == set(want), label
        for k, w in want.items():
            g = got[k]
            for f in ("maps", "rounds", "kills", "deaths", "hs", "hs_kills", "hs_maps", "wins", "decided"):
                assert g[f] == w[f], (label, k, f)
            assert abs(g["rating_x_rounds"] - float(w["rx"])) < 1e-6 and g["rating_rounds"] == w["rr"]
    # all maps = the sum of every map (12 months)
    over = doc["windows"]["12m"]["overall"]
    assert over["kills"] == sum(r[3] for r in HF.rows()) and over["maps"] == len(HF.rows())


def test_headshots_count_only_maps_with_a_real_headshot_row():
    rs = HF.rows()
    t = hltv_maps.totals(rs)
    none_row = [r for r in rs if r[5] is None]
    zero_row = [r for r in rs if r[5] == 0 and r[3] >= 12]
    assert none_row and zero_row
    assert t["hs_maps"] == len(rs) - len(none_row) - len(zero_row)
    assert t["hs_kills"] == sum(r[3] for r in rs if r[5] is not None and not (r[5] == 0 and r[3] >= 12))


def test_missing_values_stay_missing_never_zero():
    t = hltv_maps.totals([("Nuke", "2026-11-01", 1, 10, 12, None, 13, 9, None, None, None)])
    assert t["hs_maps"] == 0 and t["rating_x_rounds"] is None and t["adr_x_rounds"] is None and t["kast_x_rounds"] is None
    assert hltv_maps.totals([]) is None


def test_pooled_two_maps_is_summed_totals_hltv_maths():
    """Σkills/Σmaps, Σkills/Σrounds, Σhs/Σhs-kills, Σkills/Σdeaths, Σ(rating x rounds)/Σrounds - for every pair of pool maps."""
    doc = hltv_maps.build(HF.rows(), date.today(), HF.HLTV_ID)
    per = doc["windows"]["12m"]["maps"]
    ref = HF.per_map(365)
    keys = sorted(per)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            A, B = ref[a], ref[b]
            kpm = F(A["kills"] + B["kills"], A["maps"] + B["maps"])
            kpr = F(A["kills"] + B["kills"], A["rounds"] + B["rounds"])
            hs = F(100 * (A["hs"] + B["hs"]), A["hs_kills"] + B["hs_kills"])
            kd = F(A["kills"] + B["kills"], A["deaths"] + B["deaths"])
            rt = (A["rx"] + B["rx"]) / (A["rr"] + B["rr"])
            ga, gb = per[a], per[b]
            assert F(ga["kills"] + gb["kills"], ga["maps"] + gb["maps"]) == kpm
            assert F(ga["kills"] + gb["kills"], ga["rounds"] + gb["rounds"]) == kpr
            assert F(100 * (ga["hs"] + gb["hs"]), ga["hs_kills"] + gb["hs_kills"]) == hs
            assert F(ga["kills"] + gb["kills"], ga["deaths"] + gb["deaths"]) == kd
            assert abs((ga["rating_x_rounds"] + gb["rating_x_rounds"]) / (ga["rating_rounds"] + gb["rating_rounds"]) - float(rt)) < 1e-9
            # never the average of the two maps' averages (it differs whenever the map counts differ)
            if A["maps"] != B["maps"]:
                assert (F(A["kills"], A["maps"]) + F(B["kills"], B["maps"])) / 2 != kpm or F(A["kills"], A["maps"]) == F(B["kills"], B["maps"])


# ---------------------------------------------------------------- names and pool
@pytest.mark.parametrize("raw,key,name", [("de_dust2", "dust2", "Dust 2"), ("Dust 2", "dust2", "Dust 2"), ("de_ancient", "ancient", "Ancient"),
                                          ("Nuke", "nuke", "Nuke"), ("de_overpass", "overpass", "Overpass"), ("Train", "train", "Train"),
                                          ("de_cache", "cache", "Cache"), ("Mirage", "mirage", "Mirage"), ("de_anubis", "anubis", "Anubis"),
                                          ("de_inferno", "inferno", "Inferno")])
def test_map_names_are_display_names_never_de_prefixed(raw, key, name):
    assert hltv_maps.map_key(raw) == key
    assert hltv_maps.display(key) == name
    assert "de_" not in hltv_maps.display(key, raw)


def test_active_duty_fallback_is_the_verified_pool():
    assert hltv_maps.ACTIVE_DUTY == ("ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke")
    assert "overpass" not in hltv_maps.ACTIVE_DUTY and "train" not in hltv_maps.ACTIVE_DUTY


def test_pool_follows_hltv_vetoes_match_first_then_recent_then_constant(monkeypatch):
    import veto_serve as vs
    monkeypatch.setattr(vs, "_STORE", {})
    monkeypatch.setattr(vs, "STATE", dict(vs.STATE, table_pools=None))
    assert hltv_maps.pool_for("hltv:1") == (list(hltv_maps.ACTIVE_DUTY), "constant")
    recent = ["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "overpass"]
    monkeypatch.setattr(vs, "STATE", dict(vs.STATE, table_pools={"recent": recent}))
    assert hltv_maps.pool_for("hltv:1") == (recent, "recent")
    mine = ["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "train"]
    vs._STORE["hltv:1"] = {"vm1": {"pool": mine}}
    assert hltv_maps.pool_for("hltv:1") == (mine, "match")
    vs._STORE["hltv:1"] = {"vm1": {"pool": mine[:5]}}               # an incomplete pool is never used
    assert hltv_maps.pool_for("hltv:1") == (recent, "recent")


def test_card_doc_names_and_pool(monkeypatch):
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid: (list(hltv_maps.ACTIVE_DUTY), "constant"))
    doc = hltv_maps.card_doc("vargo", "Halcyon", "hltv:1", loader=HF.loader)
    assert doc["state"] == "ok" and doc["pool"]["keys"] == list(hltv_maps.ACTIVE_DUTY)
    assert doc["pool"]["names"]["dust2"] == "Dust 2" and doc["names"]["overpass"] == "Overpass"
    assert not any(str(v).startswith("de_") for v in doc["names"].values())
    json.dumps(doc)                                                  # serialisable as it is


# ---------------------------------------------------------------- the database read: exact id, HLTV rows only, bounded
class Cur:
    def __init__(self, rows, fail_source=False):
        self.rows, self.sql, self.params, self.fail_source = rows, [], [], fail_source

    def execute(self, sql, params=None):
        if self.fail_source and "mo.source" in sql:
            self.fail_source = False
            raise Exception('column mo.source does not exist')
        self.sql.append(sql)
        self.params.append(params)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Con:
    def __init__(self, cur):
        self.c = cur
        self.closed = False

    def cursor(self):
        return self.c

    def close(self):
        self.closed = True


def test_read_uses_the_projections_exact_hltv_id_and_hltv_rows_only(monkeypatch):
    cur = Cur(HF.rows())
    con = Con(cur)
    seen = []
    monkeypatch.setattr(hltv, "_connect", lambda: con)
    monkeypatch.setattr(hltv, "_player_ids", lambda c, nick, team: seen.append((nick, team)) or [11205])
    pid, rows = hltv_maps._load("stadodo", "RBLS", "2025-11-20")
    assert pid == 11205 and seen == [("stadodo", "RBLS")] and con.closed
    assert cur.params[0]["ids"] == [11205] and cur.params[0]["lim"] == hltv_maps.MAX_ROWS and cur.params[0]["since"] == "2025-11-20"
    sql = cur.sql[0]
    assert "mo.source = 'hltv'" in sql and "LIMIT %(lim)s" in sql and "ps.sideid = 0" in sql and "ps.playerid = ANY(%(ids)s)" in sql
    assert "lower(" not in sql and "LIKE" not in sql.upper()           # never a name match in the stats read: the id only


def test_read_on_an_older_database_without_the_source_column(monkeypatch):
    cur = Cur(HF.rows(), fail_source=True)
    monkeypatch.setattr(hltv, "_connect", lambda: Con(cur))
    monkeypatch.setattr(hltv, "_player_ids", lambda c, nick, team: [7])
    pid, rows = hltv_maps._load("x", "y", "2025-11-20")
    assert pid == 7 and rows and "mo.source" not in cur.sql[-1]


def test_no_exact_hltv_id_is_none_not_a_guess(monkeypatch):
    monkeypatch.setattr(hltv, "_connect", lambda: Con(Cur([])))
    monkeypatch.setattr(hltv, "_player_ids", lambda c, nick, team: [])
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test")
    assert hltv_maps.for_player("stadodo_academy", "RBLS Academy")["state"] == "none"


# ---------------------------------------------------------------- caching: single flight, failures, bounds
def test_700_concurrent_callers_make_one_read(monkeypatch):
    calls = []
    gate = threading.Event()

    def slow(nick, team, since):
        calls.append(1)
        gate.wait(2)
        return HF.loader(nick, team, since)
    out = []
    with ThreadPoolExecutor(max_workers=700) as ex:
        futs = [ex.submit(hltv_maps.for_player, "vargo", "Halcyon", slow) for _ in range(700)]
        threading.Timer(0.5, gate.set).start()
        out = [f.result(timeout=60) for f in futs]
    assert len(calls) == 1, len(calls)
    assert all(o["state"] == "ok" and o["windows"]["12m"]["overall"]["maps"] == len(HF.rows()) for o in out)


def test_a_failed_read_keeps_the_last_good_answer_and_is_never_no_data(monkeypatch):
    good = hltv_maps.for_player("vargo", "Halcyon", HF.loader)
    assert good["state"] == "ok"
    key = hltv_maps._key("vargo", "Halcyon")
    hltv._CACHE[key] = (0.0, good, True)                         # expired

    def down(*a):
        raise TimeoutError("connection timed out")
    again = hltv_maps.for_player("vargo", "Halcyon", down)
    assert again is good or again == good                        # the last good answer, not "none" / not empty
    # no good answer anywhere: "unavailable" (the endpoint answers 503, the card a retry), kept only FAIL_TTL
    bad = hltv_maps.for_player("nobody", "None", down)
    assert bad["state"] == "unavailable"
    ts = hltv._CACHE[hltv_maps._key("nobody", "None")][0]
    import time
    assert time.time() - ts >= hltv.TTL - hltv.FAIL_TTL - 1


def test_cache_is_bounded(monkeypatch):
    for i in range(5100):
        hltv._remember(("hmaps", f"p{i}", "t"), {"state": "none"})
    assert len(hltv._CACHE) <= 5001


# ---------------------------------------------------------------- the endpoint
def _get(env, path, cookie=True):
    req = urllib.request.Request(env.url + path)
    if cookie:
        import whop_auth
        req.add_header("Cookie", f"{whop_auth.COOKIE}=good-owner")
    for attempt in range(20):                       # the test server's small listen backlog drops a socket under a crowd: reconnect
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, ConnectionError):
            import time
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("no connection")


def _cs2_row(env):
    r = next(r for r in env.rows if r.get("nick") == "vargo" or r.get("player") == "vargo")
    return r.get("book") or r.get("platform") or "prizepicks", r["id"]


from tests.test_upgrade_browser import env  # noqa: E402,F401


def test_endpoint_off_is_the_unknown_route_answer_byte_for_byte(env, monkeypatch):
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    book, rid = _cs2_row(env)
    a = _get(env, f"/api/upgrade/player-maps?sport=cs2&book={book}&id={rid}")
    b = _get(env, f"/api/upgrade/no-such-route?sport=cs2&book={book}&id={rid}")
    assert a == b and a[0] == 404


def test_endpoint_on_members_only_board_row_player(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seen = []

    def loader(nick, team, since):
        seen.append((nick, team))
        return HF.loader(nick, team, since)
    monkeypatch.setattr(hltv_maps, "LOADER", loader)
    book, rid = _cs2_row(env)
    st, body = _get(env, f"/api/upgrade/player-maps?sport=cs2&book={book}&id={rid}&nick=s1mple&team=NAVI", cookie=False)
    assert st in (401, 404) and not seen                                            # not a member: never reaches the read
    st, body = _get(env, f"/api/upgrade/player-maps?sport=cs2&book={book}&id={rid}&nick=s1mple&team=NAVI")
    assert st == 200, body
    doc = json.loads(body)
    assert seen and seen[0][0] == "vargo" and "s1mple" not in json.dumps(seen)          # the board row's player, never the request's
    assert doc["state"] == "ok" and doc["source"] == "HLTV" and doc["pool"]["keys"]
    assert doc["windows"]["12m"]["maps"]["mirage"]["kills"] == HF.per_map()["mirage"]["kills"]
    assert _get(env, f"/api/upgrade/player-maps?sport=cs2&book={book}&id=pp:nope")[0] == 404
    assert _get(env, f"/api/upgrade/player-maps?sport=lol&book={book}&id={rid}")[0] == 404


def test_endpoint_hltv_down_is_503_never_data(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "1")

    def down(*a):
        raise TimeoutError("timed out")
    monkeypatch.setattr(hltv_maps, "LOADER", down)
    book, rid = _cs2_row(env)
    st, body = _get(env, f"/api/upgrade/player-maps?sport=cs2&book={book}&id={rid}")
    assert st == 503 and b"hltv_unavailable" in body


def test_endpoint_700_concurrent_members_one_build(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    calls = []

    def loader(nick, team, since):
        calls.append(1)
        import time
        time.sleep(0.4)
        return HF.loader(nick, team, since)
    monkeypatch.setattr(hltv_maps, "LOADER", loader)
    book, rid = _cs2_row(env)
    path = f"/api/upgrade/player-maps?sport=cs2&book={book}&id={rid}"
    with ThreadPoolExecutor(max_workers=64) as ex:
        res = list(ex.map(lambda _: _get(env, path), range(700)))
    bad = [s for s, _ in res if s != 200]
    assert not bad, bad[:5]
    assert len(calls) == 1, len(calls)
    assert len({b for _, b in res}) == 1
