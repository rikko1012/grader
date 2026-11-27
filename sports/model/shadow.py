"""sm-2 challenger in SHADOW (REGISTER.md A5 7): forecasts recorded, never displayed, never graded for members.

Where: inside the existing forward job (sports.model.fwd run -> forward.forecast_league), same scheduler slot
("model-forward"), same process, same feature frame - no extra job, no extra frame. For each market that HAS a frozen
sm-2 artifact (sports/model/artifacts/sm2_<sport>.json, hash-registered in MANIFEST_SM2.json) the recorder adds the
round-2 columns to the frame already in memory (featurize2.augment), computes the sm-2 PMF of every upcoming row that
got an sm-1 forecast, writes one row per target offer into <SPORTS_DATA_DIR>/model/shadow.db, then DROPS the added
columns again (the next market's sm-1 rows, their saved inputs and data_version are byte-identical to a run without
the shadow). Markets without an sm-2 artifact (no round-2 factor passed) cost nothing.

Never displayed: shadow rows are not store records (the member snapshot reads store forecasts only), the shadow table
is read only by the admin learning report (grade bands / scorecard) and the L2 locked-test scorer.
Off switch: SPORTS_SHADOW=0. Memory: one market's added columns (~30 float columns x frame rows) at a time.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np

from .. import config
from . import dists, featurize2, grade_policy

VERSION = "sm-2"
ART_DIR = Path(__file__).resolve().parent / "artifacts"
MANIFEST = ART_DIR / "MANIFEST_SM2.json"
RETENTION_DAYS = 180
_ON = {"1", "true", "yes", "on"}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def enabled():
    return os.environ.get("SPORTS_SHADOW", "1").strip().lower() in _ON


def art_path(sport):
    return ART_DIR / f"sm2_{sport}.json"


def load(sport):
    """The frozen sm-2 artifact if present AND its content sha matches the manifest; else None (shadow skipped)."""
    p = art_path(sport)
    if not p.exists() or not MANIFEST.exists():
        return None
    a = json.loads(p.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sha = _sha(a["markets"])[:16]
    if a.get("sha") != sha or man.get("artifact_shas", {}).get(sport) != sha:
        return None
    return a


# --------------------------------------------------------------------------------------------------------- fit
def fit(sport, hcon, as_of=None, out_dir=None):
    """Refit the frozen sm-2 configuration (model_config2_<sport>.json) on every eligible row known at as_of ->
    sm2_<sport>.json + MANIFEST_SM2.json. Workstation / worker under SPORTS_TRAIN only (like forward.fit)."""
    from ..contracts import now_utc
    from . import datasets, forward, model, study
    out_dir = Path(out_dir) if out_dir else ART_DIR
    cfg_p = out_dir / f"model_config2_{sport}.json"
    if not cfg_p.exists():
        return {"sport": sport, "state": "no sm-2 configuration (no market passed the registered tune test)"}
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    as_of = as_of or now_utc()
    arts, cur_hl, df = {}, None, None
    for mk in sorted(cfg["markets"], key=lambda m: tuple(cfg["markets"][m]["half_lives"])):
        c = cfg["markets"][mk]
        hl = tuple(c["half_lives"])
        if hl != cur_hl:
            df = None
            df = forward._frame_asof(sport, hcon, hl, as_of)
            df = study._nfl_fill(df) if sport == "nfl" else df
            featurize2.augment(df, sport, hcon, hl, as_of=as_of)
            cur_hl = hl
        el = model.eligible(df, mk)
        tr = df[el & np.isfinite(df.f(f"y_{mk}"))]
        f = study.Fitted(mk, hl)
        study.mean_model(f, tr, tr, mk, sport, list(c["features"]))
        study.fit_family_params(f, tr, mk, sport)
        f.family, f.cal = c["family"], tuple(c["cal"])
        conf = f.config()
        conf.pop("mi_hist", None)
        conf["fit_rows"], conf["fit_games"] = int(len(tr)), int(len(np.unique(tr["game_id"])))
        arts[mk] = conf
    art = {"model_version": VERSION, "sport": sport, "fitted_at": now_utc(), "data_as_of": as_of,
           "config_sha": _sha(cfg)[:16], "dataset": datasets.coverage(hcon).get(sport), "markets": arts,
           "role": "challenger (shadow) - REGISTER.md A5; not displayed, not graded for members"}
    art["sha"] = _sha(art["markets"])[:16]
    p = out_dir / f"sm2_{sport}.json"
    p.write_text(json.dumps(art, indent=1, sort_keys=True, default=float), encoding="utf-8")
    man_p = out_dir / "MANIFEST_SM2.json"
    man = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else {"version": "sm2-pkg-1",
                                                                                 "artifact_shas": {}, "files": {}}
    man["artifact_shas"][sport] = art["sha"]
    man.setdefault("freeze_at", {})[sport] = art["fitted_at"]
    from .fwd import _file_sha
    for name in (p.name, cfg_p.name):
        man["files"][name] = {"sha256": _file_sha(out_dir / name), "bytes": (out_dir / name).stat().st_size}
    man["locked_test_L2"] = ("every game starting after freeze_at[sport]; scored once at >= 150 NBA / >= 48 NFL games "
                             "(REGISTER.md A5 8)")
    man_p.write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return {"sport": sport, "sha": art["sha"], "markets": {m: v["features"] for m, v in arts.items()},
            "fitted_at": art["fitted_at"]}


EXTRAS_FILE = "ds2_extras_nfl.json.gz"


def build_extras_package(hcon, out_dir=None):
    """Workstation: the ds-2 NFL play-by-play extras (nfl_pgx / nfl_tgx, every season) as a deterministic gzip JSON
    (sorted rows, mtime 0) shipped beside the sm-2 artifacts - the packaged ds-1 history predates these tables."""
    import gzip
    out_dir = Path(out_dir) if out_dir else ART_DIR
    data = {"nfl_pgx": [list(r) for r in hcon.execute("SELECT game_id, player_id, dropbacks, scrambles FROM nfl_pgx "
                                                         "ORDER BY 1, 2")],
            "nfl_tgx": [list(r) for r in hcon.execute("SELECT game_id, team, epa_sum, epa_n, success, explosive, "
                                                         "rz_looks, neutral_secs, neutral_gaps FROM nfl_tgx ORDER BY 1, 2")]}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    p = out_dir / EXTRAS_FILE
    with open(p, "wb") as fh, gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=fh, mtime=0) as g:
        g.write(raw)
    return {"file": p.name, "rows": {k: len(v) for k, v in data.items()}, "sha256": hashlib.sha256(raw).hexdigest()}


def install_extras(history_path):
    """Forward host: put the packaged extras into the installed history copy once (INSERT OR IGNORE - the refresh of
    the current NFL season writes its own rows from the same play-by-play pass). Verified by the manifest sha."""
    import gzip
    p = ART_DIR / EXTRAS_FILE
    if not p.exists() or not MANIFEST.exists():
        return {"state": "no extras package"}
    raw = gzip.open(p, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if man.get("extras_sha256") != sha:
        return {"state": "refused: extras sha does not match MANIFEST_SM2"}
    from . import datasets
    con = datasets.connect(history_path)                    # creates the additive ds-2 tables if missing
    try:
        row = con.execute("SELECT v FROM meta WHERE k = 'ds2x_installed'").fetchone()
        if row and row[0] == sha:
            return {"state": "present"}
        data = json.loads(raw)
        con.executemany("INSERT OR IGNORE INTO nfl_pgx VALUES (?,?,?,?)", data["nfl_pgx"])
        con.executemany("INSERT OR IGNORE INTO nfl_tgx VALUES (?,?,?,?,?,?,?,?,?)", data["nfl_tgx"])
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('ds2x_installed', ?)", (sha,))
        con.commit()
        return {"state": "installed", "rows": {k: len(v) for k, v in data.items()}}
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------------- storage
def db(path=None):
    p = Path(path) if path else config.DATA_DIR / "model" / "shadow.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS shadow_forecasts (shadow_id TEXT PRIMARY KEY, model_version TEXT,
        artifact_sha TEXT, league TEXT, stat TEXT, event_id TEXT, player_id TEXT, offer_record_id TEXT, platform TEXT,
        line REAL, forecast_at TEXT, event_start TEXT, projection_mean REAL, p_over REAL, p_under REAL, p_push REAL,
        sides TEXT, chosen_side TEXT, p_side REAL, band TEXT, factors_json TEXT, pmf_lo INTEGER, pmf_json TEXT,
        sm1_p_side REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS shadow_event ON shadow_forecasts (event_id)")
    con.execute("""CREATE TABLE IF NOT EXISTS shadow_scorecard (shadow_id TEXT, settlement_id TEXT, model_version TEXT,
        league TEXT, stat TEXT, event_id TEXT, line REAL, side TEXT, p_side REAL, p_push REAL, status TEXT,
        value REAL, settled_at TEXT, PRIMARY KEY (shadow_id, settlement_id))""")
    # additive (bug sweep 2026-11-06 #42): a corrected settlement supersedes the earlier row (learning._score_one rule)
    if "superseded" not in {r[1] for r in con.execute("PRAGMA table_info(shadow_scorecard)")}:
        con.execute("ALTER TABLE shadow_scorecard ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0")
    con.execute("CREATE TABLE IF NOT EXISTS shadow_meta (key TEXT PRIMARY KEY, value TEXT)")
    # per-event progress of settle() (2026-11-08): what the event's scoring read when it last ran in full
    con.execute("CREATE TABLE IF NOT EXISTS shadow_events (event_id TEXT PRIMARY KEY, sig TEXT)")
    con.commit()
    return con


# ------------------------------------------------------------------------------------------------------ recorder
class Recorder:
    """Created once per forecast_league call; market() is called after sm-1 has been written for that market."""

    def __init__(self, league, hcon, forecast_at, absences=None, statuses=None, path=None):
        self.league, self.hcon, self.forecast_at = league, hcon, forecast_at
        self.absences, self.statuses = absences or {}, statuses or {}
        self.art = load(league) if enabled() else None
        self.written, self.skipped = 0, {}
        self.context, self.gdb = {}, None
        self.con = db(path) if self.art else None
        if self.con is not None:
            self.con.execute("DELETE FROM shadow_forecasts WHERE forecast_at < datetime('now', ?)",
                             (f"-{RETENTION_DAYS} days",))
            self.con.commit()          # release the write lock now (a second shadow recorder shares shadow.db)

    @classmethod
    def for_league(cls, scon, hcon, league, forecast_at, absences, up, gdb=None):
        """Recorder with the forward inputs sm-2 needs beyond sm-1's: the players' own injury statuses (logged only),
        NFL teammates Out (sm-1 reads the NBA report only), and the games-store game lines / NWS weather observed
        before forecast_at."""
        statuses, abs2 = _report(scon, hcon, league, forecast_at, up)
        r = cls(league, hcon, forecast_at, absences if league == "nba" else abs2, statuses)
        r.gdb = gdb
        return r

    def active(self, market):
        return self.art is not None and market in self.art["markets"]

    def market(self, market, df, hl, rowpos, items, events):
        """items: [(offer record id, offer, (game_id, player_id), sm-1 chosen-side p)] of this market's sm-1
        forecasts. Adds the round-2 columns, records sm-2, removes the columns again."""
        if not self.active(market) or not items:
            return 0
        from . import study
        from .forward import _row_dict
        from .frame import Frame
        am = self.art["markets"][market]
        new_ev = sorted({o.event_id for _, o, _, _ in items} - set(self.context))
        if new_ev:
            self.context.update(game_context(self.league, [{"event_id": e} for e in new_ev], self.forecast_at,
                                             self.gdb))
            self.context.update({e: {} for e in new_ev if e not in self.context})
        before = set(df.cols)
        try:
            featurize2.augment(df, self.league, self.hcon, hl, as_of=self.forecast_at, absences=self.absences)
            f = study.from_config(am)
            lo, ks = dists.grid(market)
            pm = {}
            n = 0
            for rid, o, gk, sm1_p in items:
                if gk not in pm:
                    row = _row_dict(df, rowpos[gk])
                    _apply_context(row, self.league, self.context.get(o.event_id))
                    one = Frame({k: np.array([np.nan if v is None else v], dtype=float)
                                 if isinstance(v, (int, float)) or v is None else np.array([v], dtype=object)
                                 for k, v in row.items()})
                    p, mu = study.pmf_for(f, one, market, self.league)
                    vals = {}
                    for fid in am["features"]:                  # the value each input had (unknown -> None)
                        v = float(np.asarray(one["F_" + fid], float)[0]) if "F_" + fid in one else None
                        vals[fid] = None if v is None or v != v else v
                    vals["injury_status"] = self.statuses.get(o.player_id)
                    pm[gk] = (p[0], float(mu[0]), vals)
                pmf, mean, vals = pm[gk]
                po, pu, pp = dists.line_probs(pmf, lo, o.line)
                sides = o.offered_sides()
                pol = grade_policy.apply(po, pu, sides, "unvalidated")
                ev = events.get(o.event_id)
                sid = _sha([VERSION, self.art["sha"], rid, self.forecast_at])[:24]
                self.con.execute("INSERT OR IGNORE INTO shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                                 "?,?,?,?)",
                                 (sid, VERSION, self.art["sha"], self.league, market, o.event_id, o.player_id, rid,
                                  o.platform, o.line, self.forecast_at, ev.start_utc if ev else None, mean, po, pu, pp,
                                  ",".join(sides), pol["chosen_side"], pol.get("chosen_p"), pol["band"],
                                  json.dumps(vals, default=float), int(lo),
                                  json.dumps([round(float(x), 12) for x in pmf]), sm1_p))
                n += 1
            self.con.commit()
            self.written += n
            return n
        except Exception as e:                                       # noqa: BLE001 - shadow never breaks sm-1
            self.skipped[market] = f"{type(e).__name__}: {str(e)[:160]}"
            return 0
        finally:
            for c in [c for c in df.cols if c not in before]:          # sm-1 frame back exactly as it was
                del df.cols[c]

    def close(self):
        if self.con is not None:
            self.con.close()
        return {"model_version": VERSION, "artifact": (self.art or {}).get("sha"), "written": self.written,
                "skipped": self.skipped, "enabled": enabled(), "markets": sorted((self.art or {}).get("markets", {}))}


REPORT_MAX_AGE_H = 24


def _tail(x):
    return str(x).rsplit(":", 1)[-1] if x else None


def _report(scon, hcon, league, forecast_at, up):
    """-> ({store player id: own injury status}, NFL {(game_id, team): [gsis ids Out]} | None per team when no fresh
    report). Same freshness rule as forward.injury_absences (a report observed within 24 h before the forecast)."""
    try:                                    # newest sighting (last_seen_at), not the first-observed time (2026-11-09)
        from .forward import listed_injuries
        fresh = [c for c in listed_injuries(scon, league, forecast_at).values()
                 if c is not None and getattr(c, "present", None) == "yes"]
    except Exception:                                                # noqa: BLE001
        return {}, {}
    statuses = {c.subject_id: c.status for c in fresh if c.subject_id}
    if league != "nfl":
        return statuses, {}
    outs = [c.subject_id for c in fresh if c.status == "out"]
    gs_of = {}
    if outs:
        esp = sorted({_tail(x) for x in outs})
        for i in range(0, len(esp), 400):
            part = esp[i:i + 400]
            for r in hcon.execute(f"SELECT i.espn_id, p.team FROM nfl_ids i JOIN nfl_pg p ON p.player_id = i.gsis_id "
                                  f"JOIN games g ON g.league = 'nfl' AND g.game_id = p.game_id WHERE i.espn_id IN "
                                  f"({','.join('?' * len(part))}) ORDER BY g.start_utc", part):
                gs_of[str(r[0])] = r[1]                       # latest team wins (ordered by start)
        gsis = {}
        for i in range(0, len(esp), 400):
            part = esp[i:i + 400]
            for r in hcon.execute(f"SELECT espn_id, gsis_id FROM nfl_ids WHERE espn_id IN ({','.join('?' * len(part))})",
                                  part):
                gsis.setdefault(str(r[0]), []).append(r[1])
    by_team = {}
    for x in outs:
        e = _tail(x)
        if e in gs_of:
            by_team.setdefault(gs_of[e], []).extend(gsis.get(e, []))
    res = {}
    for r in up:
        for team in (r["team"], r["opp"]):
            res[(r["game_id"], team)] = by_team.get(team, []) if fresh else None
    return statuses, res


def game_context(league, up, forecast_at, gdb=None):
    """{store event id: {"spread_home", "total", "wind", "temp", "indoor", "observed_at"}} from the games store
    (ESPN pickcenter + NWS snapshots observed at or before forecast_at). Missing store / snapshot -> absent (unknown)."""
    out = {}
    events = sorted({r["event_id"] for r in up if r and r.get("event_id")})
    if not events:
        return out
    own = gdb is None
    try:
        if own:
            from ..games import store as gstore
            if not os.environ.get("SPORTS_DB_DSN") and not (config.DATA_DIR / "games.db").exists():
                return out
            gdb = gstore.connect()
        for ev in events:
            eid = _tail(ev)
            d = {}
            for kind in ("pickcenter", "weather"):
                r = gdb.one("SELECT data, observed_at FROM sports_game_snapshots WHERE league = ? AND event_id = ? AND "
                            "kind = ? AND observed_at <= ? AND late = 0 ORDER BY id DESC LIMIT 1",
                            (league, eid, kind, forecast_at))
                if not r:
                    continue
                data = json.loads(r["data"])
                if kind == "pickcenter" and data and data[0].get("spread") is not None:
                    d.update(spread_home=float(data[0]["spread"]), total=None if data[0].get("total") is None
                             else float(data[0]["total"]), lines_observed_at=r["observed_at"])
                elif kind == "weather" and isinstance(data, dict):
                    d.update(indoor=bool(data.get("indoor")), wind=data.get("wind_mph"), temp=data.get("temp_f"),
                             weather_observed_at=r["observed_at"])
            if d:
                out[ev] = d
    except Exception:                                                # noqa: BLE001 - unknown, never a guess
        return out
    finally:
        if own and gdb is not None:
            try:
                gdb.close()
            except Exception:                                        # noqa: BLE001
                pass
    return out


def _apply_context(row, league, ctx):
    """Forward values of the game-line / weather inputs on an upcoming row (history rows already carry them)."""
    if not ctx:
        return
    home = row.get("home") == 1
    if ctx.get("spread_home") is not None:
        ts = ctx["spread_home"] if home else -ctx["spread_home"]
        row["spread_home"], row["team_spread"], row["spread_abs"] = ctx["spread_home"], ts, abs(ts)
    if ctx.get("total") is not None:
        row["total_line"] = ctx["total"]
        if row.get("team_spread") is not None:
            it = ctx["total"] / 2 - row["team_spread"] / 2
            row["implied_total"] = it
            row["x_implied_team_total"] = it
    if league == "nfl" and "indoor" in ctx:
        row["dome"] = 1.0 if ctx["indoor"] else 0.0
        if not ctx["indoor"]:
            row["wind"], row["temp"] = ctx.get("wind"), ctx.get("temp")


# ------------------------------------------------------------------------------------------- locked test L2
L2_MIN_GAMES = {"nba": 150, "nfl": 48}


def l2_path(sport):
    return config.DATA_DIR / "model" / f"l2_{sport}.json"


def l2_check(sport, hcon, now=None, score=False):
    """REGISTER.md A5 8: count L2 games (started after the sm-2 freeze, final, box score held). The forward job only
    COUNTS (score=False: bounded, a SQL count) and reports "threshold reached"; the one-time score is run by
    `python -m sports.model.shadow l2 <history.db> <sport>` (score=True, workstation / worker: it builds full feature
    frames, ~300 MB) and writes the result once; never re-scored."""
    from . import datasets
    p = l2_path(sport)
    if p.exists():
        return {"state": "scored", "path": str(p)}
    art = load(sport)
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    freeze = (man.get("freeze_at") or {}).get(sport)
    if art is None or not freeze:
        return {"state": "no sm-2 artifact"}
    tbl = "nba_pg" if sport == "nba" else "nfl_pg"
    cut = now or datasets.now_utc()
    n = hcon.execute(f"SELECT COUNT(DISTINCT g.game_id) FROM games g WHERE g.league = ? AND g.status = 'final' AND "
                     f"g.start_utc > ? AND g.start_utc <= ? AND EXISTS (SELECT 1 FROM {tbl} p WHERE p.game_id = "
                     f"g.game_id)", (sport, freeze, cut)).fetchone()[0]
    if n < L2_MIN_GAMES[sport]:
        return {"state": f"waiting ({n}/{L2_MIN_GAMES[sport]} games after {freeze})", "games": n}
    if not score:
        return {"state": "threshold reached - run the registered one-time L2 score", "games": n}
    res = l2_score(sport, hcon, freeze, art)
    res["games_at_trigger"] = n
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    return {"state": "scored now", "path": str(p)}


def _ref_lines(df, market, rows):
    """Registered pre-game reference line floor(mean of the player's last <= 10 prior values) + 0.5 for `rows` only."""
    from .frame import group_starts
    y = np.asarray(df[f"y_{market}"], float)
    gs = group_starts(df["player_id"])
    out = np.full(len(rows), np.nan)
    for j, i in enumerate(rows):
        prev = y[max(gs[i], i - 10):i]
        prev = prev[np.isfinite(prev)]
        if len(prev):
            out[j] = np.floor(prev.mean()) + 0.5
    return out


def l2_score(sport, hcon, freeze, art):
    """Both frozen artifacts (sm-1 champion, sm-2 challenger) on the eligible rows of games after the freeze, at the
    registered forecast time; criteria (a)-(d) of A5 8 per market. Gate F (e) stays with the shadow scorecard."""
    from . import evaluate, forward, model, study
    sm1 = forward.load_artifact(sport)
    out = {"register": "REGISTER.md A5 8", "freeze_at": freeze, "sm2": art["sha"], "sm1": sm1["sha"], "markets": {}}
    cur_hl, df = None, None
    for mk in sorted(art["markets"], key=lambda m: tuple(art["markets"][m]["half_lives"])):
        a2, a1 = art["markets"][mk], sm1["markets"][mk]
        hl = tuple(a2["half_lives"])
        if hl != cur_hl:
            df = None
            df = forward._frame_asof(sport, hcon, hl, None)          # no lag matrices (memory bound)
            df = study._nfl_fill(df) if sport == "nfl" else df
            featurize2.augment(df, sport, hcon, hl)
            cur_hl = hl
        m = model.eligible(df, mk) & (np.asarray(df["start_utc"]) > freeze) & np.isfinite(df.f(f"y_{mk}"))
        lref = _ref_lines(df, mk, np.flatnonzero(m))
        d = df[m]
        lo, ks = dists.grid(mk)
        y = np.asarray(d[f"y_{mk}"], float)
        games = np.asarray(d["game_id"])
        p2, _ = study.pmf_for(study.from_config(a2), d, mk, sport)
        p1, _ = study.pmf_for(study.from_config(a1), d, mk, sport)
        s2 = evaluate.scores(p2, lo, y, dists.mean_of(p2, ks), lref)
        s1 = evaluate.scores(p1, lo, y, dists.mean_of(p1, ks), lref)
        crps = evaluate.paired(s2, s1, games, None, "crps")
        ll = evaluate.paired(s2, s1, games, None, "logloss_ref")
        cal = evaluate.reliability(s2["p_over_ref"], s2["hit_ref"])
        cov = float(np.mean(s2["cov80"]))
        crit = {"a_crps_ci_below_0": crps["ci_game"][1] is not None and crps["ci_game"][1] < 0,
                "b_ref_logloss_not_worse": ll["diff"] is not None and ll["diff"] <= 0,
                "c_calibration": abs(cal["in_the_large"]) <= 0.02 and cal["ece"] <= 0.03,
                "d_cov80": 0.76 <= cov <= 0.84}
        out["markets"][mk] = {"rows": int(len(d)), "games": int(len(np.unique(games))), "crps_sm2_minus_sm1": crps,
                              "logloss_ref_sm2_minus_sm1": ll, "calibration": {k: cal[k] for k in ("ece", "in_the_large")},
                              "cov80": cov, "criteria": {k: bool(v) for k, v in crit.items()},
                              "history_part_passed": bool(all(crit.values())),
                              "next": "gate F at real offered lines on the shadow scorecard; owner release decides"}
    return out


# ------------------------------------------------------------------------------------------------------ settling
CORRECTION_WINDOW_DAYS = 30            # first scan: settlements learned this far back (learning.on_settled's window)


def _event_ids(con):
    """Seek once per event in shadow_event, rather than scanning every forecast revision."""
    ids = set()
    row = con.execute("SELECT event_id FROM shadow_forecasts WHERE event_id IS NOT NULL "
                      "ORDER BY event_id LIMIT 1").fetchone()
    while row:
        event = row[0]
        ids.add(event)
        row = con.execute("SELECT event_id FROM shadow_forecasts WHERE event_id > ? "
                          "ORDER BY event_id LIMIT 1", (event,)).fetchone()
    return ids


def settle(scon, path=None, now=None, deadline=None, fired=None):
    """Score shadow forecasts whose offer has a settlement (same rule as learning._score_one: the forecast's OWN line
    and chosen side), each under its OWN model_version (sm-2, sm-nfl-2). Idempotent.
    A corrected settlement (new settlement_id) SUPERSEDES the forecast's earlier row (superseded=1) instead of adding a
    second live row, and events are re-read when a settlement was learned since the last pass - also when every
    forecast of the event is already scored (bug sweep 2026-11-06 #42).
    deadline (epoch seconds, settle-post's time budget - stall 2026-11-09): stop between events - and, since 2026-11-20,
    between forecasts (rows so far committed, no mark for that event) - once passed; the events done keep their marks
    and the closing 'settled_known_at' mark is NOT advanced, so the next run re-reads the rest.
    A sweep spans runs (review 2026-11-20, as learning.on_settled): it is complete when the walk wraps back to the
    event it started at (sweep_start); 'settled_known_at' then advances to the newest settlement known when the sweep
    STARTED (sweep_newest) - a settlement learned mid-sweep for an event already passed is re-read by the next sweep.
    A run that stops before it gets back into its resume event keeps that event's cursor (resume_rowid / resume_sig)."""
    import time
    from datetime import timedelta
    from .. import settlement, store
    from ..contracts import now_utc, parse_utc
    con = db(path)
    t_now = now or now_utc()
    mark = (con.execute("SELECT value FROM shadow_meta WHERE key = 'settled_known_at'").fetchone() or [None])[0]
    since = mark or (parse_utc(t_now) - timedelta(days=CORRECTION_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Enumerate the covering event index, not every forecast payload plus a
    # scorecard lookup. The old anti-join scanned the 6.7 GB live table before
    # reaching any deadline/cursor checkpoint. Per-event signatures below skip
    # unchanged completed events; new forecasts and corrections change the signature.
    evs = _event_ids(con)
    newest = since
    # All forecast events are already included, including completed events whose
    # settlements were corrected. records_event covers (kind,event_id,known_at):
    # one reverse seek per event avoids a GROUP BY over every saved settlement.
    for ev in evs:
        row = scon.execute("SELECT known_at FROM records WHERE kind='settlement' AND event_id=? "
                           "ORDER BY known_at DESC LIMIT 1", (ev,)).fetchone()
        if row:
            newest = max(newest, row[0] or newest)
    n = 0
    from .learning import event_sig
    done = dict(con.execute("SELECT event_id, sig FROM shadow_events"))
    meta = dict(con.execute("SELECT key, value FROM shadow_meta"))
    left, cursor, i = 0, None, 0
    evs = sorted(evs)
    order, k = evs, 0
    sweeping = bool(meta.get("resume_event"))
    if sweeping:                                       # 2026-11-20: a stopped run resumes where it stopped (wrapping)
        k = next((j for j, e in enumerate(evs) if e >= meta["resume_event"]), len(evs))
        order = evs[k:] + evs[:k]
    # the sweep this run belongs to: its start event and the newest settlement known when it started
    sweep = (meta.get("sweep_start") or meta["resume_event"]) if sweeping else (order[0] if order else None)
    sweep_newest = (meta.get("sweep_newest") or newest) if sweeping else newest
    todo = len(order)
    if sweeping and order and deadline is not None:    # an unbounded run walks every event
        sk = next((j for j, e in enumerate(evs) if e >= sweep), len(evs)) % len(evs)
        todo = (sk - k) % len(evs) or len(evs)
    for i, ev in enumerate(order[:todo]):
        if deadline is not None and time.time() >= deadline:
            left = todo - i
            break
        # skip an event whose settlements and shadow forecasts are exactly those of its last full pass (a no-op: every
        # forecast it can score already has its row); the mark commits with the event's rows, so a killed run (settle
        # 1,500 s timeouts, 2026-11-08 - the closing 'settled_known_at' mark was never written) keeps its progress
        f_n, f_top, f_tot = con.execute("SELECT COUNT(*), MAX(rowid), SUM(rowid) FROM shadow_forecasts "
                                        "WHERE event_id = ?", (ev,)).fetchone()
        sig = f"{event_sig(scon, ev)}|{f_n}:{f_top}:{f_tot}"
        if done.get(ev) == sig:
            continue
        by_offer = {}
        for r in scon.execute("SELECT source, source_key, payload FROM records WHERE kind='settlement' AND event_id=? "
                              "ORDER BY source_key, revision", (ev,)):
            by_offer.setdefault((r["source"], r["source_key"].rsplit("|", 1)[0]), []).append(store._rec(r))
        if not by_offer:
            continue
        stopped = False
        # inside the event a stopped run left off in, records unchanged since: start at the forecast it stopped at
        start = int(meta["resume_rowid"]) if (i == 0 and ev == meta.get("resume_event") and meta.get("resume_rowid")
                                              and meta.get("resume_sig") == sig) else 0
        for row, sid, rid, line, side, p_side, p_push, lg, st, mv in con.execute(
                "SELECT rowid, shadow_id, offer_record_id, line, chosen_side, p_side, p_push, league, stat, "
                "model_version FROM shadow_forecasts WHERE event_id = ? AND rowid >= ? ORDER BY rowid",
                (ev, start)).fetchall():
            if deadline is not None and time.time() >= deadline:
                stopped = True                             # 2026-11-20: checked per forecast too; no mark for this event
                cursor = None if (fired and fired()) else (row, sig)    # see learning.on_settled
                break
            if side is None:
                continue
            offer = store.get(scon, rid)
            if offer is None:
                continue
            alls = by_offer.get((offer.platform, offer.offer_id), [])
            sts = [x for x in alls if x.side == side] or alls
            if not sts:
                continue
            cur = max(sts, key=lambda x: x.revision)
            if con.execute("SELECT 1 FROM shadow_scorecard WHERE shadow_id = ? AND settlement_id = ?",
                           (sid, cur.settlement_id)).fetchone():
                continue                                   # already scored against its current settlement
            status, _ = settlement.at_line(cur, line, side)
            con.execute("UPDATE shadow_scorecard SET superseded = 1 WHERE shadow_id = ? AND settlement_id != ?",
                        (sid, cur.settlement_id))
            n += con.execute("INSERT OR IGNORE INTO shadow_scorecard (shadow_id, settlement_id, model_version, league, "
                             "stat, event_id, line, side, p_side, p_push, status, value, settled_at, superseded) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                             (sid, cur.settlement_id, mv or VERSION, lg, st, ev, line, side, p_side, p_push, status,
                              cur.source_value, cur.settled_at)).rowcount
        if stopped or (fired and fired()):            # fired: learning's statement backstop interrupted a read
            con.commit()
            left = todo - i
            break
        con.execute("INSERT OR REPLACE INTO shadow_events (event_id, sig) VALUES (?, ?)", (ev, sig))
        con.commit()
    con.execute("DELETE FROM shadow_meta WHERE key IN ('resume_event', 'resume_rowid', 'resume_sig', 'sweep_start', "
                "'sweep_newest')")
    if not left:
        con.execute("INSERT OR REPLACE INTO shadow_meta (key, value) VALUES ('settled_known_at', ?)", (sweep_newest,))
    else:
        stop_ev = order[i]
        con.executemany("INSERT OR REPLACE INTO shadow_meta (key, value) VALUES (?, ?)",
                        (("resume_event", stop_ev), ("sweep_start", sweep), ("sweep_newest", sweep_newest)))
        if not cursor and stop_ev == meta.get("resume_event") and meta.get("resume_rowid"):
            cursor = (meta["resume_rowid"], meta.get("resume_sig"))     # the last run's cursor still holds
        if cursor and cursor[1]:
            con.executemany("INSERT OR REPLACE INTO shadow_meta (key, value) VALUES (?, ?)",
                            (("resume_rowid", str(cursor[0])), ("resume_sig", cursor[1])))
    con.commit()
    con.close()
    return {"shadow_scored": n, **({"partial": True, "events_left": left} if left else {})}


def scorecard_rows(path=None):
    p = Path(path) if path else config.DATA_DIR / "model" / "shadow.db"
    if not p.exists():
        return []
    con = db(p)
    cur = con.execute("SELECT model_version, league, stat, event_id, p_side, p_push, status FROM shadow_scorecard "
                      "WHERE superseded = 0")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    con.close()
    return rows


def status(path=None):
    """Admin view: artifacts, freeze times, forecasts recorded / scored per league x market (never member-facing)."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None
    out = {"model_version": VERSION, "enabled": enabled(), "manifest": man, "display": "never (shadow only)",
           "counts": {}}
    p = Path(path) if path else config.DATA_DIR / "model" / "shadow.db"
    if p.exists():
        con = db(p)
        for lg, st, n, ev in con.execute("SELECT league, stat, COUNT(*), COUNT(DISTINCT event_id) FROM shadow_forecasts "
                                         "GROUP BY 1, 2"):
            out["counts"][f"{lg}:{st}"] = {"forecasts": n, "events": ev}
        for lg, st, n in con.execute("SELECT league, stat, COUNT(*) FROM shadow_scorecard WHERE superseded = 0 "
                                     "GROUP BY 1, 2"):
            out["counts"].setdefault(f"{lg}:{st}", {})["settled"] = n
        con.close()
    return out


def main(argv=None):
    import argparse
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    p = argparse.ArgumentParser(prog="python -m sports.model.shadow")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("l2")
    s.add_argument("history")
    s.add_argument("sports", nargs="*")
    sub.add_parser("status")
    a = p.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
        return
    from . import datasets
    hcon = datasets.connect(a.history, read_only=True)
    for sp in a.sports or ["nba", "nfl"]:
        print(json.dumps({sp: l2_check(sp, hcon, score=True)}, default=str), flush=True)


if __name__ == "__main__":
    main()
