"""Historical player-game datasets ds-1 for the model (worker only; bounded, resumable, polite).

Storage: <SPORTS_DATA_DIR>/model/history.db (its own SQLite file - never the canonical store, never an esports file).

NBA  ESPN public site API (the endpoints sports/espn.py wraps): scoreboard by US day -> every final regular-season /
     play-in / playoff game -> summary (box score). Seasons 2023-24, 2024-25, 2025-26 and the current 2026-27.
     Pacing: sports/net.py (site.api.espn.com 1 request / 0.5 s), retries with jittered backoff. Resumable: every day
     and every event is an item in table crawl (pending / done / failed + attempts); a run processes items until its
     time budget and stops - the next run continues. A trimmed copy of each summary (box score, header, pickcenter,
     injuries) is kept zlib-compressed in table raw so the parse can be redone without refetching.
NFL  nflverse-data releases (CC-BY 4.0; FTN-charted fields CC-BY-SA 4.0): stats_player_week_<season>.csv (player
     weekly box), snap_counts_<season>.csv (offense snaps / share, PFR ids), players.csv (gsis <-> ESPN / PFR ids),
     schedules games.csv (kickoff, roof, temp, wind, closing spread / total), play_by_play_<season>.csv.gz (red-zone
     opportunities, team plays and neutral pass rate). Seasons 2022-2026 (2026 = the season in progress). Each file's
     sha256, size and fetch time are recorded in table files.

Point-in-time fields (every row): start_utc (kickoff / tip-off, UTC), observed_at (when we read it) and known_at =
the rule-based time the result was public: NBA start + 4 h, NFL start + 6 h (KNOWN_AFTER_H). Backfilled rows were read
long after the game, so known_at is a conservative reconstruction, never the read time; features only use rows whose
known_at <= forecast_at and whose start < the forecast game's start (featurize.py enforces both).

Participation: NBA played = ESPN minutes > 0 and not didNotPlay; a listed player with didNotPlay keeps his reason
(dnp_reason) - he did not play, which is NOT the same as an unknown. NFL: a player absent from the weekly stats file
did not record a stat; with snap counts we know whether he took offensive snaps (offense_snaps), otherwise unknown.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import sqlite3
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config, net
from ..contracts import now_utc
from ..identity import eastern_offset_hours

KNOWN_AFTER_H = {"nba": 4, "nfl": 6}
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"

# NBA seasons (ESPN season year = the year the season ends). Day ranges cover regular season through the finals.
NBA_SEASONS = {2024: ("2023-10-24", "2024-06-20"), 2025: ("2024-10-22", "2025-06-25"),
               2026: ("2025-10-21", "2026-06-30"), 2027: ("2026-10-20", None)}      # None = through yesterday
NBA_SEASON_TYPES = {2: "regular", 3: "postseason", 5: "play_in"}
NFL_SEASONS = (2022, 2023, 2024, 2025, 2026)

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS crawl (league TEXT NOT NULL, item TEXT NOT NULL, state TEXT NOT NULL,
         attempts INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT, PRIMARY KEY (league, item))""",
    """CREATE TABLE IF NOT EXISTS raw (source TEXT NOT NULL, key TEXT NOT NULL, fetched_at TEXT NOT NULL,
         sha256 TEXT NOT NULL, body BLOB NOT NULL, PRIMARY KEY (source, key))""",
    """CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, url TEXT, fetched_at TEXT, sha256 TEXT, bytes INTEGER,
         rows INTEGER)""",
    """CREATE TABLE IF NOT EXISTS games (league TEXT NOT NULL, game_id TEXT NOT NULL, season INTEGER, season_type TEXT,
         week INTEGER, start_utc TEXT, home TEXT, away TEXT, home_score REAL, away_score REAL, status TEXT,
         neutral INTEGER, venue TEXT, spread_home REAL, total_line REAL, odds_source TEXT, roof TEXT, temp REAL,
         wind REAL, espn_id TEXT, source TEXT, observed_at TEXT, known_at TEXT, PRIMARY KEY (league, game_id))""",
    """CREATE TABLE IF NOT EXISTS nba_pg (game_id TEXT NOT NULL, player_id TEXT NOT NULL, name TEXT, team TEXT,
         opp TEXT, home INTEGER, starter INTEGER, played INTEGER, dnp_reason TEXT, ejected INTEGER, position TEXT,
         min REAL, pts REAL, fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL, oreb REAL, dreb REAL,
         reb REAL, ast REAL, stl REAL, blk REAL, tov REAL, pf REAL, pm REAL, PRIMARY KEY (game_id, player_id))""",
    "CREATE INDEX IF NOT EXISTS nba_pg_player ON nba_pg (player_id)",
    """CREATE TABLE IF NOT EXISTS nba_tg (game_id TEXT NOT NULL, team TEXT NOT NULL, opp TEXT, home INTEGER,
         pts REAL, fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL, oreb REAL, dreb REAL, reb REAL,
         ast REAL, tov REAL, player_min REAL, PRIMARY KEY (game_id, team))""",
    """CREATE TABLE IF NOT EXISTS nba_absent (game_id TEXT NOT NULL, team TEXT NOT NULL, player_id TEXT NOT NULL,
         status TEXT, PRIMARY KEY (game_id, team, player_id))""",
    """CREATE TABLE IF NOT EXISTS nfl_pg (game_id TEXT NOT NULL, player_id TEXT NOT NULL, espn_id TEXT, pfr_id TEXT,
         name TEXT, position TEXT, position_group TEXT, team TEXT, opp TEXT, home INTEGER, season INTEGER,
         week INTEGER, season_type TEXT, pass_att REAL, pass_cmp REAL, pass_yds REAL, pass_td REAL, pass_int REAL,
         sacks REAL, pass_air_yds REAL, pass_epa REAL, carries REAL, rush_yds REAL, rush_td REAL, rush_epa REAL,
         targets REAL, rec REAL, rec_yds REAL, rec_td REAL, rec_air_yds REAL, rec_epa REAL, target_share REAL,
         air_yards_share REAL, wopr REAL, offense_snaps REAL, offense_pct REAL, rz_targets REAL, rz_carries REAL,
         i10_targets REAL, i10_carries REAL, PRIMARY KEY (game_id, player_id))""",
    "CREATE INDEX IF NOT EXISTS nfl_pg_player ON nfl_pg (player_id)",
    "CREATE TABLE IF NOT EXISTS nfl_ids (gsis_id TEXT PRIMARY KEY, espn_id TEXT, pfr_id TEXT)",
    """CREATE TABLE IF NOT EXISTS nfl_tg (game_id TEXT NOT NULL, team TEXT NOT NULL, opp TEXT, home INTEGER,
         plays REAL, pass_plays REAL, rush_plays REAL, neutral_pass REAL, neutral_plays REAL, pass_att REAL,
         carries REAL, targets REAL, PRIMARY KEY (game_id, team))""",
    """CREATE TABLE IF NOT EXISTS nfl_snaps (game_id TEXT NOT NULL, pfr_id TEXT NOT NULL, team TEXT, position TEXT,
         offense_snaps REAL, offense_pct REAL, PRIMARY KEY (game_id, pfr_id))""",
    # ds-2 extras (factor round 2, additive: sm-1 never reads them) - from the same nflverse play-by-play pass
    """CREATE TABLE IF NOT EXISTS nfl_pgx (game_id TEXT NOT NULL, player_id TEXT NOT NULL, dropbacks REAL,
         scrambles REAL, PRIMARY KEY (game_id, player_id))""",
    """CREATE TABLE IF NOT EXISTS nfl_tgx (game_id TEXT NOT NULL, team TEXT NOT NULL, epa_sum REAL, epa_n REAL,
         success REAL, explosive REAL, rz_looks REAL, neutral_secs REAL, neutral_gaps REAL,
         PRIMARY KEY (game_id, team))""",
]


