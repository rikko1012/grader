#!/usr/bin/env python3
"""
picklog.py - Phase 10: log every graded line, settle it from BO3.gg after the match, report
calibration on REAL lines (the only test that proves the grades beat PrizePicks).

- log_grade(res): called for each member grade (GRADE state only - SCOUT/NO GRADE are not
  wagers). One row per player + prop + line + line type + match; repeats just count requests.
- settle(b): finished matches get the actual Maps 1-2 total, hit/miss. Forfeits, BO1s and
  matches that never appear are voided.
- set_closing(id, line): you enter PrizePicks' closing line; CLV = how far it moved your way.
- report(): printed % vs actual, record by grade and units, CLV.

Stored in SQLite on the data volume (CS2_DATA_DIR/picks.db).
"""
import json
import os
import sqlite3
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / "picks.db"
_LOCK = threading.Lock()
VOID_AFTER_D = 3          # never found finished after 3 days -> void

COLUMNS = """id INTEGER PRIMARY KEY, key TEXT UNIQUE, logged_at TEXT, requests INTEGER DEFAULT 1,
player TEXT, slug TEXT, player_id INTEGER, team TEXT, opponent TEXT, match_slug TEXT, match_start TEXT,
prop TEXT, line REAL, line_type TEXT, lambda REAL, fair_line REAL, edge REAL,
p_more REAL, p_less REAL, pick TEXT, p_pick REAL, grade TEXT, units REAL, p_win REAL, market TEXT,
pred_maps TEXT, veto_known INTEGER, sample_maps INTEGER, rec_avg REAL, rec_median REAL, rec_hit TEXT,
flags TEXT, opp_def TEXT, status TEXT DEFAULT 'open', actual_maps TEXT, actual REAL, hit INTEGER,
closing_line REAL, clv REAL, settled_at TEXT"""


_SCHEMA_OK = [False]


