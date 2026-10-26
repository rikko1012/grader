"""2026-10-26 load / security audit of the sports service side: one regression test per fix (single flight on snapshot
re-builds of the compare index, per-member ids in the in-process path, fixed asset types). No network. The forecast-parse
single flight and the non-ASCII admin header are covered by sports/tests/test_audit_backend.py (origin/main's fix)."""
import json
import threading
import time

import pytest

from sports import api_board, api_compare, authtoken, gateway, snapshots, web
from sports.tests.test_service import env  # noqa: F401  (fixture)


def _snap(tmp_lg, n=50):
    tmp_lg.mkdir(parents=True, exist_ok=True)
    board = {"events": {"nba:espn:1": {"start_utc": "2030-01-01T00:00:00Z", "status": "scheduled"}},
             "markets": [{"k": f"m{i}", "e": "nba:espn:1", "q": [{"p": "underdog", "r": f"offer:underdog:o{i}:r1:ab", "l": 20.5}]}
                         for i in range(n)]}
    (tmp_lg / "board.json").write_text(json.dumps(board))
    (tmp_lg / "forecasts.json").write_text(json.dumps({"version": "t", "built_at": "x",
                                                       "forecasts": {f"fc-{i:08x}": {"id": i} for i in range(n)}}))


def _burst(fn, n=24):
    start, out, errs = threading.Event(), [], []

    def run():
        start.wait()
        try:
            out.append(fn())
        except Exception as e:                          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(n)]
    for t in ts:
        t.start()
    start.set()
    for t in ts:
        t.join(10)
    assert not errs, errs
    return out


@pytest.fixture
def snaps(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path / "snapshots")
    monkeypatch.setattr(api_board, "_FILES", {})
    monkeypatch.setattr(api_board, "_PARSED", {})
    monkeypatch.setattr(api_compare, "_INDEX", {})
    _snap(tmp_path / "snapshots" / "nba")
    return tmp_path / "snapshots" / "nba"


def test_compare_index_is_built_once_per_board_change(snaps, monkeypatch):
    builds = []
    real = api_compare.build_index

    def slow(boards):
        builds.append(1)
        time.sleep(0.05)
        return real(boards)
    monkeypatch.setattr(api_compare, "build_index", slow)
    req = web.Request(path="/api/sports/compare/check", query={"league": "nba", "items": "underdog~o1~20.5"}, role="member")
    out = _burst(lambda: api_compare.check(req))
    assert len(builds) == 1 and all(o[0] == 200 and o[1]["items"][0]["status"] == "ok" for o in out)


def test_inprocess_mode_gives_each_member_their_own_id(monkeypatch):
    seen = []
    monkeypatch.setenv("SPORTS_API_MODE", "inprocess")
    monkeypatch.setattr(web, "dispatch", lambda req: seen.append(req.member) or (200, b"{}", "application/json"))
    for user in ("user_a", "user_b", "user_a"):
        gateway.handle_get("/api/sports/picks/mine", "", {}, member=lambda u=user: u, allowed=lambda: True,
                           admin=lambda: False, headers={})
    assert seen[0] == seen[2] != seen[1] and seen[0] == authtoken.subject("user_a")   # same subject as a st1 token
    monkeypatch.delenv("SPORTS_TOKEN_SECRET")
    a, b = gateway.inprocess_member("user_a"), gateway.inprocess_member("user_b")
    assert a != b and a.startswith("m_") and gateway.inprocess_member(None) is None


def test_static_script_and_style_types_never_come_from_the_host_registry(monkeypatch):
    monkeypatch.setitem(gateway.mimetypes.types_map, ".js", "text/plain")          # a Windows registry quirk
    monkeypatch.setattr(gateway, "_PAGES", {})
    code, _, ctype, _ = gateway.handle_get("/sports/app.js", "", {}, member=lambda: None, allowed=lambda: False,
                                           admin=lambda: False, headers={})
    assert code == 200 and ctype == "application/javascript; charset=utf-8"
    code, _, ctype, _ = gateway.handle_get("/sports/sports.css", "", {}, member=lambda: None, allowed=lambda: False,
                                           admin=lambda: False, headers={})
    assert code == 200 and ctype == "text/css; charset=utf-8"