def db_path():
    return config.DATA_DIR / "model" / "history.db"


def connect(path=None, read_only=False):
    p = Path(path) if path else db_path()
    if read_only:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        for s in SCHEMA:
            con.execute(s)
        con.commit()
    con.row_factory = sqlite3.Row
    return con


def _known(start_utc, league):
    if not start_utc:
        return None
    t = datetime.fromisoformat(start_utc.replace("Z", "+00:00")) + timedelta(hours=KNOWN_AFTER_H[league])
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(x):
    try:
        if x is None or x == "" or x == "NA":
            return None
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pair(s):
    if isinstance(s, str) and "-" in s:
        a, _, b = s.partition("-")
        return _num(a), _num(b)
    return None, None


def _utc_espn(s):
    """ESPN '2024-01-15T18:00Z' -> '2024-01-15T18:00:00Z'."""
    if not s:
        return None
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================================================== NBA
def _crawl_set(con, league, item, state, error=None):
    con.execute("INSERT INTO crawl (league, item, state, attempts, error, updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(league, item) DO UPDATE SET state=excluded.state, error=excluded.error, "
                "updated_at=excluded.updated_at, attempts=crawl.attempts + (CASE WHEN excluded.state='failed' "
                "THEN 1 ELSE 0 END)", (league, item, state, 1 if state == "failed" else 0, error, now_utc()))


def nba_seed_days(con, today=None):
    """Queue every day of every NBA season (idempotent)."""
    today = today or datetime.now(timezone.utc).date()
    n = 0
    for season, (a, b) in NBA_SEASONS.items():
        d0 = date.fromisoformat(a)
        d1 = date.fromisoformat(b) if b else today - timedelta(days=1)
        d1 = min(d1, today - timedelta(days=1))
        d = d0
        while d <= d1:
            cur = con.execute("INSERT OR IGNORE INTO crawl (league, item, state, updated_at) VALUES ('nba', ?, "
                              "'pending', ?)", (f"day:{d:%Y%m%d}", now_utc()))
            n += cur.rowcount
            d += timedelta(days=1)
    con.commit()
    return n


