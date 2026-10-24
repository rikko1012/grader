"""Line correctness of the sportsbook store books.db (sports/LINE_CORRECTNESS.md rows B1-B9): price / line revisions,
A -> A sightings, delayed responses, withdrawal / reopening, concurrent writers, existing-database migration,
provider vs observation times, the shared lease."""
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import config
from sports.books import base, store

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
EV = "36220759"


def at(m):
    return NOW + timedelta(minutes=m)


def offers(price=-110, line=15.5, status="open", upd=None, sides=("over", "under")):
    common = dict(provider="fanduel", book="fanduel", jurisdiction="oh", league="nba", provider_event_id=EV,
                  event_name="New York Knicks @ Boston Celtics", home="Boston Celtics", away="New York Knicks",
                  start="2026-10-24T23:10:00Z", player="Payton Pritchard", market="points", stat_label="Points",
                  period="game", line=line, market_type="standard", status=status, provider_updated_at=upd)
    return [base.make_offer(**common, side=s, price_american=price) for s in sides]


@pytest.fixture
def db(tmp_path):
    d = store.connect(path=tmp_path / "books.db", dsn="")
    yield d
    d.close()


def save(db, offs, m, complete=True, fid=1):
    return store.save_offers(db, fid, "fanduel", "nba", EV, "espn:1", offs, complete, at(m))


def cur(db, line=15.5, side="over"):
    k = [o for o in offers(line=line) if o["side"] == side][0]["offer_key"]
    return db.one("SELECT s.*, q.price_american, q.status AS q_status, q.observed_at AS q_observed, "
                  "q.provider_updated_at AS q_upd FROM sports_book_offer_state s JOIN sports_book_quotes q "
                  "ON q.id = s.last_quote_id WHERE s.offer_key = ?", (k,))


def hist(db, line=15.5, side="over"):
    k = [o for o in offers(line=line) if o["side"] == side][0]["offer_key"]
    return db.all("SELECT price_american, status, late, observed_at, provider_updated_at FROM sports_book_quotes "
                  "WHERE offer_key = ? ORDER BY id", (k,))


def test_b1_price_a_b_a_is_three_rows_final_a_current(db):
    assert save(db, offers(-110), 0) == (2, 0)
    assert save(db, offers(-120), 10) == (2, 0)
    assert save(db, offers(-110), 20) == (2, 0)
    assert [r["price_american"] for r in hist(db)] == [-110, -120, -110]
    assert cur(db)["price_american"] == -110 and cur(db)["last_status"] == "open"


def test_b1_line_a_b_a_reopens_the_first_line(db):
    save(db, offers(line=15.5), 0)
    assert save(db, offers(line=16.5), 10) == (2, 2)          # 15.5 closed (complete read), 16.5 new
    assert save(db, offers(line=15.5), 20) == (2, 2)          # 15.5 reopened, 16.5 closed
    assert [r["status"] for r in hist(db, 15.5)] == ["open", "removed", "open"]
    assert [r["status"] for r in hist(db, 16.5)] == ["open", "removed"]
    assert cur(db, 15.5)["last_status"] == "open" and cur(db, 16.5)["last_status"] == "removed"
    from sports.books import compare
    lines = {r["line"] for r in compare.current(db, "nba", now=at(21))}
    assert lines == {15.5}


def test_b2_a_a_moves_freshness_only(db):
    save(db, offers(upd="2026-10-23T20:59:00Z"), 0)
    assert save(db, offers(upd="2026-10-23T21:09:00Z"), 10) == (0, 0)
    assert len(hist(db)) == 1
    c = cur(db)
    assert c["last_seen_at"] == "2026-10-23T21:10:00Z" and c["first_seen_at"] == "2026-10-23T21:00:00Z"
    assert c["last_provider_updated_at"] == "2026-10-23T21:09:00Z" and c["q_upd"] == "2026-10-23T20:59:00Z"


def test_b3_delayed_older_response_never_becomes_current(db):
    save(db, offers(-120, upd="2026-10-23T21:09:00Z"), 10)
    assert save(db, offers(-110, upd="2026-10-23T21:04:00Z"), 12) == (0, 0)   # source says older
    assert cur(db)["price_american"] == -120
    assert [(r["price_american"], r["late"]) for r in hist(db)] == [(-120, 0), (-110, 1)]
    assert save(db, offers(-110), 5) == (0, 0)                                    # no source time: ours orders
    assert cur(db)["price_american"] == -120
    assert cur(db)["last_seen_at"] == "2026-10-23T21:10:00Z"     # a late read is not a sighting of the current quote
    assert save(db, offers(-130, upd="2026-10-23T21:20:00Z"), 20) == (2, 0)
    assert cur(db)["price_american"] == -130


def test_b3_delayed_complete_read_cannot_withdraw_what_a_newer_read_saw(db):
    save(db, offers(), 10)
    assert save(db, [], 5) == (0, 0)                          # complete read from :05 stored after :10
    assert cur(db)["last_status"] == "open"
    assert save(db, [], 15) == (0, 2)                         # an on-time complete read without it does


def test_b3_delayed_duplicate_does_not_rewind_last_seen(db):
    save(db, offers(), 10)
    save(db, offers(), 5)
    assert cur(db)["last_seen_at"] == "2026-10-23T21:10:00Z"


