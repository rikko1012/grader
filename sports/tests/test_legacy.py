"""sharp-1 research log -> canonical versioned research records (values never changed, conditional made explicit)."""
from datetime import datetime, timezone

from sports import config, edges, legacy, record, store
from sports.tests.test_sports_edges import offer, quote

NBA = config.league("nba")


def test_sharp1_rows_import_as_unconditional_forecasts(tmp_path):
    o = offer(player="Nikola Jokic", player_key="nikola jokic", line=25.0, market="points",
              start="2026-10-23T01:30:00Z", platform="prizepicks", offer_id="pp:9", game="DEN @ OKC")
    rows = edges.build(NBA, [o], [quote(player="Nikola Jokic", player_key="nikola jokic", line=24.5, market="points",
                                        p_over=0.55, start="2026-10-23T01:30:00Z")])
    src = record.connect(tmp_path / "legacy.db")
    assert record.save(src, rows, now=datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)) == 1
    raw = dict(src.execute("SELECT * FROM forecasts").fetchone())
    src.close()
    con = store.connect()
    c = legacy.import_file(tmp_path / "legacy.db", con)
    assert c == {"rows": 1, "inserted": 1, "duplicate": 0, "invalid": 0}
    assert legacy.import_file(tmp_path / "legacy.db", con)["duplicate"] == 1          # idempotent
    (_, f), = store.forecasts(con, model_version="sharp-1")
    con.close()
    assert f.model_version == "sharp-1" and f.research_only and f.validate() == []
    side_p = f.p_over if raw["side"] == "over" else f.p_under
    assert abs(side_p - raw["chance"] * (1 - raw["push"])) < 1e-12                    # unconditional
    cond = f.p_over_given_no_push if raw["side"] == "over" else f.p_under_given_no_push
    assert abs(cond - raw["chance"]) < 1e-12                                          # original kept, named
    assert f.p_push == raw["push"] and f"legacy_row:{raw['id']}" in f.reason_codes