def _nba_day(con, day_code, observed_at):
    body = net.get_json(f"{ESPN_SITE}/basketball/nba/scoreboard", params={"dates": day_code, "limit": 500})
    added = 0
    for e in body.get("events") or []:
        st = (e.get("season") or {}).get("type")
        if st not in NBA_SEASON_TYPES:
            continue
        c = (e.get("competitions") or [{}])[0]
        side = {x.get("homeAway"): x for x in c.get("competitors") or []}
        h, a = side.get("home") or {}, side.get("away") or {}
        hid, aid = (h.get("team") or {}).get("id"), (a.get("team") or {}).get("id")
        if not hid or not aid or int(hid) > 30 or int(aid) > 30:      # All-Star / exhibition teams
            continue
        status = ((c.get("status") or {}).get("type") or {})
        start = _utc_espn(e.get("date"))
        gid = f"nba:espn:{e['id']}"
        con.execute("INSERT OR REPLACE INTO games (league, game_id, season, season_type, week, start_utc, home, away, "
                    "home_score, away_score, status, neutral, venue, espn_id, source, observed_at, known_at) "
                    "VALUES ('nba',?,?,?,NULL,?,?,?,?,?,?,?,?,?,'espn',?,?)",
                    (gid, (e.get("season") or {}).get("year"), NBA_SEASON_TYPES[st], start,
                     (h.get("team") or {}).get("abbreviation"), (a.get("team") or {}).get("abbreviation"),
                     _num(h.get("score")), _num(a.get("score")),
                     "final" if status.get("completed") else (status.get("name") or "unknown"),
                     1 if c.get("neutralSite") else 0, ((c.get("venue") or {}).get("id")), e["id"], observed_at,
                     _known(start, "nba")))
        if status.get("completed"):
            cur = con.execute("INSERT OR IGNORE INTO crawl (league, item, state, updated_at) VALUES ('nba', ?, "
                              "'pending', ?)", (f"event:{e['id']}", now_utc()))
            added += cur.rowcount
    return added


def _trim_summary(b):
    comp = ((b.get("header") or {}).get("competitions") or [{}])[0]
    return {"boxscore": b.get("boxscore"), "header": {"id": (b.get("header") or {}).get("id"),
                                                      "season": (b.get("header") or {}).get("season"),
                                                      "competition": {k: comp.get(k) for k in ("date", "neutralSite",
                                                                                               "status", "competitors")}},
            "pickcenter": b.get("pickcenter"), "injuries": b.get("injuries")}


NBA_COLS = {"minutes": "min", "points": "pts", "rebounds": "reb", "assists": "ast", "turnovers": "tov",
            "steals": "stl", "blocks": "blk", "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "fouls": "pf",
            "plusMinus": "pm"}
NBA_PAIRS = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
             "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
             "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta")}
NBA_FIELDS = ("min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk",
              "tov", "pf", "pm")


def parse_nba_summary(t, gid):
    """trimmed summary -> (player rows, team rows, absent rows, odds) - pure, used by the crawler and the tests."""
    box = t.get("boxscore") or {}
    comps = (t.get("header") or {}).get("competition", {}).get("competitors") or []
    home_abbr = next(((c.get("team") or {}).get("abbreviation") for c in comps if c.get("homeAway") == "home"), None)
    teams = [(tm.get("team") or {}).get("abbreviation") for tm in box.get("players") or []]
    players, team_rows, absent = [], [], []
    for tm in box.get("players") or []:
        abbr = (tm.get("team") or {}).get("abbreviation")
        opp = next((x for x in teams if x != abbr), None)
        tot = {k: 0.0 for k in NBA_FIELDS}
        for grp in tm.get("statistics") or []:
            keys = grp.get("keys") or []
            for a in grp.get("athletes") or []:
                ath = a.get("athlete") or {}
                if not ath.get("id"):
                    continue
                vals = {k: None for k in NBA_FIELDS}
                for k, s in zip(keys, a.get("stats") or []):
                    if k in NBA_PAIRS:
                        m, at = _pair(s)
                        vals[NBA_PAIRS[k][0]], vals[NBA_PAIRS[k][1]] = m, at
                    elif k in NBA_COLS:
                        vals[NBA_COLS[k]] = _num(s)
                dnp = bool(a.get("didNotPlay")) or not a.get("stats")
                played = 0 if dnp else (1 if (vals["min"] or 0) > 0 else 0)
                if not played:
                    vals = {k: (0.0 if k != "pm" else None) for k in NBA_FIELDS}
                    absent.append((gid, abbr, f"nba:player:espn:{ath['id']}", (a.get("reason") or "DNP")[:60]))
                for k in NBA_FIELDS:
                    if k != "pm" and vals[k] is not None:
                        tot[k] += vals[k]
                players.append({"game_id": gid, "player_id": f"nba:player:espn:{ath['id']}",
                                "name": ath.get("displayName"), "team": abbr, "opp": opp,
                                "home": 1 if abbr == home_abbr else 0, "starter": 1 if a.get("starter") else 0,
                                "played": played, "dnp_reason": None if played else (a.get("reason") or "DNP")[:60],
                                "ejected": 1 if a.get("ejected") else 0,
                                "position": ((ath.get("position") or {}).get("abbreviation")), **vals})
        team_rows.append({"game_id": gid, "team": abbr, "opp": opp, "home": 1 if abbr == home_abbr else 0,
                          **{k: tot[k] for k in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
                                                 "reb", "ast", "tov")}, "player_min": tot["min"]})
    odds = None
    for pc in t.get("pickcenter") or []:
        if pc.get("spread") is not None or pc.get("overUnder") is not None:
            # ESPN's pickcenter "spread" is from the HOME team's side (negative = home favoured); the provider and
            # the time the line was taken are not stated (assumed near closing) - see the feature registry
            odds = {"spread_home": _num(pc.get("spread")), "total_line": _num(pc.get("overUnder")),
                    "odds_source": f"espn_pickcenter:{(pc.get('provider') or {}).get('name')}"}
            break
    return players, team_rows, absent, odds


