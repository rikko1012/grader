"""SPORTS_CONTEXT_RECONCILE (remaining brief 13): same-game injury report reconciliation with source and forecast
timestamps. Flag off = the snapshot files of before (no new key anywhere); flag on = player files and slate games carry
`context_check`, which compares the injury report Grader held at each forecast's forecast_at with the report it holds
now. Display only: forecasts, chances and the store are untouched. Every name in the fixtures is a TEST FIXTURE."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import context_reconcile as cr
from sports import snapshots, store
from sports.contracts import ContextSnapshot, Membership, Player, Team
from sports.tests import helpers as h
from sports.tests.test_ui_snapshots import _fc_consistent

NOW = "2026-10-23T21:00:00Z"
F = h.T0                                               # every fixture forecast is made at 20:00
KAT = "nba:player:espn:3134907"                        # the forecast player (NY)
MATE = "nba:player:espn:3934672"                       # NY, Questionable before and after: no change
MATE2 = "nba:player:espn:900002"                       # NY, Out at the forecast, listed Active later
OPP = "nba:player:espn:900001"                         # BOS, added to the report after the forecast
WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def inj(pid, status, at, present="yes", as_of=None):
    return ContextSnapshot(league="nba", kind="injury", subject_id=pid, event_id=None, status=status, present=present,
                           detail="Ankle", as_of=as_of, observed_at=at, known_at=at, source_updated_at=as_of,
                           source_time_unknown=as_of is None)


def world(con):
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())                                                        # NY @ BOS, 23:00
    for pid, name, team in ((KAT, "Fixture Forward", h.NY), (MATE, "Fixture Guard", h.NY),
                            (MATE2, "Fixture Center", h.NY), (OPP, "Fixture Opponent", h.BOS)):
        store.put(con, Player(player_id=pid, league="nba", name=name, position="F", status="active",
                              observed_at=h.T0, known_at=h.T0))
        store.put(con, Membership(player_id=pid, team_id=team, valid_from="2026-07-01T00:00:00Z", source="espn",
                                  observed_at=h.T0, known_at=h.T0))
    store.put(con, inj(KAT, "questionable", "2026-10-23T19:30:00Z", as_of="2026-10-23T19:00:00Z"))
    store.put(con, inj(MATE, "questionable", "2026-10-23T19:30:00Z", as_of="2026-10-22T18:00:00Z"))
    store.put(con, inj(MATE2, "out", "2026-10-23T19:30:00Z", as_of="2026-10-22T18:00:00Z"))
    # after the forecast (20:00): the audit case - the forecast player goes Questionable -> Out
    store.put(con, inj(KAT, "out", "2026-10-23T20:30:00Z", as_of="2026-10-23T20:25:00Z"))
    store.put(con, inj(OPP, "out", "2026-10-23T20:45:00Z"))                       # ESPN gave no entry date
    store.put(con, inj(MATE2, "active", "2026-10-23T20:50:00Z", present="no", as_of="2026-10-23T20:48:00Z"))
    # the 20:50 read still lists the others unchanged: duplicates move only last_seen_at (a real read re-sees every
    # listed entry; av-1 shows only entries in the newest read)
    store.put(con, inj(KAT, "out", "2026-10-23T20:50:00Z", as_of="2026-10-23T20:25:00Z"))
    store.put(con, inj(MATE, "questionable", "2026-10-23T20:50:00Z", as_of="2026-10-22T18:00:00Z"))
    store.put(con, inj(OPP, "out", "2026-10-23T20:50:00Z"))
    pp = store.put(con, h.offer())["record_id"]                                      # PrizePicks 20.5 for KAT
    fc = _fc_consistent(pp)
    store.put(con, fc)
    return fc


def build(con, tmp_path):
    snapshots.build(con, "nba", now=NOW, base=tmp_path, history=False, results=False)
    root = snapshots.snap_root(tmp_path) / "nba"
    return {n: json.loads((root / p).read_text(encoding="utf-8")) for n, p in (
        ("player", f"players/{snapshots.safe_name(KAT)}.json"), ("slate", "slate.json"),
        ("manifest", "manifest.json"), ("forecasts", "forecasts.json"), ("board", "board.json"))}


def _strip(o):
    """Everything but the reconciliation and the wall-clock build time."""
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in ("context_check", "built_at", "build_s", "build_rss_mb",
                                                                  "build_stage_s")}
    if isinstance(o, list):
        return [_strip(x) for x in o]
    return o


def test_switch_is_off_unless_the_environment_turns_it_on(monkeypatch):
    monkeypatch.delenv(cr.FLAG, raising=False)
    assert cr.on() is False
    for v in ("1", "true", "YES", "on"):
        assert cr.on({cr.FLAG: v}) is True
    for v in ("", "0", "off", "no"):
        assert cr.on({cr.FLAG: v}) is False


def test_change_rules_listed_status_only():
    b = {"a": {"status": "questionable", "present": "yes"}, "b": {"status": "out", "present": "yes", "detail": "Knee"},
         "c": {"status": "out", "present": "yes"}}
    a = {"a": {"status": "out", "present": "yes", "as_of": "x1", "read_at": "r1", "source": "espn"},
         "b": {"status": "out", "present": "yes", "detail": "Ankle"},                  # detail-only: not a change
         "c": {"status": "active", "present": "no", "as_of": "x3", "read_at": "r3"},
         "d": {"status": "out", "present": "yes", "as_of": None, "read_at": "r4"}}
    got = {c["player_id"]: c for c in cr.changes(b, a, ["a", "b", "c", "d", "e"])}
    assert set(got) == {"a", "c", "d"}
    assert (got["a"]["before"], got["a"]["after"], got["a"]["source_as_of"], got["a"]["read_at"]) == \
        ("questionable", "out", "x1", "r1")
    assert (got["c"]["before"], got["c"]["after"]) == ("out", None)                   # listed Active = not listed
    assert (got["d"]["before"], got["d"]["after"], got["d"]["source_as_of"]) == (None, "out", None)


def test_flag_off_writes_no_reconciliation_and_flag_on_changes_nothing_else(tmp_path, monkeypatch):
    con = store.connect()
    world(con)
    monkeypatch.delenv(cr.FLAG, raising=False)
    off = build(con, tmp_path / "off")
    assert "context_check" not in json.dumps(off)
    n_records = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    monkeypatch.setenv(cr.FLAG, "1")
    on = build(con, tmp_path / "on")
    assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == n_records     # no store write
    assert "context_check" in on["player"] and "context_check" in on["slate"]["events"][0]
    assert on["manifest"]["context_check"]["games_changed"] == 1
    for k in ("player", "slate", "forecasts", "board"):                                 # forecasts, chances untouched
        assert _strip(on[k]) == _strip(off[k]), k
    m_on, m_off = _strip(on["manifest"]), _strip(off["manifest"])
    assert m_on.pop("sizes").pop("slate") > m_off.pop("sizes").pop("slate")            # the slate carries the block
    assert m_on == m_off
    con.close()


def test_flag_on_reconciles_the_forecast_player_teammates_and_opponents_with_both_times(tmp_path, monkeypatch):
    con = store.connect()
    fc = world(con)
    monkeypatch.setenv(cr.FLAG, "1")
    got = build(con, tmp_path)
    con.close()
    pf = got["player"]
    assert pf["forecasts"][fc.forecast_id]["forecast_at"] == F
    cc = pf["context_check"]
    assert cc["version"] == cr.VERSION and cc["checked_at"] == NOW and cc["player_id"] == KAT
    assert cc["report_read_at"] == "2026-10-23T20:50:00Z" and cc["source"] == "ESPN injury report"
    x = cc["by_forecast_at"][F]
    assert x["state"] == "changed" and x["listed_then"] == 3 and x["listed_now"] == 3
    rows = [(c["player"], c["role"], c["team"], c["before"], c["after"], c["source_as_of"], c["read_at"])
            for c in x["changes"]]
    assert rows == [
        ("Fixture Forward", "player", "NYK", "questionable", "out", "2026-10-23T20:25:00Z", "2026-10-23T20:30:00Z"),
        ("Fixture Center", "teammate", "NYK", "out", None, "2026-10-23T20:48:00Z", "2026-10-23T20:50:00Z"),
        ("Fixture Opponent", "opponent", "BOS", None, "out", None, "2026-10-23T20:45:00Z")]
    assert x["player_change"]["player_id"] == KAT
    assert MATE not in {c["player_id"] for c in x["changes"]}                             # unchanged teammate
    ev = got["slate"]["events"][0]["context_check"]
    assert ev["event_id"] == "nba:espn:1" and list(ev["by_forecast_at"]) == [F]
    assert "role" not in ev["by_forecast_at"][F]["changes"][0]                           # the game view has no role
    # the board's own injury entry (unchanged rule) shows the new status
    assert pf["injury"]["status"] == "out"


def test_no_change_since_the_forecast_says_unchanged(tmp_path, monkeypatch):
    con = store.connect()
    world(con)
    monkeypatch.setenv(cr.FLAG, "1")
    snapshots.build(con, "nba", now="2026-10-23T20:10:00Z", base=tmp_path, history=False, results=False)
    con.close()
    pf = json.loads((snapshots.snap_root(tmp_path) / "nba" / "players" / f"{snapshots.safe_name(KAT)}.json")
                    .read_text(encoding="utf-8"))
    x = pf["context_check"]["by_forecast_at"][F]
    assert x["state"] == "unchanged" and x["changes"] == [] and x["player_change"] is None


def test_bounded_store_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(cr, "statuses_at", lambda con, lg, at: calls.append(at) or {})
    times = [f"2026-10-23T{h_:02d}:00:00Z" for h_ in range(0, 20)]
    c = cr.Checker(None, "nba", NOW, times + ["2026-10-24T00:00:00Z"])               # a time after now is ignored
    assert len(c.times) == cr.MAX_TIMES and c.times[0] == "2026-10-23T19:00:00Z"
    for i in range(50):                                                                 # 50 games, same times
        c.event_block(f"e{i}", {"t": ["p"]}, times, str, {})
    assert len(calls) == 1 + cr.PER_EVENT_TIMES                                         # now + 3 newest, read once
    assert c.event_block("old", {"t": ["p"]}, ["2026-10-23T01:00:00Z"], str, {}) is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_member_text_in_node():
    script = Path(__file__).resolve().parent / "ui_context_test.js"
    r = subprocess.run(["node", str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_pages_draw_it_only_from_the_snapshot_and_keep_owner_wording():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    lab = (WEB / "labels.js").read_text(encoding="utf-8")
    assert "contextCheckHtml(P.context_check, fc && fc.forecast_at" in app
    assert "e.context_check ? " in app                                                   # game page: only when present
    assert 'if (!r) return "";' in app
    i = lab.index("function contextCheck(")
    body = lab[i:lab.index("var api = ", i)].lower()
    for banned in ("not betting", "financial advice", "not a bet", "place a bet", "maybe", "unsure", "uncertain",
                   "might", "hypothetical", "no bet"):
        assert banned not in body, banned
