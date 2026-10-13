"""recap.py: every breakdown reconciles to its cohort total, statuses sum, pushes/voids stay out of the hit rate,
deduplication never merges different line / scope / side, and cohorts can never be mixed in one summary."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recap as R

_ID = [0]


def _p(test=2, status="settled", hit=1, line=30.5, pick="More", prop="kills", scope="12", book="underdog", units=0.5,
       p=0.6, grade="B", slug="zywoo", match="vit-vs-g2", start="2026-10-11T15:00:00+00:00", actual=None,
       logged="2026-10-11T10:00:00+00:00", line_type="standard", version="V1", first_logged_at=None):
    _ID[0] += 1
    if actual is None and status in ("settled", "push"):
        actual = (line + 1 if hit else line - 1) if pick == "More" else (line - 1 if hit else line + 1)
        if status == "push":
            actual = line
    return {"id": _ID[0], "key": f"k{_ID[0]}", "test": test, "status": status,
            "hit": hit if status == "settled" else None, "line": line, "pick": pick, "prop": prop, "scope": scope,
            "book": book, "units": units, "p_pick": p, "grade": grade, "slug": slug, "player": slug, "match_slug": match,
            "match_start": start, "actual": actual, "logged_at": logged, "line_type": line_type,
            "model_version": version, "first_logged_at": first_logged_at}


def _mixed_log():
    return [
        _p(), _p(hit=0, pick="Less", p=0.55), _p(status="push", line=30.0, hit=None), _p(status="void", hit=None, actual=None),
        _p(status="open", hit=None), _p(prop="hs", line=14.5, scope="1", units=0.0, grade="LEAN"),
        _p(scope="123", line=44.5, hit=0, units=1.0, grade="A"),
        _p(book="prizepicks", hit=1), _p(book=None, start="2026-10-08T15:00:00+00:00"),
        _p(test=0, book=None, units=0.0, grade="LEAN"), _p(test=0, book=None, hit=0, status="settled"),
        _p(test=0, book=None, status="push", line=20.0, hit=None), _p(test=1, book=None),
        _p(start="2026-10-12T03:00:00+00:00"),       # 22:00 Oct 11 Central
    ]


def test_statuses_sum_and_every_breakdown_reconciles():
    rc = R.recap(_mixed_log())
    for c in R.COHORTS:
        for basis in ("records", "deduplicated"):
            s = rc["cohorts"][c][basis]
            t = s["totals"]
            assert t["win"] + t["loss"] + t["push"] + t["void"] + t["pending"] == t["n"]
            for d in R.DIMENSIONS:
                rows = s["breakdowns"][d].values()
                for k in ("n", "win", "loss", "push", "void", "pending"):
                    assert sum(v[k] for v in rows) == t[k], (c, basis, d, k)
                assert abs(sum(v["expected_wins"] for v in rows) - t["expected_wins"]) < 1e-9
    assert rc["header"]["excluded"] == {"owner load tests (test=1)": 1}
    b = rc["cohorts"]["board"]["records"]["totals"]
    assert (b["n"], b["win"], b["loss"], b["push"], b["void"], b["pending"]) == (10, 5, 2, 1, 1, 1)
    m = rc["cohorts"]["member"]["records"]["totals"]
    assert (m["n"], m["win"], m["loss"], m["push"]) == (3, 1, 1, 1)
    s = rc["cohorts"]["staked"]["records"]["totals"]
    assert s["n"] == 9                                  # the LEAN 0U board row is board only


def test_pushes_and_voids_excluded_from_hit_rate_and_expected():
    log = [_p(p=0.7), _p(hit=0, p=0.6), _p(status="push", line=30.0, hit=None, p=0.9),
           _p(status="void", hit=None, p=0.9), _p(status="open", hit=None, p=0.9)]
    t = R.recap(log)["cohorts"]["board"]["records"]["totals"]
    assert t["decided"] == 2 and t["hit_rate"] == 0.5
    assert abs(t["expected_wins"] - 1.3) < 1e-9 and abs(t["expected_rate"] - 0.65) < 1e-9
    assert t["units_on_wins"] == 0.5 and t["units_on_losses"] == 0.5 and t["units_on_push"] == 0.5


def test_dedup_merges_identical_cross_platform_records_only():
    same = dict(slug="donk", match="spirit-vs-faze", line=25.5, pick="More", scope="12", prop="kills", hit=1)
    log = [_p(book="underdog", logged="2026-10-11T09:00:00+00:00", p=0.62, **same),
           _p(book="prizepicks", logged="2026-10-11T09:30:00+00:00", p=0.64, **same),
           # different line / scope / side / line type: never merged
           _p(book="prizepicks", **dict(same, line=26.5)),
           _p(book="prizepicks", **dict(same, scope="1")),
           _p(book="prizepicks", **dict(same, pick="Less", hit=0)),
           _p(book="prizepicks", line_type="demon", **same)]
    co = R.recap(log)["cohorts"]["board"]
    assert co["records"]["totals"]["n"] == 6
    assert co["deduplicated"]["totals"]["n"] == 5 and co["dedup"]["records_removed_by_dedup"] == 1
    assert co["deduplicated"]["platform_records"] == 6
    merged = co["deduplicated"]["breakdowns"]["platform"]["prizepicks + underdog"]
    assert merged["n"] == 1 and abs(merged["expected_wins"] - 0.62) < 1e-9     # earliest-published chance
    lines = {e["dedup_key"] for e in R.deduplicate([R.entry(r, "board") for r in log])[0]}
    assert len(lines) == 5


def test_dedup_keeps_same_proposition_with_different_outcomes_apart():
    same = dict(slug="m0nesy", match="g2-vs-navi", line=40.5, pick="Less")
    log = [_p(book="underdog", **same), _p(book="prizepicks", status="open", hit=None, **same)]
    co = R.recap(log)["cohorts"]["board"]
    assert co["deduplicated"]["totals"]["n"] == 2 and co["dedup"]["conflicting_groups_kept_separate"] == 1


def test_dedup_never_crosses_cohorts():
    same = dict(slug="ropz", match="faze-vs-mouz", line=33.5, pick="More")
    # the member typed the same line on the card (no platform: member-entered, on_board 0)
    rc = R.recap([_p(test=2, book="underdog", **same), _p(test=0, book=None, **same)])
    assert rc["cohorts"]["board"]["deduplicated"]["totals"]["n"] == 1
    assert rc["cohorts"]["member"]["deduplicated"]["totals"]["n"] == 1


def test_mixing_cohorts_raises():
    ents = [R.entry(_p(test=2), "board"), R.entry(_p(test=0, book=None), "member")]
    with pytest.raises(R.MixedCohortError):
        R.summarize(ents, "board")
    with pytest.raises(R.MixedCohortError):
        R.summarize(ents, "member")


def test_self_check_raises_when_a_subtotal_is_off():
    s = R.summarize([R.entry(_p(), "board"), R.entry(_p(hit=0), "board")], "board")
    s["breakdowns"]["side"]["Over"]["win"] += 1
    with pytest.raises(R.RecapError):
        R.reconcile(s)
    s = R.summarize([R.entry(_p(), "board")], "board")
    s["totals"]["pending"] += 1
    with pytest.raises(R.RecapError):
        R.reconcile(s)


def test_unknown_status_raises():
    with pytest.raises(R.RecapError):
        R.recap([_p(status="weird", hit=None)])


def test_day_uses_central_fixed_offset_and_date_range():
    log = [_p(start="2026-10-12T03:00:00+00:00"),           # Oct 11 22:00 Central -> Oct 11
           _p(start="2026-10-12T05:30:00+00:00")]           # Oct 12 00:30 Central -> Oct 12
    rc = R.recap(log, date_from="2026-10-11", date_to="2026-10-11")
    assert rc["cohorts"]["board"]["records"]["totals"]["n"] == 1
    assert list(rc["cohorts"]["board"]["records"]["breakdowns"]["day"]) == ["2026-10-11"]
    assert rc["header"]["excluded"] == {"match day outside the range": 1}


def test_cutoff_excludes_later_rows_and_counts_them():
    rc = R.recap([_p(), _p(logged="2026-10-12T20:00:00+00:00")], cutoff="2026-10-12T15:55:35Z")
    assert rc["cohorts"]["board"]["records"]["totals"]["n"] == 1
    assert rc["header"]["excluded"] == {"logged after the cutoff": 1}


def test_platform_of_unrecorded_board_book_is_labelled():
    rc = R.recap([_p(book=None), _p(test=0, book=None)])
    assert list(rc["cohorts"]["board"]["records"]["breakdowns"]["platform"]) == [R.UD_INFERRED]
    assert list(rc["cohorts"]["member"]["records"]["breakdowns"]["platform"]) == [R.NO_PLATFORM]


def test_markdown_labels_every_table_with_cohort_and_basis():
    md = R.render_markdown(R.recap(_mixed_log()))
    for line in md.splitlines():
        if line.startswith("### "):
            assert any(R.COHORT_LABEL[c] in line for c in R.COHORTS)
            assert "(platform records)" in line or "(deduplicated)" in line
    assert "NOT platform returns" in md


def test_board_line_opened_by_a_member_stays_a_board_grade():
    """2026-10-12: picklog rewrote a board row's test 2 -> 0 when a member opened it; on_board keeps it on the board."""
    import recap as R
    assert R.cohorts_of({"test": 0, "on_board": 1, "units": 0.5}) == ("board", "staked")
    assert R.cohorts_of({"test": 0, "on_board": 2, "units": 0}) == ("board",)
    assert R.cohorts_of({"test": 0, "on_board": 0, "units": 1}) == ("member",)
    assert R.cohorts_of({"test": 0, "book": "underdog", "units": 0}) == ("board",)       # older snapshot: inferred
    assert R.cohorts_of({"test": 0, "book": None, "units": 1}) == ("member",)


