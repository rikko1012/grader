"""New pick'em app readers (owner 2026-11-05: Betr, ParlayPlay, Boom, Dabble). Dabble answers without credentials and is read like
Sleeper (book_providers.poll_dabble, one daemon poller, never a member request); Betr (401 + location check), ParlayPlay (Cloudflare
403) and Boom (app only) are config entries marked blocked with their evidence: no flag, no poller. No network: every fetch is a fake
built from the samples saved by the 2026-11-05 probe (tests/fixtures/dabble)."""
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import book_providers as bp
import workspace_books as wb
from tests.test_workspace_api import env, get, records, API  # noqa: F401

FX = Path(__file__).parent / "fixtures" / "dabble"
FLAGS = ("SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE", "SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_ODDSAPI")
NOW = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc).timestamp()          # before both sample fixtures start
CS2_ID = "01a18e35-266b-72f9-9d1f-e688d72645c7"


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for f in FLAGS:
        monkeypatch.delenv(f, raising=False)
    bp.reset()
    wb.reset()
    monkeypatch.setattr(bp, "start", lambda data_dir: False)
    yield
    bp.reset()


def stats():
    return bp.providers()["dabble"]["stats"]


def lol_id():
    return load("lol_details.json")["data"]["id"]


class Fake:
    """api.dabble.com from the saved samples: competitions, one CS2 and one LoL fixture, nothing for NBA / NFL."""

    def __init__(self):
        self.calls, self.err, self.status, self.fail_details = [], None, 200, set()
        self.details = {CS2_ID: load("cs2_details.json"), lol_id(): load("lol_details.json")}

    def __call__(self, url, etag=None, timeout=None, tries=None):
        self.calls.append((url, etag))
        if self.err:
            raise self.err
        path = url.split("api.dabble.com", 1)[1]
        if path == "/competitions/active/":
            return 200, json.dumps(load("competitions_active.json")).encode(), None
        if path.endswith("/sport-fixtures"):
            cid = path.split("/")[2]
            docs = [d["data"] for d in self.details.values() if d["data"]["competitionId"] == cid]
            return 200, json.dumps({"data": [{"id": d["id"], "status": d["status"], "advertisedStart": d["advertisedStart"]} for d in docs]}).encode(), None
        fid = path.rsplit("/", 1)[1]
        if fid in self.fail_details:
            raise bp.FetchError("HTTP 502", 502)
        if self.status == 304 and etag:
            return 304, None, etag
        return 200, json.dumps(self.details[fid]).encode(), 'W/"' + fid[-6:] + '"'


# ---------------------------------------------------------------------------------------------------------- config

def test_dabble_is_a_direct_pickem_provider_with_sourced_rules_and_observed_stats_only():
    p = bp.providers()["dabble"]
    assert p["flag"] == "SCOUT_BOOK_DABBLE" and p["source"] == "direct" and p["kind"] == "pickem" and "dabble" in bp.POLLERS
    assert set(p["sports"]) == {"cs2", "lol", "nba", "nfl"}
    f = p["feed"]
    assert f["poll_s"] >= 120 and f["stale_after_s"] > f["poll_s"] and f["checked_at"] and f["checked_note"]
    assert all(s["url"].startswith("https://help.dabblefantasy.com/") for s in p["rules"]["sources"])
    assert p["rules"]["all_in"]["2"] == 3 and p["rules"]["hedge"]["16"][0] == 10000
    # only keys the 2026-11-05 probe saw on the live feed; no Game 2, quarter / half or touchdown market is mapped
    seen = {"cs2": {"game-1-kills", "total-kills-map-one-and-map-two", "game-1-headshots"}, "lol": {"game-1-kills", "game-1-assists"}}
    for sport, keys in seen.items():
        assert set(p["stats"][sport]) <= keys
    assert not any(k.startswith(("game-2", "first-", "second-", "third-", "fourth-")) or "touchdown" in k.replace("passing-touchdowns", "")
                   for d in p["stats"].values() for k in d)


def test_blocked_apps_have_evidence_no_flag_and_no_poller():
    blocked = bp.config()["blocked"]
    assert set(blocked) == {"betr", "parlayplay", "boom"}
    for pid, b in blocked.items():
        assert b["status"] == "blocked" and b["reason"] and b["checked_at"] and b["evidence"] and b["endpoints"], pid
        assert pid not in bp.providers() and pid not in bp.POLLERS and not bp.enabled(pid) and "flag" not in b
        assert all(s["url"] for s in b["rules"]["sources"])
    assert any(e["status"] == 401 for e in blocked["betr"]["endpoints"])
    assert any(e["status"] == 403 for e in blocked["parlayplay"]["endpoints"])
    assert "parlayplay" not in bp.config()["not_added"] and "dabble" not in bp.config()["not_added"]


