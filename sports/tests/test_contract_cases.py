"""Owner brief section 12 contract tests: mismatched identities / scopes / variants, stale and missing prices,
DST and reschedules, duplicate updates, corrections, trades, injuries, missing participation."""
from dataclasses import replace

import pytest

from sports import adapters, capabilities, config, identity, settlement, sharp2, store
from sports.contracts import SideQuote
from sports.tests import helpers as h
from sports.tests.conftest import fixture

NBA = config.league("nba")


@pytest.fixture
def db():
    con = store.connect()
    yield con
    con.close()


# ------------------------------------------------------------------------------------------- identities
def test_namesake_on_another_team_is_not_joined():
    r = h.resolver(roster=[(h.NY, "nba:player:espn:1", "Jalen Williams"), (h.CHI, "nba:player:espn:2", "Jalen Williams")])
    pid, why = r.player("Jalen Williams", "NYK", "nba:espn:1")
    assert pid == "nba:player:espn:1"
    pid, why = r.player("Jalen Williams", "CHI", "nba:espn:1")       # CHI is not in NYK @ BOS
    assert pid is None and "not in this event" in why


def test_two_roster_players_with_one_name_are_ambiguous_without_team():
    r = h.resolver(roster=[(h.NY, "nba:player:espn:1", "Kevin Porter"), (h.BOS, "nba:player:espn:2", "Kevin Porter Jr.")])
    pid, why = r.player("Kevin Porter", None, "nba:espn:1")
    assert pid is None and "two roster players" in why


def test_unmapped_team_abbreviation_is_never_guessed():
    r = h.resolver()
    eid, why = r.event("2026-10-23", "NYKS", "BOS")
    assert eid is None and "not mapped" in why
    eid, why = r.event("2026-10-23", "NYK", "BOS")                   # exact alias NYK -> NY
    assert eid == "nba:espn:1"


def test_different_events_are_not_compatible_markets():
    a = h.offer()
    assert capabilities.compatible(a, replace(a, offer_id="pp:2"))[0]
    assert not capabilities.compatible(a, replace(a, event_id="nba:espn:9"))[0]
    assert not capabilities.compatible(a, replace(a, player_id=None))[0]
    assert not capabilities.compatible(a, replace(a, stat="rebounds"))[0]


# ------------------------------------------------------------------------------------- scopes / variants
def test_half_and_quarter_lines_never_match_full_game():
    a = h.offer()
    for sc in ("first_half", "q1", "second_half"):
        ok, why = capabilities.compatible(a, replace(a, scope=sc))
        assert not ok and "scope" in why
        ok, why = capabilities.gate(replace(a, scope=sc))
        assert not ok and any("full-game" in w for w in why)


def test_underdog_period_prefixes_are_scoped_not_full_game():
    assert adapters._ud_scope("period_1_2_receiving_yds") == ("first_half", "receiving_yds")
    assert adapters._ud_scope("period_1_points") == ("q1", "points")
    assert adapters._ud_scope("period_7_points")[0] == "unknown"
    assert adapters._ud_scope("receiving_yds") == ("full_game", "receiving_yds")


def test_prizepicks_quarter_label_and_duration_scope():
    body = {"data": [{"type": "projection", "id": "1", "attributes": {
        "line_score": 5.5, "stat_type": "1Q Points", "start_time": "2026-10-23T19:00:00-04:00",
        "status": "pre_game", "odds_type": "standard", "updated_at": "2026-10-23T15:00:00-04:00"},
        "relationships": {"new_player": {"data": {"type": "new_player", "id": "p"}},
                          "projection_type": {"data": {"type": "projection_type", "id": "2"}}}}],
        "included": [{"type": "new_player", "id": "p", "attributes": {"display_name": "Jayson Tatum", "team": "BOS"}},
                     {"type": "projection_type", "id": "2", "attributes": {"name": "Single Stat"}}]}
    b = adapters.pp_offers(body, NBA, h.T0, None)
    assert len(b.records) == 1 and b.records[0].scope == "q1" and b.records[0].stat == "points"
    assert not capabilities.gate(b.records[0])[0]


def test_promotional_and_alternate_lines_are_never_ordinary():
    a = h.offer()
    assert not capabilities.compatible(a, replace(a, promotional=True))[0]
    assert not capabilities.compatible(a, replace(a, variant="goblin"))[0]
    assert not capabilities.gate(replace(a, variant="demon"))[0]
    assert not capabilities.gate(replace(a, promotional=True))[0]


