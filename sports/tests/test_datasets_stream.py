"""nflverse files are read from disk as streams (sports/model/datasets.py _download / _rows): same rows and file record
as decoding the whole file, without holding it as one string. No network: the cached copy is used."""
import gzip
import hashlib

from sports.model import datasets


def test_cached_release_file_is_streamed_with_its_hash_recorded(tmp_path):
    con = datasets.connect(tmp_path / "model" / "history.db")
    text = "game_id,player_display_name\n2026_01_KC_BAL,Zoë Ünïcode\n2026_01_KC_BAL,Plain Name\n"
    raw = gzip.compress(text.encode("utf-8"))
    (datasets.db_path().parent / "files").mkdir(parents=True, exist_ok=True)
    (datasets.db_path().parent / "files" / "pbp.csv.gz").write_bytes(raw)        # fresh copy: no download
    rows = list(datasets._rows(datasets._download(con, "pbp.csv.gz", "https://example.invalid/pbp.csv.gz", gz=True)))
    assert [r["player_display_name"] for r in rows] == ["Zoë Ünïcode", "Plain Name"]
    assert rows == list(datasets._rows(text))                                    # str input (tests) reads the same
    f = con.execute("SELECT sha256, bytes FROM files WHERE name = 'pbp.csv.gz'").fetchone()
    assert (f["sha256"], f["bytes"]) == (hashlib.sha256(raw).hexdigest(), len(raw))   # hash of the file as stored