def test_default_on_has_dabble_and_no_blocked_app():
    import server
    assert "SCOUT_BOOK_DABBLE" in server.DEFAULT_ON and "SCOUT_BOOK_SLEEPER" in server.DEFAULT_ON
    assert not {"SCOUT_BOOK_BETR", "SCOUT_BOOK_PARLAYPLAY", "SCOUT_BOOK_BOOM"} & set(server.DEFAULT_ON)
    env = {"SCOUT_BOOK_DABBLE": "0"}
    server.apply_default_flags(env)
    assert env["SCOUT_BOOK_DABBLE"] == "0"                                          # Render "0" turns it off


def test_flag_off_nothing_runs(tmp_path):
    assert bp.offers("dabble") == [] and bp.features() is None
    import importlib
    real = importlib.reload(bp)
    assert real.start(tmp_path) is False and real._THREAD["t"] is None


# ---------------------------------------------------------------------------------------------------------- parsing

def test_parse_cs2_sample_maps_observed_keys_and_counts_game_2():
    offers, skipped = bp.parse_dabble(load("cs2_details.json"), NOW, "cs2", stats())
    got = {(o["player"], o["stat"], o["period"], o["line"]) for o in offers}
    assert got == {("w0nderful", "kills", "map1", 17.5), ("Aleksib", "hs", "map1", 5.5), ("iM", "kills", "maps12", 28.5)}
    assert skipped == {"unmapped stat: cs2 game-2-headshots": 1, "unmapped stat: cs2 game-2-kills": 1}
    w = next(o for o in offers if o["player"] == "w0nderful")
    assert w["team"] == "Natus Vincere" and w["team_norm"] == "natusvincere" and w["game_id"] == CS2_ID
    assert w["over"]["offered"] and w["over"]["price"] > 1 and w["over"]["payout"] is None and w["source_at"] is None
    assert w["raw_stat"] == "game-1-kills" and w["provider"] == "dabble"


def test_parse_lol_sample():
    offers, skipped = bp.parse_dabble(load("lol_details.json"), NOW, "lol", stats())
    assert {(o["player"], o["stat"], o["period"], o["line"]) for o in offers} == {("ShowMaker", "kills", "map1", 3.5), ("Bin", "assists", "map1", 4.5)}
    assert skipped == {"unmapped stat: lol game-2-assists": 1, "unmapped stat: lol game-2-kills": 1}


def test_parse_started_or_closed_fixture_offers_nothing():
    doc = load("cs2_details.json")
    start = bp._iso_ts(doc["data"]["advertisedStart"])
    assert bp.parse_dabble(doc, start + 1, "cs2", stats())[0] == []
    doc["data"]["status"] = "Suspended"
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert offers == [] and skipped["not open before the game"] == 5


def _one(doc, player):
    """The two playerProps rows and the market of one player's market in a sample."""
    d = doc["data"]
    rows = [r for r in d["playerProps"] if r["playerName"] == player]
    return d, rows


def test_a_side_without_a_price_row_is_not_offered_and_unpriced_markets_are_left_out():
    doc = load("cs2_details.json")
    d, rows = _one(doc, "w0nderful")
    over = next(r for r in rows if r["lineType"] == "over")
    d["prices"] = [p for p in d["prices"] if p["selectionId"] != over["selectionId"]]
    w = next(o for o in bp.parse_dabble(doc, NOW, "cs2", stats())[0] if o["player"] == "w0nderful")
    assert w["over"] == {"offered": False, "payout": None, "price": None} and w["under"]["offered"]
    d["prices"] = [p for p in d["prices"] if p["marketId"] != over["marketId"]]
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert "w0nderful" not in {o["player"] for o in offers} and skipped["no price on either side"] == 1


def test_closed_or_non_pickem_market_and_alternate_lines_are_skipped():
    doc = load("cs2_details.json")
    d, rows = _one(doc, "w0nderful")
    next(m for m in d["markets"] if m["id"] == rows[0]["marketId"])["isDfsAllowed"] = False
    d2, rows2 = _one(doc, "iM")
    for r in rows2:
        r["isOptimal"] = False
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert {o["player"] for o in offers} == {"Aleksib"}
    assert skipped["market not open for pick'em"] == 1 and skipped["alternate line (not the main line)"] == 1


