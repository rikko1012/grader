"""pgn-1 LoL hook (lol/center/forecast.archive_news): next to every saved forecast, the posted player's team + role and
each team's last fielded lineup, change-only, observed at the save time; a failure never raises."""
import pregame_news as core
from lol.center import forecast


class _Idx:
    def last_lineup(self, team, day):
        return {"top": f"{team}-p1", "mid": f"{team}-p3"}


def _rec(role="mid"):
    return {"sid": "s1", "pid": "p9", "tid": "t1", "opp_tid": "t2", "role": role, "best_of": 3,
            "series_start": "2026-11-06T08:00:00Z"}


def test_archive_news_change_only(monkeypatch, tmp_path):
    db = tmp_path / "news.db"
    monkeypatch.setenv("PREGAME_NEWS_LOL_DB", str(db))
    monkeypatch.setitem(forecast._FF, "index", _Idx())
    assert forecast.archive_news([("2026-11-05T10:00:00Z", _rec())]) == {"news_rows": 3}
    assert forecast.archive_news([("2026-11-05T10:30:00Z", _rec())]) == {}
    assert forecast.archive_news([("2026-11-05T11:00:00Z", _rec("top"))]) == {"news_rows": 1}
    con = core.connect(db)
    rows = core.as_of(con, "lol", "p9", "2026-11-05T10:45:00Z", event="s1")
    assert rows[0]["news"]["role"] == "mid" and rows[0]["observed_at"] == "2026-11-05T10:00:00Z"
    lu = core.as_of(con, "lol", "t2", "2026-11-05T12:00:00Z", kinds=("lineup_assumed",))
    assert lu[0]["news"]["lineup"]["top"] == "t2-p1"


def test_archive_news_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PREGAME_NEWS_LOL_DB", str(tmp_path))        # a directory: cannot be opened
    out = forecast.archive_news([("2026-11-05T10:00:00Z", _rec())])
    assert "news_error" in out
    assert forecast.archive_news([]) == {}