def _model_version():
    """Short hash of everything that can move a grade (grader, simulation, the inputs pipeline):
    every pick says exactly which version made it."""
    import hashlib
    h = hashlib.sha1()
    # perf.py / perf_live.py / scope.py added 2026-10-09 (the performance model moves grades too)
    # market_odds.py / thunderpick.py added 2026-10-13 (the projection's market input can come from Thunderpick)
    # pick_verdict.py added 2026-10-13 (it sets the logged side, Lean / grade and stake). The alias / matching state is
    # NOT hashed here: it is mapping.revision(), recorded on every forecast (mapping_rev)
    for f in ("grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py",
              "market_odds.py", "thunderpick.py", "pick_verdict.py"):
        try:
            h.update((Path(__file__).resolve().parent / f).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


MODEL_VERSION = _model_version()


def _save_config_snapshot():
    """The full grading settings for this version, kept once per version (frozen-test record)."""
    try:
        from grader import CONFIG
        p = DB.parent / "model_versions.json"
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if MODEL_VERSION not in cur:
            cur[MODEL_VERSION] = {"saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "config": json.loads(json.dumps(CONFIG, default=str))}
            p.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    except Exception:
        pass


def _db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")   # with WAL: safe, and writes don't wait on a full disk sync
    if _SCHEMA_OK[0]:
        return con
    DB.parent.mkdir(parents=True, exist_ok=True)
    con.execute("PRAGMA journal_mode=WAL")     # readers and the writer no longer block each other
    con.execute(f"CREATE TABLE IF NOT EXISTS picks ({COLUMNS})")
    try:                                   # added 2026-09-29: owner's load tests are not real picks
        con.execute("ALTER TABLE picks ADD COLUMN test INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass                               # column already there
    try:                                   # added 2026-10-07: the exact grader that made the pick (frozen-version tests)
        con.execute("ALTER TABLE picks ADD COLUMN model_version TEXT")
    except sqlite3.OperationalError:
        pass
    # 2026-10-07: the forecast history - the picks row is the first published forecast (immutable), every
    # changed forecast before the match is appended here (timestamp, line, side, chance, grade, version, shadows)
    con.execute("""CREATE TABLE IF NOT EXISTS forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, kind TEXT, logged_at TEXT,
                   match_start TEXT, line REAL, pick TEXT, p_pick REAL, p_more REAL, grade TEXT, units REAL, lambda REAL,
                   p_win REAL, rec_hit TEXT, sample_maps INTEGER, model_version TEXT, shadow TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS forecasts_pick ON forecasts (pick_id, id)")
    try:                                   # 2026-10-08: the exact grader inputs of each forecast (zlib JSON) - replayable
        con.execute("ALTER TABLE forecasts ADD COLUMN inputs BLOB")
    except sqlite3.OperationalError:
        pass
    # 2026-10-12 (owner review): the model's own outcome chances before caps / margins (over, under, PUSH, p_more_no_push)
    # and the HLTV dataset revision the forecast was graded on - a later backfill changes inputs without a code change
    # 2026-10-13 (forecast record): the odds policy that selected the market input, its signature (what the input IS -
    # a moved price or another source is a new forecast, an unchanged quote re-polled is not), the alias / matching
    # revision the odds were linked under (content in mapping_revisions) and the deployed commit (RENDER_GIT_COMMIT)
    for col in ("probs_raw", "data_rev", "odds_source", "odds_policy", "mapping_rev", "odds_sig", "git_commit"):
        try:
            con.execute(f"ALTER TABLE forecasts ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    # first_logged_at set = a board row that was overwritten by a later grade (2026-10-07 ~21:00-22:00Z only)
    for col in ("first_logged_at", "regrades"):
        try:
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} {'INTEGER DEFAULT 0' if col == 'regrades' else 'TEXT'}")
        except sqlite3.OperationalError:
            pass
    try:                                   # added 2026-10-06: which maps the line covers
        con.execute("ALTER TABLE picks ADD COLUMN scope TEXT DEFAULT '12'")
    except sqlite3.OperationalError:
        pass
    # 2026-10-12 recap audit: a member opening a board line rewrote the row's test 2 -> 0, so board grades drifted into
    # the member cohort. on_board keeps the board provenance for good: 1 = written by the board (recorded), 2 = inferred
    # for rows from before this column (test 0 with a platform recorded = a board line a member opened), 0 = never.
    try:
        con.execute("ALTER TABLE picks ADD COLUMN on_board INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # added 2026-10-10: platform + provider market id (+ PrizePicks market); 2026-10-13: profile_team = the player's BO3.gg
    # PROFILE team (picks.team is now the team he plays THIS match for - ALLE: team 'ex-Keyd Stars', profile 'UNO MILLE')
    for col in ("book", "market_id", "market_key", "profile_team"):
        try:
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    # 2026-10-13 audit D6: the on_board backfill ran only right after its ALTER - lost for good when that connection never
    # committed (a reader first) or when `book` did not exist yet. It now runs once every column exists, until its marker
    # is stored (same transaction), and only fills 0s it is sure of: test 2 = the board wrote it; test 0 with a platform =
    # a board line a member opened (only board grades carry a platform). A recorded value is never changed.
    con.execute("CREATE TABLE IF NOT EXISTS schema_marks (name TEXT PRIMARY KEY, at TEXT)")
    if not con.execute("SELECT 1 FROM schema_marks WHERE name = 'on_board_backfill'").fetchone():
        con.execute("UPDATE picks SET on_board = CASE WHEN test = 2 THEN 1 ELSE 2 END "
                    "WHERE COALESCE(on_board, 0) = 0 AND (test = 2 OR (test = 0 AND book IS NOT NULL))")
        con.execute("INSERT OR IGNORE INTO schema_marks (name, at) VALUES ('on_board_backfill', ?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    con.commit()                           # the migrations are kept whoever opens the database first
    for col in ("factors", "result"):      # added 2026-10-02: every factor's value at grade time and
        try:                               # what happened (rounds, OT, map wins) - each measurable
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    try:                                   # 2026-10-13: every alias / matching revision a forecast used (append-only)
        import mapping
        mapping.ensure_schema(con)
        con.commit()
    except Exception:
        pass
    _SCHEMA_OK[0] = True
    return con


PRIOR_SMOOTH = {"m0": 0.93, "T": 800.0}     # chosen on TRAIN only (kills MAE grid), 2026-10-09 - shadow, never shown


def smooth_prior_pf(pf, m0=PRIOR_SMOOTH["m0"], T=PRIOR_SMOOTH["T"]):
    """The live perf-2 projection re-run with the challenger prior target m(r) = 1 - (1 - m0) * T / (T + r) instead of
    the flat prior_m, r = the player's time-weighted rounds. Exact: the prior enters rate_all linearly and reaches the
    final rate through the environment blend with weight ke / (env rounds + ke). None when pf is not a perf-2 projection."""
    import perf
    if not pf or not str(pf.get("version") or "").startswith("perf-2") or pf.get("rate") is None or pf.get("history_rounds_weighted") is None:
        return None
    p, r = perf.PARAMS, float(pf["history_rounds_weighted"])
    m_new = 1 - (1 - m0) * T / (T + r)
    d_all = p["k0"] * p["pop"] * (m_new - p["prior_m"]) / (r + p["k0"])
    rate = float(pf["rate"]) + d_all * p["ke"] / (float(pf.get("env_rounds_weighted") or 0.0) + p["ke"])
    s = rate / float(pf["rate"]) if pf["rate"] else 1.0
    out = dict(pf, rate=rate, lambda_kills=pf["lambda_kills"] * s, **{"lambda": pf["lambda"] * s}, prior_target=round(m_new, 4),
               version=str(pf["version"]) + "-prior-smooth")
    out["sd"] = float(pf["spread_c"]) * (max(out["lambda"], 1.0) ** 0.5)
    out.pop("g7", None)
    return out


def _perf_challenger(gi, pf, cfg):
    """(inputs, config) that grade gi with another performance-model projection pf THROUGH the performance model.
    grader.grade_leg uses perf only for versions in cfg["perf_versions"] and otherwise falls back to the old pipeline, so
    pf's own version is added to a copy of the config - the live CONFIG is never changed. 2026-10-13: 'perf-2-prior-smooth'
    was missing there, so the prior_smooth shadow logged from 2026-10-09 was the old pipeline (lambda = shadow.legacy)."""
    vs = tuple(cfg.get("perf_versions", ()))
    return dict(gi, perf=pf), (cfg if pf.get("version") in vs else dict(cfg, perf_versions=vs + (pf.get("version"),)))


def shadow_alts(gi):
    """The comparison versions graded on these inputs: name -> config, or (inputs, config) for a comparison that swaps
    in another performance-model projection (graded by the performance model under that projection's own version)."""
    from grader import CONFIG
    # pre_fix_v2: the behaviour before the 2026-10-09 fix (gap centred on line + 0.5; Maps 1-3 pushes removed per case)
    alts = {"old_spread": dict(CONFIG, c=1.55, c_hs=1.30),
            "no_rest_bonus": dict(CONFIG, rest_bonus=0.0),
            "pre_fix_v2": dict(CONFIG, fix_v2=False),
            # 2026-10-08 environment revision: the old behaviour and the provisional taper, on identical inputs
            "online_none": dict(CONFIG, online_guard="none"),
            "online_taper": dict(CONFIG, online_guard="taper"),
            # 2026-10-08 perf-1 goes live on Maps 1-2: the old pipeline, on identical inputs
            "legacy": dict(CONFIG, perf_scopes=()),
            # 2026-10-08 release comparisons: without the BO3.gg fallback / with the perf-1 normal HS curve
            "no_bo3": dict(CONFIG, perf_versions=("perf-2", "perf-1")),
            "perf1_normal_hs": dict(CONFIG, perf_hs_dist=False),
            # Map 1 candidate (frozen 2026-10-08): the performance model for ONE map (no halving of Maps 1-2)
            "perf_map1": dict(CONFIG, perf_scopes=("12", "1"))}
    pf_ = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    if pf_.get("g7"):                     # 2026-10-09 perf-2 live: the perf-1 (G7) projection on the same inputs
        alts["perf1_g7"] = _perf_challenger(gi, pf_["g7"], CONFIG)
    sp = smooth_prior_pf(pf_)
    if sp:                                # 2026-10-09 challenger: prior that relaxes toward the population as history grows
        alts["prior_smooth"] = _perf_challenger(gi, sp, CONFIG)
    return alts


def _factors(res):
    """Every input and every adjustment behind this grade (for measuring each factor later)."""
    gi, g, pb = res.get("grader_inputs") or {}, res.get("grade") or {}, res.get("player_block") or {}
    keep = ("kpr", "hs_pct", "maps_played", "sample_maps", "recent_avg", "recent_median", "recent_n", "kpr_last5",
            "round_swing", "rating", "teammate_ratings", "is_igl", "sniping", "dpr", "kast", "adr",
            "market_quality", "days_since_last_map", "map_win_probs", "trade_pr", "standin_for", "is_standin",
            "roster_confirmed", "on_listed_team", "recent", "per_map", "p_team_market", "odds_policy", "mapping_rev")
    out = {k: gi.get(k) for k in keep if k in gi}
    out["h2h_series"] = len(gi.get("h2h_series") or [])
    out["regional_series"] = len(gi.get("regional_series") or [])
    out["detail"] = g.get("detail")
    out["lambda_base"] = g.get("lambda_base")
    out["sd"] = g.get("sd")
    out["form_fight"], out["tape_fight"] = g.get("form_fight"), g.get("tape_fight")
    out["form_windows"] = (pb.get("overall") or {}).get("form_windows")
    out["team_kill_share"] = pb.get("team_kill_share")
    out["opening"] = pb.get("opening")
    out["hltv"] = {k: (res.get("hltv") or {}).get(k) for k in ("rating", "round_swing", "adr", "kast", "maps")}
    out["environment"] = (res.get("hltv_context") or {}).get("environment")   # LAN / online split (tested later)
    # frozen-test comparisons (2026-10-07): the same inputs graded by the alternatives - never shown to members
    try:
        from grader import grade_leg
        out["shadow"] = {"live_raw": g.get("probs_raw"),     # the live grade's own Over / Under / push chances
                         "live_online": g.get("online_adj")}   # online weight + the bonus actually applied
        for name, cfg in shadow_alts(gi).items():
            gin, cfg = cfg if isinstance(cfg, tuple) else (gi, cfg)
            gg = grade_leg(gin, cfg)
            # source = the projection that graded it (2026-10-13): a prior_smooth row is valid only with a
            # '...-prior-smooth' source - rows logged before then carry none and were the old pipeline
            out["shadow"][name] = {"p_more": gg.get("p_more"), "lambda": gg.get("lambda_final"),
                                   "grade": gg.get("grade"), "grade_less": gg.get("grade_less"),
                                   "raw": gg.get("probs_raw"), "source": gg.get("projection_source")}
    except Exception:
        pass
    try:   # shared-match challenger M1 (shadow, 2026-10-09): one simulated match per forecast - logged, never shown
        import match_model
        pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
        if (gi.get("scope") or "12") in ("1", "12") and pf.get("lambda_kills") is not None:
            m1 = match_model.shadow(pf, g.get("detail") or {}, gi.get("scope") or "12", gi.get("prop"), float(gi["line"]))
            if m1:
                out.setdefault("shadow", {})["match_m1"] = m1
    except Exception:
        pass
    try:   # perf2_hs (live on Maps 1-2 since G7; Map 1 still comparison): HS distribution averaged over kills
        pf = gi.get("perf") or {}
        if gi.get("prop") == "hs" and pf.get("lambda_kills") is not None and (gi.get("scope") or "12") in ("1", "12"):
            import perf
            o, u, pu = perf.hs_probs(pf["lambda_kills"], gi.get("scope") or "12", pf["hs_per_kill"], gi["line"], c=perf.kills_c(pf, gi.get("scope") or "12"))
            out.setdefault("shadow", {})["perf2_hs"] = {"p_more": round(o / (o + u), 4) if o + u else 0.5, "version": pf.get("version"),
                                                       "raw": {"over": round(o, 4), "under": round(u, 4), "push": round(pu, 4)}}
    except Exception:
        pass
    try:   # Maps 1-2 market-rounds candidate (perf_market, shadow only, 2026-10-13): logged, never shown. The module is
        # optional - absent (or failing) = nothing logged, the grade and the row are unaffected
        try:
            import perf_market
        except ImportError:
            perf_market = None
        if perf_market is not None and hasattr(perf_market, "shadow_entry"):
            se = perf_market.shadow_entry(gi, g)
            if se is not None:
                out.setdefault("shadow", {})["market_rounds"] = se
    except Exception:
        pass
    out["opponent_defense"] = res.get("opponent_defense")
    out["sim"] = (res.get("simulation") or {}).get("p_more")
    out["map_prediction"] = (res.get("player_block") or {}).get("map_prediction")
    try:   # what the projection would be if predicted maps counted (to test them on real lines)
        from grader import grade_leg, CONFIG
        if gi.get("map_probs"):
            out["lambda_with_pred_maps"] = grade_leg(gi, dict(CONFIG, use_predicted_maps=True))["lambda_final"]
    except Exception:
        pass
    return out


_QUEUE = queue.Queue()
_WRITER = [None]


_save_config_snapshot_done = [False]


def log_async(res, verdict, test=False):
    """log_grade() without making the member wait: the row is built now (small) and written by one
    background writer (a busy pick log used to add 2-15 s to every grade)."""
    if not _save_config_snapshot_done[0]:
        _save_config_snapshot_done[0] = True
        _save_config_snapshot()
    try:
        row = pick_row(res, verdict, test)
    except Exception:
        return
    if row is None:
        return
    _QUEUE.put(row)
    if _WRITER[0] is None or not _WRITER[0].is_alive():
        with _LOCK:
            if _WRITER[0] is None or not _WRITER[0].is_alive():
                _WRITER[0] = threading.Thread(target=_writer, daemon=True, name="picklog-writer")
                _WRITER[0].start()


def _writer():
    while True:
        row = _QUEUE.get()
        try:
            write_row(row)
        except Exception:
            import traceback
            traceback.print_exc()


def log_grade(res, verdict, test=False):
    """Log a member's graded line. Returns the row id (or None when it isn't a wager).
    test=True (owner's load tests) is stored but left out of the report."""
    return write_row(pick_row(res, verdict, test))


def pick_row(res, verdict, test=False):
    """The pick-log row for this grade (None when it isn't a wager)."""
    g, pb, nm = res.get("grade") or {}, res.get("player_block") or {}, res.get("next_match") or {}
    slug = nm.get("slug") or (f"hltv-{nm['hltv_match']}" if nm.get("hltv_match") else None)
    if not verdict or verdict.get("state") != "GRADE" or "p_more" not in g or not slug:
        return None
    nm = dict(nm, slug=slug)   # matches only HLTV has are logged as hltv-<HLTV match id>
    # the record logged with the pick is in the LINE'S scope: Map 1 lines use the Map 1 record; Maps 1-3 has none
    # (until 2026-10-08 the Maps 1-2 record was logged against Map 1 / Maps 1-3 lines)
    _sc = (g.get("scope") or "12")
    rf = (pb.get("recent_form_map1") or {}) if _sc == "1" else (pb.get("recent_form_123") or {}) if _sc == "123" else (pb.get("recent_form") or {})
    scope = g.get("scope") or "12"
    key = "|".join(str(x) for x in (pb["player"]["slug"], g["prop"], g["line"], g["line_type"], nm["slug"]))
    if scope != "12":
        key += "|maps" + scope                         # Maps 1-2 keys unchanged
    bk = res.get("book") or {}                         # board grades: the platform + the provider's market id
    if bk.get("book") == "prizepicks":
        key += "|pp"                                   # one row per platform: the same number on both books never merges
    row = {
        "key": key, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "player": pb["player"]["nick"], "slug": pb["player"]["slug"], "player_id": pb["player"]["id"],
        # 2026-10-13 (UNO MILLE audit): team = the team he plays THIS match for (the one the projection, the odds and the
        # match use), else the platform's label, else his team now; the BO3.gg profile team is kept as profile_team.
        # Before: always the BO3.gg profile team (ALLE 'UNO MILLE' while playing ex-Keyd Stars vs Galorys)
        "team": pb.get("playing_for") or res.get("team_hint") or pb.get("current_team"),
        "profile_team": pb.get("profile_team", pb.get("current_team")),
        "opponent": nm.get("opponent"), "match_slug": nm["slug"],
        "match_start": nm.get("start"), "prop": g["prop"], "line": g["line"], "line_type": g["line_type"],
        "lambda": g.get("lambda_final"), "fair_line": (g.get("value") or {}).get("fair_line"),
        "edge": round(g["lambda_final"] - g["line"], 2), "p_more": g["p_more"], "p_less": g["p_less"],
        "pick": verdict["side"], "p_pick": verdict["p"], "grade": verdict["grade"], "units": verdict["units"],
        "p_win": (g.get("detail") or {}).get("p_win"), "market": g.get("market_quality"),
        "pred_maps": json.dumps(pb.get("map_prediction")), "veto_known": int(bool(nm.get("maps"))),
        "sample_maps": (pb.get("overall") or {}).get("maps_played"), "rec_avg": rf.get("avg_total"),
        "rec_median": rf.get("median_total"),
        "rec_hit": f"{rf.get('overs')}/{rf.get('decided', rf.get('matches'))}" if rf.get("overs") is not None else None,   # pushes out
        "flags": json.dumps(g.get("flags")), "opp_def": json.dumps(res.get("opponent_defense")),
        "test": test if test == 2 else int(bool(test)),   # 1 = owner's tests, 2 = Underdog board (not a member pick)
        "factors": json.dumps(_factors(res), default=str),
        "scope": scope,
        "model_version": MODEL_VERSION,
        # 2026-10-10: the platform the line came from (board grades) and its market id; member research lines: None
        "book": bk.get("book"), "market_id": bk.get("market_id"), "market_key": bk.get("market"),
    }
    try:
        import zlib
        row["_gi"] = zlib.compress(json.dumps(res.get("grader_inputs"), default=str).encode(), 6)
    except Exception:
        pass
    row["_probs"] = json.dumps(g.get("probs_raw")) if g.get("probs_raw") else None
    # which market supplied this grade's win probability (market_odds.select) - a change of source is a new forecast;
    # 2026-10-13: so is a changed market input of the same source (odds_sig) or another odds policy; the alias / matching
    # revision is recorded on every row (a mapping change that alters a line's selection shows up in odds_sig / source)
    gi = res.get("grader_inputs") or {}
    pv = (res.get("odds") or {}).get("provenance") or {}
    row["_odds_src"] = pv.get("selected")
    row["_odds_sig"] = pv.get("sig")
    if row["_odds_sig"] is None and res.get("odds") is not None:
        try:
            import market_odds
            row["_odds_sig"] = market_odds.odds_signature(res.get("odds"))
        except Exception:
            pass
    row["_odds_policy"] = pv.get("odds_policy") or gi.get("odds_policy")
    row["_mapping_rev"] = gi.get("mapping_rev") or pv.get("mapping_rev")
    return row


_REV = {"at": 0.0, "rev": None}
REV_TTL = 600


def data_revision():
    """The HLTV dataset revision: 'matches=<n>;max_id=<id>;max_date=<day>' from match_overview, cached 10 minutes. None
    when the database can't be read (never blocks logging). Called from the pick-log writer thread only."""
    now = time.time()
    if now - _REV["at"] < REV_TTL:
        return _REV["rev"]
    _REV["at"] = now
    try:
        import hltv
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT count(*), max(matchid), max(date) FROM match_overview")
                n, mx, md = cur.fetchone()
        finally:
            con.close()
        _REV["rev"] = f"matches={n};max_id={mx};max_date={md}"
    except Exception:
        _REV["rev"] = None
    return _REV["rev"]


def _shadow(row):
    try:
        return json.dumps((json.loads(row.get("factors") or "{}") or {}).get("shadow"))
    except (ValueError, TypeError):
        return None


def _unchanged(last, row, extra):
    """The new grade repeats the last stored forecast: same line, side, grade, stake, model version, odds source, odds
    policy and market input (odds_sig), chance within 0.01 and projection within 0.2. mapping_rev is recorded, not
    compared: a mapping change that alters a line's selection already changes odds_sig / odds_source; one that does not
    leaves the forecast as it was (unchanged polling never writes a row)."""
    return (last["line"] == row["line"] and last["pick"] == row["pick"] and last["grade"] == row["grade"]
            and last["units"] == row["units"] and last["model_version"] == row.get("model_version")
            and abs((last["p_pick"] or 0) - (row["p_pick"] or 0)) < 0.01
            and abs((last["lambda"] or 0) - (row.get("lambda") or 0)) < 0.2
            and last["odds_source"] == extra.get("odds_source") and last["odds_sig"] == extra.get("odds_sig")
            and last["odds_policy"] == extra.get("odds_policy"))


def _record_mapping(con, rev):
    """The first forecast made under a mapping revision stores that revision's full content (mapping_revisions,
    append-only, same transaction as the forecast - a rolled-back write leaves no orphan) - any old alias / matching
    state can be replayed (mapping.content(rev))."""
    if not rev:
        return
    try:
        import mapping
        if con.execute("SELECT 1 FROM mapping_revisions WHERE rev = ?", (rev,)).fetchone() or mapping.revision() != rev:
            return
        mapping.record(con)
    except Exception:
        pass


def _forecast(con, pid, row, kind, gi=None, extra=None):
    """Append one forecast to the history - only when it differs from the last one stored for this pick.
    The picks row itself stays the FIRST published forecast (never overwritten)."""
    extra = extra or {}
    last = con.execute("SELECT line, pick, p_pick, grade, units, lambda, model_version, odds_source, odds_sig, odds_policy "
                       "FROM forecasts WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if last and kind == "update" and _unchanged(last, row, extra):
        return
    con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, "
                "lambda, p_win, rec_hit, sample_maps, model_version, shadow, inputs, probs_raw, data_rev, odds_source, "
                "odds_policy, mapping_rev, odds_sig, git_commit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, kind, row["logged_at"], row.get("match_start"), row["line"], row["pick"], row["p_pick"],
                 row.get("p_more"), row["grade"], row["units"], row.get("lambda"), row.get("p_win"), row.get("rec_hit"),
                 row.get("sample_maps"), row.get("model_version"), _shadow(row), gi, extra.get("probs_raw"), extra.get("data_rev"),
                 extra.get("odds_source"), extra.get("odds_policy"), extra.get("mapping_rev"), extra.get("odds_sig"),
                 extra.get("git_commit")))
    _record_mapping(con, extra.get("mapping_rev"))


def _utc_time(s):
    """An ISO time as an aware UTC datetime (no offset = UTC, as recap / learning read it); None when unreadable."""
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _before_start(row, cur):
    """The new grade was made before the match started (by the start the row has, and the one it brings). Compared as
    times (2026-10-13 audit: as strings a start with another UTC offset, or written without the 'T', compared wrong)."""
    now = row["logged_at"]
    for st in (cur["match_start"], row.get("match_start")):
        if not st:
            continue
        a, b = _utc_time(now), _utc_time(st)
        if (a >= b) if a is not None and b is not None else now >= st.replace("Z", "+00:00")[:25]:
            return False
    return True


def write_row(row):
    if row is None:
        return None
    row = dict(row)
    gi = row.pop("_gi", None)                       # the exact grader inputs - forecasts table only
    extra = {"probs_raw": row.pop("_probs", None), "data_rev": data_revision(), "odds_source": row.pop("_odds_src", None),
             "odds_sig": row.pop("_odds_sig", None), "odds_policy": row.pop("_odds_policy", None),
             "mapping_rev": row.pop("_mapping_rev", None),
             "git_commit": os.environ.get("RENDER_GIT_COMMIT") or None}    # the deployed commit (Render sets it)
    key, test = row["key"], row["test"]
    with _LOCK:
        con = _db()
        try:
            cur = con.execute("SELECT id, test, status, match_start FROM picks WHERE key = ?", (key,)).fetchone()
            if cur and cur["status"] == "open" and _before_start(row, cur):
                _forecast(con, cur["id"], row, "update", gi, extra)  # every changed pre-match forecast, timestamped
                con.execute("UPDATE picks SET regrades = COALESCE(regrades, 0) + 1 WHERE id = ?", (cur["id"],))
            if cur:
                # a real member asking for a line first seen in a test makes it a real pick. 2026-10-13 audit D3: so does the
                # board grading a line an owner load test reached first (it stayed test 1 - out of every cohort - with no
                # platform); the board's write fills the platform + market id the row lacks. A board row the owner marked
                # as a test by hand (mark_tests) has its platform and stays a test.
                bw = int(int(test or 0) == 2)
                con.execute("UPDATE picks SET requests = requests + 1, "
                            "test = CASE WHEN ? AND test = 1 AND book IS NULL THEN 2 WHEN ? THEN test ELSE 0 END, "
                            "on_board = CASE WHEN ? THEN 1 ELSE COALESCE(on_board, 0) END, "
                            "book = CASE WHEN ? THEN COALESCE(book, ?) ELSE book END, "
                            "market_id = CASE WHEN ? THEN COALESCE(market_id, ?) ELSE market_id END, "
                            "market_key = CASE WHEN ? THEN COALESCE(market_key, ?) ELSE market_key END WHERE id = ?",
                            (bw, int(bool(test)), bw, bw, row.get("book"), bw, row.get("market_id"), bw, row.get("market_key"),
                             cur["id"]))
                con.commit()
                return cur["id"]
            row["on_board"] = 1 if int(test or 0) == 2 else 0
            cols = ", ".join(row)
            con.execute(f"INSERT INTO picks ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            pid = con.execute("SELECT id FROM picks WHERE key = ?", (key,)).fetchone()["id"]
            _forecast(con, pid, row, "first", gi, extra)
            con.commit()
            return pid
        finally:
            con.close()


def _quick_rows(b, player_id, mt):
    """Maps 1-2 kills/headshots from BO3.gg's quick scoreboard (published right after the match)."""
    out = []
    for g in sorted((g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2),
                    key=lambda g: g.get("number") or 0):
        try:
            row = next((x for x in b.game_short_stats(g["id"]) or [] if x.get("player_id") == player_id), None)
        except Exception:
            row = None
        if not row or row.get("kills_sum") is None:
            return []
        if 0 < (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0) < 13:
            return []                                  # a map can't end under 13 rounds: forfeit / technical win
        # 2026-10-13 audit D13: a quick scoreboard without headshots has not published them yet - never 0 (an HS pick
        # used to settle as a big Under); the caller waits for them (or HLTV)
        out.append({"map": g.get("map_name"), "kills": row["kills_sum"], "headshots": row.get("headshots_sum")})
    return out if len(out) == 2 else []


def settle(b, map_rows):
    """Fill in actual Maps 1-2 totals for matches that have finished."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status = 'open'").fetchall()
        con.close()
    done = 0
    for r in rows:
        try:                                          # 2026-10-13 audit B4: one failing pick (a BO3.gg error, a missing
            done += _settle_one(b, map_rows, r, now)  # stat) used to stop the whole pass - every pass, every later pick
        except Exception:
            import traceback
            traceback.print_exc()
    try:
        revert_full_scoreboard_corrections(now=now)
    except Exception:
        pass
    try:
        recheck_bo3_quick(b, map_rows, now=now)
    except Exception:
        pass                                           # never blocks settling; the next pass tries again
    try:
        recheck_bo3_with_hltv(now=now)
    except Exception:
        pass
    return done


def _settle_one(b, map_rows, r, now):
    """One open pick: 1 when it was settled / pushed / voided, else 0."""
    try:
        start = datetime.fromisoformat((r["match_start"] or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    if start.tzinfo is None:                      # 2026-10-13 audit B4: a start without an offset is UTC (it used to raise
        start = start.replace(tzinfo=timezone.utc)  # on the first comparison and stop the pass)
    # the data decides, not the clock: HLTV's result settles a pick as soon as the scraper has it
    # (listed start times are often off); BO3.gg's only after SETTLE_MIN minutes
    early = now < start + timedelta(minutes=SETTLE_MIN)
    if now < start:
        return 0          # 2026-10-13 audit D1: never before the listed start (was 12 h before: an earlier meeting of the
        #                   same teams that day could settle it)
    if (_col(r, "scope") or "12") != "12":         # Maps 1-3 / Map 1 lines: HLTV only (has map 3 and BO1)
        return _set_result(r, _settle_scope(r, now, start), now)
    if early and not (r["match_slug"] or "").startswith("hltv-"):
        hv = _hltv_totals(r, same_day_only=True)
        return _set_result(r, hv if isinstance(hv, tuple) else None, now)
    if (r["match_slug"] or "").startswith("hltv-"):
        return _set_result(r, _settle_hltv(r, now, start), now)
    # HLTV first - the official record (BO3.gg's quick scoreboards and map order are sometimes off)
    hv = _hltv_totals(r)
    if hv == "transient":
        return 0          # 2026-10-13 audit D5: HLTV unreachable - never BO3.gg's result or a void as if HLTV had no match
    if hv == "wait" and now < start + timedelta(hours=HLTV_WAIT_H):
        return 0          # HLTV has the match but not this stat yet (scraped every ~2 h)
    if isinstance(hv, tuple):
        return _set_result(r, hv, now)
    # fresh list (15 min): a 12-hour-old copy still shows finished matches as upcoming
    ms = b.player_matches(r["player_id"], pages=1, ttl=0.25).get("results") or []
    mt = next((m for m in ms if m.get("slug") == r["match_slug"]), None)
    upd = None
    stat = "headshots" if r["prop"] == "hs" else "kills"
    if mt and mt.get("status") == "finished":
        maps = map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
        done12 = [g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2 and g.get("state") == "done"]
        if len(maps) < 2 and len(done12) >= 2:
            for g in done12:                          # maps done but a scoreboard read empty:
                try:                                  # fetch it fresh once and look again
                    b.game_players_fresh(g["id"])
                except Exception:
                    pass
            maps = map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
        quick = False
        if len(maps) < 2 or any(x.get(stat) is None for x in maps):
            qr = _quick_rows(b, r["player_id"], mt)       # full stats not out yet
            maps, quick = (qr, True) if qr else (maps, False)
        if len(maps) == 2 and all(x.get(stat) is not None for x in maps):   # D13: a missing stat is never a 0
            # quick=True: settled from BO3.gg's quick scoreboard - recheck_bo3_quick() re-derives it from the full
            # scoreboard once that is out (2026-10-12 audit: the quick one can be a kill short)
            _save_result(r["id"], {"source": "bo3", "rounds": [x.get("rounds") for x in maps],
                                   "won": [x.get("won") for x in maps], **({"quick": True} if quick else {}),
                                   "per_map": [[x.get("kills"), x.get("headshots")] for x in maps]})
            actual = sum(x[stat] for x in maps)
            if actual == r["line"]:                   # whole-number line landed exactly: push
                upd = ("push", json.dumps([x["map"] for x in maps]), actual, None)
            else:
                hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
                upd = ("settled", json.dumps([x["map"] for x in maps]), actual, int(hit))
        elif (len([g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2]) >= 2
              and now < start + timedelta(days=VOID_AFTER_D)):
            return 0      # Maps 1-2 were played but BO3.gg hasn't published the stats yet: wait
        else:
            upd = ("void", json.dumps([x["map"] for x in maps]), None, None)   # forfeit / BO1 / sat out
    elif mt and mt.get("status") == "defwin":            # forfeit: no maps played
        upd = ("void", None, None, None)
    elif now > start + timedelta(days=VOID_AFTER_D):
        upd = ("void", None, None, None)
    return _set_result(r, upd, now)


def _set_result(r, upd, now):
    """Store a settlement (status, actual_maps, actual, hit) for one pick: 1, or 0 when there is none yet."""
    if not upd:
        return 0
    with _LOCK:
        con = _db()
        con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                    (*upd, now.isoformat(timespec="seconds"), r["id"]))
        con.commit()
        con.close()
    return 1


HLTV_RECHECK_MAX = 60        # picks per settle pass
_RECHECK = {}                # recheck rotation: pass name -> the last key it checked (this process)


def _rotate(items, key, name, limit):
    """Up to `limit` items, starting after the last one the previous pass checked and wrapping around. 2026-10-13 audit
    D7: the first `limit` rows in row order were taken every pass, so rows HLTV never has (outside the scraper's coverage)
    held every newer row back until they left the window."""
    items = sorted(items, key=key)
    last = _RECHECK.get(name)
    if last is not None:
        items = [x for x in items if key(x) > last] + [x for x in items if key(x) <= last]
    out = items[:limit]
    if out:
        _RECHECK[name] = key(out[-1])
    return out


def _hltv_due(r):
    """A BO3.gg settlement (the row's own source - a corrected row keeps the BO3.gg detail nested) HLTV has not checked,
    or checked while it was held for review."""
    try:
        res = json.loads(r["result"] or "{}")
    except ValueError:
        return False
    return res.get("source") == "bo3" and (not res.get("hltv_checked") or bool(res.get("review")))


def recheck_bo3_with_hltv(now=None, apply=True, days=7, limit=HLTV_RECHECK_MAX):
    """2026-10-12 audit D4: picks settled from BO3.gg (cool4st 23 vs HLTV 24, chelo 15 vs 16) are re-derived from HLTV
    once HLTV has the match, through scope_result() (strict identity: the player's own team, the opponent's HLTV id,
    exactly one match). A different total is corrected with old and new values in settlement_corrections.jsonl; an
    equal one is marked hltv_checked (and a hold for review released). HLTV unreachable / not there yet = untouched (next
    pass); `limit` rows per pass, in rotation."""
    from scope import outcome as _oc
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _LOCK:
        con = _db()
        # a pick HLTV confirmed while it was held for review (before 2026-10-13 the hold was kept) is re-derived once more:
        # confirmed again -> released, different -> corrected to HLTV's total
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push') AND settled_at >= ? "
                           "AND (scope IS NULL OR scope = '12') AND result LIKE '%\"source\": \"bo3\"%' "
                           "AND (result NOT LIKE '%hltv_checked%' OR result LIKE '%\"review\": true%')", (since,)).fetchall()
        con.close()
    rows = [r for r in rows if _hltv_due(r)]
    out = {"checked": 0, "confirmed": 0, "corrected": [], "waiting": 0}
    for r in _rotate(rows, lambda x: x["id"], "hltv", limit):
        try:
            res = scope_result(r)
        except Exception:
            out["waiting"] += 1
            continue
        if res.get("status") != "ok":
            out["waiting"] += 1
            continue
        out["checked"] += 1
        try:
            old = json.loads(r["result"] or "{}")
        except ValueError:
            old = {}
        st, hit = _oc(res["total"], r["line"], r["pick"])
        if res["total"] == r["actual"] and st == r["status"] and (hit == r["hit"] or (hit is None and r["hit"] is None)):
            out["confirmed"] += 1
            if apply:
                # 2026-10-13 audit D4: HLTV (authoritative) confirms the total - a hold for review / provisional mark is
                # released (it used to be copied forward, and the row was never looked at again: unscored for good)
                rel = {k: False for k in ("review", "provisional") if k in old}
                _save_result(r["id"], dict(old, hltv_checked=True, hltv_match=res["hltv_match"], hltv_total=res["total"], **rel, **_ident(res)))
            continue
        detail = {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                  "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], "hltv_checked": True, "bo3_detail": old, **_ident(res)}
        d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": "12", "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": res["total"], "status": st, "hit": hit}, "hltv_match": res["hltv_match"],
             "reason": "HLTV result differs from the BO3.gg settlement"}
        out["corrected"].append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, actual_maps=?, result=? WHERE id=?",
                            (st, res["total"], hit, json.dumps(res["maps"]), json.dumps(detail, default=str), r["id"]))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=now.isoformat(timespec="seconds")), default=str) + "\n")
    return out


QUICK_RECHECK_D = 7          # BO3.gg settlements are compared with the full scoreboard for this long
QUICK_RECHECK_MAX = 40       # matches per settle pass (each = one player list + two scoreboards, cached)
REVIEWS = DB.parent / "settlement_reviews.jsonl"
SOURCE_RULE = ("HLTV authoritative; BO3.gg provisional until HLTV re-derives it; BO3.gg quick scoreboard preferred over the "
               "full one (2026-10-12 audit: from 2026-03 quick = HLTV on ~99.2% of player-maps, full on 96.1% - the full "
               "scoreboard subtracts team-kills); quick != full with the line between them -> held for review, not scored")


def _is_quick(res):
    """A BO3.gg settlement not yet compared with the full scoreboard: marked quick, or (before the mark, 2026-10-12) no
    round counts."""
    return res.get("source") == "bo3" and not res.get("full_checked") and (
        res.get("quick") or all(x is None for x in (res.get("rounds") or [None])))


def _between(line, a, b):
    """The line separates the two totals (the outcome depends on which source is right)."""
    lo, hi = min(a, b), max(a, b)
    return lo != hi and lo <= line <= hi


def recheck_bo3_quick(b, map_rows, now=None, apply=True, days=QUICK_RECHECK_D, limit=QUICK_RECHECK_MAX):
    """Compare every BO3.gg quick-scoreboard settlement with BO3.gg's full scoreboard once it is out (same player id,
    match slug, maps 1-2 by number). The quick total STANDS (SOURCE_RULE). Equal -> marked full_checked. Different ->
    the result records both totals ("discrepancy") and stays provisional; when the line sits between them the pick is
    held for review ("review": True - the learning ledger does not score it) and logged in settlement_reviews.jsonl.
    Status / total / hit are never changed here. Returns {"checked", "confirmed", "disagree", "held", "waiting"}."""
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push') AND settled_at >= ? "
                           "AND (scope IS NULL OR scope = '12') AND result LIKE '%\"source\": \"bo3\"%'", (since,)).fetchall()
        con.close()
    groups = {}
    for r in rows:
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            continue
        # 2026-10-13 audit D4: a total HLTV (authoritative) has confirmed is never held again by BO3.gg's full scoreboard
        if _is_quick(res) and not res.get("hltv_checked") and r["player_id"] and r["match_slug"]:
            groups.setdefault((r["player_id"], r["match_slug"]), []).append((r, res))
    out = {"checked": 0, "confirmed": 0, "disagree": [], "held": 0, "waiting": 0}
    for (pid, slug), picks in _rotate(list(groups.items()), lambda kv: min(x["id"] for x, _ in kv[1]), "quick", limit):
        try:                                            # one BO3.gg error never stops the other matches (audit B4)
            ms = b.player_matches(pid, pages=1, ttl=1).get("results") or []
            mt = next((m for m in ms if m.get("slug") == slug), None)
            if not mt or mt.get("status") != "finished":
                out["waiting"] += len(picks)
                continue
            maps = sorted(map_rows(b, pid, [mt], lambda m, g: (g.get("number") or 9) <= 2), key=lambda x: x.get("number") or 9)
        except Exception:
            out["waiting"] += len(picks)
            continue
        if [x.get("number") for x in maps] != [1, 2] or any(x.get("rounds") in (None, 0) for x in maps):
            out["waiting"] += len(picks)                # full scoreboard not published yet
            continue
        for r, old in picks:
            out["checked"] += 1
            stat = "headshots" if r["prop"] == "hs" else "kills"
            if any(x.get(stat) is None for x in maps) or r["actual"] is None:
                out["waiting"] += 1
                continue
            full = sum(x[stat] for x in maps)
            new = dict(old, rounds=[x["rounds"] for x in maps], won=[x.get("won") for x in maps], full_checked=True,
                       quick=True, full_per_map=[[x.get("kills"), x.get("headshots")] for x in maps], rule=SOURCE_RULE)
            if full == r["actual"]:
                out["confirmed"] += 1
                if apply:
                    _save_result(r["id"], new)
                continue
            held = _between(r["line"], r["actual"], full)
            new.update(provisional=True, discrepancy={"quick_total": r["actual"], "full_total": full}, review=held)
            d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"], "pick": r["pick"],
                 "quick_total": r["actual"], "full_total": full, "held_for_review": held, "match": slug}
            out["disagree"].append(d)
            out["held"] += int(held)
            if apply:
                _save_result(r["id"], new)
                with open(REVIEWS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, at=now.isoformat(timespec="seconds"), rule=SOURCE_RULE), default=str) + "\n")
    return out


FULL_REASONS = ("BO3.gg full scoreboard replaced the quick scoreboard", "settlement audit: BO3.gg full scoreboard differs")


def revert_full_scoreboard_corrections(now=None, apply=True):
    """Undo the 2026-10-12 corrections that replaced a BO3.gg quick-scoreboard settlement with the full scoreboard
    (SOURCE_RULE): restore the logged old status / total / hit, record both totals, hold for review when the line sits
    between them, and append the reversal to settlement_corrections.jsonl. Idempotent (marker reverted_full); never
    touches a pick HLTV has since settled (source hltv)."""
    now = now or datetime.now(timezone.utc)
    try:
        lines = [json.loads(x) for x in CORRECTIONS.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    first = {}
    for e in lines:
        if e.get("reason") in FULL_REASONS and e.get("id") is not None and e["id"] not in first:
            first[e["id"]] = e                          # the first full-scoreboard correction holds the quick values
    done = []
    for pid, e in first.items():
        with _LOCK:
            con = _db()
            r = con.execute("SELECT * FROM picks WHERE id = ?", (pid,)).fetchone()
            con.close()
        if r is None:
            continue
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            res = {}
        if res.get("reverted_full") or res.get("source") == "hltv" or res.get("hltv_checked"):
            continue                                    # HLTV settled / confirmed it: never changed back (2026-10-13 audit)
        old = e.get("old") or {}
        if old.get("actual") is None:
            continue
        full = r["actual"]
        held = full is not None and _between(r["line"], old["actual"], full)
        new = dict(res, source="bo3", quick=True, full_checked=True, reverted_full=True, provisional=True, rule=SOURCE_RULE,
                   discrepancy={"quick_total": old["actual"], "full_total": full}, review=held,
                   per_map=old.get("per_map") or res.get("per_map"), full_per_map=res.get("per_map"))
        d = {"id": pid, "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": old["actual"], "status": old.get("status"), "hit": old.get("hit")}, "held_for_review": held,
             "reason": "reverted: the BO3.gg quick scoreboard matches HLTV's definition better than the full one (SOURCE_RULE)"}
        done.append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, result=? WHERE id=?",
                            (old.get("status") or r["status"], old["actual"], old.get("hit"), json.dumps(new, default=str), pid))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=now.isoformat(timespec="seconds")), default=str) + "\n")
    return done


def _save_result(pick_id, result):
    try:
        with _LOCK:
            con = _db()
            con.execute("UPDATE picks SET result = ? WHERE id = ?", (json.dumps(result, default=str), pick_id))
            con.commit()
            con.close()
    except sqlite3.Error:
        pass


SETTLE_MIN = 75     # BO3.gg results are trusted this long after the listed start (Maps 1-2 can't be faster)
HLTV_WAIT_H = 12   # HLTV has the match but not its headshots yet: wait this long before BO3.gg


def _outcome(r, actual, maps):
    if actual == r["line"]:
        return ("push", json.dumps(maps), actual, None)
    hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
    return ("settled", json.dumps(maps), actual, int(hit))


def _col(r, k):
    try:
        return r[k]
    except (IndexError, KeyError):
        return None


def _forfeited(cur, mid):
    """HLTV's forfeited-map count for a match (None = unknown). HLTV unreachable raises (2026-10-13 audit: read as
    'unknown', a forfeited match settled on its 0-kill map instead of waiting)."""
    try:
        cur.execute("SELECT forfeited_maps FROM match_overview WHERE matchid = %s", (mid,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            raise
        return None


def _team_confirmed(cur, ids, team):
    """The HLTV player's maps include the pick's team: an exact name variant, or the team's HLTV id via the alias table
    (hltv.resolve_team_id). No team on the pick = nothing to confirm against (unconfirmed)."""
    import hltv
    if not team:
        return False
    cur.execute("SELECT DISTINCT t.teamid, t.name FROM player_stats ps JOIN teams t ON t.teamid = ps.teamid "
                "WHERE ps.playerid = ANY(%s)", (list(ids),))
    got = cur.fetchall()
    variants = set(hltv.team_name_variants(team))
    if any(variants & set(hltv.team_name_variants(name)) for _tid, name in got):
        return True
    tid = hltv.resolve_team_id(cur, team)
    return tid is not None and any(t == tid for t, _n in got)


def _settle_teams(r):
    """The team names settlement may identify the player by, in order: picks.team (since 2026-10-13 the team he plays
    THIS match for - the projection's and the odds' team), then picks.profile_team (his BO3.gg profile team - what
    picks.team held before; NULL on older rows). The settlement keys themselves are unchanged: HLTV player id + the
    opponent's HLTV team id + the listed match (the hltv-<id>, or the candidate matchid verifies - 2026-10-13), or the
    BO3.gg player id + match slug."""
    out = []
    for t in (_col(r, "team"), _col(r, "profile_team")):
        if t and str(t).strip().lower() not in [x.strip().lower() for x in out]:
            out.append(str(t))
    return out or [None]


def _player_identity(cur, r):
    """(HLTV player ids, refused): the first of _settle_teams whose HLTV player is confirmed on that team. A stand-in's
    match team usually confirms (that match is in his HLTV rows once played); his profile team is the fallback, so a
    pick never settles less than before. refused = candidates found but none confirmed."""
    import hltv
    found = False
    for t in _settle_teams(r):
        ids = hltv._player_ids(cur, r["player"], t)
        if not ids:
            continue
        found = True
        if _team_confirmed(cur, ids, t):
            return ids, False
    return [], found


def _canonical_hltv(r):
    """2026-10-13 audit D1 (owner: settlement on the SAME canonical identity as grading): the pick's HLTV match =
    matchid.canonical_match(team, opponent, listed start) - the call the projection's odds link makes
    (thunderpick.projection_odds): canonical team ids + the maintained aliases, an HLTV schedule time within
    matchid.WINDOW_H of the start, or a finished match dated that UTC day. picks.team first, then the BO3.gg profile team
    (_settle_teams). Returns (HLTV match id, opponent's HLTV team id or None, None), (None, None, why) when the canonical
    match names no HLTV match (hashed / ambiguous / unresolved), or (None, None, None) when matchid has no HLTV team
    table loaded (tests; a first boot without the database) - the previous rule (date window) applies."""
    import matchid
    try:
        if not ((matchid.status().get("teams") or {}).get("rows")):
            return None, None, None
    except Exception:
        return None, None, "match identity unavailable"
    why = []
    for t in _settle_teams(r):
        cm = matchid.canonical_match(t, _col(r, "opponent"), _col(r, "match_start"))
        if cm.get("hltv_match") is not None:
            k = str(matchid.team_key(_col(r, "opponent")) or "")
            return int(cm["hltv_match"]), (int(k[5:]) if k.startswith("team:") else None), None
        why.append(f"{t} vs {_col(r, 'opponent')}: {cm.get('reason') or cm.get('status')}")
        if cm.get("status") == "ambiguous":
            break
    return None, None, "; ".join(why) or "no canonical match"


def scope_result(r, same_day_only=False):
    """The pick's series from HLTV and its total for the line's scope, via the shared scope rules (scope.py).
    Returns {"status": "ok", "total", "maps_used", "maps", "per_map", "rounds", "won", "hltv_match"} or
    {"status": "wait"|"none"|"error", "reason"}. Never substitutes another scope; two candidate matches = error.
    2026-10-13 audit D1: only the pick's own match, read by its HLTV match id - an hltv-<id> pick's own id (every scope),
    a BO3.gg-slug pick's canonical match (_canonical_hltv, the identity grading uses). Never a date window: an earlier
    meeting of the same teams (yesterday's upper-bracket final, this morning's opener) used to settle the pick."""
    import hltv
    from scope import scope_total, MAP3_NOT_PLAYED
    day = (r["match_start"] or "")[:10]
    slug = str(_col(r, "match_slug") or "")
    own = int(slug[5:]) if slug.startswith("hltv-") and slug[5:].isdigit() else None
    link, oid, why = (own, None, None) if own is not None else _canonical_hltv(r)
    if link is None and why:
        return {"status": "none", "reason": f"no canonical HLTV match for this pick ({why}) - not settled from HLTV"}
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            ids, refused = _player_identity(cur, r)
            if refused:
                # 2026-10-12 audit: a nick-only match (foggers of Rooster -> an HLTV 'foggers' who never played for
                # Rooster) is not this player - never settle on it (Maps 1-2 falls back to BO3.gg's player id)
                return {"status": "none", "reason": "player/team identity not confirmed in HLTV"}
            if link is not None:
                rows = hltv._series_rows(cur, ids, "2000-01-01", "AND mo.matchid = %(mid)s", {"mid": link}, maxn=3, need=(1,)) if ids else []
            else:                                            # matchid not loaded: the previous rule
                rows = hltv._series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                         {"d": day}, maxn=3, need=(1,)) if ids else []
            opp = (r["opponent"] or "").strip()
            if oid is not None:                              # the canonical opponent (matchid team id + aliases)
                on = [x for x in rows if x.get("opponent_id") in (None, oid)]
            else:
                on, how = hltv.opponent_rows(cur, rows, opp)     # opponent's HLTV team id first, exact names as fallback
            if same_day_only and link is None:
                on = [x for x in on if x["date"] >= day]     # an earlier meeting is not this match (previous rule)
            mids = {x["hltv_match"] for x in on}
            forfeits = _forfeited(cur, next(iter(mids))) if len(mids) == 1 else None
    finally:
        con.close()
    if not on and rows and link is not None:
        return {"status": "none", "reason": f"HLTV match {link}: the player's rows are not against {opp!r} - not settled"}
    if not on:
        return {"status": "none", "reason": "match not in HLTV yet"}
    if len(mids) > 1:
        return {"status": "error", "reason": f"{len(mids)} HLTV matches vs this opponent around that day - ambiguous"}
    if forfeits:
        return {"status": "void", "reason": f"{forfeits} forfeited map(s)"}
    ms = sorted(on, key=lambda x: x["number"])
    maps = [{"number": x["number"], "kills": x["kills"], "headshots": x["headshots"], "won": x["won"]} for x in ms]
    sc = _col(r, "scope") or "12"
    total, used = scope_total(maps, sc, r["prop"])
    if total is None and used == MAP3_NOT_PLAYED:
        return {"status": "void", "reason": used}      # Underdog voids Maps 1-3 when map 3 isn't played
    if total is None:
        if sc == "123" and len(ms) == 2 and ms[0]["won"] != ms[1]["won"]:
            return {"status": "wait", "reason": "1-1 after two maps: map 3 not in yet"}
        if any(x[("headshots" if r["prop"] == "hs" else "kills")] is None for x in ms):
            return {"status": "wait", "reason": "stat not scraped yet"}
        return {"status": "error", "reason": used}
    return {"status": "ok", "total": total, "maps_used": used, "maps": [x["map"] for x in ms if x["number"] in used],
            "per_map": [[x["kills"], x["headshots"]] for x in ms], "rounds": [x["rounds"] for x in ms],
            "won": [x["won"] for x in ms], "hltv_match": ms[0]["hltv_match"],
            # which identity the match was read under (stored with the settlement - evaluation keys on verified ones)
            "identity": "hltv id" if own is not None else "canonical match" if link is not None else "date window (matchid not loaded)"}


def _ident(res):
    """The settlement's identity label, for the stored result (absent when the lookup did not say)."""
    return {"identity": res["identity"]} if res.get("identity") else {}


def _settle_scope(r, now, start):
    """A Maps 1-3 or Map 1 line, from HLTV via scope_result(). None = not yet; void after VOID_AFTER_D days."""
    try:                                              # the match's own day first; the +-1 day window (time zones) only once the
        res = scope_result(r, same_day_only=now < start + timedelta(hours=36))   # right match had time to be scraped
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            return None                               # HLTV unreachable: never a void on that (2026-10-11 audit)
        res = {"status": "none"}
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] != "ok":
        return ("void", None, None, None) if now > start + timedelta(days=VOID_AFTER_D) else None
    _save_result(r["id"], {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                           "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)})
    return _outcome(r, res["total"], res["maps"])


CORRECTIONS = DB.parent / "settlement_corrections.jsonl"


def fix_scope_settlements(apply=False, scopes=("1", "123"), test=None, until=None, ids=None, bo3=None, map_rows=None):
    """2026-10-08 incident: recheck_with_hltv() re-settled Map 1 / Maps 1-3 picks with Maps 1-2 totals. Re-derive every
    settled non-Maps-1-2 pick from HLTV through scope_result(); with apply=True correct the mismatches and append the
    old and new values to settlement_corrections.jsonl (nothing is lost). Returns what differs / what was skipped.
    2026-10-12 owner review: a FROZEN cohort (until = settled_at cutoff and/or ids) so totals don't move between runs;
    picks HLTV can't verify (player/team not in HLTV) are checked against BO3.gg's full scoreboard (bo3 + map_rows:
    the original settlement source, by player id + match slug + map numbers); the summary says
    "N wrong among V verified; U unresolved"."""
    from scope import outcome
    want = {int(i) for i in ids} if ids else None
    with _LOCK:
        con = _db()
        rows = [x for x in con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push')").fetchall()
                if (x["scope"] or "12") in scopes and (test is None or (x["test"] or 0) == test)
                and (until is None or (x["settled_at"] or "") <= until) and (want is None or x["id"] in want)]
        con.close()
    diffs, skipped, details = [], [], []
    import time as _time
    import hltv as _hltv
    for r in rows:
        res, err = None, None
        for attempt in range(4):              # an audit waits out a database blip instead of skipping the rest (2026-10-12)
            try:
                res = scope_result(r)
                break
            except Exception as e:
                err = e
                if not _hltv.is_transient(e):
                    break
                _time.sleep(2 * (attempt + 1))
                _hltv.allow_probe()
        if res is None:
            skipped.append({"id": r["id"], "reason": f"error {err}"}); continue
        if res["status"] == "void":
            # 2026-10-11: a Maps 1-3 pick whose BO3 ended 2-0 (no map 3) is a void, not a 2-map total
            d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
                 "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
                 "new": {"actual": None, "status": "void", "hit": None}, "reason": res.get("reason")}
            diffs.append(d)
            if apply:
                with _LOCK:
                    con = _db()
                    con.execute("UPDATE picks SET status='void', hit=NULL WHERE id=?", (r["id"],))
                    con.commit()
                    con.close()
                    with open(CORRECTIONS, "a", encoding="utf-8") as f:
                        f.write(json.dumps(dict(d, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds")), default=str) + "\n")
            continue
        if res["status"] != "ok":
            skipped.append({"id": r["id"], "reason": res.get("reason")}); continue
        st, hit = outcome(res["total"], r["line"], r["pick"])
        new_detail = {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                      "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)}
        if res["total"] == r["actual"] and st == r["status"] and (hit == r["hit"] or (hit is None and r["hit"] is None)):
            # 2026-10-09: the result is right but the stored detail may be stale (the 2026-10-08 correction updated the
            # total only): if the stored maps don't reproduce the settled total under this scope's rules, refresh it
            try:
                old = json.loads(r["result"] or "{}")
            except ValueError:
                old = {}
            won = old.get("won") or []
            om = [{"number": i + 1, "kills": k, "headshots": h, **({"won": won[i]} if i < len(won) else {})}
                  for i, (k, h) in enumerate(old.get("per_map") or [])]
            from scope import scope_total
            t_old, _ = scope_total(om, r["scope"] or "12", "hs" if r["prop"] == "hs" else "kills") if om else (None, None)
            if t_old != r["actual"]:
                dd = {"id": r["id"], "player": r["player"], "scope": r["scope"], "actual": r["actual"], "old_detail": old, "new_detail": new_detail}
                details.append(dd)
                if apply:
                    with _LOCK:
                        con = _db()
                        con.execute("UPDATE picks SET result=? WHERE id=?", (json.dumps(new_detail, default=str), r["id"]))
                        con.commit()
                        con.close()
                        with open(CORRECTIONS, "a", encoding="utf-8") as f:
                            f.write(json.dumps(dict(dd, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                    reason="stale settlement detail (total already correct)"), default=str) + "\n")
            continue
        d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": res["total"], "status": st, "hit": hit}, "maps_used": res["maps_used"],
             "hltv_match": res["hltv_match"]}
        diffs.append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, actual_maps=?, result=? WHERE id=?",
                            (st, res["total"], hit, json.dumps(res["maps"]), json.dumps(new_detail, default=str), r["id"]))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                            reason="recheck_with_hltv used Maps 1-2 totals for a non-Maps-1-2 line")) + "\n")
    by_bo3, unresolved, disagree = 0, [], []
    byid = {r["id"]: r for r in rows}
    for sk in skipped:
        r = byid[sk["id"]]
        if bo3 is None or (r["scope"] or "12") != "12" or not r["player_id"] or not (r["match_slug"] or "") \
                or r["match_slug"].startswith("hltv-"):
            unresolved.append(sk); continue
        try:
            got = _bo3_totals(bo3, map_rows, r)
        except Exception as e:
            got = f"BO3.gg error {type(e).__name__}"
        if isinstance(got, str):
            unresolved.append(dict(sk, bo3=got)); continue
        by_bo3 += 1
        q, f = got["quick"], got["full"]
        if q is not None and f is not None and q != f:
            disagree.append({"id": r["id"], "player": r["player"], "line": r["line"], "stored": r["actual"], "quick": q, "full": f,
                             "line_between": _between(r["line"], q, f)})
        ref = q if q is not None else f                 # SOURCE_RULE: quick first
        upd = _outcome(r, ref, got["maps"])
        if ref == r["actual"] and upd[0] == r["status"] and upd[3] == r["hit"]:
            continue
        diffs.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": "12", "line": r["line"], "pick": r["pick"],
                      "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
                      "new": {"actual": ref, "status": upd[0], "hit": upd[3]},
                      "source": "bo3 quick scoreboard" if q is not None else "bo3 full scoreboard (no quick)"})
    verified = len(rows) - len(skipped) + by_bo3
    summary = {"cohort": len(rows), "settled_until": until or max((r["settled_at"] or "" for r in rows), default=None),
               "verified": verified, "verified_hltv": len(rows) - len(skipped), "verified_bo3": by_bo3,
               "wrong": len(diffs), "unresolved": len(unresolved),
               "line": f"{len(diffs)} wrong among {verified} verified; {len(unresolved)} unresolved"}
    summary["bo3_quick_full_disagree"] = len(disagree)
    summary["rule"] = SOURCE_RULE
    return {"summary": summary, "checked": len(rows), "differs": diffs, "stale_details": details, "skipped": unresolved,
            "bo3_disagreements": disagree, "applied": bool(apply) and False,
            "note": "BO3.gg-based differences are reported, never applied by the audit (provisional source)"}


def _bo3_totals(b, map_rows, r):
    """{"quick", "full", "maps"} totals of a Maps 1-2 pick from BO3.gg (the pick's BO3.gg player id, its exact match slug,
    maps 1 and 2 by number, the pick's team on the scoreboard) - or a reason string. Either total may be None."""
    import hltv
    ms = b.player_matches(r["player_id"], pages=3, ttl=1).get("results") or []
    mt = next((m for m in ms if m.get("slug") == r["match_slug"]), None)
    if not mt:
        return "BO3.gg match not in the player's list"
    stat = "headshots" if r["prop"] == "hs" else "kills"
    maps = sorted(map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2), key=lambda x: x.get("number") or 9)
    full = None
    if [x.get("number") for x in maps] == [1, 2] and all(x.get("rounds") for x in maps):
        if r["team"] and not (hltv.same_team(r["team"], maps[0].get("team") or "")
                              or (maps[0].get("team_slug") or "#") in (r["match_slug"] or "")):
            return f"BO3.gg team {maps[0].get('team')!r} is not the pick's team"
        if all(x.get(stat) is not None for x in maps):
            full = sum(x[stat] for x in maps)
    qr = _quick_rows(b, r["player_id"], mt)
    quick = sum(x[stat] for x in qr) if len(qr) == 2 and all(x[stat] is not None for x in qr) else None   # D13: missing != 0
    if quick is None and full is None:
        return "BO3.gg scoreboards for maps 1-2 not available"
    names = [x.get("map") for x in maps] if len(maps) == 2 else [x.get("map") for x in qr]
    return {"quick": quick, "full": full, "maps": names}


def _hltv_totals(r, same_day_only=False):
    """HLTV's result for a Maps 1-2 pick through the shared scope rules (scope_result): an update tuple, "wait" (match
    there, stat not yet), "transient" (HLTV unreachable) or None. Since 2026-10-08 it refuses ambiguous matches (two
    candidates used to take the first) and voids forfeits. 2026-10-13 audit D5: an outage was read as 'not in HLTV', so
    the BO3.gg fallback could settle or void the pick while HLTV was down."""
    try:
        res = scope_result(r, same_day_only=same_day_only)
    except Exception as e:
        import hltv
        return "transient" if hltv.is_transient(e) else None
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] == "wait":
        return "wait"
    if res["status"] != "ok":
        return None
    _save_result(r["id"], {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                           "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)})
    return _outcome(r, res["total"], res["maps"])


def recheck_with_hltv():
    """Admin: re-settle already-settled picks from HLTV where HLTV has the match (corrects BO3.gg).
    Returns the rows that changed."""
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push')").fetchall()
        con.close()
    changed = []
    for r in rows:
        if (_col(r, "scope") or "12") != "12":
            continue          # _hltv_totals is Maps 1-2 only - Map 1 / Maps 1-3 lines settle via scope_result() (incident 2026-10-08)
        hv = _hltv_totals(r)
        if isinstance(hv, tuple) and hv[0] == "void":
            # never silently void a settled pick here - listed for review by hand instead (2026-10-11 audit: a BO3.gg
            # settlement on a forfeited map used to stay settled with no sign of it)
            changed.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"], "pick": r["pick"],
                            "bo3": r["actual"], "hltv": None, "was_hit": r["hit"], "now_hit": None,
                            "review": "HLTV voids this match (forfeit / map not played) - settled from BO3.gg; review by hand"})
            continue
        if isinstance(hv, tuple) and (hv[2] != r["actual"] or hv[0] != r["status"]):
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=? WHERE id=?", (*hv, r["id"]))
                con.commit()
                con.close()
            changed.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"],
                            "pick": r["pick"], "bo3": r["actual"], "hltv": hv[2], "was_hit": r["hit"], "now_hit": hv[3]})
    return changed