def test_unsupported_markets_refuse_to_grade_with_a_handler():
    for lab, fam in (("Anytime TDs", "touchdown"), ("Double-Double", "double_double"), ("rush_rec_tds", "touchdown")):
        cap = capabilities.classify("nfl", None, lab)
        assert cap.status == "unsupported" and cap.handler.name == fam and not cap.handler.can_grade()[0]
    assert capabilities.classify("nba", "pra").status == "research_only"         # combos: never summed
    assert capabilities.classify("nba", "points").gradable
    assert capabilities.classify("nfl", "rec").gradable and capabilities.classify("nfl", "pass_td").status == \
        "research_only"


def test_side_availability_is_per_offer():
    # an Underdog standard line whose Under is suspended right now keeps its stored type (cap-2): still gradable
    more_only = h.offer(platform="underdog", oid="ud:1", sides=(SideQuote("over", True), SideQuote("under", False)))
    assert more_only.offered_sides() == ("over",)
    assert capabilities.gate(more_only)[0]
    # os-2 (audit_1121): a PrizePicks line LISTING one side is a one-sided line - never graded
    pp_more_only = h.offer(sides=(SideQuote("over", True), SideQuote("under", False)))
    assert not capabilities.gate(pp_more_only)[0]
    none = h.offer(sides=(SideQuote("over", False), SideQuote("under", False)))
    assert not capabilities.gate(none)[0]


# --------------------------------------------------------------------------------- stale / missing prices
def _rungs(*qs):
    return [(f"rid{i}", q, None) for i, q in enumerate(qs)]


def test_sharp2_refuses_stale_in_play_wide_and_untraded_quotes():
    ev = h.event()
    o = h.offer(line=19.5)
    fresh = h.kalshi_rung(19.5, 0.40, 0.42, observed_at="2026-10-23T22:50:00Z")
    fc, why = sharp2.forecast(o, "o1", _rungs(fresh), ev, "2026-10-23T22:55:00Z")
    assert fc is not None and abs(fc.p_over - 0.41) < 1e-9 and fc.p_push == 0.0
    stale = h.kalshi_rung(19.5, 0.40, 0.42, observed_at="2026-10-23T22:00:00Z")
    assert sharp2.forecast(o, "o1", _rungs(stale), ev, "2026-10-23T22:55:00Z")[0] is None
    in_play = h.kalshi_rung(19.5, 0.40, 0.42, observed_at="2026-10-23T23:05:00Z")
    fc, why = sharp2.forecast(o, "o1", _rungs(in_play), ev, "2026-10-23T23:06:00Z")
    assert fc is None and any("in play" in w for w in why)
    wide = h.kalshi_rung(19.5, 0.30, 0.42, observed_at="2026-10-23T22:50:00Z")
    assert sharp2.forecast(o, "o1", _rungs(wide), ev, "2026-10-23T22:55:00Z")[0] is None
    dead = h.kalshi_rung(19.5, 0.40, 0.42, volume=0.0, oi=0.0, observed_at="2026-10-23T22:50:00Z")
    fc, why = sharp2.forecast(o, "o1", _rungs(dead), ev, "2026-10-23T22:55:00Z")
    assert fc is None and any("no trades" in w for w in why)
    one_sided = h.kalshi_rung(19.5, 0.40, 0.42, observed_at="2026-10-23T22:50:00Z")
    one_sided = replace(one_sided, sides=(SideQuote("over", True, "exchange_yes_no", bid=None, ask=0.42),
                                          SideQuote("under", False)))
    assert sharp2.forecast(o, "o1", _rungs(one_sided), ev, "2026-10-23T22:55:00Z")[0] is None
    assert sharp2.forecast(o, "o1", [], ev, "2026-10-23T22:55:00Z")[0] is None          # no price at all


def test_missing_payout_modifier_never_becomes_a_payout():
    from sports import payouts
    pay, basis = payouts.ud_entry_payout(2, [1.0, None])
    assert pay is None and "missing" in basis