def _nba_event(con, espn_id, observed_at):
    body = net.get_json(f"{ESPN_SITE}/basketball/nba/summary", params={"event": espn_id})
    t = _trim_summary(body)
    raw = json.dumps(t, separators=(",", ":"), sort_keys=True).encode("utf-8")
    con.execute("INSERT OR REPLACE INTO raw (source, key, fetched_at, sha256, body) VALUES ('espn:nba:summary',?,?,?,?)",
                (espn_id, observed_at, hashlib.sha256(raw).hexdigest(), zlib.compress(raw, 6)))
    gid = f"nba:espn:{espn_id}"
    store_nba_parse(con, gid, *parse_nba_summary(t, gid))
    return True


def store_nba_parse(con, gid, players, team_rows, absent, odds):
    con.execute("DELETE FROM nba_pg WHERE game_id = ?", (gid,))     # re-parse of the same saved document
    con.execute("DELETE FROM nba_tg WHERE game_id = ?", (gid,))
    con.execute("DELETE FROM nba_absent WHERE game_id = ?", (gid,))
    cols = ("game_id", "player_id", "name", "team", "opp", "home", "starter", "played", "dnp_reason", "ejected",
            "position") + NBA_FIELDS
    con.executemany(f"INSERT OR REPLACE INTO nba_pg ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(p[c] for c in cols) for p in players])
    tc = ("game_id", "team", "opp", "home", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
          "ast", "tov", "player_min")
    con.executemany(f"INSERT OR REPLACE INTO nba_tg ({','.join(tc)}) VALUES ({','.join('?' * len(tc))})",
                    [tuple(r[c] for c in tc) for r in team_rows])
    con.executemany("INSERT OR REPLACE INTO nba_absent VALUES (?,?,?,?)", absent)
    if odds:
        con.execute("UPDATE games SET spread_home=?, total_line=?, odds_source=? WHERE league='nba' AND game_id=?",
                    (odds["spread_home"], odds["total_line"], odds["odds_source"], gid))


def nba_crawl(con, budget_s=270, max_attempts=4, log=None):
    """Process pending crawl items (days first, then events) until the time budget. -> counts. Resumable."""
    t0 = time.monotonic()
    out = {"days": 0, "events": 0, "failed": 0, "new_events": 0}
    while time.monotonic() - t0 < budget_s:
        row = con.execute("SELECT item FROM crawl WHERE league='nba' AND (state='pending' OR (state='failed' AND "
                          "attempts < ?)) ORDER BY CASE WHEN item LIKE 'day:%' THEN 0 ELSE 1 END, item LIMIT 1",
                          (max_attempts,)).fetchone()
        if not row:
            break
        item = row["item"]
        observed_at = now_utc()
        try:
            if item.startswith("day:"):
                out["new_events"] += _nba_day(con, item[4:], observed_at)
                out["days"] += 1
            else:
                _nba_event(con, item[6:], observed_at)
                out["events"] += 1
            _crawl_set(con, "nba", item, "done")
        except (net.HttpError, ValueError, KeyError, TypeError) as e:
            _crawl_set(con, "nba", item, "failed", f"{type(e).__name__}: {str(e)[:200]}")
            out["failed"] += 1
        con.commit()
        if log and (out["days"] + out["events"]) % 100 == 0:
            log(out)
    out["pending"] = con.execute("SELECT COUNT(*) FROM crawl WHERE league='nba' AND state!='done'").fetchone()[0]
    return out


# ============================================================================================================== NFL
FILE_MAX_AGE_H = 20          # a downloaded release file is reused for this long (nflverse rebuilds nightly)


def _write_atomic(local, data, gz=False):
    """Write a downloaded file so a killed / failed write never leaves a fresh-looking partial file that is reused for
    FILE_MAX_AGE_H (bug sweep 2026-11-06 #41): a temp file in the same folder (a gzip file must decompress to its
    end-of-stream marker), then os.replace'd into place. A failure raises and keeps the old file (if any) untouched."""
    import os
    tmp = local.with_name(f".{local.name}.{os.getpid()}.part")
    try:
        tmp.write_bytes(data)
        if gz:
            with gzip.open(tmp, "rb") as f:          # EOFError / BadGzipFile on a truncated download
                while f.read(1 << 20):
                    pass
        os.replace(tmp, local)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _download(con, name, url, gz=False):
    """GET one nflverse release file -> a text stream (decompressed on the fly). Records sha256 / size in table files.
    The raw file is kept under <SPORTS_DATA_DIR>/model/files/ and reused while younger than FILE_MAX_AGE_H (no repeated
    downloads). Read from disk as a stream, never decoded whole: one play-by-play season is ~19 MB gzipped but several
    hundred MB as one Python string - past the Starter worker's memory (OOM-killed at a 340 MB cgroup)."""
    local = db_path().parent / "files" / name
    if not (local.exists() and time.time() - local.stat().st_mtime < FILE_MAX_AGE_H * 3600):
        data = net.get(url, timeout=180).content
        local.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(local, data, gz=gz)
        del data
    h, size = hashlib.sha256(), 0
    with local.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    con.execute("INSERT OR REPLACE INTO files (name, url, fetched_at, sha256, bytes, rows) VALUES (?,?,?,?,?,NULL)",
                (name, url, now_utc(), h.hexdigest(), size))
    con.commit()
    return io.TextIOWrapper(gzip.open(local, "rb") if gz else local.open("rb"), encoding="utf-8", errors="replace",
                            newline="")


def _rows(text):
    """text: the whole file as a str (tests) or a text stream (_download)."""
    return csv.DictReader(io.StringIO(text) if isinstance(text, str) else text)


def nfl_start_utc(gameday, gametime):
    """nflverse gameday (YYYY-MM-DD) + gametime (HH:MM, US Eastern) -> UTC (DST-exact)."""
    if not gameday:
        return None
    hh, mm = (gametime or "13:00").split(":")[:2]
    local = datetime.fromisoformat(f"{gameday}T{int(hh):02d}:{int(mm):02d}:00").replace(tzinfo=timezone.utc)
    off = eastern_offset_hours(local + timedelta(hours=5))
    return (local - timedelta(hours=off)).strftime("%Y-%m-%dT%H:%M:%SZ")


def nfl_files(season):
    """The nflverse release files nfl_build reads for one season: (name, url, gzipped, required)."""
    return [("games.csv", f"{NFLVERSE}/schedules/games.csv", False, True),
            ("players.csv", f"{NFLVERSE}/players/players.csv", False, True),
            (f"stats_player_week_{season}.csv", f"{NFLVERSE}/stats_player/stats_player_week_{season}.csv", False, True),
            (f"snap_counts_{season}.csv", f"{NFLVERSE}/snap_counts/snap_counts_{season}.csv", False, False),
            (f"play_by_play_{season}.csv.gz", f"{NFLVERSE}/pbp/play_by_play_{season}.csv.gz", True, False)]


def nfl_prefetch(con, season):
    """Download (or reuse the cached copy of) every file nfl_build reads for `season` BEFORE any table changes, so a
    source failure leaves the history as it was. Snap counts / play-by-play may be unavailable (nfl_build skips them)."""
    out = {}
    for name, url, gz, required in nfl_files(season):
        try:
            _download(con, name, url, gz).close()
            out[name] = "ok"
        except net.HttpError as e:
            if required:
                raise
            out[name] = f"unavailable: {e}"
    return out


def nfl_build(con, seasons=NFL_SEASONS, files=None, log=None):
    """Download + load the NFL tables. `files` (name -> text) replaces downloads in tests. Idempotent per season."""
    files = files or {}

    def get(name, url, gz=False):
        return files[name] if name in files else _download(con, name, url, gz)
    out = {}
    games_txt = get("games.csv", f"{NFLVERSE}/schedules/games.csv")
    observed_at = now_utc()
    gmap = {}
    for r in _rows(games_txt):
        try:
            season = int(r["season"])
        except (TypeError, ValueError):
            continue
        if season not in seasons:
            continue
        start = nfl_start_utc(r.get("gameday"), r.get("gametime"))
        st = {"REG": "regular"}.get(r.get("game_type"), "postseason")
        done = r.get("home_score") not in ("", "NA", None)
        sp = _num(r.get("spread_line"))
        # nflverse spread_line is from the HOME team's view with POSITIVE = home favoured; stored as spread_home with
        # the ESPN sign (negative = home favoured) so both leagues read alike
        con.execute("INSERT OR REPLACE INTO games (league, game_id, season, season_type, week, start_utc, home, away, "
                    "home_score, away_score, status, neutral, venue, spread_home, total_line, odds_source, roof, temp, "
                    "wind, espn_id, source, observed_at, known_at) VALUES ('nfl',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "'nflverse',?,?)",
                    (r["game_id"], season, st, int(r["week"]), start, r["home_team"], r["away_team"],
                     _num(r.get("home_score")), _num(r.get("away_score")), "final" if done else "scheduled",
                     1 if r.get("location") == "Neutral" else 0, r.get("stadium_id"), -sp if sp is not None else None,
                     _num(r.get("total_line")), "nflverse_schedule_closing" if sp is not None else None,
                     r.get("roof"), _num(r.get("temp")), _num(r.get("wind")), r.get("espn") or None, observed_at,
                     _known(start, "nfl")))
        gmap[r["game_id"]] = (r["home_team"], r["away_team"])
    con.commit()
    del games_txt
    ptxt = get("players.csv", f"{NFLVERSE}/players/players.csv")
    ids = {}
    for r in _rows(ptxt):
        ids[r["gsis_id"]] = (r.get("espn_id") or None, r.get("pfr_id") or None)
    del ptxt
    pfr_to_gsis = {v[1]: k for k, v in ids.items() if v[1]}
    con.executemany("INSERT OR REPLACE INTO nfl_ids (gsis_id, espn_id, pfr_id) VALUES (?,?,?)",
                    [(k, v[0], v[1]) for k, v in ids.items()])
    con.commit()
    for season in seasons:
        n = 0
        stxt = get(f"stats_player_week_{season}.csv", f"{NFLVERSE}/stats_player/stats_player_week_{season}.csv")
        con.execute("DELETE FROM nfl_pg WHERE season = ?", (season,))
        batch = []
        for r in _rows(stxt):
            gid = r.get("game_id")
            if not gid or gid not in gmap:
                continue
            home = 1 if r.get("team") == gmap[gid][0] else 0
            esp, pfr = ids.get(r["player_id"], (None, None))
            batch.append((gid, r["player_id"], esp, pfr, r.get("player_display_name"), r.get("position"),
                          r.get("position_group"), r.get("team"), r.get("opponent_team"), home, season,
                          int(r["week"]), {"REG": "regular"}.get(r.get("season_type"), "postseason"),
                          _num(r.get("attempts")), _num(r.get("completions")), _num(r.get("passing_yards")),
                          _num(r.get("passing_tds")), _num(r.get("passing_interceptions")),
                          _num(r.get("sacks_suffered")), _num(r.get("passing_air_yards")), _num(r.get("passing_epa")),
                          _num(r.get("carries")), _num(r.get("rushing_yards")), _num(r.get("rushing_tds")),
                          _num(r.get("rushing_epa")), _num(r.get("targets")), _num(r.get("receptions")),
                          _num(r.get("receiving_yards")), _num(r.get("receiving_tds")),
                          _num(r.get("receiving_air_yards")), _num(r.get("receiving_epa")),
                          _num(r.get("target_share")), _num(r.get("air_yards_share")), _num(r.get("wopr"))))
            n += 1
        con.executemany("INSERT OR REPLACE INTO nfl_pg (game_id, player_id, espn_id, pfr_id, name, position, "
                        "position_group, team, opp, home, season, week, season_type, pass_att, pass_cmp, pass_yds, "
                        "pass_td, pass_int, sacks, pass_air_yds, pass_epa, carries, rush_yds, rush_td, rush_epa, "
                        "targets, rec, rec_yds, rec_td, rec_air_yds, rec_epa, target_share, air_yards_share, wopr) "
                        "VALUES (" + ",".join("?" * 34) + ")", batch)
        con.execute("UPDATE files SET rows=? WHERE name=?", (n, f"stats_player_week_{season}.csv"))
        con.commit()
        out[f"stats_{season}"] = n
        del stxt, batch
        # snap counts (PFR ids -> gsis through players.csv)
        try:
            sntxt = get(f"snap_counts_{season}.csv", f"{NFLVERSE}/snap_counts/snap_counts_{season}.csv")
        except net.HttpError as e:
            out[f"snaps_{season}"] = f"unavailable: {e}"
            sntxt = None
        if sntxt:
            sb = []
            for r in _rows(sntxt):
                if r.get("game_id") in gmap and r.get("pfr_player_id"):
                    sb.append((r["game_id"], r["pfr_player_id"], r.get("team"), r.get("position"),
                               _num(r.get("offense_snaps")), _num(r.get("offense_pct"))))
            con.executemany("INSERT OR REPLACE INTO nfl_snaps VALUES (?,?,?,?,?,?)", sb)
            con.execute("UPDATE nfl_pg SET offense_snaps = (SELECT s.offense_snaps FROM nfl_snaps s WHERE "
                        "s.game_id = nfl_pg.game_id AND s.pfr_id = nfl_pg.pfr_id), offense_pct = (SELECT "
                        "s.offense_pct FROM nfl_snaps s WHERE s.game_id = nfl_pg.game_id AND s.pfr_id = nfl_pg.pfr_id) "
                        "WHERE season = ?", (season,))
            # participation: a QB / RB / WR / TE with offensive snaps but no stats row PLAYED and recorded nothing ->
            # an explicit zero row (without it every 0-target game would be missing and Overs would look likelier)
            zero = []
            have = {(r[0], r[1]) for r in con.execute("SELECT game_id, player_id FROM nfl_pg WHERE season = ?",
                                                       (season,))}
            for gid, pfr, team, pos, snaps, pct in sb:
                g = pfr_to_gsis.get(pfr)
                if not g or not snaps or pos not in ("QB", "RB", "WR", "TE", "FB") or (gid, g) in have:
                    continue
                h, a = gmap[gid]
                wk = int(gid.split("_")[1])
                zero.append((gid, g, ids[g][0], pfr, None, pos, pos if pos != "FB" else "RB", team,
                             a if team == h else h, 1 if team == h else 0, season, wk,
                             "regular" if wk <= 18 else "postseason", snaps, pct))
            con.executemany("INSERT OR REPLACE INTO nfl_pg (game_id, player_id, espn_id, pfr_id, name, position, "
                            "position_group, team, opp, home, season, week, season_type, offense_snaps, offense_pct, "
                            "pass_att, pass_cmp, pass_yds, pass_td, pass_int, sacks, carries, rush_yds, rush_td, "
                            "targets, rec, rec_yds, rec_td) VALUES (" + ",".join("?" * 15) + ",0,0,0,0,0,0,0,0,0,0,0,0,0)",
                            zero)
            con.commit()
            out[f"snaps_{season}"] = len(sb)
            out[f"zero_rows_{season}"] = len(zero)
            del sntxt, sb
        # play-by-play: red-zone opportunities per player, team plays and neutral pass rate
        try:
            ptxt2 = get(f"play_by_play_{season}.csv.gz", f"{NFLVERSE}/pbp/play_by_play_{season}.csv.gz", gz=True)
        except net.HttpError as e:
            out[f"pbp_{season}"] = f"unavailable: {e}"
            continue
        out[f"pbp_{season}"] = _load_pbp(con, ptxt2, gmap)
        del ptxt2
        if log:
            log({"season": season, **{k: v for k, v in out.items() if k.endswith(str(season))}})
    return out


