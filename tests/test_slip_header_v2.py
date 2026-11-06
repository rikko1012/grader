"""SCOUT_SLIP_HEADER_V2 (default off): competitor spec 4.3 live slip header. Formula tests for gradecore/slip_header.py and the
recheck route that carries it (every entry type at once, so the Power / Flex toggle repaints without a request)."""
import json

import pytest

import server
import workspace_api as W
from gradecore import payouts, slip_header as gsh
from gradecore.tests.pickem_ref import reference_tables  # noqa: F401  (fixture: PrizePicks REFERENCE rates, formula tests only)
from tests.test_workspace_api import calibrated_export, check, env, get, pair  # noqa: F401  (env is a fixture)


@pytest.fixture
def ref(reference_tables):
    """gradecore formula tests run on the PrizePicks REFERENCE table; the live config leaves it out (unverified)."""
    return reference_tables


def legs_of(*ps, same=False):
    return [{"p": p, "match_key": "m" if same else f"m{i}"} for i, p in enumerate(ps)]


# ---- formulas
def test_spec_check_two_leg_power_at_sixty_sixty_is_36_and_plus_8(ref):
    t = gsh.entry_header(legs_of(0.60, 0.60), "prizepicks", "power")
    assert t["rated"] and t["payout"] == 3.0 and t["win"] == 0.36 and t["ev"] == pytest.approx(0.08)
    assert t["break_even"] == pytest.approx((1 / 3) ** 0.5, abs=1e-4) and t["break_even_american"] == -137
    assert [l["below"] for l in t["legs"]] == [False, False]
    assert t["legs"][0]["ev"] == pytest.approx(0.60 / (1 / 3) ** 0.5 - 1, abs=1e-4)
    # quarter-Kelly on the slip's payout distribution: full = (pM - 1) / (M - 1) = 0.04, a quarter = 1% = 1 unit (display only)
    assert t["kelly_fraction"] == 0.25 and t["kelly_units"] == pytest.approx(1.0)


def test_flex_recomputes_win_paid_ev_and_break_even_from_its_own_tiers(ref):
    h = gsh.header(legs_of(0.60, 0.60, 0.55), "prizepicks")
    pw, fx = h["types"]["power"], h["types"]["flex"]
    p = [0.60, 0.60, 0.55]
    all3 = p[0] * p[1] * p[2]
    two = sum(p[i] * p[j] * (1 - p[k]) for i, j, k in ((0, 1, 2), (0, 2, 1), (1, 2, 0)))
    assert pw["ev"] == pytest.approx(all3 * 6.0 - 1, abs=1e-4) and fx["ev"] == pytest.approx(all3 * 3.0 + two * 1.0 - 1, abs=1e-4)
    assert pw["win"] == fx["win"] == pytest.approx(all3, abs=1e-4) and fx["paid"] == pytest.approx(all3 + two, abs=1e-4)
    assert fx["break_even"] == pytest.approx(payouts.tier_break_even({3: 3.0, 2: 1.0}, 3), abs=1e-4)
    assert pw["break_even"] == pytest.approx((1 / 6) ** (1 / 3), abs=1e-4)


def test_leg_below_break_even_is_marked_and_negative_ev_gives_zero_kelly(ref):
    t = gsh.entry_header(legs_of(0.60, 0.50), "prizepicks", "power")
    assert [l["below"] for l in t["legs"]] == [False, True] and t["ev"] == pytest.approx(0.6 * 0.5 * 3 - 1, abs=1e-4)
    assert t["ev"] < 0 and t["kelly_units"] == 0


def test_unrated_legs_say_how_many_and_keep_payout_and_break_even(ref):
    t = gsh.entry_header(legs_of(0.6, None, None), "underdog", "standard")
    assert not t["rated"] and t["reason"] == "Can't rate: 2 unrated legs." and t["payout"] == 6.5 and t["break_even"]
    assert "ev" not in t and "win" not in t and gsh.header(legs_of(0.6, None), "underdog")["unrated"] == 1