def test_picklog_keeps_board_provenance(monkeypatch, tmp_path):
    import sqlite3, picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "p.db"); monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    base = {"key": "k1", "logged_at": "2026-10-12T10:00:00+00:00", "player": "p", "prop": "kills", "line": 30.5,
            "pick": "More", "status": "open", "match_start": "2099-01-01T00:00:00Z", "p_pick": 0.6, "p_more": 0.6,
            "grade": "B", "units": 0.5, "lambda": 31.0}
    picklog.write_row(dict(base, test=2))                 # the board grades it
    picklog.write_row(dict(base, test=0))                 # a member opens it
    picklog.write_row(dict(base, key="k2", test=0))       # a member types a line nobody listed
    picklog.write_row(dict(base, key="k2", test=2))       # ... and the board lists it later
    con = sqlite3.connect(tmp_path / "p.db")
    got = dict(con.execute("SELECT key, on_board FROM picks").fetchall())
    assert got == {"k1": 1, "k2": 1}


def test_one_match_under_two_slugs_counts_once():
    """Audit D1: the board logged one match under a BO3.gg slug and hltv-<id>; accuracy counts it once."""
    a = _p(test=2, slug="xureba", match="lp-vs-ex-keyd-stars-12-10-2026", start="2026-10-12T21:00:00+00:00")
    b = _p(test=2, slug="xureba", match="hltv-2399300", start="2026-10-12T21:30:00+00:00")
    for r in (a, b):
        r.update(team="ex-Keyd Stars", opponent="LP", player="xureba")
    rc = R.recap([a, b])
    assert rc["cohorts"]["board"]["records"]["totals"]["n"] == 2
    assert rc["cohorts"]["board"]["deduplicated"]["totals"]["n"] == 1