# ----------------------------------------------------------------------------------- DST / reschedules
def test_us_date_is_dst_correct():
    # 2026: DST ends Sunday Nov 1 06:00 UTC; starts Sunday Mar 8 07:00 UTC
    assert identity.us_date("2026-10-24T03:30:00Z") == "2026-10-23"          # 11:30 pm EDT
    assert identity.us_date("2026-11-02T04:30:00Z") == "2026-11-01"          # 11:30 pm EST (after fall back)
    assert identity.us_date("2026-11-01T04:30:00Z") == "2026-11-01"          # 00:30 EDT, Nov 1
    assert identity.us_date("2026-03-08T04:30:00Z") == "2026-03-07"          # 11:30 pm EST
    assert identity.us_date("2026-03-09T03:30:00Z") == "2026-03-08"          # 11:30 pm EDT
    assert identity.eastern_offset_hours(identity.datetime(2026, 11, 1, 5, 59,
                                                           tzinfo=identity.timezone.utc)) == -4
    assert identity.eastern_offset_hours(identity.datetime(2026, 11, 1, 6, 0,
                                                           tzinfo=identity.timezone.utc)) == -5


def test_reschedule_is_a_revision_with_history_and_still_resolves(db):
    body = {"events": [{"id": "77", "date": "2026-10-23T23:00Z", "season": {"year": 2027, "type": 2},
                        "competitions": [{"competitors": [
                            {"homeAway": "home", "team": {"id": "2", "abbreviation": "BOS", "displayName": "Boston"}},
                            {"homeAway": "away", "team": {"id": "18", "abbreviation": "NY", "displayName": "NY"}}],
                            "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre"}}}]}]}
    b1 = adapters.espn_events(body, NBA, h.T0)
    store.put_many(db, b1.records)
    body["events"][0]["date"] = "2026-10-25T19:00Z"
    body["events"][0]["competitions"][0]["status"]["type"] = {"name": "STATUS_SCHEDULED", "state": "pre"}
    prev = {e.event_id: e for _, e in store.events(db, "nba")}
    b2 = adapters.espn_events(body, NBA, "2026-10-23T21:00:00Z", prev)
    ev2 = [r for r in b2.records if r.KIND == "event"][0]
    assert ev2.start_utc == "2026-10-25T19:00:00Z" and ev2.reschedules[0].from_utc == "2026-10-23T23:00:00Z"
    assert store.put(db, ev2)["status"] == "revision"
    revs = store.revisions(db, "event", "espn", "nba:espn:77")
    assert [r[1].start_utc for r in revs] == ["2026-10-23T23:00:00Z", "2026-10-25T19:00:00Z"]
    r = adapters.Resolver("nba", identity.Teams("nba", h.NBA_TEAMS), [ev2])
    assert r.event("2026-10-25", "NYK", "BOS")[0] == "nba:espn:77"
    assert r.event("2026-10-23", "NYK", "BOS")[0] == "nba:espn:77"        # an offer listed on the old date


def test_postponed_game_is_not_settled():
    ev = h.event(status="postponed")
    assert settlement.settle_offer(h.offer(), {"pts": h.stat()}, ev, h.T0) == []


# --------------------------------------------------------------------------- duplicates / corrections
def test_duplicate_updates_store_once(db):
    b = adapters.pp_offers(fixture("pp_nba.json"), NBA, h.T0, h.resolver())
    r1 = store.put_many(db, b.records)
    b2 = adapters.pp_offers(fixture("pp_nba.json"), NBA, "2026-10-23T20:05:00Z", h.resolver())
    r2 = store.put_many(db, b2.records)
    assert r1["inserted"] == len(b.records) and r2["duplicate"] == len(b2.records) and r2["inserted"] == 0


def test_provider_correction_creates_a_superseding_settlement(db):
    ev = h.event(status="final")
    o = h.offer(line=23.5)
    s1 = settlement.settle_offer(o, {"pts": h.stat(value=23.0)}, ev, "2026-10-24T03:00:00Z")
    assert settlement.record(db, s1)["inserted"] == 2
    assert {x.side: x.status for x in s1} == {"over": "loss", "under": "win"}
    store.put(db, h.stat(value=24.0, observed_at="2026-10-24T09:00:00Z"))       # ESPN stat correction
    s2 = settlement.settle_offer(o, {"pts": h.stat(value=24.0, observed_at="2026-10-24T09:00:00Z")}, ev,
                                 "2026-10-24T09:00:00Z")
    c = settlement.record(db, s2)
    assert c["revision"] == 2
    revs = store.revisions(db, "settlement", "prizepicks", "pp:1|over")
    assert [r[1].status for r in revs] == ["loss", "win"] and revs[1][1].supersedes == revs[0][1].settlement_id
    assert settlement.record(db, s2)["unchanged"] == 2                         # re-run: nothing new
    obs = store.revisions(db, "stat", "espn", "nba:espn:1|nba:player:espn:3134907|full_game|pts")
    assert [x[1].value for x in obs] == [24.0]


