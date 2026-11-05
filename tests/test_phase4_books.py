"""Phase 4 item 1: extra books in the book toggle (book_providers + workspace_books), one flag per provider, default off.
Flag off = no /me key, the unknown-route answer, no thread, no request, no file. Flag on = exact-match display lines that never
change a grade, chance or stake, never go on a slip, and hide when the feed is stale. No network: every fetch is a fake."""
import json
import time

import pytest

import book_providers as bp
import workspace_books as wb
from tests.test_workspace_api import env, get, records, API  # noqa: F401

FLAGS = ("SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_ODDSAPI")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for f in FLAGS:
        monkeypatch.delenv(f, raising=False)
    bp.reset()
    wb.reset()
    monkeypatch.setattr(bp, "start", lambda data_dir: False)      # tests drive polls by hand; no thread
    yield
    bp.reset()


def line(sid="7", sport="cs", stat="kills_maps_1_2", value=38.5, over="1.80", under="1.76", team="Halcyon", **kw):
    r = {"status": "active", "game_status": "pre_game", "subject_type": "player", "outcome_type": "over_under", "line_type": "normal",
         "sport": sport, "wager_type": stat, "subject_id": sid, "game_id": "match-1", "updated_at": int((time.time() - 90) * 1000),
         "options": [{"status": "active", "outcome": "over", "outcome_value": value, "payout_multiplier": over, "subject_team": team, "line_id": "L1"},
                     {"status": "active", "outcome": "under", "outcome_value": value, "payout_multiplier": under, "subject_team": team, "line_id": "L2"}]}
    r.update(kw)
    return r


PLAYERS = {"cs": {"7": {"player_id": "7", "metadata": {"username": "vargo"}, "team": "Halcyon"},
                  "8": {"player_id": "8", "metadata": {"username": "nobody"}, "team": "X"}}}


class Fake:
    def __init__(self, body, status=200, err=None):
        self.body, self.status, self.err, self.calls = body, status, err, []

    def __call__(self, url, etag=None, timeout=None, tries=None):
        self.calls.append(url)
        if self.err:
            raise self.err
        if "/players/" in url:
            sport, sid = url.rsplit("/", 2)[-2:]
            doc = PLAYERS.get(sport, {}).get(sid)
            return 200, json.dumps(doc).encode() if doc else b"null", None
        return self.status, (json.dumps(self.body).encode() if self.status == 200 else None), 'W/"e1"'


# ---------------------------------------------------------------------------------------------------------- unit

def test_config_has_a_flag_and_sourced_rules_per_provider_and_unknowns_stay_unknown():
    for pid, p in bp.providers().items():
        assert p["flag"].startswith("SCOUT_BOOK_") and p["rules"]["sources"], pid
        assert all(s.get("url") for s in p["rules"]["sources"])
    sl = bp.providers()["sleeper"]["rules"]
    assert sl["flex"].startswith("unknown") and sl["push"].startswith("unknown")


def test_flags_off_nothing_runs(tmp_path):
    assert bp.enabled_ids() == [] and bp.features() is None
    assert bp.offers("sleeper") == []
    import importlib
    real = importlib.reload(bp)                    # the real start(): no provider on = no thread
    assert real.start(tmp_path) is False and real._THREAD["t"] is None
    assert not (tmp_path / "book_providers_players.json").exists()


def test_parse_maps_whole_stat_names_only_and_skips_alt_live_and_unknown():
    players = {"cs2": {"7": {"name": "vargo", "team": "Halcyon"}}}
    stats = bp.providers()["sleeper"]["stats"]
    body = [line(), line(stat="fantasy_points"), line(line_type="alt"), line(game_status="in_game"), line(sid="9"),
            line(sport="tennis")]
    offers, skipped, wanted = bp.parse_sleeper(body, 1000.0, players, stats)
    assert len(offers) == 1 and offers[0]["stat"] == "kills" and offers[0]["period"] == "maps12" and offers[0]["line"] == 38.5
    assert offers[0]["over"]["payout"] == 1.8 and offers[0]["under"]["payout"] == 1.76 and offers[0]["over"]["price"] is None
    assert wanted == {("cs2", "9")} and skipped["player name not read yet"] == 1
    assert any(k.startswith("unmapped stat") for k in skipped) and skipped["alternate or promo line"] == 1


