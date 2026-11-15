import sqlite3
from lol.center import winner_form


def test_series_matchup_uses_only_prior_completed_series_and_exact_teams():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE c_series(team1,team2,team1_name,team2_name,winner,scheduled_utc,sid,status,ff,nullified)")
    rows = [("a","b","Alpha","Bravo",1,"2026-11-01","1","complete",0,0),
            ("a","b","Alpha","Bravo",2,"2026-11-12","2","complete",0,0),
            ("a","b","Alpha","Bravo",2,"2026-11-03","3","complete",1,0)]
    con.executemany("INSERT INTO c_series VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    d = {"observed_at":"2026-11-12T10:00:00Z","games":[{"teams":["Bravo","Alpha"]},{"teams":["Alpha Academy","Bravo"]}]}
    winner_form.attach(con,d)
    assert set(d["games"][0]["winner_matchup"]) == {"Series Elo","Recent series form","Head-to-head"}
    assert all(v < 0 for v in d["games"][0]["winner_matchup"].values())
    assert "winner_matchup" not in d["games"][1]
    con.close()
