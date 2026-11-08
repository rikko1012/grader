"""Player role tags, NBA / WNBA / NFL (owner 2026-11-07: "Sf PF, WR" on every player card). Display only: a new "role" field
("ro" on the compact board row) beside position / pos, which the models read and which stay exactly as they were.
Source: ESPN's depth chart (read from the shared cache only - a snapshot build never asks ESPN), else the ESPN roster
position; a depth row on another team is a namesake and is ignored; no source = no role."""
import json
from pathlib import Path

from sports import cache, espn, names, net, role_display as rd, snapshots, store
from sports.contracts import Membership, Player, Team
from sports.tests import helpers as h
from sports.tests.test_ui_snapshots import BRUNSON, KAT, NOW

FX = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def nba_depth():
    return espn.parse_depth(fixture("espn_depth_nba.json"), "basketball", "NY")       # ESPN's own code for the Knicks


def test_nba_depth_positions():
    idx = nba_depth()
    assert rd.role("nba", "Jalen Brunson", "NYK", "G", idx) == "PG"
    assert rd.role("nba", "Josh Hart", "NYK", "G-F", idx) == "SG"                     # his top slot
    assert rd.role("nba", "Karl-Anthony Towns", "NYK", "C", idx) == "C"
    assert rd.role("wnba", "Jalen Brunson", "NY", None, idx) == "PG"


def test_nfl_depth_slot_number_is_dropped():
    idx = espn.parse_depth(fixture("espn_depth_nfl.json"), "football", "DET")
    assert idx[names.key("Jameson Williams")]["pos"] == "wr2"
    assert rd.role("nfl", "Jameson Williams", "DET", "WR", idx) == "WR"
    assert rd.role("nfl", "Jared Goff", "DET", "QB", idx) == "QB"
    assert rd.role("nfl", "Sam LaPorta", "DET", "TE", idx) == "TE"
    assert rd.role("nfl", "Aidan Hutchinson", "DET", "DE", idx) == "DE"              # defence is not in the index: roster


def test_namesake_on_another_team_falls_back_to_the_roster():
    idx = nba_depth()
    assert rd.role("nba", "Josh Hart", "BOS", "G-F", idx) == "G/F"
    assert rd.role("nba", "Josh Hart", None, "C-F", idx) == "C/F"
    assert rd.role("nba", "Josh Hart", "BOS", None, idx) is None
    assert rd.role("nba", "Nobody Here", "NYK", "", idx) is None
    assert rd.role("nba", "", "NYK", None, idx) is None


def test_depth_index_is_read_from_the_cache_only(monkeypatch):
    monkeypatch.setattr(net, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ESPN read")))
    assert rd.depth_cached("nba") == {}                                              # nothing cached: no fetch, roster only
    assert rd.depth_cached("ncaab") == {}
    cache.put("espn:depth", "nba", nba_depth())
    assert rd.depth_cached("nba")[names.key("Jalen Brunson")]["pos"] == "pg"


def _world(con, kat_pos="C-F", brunson_pos="G"):
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"], observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())                                                       # NY @ BOS
    for pid, name, pos in ((KAT, "Karl-Anthony Towns", kat_pos), (BRUNSON, "Jalen Brunson", brunson_pos)):
        store.put(con, Player(player_id=pid, league="nba", name=name, position=pos, status="active", observed_at=h.T0, known_at=h.T0))
        store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn", observed_at=h.T0, known_at=h.T0))
    store.put(con, h.offer())                                                       # KAT points
    store.put(con, h.offer(oid="pp:jb", player_id=BRUNSON, player_name="Jalen Brunson", player_key="jalen brunson", stat="assists", line=6.5))


def _build(tmp_path, monkeypatch, depth):
    monkeypatch.setattr(net, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a snapshot build never asks ESPN")))
    if depth is not None:
        cache.put("espn:depth", "nba", depth)
    con = store.connect()
    _world(con)
    snapshots.build(con, "nba", now=NOW, base=tmp_path, history=False, results=False)
    con.close()
    b = json.loads((tmp_path / "nba" / "board.json").read_text(encoding="utf-8"))
    rows = {m["pid"]: m for m in b["markets"]}
    files = {}
    for f in (tmp_path / "nba" / "players").glob("*.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        files[p["player_id"]] = p
    return rows, files


def test_snapshot_rows_and_player_files_carry_one_role(tmp_path, monkeypatch):
    rows, files = _build(tmp_path, monkeypatch, nba_depth())
    assert rows[BRUNSON]["ro"] == files[BRUNSON]["role"] == "PG"
    assert rows[KAT]["ro"] == files[KAT]["role"] == "C"
    assert rows[KAT]["pos"] == files[KAT]["position"] == "C-F"                      # the models' position: unchanged
    assert rows[BRUNSON]["pos"] == files[BRUNSON]["position"] == "G"
    assert all(m.get("role") == files[pid]["role"] for pid in files for m in files[pid]["markets"])


def test_without_a_cached_depth_chart_the_roster_position_shows(tmp_path, monkeypatch):
    rows, files = _build(tmp_path, monkeypatch, None)
    assert rows[KAT]["ro"] == files[KAT]["role"] == "C/F" and rows[KAT]["pos"] == "C-F"
    assert rows[BRUNSON]["ro"] == files[BRUNSON]["role"] == "G"


def test_a_depth_row_of_another_team_is_ignored(tmp_path, monkeypatch):
    other = {k: dict(v, team="BOS") for k, v in nba_depth().items()}
    rows, files = _build(tmp_path, monkeypatch, other)
    assert rows[BRUNSON]["ro"] == "G" and rows[KAT]["ro"] == "C/F"


def test_no_position_anywhere_means_no_role_key(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ESPN read")))
    con = store.connect()
    _world(con, kat_pos=None, brunson_pos=None)
    snapshots.build(con, "nba", now=NOW, base=tmp_path, history=False, results=False)
    con.close()
    b = json.loads((tmp_path / "nba" / "board.json").read_text(encoding="utf-8"))
    assert all("ro" not in m for m in b["markets"])
    p = json.loads(next((tmp_path / "nba" / "players").glob("*.json")).read_text(encoding="utf-8"))
    assert p["role"] is None


def test_the_models_never_read_the_role():
    root = Path(__file__).resolve().parents[1]
    for f in list((root / "model").glob("*.py")) + list((root / "games").glob("*.py")):
        t = f.read_text(encoding="utf-8")
        assert "role_display" not in t and '"ro"' not in t, f