def test_poll_rate_limit_etag_backoff_retry_after_and_freshness(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    fake = Fake([line(), line(sid="8")])
    st = bp.poll_sleeper(tmp_path, now=1000.0, fetch=fake, sleep=lambda s: None)
    assert st["at"] == 1000.0 and len(st["offers"]) == 2 and st["etag"] == 'W/"e1"'
    assert (tmp_path / "book_providers_players.json").exists()         # names cached for the next start
    n = len(fake.calls)
    bp.poll_sleeper(tmp_path, now=1010.0, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == n                                        # rate limit: not due before poll_s
    fake.status = 304
    st = bp.poll_sleeper(tmp_path, now=1200.0, fetch=fake, sleep=lambda s: None)
    assert st["not_modified"] == 1 and len(st["offers"]) == 2
    fake.err = bp.FetchError("HTTP 429", 429, retry_after=3600)
    st = bp.poll_sleeper(tmp_path, now=1400.0, fetch=fake, sleep=lambda s: None)
    assert st["failures"] == 1 and st["backoff_until"] >= 1400 + 3600 and st["offers"]      # last good lines kept ...
    assert bp.freshness(st, bp.providers()["sleeper"]["feed"], 1400.0) == "ok"
    assert bp.freshness(st, bp.providers()["sleeper"]["feed"], 1200.0 + 901) == "stale"      # ... but hidden once stale
    monkeypatch.setattr(bp.time, "time", lambda: 1200.0 + 901)
    assert bp.offers("sleeper") == []
    h = {x["id"]: x for x in bp.health(1500.0)}["sleeper"]
    assert h["state"] == "backoff" and h["failures"] == 1 and h["requests"] >= 3


def test_exponential_backoff_is_capped():
    feed = bp.providers()["sleeper"]["feed"]
    st = bp._blank("x")
    for _ in range(12):
        bp.on_failure(st, feed, 0.0, bp.FetchError("HTTP 503", 503), rand=lambda: 0.5)
    assert st["backoff_until"] == pytest.approx(feed["backoff_max_s"])


def test_http_get_retries_5xx_never_4xx():
    import urllib.error

    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 503 if len(calls) < 2 else 403, "x", {}, None)
    with pytest.raises(bp.FetchError) as e:
        bp.http_get("https://example.invalid/x", tries=3, opener=opener, sleep=lambda s: None)
    assert e.value.status == 403 and len(calls) == 2


def rec(name="vargo", team="Halcyon", line_=38.5, platform="underdog", side="over", p=0.61, grade="B+", eid="e1"):
    return {"sport": "cs2", "key": f"cs2.{eid}.{name}.kills.maps12.{line_}", "event": {"id": eid, "label": "A vs B", "start_utc": "2099-01-01T00:00:00+00:00"},
            "player": {"key": name, "name": name, "team": team}, "market": {"stat": "kills", "stat_label": "Kills", "period": "maps12", "period_label": "Maps 1-2", "line_type": "standard"},
            "offer": {"platform": platform, "platform_label": platform.title(), "offer_id": "o", "line": line_},
            "scout": {"side": side, "p_side": p, "p_over": p, "p_under": None, "p_push": 0.0, "projection": 40.0, "grade": grade, "units": 1.0,
                      "stake_reason": "x", "hold": None, "model_version": "M", "read_rev": "r"},
            "history": {"l10": {"over": 6, "under": 4, "n": 10}}, "state": {"kind": "ok"}}


def test_match_is_exact_and_ambiguous_names_need_the_team():
    subj = wb.subjects([rec(), rec(team="Other", eid="e2")])
    o = {"player_norm": "vargo", "stat": "kills", "period": "maps12", "team_norm": "halcyon"}
    got, why = wb.match(subj, o)
    assert got and got[0]["player"]["team"] == "Halcyon"
    assert wb.match(subj, dict(o, team_norm="nobody")) == (None, "two board players share this name")
    assert wb.match(subj, dict(o, player_norm="varg0"))[1] == "player not on the board"          # never fuzzy


def test_derive_same_line_keeps_the_saved_read_unchanged_and_drops_the_stake():
    base = rec()
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [base], 38.5, {"offered": True, "payout": 1.8}, {"offered": True, "payout": 1.76}, None, 1.0, "k")
    assert r["scout"]["p_side"] == base["scout"]["p_side"] and r["scout"]["grade"] == "B+" and r["scout"]["side"] == "over"
    assert r["scout"]["units"] is None and "stakes are set for that entry only" in r["scout"]["stake_reason"]
    assert r["offer"]["platform"] == "sleeper" and r["offer"]["slip"] is False and r["extra"]["same_line_as"] == "Underdog"
    assert base["scout"]["units"] == 1.0                                         # the board's own record is untouched