# ------------------------------------------------------------------------------------------------ trades
def test_trade_closes_membership_and_old_team_offer_does_not_resolve():
    body = {"athletes": [{"id": "3134907", "displayName": "Karl-Anthony Towns", "status": {"type": "active"}}]}
    rost = identity.Rosters()
    b1 = adapters.espn_roster(body, NBA, h.NY, "2026-10-01T12:00:00Z", rost)
    m1 = [r for r in b1.records if r.KIND == "membership"][0]
    rost2 = identity.Rosters()
    b2 = adapters.espn_roster(body, NBA, h.CHI, "2026-10-20T12:00:00Z", rost2, previous={m1.player_id: m1})
    ms = [r for r in b2.records if r.KIND == "membership"]
    closed = [m for m in ms if m.team_id == h.NY][0]
    opened = [m for m in ms if m.team_id == h.CHI][0]
    assert closed.valid_to == "2026-10-20T00:00:00Z" and opened.valid_from == "2026-10-20T00:00:00Z"
    assert opened.valid_to is None and closed.identity() == m1.identity()      # a revision of the same membership
    ev = h.event(eid="nba:espn:5", home=h.MIL, away=h.CHI)
    r = adapters.Resolver("nba", identity.Teams("nba", h.NBA_TEAMS), [ev], rost2)
    assert r.player("Karl-Anthony Towns", "CHI", "nba:espn:5")[0] == "nba:player:espn:3134907"
    pid, why = r.player("Karl-Anthony Towns", "NYK", "nba:espn:5")             # a stale board still says NYK
    assert pid is None


# ---------------------------------------------------------------------------------------------- injuries
def test_injury_report_is_context_and_absence_is_unknown():
    b = adapters.espn_injuries(fixture("espn_injuries_nba.json"), NBA, h.T0)
    assert b.records and all(r.kind == "injury" for r in b.records)
    assert all(r.present in ("yes", "no") for r in b.records)
    assert all(r.detail is None or len(r.detail) < 60 for r in b.records)       # no editorial text stored
    healthy = {r.subject_id for r in b.records}
    assert "nba:player:espn:999999" not in healthy                              # absent = no record = unknown


# ------------------------------------------------------------------------------- missing participation
def test_participation_rules_by_platform():
    ev = h.event(status="final")
    dnp = {"min": h.stat(part="min", value=0.0, participation="did_not_play"),
           "pts": h.stat(value=0.0, participation="did_not_play")}
    pp = settlement.settle_offer(h.offer(), dnp, ev, h.T0)
    assert {s.status for s in pp} == {"void"}
    k = settlement.settle_offer(h.kalshi_rung(19.5, 0.4, 0.42), dnp, ev, h.T0)
    assert {s.status for s in k} == {"void"} and "fair-price" in k[0].reason
    unknown = {"pts": h.stat(value=None, participation="unknown")}
    assert {s.status for s in settlement.settle_offer(h.offer(), unknown, ev, h.T0)} == {"unresolved"}
    assert {s.status for s in settlement.settle_offer(h.offer(), {}, ev, h.T0)} == {"unresolved"}


def test_football_player_missing_from_every_box_group_is_unknown_not_zero():
    from sports import espn
    nfl = config.league("nfl")
    box = espn.parse_summary(fixture("espn_summary_nfl.json"), "football")
    box["players"]["ghost player"] = [{"name": "Ghost Player", "id": "1", "team": "KC", "played": None, "parts": {}}]
    b = adapters.espn_box(box, nfl, "nfl:espn:1", h.T0)
    ghost = [r for r in b.records if r.KIND == "stat" and r.participant_id == "nfl:player:espn:1"]
    assert all(r.participation == "unknown" and r.value is None for r in ghost)


def test_whole_number_tie_is_a_push():
    ev = h.event(status="final")
    s = settlement.settle_offer(h.offer(line=24.0), {"pts": h.stat(value=24.0)}, ev, h.T0)
    assert {x.status for x in s} == {"push"} and {x.outcome for x in s} == {"push"}