def _load_pbp(con, text, gmap):
    rz, tg = {}, {}
    xp, xt, last = {}, {}, {}
    n = 0
    for r in _rows(text):
        gid, pos = r.get("game_id"), r.get("posteam")
        if gid not in gmap or not pos or r.get("two_point_attempt") == "1":
            continue
        pt = r.get("play_type")
        if pt not in ("pass", "run"):
            continue
        n += 1
        _extras(r, gid, pos, pt, xp, xt, last)
        yl = _num(r.get("yardline_100"))
        t = tg.setdefault((gid, pos), {"plays": 0, "pass": 0, "rush": 0, "npass": 0, "nplays": 0})
        t["plays"] += 1
        t["pass" if pt == "pass" else "rush"] += 1
        sd, q, wp = _num(r.get("score_differential")), _num(r.get("qtr")), _num(r.get("wp"))
        if sd is not None and q is not None and wp is not None and abs(sd) <= 7 and q <= 3 and 0.2 <= wp <= 0.8:
            t["nplays"] += 1
            t["npass"] += 1 if pt == "pass" else 0
        pid = r.get("receiver_player_id") if pt == "pass" else r.get("rusher_player_id")
        if pid and pid != "NA" and yl is not None and yl <= 20:
            d = rz.setdefault((gid, pid), [0, 0, 0, 0])      # rz targets, rz carries, i10 targets, i10 carries
            k = 0 if pt == "pass" else 1
            d[k] += 1
            if yl <= 10:
                d[k + 2] += 1
    # a player in a game with play-by-play but no red-zone look had 0 (known), not unknown
    con.executemany("UPDATE nfl_pg SET rz_targets = 0, rz_carries = 0, i10_targets = 0, i10_carries = 0 "
                    "WHERE game_id = ?", [(g,) for g in sorted({g for g, _ in tg})])
    con.executemany("UPDATE nfl_pg SET rz_targets=?, rz_carries=?, i10_targets=?, i10_carries=? WHERE game_id=? AND "
                    "player_id=?", [(v[0], v[1], v[2], v[3], g, p) for (g, p), v in rz.items()])
    rows = []
    for (gid, team), t in tg.items():
        h, a = gmap[gid]
        rows.append((gid, team, a if team == h else h, 1 if team == h else 0, t["plays"], t["pass"], t["rush"],
                     t["npass"], t["nplays"]))
    con.executemany("INSERT OR REPLACE INTO nfl_tg (game_id, team, opp, home, plays, pass_plays, rush_plays, "
                    "neutral_pass, neutral_plays) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    _write_extras(con, xp, xt)
    con.execute("UPDATE nfl_tg SET pass_att = (SELECT SUM(pass_att) FROM nfl_pg p WHERE p.game_id = nfl_tg.game_id AND "
                "p.team = nfl_tg.team), carries = (SELECT SUM(carries) FROM nfl_pg p WHERE p.game_id = nfl_tg.game_id "
                "AND p.team = nfl_tg.team), targets = (SELECT SUM(targets) FROM nfl_pg p WHERE p.game_id = "
                "nfl_tg.game_id AND p.team = nfl_tg.team)")
    con.commit()
    return n


def _extras(r, gid, pos, pt, xp, xt, last):
    """ds-2 extras accumulated in the same play-by-play pass (factor round 2; sm-1 never reads them):
    player dropbacks / scrambles (QB), team offensive EPA / success / explosive plays (rush 10+, pass 20+), red-zone looks
    (plays inside the 20), neutral-situation seconds between consecutive snaps of one drive (gap 0-60 s)."""
    t = xt.setdefault((gid, pos), [0.0, 0, 0, 0, 0, 0.0, 0])
    epa = _num(r.get("epa"))
    if epa is not None:
        t[0] += epa
        t[1] += 1
    t[2] += 1 if r.get("success") == "1" else 0
    yg = _num(r.get("yards_gained")) or 0
    t[3] += 1 if (pt == "run" and yg >= 10) or (pt == "pass" and yg >= 20) else 0
    yl = _num(r.get("yardline_100"))
    t[4] += 1 if yl is not None and yl <= 20 else 0
    gsr, drive = _num(r.get("game_seconds_remaining")), r.get("drive")
    sd, q, wp = _num(r.get("score_differential")), _num(r.get("qtr")), _num(r.get("wp"))
    neutral = sd is not None and q is not None and wp is not None and abs(sd) <= 7 and q <= 3 and 0.2 <= wp <= 0.8
    prev = last.get((gid, pos))
    if neutral and prev and prev[0] == drive and gsr is not None and prev[1] is not None and 0 < prev[1] - gsr <= 60:
        t[5] += prev[1] - gsr
        t[6] += 1
    last[(gid, pos)] = (drive, gsr)
    if r.get("qb_dropback") == "1":
        pid = r.get("passer_player_id") if r.get("passer_player_id") not in (None, "", "NA") else r.get("rusher_player_id")
        if pid and pid != "NA":
            d = xp.setdefault((gid, pid), [0, 0])
            d[0] += 1
            d[1] += 1 if r.get("qb_scramble") == "1" else 0


def _write_extras(con, xp, xt):
    con.executemany("INSERT OR REPLACE INTO nfl_pgx (game_id, player_id, dropbacks, scrambles) VALUES (?,?,?,?)",
                    [(g, p, v[0], v[1]) for (g, p), v in xp.items()])
    con.executemany("INSERT OR REPLACE INTO nfl_tgx (game_id, team, epa_sum, epa_n, success, explosive, rz_looks, "
                    "neutral_secs, neutral_gaps) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(g, t, *v) for (g, t), v in xt.items()])


def nfl_extras_from_files(con, files_dir, seasons=NFL_SEASONS):
    """(Re)build ONLY the ds-2 extras tables from cached nflverse play-by-play files (no download, nothing else
    changes). -> {season: plays}."""
    import gzip as _gz
    gmap = {r["game_id"]: (r["home"], r["away"]) for r in con.execute(
        "SELECT game_id, home, away FROM games WHERE league = 'nfl'")}
    out = {}
    for season in seasons:
        f = Path(files_dir) / f"play_by_play_{season}.csv.gz"
        if not f.exists():
            out[season] = "missing"
            continue
        xp, xt, last = {}, {}, {}
        n = 0
        with io.TextIOWrapper(_gz.open(f, "rb"), encoding="utf-8", errors="replace", newline="") as fh:
            for r in _rows(fh):
                gid, pos = r.get("game_id"), r.get("posteam")
                if gid not in gmap or not pos or r.get("two_point_attempt") == "1":
                    continue
                pt = r.get("play_type")
                if pt not in ("pass", "run"):
                    continue
                n += 1
                _extras(r, gid, pos, pt, xp, xt, last)
        _write_extras(con, xp, xt)
        con.commit()
        out[season] = n
    return out


# ========================================================================================================= coverage
def coverage(con):
    """Counts per league / season / season type, crawl state, files - the dataset's coverage record."""
    out = {"dataset_version": "ds-1", "known_after_hours": KNOWN_AFTER_H, "generated_at": now_utc(), "nba": {},
           "nfl": {}}
    for r in con.execute("SELECT g.season, g.season_type, COUNT(DISTINCT g.game_id) games, MIN(g.start_utc) first, "
                         "MAX(g.start_utc) last FROM games g WHERE g.league='nba' AND g.status='final' "
                         "GROUP BY g.season, g.season_type ORDER BY 1, 2"):
        k = f"{r['season']}:{r['season_type']}"
        box = con.execute("SELECT COUNT(DISTINCT p.game_id) g, COUNT(*) rows, SUM(p.played) played FROM nba_pg p "
                          "JOIN games g ON g.game_id = p.game_id WHERE g.season=? AND g.season_type=?",
                          (r["season"], r["season_type"])).fetchone()
        out["nba"][k] = {"final_games": r["games"], "games_with_box": box["g"], "player_rows": box["rows"],
                         "played_rows": box["played"], "first": r["first"], "last": r["last"]}
    out["nba"]["crawl"] = {f"{r['kind']}:{r['state']}": r["n"] for r in con.execute(
        "SELECT substr(item, 1, instr(item, ':') - 1) kind, state, COUNT(*) n FROM crawl WHERE league='nba' "
        "GROUP BY 1, 2")}
    out["nba"]["odds_rows"] = con.execute("SELECT COUNT(*) FROM games WHERE league='nba' AND total_line IS NOT NULL"
                                          ).fetchone()[0]
    for r in con.execute("SELECT season, season_type, COUNT(DISTINCT game_id) games, COUNT(*) rows, "
                         "SUM(offense_snaps IS NOT NULL) snaps, SUM(rz_targets IS NOT NULL) pbp, "
                         "SUM(espn_id IS NOT NULL) espn FROM nfl_pg GROUP BY season, season_type ORDER BY 1, 2"):
        out["nfl"][f"{r['season']}:{r['season_type']}"] = {k: r[k] for k in ("games", "rows", "snaps", "pbp", "espn")}
    out["nfl"]["team_games"] = con.execute("SELECT COUNT(*) FROM nfl_tg").fetchone()[0]
    out["files"] = [dict(r) for r in con.execute("SELECT * FROM files ORDER BY name")]
    return out