def _settle_hltv(r, now, start):
    """A match only HLTV has: Maps 1-2 kills / headshots from the hltv-scraper database.
    None = not yet (stats or headshots not scraped); void after VOID_AFTER_D days without them."""
    import hltv
    tot, transient = {"found": False}, False
    for team in _settle_teams(r):                     # the match's team first, then the BO3.gg profile team (2026-10-13)
        try:
            tot = hltv.settle_totals(r["player"], team, int(r["match_slug"].split("-", 1)[1]))
        except Exception as e:
            tot = {"found": False, "transient": hltv.is_transient(e)}
        transient = transient or bool(tot.get("transient"))
        if tot.get("found"):
            break
    if not tot.get("found") and transient:
        tot = dict(tot, transient=True)
    actual = tot.get("headshots" if r["prop"] == "hs" else "kills") if tot.get("found") else None
    if actual is None and tot.get("transient"):
        return None                                   # HLTV unreachable: never a void on that (2026-10-11 audit)
    try:                                              # a forfeited map voids the pick (2026-10-08)
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                if _forfeited(cur, int(r["match_slug"].split("-", 1)[1])):
                    return ("void", None, None, None)
        finally:
            con.close()
    except Exception as e:
        if hltv.is_transient(e):
            return None                               # forfeit unknown while HLTV is unreachable: next pass (2026-10-13 audit)
    if actual is None:
        return ("void", None, None, None) if now > start + timedelta(days=VOID_AFTER_D) else None
    maps = json.dumps(tot.get("maps"))
    if actual == r["line"]:
        return ("push", maps, actual, None)
    hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
    return ("settled", maps, actual, int(hit))


