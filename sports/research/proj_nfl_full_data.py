"""pnf-1 data: the full NFL history working db for the projection rebuild (research only, read-only inputs).

    python -m sports.research.proj_nfl_full_data --ds1 <history_ds1_2026-10-23.db> --work <nfl_full.db> --out <new db>

Builds ONE local SQLite file in the ds-1 schema (games / nfl_pg / nfl_tg / nfl_ids) so the repo's own featurizer
(sports.model.featurize.nfl_frame) runs on it unchanged:

  * every table of the committed ds-1 artifact (nflverse 2022-09 .. 2026-10-23, 86,354 player-games), copied as is;
  * ESPN per-player game logs (sports service /api/sports/history, harvested read-only) for games AFTER the ds-1 cutoff
    (2026 weeks 7-8, the games of the settled book-line props). Those rows carry box-score volume only (attempts,
    completions, carries, targets, receptions, yards); snap share / target share / air yards / red-zone stay NULL and
    `src` = 'espn' marks them. Their team-game rows exist (rest days, QB-change lag) with NULL play counts, and the
    games are marked final. ESPN ids map to gsis ids through nfl_ids; team abbreviations LAR -> LA, WSH -> WAS.

Nothing here reads outcomes into features: this only assembles history rows; featurize keeps every feature strictly
prior to the row's game (start + 6 h known-at rule)."""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

TEAM_MAP = {"LAR": "LA", "WSH": "WAS"}
STATS = ("pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int", "carries", "rush_yds", "targets", "rec", "rec_yds")


def _ro(path):
    return sqlite3.connect("file:" + str(Path(path)).replace("\\", "/") + "?mode=ro", uri=True)


def build(ds1, work, out):
    out = Path(out)
    if out.exists():
        out.unlink()
    shutil.copyfile(ds1, out)
    out.chmod(0o644)
    con = sqlite3.connect(out)
    con.execute("PRAGMA journal_mode=OFF")
    cols = [r[1] for r in con.execute("PRAGMA table_info(nfl_pg)")]
    if "src" not in cols:
        con.execute("ALTER TABLE nfl_pg ADD COLUMN src TEXT DEFAULT 'nflverse'")
        con.execute("UPDATE nfl_pg SET src = 'nflverse'")
    cutoff = con.execute("SELECT MAX(g.start_utc) FROM games g JOIN nfl_pg p ON p.game_id = g.game_id "
                         "WHERE g.league = 'nfl' AND g.status = 'final'").fetchone()[0]
    w = _ro(work)
    games = {r[0]: r for r in con.execute("SELECT espn_id, game_id, season, season_type, week, home, away, start_utc "
                                         "FROM games WHERE league = 'nfl' AND espn_id IS NOT NULL")}
    gsis = {}
    for g, e in con.execute("SELECT gsis_id, espn_id FROM nfl_ids WHERE espn_id IS NOT NULL AND gsis_id IS NOT NULL"):
        try:
            gsis[str(int(float(e)))] = g
        except ValueError:
            continue
    last_pos = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT p.player_id, p.position, p.position_group, p.name FROM nfl_pg p JOIN games g ON g.game_id = p.game_id "
        "ORDER BY g.start_utc")}
    ctx_pos = {str(r[0]).split(":")[-1]: (r[1], r[2]) for r in w.execute("SELECT player_id, position, player FROM "
                                                                         "espn_player_ctx")}
    rows = w.execute("SELECT player_id, event_id, start_utc, team, opp, home, participation, pass_att, pass_cmp, "
                     "pass_yds, pass_td, pass_int, rush_att, rush_yds, targets, rec, rec_yds FROM espn_gamelog "
                     "WHERE start_utc > ? ORDER BY start_utc", (cutoff,)).fetchall()
    added, teams, finals, skipped = 0, set(), set(), {}
    for r in rows:
        eid = str(r[1]).split(":")[-1]
        g = games.get(eid)
        if g is None or r[6] != "played":
            skipped["no_game" if g is None else "not_played"] = skipped.get("no_game" if g is None else
                                                                            "not_played", 0) + 1
            continue
        espn = str(r[0]).split(":")[-1]
        pid = gsis.get(espn, "espn:" + espn)
        pos, pgrp, name = last_pos.get(pid, (None, None, None))
        if pos is None:
            cp = ctx_pos.get(espn, (None, None))
            pos, pgrp, name = cp[0], cp[0], cp[1]
        team, opp = TEAM_MAP.get(r[3], r[3]), TEAM_MAP.get(r[4], r[4])
        game_id = g[1]
        if con.execute("SELECT 1 FROM nfl_pg WHERE game_id = ? AND player_id = ?", (game_id, pid)).fetchone():
            continue
        vals = dict(zip(("pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int", "carries", "rush_yds", "targets",
                         "rec", "rec_yds"), r[7:17]))
        con.execute("INSERT INTO nfl_pg (game_id, player_id, espn_id, name, position, position_group, team, opp, home, "
                    "season, week, season_type, pass_att, pass_cmp, pass_yds, pass_td, pass_int, carries, rush_yds, "
                    "targets, rec, rec_yds, src) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'espn')",
                    (game_id, pid, espn, name, pos, pgrp, team, opp, int(r[5] or 0), g[2], g[4], g[3],
                     *[vals[k] for k in STATS]))
        added += 1
        teams.add((game_id, team, opp, int(r[5] or 0)))
        teams.add((game_id, opp, team, 1 - int(r[5] or 0)))
        finals.add(game_id)
    for game_id, team, opp, home in teams:
        if not con.execute("SELECT 1 FROM nfl_tg WHERE game_id = ? AND team = ?", (game_id, team)).fetchone():
            con.execute("INSERT INTO nfl_tg (game_id, team, opp, home) VALUES (?,?,?,?)", (game_id, team, opp, home))
    for game_id in finals:
        con.execute("UPDATE games SET status = 'final' WHERE game_id = ?", (game_id,))
    con.execute("CREATE TABLE IF NOT EXISTS pnf_build (k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT OR REPLACE INTO pnf_build VALUES (?,?)", [
        ("ds1_cutoff", cutoff), ("espn_rows_added", str(added)), ("espn_games", str(len(finals))),
        ("skipped", repr(skipped))])
    con.commit()
    con.close()
    w.close()
    return {"ds1_cutoff": cutoff, "espn_rows_added": added, "espn_games": len(finals), "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds1", required=True)
    ap.add_argument("--work", required=True, help="nfl_full.db working copy (espn_gamelog / espn_player_ctx)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    print(build(a.ds1, a.work, a.out))


if __name__ == "__main__":
    main()
