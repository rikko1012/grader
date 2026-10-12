"""Card / board context fields added 2026-10-11 (display only - none of them moves a grade):
last-10 environment label + the other environment's skipped series, HLTV last-5 map order, the comparison panel's chances
at the graded line, the form-gap numbers, board cap reasons + warning marker, match exposure (staked picks only) and
"Did you mean" suggestions."""
import copy
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparison as cmp
import cs2_scout
import grader
import hltv
import server
from tests.test_perf import _inputs


def _series(mid, day, kills, env, opp="Opp", start_number=1):
    """Maps 1-2 rows of one series (newest-first lists are built by the caller)."""
    return [{"match_id": mid, "date": day, "number": start_number + i, "map": "de_x", "kills": k, "headshots": k // 2,
             "rounds": 22, "won": True, "opponent": opp, "env": env, "source": "hltv"} for i, k in enumerate(kills)]


# ---------------------------------------------------------------- 1. last-10 environment label
def test_last10_env_label_lists_skipped_online_series_without_changing_the_list():
    # 6 LAN series (tonight is LAN), 2 online series inside that span (one = Oct 8 vs PRIVATE), 1 older online series
    deep = []
    deep += _series("on-8", "2026-10-08", [15, 13], "online", "PRIVATE")
    deep += _series("lan-6", "2026-10-05", [14, 14], "lan")
    deep += _series("on-4", "2026-10-04", [10, 9], "online", "Rival")
    for i, d in enumerate(("2026-09-30", "2026-09-25", "2026-09-20", "2026-09-15", "2026-09-10")):
        deep += _series(f"lan-{i}", d, [12, 13], "lan")
    deep += _series("on-old", "2026-08-01", [20, 20], "online", "Old")
    deep.sort(key=lambda r: (r["date"], r["match_id"]), reverse=True)
    pb = {"_match_env": {}}
    ok = cs2_scout._usable_series(deep, "kills", 2)
    listed = cs2_scout._first_series(cs2_scout._env_rows(ok, pb, "lan"), 10)      # what finish_line's last10() lists
    before = copy.deepcopy(listed)
    rf = cs2_scout.recent_form(listed, 0.7, 26.5, "kills")
    n_before = rf["matches"]
    cs2_scout._label_env(rf, deep, listed, pb, "lan", "kills", 2)
    assert listed == before and rf["matches"] == n_before == 6                   # values only: the list is unchanged
    assert rf["env"] == "lan" and rf["env_skipped_n"] == 2
    assert rf["env_skipped"] == [{"date": "2026-10-08", "opponent": "PRIVATE", "total": 28, "env": "online"},
                                 {"date": "2026-10-04", "opponent": "Rival", "total": 19, "env": "online"}]
    rf2 = cs2_scout.recent_form(listed, 0.7, 26.5, "kills")
    cs2_scout._label_env(rf2, deep, listed, pb, None, "kills", 2)                 # not filtered: no label, nothing skipped
    assert rf2["env"] is None and rf2["env_skipped"] == [] and rf2["env_skipped_n"] == 0


def test_last10_env_skipped_keeps_only_three_newest():
    deep = []
    for i in range(5):
        deep += _series(f"on-{i}", f"2026-10-0{9 - i}", [10, 10 + i], "online", f"O{i}")
    for i in range(6):
        deep += _series(f"lan-{i}", f"2026-09-{20 + i:02d}", [12, 12], "lan")
    deep.sort(key=lambda r: (r["date"], r["match_id"]), reverse=True)
    pb = {}
    listed = cs2_scout._first_series(cs2_scout._env_rows(cs2_scout._usable_series(deep, "kills", 2), pb, "lan"), 10)
    rf = cs2_scout._label_env({}, deep, listed, pb, "lan", "kills", 2)
    assert rf["env_skipped_n"] == 5 and [s["opponent"] for s in rf["env_skipped"]] == ["O0", "O1", "O2"]


# ---------------------------------------------------------------- 2. HLTV last 5 maps in played order
class _Cur:
    def __init__(self, rows, fail_first=None):
        self.rows, self.fail_first, self.sql = rows, fail_first, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.fail_first and len(self.sql) == 1:
            raise Exception(self.fail_first)

    def fetchall(self):
        return list(self.rows)