def test_two_players_with_one_name_are_left_out_and_counted():
    doc = load("cs2_details.json")
    d, rows = _one(doc, "iM")
    clones = copy.deepcopy(rows)
    for r in clones:
        r.update(playerName="w0nderful", playerId="other-player", stats=["game-1-kills"], value=12.5)
    d["playerProps"] += clones
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert not any(o["player"] == "w0nderful" for o in offers) and skipped["two players share this name"] == 2


def test_two_main_lines_for_one_market_are_left_out_and_counted():
    doc = load("cs2_details.json")
    d, rows = _one(doc, "w0nderful")
    alt = copy.deepcopy(rows)
    for r in alt:
        r["value"] = 19.5                                       # same market id, a second line: grouped apart by value
    d["playerProps"] += alt
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert not any(o["player"] == "w0nderful" for o in offers) and skipped["two main lines for one market"] == 2


def test_combo_keys_are_sorted_exact_and_unknown_names_are_counted_never_guessed():
    assert bp.dabble_stat_key(["points", "assists", "rebounds"]) == "assists+points+rebounds"
    assert bp.dabble_stat_key([]) is None and bp.dabble_stat_key("points") is None
    doc = load("cs2_details.json")
    d, rows = _one(doc, "w0nderful")
    for r in rows:
        r["stats"] = ["game-1-kill"]                             # one letter off: unmapped, never fuzzy-matched
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert "w0nderful" not in {o["player"] for o in offers} and skipped["unmapped stat: cs2 game-1-kill"] == 1


def test_sides_that_disagree_are_skipped():
    doc = load("cs2_details.json")
    d, rows = _one(doc, "w0nderful")
    rows[0]["playerId"] = "someone-else"
    offers, skipped = bp.parse_dabble(doc, NOW, "cs2", stats())
    assert "w0nderful" not in {o["player"] for o in offers} and skipped["sides disagree"] == 1


def test_competitions_by_exact_name_then_id_never_fuzzy():
    doc = load("competitions_active.json")
    want = bp.providers()["dabble"]["feed"]["competitions"]
    got = bp.parse_dabble_competitions(doc, want)
    assert got == {s: w["id"] for s, w in want.items()}
    for c in doc["data"]["activeCompetitions"]:
        if c["name"] == "LoL":
            c["name"] = "LoL Academy"                            # renamed: the id still names it
        if c["name"] == "NBA":
            c.update(name="NBA Summer", id="x")                  # neither name nor id: absent, never "closest name"
    got = bp.parse_dabble_competitions(doc, want)
    assert got["lol"] == want["lol"]["id"] and "nba" not in got


def test_fixture_list_keeps_open_fixtures_inside_the_horizon():
    doc = {"data": [{"id": "a", "status": "Open", "advertisedStart": "2026-11-06T06:30:00.000Z"},
                    {"id": "b", "status": "Open", "advertisedStart": "2026-11-09T06:30:00.000Z"},
                    {"id": "c", "status": "Closed", "advertisedStart": "2026-11-06T06:30:00.000Z"},
                    {"id": "d", "status": "Open", "advertisedStart": "2026-11-05T19:00:00.000Z"}]}
    assert [f for f, _ in bp.parse_dabble_fixtures(doc, NOW, 48 * 3600)] == ["a"]


# ---------------------------------------------------------------------------------------------------------- polling