def set_closing(pick_id, closing):
    """CLV: positive = the line moved toward your side after you logged it."""
    with _LOCK:
        con = _db()
        r = con.execute("SELECT line, pick FROM picks WHERE id = ?", (pick_id,)).fetchone()
        if r:
            clv = (closing - r["line"]) if r["pick"] == "More" else (r["line"] - closing)
            con.execute("UPDATE picks SET closing_line = ?, clv = ? WHERE id = ?", (closing, round(clv, 2), pick_id))
            con.commit()
        con.close()


def resettle_pushes():
    """One-off: rows settled as a miss when the total landed exactly on a whole-number line."""
    with _LOCK:
        con = _db()
        n = con.execute("UPDATE picks SET status='push', hit=NULL WHERE status='settled' AND actual = line").rowcount
        con.commit()
        con.close()
    return n


def reopen(ids):
    """Admin: put rows back to open (e.g. voided before BO3.gg published the map stats)."""
    with _LOCK:
        con = _db()
        con.executemany("UPDATE picks SET status='open', actual=NULL, hit=NULL, actual_maps=NULL, settled_at=NULL "
                        "WHERE id = ?", [(i,) for i in ids])
        con.commit()
        con.close()


def mark_tests(ids):
    """Admin: flag rows that came from the owner's own testing (not members' real lines)."""
    with _LOCK:
        con = _db()
        con.executemany("UPDATE picks SET test = 1 WHERE id = ?", [(i,) for i in ids])
        con.commit()
        con.close()