def test_derive_other_line_has_no_side_no_chance_no_letter():
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [rec()], 37.5, {"offered": True, "payout": 1.8}, {"offered": True, "payout": 1.76}, None, 1.0, "k")
    s = r["scout"]
    assert s["side"] is None and s["p_side"] is None and s["p_over"] is None and s["grade"] is None and s["units"] is None
    assert s["projection"] == 40.0 and r["history"]["l10"] is None and r["extra"]["same_line_as"] is None


# ---------------------------------------------------------------------------------------------------------- HTTP

def test_http_flag_off_is_the_unknown_route_and_no_me_key(env):
    assert get(env, "book-board?sport=cs2&book=sleeper") == get(env, "no-such-route") == (404, {"error": "not found"})
    st, me = get(env, "me")
    assert st == 200 and "extra_books" not in me["features"]


def test_http_flag_on_lines_match_the_board_and_never_change_it(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    before = records(env)
    st = bp.state("sleeper")
    now = time.time()
    st.update(at=now, offers=[
        {"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills", "period": "maps12", "subject_id": "7",
         "player": "vargo", "player_norm": "vargo", "team": "Halcyon", "team_norm": "halcyon", "game_id": "g", "line": 38.5,
         "over": {"offered": True, "payout": 1.8, "price": None}, "under": {"offered": True, "payout": 1.76, "price": None}, "source_at": now - 60, "observed_at": now},
        {"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills", "period": "maps12", "subject_id": "9",
         "player": "brandr", "player_norm": "brandr", "team": "Norrland", "team_norm": "norrland", "game_id": "g", "line": 33.5,
         "over": {"offered": True, "payout": 1.9, "price": None}, "under": {"offered": True, "payout": 1.7, "price": None}, "source_at": now - 60, "observed_at": now},
        {"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills", "period": "maps12", "subject_id": "10",
         "player": "ghost", "player_norm": "ghost", "team": "", "team_norm": "", "game_id": "g", "line": 20.5,
         "over": {"offered": True, "payout": 1.9, "price": None}, "under": {"offered": True, "payout": 1.7, "price": None}, "source_at": now - 60, "observed_at": now}])
    st_, me = get(env, "me")
    assert [x["id"] for x in me["features"]["extra_books"]] == ["sleeper"] and "api.sleeper.app/lines" not in json.dumps(me)
    st_, doc = get(env, "book-board?sport=cs2&book=sleeper")
    assert st_ == 200 and doc["feed"]["state"] == "ok"
    by = {r["player"]["name"]: r for r in doc["records"]}
    assert set(by) == {"vargo", "brandr"} and doc["coverage"]["reasons"] == {"player not on the board": 1}
    v, b = by["vargo"], by["brandr"]
    ud = before[("vargo", "underdog")]
    assert v["scout"]["p_side"] == ud["scout"]["p_side"] and v["scout"]["grade"] == ud["scout"]["grade"] and v["scout"]["units"] is None
    assert b["scout"]["p_side"] is None and b["scout"]["grade"] is None and b["offer"]["line"] == 33.5
    assert doc["coverage"]["covered_subjects"] == 2 and doc["coverage"]["median_staleness_s"] is not None
    assert records(env) == before                                         # the board is unchanged
    assert get(env, "book-board?sport=nba&book=sleeper")[0] == 404        # a sport the member does not see
    st["at"] = now - 3600                                                 # stale feed: lines hidden, state said
    import server
    server._RESULTS.clear()
    st_, doc = get(env, "book-board?sport=cs2&book=sleeper")
    assert doc["records"] == [] and doc["feed"]["state"] == "stale"


# ---------------------------------------------------------------------------------------------------- lc-1 letter containment

def test_extra_line_never_shows_a_held_letter(monkeypatch):
    """A same-line extra-book row copies the board's read; while CS2 letters are held it carries no letter, ever."""
    import letter_containment as lc
    monkeypatch.setattr(lc, "contained", lambda sport, stat=None: sport == "cs2")
    args = ({"offered": True, "payout": 1.8}, {"offered": True, "payout": 1.76}, None, 1.0, "k")
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [rec()], 38.5, *args)
    assert r["scout"]["grade"] is None and r["scout"]["letter_held"] is True and r["scout"]["units"] is None
    assert r["scout"]["p_side"] == 0.61 and r["scout"]["side"] == "over"                 # the chance and the Lean side stay
    held = rec()
    held["scout"].update(grade=None, units=0.0, letter_held=True)                         # what record_of sends while held
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [held], 38.5, *args)
    assert r["scout"]["grade"] is None and r["scout"]["letter_held"] is True
    monkeypatch.setattr(lc, "contained", lambda sport, stat=None: False)
    assert wb.derive("sleeper", "Sleeper", "pickem", "cs2", [rec()], 38.5, *args)["scout"]["grade"] == "B+"