def test_forecast_keeps_raw_chances_and_dataset_revision(monkeypatch, tmp_path):
    """Owner review 2026-10-12: a backfill changes inputs without a code change - every forecast records the HLTV dataset
    revision it was graded on, and the model's own over / under / push chances before caps."""
    import sqlite3, json as _j, picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "p.db"); monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: "matches=8578;max_id=2399300;max_date=2026-10-11")
    row = {"key": "k1", "logged_at": "2026-10-12T10:00:00+00:00", "player": "p", "prop": "kills", "line": 30.0,
           "pick": "More", "status": "open", "match_start": "2099-01-01T00:00:00Z", "p_pick": 0.6, "p_more": 0.6,
           "grade": "B", "units": 0.5, "lambda": 31.0, "test": 2,
           "_probs": _j.dumps({"over": 0.55, "under": 0.38, "push": 0.07, "p_more_no_push": 0.5914})}
    picklog.write_row(row)
    f = sqlite3.connect(tmp_path / "p.db").execute("SELECT probs_raw, data_rev FROM forecasts").fetchone()
    assert _j.loads(f[0])["push"] == 0.07 and f[1].startswith("matches=8578")
    cols = [r[1] for r in sqlite3.connect(tmp_path / "p.db").execute("PRAGMA table_info(picks)")]
    assert "_probs" not in cols and "probs_raw" not in cols


def test_a_change_of_odds_source_is_a_new_forecast(monkeypatch, tmp_path):
    """2026-10-13: the same numbers from another odds source (Polymarket -> Thunderpick) must be saved as a forecast with
    its own provenance - the saved forecast always matches the snapshot the grade used."""
    import sqlite3, picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "p.db"); monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    base = {"key": "k1", "logged_at": "2026-10-13T10:00:00+00:00", "player": "p", "prop": "kills", "line": 30.5,
            "pick": "More", "status": "open", "match_start": "2099-01-01T00:00:00Z", "p_pick": 0.6, "p_more": 0.6,
            "grade": "B", "units": 0.5, "lambda": 31.0, "test": 2}
    picklog.write_row(dict(base, _odds_src="polymarket"))
    picklog.write_row(dict(base, _odds_src="polymarket"))                 # unchanged: not logged again
    picklog.write_row(dict(base, _odds_src="thunderpick"))                # same numbers, new source: logged
    rows = sqlite3.connect(tmp_path / "p.db").execute("SELECT odds_source FROM forecasts ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["polymarket", "thunderpick"]
