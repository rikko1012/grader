"""matchid: canonical team / match identity. Fixtures only - no database, no network."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import hltv
import hltv_store
import matchid

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
T1 = NOW + timedelta(hours=20)          # an upcoming match slot

TEAMS = [(9565, "Vitality"), (13300, "Vitality Academy"), (4494, "MOUZ"), (11176, "MOUZ NXT"), (9215, "MIBR"),
         (13001, "MIBR fe"), (4608, "Natus Vincere"), (12001, "NAVI Junior"), (7020, "Spirit"), (12500, "Spirit Academy"),
         (4411, "Ninjas in Pyjamas"), (6667, "FaZe"), (11283, "Falcons"), (9455, "Imperial"), (13926, "DENDELE"),
         (8297, "FURIA"), (5995, "G2"), (12916, "Betclic"), (13855, "Overtake Sector")]


def sched(mid, start, a, b, fmt="bo3", event="Test Cup"):
    return {"matchid": mid, "start": start, "team1": a, "team2": b, "event": event, "format": fmt}


@pytest.fixture
def mi(monkeypatch, tmp_path):
    """matchid wired to fixture HLTV data (teams / schedule / finished), a private links db and alias file."""
    aliases = tmp_path / "team_aliases.json"
    aliases.write_text(json.dumps({"aliases": [
        {"name": "NIP", "team_id": 4411, "proof": "fixture"},
        {"name": "Overtake", "hltv_name": "Overtake Sector", "proof": "fixture"},
        {"name": "DENDELE CS", "team_id": 13926, "proof": "fixture"}]}), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", aliases)
    monkeypatch.setattr(matchid, "DB_PATH", tmp_path / "match_links.db")
    monkeypatch.setattr(matchid, "_local", __import__("threading").local())
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    data = {"teams": list(TEAMS), "schedule": [], "finished": []}

    def source(name):
        def load():
            v = data[name]
            if isinstance(v, Exception):
                raise v
            return list(v)
        return load
    for n in ("teams", "schedule", "finished"):
        monkeypatch.setattr(matchid, f"_db_{n}", source(n))
    matchid._reset()
    yield data
    matchid._reset()


def load(data, **kw):
    data.update(kw)
    matchid._reset()
    matchid.refresh(force=True)


# ---------------------------------------------------------------- teams
def test_team_key_exact_affixes_and_aliases(mi):
    load(mi)
    v = matchid.team_key("Vitality")
    assert v == "team:9565"
    for spelling in ("Team Vitality", "Vitality Esports", "VITALITY", "Vitality Gaming", "vitality."):
        assert matchid.team_key(spelling) == v, spelling
    assert matchid.team_key("Team Falcons") == "team:11283"
    assert matchid.team_key("NIP") == matchid.team_key("Ninjas in Pyjamas") == "team:4411"     # alias by team id
    assert matchid.team_key("Overtake") == "team:13855"                                          # alias by HLTV name
    assert matchid.team_key("DENDELE CS") == matchid.team_key("DENDELE")


def test_academy_junior_female_nxt_stay_distinct(mi):
    load(mi)
    pairs = [("Vitality Academy", "Vitality"), ("MOUZ NXT", "MOUZ"), ("MIBR fe", "MIBR"), ("NAVI Junior", "Natus Vincere"),
             ("Spirit Academy", "Spirit")]
    for squad, main in pairs:
        ks, km = matchid.team_key(squad), matchid.team_key(main)
        assert ks and km and ks != km, (squad, main)
    # squads HLTV doesn't list are unresolved - never folded onto the main team, never substring-matched
    for name in ("FaZe Academy", "G2 Ares", "FURIA fe", "Spirit 2", "Vita", "Imperial Esports Female", "Natus"):
        assert matchid.team_key(name) is None, name
    assert matchid.team_key("Imperial Esports") == "team:9455"      # org affix only


def test_affix_collision_is_unresolved_not_guessed(mi):
    load(mi, teams=TEAMS + [(1, "Nova Esports"), (2, "Nova Gaming")])
    assert matchid.team_key("Nova Esports") == "team:1"           # exact names still resolve
    assert matchid.team_key("Nova Gaming") == "team:2"
    assert matchid.team_key("Nova") is None                       # two HLTV teams after stripping: not guessed
    assert "2 HLTV teams" in matchid.team_key_why("Nova")[1]


def test_schedule_only_team_gets_a_name_key(mi):
    load(mi, schedule=[sched(2400001, T1, "Envy", "Heroic")])
    assert matchid.team_key("Envy") == "name:envy"
    assert matchid.team_key("Heroic") == "name:heroic"
    r = matchid.canonical_match("Heroic", "Team Envy", T1.isoformat())
    assert r["status"] == "linked" and r["match_id"] == "hltv:2400001"


# ---------------------------------------------------------------- matches
def test_reversed_order_same_id(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    a = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())
    b = matchid.canonical_match("MOUZ", "Team Vitality", (T1 + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S+00"))
    assert a["match_id"] == b["match_id"] == "hltv:2399001"
    assert a["team_keys"] == b["team_keys"] == sorted(["team:9565", "team:4494"])
    assert a["flipped"] != b["flipped"]
    assert a["status"] == b["status"] == "linked"


def test_hashed_id_when_hltv_has_no_match(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    a = matchid.canonical_match("FaZe", "G2", T1.isoformat(), bo=3)
    b = matchid.canonical_match("G2 Esports", "FaZe", (T1 + timedelta(minutes=20)).isoformat())   # other platform: no BO, +20 min
    assert a["status"] == "linked" and a["source"] == "hash" and a["match_id"].startswith("cs2:")
    assert a["match_id"] == b["match_id"]                         # stable across platforms, order and a missing BO
    assert a["hltv_match"] is None


def test_rematch_gets_a_different_id(mi):
    t2 = T1 + timedelta(hours=26)
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ"), sched(2399050, t2, "MOUZ", "Vitality")])
    a = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())
    b = matchid.canonical_match("Vitality", "MOUZ", t2.isoformat())
    assert (a["match_id"], b["match_id"]) == ("hltv:2399001", "hltv:2399050")
    # without HLTV: a different hour is a different match, the same hour the same one
    h1 = matchid.canonical_match("FaZe", "G2", T1.isoformat())["match_id"]
    h2 = matchid.canonical_match("FaZe", "G2", (T1 + timedelta(hours=5)).isoformat())["match_id"]
    assert h1 != h2
    # a match just outside the window is not linked to HLTV's
    far = matchid.canonical_match("Vitality", "MOUZ", (T1 + timedelta(hours=3, minutes=5)).isoformat())
    assert far["source"] == "hash"


def test_two_candidates_are_ambiguous(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ"), sched(2399002, T1 + timedelta(hours=2), "MOUZ", "Vitality")])
    r = matchid.canonical_match("Vitality", "MOUZ", (T1 + timedelta(hours=1)).isoformat())
    assert r["status"] == "ambiguous" and r["match_id"] is None
    assert "2399001" in r["reason"] and "2399002" in r["reason"]


def test_bo_breaks_a_tie_only_when_unique(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ", "bo1"), sched(2399002, T1 + timedelta(hours=2), "MOUZ", "Vitality", "bo3")])
    r = matchid.canonical_match("Vitality", "MOUZ", (T1 + timedelta(hours=1)).isoformat(), bo="BO3")
    assert r["match_id"] == "hltv:2399002"


def test_unresolved_team_is_unlinked(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    r = matchid.canonical_match("Vitality", "Some Mix Team", T1.isoformat())
    assert r["status"] == "unlinked" and r["match_id"] is None and "Some Mix Team" in r["reason"]
    assert matchid.canonical_match("Vitality", "Vitality Esports", T1.isoformat())["status"] == "unlinked"   # same team twice
    assert matchid.canonical_match("Vitality", "MOUZ", None)["status"] == "unlinked"
    assert matchid.canonical_match("Vitality", "MOUZ", "not a date")["match_id"] is None


def test_series_and_map_scope_share_the_match_not_the_market(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    s = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat(), scope="series")
    m = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat(), scope="map1")
    assert s["match_id"] == m["match_id"]
    ks = matchid.market_key(s["match_id"], "series", "polymarket", "1544453")
    km = matchid.market_key(m["match_id"], "map1", "polymarket", "1544454")
    assert ks != km and "|series|" in ks and "|map1|" in km
    assert matchid.market_key(s["match_id"], "Map 1", "Kalshi", "X") == matchid.market_key(s["match_id"], "map1", "kalshi", "X")
    assert matchid.market_key(s["match_id"], "moneyline", "kalshi", "X") == matchid.market_key(s["match_id"], "series", "kalshi", "X")
    assert matchid.market_key(None, "series", "kalshi", "X") is None
    with pytest.raises(ValueError):
        matchid.market_key(s["match_id"], "series", "kalshi", "")


def test_finished_match_links_by_utc_date_and_never_to_a_future_rematch(mi):
    yday = NOW - timedelta(days=1)
    load(mi, finished=[{"matchid": 2399406, "date": yday.date(), "team_ids": [9455, 13926]},
                       {"matchid": 2399398, "date": (yday - timedelta(days=1)).date(), "team_ids": [9455, 13926]}])
    r = matchid.canonical_match("DENDELE CS", "Imperial", yday.replace(hour=12).isoformat())
    assert r["match_id"] == "hltv:2399406" and r["status"] == "linked"
    r2 = matchid.canonical_match("Imperial", "DENDELE", (yday - timedelta(days=1)).replace(hour=12).isoformat())
    assert r2["match_id"] == "hltv:2399398"                       # the day-before meeting is its own match
    fut = matchid.canonical_match("Imperial", "DENDELE", (NOW + timedelta(days=1)).isoformat())
    assert fut["source"] == "hash"                                # a future rematch never links to a finished match


def test_started_match_keeps_its_hltv_id_after_leaving_the_schedule(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    load(mi, schedule=[])                                         # HLTV dropped it (live / over); restart-like reset
    r = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())
    assert r["match_id"] == "hltv:2399001"                        # from match_links.db


# ---------------------------------------------------------------- outages
def test_hltv_outage_never_crashes(mi):
    down = hltv.Unavailable("breaker on")
    load(mi, teams=down, schedule=down, finished=down)
    assert matchid.team_key("Vitality") is None
    r = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())
    assert r["status"] == "unlinked" and r["match_id"] is None
    assert matchid.match_id_for_row({"team_name": "Vitality", "opp_name": "MOUZ", "start": T1.isoformat()}) is None
    st = matchid.status()
    assert st["teams"]["ok"] is False and st["teams"]["error"]


def test_outage_after_a_good_read_uses_the_stored_copy(mi, monkeypatch):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    good = matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())["match_id"]
    monkeypatch.setattr(matchid, "DB_PATH", matchid.DB_PATH.parent / "other.db")   # not even the persisted schedule
    down = hltv.Unavailable("timed out")
    mi.update(teams=down, schedule=down, finished=down)
    matchid._reset()                                              # restart during the outage
    assert matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())["match_id"] == good == "hltv:2399001"


def test_one_source_failing_never_blocks_the_others(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")], finished=RuntimeError("boom"))
    assert matchid.canonical_match("Vitality", "MOUZ", T1.isoformat())["match_id"] == "hltv:2399001"
    assert matchid.status()["finished"]["ok"] is False


def test_no_schedule_ever_withholds_the_hashed_id(mi):
    load(mi, schedule=hltv.Unavailable("down"))
    r = matchid.canonical_match("FaZe", "G2", T1.isoformat())
    assert r["match_id"] is None and "schedule unavailable" in r["reason"]   # a hash now could fork from the HLTV id later


# ---------------------------------------------------------------- board rows + links
def test_match_id_for_row_props_and_platform_spellings_agree(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    ud = {"team": "Team Vitality", "opponent": "MOUZ Esports", "start": T1.strftime("%Y-%m-%dT%H:%M:%SZ"),
          "team_name": "Vitality", "opp_name": "MOUZ", "bo": 3, "event": "Test Cup"}
    pp = {"team": "MOUZ", "opponent": "Vitality", "start": (T1 + timedelta(minutes=5)).isoformat()}
    assert matchid.match_id_for_row(ud) == matchid.match_id_for_row(pp) == "hltv:2399001"
    assert matchid.match_id_for_row({"team_name": "Vitality"}) is None


def test_link_is_idempotent_and_follows_a_hash_to_its_hltv_id(mi):
    load(mi)
    mid = matchid.canonical_match("FaZe", "G2", T1.isoformat())["match_id"]
    otm = {"0": "team:6667", "1": "team:5995"}
    assert matchid.link("polymarket", "257425", "1544453", mid, otm)["changed"] is True
    assert matchid.link("Polymarket", "257425", "1544453", mid, dict(reversed(list(otm.items()))))["changed"] is False
    assert matchid.get_link("polymarket", "1544453")["outcome_team_map"] == otm
    matchid.link("kalshi", "KXCS2GAME-X", "KXCS2GAME-X-FAZE", mid, {"yes": "team:6667"})
    assert {l["platform"] for l in matchid.links_for(mid)} == {"polymarket", "kalshi"}
    # HLTV lists the match later: re-linking records the alias, old keys still find it
    res = matchid.link("polymarket", "257425", "1544453", "hltv:2400100", otm)
    assert res == {"changed": True, "previous": mid}
    assert matchid.current_id(mid) == "hltv:2400100"
    assert {l["market_id"] for l in matchid.links_for("hltv:2400100")} == {"1544453", "KXCS2GAME-X-FAZE"}
    with pytest.raises(ValueError):
        matchid.link("kalshi", "E", "M", None, {})


def test_link_writes_from_many_threads(mi):
    import threading
    load(mi)
    errs = []

    def w(i):
        try:
            matchid.link("kalshi", "E", f"M{i % 10}", "cs2:abc", {"yes": f"team:{i % 10}"})
        except Exception as e:      # pragma: no cover - the assertion reports it
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(60)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(matchid.links_for("cs2:abc")) == 10


def test_name_normalisation():
    assert matchid.norm("QUINTESSÊNCIA") == "quintessencia"
    assert matchid.name_variants("Team Spirit Esports") == ["teamspiritesports", "spirit"]
    assert matchid.name_variants("TeamOne") == ["teamone"]       # 'Team' only as a separate word
    assert matchid.name_variants("MOUZ NXT") == ["mouznxt"]


# ---------------------------------------------------------------- hashed ids never fork across platforms
def test_hashed_id_does_not_fork_across_the_half_hour_or_a_restart(mi):
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    pm = matchid.canonical_match("FaZe", "G2", (T1 + timedelta(minutes=35)).isoformat())      # rounds to T1 + 1 h
    ks = matchid.canonical_match("G2", "FaZe", (T1 + timedelta(minutes=25)).isoformat())      # rounds to T1
    assert matchid.hash_id(pm["team_keys"], T1 + timedelta(minutes=35)) != matchid.hash_id(pm["team_keys"], T1 + timedelta(minutes=25))
    assert pm["source"] == ks["source"] == "hash" and pm["match_id"] == ks["match_id"]
    moved = matchid.canonical_match("FaZe", "G2", (T1 + timedelta(hours=2, minutes=50)).isoformat())   # rescheduled
    assert moved["match_id"] == pm["match_id"]
    matchid._reset()                                              # restart: the anchor is read back from match_links.db
    matchid.refresh(force=True)
    again = matchid.canonical_match("G2", "FaZe", (T1 + timedelta(minutes=40)).isoformat())
    assert again["match_id"] == pm["match_id"]
    far = matchid.canonical_match("FaZe", "G2", (T1 + timedelta(hours=8)).isoformat())       # a later rematch
    assert far["match_id"] != pm["match_id"]


def _ml_listing(platform, teams, start, p):
    """One normalized moneyline listing (moneyline.py shape), a deep two-sided book around p for teams[0]."""
    probs = (p, 1 - p)
    qs = []
    for i, t in enumerate(teams):
        pi = probs[i]
        qs.append({"platform": platform, "market_id": f"{platform}-m{i if platform == 'kalshi' else 0}",
                   "outcome_id": "yes" if platform == "kalshi" else str(i), "team_name": t, "bid": round(pi - 0.01, 4),
                   "ask": round(pi + 0.01, 4), "bid_size": 500.0, "ask_size": 500.0,
                   "depth": {"bid_5c": 900.0, "ask_5c": 900.0}, "market_open": True, "status": "open", "start": start,
                   "received_ts": start, "source_ts": None, "event_id": f"{platform}-ev", "bo": None, "event_title": "Cup"})
    return {"platform": platform, "event_id": f"{platform}-ev", "teams": list(teams), "start": start, "bo": None,
            "event_title": "Cup", "url": None, "rules": "", "live": False, "quotes": qs, "problem": None}


def test_moneyline_board_one_entry_for_07_35_and_07_25(mi, monkeypatch):
    """Polymarket 07:35Z, Kalshi 07:25Z, same two HLTV-known teams, no HLTV match: one board entry with both blocks."""
    import moneyline
    monkeypatch.setattr(moneyline, "_GROUP", {})
    load(mi, schedule=[sched(2399001, T1, "Vitality", "MOUZ")])
    base = T1.replace(hour=7) + timedelta(days=1)
    pl = _ml_listing("polymarket", ["FaZe", "G2"], (base + timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ"), 0.6)
    kl = _ml_listing("kalshi", ["G2", "FaZe"], (base + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ"), 0.4)

    def sources():
        return {p: moneyline.Source(p, lambda: None, lambda raw, L=L: [dict(L)], canonical_fn=matchid.canonical_match,
                                    snapshot=False) for p, L in (("polymarket", pl), ("kalshi", kl))}
    now = base - timedelta(hours=2)
    for anchored in (True, False):           # False: the board's own 3 h merge of hashed ids is the safety net
        if not anchored:
            matchid._reset()
            matchid.refresh(force=True)
            monkeypatch.setattr(matchid, "anchored_hash_id", matchid.hash_id)
        srcs = sources()
        for s in srcs.values():
            s.refresh_once()
        ids = {s.name: s.listings[0]["canon"]["match_id"] for s in srcs.values()}
        assert (ids["polymarket"] == ids["kalshi"]) is anchored
        b = moneyline.board(srcs, now=now)
        assert len(b) == 1, b
        m = b[0]
        assert m["match_id"].startswith("cs2:") and m["platforms"]["polymarket"] and m["platforms"]["kalshi"]
        assert m["platforms"]["polymarket"]["prob_a"] == pytest.approx(m["platforms"]["kalshi"]["prob_a"])
        assert m["baseline"]["prob_a"] is not None and m["disagreement"] is False