def test_same_match_needs_a_validated_correlation_then_shows_copula_ev_with_interval(ref):
    t = gsh.entry_header(legs_of(0.6, 0.6, same=True), "prizepicks", "power")
    assert not t["rated"] and t["reason"] == "Can't rate: same-match picks have no tested correlation."
    t = gsh.entry_header(legs_of(0.6, 0.6, same=True), "prizepicks", "power", correlation=lambda a, b: 0.3)
    assert t["rated"] and t["correlated"] and "20000 draws" in t["basis"]
    assert t["ev_low"] < t["ev"] < t["ev_high"] and t["win_low"] < t["win"] < t["win_high"]
    assert t["win"] > 0.36                      # positive correlation raises the all-hit chance over independence
    zero = gsh.entry_header(legs_of(0.6, 0.6, same=True), "prizepicks", "power", correlation=lambda a, b: 0.0)
    se = (zero["win_high"] - zero["win"]) / 1.96         # rho 0 agrees with the exact 36.0% within simulation error (3 SE)
    assert abs(zero["win"] - 0.36) < 3 * se and zero["ev"] == pytest.approx(3 * zero["win"] - 1, abs=1e-3)


def test_no_verified_table_or_size_is_unavailable_never_a_guess(ref):
    assert gsh.entry_header(legs_of(*[0.6] * 7), "prizepicks", "power")["reason"] == "No published power payout for 7 picks."
    assert gsh.header(legs_of(0.6, 0.6), "sleeper") is None and gsh.header([], "prizepicks") is None
    t = gsh.entry_header([dict(l, unpriced="set elsewhere") for l in legs_of(0.6, 0.6)], "prizepicks", "power")
    assert not t["rated"] and t["reason"] == "set elsewhere" and "payout" not in t


def test_ev_is_always_expected_payout_minus_one(ref):
    for book, entries in (("prizepicks", ("power", "flex")), ("underdog", ("standard", "flex"))):
        for e in entries:
            t = gsh.entry_header(legs_of(0.58, 0.61, 0.57), book, e)
            dist = __import__("gradecore.slip", fromlist=["x"]).poisson_binomial([0.58, 0.61, 0.57])
            want = sum(p * payouts.tiers(book, e, 3).get(k, 0.0) for k, p in enumerate(dist)) - 1
            assert t["ev"] == pytest.approx(want, abs=1e-4)


# ---- the route
def test_flag_off_sends_no_header(env, monkeypatch):
    monkeypatch.delenv("SCOUT_SLIP_HEADER_V2", raising=False)
    for b in ("underdog", "prizepicks"):
        assert "slip_header" not in json.dumps(check(env, pair(env, b))[1])
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "0")
    assert "slip_header" not in json.dumps(check(env, pair(env))[1])


def test_flag_on_prizepicks_rates_power_on_its_verified_table(env, monkeypatch):
    """Owner sign-off 2026-11-05: the PrizePicks help-center table is verified, so Power 2 is rated exactly where the live EV
    check is (the same steps as Underdog); Flex 2-pick is not an entry the app offers, so it is not shown."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    monkeypatch.delenv("SCOUT_SLIP_HEADER", raising=False)
    before = check(env, pair(env, "prizepicks"))[1]["entries"]["prizepicks"]
    pw = before["slip_header"]["types"]["power"]
    assert pw["rated"] is False and pw["reason"] == before["ev"]["reason"] and "settled matches" in pw["reason"]   # 0 settled
    assert pw["payout"] == 3.0 and pw["break_even"] == pytest.approx(3.0 ** -0.5, abs=1e-6) and "ev" not in pw
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    st, doc = check(env, pair(env, "prizepicks"))
    e = doc["entries"]["prizepicks"]
    h = e["slip_header"]
    assert set(h["types"]) == {"power"}
    pw = h["types"]["power"]
    ps = [doc["legs"][l["id"]]["current"]["p_side"] for l in pair(env, "prizepicks")]
    want = gsh.entry_header([{"p": p, "match_key": str(i)} for i, p in enumerate(ps)], "prizepicks", "power")
    assert e["ev"]["available"] is True and pw["rated"] is True
    assert pw["ev"] == pytest.approx(e["ev"]["ev"], abs=1e-3) == pytest.approx(want["ev"]) and pw["win"] == pytest.approx(want["win"])
    assert "kelly_units" not in pw and "kelly_fraction" not in pw


def test_flag_on_underdog_rates_only_where_the_live_ev_check_does(env, monkeypatch):
    monkeypatch.delenv("SCOUT_SLIP_HEADER_V2", raising=False)
    before = check(env, pair(env))[1]["entries"]["underdog"]
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    e = check(env, pair(env))[1]["entries"]["underdog"]
    std = e["slip_header"]["types"]["standard"]
    assert std["rated"] is False and std["reason"] == e["ev"]["reason"] and "settled matches" in std["reason"]    # 0 settled
    assert std["payout"] == 3.5 and "ev" not in std and "win" not in std
    assert {k: v for k, v in e.items() if k != "slip_header"} == before       # checks, rules, EV unchanged by the header
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    st, doc = check(env, pair(env))
    e = doc["entries"]["underdog"]
    std = e["slip_header"]["types"]["standard"]
    ps = [doc["legs"][l["id"]]["current"]["p_side"] for l in pair(env)]
    want = gsh.entry_header([{"p": p, "match_key": str(i)} for i, p in enumerate(ps)], "underdog", "standard")
    assert e["ev"]["available"] is True and std["rated"] is True
    assert std["ev"] == pytest.approx(e["ev"]["ev"], abs=1e-3) == pytest.approx(want["ev"]) and std["win"] == pytest.approx(want["win"])
    assert "kelly_units" not in std and "kelly_fraction" not in std          # no Kelly on the member slip (owner decision pending)
    assert e["slip_header"]["types"]["flex"]["rated"] is False                 # Underdog lists no 2-pick flex
    assert '"slip_header"' not in json.dumps(get(env, "mine")[1])


def test_flag_on_held_cs2_letter_is_not_rated_and_shows_the_hold(env, monkeypatch):
    """Review 2026-11-05 blocker: a held CS2 letter gets the hold banner from the live EV path, never a header EV."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    monkeypatch.setattr(W.lc, "state", lambda sport: {"contained": True} if sport == "cs2" else {})
    e = check(env, pair(env))[1]["entries"]["underdog"]
    banner = W.lc.banner("cs2")
    assert e["ev"]["available"] is False and e["ev"]["reason"] == banner
    for t in e["slip_header"]["types"].values():
        assert t["rated"] is False and "ev" not in t and "win" not in t
    assert e["slip_header"]["types"]["standard"]["reason"] == banner