def test_b4_withdrawal_and_reopening_at_the_same_line(db):
    save(db, offers(-110), 0)
    assert save(db, offers(-110)[:1], 10) == (0, 1)           # the Under left the board (complete read)
    assert save(db, offers(-110)[:1], 15, complete=False) == (0, 0)
    assert cur(db, side="under")["last_status"] == "removed"
    assert save(db, offers(-110), 20) == (1, 0)               # back at the same line and price
    u = cur(db, side="under")
    assert u["last_status"] == "open" and u["price_american"] == -110
    assert [r["status"] for r in hist(db, side="under")] == ["open", "removed", "open"]


def test_b4_suspended_without_price_does_not_crash(db):
    save(db, offers(-110), 0)
    sus = [dict(o, status="suspended", price_american=None, price_decimal=None) for o in offers(-110)]
    assert save(db, sus, 10) == (2, 0)
    assert save(db, offers(-110), 20) == (2, 0)
    assert cur(db)["last_status"] == "open"


WRITER = textwrap.dedent("""
    import sys
    from datetime import datetime, timedelta, timezone
    sys.path.insert(0, {root!r})
    from sports.books import store
    from sports.tests.test_line_correctness_books import offers, EV
    i, n, rounds = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    from pathlib import Path
    db = store.connect(path=Path({path!r}), dsn="")
    t0 = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    for k in range(rounds):
        sec = k * n + i
        price = -110 - 5 * ((k + i) % 3)
        store.save_offers(db, sec, "fanduel", "nba", EV, "espn:1", offers(price), True, t0 + timedelta(seconds=sec))
    db.close()
""")


def test_b7_concurrent_writers_keep_one_consistent_current_quote(tmp_path):
    path = tmp_path / "books.db"
    store.connect(path=path, dsn="").close()
    script = tmp_path / "w.py"
    script.write_text(WRITER.format(root=str(ROOT), path=str(path)), encoding="utf-8")
    n, rounds = 4, 30
    procs = [subprocess.Popen([sys.executable, str(script), str(i), str(n), str(rounds)], stdin=subprocess.DEVNULL)
             for i in range(n)]
    assert all(p.wait(timeout=240) == 0 for p in procs)
    db = store.connect(path=path, dsn="")
    last = rounds * n - 1
    k, i = divmod(last, n)
    want = -110 - 5 * ((k + i) % 3)
    for side in ("over", "under"):
        c = cur(db, side=side)
        assert c["price_american"] == want and c["last_status"] == "open"
        assert c["last_seen_at"] == (NOW + timedelta(seconds=last)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # the current quote is the newest non-late row; no non-late row is newer than it
        newer = db.one("SELECT COUNT(*) AS n FROM sports_book_quotes WHERE offer_key = ? AND late = 0 AND "
                       "observed_at > ?", (c["offer_key"], c["q_observed"]))["n"]
        assert newer == 0
    assert db.one("SELECT COUNT(*) AS n FROM sports_book_offer_state")["n"] == 2
    db.close()


def test_b8_existing_books_db_is_migrated_additively(tmp_path):
    path = tmp_path / "books.db"
    old = store.DB(path=path)                                 # the pre-change shape: SCHEMA only, no new columns
    assert "late" not in old.columns("sports_book_quotes")
    qid = old.insert(store._INSERT_Q, store._quote_row(offers(-110)[0], 1, "2026-10-23T21:00:00Z", "espn:1"))
    old.execute("INSERT INTO sports_book_offer_state (offer_key, provider, provider_event_id, league, event_key, "
                "player_key, market, last_quote_id, last_status, first_seen_at, last_seen_at, last_fetch_id) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)", (offers()[0]["offer_key"], "fanduel", EV, "nba", "espn:1",
                                               "payton pritchard", "points", qid, "open", "2026-10-23T21:00:00Z",
                                               "2026-10-23T21:00:00Z", 1))
    old.commit()
    before = [dict(r) for r in old.all("SELECT * FROM sports_book_quotes")]
    old.close()
    db = store.connect(path=path, dsn="")                     # restart with the new code
    assert {"late"} <= db.columns("sports_book_quotes")
    assert "last_provider_updated_at" in db.columns("sports_book_offer_state")
    rows = db.all("SELECT * FROM sports_book_quotes")
    assert [{k: r[k] for k in before[0]} for r in rows] == before and rows[0]["late"] == 0
    assert save(db, offers(-110)[:1], 10) == (0, 0)           # unchanged: a sighting on the migrated row
    assert cur(db)["last_seen_at"] == "2026-10-23T21:10:00Z"
    db.close()
    store.connect(path=path, dsn="").close()                  # idempotent


def test_b9_provider_and_observation_times_are_separate(db):
    save(db, offers(upd="2026-10-23T20:30:00Z"), 0)
    r = hist(db)[0]
    assert r["observed_at"] == "2026-10-23T21:00:00Z" and r["provider_updated_at"] == "2026-10-23T20:30:00Z"


def test_b9_books_worker_stamps_each_read_with_its_own_time(monkeypatch, tmp_path):
    from sports.books import worker
    calls = iter([NOW, NOW + timedelta(minutes=7)])
    monkeypatch.setattr(worker, "_now", lambda: next(calls))
    assert worker._seen_at(NOW, True) == NOW                  # a caller's fixed clock is kept
    assert worker._seen_at(NOW, False) == NOW
    assert worker._seen_at(NOW, False) == NOW + timedelta(minutes=7)


def test_b_lease_is_released_so_the_other_league_process_runs_next(db):
    assert store.take_lease(db, "nba-proc", 900, now=NOW)
    assert not store.take_lease(db, "nfl-proc", 900, now=NOW)     # while a pass runs: busy (no overlap)
    store.release_lease(db, "nba-proc")
    assert store.take_lease(db, "nfl-proc", 900, now=NOW + timedelta(seconds=1))
