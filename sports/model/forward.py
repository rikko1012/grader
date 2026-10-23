"""Forward (prospective) forecasting: the frozen sm-1 artifact, research forecasts for stored offers, saved inputs and
replay. Worker only (python -m sports.worker train fit | forecast). Writes Forecast records through sports.store (the
foundation contract: immutable, content-hashed ids, versions, unconditional probabilities).

Artifact = the tune-chosen configuration (half-lives, kept features, family, cal-1) REFIT on every eligible ds-1 row
known at fit time (REGISTER.md 9). Its sha256 prefix is artifact_version on every forecast.

Saved inputs (model/forecasts.db, table inputs): the forecast's complete feature row + artifact sha + market inputs.
replay(forecast_id) rebuilds the PMF and the Forecast from those saved inputs alone and must be identical.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np

from .. import capabilities, config, identity, store
from ..contracts import Forecast, now_utc, parse_utc
from . import (GRADE_POLICY_VERSION, MARKET_INFORMED_VERSION, MODEL_VERSION, STAKE_POLICY_VERSION, datasets, dists,
               featurize, grade_policy, market, model, study)
from .frame import Frame

ART_DIR = Path(__file__).resolve().parent / "artifacts"
TARGETS = ("prizepicks", "underdog")
SPORT_MARKETS = {"nba": list(featurize.NBA_MARKETS), "nfl": list(featurize.NFL_MARKETS)}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def art_path(sport):
    return ART_DIR / f"sm1_{sport}.json"


# ------------------------------------------------------------------------------------------------------------- fit
def fit(sport, hcon, as_of=None, out_path=None):
    """Refit the frozen configuration on all eligible rows known at as_of -> artifact dict (written to out_path)."""
    cfg = json.loads((ART_DIR / f"model_config_{sport}.json").read_text(encoding="utf-8"))
    as_of = as_of or now_utc()
    arts = {}
    frames = {}
    for mk in SPORT_MARKETS[sport]:
        c = cfg["markets"][mk]
        hl = tuple(c["half_lives"])
        if hl not in frames:
            df = study._frame(sport, hcon, hl) if as_of is None else _frame_asof(sport, hcon, hl, as_of)
            frames[hl] = study._nfl_fill(df) if sport == "nfl" else df
        df = frames[hl]
        el = model.eligible(df, mk)
        tr = df[el & np.isfinite(df.f(f"y_{mk}"))]
        f = study.Fitted(mk, hl)
        study.mean_model(f, tr, tr, mk, sport, list(c["features"]))
        study.fit_family_params(f, tr, mk, sport)
        f.family = c["family"]
        f.cal = tuple(c["cal"])                                   # cal-1 stays the tune-fitted one (frozen)
        conf = f.config()
        conf.pop("mi_hist", None)
        conf["fit_rows"] = int(len(tr))
        conf["fit_games"] = int(len(np.unique(tr["game_id"])))
        arts[mk] = conf
    art = {"model_version": MODEL_VERSION, "sport": sport, "fitted_at": now_utc(), "data_as_of": as_of,
           "config_sha": _sha(cfg)[:16], "dataset": datasets.coverage(hcon).get(sport if sport == "nfl" else "nba"),
           "markets": arts}
    art["sha"] = _sha(art["markets"])[:16]
    p = Path(out_path) if out_path else art_path(sport)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(art, indent=1, sort_keys=True, default=float), encoding="utf-8")
    return art


def _frame_asof(sport, hcon, hl, as_of, upcoming=None, absences=None):
    if sport == "nba":
        return featurize.nba_frame(hcon, h_min=hl[0], h_rate=hl[1], upcoming=upcoming, as_of=as_of,
                                   absences=absences)
    return featurize.nfl_frame(hcon, h_opp=hl[0], upcoming=upcoming, as_of=as_of)


def load_artifact(sport):
    p = art_path(sport)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------------------------------------- saved inputs db
def inputs_db():
    p = config.DATA_DIR / "model" / "forecasts.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS inputs (forecast_id TEXT PRIMARY KEY, model_version TEXT, league TEXT, "
                "market TEXT, offer_record_id TEXT, artifact_sha TEXT, forecast_at TEXT, row_json TEXT, "
                "market_json TEXT, pmf_lo INTEGER, pmf_json TEXT, policy_json TEXT, created_at TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS inputs_offer ON inputs (offer_record_id)")
    return con


# ---------------------------------------------------------------------------------------------- upcoming rows
def _teams(con, league):
    return {store._rec(r).team_id: store._rec(r).abbr for r in store._latest_rows(con, "team", "league = ?", [league],
                                                                                    None, 1000)}


def _current_team(con, league):
    rows = con.execute("SELECT r.payload FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records "
                       "WHERE kind='membership' AND player_id LIKE ? GROUP BY source, source_key) z ON "
                       "r.source=z.source AND r.source_key=z.source_key AND r.revision=z.rv WHERE r.kind='membership'",
                       (f"{league}:%",)).fetchall()
    out = {}
    for row in rows:
        m = store._rec(row)
        if m.valid_to is None:
            out[m.player_id] = m.team_id
    return out


def upcoming_rows(scon, hcon, league, now):
    """(event, player) pairs of gradable target offers whose event has not started -> ds-1-shaped upcoming rows.
    -> (rows, {(event_id, player_id): ds1 player id}, skipped reasons)."""
    events = {e.event_id: e for _, e in store.events(scon, league)}
    abbr = _teams(scon, league)
    team_of = _current_team(scon, league)
    nfl_games, gsis = {}, {}
    if league == "nfl":
        for r in hcon.execute("SELECT game_id, espn_id, home, away, season, week, season_type FROM games "
                              "WHERE league='nfl' AND espn_id IS NOT NULL"):
            nfl_games[f"nfl:espn:{r['espn_id']}"] = dict(r)
        for r in hcon.execute("SELECT gsis_id, espn_id FROM nfl_ids WHERE espn_id IS NOT NULL AND espn_id != ''"):
            gsis.setdefault(str(r["espn_id"]), []).append(r["gsis_id"])
    pos = {}
    tbl = "nba_pg" if league == "nba" else "nfl_pg"
    for r in hcon.execute(f"SELECT player_id, position FROM {tbl} WHERE position IS NOT NULL AND position != ''"):
        pos[r["player_id"]] = r["position"]
    rows, idmap, skipped = {}, {}, {}
    for rid, o in store.offers(scon, league=league, limit=500000):
        if o.platform not in TARGETS:
            continue
        ok, why = capabilities.gate(o)
        ev = events.get(o.event_id)
        if not ok or ev is None or not ev.start_utc or ev.start_utc <= now:
            continue
        key = (o.event_id, o.player_id)
        if key in idmap or key in skipped:
            continue
        team_id = team_of.get(o.player_id)
        if team_id not in (ev.home_team_id, ev.away_team_id):
            skipped[key] = "player's current ESPN team is not in this event"
            continue
        home = 1 if team_id == ev.home_team_id else 0
        opp_id = ev.away_team_id if home else ev.home_team_id
        if league == "nba":
            pid = o.player_id
            gid, team, opp = o.event_id, abbr.get(team_id), abbr.get(opp_id)
            extra = {"season": ev.season, "season_type": ev.season_type}
        else:
            g = nfl_games.get(o.event_id)
            ids = gsis.get(o.player_id.split(":")[-1], [])
            if not g or len(ids) != 1:
                skipped[key] = "no nflverse game for this ESPN event" if not g else "ESPN id not mapped to one gsis id"
                continue
            pid, gid = ids[0], g["game_id"]
            team, opp = (g["home"], g["away"]) if home else (g["away"], g["home"])
            extra = {"season": g["season"], "week": g["week"], "season_type": g["season_type"]}
        if not team or not opp:
            skipped[key] = "team abbreviation unknown"
            continue
        rows[key] = {"game_id": gid, "player_id": pid, "team": team, "opp": opp, "home": home,
                     "start_utc": ev.start_utc, "position": pos.get(pid, ""), **extra}
        idmap[key] = pid
    return list(rows.values()), idmap, skipped


REPORT_MAX_AGE_H = 24


def injury_absences(scon, league, rows, forecast_at):
    """{(game_id, team): [ds-1 player ids listed Out]} from the ESPN injury report in the store, known at forecast_at
    and read within REPORT_MAX_AGE_H; a team without a fresh report -> None (unknown)."""
    if league != "nba":
        return {}
    recs = [store._rec(r) for r in store._latest_rows(scon, "context", "league = ?", [league], forecast_at, 20000)]
    fresh = [c for c in recs if (parse_utc(forecast_at) - parse_utc(c.observed_at)).total_seconds()
             <= REPORT_MAX_AGE_H * 3600]
    team_of = _current_team(scon, league)
    abbr = _teams(scon, league)
    seen_report = bool(fresh)                     # ESPN publishes one league-wide report: a fresh read covers every team
    out_by_team = {}
    for c in fresh:
        if c.status == "out" and c.present == "yes":
            t = abbr.get(team_of.get(c.subject_id))
            if t:
                out_by_team.setdefault(t, []).append(c.subject_id)
    res = {}
    for r in rows:
        for team in (r["team"], r["opp"]):
            res[(r["game_id"], team)] = out_by_team.get(team, []) if seen_report else None
    return res


# ---------------------------------------------------------------------------------------------- forecasting
def _row_dict(df, i):
    out = {}
    for c, v in df.cols.items():
        x = v[i]
        out[c] = None if (isinstance(x, float) and x != x) else (x.item() if hasattr(x, "item") else x)
    return out


def pmf_from_row(art_m, row, market_key, sport):
    """The calibrated PMF for one saved row (replay path: no dataset needed)."""
    f = study.from_config(art_m)
    one = Frame({k: np.array([np.nan if v is None else v], dtype=float) if isinstance(v, (int, float)) or v is None
                 else np.array([v], dtype=object) for k, v in row.items()})
    p, mu = study.pmf_for(f, one, market_key, sport)
    return p[0], float(mu[0])


def _validation_state(model_version, market_key):
    reg = registry()
    return (reg.get("versions", {}).get(model_version, {}).get("validation", {}) or {}).get(market_key,
                                                                                             "unvalidated")


def registry():
    p = ART_DIR / "registry.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_forecast(o, rid, forecast_at, model_version, artifact_version, pmf, lo, mean, input_ids, codes,
                   data_version, calibration_version, extra_dist=(), vstate=None):
    ks = np.arange(lo, lo + len(pmf))
    po, pu, pp = dists.line_probs(pmf, lo, o.line)
    sides = o.offered_sides()
    pol = grade_policy.apply(po, pu, sides, vstate or _validation_state(model_version, o.stat))
    dec = 1 - pp
    med = float(dists.quantile(pmf[None, :], ks.astype(float), 0.5)[0])
    fc = Forecast.build(
        forecast_at=forecast_at, league=o.league, event_id=o.event_id, player_id=o.player_id,
        player_key=o.player_key, stat=o.stat, scope=o.scope, line=o.line, offer_snapshot_ids=(rid,),
        input_snapshot_ids=tuple(input_ids), model_version=model_version, artifact_version=artifact_version,
        data_version=data_version, calibration_version=calibration_version, grade_policy_version=GRADE_POLICY_VERSION,
        projection_mean=round(float(mean), 6), projection_median=med,
        distribution=(("family", "pmf"), ("pmf_lo", int(lo)), ("pmf_sha", _sha([round(float(x), 15) for x in pmf])[:16]))
        + tuple(extra_dist),
        p_over=po, p_under=pu, p_push=pp, p_over_given_no_push=po / dec if dec > 0 else None,
        p_under_given_no_push=pu / dec if dec > 0 else None, sides_available=sides,
        chosen_side=pol["chosen_side"], grade=pol["grade"], stake_units=pol["stake_units"],
        stake_policy_version=STAKE_POLICY_VERSION, reason_codes=tuple(codes) + (f"gp:{pol['validation_state']}",
                                                                              f"gp_band:{pol['band']}"),
        seed=0, research_only=True, observed_at=forecast_at, known_at=forecast_at)
    return fc, pol


def forecast_league(scon, hcon, league, forecast_at=None, log=print):
    """sm-1 + mi-1 + mc-1 + single-source market forecasts for every gradable target offer of `league`."""
    forecast_at = forecast_at or now_utc()
    art = load_artifact(league)
    if art is None:
        return {"error": f"no artifact for {league}: run `train fit` first"}
    up, idmap, skipped = upcoming_rows(scon, hcon, league, forecast_at)
    out = {"league": league, "forecast_at": forecast_at, "artifact": art["sha"], "upcoming_rows": len(up),
           "skipped": {}, "written": {}, "no_forecast": {}}
    for why in skipped.values():
        out["skipped"][why] = out["skipped"].get(why, 0) + 1
    if not up:
        return out
    events = {e.event_id: e for _, e in store.events(scon, league)}
    quotes = {}
    for rid, q in store.offers(scon, league=league, limit=500000):
        if q.platform in TARGETS or q.event_id is None or q.player_id is None:
            continue
        meta = store.row_meta(scon, rid)
        quotes.setdefault((q.event_id, q.player_id, q.stat), []).append((rid, q, meta.get("last_seen_at")))
    absences = injury_absences(scon, league, up, forecast_at)
    out["injury_report"] = "fresh ESPN report" if any(v is not None for v in absences.values()) else         ("no fresh report: same-game absence unknown" if league == "nba" else "not used")
    mcfg = market_config()
    dnp = mcfg.get("dnp_share", {}).get(league, 0.0)
    icon = inputs_db()
    frames = {}
    for mk in SPORT_MARKETS[league]:
        am = art["markets"][mk]
        hl = tuple(am["half_lives"])
        if hl not in frames:
            df = _frame_asof(league, hcon, hl, forecast_at, upcoming=up, absences=absences)
            frames[hl] = study._nfl_fill(df) if league == "nfl" else df
        df = frames[hl]
        upi = np.flatnonzero(df["is_up"] == 1)
        el = model.eligible(df, mk)
        rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
        f = study.from_config(am)
        lo, ks = dists.grid(mk)
        # each forecast's PMF is computed from its OWN saved row (pmf_from_row) - exactly the replay path, so a
        # replay is bit-identical (a batched matrix product rounds the last bit differently from a one-row one)
        pmf_of = {}
        for i in upi:
            pmf_i, mu_i = pmf_from_row(am, _row_dict(df, i), mk, league)
            pmf_of[(df["game_id"][i], df["player_id"][i])] = (pmf_i, mu_i, bool(el[i]))
        for rid, o in store.offers(scon, league=league, stat=mk, limit=500000):
            if o.platform not in TARGETS or (o.event_id, o.player_id) not in idmap:
                continue
            ok, _ = capabilities.gate(o)
            ev = events.get(o.event_id)
            if not ok or ev is None or ev.start_utc <= forecast_at:
                continue
            urow = next((r for r in up if r["player_id"] == idmap[(o.event_id, o.player_id)] and
                         r["start_utc"] == ev.start_utc), None)
            gk = (urow["game_id"], urow["player_id"]) if urow else None
            if gk not in pmf_of:
                continue
            pmf, mean, eligible = pmf_of[gk]
            row = _row_dict(df, rowpos[gk])
            codes = ["statistical_only", "deterministic_quadrature"]
            if not eligible:
                codes.append("outside_validated_population")
            dq = row.get("prior_n") or 0
            if (league == "nba" and dq < 10) or (league == "nfl" and dq < 6):
                codes.append("thin_history")
            if league == "nba" and row.get("absent_share") is None:
                codes.append("teammate_absence_unknown")
            data_version = "ds-1:" + _sha(row)[:16]
            written = []
            fc, pol = build_forecast(o, rid, forecast_at, MODEL_VERSION, f"sm-1:{art['sha']}", pmf, lo, mean, (rid,),
                                     codes, data_version, f"cal-1:{am['cal'][0]:g},{am['cal'][1]:g}",
                                     (("family_fit", am["family"]),))
            written.append(("sm-1", fc, pol, pmf, {"row": row}))
            # market candidates on the same offer
            per, reasons = market.eligible_quotes(o, quotes.get((o.event_id, o.player_id, o.stat), []), events,
                                                  forecast_at, dnp)
            qids = tuple(v[0] for v in per.values())
            if per:
                mi = market.market_informed(pmf, lo, mean, per)
                if mi is not None:
                    pmf_mi, cfac = mi
                    fc2, pol2 = build_forecast(o, rid, forecast_at, MARKET_INFORMED_VERSION, f"sm-1:{art['sha']}",
                                               pmf_mi, lo, float(dists.mean_of(pmf_mi[None, :], ks)[0]),
                                               (rid,) + qids, ["market_informed", "sources:" + "+".join(sorted(per))],
                                               data_version, f"mi-1:c={cfac:.6f}")
                    written.append(("mi-1", fc2, pol2, pmf_mi, {"row": row, "sources": per}))
                for name, srcs in [(market.VERSION_CONSENSUS, None)] + [(f"mkt-{s}", [s]) for s in sorted(per)]:
                    res = market.consensus(o, per, srcs)
                    if res is None:
                        continue
                    po, pu, pp, used = res
                    fc3 = _market_forecast(o, rid, forecast_at, name, po, pu, pp, (rid,) + tuple(per[s][0] for s in used),
                                           used, mcfg)
                    pol3 = grade_policy.apply(po, pu, o.offered_sides(), _validation_state(name, o.stat))
                    written.append((name, fc3, pol3, None, {"sources": {s: per[s] for s in used}}))
            else:
                out["no_forecast"]["market: " + (reasons[0].split(": ", 1)[-1] if reasons else "no quote")] = \
                    out["no_forecast"].get("market: " + (reasons[0].split(": ", 1)[-1] if reasons else "no quote"), 0) + 1
            for name, fcx, polx, pmfx, inp in written:
                store.assert_known_before(scon, fcx.input_snapshot_ids, forecast_at)
                res = store.put(scon, fcx)
                if res["status"] == "inserted":
                    icon.execute("INSERT OR IGNORE INTO inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (fcx.forecast_id, name, league, mk, rid, art["sha"], forecast_at,
                                  json.dumps(inp.get("row"), default=str),
                                  json.dumps(inp.get("sources"), default=str), lo if pmfx is not None else None,
                                  json.dumps([float(x) for x in pmfx]) if pmfx is not None else None,
                                  json.dumps(polx, default=str), now_utc()))
                out["written"][name] = out["written"].get(name, 0) + (1 if res["status"] == "inserted" else 0)
        icon.commit()
        log({"league": league, "market": mk, "written": out["written"]})
    icon.close()
    return out


def _market_forecast(o, rid, forecast_at, name, po, pu, pp, input_ids, used, mcfg):
    dec = 1 - pp
    sides = o.offered_sides()
    chosen = max(sides, key=lambda s: po if s == "over" else pu) if sides else None
    return Forecast.build(
        forecast_at=forecast_at, league=o.league, event_id=o.event_id, player_id=o.player_id, player_key=o.player_key,
        stat=o.stat, scope=o.scope, line=o.line, offer_snapshot_ids=(rid,), input_snapshot_ids=tuple(input_ids),
        model_version=name, artifact_version=f"mc-1:{mcfg.get('sha')}", data_version="quotes",
        calibration_version=None, grade_policy_version=GRADE_POLICY_VERSION, projection_mean=None,
        projection_median=None, distribution=(("family", "dispersion.json shape"), ("sources", "+".join(used))),
        p_over=po, p_under=pu, p_push=pp, p_over_given_no_push=po / dec if dec > 0 else None,
        p_under_given_no_push=pu / dec if dec > 0 else None, sides_available=sides, chosen_side=chosen, grade=None,
        stake_units=0.0, stake_policy_version=STAKE_POLICY_VERSION,
        reason_codes=("market_only", "consensus" if len(used) > 1 else "single_source", "gp:unvalidated"),
        seed=0, research_only=True, observed_at=forecast_at, known_at=forecast_at)


def market_config():
    p = ART_DIR / "market_mc1.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"dnp_share": {"nba": 0.0, "nfl": 0.0}}


# --------------------------------------------------------------------------------------------------------- replay
def replay(forecast_id, scon=None):
    """Recompute a saved sm-1 / mi-1 forecast from its saved inputs + artifact. -> (original, replayed)."""
    icon = inputs_db()
    r = icon.execute("SELECT * FROM inputs WHERE forecast_id = ?", (forecast_id,)).fetchone()
    icon.close()
    if r is None:
        raise KeyError(forecast_id)
    cols = ["forecast_id", "model_version", "league", "market", "offer_record_id", "artifact_sha", "forecast_at",
            "row_json", "market_json", "pmf_lo", "pmf_json", "policy_json", "created_at"]
    r = dict(zip(cols, r))
    art = load_artifact(r["league"])
    if art is None or art["sha"] != r["artifact_sha"]:
        raise RuntimeError("artifact changed since this forecast (replay needs the saved artifact version)")
    own = scon is None
    scon = scon or store.connect(read_only=True)
    try:
        orig = store.get(scon, forecast_id)
        offer = store.get(scon, r["offer_record_id"])
    finally:
        if own:
            scon.close()
    row = json.loads(r["row_json"])
    vstate = json.loads(r["policy_json"]).get("validation_state")
    pmf, mean = pmf_from_row(art["markets"][r["market"]], row, r["market"], r["league"])
    am = art["markets"][r["market"]]
    lo, ks = dists.grid(r["market"])
    if r["model_version"] == MODEL_VERSION:
        fc, _ = build_forecast(offer, r["offer_record_id"], r["forecast_at"], MODEL_VERSION, f"sm-1:{art['sha']}", pmf,
                               lo, mean, (r["offer_record_id"],), list(_codes(orig)), "ds-1:" + _sha(row)[:16],
                               f"cal-1:{am['cal'][0]:g},{am['cal'][1]:g}", (("family_fit", am["family"]),), vstate)
    else:
        per = {k: tuple(v) for k, v in json.loads(r["market_json"]).items()}
        pmf_mi, cfac = market.market_informed(pmf, lo, mean, per)
        fc, _ = build_forecast(offer, r["offer_record_id"], r["forecast_at"], MARKET_INFORMED_VERSION,
                               f"sm-1:{art['sha']}", pmf_mi, lo, float(dists.mean_of(pmf_mi[None, :], ks)[0]),
                               (r["offer_record_id"],) + tuple(v[0] for v in per.values()),
                               ["market_informed", "sources:" + "+".join(sorted(per))], "ds-1:" + _sha(row)[:16],
                               f"mi-1:c={cfac:.6f}", (), vstate)
    return orig, fc


def _codes(fc):
    return tuple(c for c in fc.reason_codes if not c.startswith("gp"))