def test_header_is_built_once_per_leg_set_not_per_member(env, monkeypatch):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    calls = []
    real = gsh.header
    monkeypatch.setattr(gsh, "header", lambda *a, **k: calls.append(1) or real(*a, **k))
    legs = pair(env)
    for u in ("u1", "u2", "u3"):
        assert "slip_header" in check(env, legs, user=u)[1]["entries"]["underdog"]
    assert len(calls) == 1


def test_special_lines_multipliers_and_failures(env, monkeypatch, ref):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    plain = {"ok": True, "_ev": {"sport": "cs2", "p": 0.6, "on_side": True, "match": "b"}}
    goblin = {"ok": True, "_ev": dict(plain["_ev"], match="a", line_type="goblin")}
    two = [{"id": 1}, {"id": 2}]
    h = W._slip_header_v2("prizepicks", two, {1: goblin, 2: plain})
    assert all(not t["rated"] and t["reason"] == W.SPECIAL_LINE_REASON for t in h["types"].values())
    ud = W._slip_header_v2("underdog", two, {1: {"ok": True, "_ev": dict(plain["_ev"], match="a", multiplier=1.1)}, 2: plain})
    assert ud["types"]["standard"]["reason"] == W.UD_MULTIPLIER_REASON and not ud["types"]["standard"]["rated"]
    against = W._slip_header_v2("prizepicks", two, {1: {"ok": True, "_ev": dict(plain["_ev"], on_side=False, match="a")}, 2: plain})
    assert against["unrated"] == 1 and against["types"]["power"]["reason"] == "Can't rate: 1 unrated leg."
    lol = W._slip_header_v2("prizepicks", two, {1: {"ok": True, "_ev": dict(plain["_ev"], sport="lol", on_side=False, match="a")}, 2: plain})
    assert lol["unrated"] == 0                           # LoL / NBA / NFL print a chance for either side
    assert W._slip_header_v2("prizepicks", two, {1: plain, 2: plain})["types"]["power"]["reason"].startswith("Can't rate: same-match")
    assert W._slip_header_v2("prizepicks", [{"id": 1}], {1: plain}) is None
    assert W._slip_header_v2("prizepicks", two, {1: dict(plain, ok=False), 2: plain}) is None
    four = W._slip_header_v2("underdog", [{"id": i} for i in range(4)], {i: {"ok": True, "_ev": dict(plain["_ev"], match=str(i))} for i in range(4)})
    assert four["types"]["flex"]["reason"] == W.UD_FLEX4_REASON and four["types"]["standard"]["rated"]
    monkeypatch.setattr(gsh, "header", lambda *a, **k: 1 / 0)
    server._RESULTS.clear()                              # the shared header copies from the checks above
    e = check(env, pair(env, "prizepicks"))[1]["entries"]["prizepicks"]
    assert "slip_header" not in e and "ok" in e