def test_poll_rate_limit_etag_fixture_refresh_and_health(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    feed = bp.providers()["dabble"]["feed"]
    fake = Fake()
    st = bp.poll_dabble(tmp_path, now=NOW, fetch=fake, sleep=lambda s: None)
    assert st["at"] == NOW and st["failures"] == 0 and len(st["offers"]) == 5
    assert {o["sport"] for o in st["offers"]} == {"cs2", "lol"} and st["fixtures_read"] == 2
    assert st["skipped"]["unmapped stat: cs2 game-2-kills"] == 1
    n = len(fake.calls)
    assert sum("/sport-fixtures" in u for u, _ in fake.calls) == 4                   # one list per sport the config names
    assert not list(tmp_path.iterdir())                                               # Dabble writes no file
    bp.poll_dabble(tmp_path, now=NOW + 10, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == n                                                       # not due before poll_s
    fake.status = 304
    t = NOW + feed["poll_s"]
    st = bp.poll_dabble(tmp_path, now=t, fetch=fake, sleep=lambda s: None)
    new = fake.calls[n:]
    assert all(e for u, e in new) and not any("competitions" in u for u, _ in new)    # conditional, list not re-read yet
    assert st["not_modified"] == 2 and len(st["offers"]) == 5 and all(o["observed_at"] == t for o in st["offers"])
    st = bp.poll_dabble(tmp_path, now=NOW + feed["fixtures_refresh_s"], fetch=fake, sleep=lambda s: None)
    assert any("competitions/active" in u for u, _ in fake.calls[n + len(new):])      # fixture list re-read on its own clock
    h = {x["id"]: x for x in bp.health(NOW + feed["fixtures_refresh_s"])}["dabble"]
    assert h["state"] == "ok" and h["offers"] == 5 and h["by_sport"] == {"cs2": 3, "lol": 2, "nba": 0, "nfl": 0}
    assert h["skipped"]["unmapped stat: cs2 game-2-kills"] == 1 and h["requests"] == st["requests"]


def test_poll_retry_after_backoff_and_freshness(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    feed = bp.providers()["dabble"]["feed"]
    fake = Fake()
    bp.poll_dabble(tmp_path, now=NOW, fetch=fake, sleep=lambda s: None)
    fake.err = bp.FetchError("HTTP 429", 429, retry_after=3600)
    t = NOW + feed["fixtures_refresh_s"]
    st = bp.poll_dabble(tmp_path, now=t, fetch=fake, sleep=lambda s: None)
    assert st["failures"] == 1 and st["backoff_until"] >= t + 3600 and st["offers"]           # last good lines kept ...
    assert {x["id"]: x for x in bp.health(t + 1)}["dabble"]["state"] == "backoff"
    n = len(fake.calls)
    bp.poll_dabble(tmp_path, now=t + feed["poll_s"], fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == n                                                               # Retry-After honoured
    assert bp.freshness(st, feed, NOW + feed["stale_after_s"] + 1) == "stale"                 # ... but hidden once stale
    monkeypatch.setattr(bp.time, "time", lambda: NOW + feed["stale_after_s"] + 1)
    assert bp.offers("dabble") == []
    monkeypatch.setattr(bp.time, "time", lambda: NOW + 1)
    assert len(bp.offers("dabble", "cs2")) == 3


def test_a_refusal_is_recorded_and_waits_long_never_worked_around(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    feed = bp.providers()["dabble"]["feed"]
    fake = Fake()
    fake.err = bp.FetchError("HTTP 403", 403)
    st = bp.poll_dabble(tmp_path, now=NOW, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == 1 and st["last_status"] == 403                                   # one request, no retry, no other host
    assert st["backoff_until"] >= NOW + feed["refused_backoff_s"]
    assert {x["id"]: x for x in bp.health(NOW + 1)}["dabble"]["state"] == "refused"


def test_one_fixture_failing_keeps_its_last_read_until_stale_all_failing_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    feed = bp.providers()["dabble"]["feed"]
    fake = Fake()
    bp.poll_dabble(tmp_path, now=NOW, fetch=fake, sleep=lambda s: None)
    fake.fail_details = {CS2_ID}
    t = NOW + feed["poll_s"]
    st = bp.poll_dabble(tmp_path, now=t, fetch=fake, sleep=lambda s: None)
    assert st["at"] == t and len(st["offers"]) == 5 and st["skipped"]["fixture read failed"] == 1
    fake.fail_details = {CS2_ID, lol_id()}
    st = bp.poll_dabble(tmp_path, now=t + feed["poll_s"], fetch=fake, sleep=lambda s: None)
    assert st["failures"] == 1 and st["at"] == t and st["last_error"] == "every fixture read failed"


def test_started_fixtures_are_dropped_and_the_row_cap_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    feed = dict(bp.providers()["dabble"]["feed"], max_offers=2)
    monkeypatch.setitem(bp.providers()["dabble"], "feed", feed)
    fake = Fake()
    st = bp.poll_dabble(tmp_path, now=NOW, fetch=fake, sleep=lambda s: None)
    assert len(st["offers"]) == 2 and st["skipped"]["over the row cap"] == 3
    lol_start = bp._iso_ts(load("lol_details.json")["data"]["advertisedStart"])
    st = bp.poll_dabble(tmp_path, now=lol_start + 1, fetch=fake, sleep=lambda s: None)
    assert lol_id() not in st["by_fixture"] and all(o["sport"] == "cs2" for o in st["offers"])


# ---------------------------------------------------------------------------------------------------------- HTTP + generic code

def offer(player="vargo", team="Halcyon", line=38.5, stat="kills", period="maps12", now=None):
    now = time.time() if now is None else now
    return {"provider": "dabble", "book": "dabble", "sport": "cs2", "raw_stat": "total-kills-map-one-and-map-two", "stat": stat, "period": period,
            "subject_id": "u-" + player, "player": player, "player_norm": bp.norm(player), "team": team, "team_norm": bp.norm(team),
            "game_id": "f1", "line": line, "over": {"offered": True, "payout": None, "price": 1.91},
            "under": {"offered": True, "payout": None, "price": 2.1}, "source_at": None, "observed_at": now}


def test_http_flag_off_is_the_unknown_route(env):
    assert get(env, "book-board?sport=cs2&book=dabble") == get(env, "no-such-route") == (404, {"error": "not found"})
    assert get(env, "book-board?sport=cs2&book=betr") == (404, {"error": "not found"})


def test_http_dabble_lines_match_the_board_exactly_and_never_change_it(env, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    before = records(env)
    now = time.time()
    bp.state("dabble").update(at=now, offers=[offer(now=now), offer("brandr", "Norrland", 33.5, now=now), offer("ghost", "", 20.5, now=now),
                                              offer("varg0", "Halcyon", 38.5, now=now)])
    st, me = get(env, "me")
    assert [x["id"] for x in me["features"]["extra_books"]] == ["dabble"] and "api.dabble.com" not in json.dumps(me)
    assert me["features"]["extra_books"][0]["short"] == "DB" and me["features"]["extra_books"][0]["rules"]["all_in"]["2"] == 3
    st, doc = get(env, "book-board?sport=cs2&book=dabble")
    assert st == 200 and doc["feed"]["state"] == "ok" and doc["label"] == "Dabble"
    by = {r["player"]["name"]: r for r in doc["records"]}
    assert set(by) == {"vargo", "brandr"} and doc["coverage"]["reasons"] == {"player not on the board": 2}    # never fuzzy
    v, b = by["vargo"], by["brandr"]
    ud = before[("vargo", "underdog")]
    assert v["scout"]["p_side"] == ud["scout"]["p_side"] and v["scout"]["units"] is None and v["offer"]["slip"] is False
    assert v["offer"]["over"]["price"] == 1.91 and v["extra"]["provider"] == "dabble"
    assert b["scout"]["p_side"] is None and b["scout"]["grade"] is None
    assert records(env) == before
    st, lines = get(env, "book-lines")
    assert st == 200 and lines["books"]["dabble"]["cs2"] == 2
    st, gen = get(env, "generate?platform=underdog&type=standard&slips=4&picks=2&sports=cs2")
    assert st == 200 and "dabble" not in json.dumps(gen).lower()                                         # never on a slip


def test_market_sheet_reads_dabble_as_a_pickem_price_never_a_member_row(monkeypatch):
    import workspace_markets as wm
    from tests.test_phase4_markets import snap, iso  # noqa: F401
    now = time.time()
    s = snap([("p1", 20.5, [("fanduel", 2.10, 1.80, 30), ("betrivers", 1.95, 1.90, 40), ("oddsapi:draftkings", 2.25, 1.70, 20)])], now)
    monkeypatch.setattr(wb, "snap", lambda srv, sport: s if sport == "nba" else None)
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: False)
    monkeypatch.setenv("SCOUT_BOOK_DABBLE", "1")
    o = dict(offer("Nba p1", "A", 20.5, "points", "game", now), sport="nba")
    o["over"]["price"] = 2.6
    bp.state("dabble").update(at=now, offers=[o])
    sh = wm.sheet(None, "nba", now)
    db = [x for x in sh["ev"] if x["book"] == "dabble"]
    assert db and db[0]["kind"] == "pickem" and db[0]["book_label"] == "Dabble" and "Pick'em multiplier: needs 2+ picks" in db[0]["flags"]
    assert all(l["book"] != "dabble" for a in sh["arbitrage"] for l in a["legs"])
    m = wm.for_member(json.dumps({"as_of": iso(now), "method": {}, "sports": {"nba": sh}}).encode(), {"nba": True})
    assert not any(r["book"] == "dabble" for r in m["sports"]["nba"]["ev"])


def test_admin_coverage_lists_dabble_and_the_blocked_apps():
    import admin_coverage
    rows = admin_coverage._extra(None, time.time())
    by = {r["source"]: r for r in rows}
    assert by["Dabble"]["state"] == "off" and by["Dabble"]["flag"] == "SCOUT_BOOK_DABBLE"
    for label in ("Betr Picks", "ParlayPlay", "Boom Fantasy"):
        assert by[label]["state"] == "blocked" and by[label]["detail"] and by[label]["evidence"] and by[label]["flag"] is None
    html = admin_coverage.page({"rows": rows, "summary": {}, "at": "x"})
    assert "Cloudflare" in html and "blocked" in html