def export():
    with _LOCK:
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM picks ORDER BY id")]
        con.close()
    return rows


def export_forecasts(inputs=False, last=None):
    """The forecast history; inputs=True decodes each forecast's exact grader inputs (large)."""
    import zlib
    with _LOCK:
        con = _db()
        q = "SELECT * FROM forecasts ORDER BY id" + (f" DESC LIMIT {int(last)}" if last else "")
        rows = [dict(r) for r in con.execute(q)]
        con.close()
    for r in rows:
        blob = r.pop("inputs", None)
        if inputs and blob:
            try:
                r["inputs"] = json.loads(zlib.decompress(blob))
            except Exception:
                r["inputs"] = None
        elif inputs:
            r["inputs"] = None
        else:
            r["has_inputs"] = blob is not None
    return rows


def report():
    with _LOCK:
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM picks WHERE COALESCE(test, 0) = 0 ORDER BY id DESC")]
        con.close()
    settled = [r for r in rows if r["status"] == "settled"]

    def bucket(rs, key, bounds):
        out = []
        for lo, hi, name in bounds:
            b_ = [r for r in rs if lo <= (r[key] or 0) < hi]
            if b_:
                out.append({"bucket": name, "n": len(b_),
                            "printed": round(sum(r["p_pick"] for r in b_) / len(b_), 3),
                            "actual": round(sum(r["hit"] for r in b_) / len(b_), 3)})
        return out

    pbounds = ((0.5, 0.57, "C 50-57%"), (0.57, 0.63, "B 57-63%"), (0.63, 0.68, "B+ 63-68%"),
               (0.68, 0.74, "A- 68-74%"), (0.74, 0.80, "A 74-80%"), (0.80, 1.01, "A+ 80%+"))
    ubounds = ((0, 0.01, "0U"), (0.5, 0.51, "0.5U"), (1, 1.01, "1U"), (1.5, 1.51, "1.5U"), (2, 2.01, "2U"))
    units_won = sum((r["units"] or 0) * (1 if r["hit"] else -1) for r in settled)
    clv = [r["clv"] for r in rows if r["clv"] is not None]
    return {
        "total": len(rows), "open": sum(r["status"] == "open" for r in rows), "settled": len(settled),
        "void": sum(r["status"] == "void" for r in rows),
        "push": sum(r["status"] == "push" for r in rows),
        "by_printed_pct": bucket(settled, "p_pick", pbounds),
        "by_units": bucket(settled, "units", ubounds),
        "hit_rate_all": round(sum(r["hit"] for r in settled) / len(settled), 3) if settled else None,
        "units_net_flat": round(units_won, 2),
        "clv_avg": round(sum(clv) / len(clv), 2) if clv else None, "clv_n": len(clv),
        "clv_positive": round(sum(c > 0 for c in clv) / len(clv), 3) if clv else None,
        "rows": rows[:300],
    }