def test_held_letter_is_never_a_calibrated_chance_for_ev(monkeypatch):
    import letter_containment as lc
    import workspace_markets as wm
    monkeypatch.setattr(lc, "contained", lambda sport, stat=None: True)
    assert wm.model_released("cs2", "kills") is False and wm.model_released("lol", "kills") is False


def test_extra_lines_never_enter_the_slip_generator_pool(env, monkeypatch):
    """Both generators read the pick'em boards only: the server's from the shared snapshots, the hub's from H.records."""
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    now = time.time()
    bp.state("sleeper").update(at=now, offers=[{"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills",
                                                "period": "maps12", "subject_id": "7", "player": "vargo", "player_norm": "vargo", "team": "Halcyon",
                                                "team_norm": "halcyon", "game_id": "g", "line": 38.5, "source_at": now, "observed_at": now,
                                                "over": {"offered": True, "payout": 1.8, "price": None}, "under": {"offered": True, "payout": 1.76, "price": None}}])
    assert get(env, "book-board?sport=cs2&book=sleeper")[1]["records"]
    st, doc = get(env, "generate?platform=underdog&type=standard&slips=4&picks=2&sports=cs2")
    assert st == 200 and "sleeper" not in json.dumps(doc).lower()
    from pathlib import Path
    js = (Path(wb.__file__).parent / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    assert "const pool=H.records.filter(" in js and "H.extraRecs.filter(" not in js.split("const pool=")[1][:200]


def test_book_quote_staleness_follows_the_sports_fetch_ladder():
    """A FanDuel / BetRivers quote is stale on the sports service's own rule (2 x the fetch interval, >= 20 min), so a
    game 14 h out (fetched hourly) keeps its quote for 2 h instead of disappearing between fetches."""
    import book_providers as bp
    import workspace_books as wb
    from sports.books import schedule
    for pid in ("fanduel", "betrivers"):
        feed = bp.providers()[pid]["feed"]
        assert [tuple(x) for x in feed["ladder"]] == [tuple(x) for x in schedule.LADDERS["full"]]
        assert feed["stale_min_s"] == schedule.STALE_MIN_S
    feed = bp.providers()["fanduel"]["feed"]
    now = 1_800_000_000.0

    def rec(hours):
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(now + hours * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [{"event": {"start_utc": iso}}]
    assert wb.quote_stale_after(feed, rec(14), now) == 7200          # hourly rung
    assert wb.quote_stale_after(feed, rec(1), now) == 1200           # 10-min rung -> the 20-min floor
    assert wb.quote_stale_after(feed, rec(48), now) == 6 * 3600      # 3-hourly rung
    assert wb.quote_stale_after(feed, [], now) == feed["stale_after_s"]
    assert wb.quote_stale_after(feed, rec(-1), now) == feed["stale_after_s"]   # started: the fixed rule
