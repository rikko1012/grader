"""A slow historical report must not block publication of current forecasts."""
import json

import pytest

from sports import flags, scheduler
from sports.model import fwd, pipeline, report
from sports.tests.test_fwd import AT, fworld  # noqa: F401
from sports.tests.test_line_correctness_model import world  # noqa: F401


def test_forward_publishes_without_rebuilding_historical_reports(fworld, monkeypatch):
    path = pipeline.api_dir() / "admin_learning.json"
    path.write_text(json.dumps({"written_at": "old-report", "scorecard": [{"kept": True}]}))

    def forbidden(*args, **kwargs):
        raise AssertionError("historical report entered the live forecast path")

    monkeypatch.setattr(pipeline, "write_reports", forbidden)
    result = fwd.run(["nba"], forecast_at=AT, log=lambda *_: None)
    assert result["forecasts_added"] == 1
    saved = json.loads(path.read_text())
    assert saved["written_at"] == "old-report"
    assert saved["scorecard"] == [{"kept": True}]
    assert saved["forecast_run"]["forecasts_added"] == 1
    assert json.loads((pipeline.api_dir() / "forecasts_nba.json").read_text())["forecasts"]


def test_reports_leave_forecast_files_byte_identical(fworld):
    fwd.run(["nba"], forecast_at=AT, log=lambda *_: None)
    paths = [pipeline.api_dir() / name for name in ("forecasts_nba.json", "forecasts_nfl.json", "pmf_nba.json", "pmf_nfl.json")]
    before = [p.read_bytes() for p in paths]
    pipeline.write_reports(now=AT)
    assert [p.read_bytes() for p in paths] == before
    assert json.loads((pipeline.api_dir() / "admin_learning.json").read_text())["written_at"] == AT


def test_report_worker_requires_forecast_flag(monkeypatch):
    monkeypatch.setenv("SPORTS_FORECAST", "0")
    with pytest.raises(flags.FlagOff):
        report.main()


def test_report_has_independent_bounded_scheduler_slot():
    spec = scheduler.default_jobs()["model-report"]
    assert spec["cls"] == scheduler.HEAVY
    assert spec["timeout_s"] == 900
    assert spec["every_s"] == 3600
    assert set(spec["yields_to"]) == {"settle", "settle-post", "history"}
