"""pl-2 (owner 2026-10-15): pl-1's weights and probability calculation with corrected anchor eligibility, computed only from
the anchors logged with the forecast. pl-1 stays as recorded, promotion blocked."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import learning as L

T = "2026-10-16T10:00:00+00:00"


def q(book="underdog", line=30.5, mt="standard", over=True, under=True, po=1.9, pu=1.9, obs="2026-10-16T09:58:00+00:00", state="ok", **kw):
    return dict({"book": book, "source_id": "x", "line": line, "market_type": mt, "over_offered": over, "under_offered": under,
                 "price_over": po, "price_under": pu, "observed_at": obs, "feed_state": state}, **kw)


@pytest.mark.parametrize("rec,ok,why", [
    (q(), True, None), (q(book="prizepicks", po=None, pu=None), True, None),
    (q(mt="promo", book="prizepicks"), False, "boosted"), (q(mt="alt"), False, "alternate"), (q(mt="demon", book="prizepicks"), False, "adjusted"),
    (q(under=None), False, "not confirmed"), (q(over=False), False, "not confirmed"),
    (q(po=1.8, pu=2.03), False, "unequal side prices"), (q(obs=None), False, "unknown"),
    (q(obs="2026-10-16T09:45:00+00:00"), False, "limit 10"), (q(book="prizepicks", state="stale"), False, "feed state")])
def test_anchor_eligibility(rec, ok, why):
    e, r, lab = L.anchor_eligible(rec, T, 10)
    assert e is ok and (why is None or why in r)
    if ok:
        assert lab == ("price-supported" if rec["book"] == "underdog" else "price-unverified")


F = {"source": "board", "line_type": "standard", "book": "underdog", "prop": "kills", "line": 30.5, "logged_at": T, "lambda": 33.0}
FEAT = {"perf": {"lambda": 33.0, "sd": 6.0}}


def test_pl2_uses_only_eligible_logged_anchors_and_replays_exactly():
    other = q(book="prizepicks", line=31.5, po=None, pu=None)
    p, mu, t = L.prop_line_2_value(F, FEAT, {"anchors": {"target": q(), "other": other}})
    assert mu == 31.0 and t["other_used"] and t["anchor_changed"] and t["target_label"] == "price-supported"
    assert p == t["p_anchor"]                                              # kills w = 0: pl-2 == its corrected anchor, exactly
    stale = dict(other, observed_at="2026-10-16T09:40:00+00:00")
    p2, mu2, t2 = L.prop_line_2_value(F, FEAT, {"anchors": {"target": q(), "other": stale}})
    assert mu2 == 30.5 and not t2["other_used"] and "limit 10" in t2["other_reason"]   # stale: the target alone, reason kept
    p3, mu3, t3 = L.prop_line_2_value(F, FEAT, {"anchors": {"target": q(), "other": None, "other_reason": "ambiguous: 2 standard lines"}})
    assert t3["other_reason"].startswith("ambiguous") and mu3 == 30.5
    hs = dict(F, prop="hs", line=14.5)
    p4, mu4, t4 = L.prop_line_2_value(hs, {"perf": {"lambda": 16.5, "sd": 3.5}}, {"anchors": {"target": q(line=14.5), "other": None}})
    assert abs(mu4 - (14.5 + 0.25 * 2.0)) < 1e-12


def test_pl2_gives_no_forecast_when_the_target_is_ineligible():
    for tq, why in ((q(mt="promo", book="prizepicks"), "boosted"), (q(obs="2026-10-16T09:00:00+00:00"), "limit 30"), (None, "no quote")):
        p, mu, t = L.prop_line_2_value(F, FEAT, {"anchors": {"target": tq, "other": q(book="prizepicks")}})
        assert p is None and why in t["reject"]
    assert L.prop_line_2_value(F, FEAT, None)[2]["reject"].startswith("no anchors")
    assert L.prop_line_2_value(dict(F, source="member", book=None), FEAT, {"anchors": {"target": q()}})[0] is None


def test_pl1_is_blocked_but_kept_and_pl2_freezes_with_its_own_clock(tmp_path):
    con = L.connect(tmp_path / "l.db")
    t0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    L._ensure_prop_line(con, t0)
    L._ensure_prop_line_2(con, t0 + timedelta(hours=1))
    pl1 = con.execute("SELECT * FROM challengers WHERE family = 'prop-line-1'").fetchone()
    pl2 = con.execute("SELECT * FROM challengers WHERE family = 'prop-line-2'").fetchone()
    assert pl1["status"] == "blocked" and "anchor-quality" in pl1["status_reason"]
    assert pl2["status"] == "shadow" and pl2["frozen_at"] == "2026-10-15T03:00:00+00:00" and pl2["revises"] == pl1["id"]
    assert json.loads(pl2["config"])["w"] == json.loads(pl1["config"])["w"]               # same weights: only eligibility differs
    assert L.review_step(con, t0 + timedelta(days=60)) == []                             # nothing reviewed without forward units
    statuses = [r["status"] for r in con.execute("SELECT status FROM challengers WHERE status = 'shadow'")]
    assert "blocked" not in statuses                                                      # review_step only ever reads 'shadow'


def test_server_matches_the_other_platform_exactly(monkeypatch):
    import server
    pp_rows = [{"id": "pp:1", "prop": "kills", "scope": "12", "line": 31.5, "odds_type": "standard", "over_available": True, "under_available": True,
                "start": "2026-10-16T15:00:00+00:00", "updated_at": "2026-10-16T08:00:00+00:00"},
               {"id": "pp:2", "prop": "kills", "scope": "12", "line": 29.5, "odds_type": "standard", "start": "2026-10-16T15:00:00+00:00"},
               {"id": "pp:3", "prop": "hs", "scope": "12", "line": 14.5, "odds_type": "standard", "start": "2026-10-16T15:00:00+00:00"}]
    monkeypatch.setattr(server.prizepicks, "board", lambda *a, **k: {"rows": pp_rows, "ingested_at": 1792133880.0, "at": 1792133800.0, "state": "ok"})
    monkeypatch.setattr(server, "BOARD", {"pp:1": {"player_id": 7, "match_slug": "a-vs-b-16-10-2026"},
                                          "pp:2": {"player_id": 8, "match_slug": "a-vs-b-16-10-2026"},     # another player
                                          "pp:3": {"player_id": 7, "match_slug": "a-vs-b-16-10-2026"}})    # another stat
    rec, why = server.other_anchor("underdog", 7, "a-vs-b-16-10-2026", None, "kills", "12")
    assert why is None and rec["source_id"] == "pp:1" and rec["line"] == 31.5 and rec["market_type"] == "standard"
    assert rec["observed_at"] and rec["provider_read_at"] and rec["over_offered"] is True and rec["matched_on"].startswith("player_id")
    rec2, why2 = server.other_anchor("underdog", 7, "c-vs-d-16-10-2026", None, "kills", "12")       # another match
    assert rec2 is None and "no identified line" in why2


def test_end_to_end_logged_anchors_to_the_pl2_scorecard(monkeypatch, tmp_path):
    import zlib
    import picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    ldb = tmp_path / "l.db"
    t0 = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
    L.run_cycle(now=t0, picks=[], forecasts=[], batches=[], corrections=[], db=ldb, train=False)      # freezes pl-1 / pl-2
    con = picklog._db()
    con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, actual, "
                "hit, result, settled_at, test, on_board, units, logged_at, book, market_id, line_type) VALUES (1, 'a', 7, 'A', 'B', "
                "'a-vs-b-16-10-2026', '2026-10-16T15:00:00Z', 'kills', '12', 30.5, 'More', 'settled', 33, 1, ?, '2026-10-16T18:00:00+00:00', "
                "2, 1, 0.5, ?, 'underdog', 'm1', 'standard')",
                (json.dumps({"source": "hltv", "per_map": [[17, 8], [16, 7]], "rounds": [22, 21], "won": [True, True]}), T))
    quote = {"price_over": 1.9, "price_under": 1.9, "anchors": {"target": q(), "other": q(book="prizepicks", line=31.5, po=None, pu=None)}}
    con.execute("INSERT INTO forecasts (id, pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, model_version, "
                "inputs, quote) VALUES (1, 1, 'first', ?, '2026-10-16T15:00:00Z', 30.5, 'More', 0.6, 0.6, 'B', 0.5, 33.0, 'V', ?, ?)",
                (T, zlib.compress(json.dumps({"perf": {"version": "perf-2", "lambda": 33.0, "sd": 6.0}}).encode()), json.dumps(quote)))
    con.commit()
    con.close()
    L.run_cycle(now=t0 + timedelta(days=2), picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True), batches=[], corrections=[], db=ldb, train=False)
    lcon = L.connect(ldb)
    pl2 = lcon.execute("SELECT id FROM challengers WHERE family = 'prop-line-2'").fetchone()["id"]
    sh = lcon.execute("SELECT * FROM shadow WHERE challenger_id = ?", (pl2,)).fetchone()
    t = json.loads(sh["note"])
    assert t["other_used"] and t["anchor"] == 31.0 and abs(sh["p_over_ch"] - t["p_anchor"]) < 1e-12
    row = next(c for c in L.scorecard(lcon)["product"]["challengers"] if c["id"] == pl2)["c1_pl2"]
    assert row["eligible_units"] == 1 and row["coverage"] == 1.0 and row["anchor_changed"] == 1
    assert row["replay"]["rows_checked"] >= 1 and row["replay"]["mismatches"] == 0
    assert row["identity_kills"]["pl2_differs_from_B2"] == 0
    pl1 = next(c for c in L.scorecard(lcon)["product"]["challengers"] if c["id"].startswith("pl-001"))
    assert pl1["promotion"] and "blocked" in pl1["promotion"]


def test_the_replay_check_reruns_saved_inputs_and_catches_a_changed_value(tmp_path):
    con = L.connect(tmp_path / "l.db")
    L._ensure_prop_line_2(con, datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc))
    cid = con.execute("SELECT id FROM challengers WHERE family = 'prop-line-2'").fetchone()["id"]
    feat = {"perf": {"lambda": 33.123456789, "sd": 6.0987654321}}
    quote = {"anchors": {"target": q(), "other": q(book="prizepicks", line=31.5, po=None, pu=None)}}
    con.execute("INSERT INTO forecast_copy (forecast_id, pick_id, logged_at, line, prop, scope, source, book, line_type, features, quote) "
                "VALUES (1, 1, ?, 30.5, 'hs', '12', 'board', 'underdog', 'standard', ?, ?)", (T, json.dumps(feat), json.dumps(quote)))
    f = dict(con.execute("SELECT * FROM forecast_copy WHERE forecast_id = 1").fetchone())
    pc, mu, tr = L.prop_line_2_value(f, feat, quote)
    con.execute("INSERT INTO shadow (challenger_id, forecast_id, p_over_ch, note) VALUES (?, 1, ?, ?)", (cid, pc, json.dumps(tr)))
    assert L.pl2_replay_check(con, cid) == {"rows_checked": 1, "mismatches": 0, "source": "saved inputs (forecast copy + logged anchors)"}
    con.execute("UPDATE shadow SET p_over_ch = p_over_ch + 1e-9 WHERE forecast_id = 1")
    assert L.pl2_replay_check(con, cid)["mismatches"] == 1
