"""Bug sweep 2026-11-06: #21 asks_at_cutoff cuts at the SAME earliest stated start as forward_rows (incl. ml_forecasts'
HLTV start), and the ml_store archive keeps exactly the rows that cutoff reads; #46 the Underdog line-move memo
(_LAST_SEEN) stays bounded - lines of matches that started more than 3 days ago are dropped unless still on the board."""
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import ml_eval
import ml_store
import underdog
from tests.test_ml_store import COLS, NOW, built, iso, store  # noqa: F401 - shared fixture


def _q(**kw):
    return dict({c: None for c in COLS}, **kw)


def _db(tmp_path):
    db = tmp_path / "ml.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ml_forecasts (match_id TEXT, model_version TEXT, logged_ts TEXT, platform TEXT, prob_a REAL,"
                " team_a TEXT, team_b TEXT, start TEXT, available INTEGER, disagreement REAL, reason TEXT, rule TEXT)")
    con.execute(f"CREATE TABLE ml_quotes ({', '.join(COLS)})")
    con.execute("INSERT INTO ml_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("hltv:1", "ml-baseline-1", "2026-11-05T13:20:00Z", "polymarket", 0.6, "A", "B", "2026-11-05T14:00:00Z",
                 1, 0, None, "rule"))                                       # HLTV start 14:00
    common = dict(platform="polymarket", market_id="m", outcome_id="m-a", match_id="hltv:1", link_status="linked",
                  team_name="A", team_side="a", ask=0.62, bid=0.60, start="2026-11-05T15:00:00Z", bo=3,
                  source="polymarket", scope="series", team_a="A", team_b="B")
    rows = [_q(**common, received_ts="2026-11-05T13:20:00Z", captured_at="2026-11-05T13:20:00Z", usable=1, prob_a=0.61),
            _q(**common, received_ts="2026-11-05T14:10:00Z", captured_at="2026-11-05T14:10:00Z", usable=0,
               unusable_reason="started")]                                  # written after the HLTV start: unusable
    con.executemany(f"INSERT INTO ml_quotes ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                    [tuple(r[c] for c in COLS) for r in rows])
    con.commit()
    con.close()
    return str(db)


def test_21_ask_is_cut_at_the_forecasts_earliest_start(tmp_path):
    db = _db(tmp_path)
    fwd, _ = ml_eval.forward_rows(db, Counter())
    cut = ml_eval._ts("2026-11-05T14:00:00Z") - ml_eval.CUTOFF_MIN * 60
    assert fwd and all(r["at"] <= cut for r in fwd)                          # forecasts cut at 13:30
    assert ml_eval.asks_at_cutoff(db) == {"hltv:1": {"ask_a": 0.62, "ask_a_platform": "polymarket"}}   # was {}
    assert ml_eval.earliest_starts(db) == {"hltv:1": ml_eval._ts("2026-11-05T14:00:00Z")}


def test_21_archive_keeps_the_rows_the_shared_cutoff_reads(store, monkeypatch):
    """The production-like store with every ml_forecasts start 1 h before the platforms' start: the evaluation reads
    the same forecasts AND asks before and after the archive pass."""
    monkeypatch.setattr(ml_store, "RETAIN_DAYS", 7)
    db, _n = built(store)
    con = sqlite3.connect(db)
    for mid, st, ta, tb in con.execute("SELECT match_id, MIN(start), team_a, team_b FROM ml_forecasts "
                                       "GROUP BY match_id").fetchall():   # rows are immutable: add an earlier HLTV start
        d = datetime.fromtimestamp(ml_eval._ts(st), timezone.utc) - timedelta(hours=1)
        con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, platform, prob_a, team_a, team_b, "
                    "start, available, disagreement, reason, rule) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, "ml-baseline-1", iso(d - timedelta(days=2)), "polymarket", 0.55, ta, tb, iso(d), 1, 0, None,
                     "rule"))
    con.commit()
    con.close()
    ex0 = Counter()
    fwd0, _ = ml_eval.forward_rows(str(db), ex0)
    asks0 = ml_eval.asks_at_cutoff(str(db))
    assert fwd0 and asks0
    out = ml_store.archive_pass(now=NOW, max_days=100)
    assert out["error"] is None and out["rows"] > 0
    ex1 = Counter()
    fwd1, _ = ml_eval.forward_rows(str(db), ex1)
    assert fwd1 == fwd0 and ex1 == ex0
    assert ml_eval.asks_at_cutoff(str(db)) == asks0


# ------------------------------------------------------------------ #46
def _rows(start, n=300, tag="d"):
    return [{"alt": False, "player": f"P{tag}{i}", "team": "A", "opponent": "B", "start": start, "prop": "kills",
             "scope": "12", "line": 20.5, "higher": 1.0, "lower": 1.0, "id": f"{tag}{i}"} for i in range(n)]


def test_46_last_seen_memo_drops_old_matches_and_restart_seed_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})
    for d in range(1, 11):                                                   # ten past days of boards (months ago)
        underdog._log_moves(_rows(f"2026-01-{d:02d}T15:00:00Z", tag=f"d{d}"))
    assert len(underdog._LAST_SEEN) <= 300 + 1                               # only the current board stays (was 3001)
    soon = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    underdog._log_moves(_rows(soon, tag="now"))
    keys = [k for k in underdog._LAST_SEEN if k != "_seeded"]
    assert len(keys) == 300 and all(k[3] == soon for k in keys)
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})                          # restart
    underdog._log_moves([])
    keys = [k for k in underdog._LAST_SEEN if k != "_seeded"]
    assert len(keys) == 300 and all(k[3] == soon for k in keys)             # was every historical key (3300)


def test_46_a_line_still_on_the_board_is_never_relogged_and_a_move_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})
    db = tmp_path / ".cache" / "ud_moves.db"
    n = lambda: sqlite3.connect(db).execute("SELECT count(*) FROM moves").fetchone()[0]   # noqa: E731
    old = _rows("2026-01-01T15:00:00Z", n=2)                                 # an old start still listed
    underdog._log_moves(old)
    underdog._log_moves(old)
    assert n() == 2
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})                          # restart: seeded because it is on the board
    underdog._log_moves(old)
    assert n() == 2
    underdog._log_moves([dict(old[0], line=21.5), old[1]])
    assert n() == 3