def test_last5_orders_each_series_by_map_number():
    d1, d0 = date(2026, 9, 25), date(2026, 9, 20)
    # the database can hand one series' maps back in any order (Astralis Sep 25: Ancient, Nuke, Cache)
    rows = [(d1, "Ancient", "X", 13, 9, 20, 15, 1.2, "hltv", 501, 2), (d1, "Nuke", "X", 10, 13, 15, 17, 0.9, "hltv", 501, 1),
            (d1, "Cache", "X", 13, 7, 18, 12, 1.3, "hltv", 501, 3), (d0, "Mirage", "Y", 13, 4, 22, 9, 1.6, "hltv", 400, 2),
            (d0, "Inferno", "Y", 13, 11, 17, 16, 1.0, "hltv", 400, 1)]
    cur = _Cur(rows)
    out = hltv._last5(cur, [7])
    assert [(r["map"], r["number"], r["match"]) for r in out] == [("Nuke", 1, 501), ("Ancient", 2, 501), ("Cache", 3, 501),
                                                                  ("Inferno", 1, 400), ("Mirage", 2, 400)]
    assert "mm.number ASC" in cur.sql[0] and "LEFT JOIN match_maps" in cur.sql[0]


def test_last5_older_database_without_map_numbers_still_answers():
    rows = [(date(2026, 9, 25), "Nuke", "X", 13, 9, 20, 15, 1.2, "hltv", 501, None)]
    cur = _Cur(rows, fail_first='relation "match_maps" does not exist')
    out = hltv._last5(cur, [7])
    assert out[0]["map"] == "Nuke" and out[0]["number"] is None and "match_maps" not in cur.sql[1]


# ---------------------------------------------------------------- 3. comparison: chances at the graded line
def _res_with_g7(seed=3):
    rnd = random.Random(seed)
    d = _inputs(rnd, "12"); d["prop"] = "kills"; d["line"] = 30.5
    p1 = dict(d["perf"], **{"lambda": d["perf"]["lambda"] + 1.5, "sd": d["perf"]["sd"] * 1.6})
    d["perf"] = dict(d["perf"], g7=p1)
    g = grader.grade_leg(dict(d))
    side = "More" if g["p_more"] >= 0.5 else "Less"
    return {"grade": g, "grader_inputs": d, "verdict": {"side": side, "p": g["p_more"] if side == "More" else g["p_less"]},
            "player_block": {"player": {"nick": "cairne"}, "playing_for": "fnatic", "recent_form": {}},
            "next_match": {"start": "2026-10-09T15:00:00.000+00:00", "opponent": "K27", "slug": "fnatic-vs-k27"}}


def test_panel_chances_are_at_the_graded_line_for_both_models():
    for seed in range(1, 8):
        res = _res_with_g7(seed)
        g = res["grade"]
        # an Underdog offer at ANOTHER line must not change the panel's "chance at the line"
        rows = [{"id": "u1", "player": "cairne", "team": "Fnatic", "opponent": "K27", "start": "2026-10-09T15:00:00Z",
                 "prop": "kills", "scope": "12", "line": 28.5, "higher": 1.0, "lower": 1.0, "alt": False}]
        pnl = cmp.panel(res, rows, now=datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
        side = pnl["ours"]["side"]
        assert pnl["ours"]["line"] == g["line"] == pnl["internal"][0]["line"]
        assert pnl["ours"]["chance_at_line"] == (g["p_more"] if side == "Over" else g["p_less"])
        assert pnl["ours"]["chance_at_line"] == res["verdict"]["p"]                 # the card's own chance
        it = pnl["internal"][0]
        assert it["p_side"] == (it["p_over"] if side == "Over" else it["p_under"]) and "sd" not in it   # spread stays private
        assert "sd" not in pnl["ours"] and isinstance(it["spread_differs"], bool)
        dp, do = it["projection"] - g["lambda_final"], it["p_over"] - g["p_more"]
        assert it["spread_differs"] == (abs(dp) >= 0.05 and abs(do) >= 0.001 and (dp > 0) != (do > 0))


def test_panel_leaves_the_grade_untouched():
    res = _res_with_g7(2)
    before = json.dumps(res["grade"], sort_keys=True, default=str)
    cmp.panel(res, [], now=datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
    assert json.dumps(res["grade"], sort_keys=True, default=str) == before


# ---------------------------------------------------------------- 4. form gap
def test_form_gap_plain_numbers(monkeypatch):
    today = date.today()
    rows = []
    for i, k in enumerate((30, 26, 22, 18)):
        rows += _series(f"s{i}", (today - timedelta(days=20 + 60 * i)).isoformat(), [k // 2, k - k // 2], "lan")
    rows += _series("old", (today - timedelta(days=400)).isoformat(), [40, 40], "lan")          # older than 12 months: out
    rows += _series("bo1", (today - timedelta(days=5)).isoformat(), [25], "lan")                 # no Map 2: out
    monkeypatch.setattr(cs2_scout, "_form_rows", lambda pb, stat="kills", keep_n=10, back_days=None: rows)
    pb = {"recent_maps": [], "recent_form": {"avg_total": 27.4, "matches": 10, "env": "lan"},
          "_rank_band": {"opp_rank": 12, "band": 10, "maps": 31, "kpr": 0.71, "kpr_all": 0.68, "ratio": 1.044, "teams": ["A"]}}
    res = {"grade": {"lambda_final": 24.04}, "next_match": {"opponent": "MOUZ"}}

    class A:
        scope, opponent = "12", "MOUZ"
    fg = cs2_scout._form_gap(res, pb, A, "kills")
    assert fg == {"last10_avg": 27.4, "last10_n": 10, "last10_env": "lan", "projection": 24.0, "gap": -3.4,
                  "months12_avg": 24.0, "months12_n": 4, "stat": "kills",
                  "rank_band": {"opp_rank": 12, "kpr_ratio": 1.044, "maps": 31}, "opponent": "MOUZ"}
    A.scope = "1"
    assert cs2_scout._form_gap(res, pb, A, "kills") is None                                       # Maps 1-2 lines only


# ---------------------------------------------------------------- 5. board cap reasons + warning marker
def test_stake_cap_reasons_plain_words_only_when_binding():
    g = {"flags": ["STAND-IN for Foo: different team and role than his usual - SD inflated, max 1U",
                   "Gate C: roster not confirmed - regional H2H extension disabled",
                   "Gate D: only 8 Maps 1-2 - variance flag, SD inflated, capped 1.5U"]}
    assert server.stake_cap_reasons(g, {"units": 1.0, "p": 0.75}) == ["stand-in"]   # 2U earned: the 1U stand-in cap binds, not the 1.5U one
    assert server.stake_cap_reasons(g, {"units": 1.0, "p": 0.70}) == ["stand-in"]                  # 1.5U earned: 1.5 cap not binding
    assert server.stake_cap_reasons(g, {"units": 1.0, "p": 0.64}) == []                            # 1U earned: nothing capped
    assert server.stake_cap_reasons(g, {"units": 0.0, "p": 0.75}) == []                            # a Lean has no cap
    rust = {"flags": ["RUST: last official map 40 days ago - SD inflated, max 1U", "SOFT BOOK: win chance from a thin/wide "
                      "Polymarket book - max 1.5U"]}
    assert server.stake_cap_reasons(rust, {"units": 1.0, "p": 0.75}) == ["long break"]   # 1U rust binds; the 1.5U market cap did not cut
    assert server.cap_reason_text(["stand-in"], 4) == "stand-in; same-match cap (4 bets on this team)"
    assert server.cap_reason_text([], None) is None
    assert server.has_warning({"flags": ["Gate C: roster not confirmed - regional H2H extension disabled"]})
    assert not server.has_warning({"flags": ["CAP: model chance 81.0% shown as 78% (kills cap - fat tails)",
                                             "TAPE: projection 24.0 vs last-10 average 27.4 (-3.4) - display only",
                                             "Note: last official map 9 days ago"]})


def test_board_rows_carry_cap_reason_and_flagged(monkeypatch):
    import roles
    import underdog
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    feed = [{"id": f"r{i}", "player": f"p{i}", "team": "Aa", "opponent": "Bb", "matchup": "Aa vs Bb", "start": start,
             "prop": "kills", "scope": "12", "line": 30.5, "alt": False} for i in range(5)]
    monkeypatch.setattr(underdog, "board", lambda wait=False: {"rows": feed, "at": 0, "error": None})
    base = {"side": "More", "p": 0.75, "grade": "A", "units": 2.0, "graded_line": 30.5, "state": "GRADE", "dstat": ["ok", ""],
            "cap_grade": [], "warn": False}
    board = {"r0": dict(base, units=1.0, cap_grade=["stand-in"]), "r1": dict(base), "r2": dict(base),
             "r3": dict(base, units=0.0, grade="LEAN", cap_grade=[], warn=True),
             "r4": dict(base, dstat=["verify", "roster not confirmed"])}
    monkeypatch.setattr(server, "BOARD", board)
    monkeypatch.setattr(server, "awp_role", lambda *a, **k: (None, "unknown"))
    monkeypatch.setattr(roles, "igl_role", lambda *a, **k: (None, None))
    monkeypatch.setattr(server, "_row_match_id", lambda r: None)
    rows = {r["id"]: r for r in server.board_payload()["rows"]}
    # 1 + 2 + 2 + 2 = 7U on one team, same side: the same-match cap cuts them all
    assert rows["r1"]["cap_reason"] == "same-match cap (4 bets on this team)"
    assert rows["r0"]["cap_reason"] == "stand-in; same-match cap (4 bets on this team)"
    assert rows["r3"]["cap_reason"] is None                                    # 0U Lean: no cap
    assert rows["r4"]["flagged"] and not rows["r2"]["flagged"]                  # data status 'verify' marks the row
    # a row in a capped group that the cap did not cut (0.5U floor) does not claim the same-match cap
    feed[:] = feed[:4]
    board.update(r1=dict(base, units=0.5, p=0.58, grade="B"), r2=dict(base))          # 1 + 0.5 + 2 = 3.5U > 3U
    rows = {r["id"]: r for r in server.board_payload()["rows"]}
    assert rows["r1"]["cap_n"] and rows["r1"]["units"] == rows["r1"]["units_full"] == 0.5 and rows["r1"]["cap_reason"] is None
    assert rows["r2"]["cap_reason"] == "same-match cap (3 bets on this team)"
    assert rows["r3"]["flagged"] and not rows["r1"]["flagged"]
    assert "cap_grade" not in rows["r0"] and "warn" not in rows["r0"]


def test_board_row_graded_before_cap_words_reads_its_reason(monkeypatch):
    assert server._legacy_cap_words("Projection 31.0 vs line 28.5 (+2.5 kills) · stake capped: RUST") == ["long break"]
    assert server._legacy_cap_words("Projection 31.0 vs line 28.5") == []


# ---------------------------------------------------------------- 7. match exposure counts staked picks only
def test_team_exposure_counts_only_picks_with_units():
    ps = [{"nick": "a", "kills": {"line": 30.5, "side": "More", "units": 1.0}, "hs": {"line": 14.5, "side": "Less", "units": 0.5}},
          {"nick": "b", "kills": {"line": 28.5, "side": "More", "units": 1.0}, "hs": {"line": 13.5, "side": "More", "units": 0.0}},
          {"nick": "c", "kills": {"proj": 20.0, "line": None, "no_line": True}, "hs": None}]
    e = server.team_exposure(ps)
    assert e == {"picks": 3, "over": 2, "under": 1, "units": 2.5, "leans": 1,
                 "both_props": [{"nick": "a", "kills": "Over", "hs": "Under"}]}           # b's HS is a 0U Lean: not exposure


# ---------------------------------------------------------------- 8. "Did you mean ...?"
def test_suggest_nicks_from_the_index(monkeypatch):
    rows = [{"nick": n, "team": "T", "key": hltv._norm(n), "loose": hltv._loose(hltv._norm(n))}
            for n in ("m0NESY", "ZywOo", "donk", "sh1ro", "monk", "s1mple", "NiKo")]
    monkeypatch.setattr(hltv, "dsn", lambda: "postgresql://x")
    monkeypatch.setitem(hltv._INDEX, "rows", rows)
    monkeypatch.setitem(hltv._INDEX, "at", __import__("time").time())
    assert hltv.suggest_nicks("monesyy")[0] == "m0NESY"
    assert "ZywOo" in hltv.suggest_nicks("zywo")
    assert "donk" not in hltv.suggest_nicks("donk")                            # never the typed name itself
    assert len(hltv.suggest_nicks("s", 5)) <= 5
    monkeypatch.setitem(hltv._INDEX, "rows", [])
    assert hltv.suggest_nicks("monesy") == []
