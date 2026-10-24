"""Scout Sports UI snapshots (ui-snap-1): the JSON files the member pages read, built in the WORKER process.

The web process never queries the store for a member page: sports/api_board.py only reads these files (bytes, cached by
mtime). One build per league writes, atomically (temp file + os.replace):

  <SPORTS_DATA_DIR>/snapshots/<league>/manifest.json   built_at, versions, counts, per-source freshness
  .../slate.json        games (local time is the browser's job; UTC + provenance here), injuries, coverage per game
  .../board.json        one row per MARKET (event + player + stat + scope, ordinary lines) with every book's offer of
                        that market, the forecast attached to each offer, alternates / secondary stats counted
  .../forecasts.json    every displayed forecast, compact, keyed by forecast_id (the one copy every surface renders)
  .../players/<id>.json the Player Workspace: all offers (alternates, full Kalshi ladder), forecasts with their
                        distribution / alternate-line ladder, context, descriptive factors with sample sizes
  .../history/<id>.json per-game history (typed StatObservation record ids per value)
  .../results.json      settled forecasts: projection accuracy, probability accuracy, recorded payouts - separate

Rules
- Every number in a snapshot is copied from a typed record (with its record id) or computed from listed records
  (window statistics, accuracy) - never invented. Unknown stays None.
- A forecast is attached to an offer only when it was made FOR that offer: the same record id, or an earlier revision
  of the same provider offer with the same line, stat, scope and offered sides (flagged `for_revision`).
- The alternate-line ladder is read from the forecast's own distribution reference; it is published only when that
  distribution reproduces the forecast's stored chance at its own line (within LADDER_TOL) - otherwise it is withheld
  and the reason says so.
- Cross-book comparison: offers share a board row only when capabilities.compatible() says they are the same market.

History ingestion (ingest_history): ESPN public game logs -> typed records with source "espn_gamelog" (Event with
start_source "espn_gamelog", a dated one-game Membership for the player's team that night, StatObservation per box-score
part). The settlement box score (source "espn") wins over the game log when both exist for an event.

CLI (worker only, SPORTS_INGEST=1):
  python -m sports.snapshots build nba nfl [--now 2026-10-23T22:00:00Z]
  python -m sports.snapshots history nba nfl [--cache-only] [--max-players 400]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from . import cache, capabilities, config, fair, flags, forecast_detail, identity, market_view, markets, store
from .contracts import (ContractError, Event, Membership, SourceId, StatObservation, from_dict, now_utc, parse_utc,
                        utc)

SNAP_VERSION = "ui-snap-1"
HISTORY_SOURCE = "espn_gamelog"
FAMILY = {"nba": "basketball", "nfl": "football"}
LEAGUES = ("nba", "nfl")
SLATE_DAYS = {"nba": 2, "nfl": 8}                  # how far ahead the slate looks (US dates)
STARTED_KEEP_H = 4                                 # a started game stays on the slate this long (shown as started)
LADDER_TOL = 0.005                                 # distribution must reproduce the stored chance within 0.5 points
MAX_GAMES = 60
# display preference when several models forecast one offer: Scout's own model first (owner 2026-10-24: a projection
# is not a market estimate; market estimates are shown separately as "Market estimate")
DEFAULT_MODELS = ("sm-1", "mi-1", "sharp-2")
PRIMARY_STATS = capabilities.SUPPORTED
EXTRA_PAIRS = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
               "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta")}
FACTOR_PARTS = {
    "basketball": [("min", "Minutes"), ("fga", "Field-goal attempts"), ("fg3a", "3-point attempts"),
                   ("fta", "Free-throw attempts")],
    "football": [("targets", "Targets"), ("rec", "Receptions"), ("rush_att", "Carries"), ("pass_att", "Pass attempts")],
}
# owner factor list (OWNER_FACTORS.md, 2026-10-23) entries Scout does not hold - stated as not held, never shown as known
NOT_HELD = {          # fallback only; the player file lists player_context.NOT_HELD with a reason each
    "basketball": ["Likely defender", "On/off splits", "Potential assists"],
    "football": ["Coverage scheme (licensed)", "PFF / NGS / DVOA (licensed)", "Defender matchup (licensed)",
                 "Route participation / TPRR"],
}


def models_pref():
    env = os.environ.get("SPORTS_UI_MODELS", "").strip()
    return tuple(x.strip() for x in env.split(",") if x.strip()) or DEFAULT_MODELS


def snap_root(base=None) -> Path:
    return Path(base) if base else config.DATA_DIR / "snapshots"


def safe_name(record_key: str) -> str:
    if not re.fullmatch(r"[a-z]{2,4}:[a-z]+:[A-Za-z0-9:._-]{1,80}", record_key or ""):
        raise ValueError(f"bad id {record_key!r}")
    return record_key.replace(":", "_")


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def _r(x, n=4):
    return None if x is None else round(float(x), n)


# ---------------------------------------------------------------------------------------------- store reads (worker)
def _latest(con, kind, where="", args=(), as_of=None, limit=10 ** 7):
    """Latest revision per (source, source_key) among rows of `kind` matching `where`, known at as_of.
    One indexed scan, reduced in Python (SQLite's plan for the GROUP BY self-join scanned every row of the kind).
    Same CURRENT rule as store.offers (store.CURRENT_SQL: late, delayed responses are history, never current)."""
    cond = f"kind = ? AND {store.CURRENT_SQL}" + (f" AND {where}" if where else "")
    a = [kind, *args]
    if as_of:
        cond += " AND known_at <= ?"
        a.append(as_of)
    best = {}
    for row in con.execute(f"SELECT record_id, source, source_key, revision, known_at, last_seen_at, "
                           f"last_source_updated_at FROM records WHERE {cond}", a):
        k = (row["source"], row["source_key"])
        cur = best.get(k)
        if cur is None or row["revision"] > cur["revision"]:
            best[k] = row
    out = []
    rows = list(best.values())[:int(limit)]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        pay = {r["record_id"]: r["payload"] for r in con.execute(
            f"SELECT record_id, payload FROM records WHERE record_id IN ({','.join('?' * len(chunk))})",
            [r["record_id"] for r in chunk])}
        for row in chunk:
            out.append((row["record_id"], from_dict(store._unpack(pay[row["record_id"]])),
                        {"known_at": row["known_at"], "revision": row["revision"], "last_seen_at": row["last_seen_at"],
                         "last_source_updated_at": row["last_source_updated_at"], "source_key": row["source_key"]}))
    return out


def _all_forecasts(con, league, as_of):
    rows = con.execute("SELECT record_id, payload FROM records WHERE kind='forecast' AND league=? AND known_at <= ?",
                       (league, as_of)).fetchall()
    return [from_dict(store._unpack(r["payload"])) for r in rows]


def _offer_revisions(con, league, as_of):
    """record_id -> (platform, offer_id, revision) for every stored offer revision (to attach older-revision forecasts)."""
    out = {}
    for r in con.execute("SELECT record_id, source, source_key, revision FROM records WHERE kind='offer' AND league=? "
                         "AND known_at <= ?", (league, as_of)):
        out[r["record_id"]] = (r["source"], r["source_key"], r["revision"])
    return out


# ------------------------------------------------------------------------------------------------ history ingestion
def _part_unit(p):
    return "minutes" if p == "min" else ("yards" if p.endswith("_yds") else "count")


def gamelog_records(league, athlete_id, body, observed_at):
    """One ESPN game-log answer -> (events, memberships, stats) typed records. Preseason left out (espn.parse_gamelog)."""
    from . import espn
    fam = FAMILY[league]
    pid = identity.player_id(league, athlete_id)
    meta = body.get("events") or {}
    names = body.get("names") or []
    extra = {}
    for st in body.get("seasonTypes") or []:
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                e = {}
                for n, s in zip(names, ev.get("stats") or []):
                    if n in EXTRA_PAIRS:
                        a, b = markets._made(s)
                        if a is not None:
                            e[EXTRA_PAIRS[n][0]], e[EXTRA_PAIRS[n][1]] = a, b
                extra[ev.get("eventId")] = e
    season = espn._season_of(body)
    evs, mems, stats = [], [], []
    for g in espn.parse_gamelog(body, fam):
        m = meta.get(g["event_id"]) or {}
        start = utc(g["date"]) if g.get("date") else None
        if not start or not g.get("event_id"):
            continue
        eid = f"{league}:espn:{g['event_id']}"
        home = m.get("homeTeamId")
        away = m.get("awayTeamId")
        team = (m.get("team") or {}).get("id")
        evs.append(Event(event_id=eid, league=league, season=season, season_type="postseason" if g.get("postseason")
                         else "regular", home_team_id=identity.team_id(league, home) if home else None,
                         away_team_id=identity.team_id(league, away) if away else None, start_utc=start,
                         start_source=HISTORY_SOURCE, status="final", source_ids=(SourceId("espn", g["event_id"]),),
                         settlement_source="espn", observed_at=observed_at, known_at=observed_at))
        if team:
            mems.append(Membership(player_id=pid, team_id=identity.team_id(league, team), valid_from=start,
                                   valid_to=start, source=HISTORY_SOURCE, observed_at=observed_at,
                                   known_at=observed_at))
        parts = dict(g["parts"])
        parts.update(extra.get(g["event_id"]) or {})
        played = "played"
        if fam == "basketball" and not (parts.get("min") or 0) > 0:
            played = "did_not_play"
        for p, v in sorted(parts.items()):
            stats.append(StatObservation(
                event_id=eid, league=league, participant_id=pid, participant_type="player", scope="full_game",
                includes_overtime="yes", stat=p, unit=_part_unit(p), value=None if played == "did_not_play" else v,
                participation=played, source=HISTORY_SOURCE, source_ref=f"espn:gamelog:{g['event_id']}:{athlete_id}",
                final=True, observed_at=observed_at, known_at=observed_at))
    return evs, mems, stats


def _gamelog_bodies(league, athlete_id, seasons, cache_only):
    from . import espn, net
    lg = config.league(league)
    url = f"{espn.WEB}/{espn._path(lg)}/athletes/{athlete_id}/gamelog"
    key = f"{league}:{athlete_id}"
    bodies = []
    if cache_only:
        b = cache.get("espn:gamelog", key)
        if b:
            bodies.append(b)
        yr = int(now_utc()[:4])
        for s in range(yr + 1, yr - 3, -1):
            b = cache.get("espn:gamelog", f"{key}:{s}")
            if b:
                bodies.append(b)
        return bodies
    body = cache.fetch("espn:gamelog", key, 6 * 3600, lambda: net.get_json(url))
    bodies.append(body)
    season = espn._season_of(body)
    for back in range(1, seasons):
        if not season:
            break
        s = season - back
        try:
            bodies.append(cache.fetch("espn:gamelog", f"{key}:{s}", 30 * 86400,
                                      lambda s=s: net.get_json(url, params={"season": s})))
        except net.HttpError:
            break
    return bodies


def board_player_ids(con, league, now):
    """Resolved ESPN players with an open, gradable-family offer on an upcoming game."""
    events = {e.event_id: e for _, e, _ in _latest(con, "event", "league = ?", [league], now)
              if e.start_source == "espn"}
    out = []
    seen = set()
    for _, o, _ in _latest(con, "offer", "league = ?", [league], now):
        ev = events.get(o.event_id)
        if not o.player_id or o.player_id in seen or not ev or not ev.start_utc or ev.start_utc < now:
            continue
        if ":espn:" not in o.player_id or o.availability != "open":
            continue
        seen.add(o.player_id)
        out.append(o.player_id)
    return sorted(out)


def ingest_history(con, league, player_ids, observed_at=None, seasons=2, cache_only=False, max_players=400):
    """Game logs for these players -> typed records (store.put_many; invalid ones quarantined). Idempotent: same content
    is a duplicate, a corrected game-log value is a revision."""
    observed_at = observed_at or now_utc()
    c = {"players": 0, "unchanged": 0, "no_log": 0, "errors": 0, "inserted": 0, "duplicate": 0, "revision": 0, "invalid": 0}
    for pid in list(player_ids)[:max_players]:
        aid = pid.rsplit(":", 1)[-1]
        try:
            bodies = _gamelog_bodies(league, aid, seasons, cache_only)
        except Exception as e:                                          # noqa: BLE001 - one player never stops the job
            c["errors"] += 1
            sys.stderr.write(f"  gamelog {pid}: {type(e).__name__}: {str(e)[:120]}\n")
            continue
        if not bodies:
            c["no_log"] += 1
            continue
        import hashlib
        digest = hashlib.sha256(json.dumps(bodies, sort_keys=True).encode("utf-8")).hexdigest()
        if cache.get("ui:gamelog_ingested", pid) == digest:     # same answers already stored: no 1,000 duplicate puts
            c["unchanged"] += 1
            continue
        recs = []
        for b in bodies:
            evs, mems, stats = gamelog_records(league, aid, b, observed_at)
            recs += evs + mems + stats
        res = store.put_many(con, recs)
        for k in ("inserted", "duplicate", "revision", "invalid"):
            c[k] += res[k]
        cache.put("ui:gamelog_ingested", pid, digest)
        c["players"] += 1
    return c


# ----------------------------------------------------------------------------------------------------- distributions
def _dist(fc):
    d = dict(fc.distribution or ())
    fam = d.get("family")
    if fam not in ("normal", "count", "negbin") or d.get("mean") is None:
        return None
    shape = "normal" if fam == "normal" else "count"
    return shape, float(d.get("var_a", 1.0) or 1.0), float(d.get("var_b", 0.0) or 0.0), float(d["mean"])


def ladder(fc, lines):
    """Alternate-line chances from the forecast's own distribution: [{line, p_over, p_under, p_push}] or (None, why)."""
    dd = _dist(fc)
    if dd is None:
        return None, "the forecast stores no distribution parameters"
    shape, a, b, mean = dd
    o, u, p = fair.dist_probs(shape, a, b, mean, float(fc.line))
    gap = max(abs(o - fc.p_over), abs(u - fc.p_under), abs(p - fc.p_push))
    if gap > LADDER_TOL:
        why = " (its chance blends two Kalshi rungs)" if "between_rungs" in fc.reason_codes else ""
        return None, (f"the stored distribution gives {o * 100:.1f}% Over at {fc.line:g} but the forecast says "
                      f"{fc.p_over * 100:.1f}%{why}, so other lines are not read from it")
    out = []
    for ln in sorted(set(lines)):
        if ln == fc.line:
            out.append({"line": ln, "p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push, "stored": True})
            continue
        o, u, p = fair.dist_probs(shape, a, b, mean, float(ln))
        out.append({"line": ln, "p_over": _r(o, 6), "p_under": _r(u, 6), "p_push": _r(p, 6), "stored": False})
    return out, None


def pmf(fc, max_bins=80):
    """Outcome distribution for the chart: [[value, probability]] at whole numbers (binned for wide yards ranges)."""
    dd = _dist(fc)
    if dd is None:
        return None
    shape, a, b, mean = dd
    sd = math.sqrt(max(1e-9, a * mean + b * mean * mean)) if shape == "normal" else math.sqrt(mean + b * mean * mean)
    lo, hi = max(0, int(mean - 4 * sd)), int(mean + 4 * sd) + 1
    width = max(1, int(math.ceil((hi - lo + 1) / max_bins)))
    out = []
    k = lo
    while k <= hi:
        # P(k <= X <= k + width - 1) via the same function the forecast uses
        top = k + width - 1
        o_lo, u_lo, _ = fair.dist_probs(shape, a, b, mean, k - 0.5) if k > 0 else (1.0, 0.0, 0.0)
        o_hi, _, _ = fair.dist_probs(shape, a, b, mean, top + 0.5)
        out.append([k, width, _r(max(0.0, o_lo - o_hi), 6)])
        k += width
    return {"family": shape, "mean": _r(mean, 4), "sd": _r(sd, 4), "bins": out}


def pmf_ladder(fc, saved, lines):
    """Chances at other lines from the forecast's own saved PMF (the stored line reproduces the stored chance)."""
    lo, p = saved
    out = []
    for ln in sorted(set(lines) | {fc.line}):
        if ln == fc.line:
            out.append({"line": ln, "p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push, "stored": True})
            continue
        o, u, pu = forecast_detail.line_probs(lo, p, float(ln))
        out.append({"line": ln, "p_over": _r(o, 6), "p_under": _r(u, 6), "p_push": _r(pu, 6), "stored": False})
    return out


def pmf_chart(saved, max_bins=80):
    lo, p = saved
    mean = sum((lo + k) * x for k, x in enumerate(p))
    sd = math.sqrt(max(0.0, sum(((lo + k) - mean) ** 2 * x for k, x in enumerate(p))))
    width = max(1, int(math.ceil(len(p) / max_bins)))
    bins = [[lo + i, width, _r(sum(p[i:i + width]), 6)] for i in range(0, len(p), width)]
    return {"family": "pmf", "mean": _r(mean, 4), "sd": _r(sd, 4), "bins": [b for b in bins if b[2] and b[2] > 1e-6]}


def ladder_lines(line, stat_shape, book_lines):
    step = 5.0 if stat_shape == "normal" and line >= 40 else 1.0
    base = [line + i * step for i in range(-4, 5)]
    return [x for x in sorted(set(base + list(book_lines))) if x >= 0]


# -------------------------------------------------------------------------------------------------------- views
def forecast_view(fc, offer_rid, for_revision=None):
    return {"forecast_id": fc.forecast_id, "model_version": fc.model_version, "artifact_version": fc.artifact_version,
            "data_version": fc.data_version, "calibration_version": fc.calibration_version,
            "grade_policy_version": fc.grade_policy_version, "forecast_at": fc.forecast_at, "league": fc.league,
            "event_id": fc.event_id, "player_id": fc.player_id, "stat": fc.stat, "scope": fc.scope, "line": fc.line,
            "projection_mean": fc.projection_mean, "projection_median": fc.projection_median,
            "p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push,
            "p_over_given_no_push": fc.p_over_given_no_push, "p_under_given_no_push": fc.p_under_given_no_push,
            "sides_available": list(fc.sides_available), "grade": fc.grade, "stake_units": fc.stake_units,
            "stake_policy_version": fc.stake_policy_version, "reason_codes": list(fc.reason_codes),
            "research_only": fc.research_only, "offer_record_id": offer_rid, "for_revision": for_revision,
            "offer_snapshot_ids": list(fc.offer_snapshot_ids), "input_snapshot_ids": list(fc.input_snapshot_ids),
            "distribution": [list(x) for x in fc.distribution], "seed": fc.seed,
            "contributions": None}


def offer_view(rid, o, meta):
    ok, why = capabilities.gate(o)
    return {"record_id": rid, "platform": o.platform, "offer_id": o.offer_id, "line": o.line, "variant": o.variant,
            "promotional": o.promotional, "availability": o.availability, "scope": o.scope,
            "source_stat": o.source_stat, "rule_version": o.rule_version, "revision": meta["revision"],
            "sides": [{"side": s.side, "offered": s.offered, "price_format": s.price_format, "price": s.price,
                       "bid": s.bid, "ask": s.ask, "multiplier": s.multiplier} for s in o.sides],
            "listed_start_utc": o.listed_start_utc, "listed_time_kind": o.listed_time_kind,
            "source_updated_at": o.source_updated_at, "observed_at": o.observed_at,
            "last_seen_at": meta.get("last_seen_at") or o.observed_at, "source_time_unknown": o.source_time_unknown,
            "liquidity": dict(o.liquidity) if o.liquidity else {}, "gradable": ok, "not_gradable_because": why,
            "forecast_id": None}


def _team_view(teams, tid):
    t = teams.get(tid)
    return {"team_id": tid, "abbr": t.abbr if t else None, "name": t.name if t else None}


def _choose(fcs):
    """Latest forecast per model, then by display preference."""
    pref = models_pref()
    best = {}
    for fc, rid, rev in fcs:
        cur = best.get(fc.model_version)
        if cur is None or fc.forecast_at > cur[0].forecast_at:
            best[fc.model_version] = (fc, rid, rev)
    ranked = sorted(best.values(), key=lambda x: (pref.index(x[0].model_version) if x[0].model_version in pref
                                                   else len(pref), not (not x[0].research_only), x[0].model_version))
    return ranked


# --------------------------------------------------------------------------------------------------------- games
def player_games(con, league, pid, before, teams):
    """Per-game history of one player from typed StatObservations of games that started before `before`."""
    fam = FAMILY[league]
    rows = _latest(con, "stat", "player_id = ?", [pid], before)
    by_ev = defaultdict(dict)
    for rid, obs, _ in rows:
        cur = by_ev[obs.event_id].get(obs.stat)
        if cur is None or (obs.source == "espn" and cur[1].source != "espn"):  # box score beats the game log
            by_ev[obs.event_id][obs.stat] = (rid, obs)
    evs = {}
    ids = sorted(by_ev)
    for i in range(0, len(ids), 400):                   # one query per 400 games (was one per game)
        chunk = ids[i:i + 400]
        for row in _latest(con, "event", f"event_id IN ({','.join('?' * len(chunk))})", chunk, before):
            cur = evs.get(row[1].event_id)
            if cur is None or (row[1].start_source == "espn" and cur[1].start_source != "espn"):
                evs[row[1].event_id] = row
    mems = defaultdict(list)
    for rid, m, _ in _latest(con, "membership", "player_id = ?", [pid], before):
        mems[m.player_id].append(m)
    games = []
    for eid, stats in by_ev.items():
        if eid not in evs:
            continue
        erid, ev, _ = evs[eid]
        if not ev.start_utc or ev.start_utc >= before:
            continue
        team = next((m.team_id for m in mems[pid] if m.valid_from <= ev.start_utc and
                     (m.valid_to is None or m.valid_to >= ev.start_utc) and m.team_id in (ev.home_team_id,
                                                                                         ev.away_team_id)), None)
        home = None if team is None else team == ev.home_team_id
        opp = None if team is None else (ev.away_team_id if home else ev.home_team_id)
        parts = {s: v[1].value for s, v in stats.items()}
        part_ids = {s: v[0] for s, v in stats.items()}
        played = next(iter(stats.values()))[1].participation
        values = {}
        for mk, spec in markets.MARKETS[fam].items():
            if played == "did_not_play":
                continue
            if all(p in parts and parts[p] is not None for p in spec["parts"]):
                values[mk] = sum(parts[p] for p in spec["parts"])
        games.append({"event_id": eid, "event_record_id": erid, "start_utc": ev.start_utc, "season": ev.season,
                      "season_type": ev.season_type, "team": _team_view(teams, team) if team else None,
                      "opponent": _team_view(teams, opp) if opp else None, "home": home, "participation": played,
                      "parts": parts, "values": values, "record_ids": part_ids,
                      "source": sorted({v[1].source for v in stats.values()}),
                      "observed_at": max(v[1].observed_at for v in stats.values())})
    games.sort(key=lambda g: g["start_utc"], reverse=True)
    return games[:MAX_GAMES]


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def factors(fam, games, next_event, teams_out):
    """Owner-list factors Scout can compute from what it holds, each with its sample size and window."""
    played = [g for g in games if g["participation"] == "played"]
    out = []
    for part, label in FACTOR_PARTS[fam]:
        gs = [g for g in played if g["parts"].get(part) is not None]
        if not gs:
            continue
        season = gs[0]["season"]
        row = {"key": part, "label": label, "windows": []}
        for n in (3, 5, 10):
            w = gs[:n]
            row["windows"].append({"window": f"L{n}", "n": len(w), "mean": _r(_mean([g["parts"][part] for g in w]), 2),
                                   "from": w[-1]["start_utc"], "to": w[0]["start_utc"]})
        sv = [g for g in gs if g["season"] == season]
        row["windows"].append({"window": f"Season {season}" if season else "Season", "n": len(sv),
                               "mean": _r(_mean([g["parts"][part] for g in sv]), 2),
                               "from": sv[-1]["start_utc"], "to": sv[0]["start_utc"]})
        out.append(row)
    ctx = []
    if next_event and played:
        last = played[0]["start_utc"]
        rest = (parse_utc(next_event["start_utc"]) - parse_utc(last)).total_seconds() / 86400.0
        ctx.append({"key": "rest_days", "label": "Days since last game", "value": _r(rest, 1),
                    "from": last, "to": next_event["start_utc"]})
        if fam == "basketball":
            ctx.append({"key": "back_to_back", "label": "Back-to-back", "value": rest < 1.6})
    if next_event:
        ctx.append({"key": "venue", "label": "Venue", "value": "home" if next_event.get("home") else "away"
                    if next_event.get("home") is not None else None})
    ctx.append({"key": "teammates_out", "label": "Teammates on the injury report", "value": len(teams_out),
                "items": teams_out})
    return {"stats": out, "context": ctx, "not_held": NOT_HELD[fam]}


def _engine_context(league, fam, now, pids, markets_rows, events, cur_team):
    """Factor-engine context per board player (sports/player_context.py: one LeagueData load per board)."""
    from . import player_context
    wanted = {}
    for pid in pids:
        rows = [r for r in markets_rows.values() if r["player_id"] == pid]
        evs = sorted({r["event_id"] for r in rows if r["event_id"] in events}, key=lambda x: events[x][1].start_utc)
        tid = cur_team.get(pid)
        if not evs or tid is None:
            continue
        e = events[evs[0]][1]
        if tid not in (e.home_team_id, e.away_team_id):
            continue
        wanted[pid] = {"team_id": tid, "opp_id": e.away_team_id if tid == e.home_team_id else e.home_team_id,
                       "event_id": e.event_id, "start_utc": e.start_utc, "home": tid == e.home_team_id,
                       "stats": sorted({r["stat"] for r in rows if r["event_id"] == e.event_id and r["scope"] == "full_game"})}
    try:
        return player_context.build(league, fam, now, wanted)
    except Exception as ex:                                     # noqa: BLE001 - context never breaks a build
        return {}, {"state": f"failed: {type(ex).__name__}: {str(ex)[:120]}"}


# --------------------------------------------------------------------------------------------------------- build
def build(con, league, now=None, base=None, history=True):
    """Build every snapshot file of one league. -> summary counts."""
    t0 = time.time()
    now = now or now_utc()
    fam = FAMILY[league]
    root = snap_root(base) / league
    teams = {t.team_id: t for _, t, _ in _latest(con, "team", "league = ?", [league], now)}
    from datetime import timedelta
    horizon = (parse_utc(now) + timedelta(days=SLATE_DAYS[league])).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep = (parse_utc(now) - timedelta(hours=STARTED_KEEP_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = {}
    for rid, e, meta in _latest(con, "event", "league = ?", [league], now):
        if e.start_source != "espn" or not e.start_utc or e.start_utc < keep or e.start_utc > horizon:
            continue
        if e.status in ("cancelled",):
            continue
        events[e.event_id] = (rid, e, meta)
    # offers on these games
    all_offers = _latest(con, "offer", "league = ?", [league], now)
    last_read = {}                                          # platform -> newest re-confirmation of any of its offers
    for _, o, m in all_offers:
        seen = m.get("last_seen_at") or o.observed_at
        if seen and seen > last_read.get(o.platform, ""):
            last_read[o.platform] = seen
    offers = [(rid, o, m) for rid, o, m in all_offers if o.event_id in events and o.availability == "open"]
    del all_offers
    unmatched = sum(1 for _, o, _ in _latest(con, "offer", "league = ? AND event_id IS NULL", [league], now))
    revs = _offer_revisions(con, league, now)
    by_rid = {rid: (rid, o, m) for rid, o, m in offers}
    by_ident = {(o.platform, o.offer_id): (rid, o, m) for rid, o, m in offers}
    attached = defaultdict(list)                            # current offer rid -> [(forecast, rid, for_revision)]
    for fc in _all_forecasts(con, league, now):
        for orid in fc.offer_snapshot_ids:
            if orid in by_rid:
                attached[orid].append((fc, orid, None))
                continue
            r = revs.get(orid)
            if not r:
                continue
            cur = by_ident.get((r[0], r[1]))
            if cur is None:
                continue
            crid, co, _ = cur
            if (co.line == fc.line and co.stat == fc.stat and co.scope == fc.scope
                    and tuple(sorted(co.offered_sides())) == tuple(sorted(fc.sides_available))):
                attached[crid].append((fc, crid, r[2]))
    # players
    pids = sorted({o.player_id for _, o, _ in offers if o.player_id})
    players = {}
    for pid in pids:
        rs = _latest(con, "player", "player_id = ?", [pid], now)
        if rs:
            players[pid] = rs[0][1]
    cur_team = {}
    team_players = defaultdict(set)
    for _, m, _ in _latest(con, "membership", "", (), now):
        if m.valid_to is None and m.team_id in teams and m.source == "espn":
            cur_team[m.player_id] = m.team_id
            team_players[m.team_id].add(m.player_id)
    injuries = {}
    for rid, c, cm in _latest(con, "context", "league = ?", [league], now):
        # ContextSnapshot.to_dict() overwrites the record's own `kind` field with "context" (contracts sc-1), so the
        # context kind is read from the stored identity (source_key = "<kind>|<subject>|<event>") instead
        ckind = (cm.get("source_key") or "").split("|", 1)[0]
        if ckind == "injury" and c.present == "yes":
            injuries[c.subject_id] = {"record_id": rid, "status": c.status, "detail": c.detail, "as_of": c.as_of,
                                      "observed_at": c.observed_at, "source": c.source}
    all_inj_players = {}
    for pid in injuries:
        rs = _latest(con, "player", "player_id = ?", [pid], now)
        if rs:
            all_inj_players[pid] = rs[0][1]

    forecasts = {}
    markets_rows = {}
    row_items = defaultdict(list)                           # market key -> every offer of it (market view, mv-1)
    row_targets = defaultdict(list)                         # market key -> ordinary quotes a member can act on
    platform_seen = defaultdict(lambda: {"offers": 0, "last_observed_at": None, "source_time_unknown": 0})
    for rid, o, m in offers:
        ps = platform_seen[o.platform]
        ps["offers"] += 1
        seen = m.get("last_seen_at") or o.observed_at
        ps["last_observed_at"] = max(ps["last_observed_at"] or "", seen)
        ps["source_time_unknown"] += 1 if o.source_time_unknown else 0
        if not o.player_id or not o.stat:
            continue
        key = f"{o.event_id}|{o.player_id}|{o.stat}|{o.scope}"
        row = markets_rows.get(key)
        if row is None:
            p = players.get(o.player_id)
            row = markets_rows[key] = {
                "key": key, "event_id": o.event_id, "player_id": o.player_id,
                "player": p.name if p else o.player_name, "position": p.position if p else None,
                "team": o.team, "team_id": cur_team.get(o.player_id), "stat": o.stat,
                "stat_label": (markets.market(fam, o.stat) or {}).get("label", o.stat), "scope": o.scope,
                "primary": o.stat in PRIMARY_STATS.get(league, ()) and o.scope == "full_game",
                "capability": capabilities.classify(league, o.stat).status,
                "capability_reason": capabilities.classify(league, o.stat).reason,
                "quotes": [], "kalshi": [], "alternates": [], "promotional": []}
        row_items[key].append((rid, o, m))
        if not o.promotional and o.platform != "kalshi" and o.variant in capabilities.ORDINARY_VARIANTS:
            row_targets[key].append((rid, o))
        v = offer_view(rid, o, m)
        ch = _choose(attached.get(rid, []))
        if ch:
            fc, frid, frev = ch[0]
            v["forecast_id"] = fc.forecast_id
            v["other_models"] = [x[0].forecast_id for x in ch[1:]]
            for fcx, frx, rvx in ch:
                forecasts[fcx.forecast_id] = forecast_view(fcx, frx, rvx)
        if o.promotional:
            row["promotional"].append(v)
        elif o.platform == "kalshi":
            row["kalshi"].append(v)
        elif o.variant in capabilities.ORDINARY_VARIANTS:
            row["quotes"].append(v)
        else:
            row["alternates"].append(v)
    # sort + slim the board: Kalshi rungs nearest the pick'em lines only (full ladder in the player file)
    # what each displayed forecast is (scout / informed / market), its 80% range, validation, used inputs, PMF
    fd_info = forecast_detail.enrich(forecasts, league,
                                     with_pmf_for={k for k, f in forecasts.items() if f.get("kind") != "market"})
    run = forward_run(league)
    board_rows = []
    dnp = market_view.dnp_share(league)
    mv_stats = {"rows": 0, "with_consensus": 0, "books_listed": 0, "books_in_consensus": 0}
    for row in markets_rows.values():
        if events[row["event_id"]][1].start_utc <= now:     # started games leave the board (shown as started on the slate)
            continue
        mv = market_view.row_view(row_targets.get(row["key"], []), row_items.get(row["key"], []),
                                  events[row["event_id"]][1], now, league, dnp)
        row["market"] = mv
        scout_line_reads(row, forecasts)
        for q in row["quotes"]:
            if q["platform"] in market_view.TARGET_BOARDS and not q.get("forecast_id") and not q.get("line_read"):
                q["no_fc"] = no_forecast_reason(row, q, run)
        if mv:
            mv_stats["rows"] += 1
            mv_stats["with_consensus"] += 1 if mv["c"] else 0
            mv_stats["books_listed"] += len(mv["bk"])
            mv_stats["books_in_consensus"] += sum(1 for b in mv["bk"] if b["in"])
            for b in mv["bk"]:
                platform_seen[b["p"]]["in_consensus_rows"] = platform_seen[b["p"]].get("in_consensus_rows", 0) +                     (1 if b["in"] else 0)
        row["quotes"].sort(key=lambda q: (q["platform"], q["line"]))
        row["kalshi"].sort(key=lambda q: q["line"])
        ref = [q["line"] for q in row["quotes"]] or [r["line"] for r in row["alternates"]]
        near = row["kalshi"]
        if ref and near:
            near = sorted(near, key=lambda q: min(abs(q["line"] - x) for x in ref))[:2]
        elif near:                                       # Kalshi only: the two rungs priced nearest an even chance
            def mid_gap(q):
                o = next((x for x in q["sides"] if x["side"] == "over"), {})
                return abs(((o.get("bid") or 0) + (o.get("ask") or 1)) / 2 - 0.5)
            near = sorted(near, key=mid_gap)[:2]
        near = sorted(near, key=lambda q: q["line"])
        board_rows.append({**{k: v for k, v in row.items() if k not in ("kalshi", "alternates", "promotional")},
                           "kalshi_near": near, "kalshi_rungs": len(row["kalshi"]),
                           "alternates": len(row["alternates"]), "promotional": len(row["promotional"])})
    board_rows.sort(key=lambda r: (events[r["event_id"]][1].start_utc, r["event_id"], not r["quotes"], r["player"],
                                   not r["primary"], r["stat"]))

    # slate
    slate = []
    by_event = defaultdict(list)
    for r in board_rows:
        by_event[r["event_id"]].append(r)
    for eid, (rid, e, meta) in sorted(events.items(), key=lambda x: (x[1][1].start_utc, x[0])):
        rows = by_event.get(eid, [])
        plat = defaultdict(int)
        for r in rows:
            for q in r["quotes"]:
                plat[q["platform"]] += 1
            plat["kalshi"] += r["kalshi_rungs"]
        inj = []
        for tid in (e.away_team_id, e.home_team_id):
            for pid in sorted(team_players.get(tid, ())):
                if pid in injuries:
                    p = all_inj_players.get(pid) or players.get(pid)
                    inj.append({"player_id": pid, "player": p.name if p else pid, "team_id": tid,
                                "team": (teams.get(tid).abbr if teams.get(tid) else None), **injuries[pid]})
        slate.append({"event_id": eid, "record_id": rid, "start_utc": e.start_utc, "start_source": e.start_source,
                      "status": e.status, "reschedules": [dict(from_utc=x.from_utc, to_utc=x.to_utc, seen_at=x.seen_at)
                                                          for x in e.reschedules],
                      "observed_at": e.observed_at, "home": _team_view(teams, e.home_team_id),
                      "away": _team_view(teams, e.away_team_id), "started": e.start_utc <= now,
                      "coverage": {"markets": len(rows), "primary": sum(1 for r in rows if r["primary"]),
                                   "forecasts": sum(1 for r in rows for q in r["quotes"] if q["forecast_id"]),
                                   "players": len({r["player_id"] for r in rows}), "by_platform": dict(plat)},
                      "injuries": inj})

    # models / validation state (facts: counts)
    settled_by_model = defaultdict(int)
    setts = _current_settlements(con, league)
    for s in setts:
        for fid in s.affected_forecast_ids:
            if fid in forecasts:
                settled_by_model[forecasts[fid]["model_version"]] += 1
    models = defaultdict(lambda: {"forecasts": 0, "research_only": True})
    for f in forecasts.values():
        mm = models[f["model_version"]]
        mm["forecasts"] += 1
        mm["research_only"] = mm["research_only"] and f["research_only"]
    model_list = [{"model_version": k, **v, "settled": settled_by_model.get(k, 0)} for k, v in sorted(models.items())]

    manifest = {"version": SNAP_VERSION, "league": league, "built_at": now_utc(), "as_of": now,
                "forecast_run": {k: run.get(k) for k in ("forecast_at", "finished_at", "history_through")},
                "forecast_detail": fd_info,
                "store_schema_level": _schema_level(con), "contracts": "sc-1",
                "capabilities": capabilities.CAPABILITY_VERSION, "models": model_list,
                "model_preference": list(models_pref()), "platforms": {k: dict(v) for k, v in platform_seen.items()},
                "market_view": {"version": market_view.DISPLAY_VERSION, **mv_stats,
                                "rule": "mc-1 rule (sports/model/market.py quote_prob filters + equal-weight log-odds "
                                        "per source, nearest line moved with dispersion.json); target platform excluded",
                                "sources": "every verified source: kalshi, polymarket, oddsapi:<book>, fanduel, "
                                           "betrivers (owner decision 2026-10-23); registered mc-1 forecasts keep "
                                           "REGISTER.md 10"},
                "sources": source_coverage(platform_seen, last_read),
                "counts": {"events": len(slate), "markets": len(board_rows), "offers": len(offers),
                           "forecasts": len(forecasts), "unmatched_offers": unmatched, "players": len(pids)}}
    base_meta = {"version": SNAP_VERSION, "league": league, "built_at": manifest["built_at"], "as_of": now}
    sizes = {}
    sizes["slate"] = _write(root / "slate.json", {**base_meta, "events": slate, "platforms": manifest["platforms"],
                                                    "counts": manifest["counts"]})
    board_meta = {**base_meta, "models": model_list,
                  "stats": {k: v["label"] for k, v in markets.MARKETS[fam].items()},
                  "events": {eid: {"start_utc": e.start_utc, "status": e.status, "home": _team_view(teams, e.home_team_id),
                                   "away": _team_view(teams, e.away_team_id)} for eid, (_, e, _) in events.items()},
                  "injuries": {pid: injuries[pid] for pid in pids if pid in injuries},
                  "format": "compact quotes: r record id, p platform, l line, v variant, o offered [over, under], "
                            "m multipliers [over, under], k Kalshi YES [bid, ask], t last seen, su source updated, "
                            "g gradable, f forecast id; fc forecasts: m model, mu mean, po/pu/pp chances, at time, "
                            "ro research only, rv offer revision it was made for"}
    prim = [r for r in board_rows if r["primary"]]
    sec = [r for r in board_rows if not r["primary"]]
    sizes["board"] = _write(root / "board.json", {**board_meta, "part": "primary",
                                                    "secondary_markets": len(sec),
                                                    "markets": [compact_row(r) for r in prim],
                                                    "fc": compact_forecasts(forecasts, prim)})
    sizes["board_more"] = _write(root / "board_more.json", {**board_meta, "part": "secondary",
                                                              "markets": [compact_row(r) for r in sec],
                                                              "fc": compact_forecasts(forecasts, sec)})
    sizes["forecasts"] = _write(root / "forecasts.json", {**base_meta, "forecasts": {
        k: ({**f, "detail": {x: y for x, y in f["detail"].items() if x != "pmf"}} if f.get("detail") else f)
        for k, f in forecasts.items()}})

    # players
    n_players = 0
    pl_dir, hi_dir = root / "players", root / "history"
    ctx_by_pid, ctx_summary = _engine_context(league, fam, now, pids, markets_rows, events, cur_team)
    manifest["player_context"] = ctx_summary
    for pid in pids:
        rows = [r for r in markets_rows.values() if r["player_id"] == pid]
        p = players.get(pid)
        evs_p = sorted({r["event_id"] for r in rows}, key=lambda x: events[x][1].start_utc)
        next_ev = None
        if evs_p:
            e = events[evs_p[0]][1]
            tid = cur_team.get(pid)
            next_ev = {"event_id": e.event_id, "start_utc": e.start_utc,
                       "home": None if tid is None else tid == e.home_team_id,
                       "opponent": _team_view(teams, e.away_team_id if tid == e.home_team_id else e.home_team_id)
                       if tid in (e.home_team_id, e.away_team_id) else None}
        games = player_games(con, league, pid, now, teams) if history else []
        tid = cur_team.get(pid)
        mates_out = []
        for mate in sorted(team_players.get(tid, ())):
            if mate != pid and mate in injuries:
                mp = all_inj_players.get(mate) or players.get(mate)
                mates_out.append({"player_id": mate, "player": mp.name if mp else mate, **injuries[mate]})
        fdetail = {}
        for r in rows:
            book_lines = [q["line"] for q in r["quotes"] + r["alternates"]]
            for q in r["quotes"] + r["alternates"]:
                fid = q.get("forecast_id")
                if not fid or fid in fdetail:
                    continue
                fc = _fc_obj(con, fid)
                spec = markets.market(fam, r["stat"]) or {"shape": "count"}
                lad, why = ladder(fc, ladder_lines(fc.line, spec["shape"], book_lines))
                chart = pmf(fc)
                saved = ((forecasts.get(fid) or {}).get("detail") or {}).get("pmf")
                if lad is None and saved:                    # sm-1 / mi-1: the forecast's own saved PMF
                    lad, why, chart = pmf_ladder(fc, saved, ladder_lines(fc.line, spec["shape"], book_lines)), None,                         pmf_chart(saved)
                fdetail[fid] = {"ladder": lad, "ladder_withheld": why, "pmf": chart}
        _write(pl_dir / f"{safe_name(pid)}.json", {
            **base_meta, "player_id": pid, "player": p.name if p else (rows[0]["player"] if rows else pid),
            "position": p.position if p else None, "player_record_id": None, "status": p.status if p else None,
            "team": _team_view(teams, tid) if tid else None, "injury": injuries.get(pid), "next_event": next_ev,
            "markets": rows, "forecast_detail": fdetail,
            "forecasts": {fid: forecasts[fid] for fid in fdetail if fid in forecasts},
            "factors": dict(factors(fam, games, next_ev, mates_out), engine=ctx_by_pid.get(pid),
                            not_held=[x["factor"] for x in (ctx_by_pid.get(pid) or {}).get("not_held", [])]
                            or NOT_HELD[fam]),
            "history": {"games": len(games), "sources": sorted({s for g in games for s in g["source"]})}})
        _write(hi_dir / f"{safe_name(pid)}.json", {**base_meta, "player_id": pid, "family": fam, "games": games})
        n_players += 1
    sizes["results"] = _write(root / "results.json", results_view(con, league, setts, now, base_meta))
    manifest["sizes"] = sizes
    manifest["build_s"] = round(time.time() - t0, 2)
    _write(root / "manifest.json", manifest)
    return {"league": league, **manifest["counts"], "player_files": n_players, "sizes": sizes,
            "build_s": manifest["build_s"]}


def compact_quote(q):
    sides = {x["side"]: x for x in q["sides"]}
    o, u = sides.get("over") or {}, sides.get("under") or {}
    out = {"r": q["record_id"], "p": q["platform"], "l": q["line"], "v": q["variant"],
           "o": [1 if o.get("offered") else 0, 1 if u.get("offered") else 0], "t": q["last_seen_at"],
           "su": q["source_updated_at"], "g": 1 if q["gradable"] else 0, "f": q.get("forecast_id")}
    if o.get("multiplier") is not None or u.get("multiplier") is not None:
        out["m"] = [o.get("multiplier"), u.get("multiplier")]
    if o.get("bid") is not None or o.get("ask") is not None:
        out["k"] = [o.get("bid"), o.get("ask")]
    if q.get("line_read"):
        out["lr"] = q["line_read"]
    if q.get("no_fc"):
        out["nf"] = q["no_fc"]
    if o.get("price_format") == "decimal" or u.get("price_format") == "decimal":    # a book's own decimal prices
        out["d"] = [o.get("price") if o.get("offered") else None, u.get("price") if u.get("offered") else None]
    return out


def compact_row(r):
    return {"k": r["key"], "e": r["event_id"], "pid": r["player_id"], "n": r["player"], "pos": r["position"],
            "tm": r["team"], "st": r["stat"], "pr": 1 if r["primary"] else 0, "cap": r["capability"],
            "q": [compact_quote(q) for q in r["quotes"]], "kn": [compact_quote(q) for q in r["kalshi_near"]],
            "kr": r["kalshi_rungs"], "alt": r["alternates"], "promo": r["promotional"], "mk": r.get("market")}


def source_coverage(platform_seen, last_read=None):
    """Per market source: offers on the board now, last observation, verified flag (sports/books SOURCES), plus the
    sources that are configured but have nothing on the board. -> {source: {...}}."""
    from .books import SOURCES
    out = {}
    for name, meta in SOURCES.items():
        out[name] = {"verified": meta["verified"], "key_env": meta["key_env"], "offers": 0, "last_observed_at": None,
                     "last_read_at": None, "platforms": [], "in_consensus_rows": 0,
                     "role": "market source" if meta["verified"] else "capture-only (prop format not verified)"}
    for plat, ps in platform_seen.items():
        base = plat.split(":", 1)[0]
        if base in market_view.TARGET_BOARDS:
            out[base] = out.get(base) or {"verified": True, "key_env": None, "offers": 0, "last_observed_at": None,
                                          "platforms": [], "role": "pick'em board (target, never a market source)"}
        cur = out.setdefault(base, {"verified": False, "key_env": None, "offers": 0, "last_observed_at": None,
                                    "platforms": [], "role": "not in sports/books SOURCES"})
        cur["offers"] += ps["offers"]
        cur["last_observed_at"] = max(cur["last_observed_at"] or "", ps["last_observed_at"] or "") or None
        cur["platforms"].append(plat)
        cur["in_consensus_rows"] = cur.get("in_consensus_rows", 0) + ps.get("in_consensus_rows", 0)
    for plat, t in (last_read or {}).items():           # newest re-confirmation of ANY stored offer = last good read
        base = plat.split(":", 1)[0]
        cur = out.setdefault(base, {"verified": False, "key_env": None, "offers": 0, "last_observed_at": None,
                                    "platforms": [], "role": "not in sports/books SOURCES"})
        cur["last_read_at"] = max(cur.get("last_read_at") or "", t) or None
    from .books import inventory
    for name, meta in SOURCES.items():
        if not meta["verified"]:
            out[name]["capture"] = {lg: inventory.coverage_entry(name, lg) for lg in LEAGUES}
    return out


def compact_forecasts(forecasts, rows):
    ids = {q.get("forecast_id") for r in rows for q in r["quotes"] + r["kalshi_near"]} - {None}
    out = {}
    for fid in sorted(ids):
        f = forecasts[fid]
        out[fid] = {"m": f["model_version"], "mu": f["projection_mean"], "po": f["p_over"], "pu": f["p_under"],
                    "pp": f["p_push"], "at": f["forecast_at"], "ro": 1 if f["research_only"] else 0,
                    "rv": f["for_revision"], "ln": f["line"], "k": f.get("kind")}
        d = f.get("detail") or {}
        if d.get("interval"):
            out[fid].update(md=d.get("median"), iv=d["interval"])
        if d.get("validation"):
            out[fid]["vs"] = d["validation"].get("state")
    return out


def forward_run(league):
    """The last forward-forecast run (sports/model/fwd.py run record) for this league: forecast_at, finished_at, the
    per-offer no-forecast reasons, and the newest final game in its history DB (= inputs through)."""
    try:
        from .model.fwd import run_record_path
        rec = json.loads(Path(run_record_path()).read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001 - no run yet
        return {}
    lg = (rec.get("leagues") or {}).get(league) or {}
    out = {"forecast_at": lg.get("forecast_at"), "finished_at": rec.get("finished_at"),
           "why_none": lg.get("why_none") or {}, "history_through": None}
    hp = (rec.get("history") or {}).get("path")
    if hp and Path(hp).exists():
        try:
            import sqlite3
            c = sqlite3.connect(f"file:{Path(hp).as_posix()}?mode=ro", uri=True, timeout=5)
            r = c.execute("SELECT MAX(start_utc) FROM games WHERE league = ? AND status = 'final'", (league,)).fetchone()
            c.close()
            out["history_through"] = r[0] if r else None
        except Exception:                               # noqa: BLE001
            pass
    return out


def scout_line_reads(row, forecasts):
    """A pick'em line with no forecast of its own, in a market that HAS a Scout (sm-1 / mi-1) forecast at another
    line: the SAME saved distribution read at this line (no new projection - inputs unchanged). Marked `line_read`
    with the source forecast id, never stored as a forecast."""
    src = None
    for q in row["quotes"]:
        f = forecasts.get(q.get("forecast_id") or "")
        if f and f.get("kind") == "scout" and (f.get("detail") or {}).get("pmf"):
            if src is None or f["forecast_at"] > src["forecast_at"]:
                src = f
    if src is None:
        return
    lo, pmf = src["detail"]["pmf"]
    for q in row["quotes"]:
        if q.get("forecast_id") or not q.get("gradable"):
            continue
        po, pu, pp = forecast_detail.line_probs(lo, pmf, float(q["line"]))
        if po is None:
            continue
        q["line_read"] = {"f": src["forecast_id"], "ln": src["line"], "po": _r(po, 4), "pu": _r(pu, 4), "pp": _r(pp, 4)}


def no_forecast_reason(row, q, run):
    """Why this shown line has no Scout number - from the pipeline's actual state, most specific first."""
    if row.get("capability") != "supported":
        return "Market not supported by the Scout model" + (f": {row['capability_reason']}" if row.get(
            "capability_reason") else "")
    if not q.get("gradable"):
        why = q.get("not_gradable_because") or []
        return "Line not modelled: " + (why[0] if why else "not an ordinary line")
    wn = run.get("why_none") or {}
    w = wn.get(f"{row['event_id']}|{row['player_id']}|{row['stat']}") or wn.get(f"{row['event_id']}|{row['player_id']}")
    if w:
        return "No Scout forecast: " + w
    fa = run.get("forecast_at")
    if not fa:
        return "No forecast run yet"
    if (q.get("observed_at") or "") > fa:
        return "Line appeared after the last forecast run"
    return "No Scout forecast in the last run"


def _schema_level(con):
    r = con.execute("SELECT v FROM meta WHERE k='schema_level'").fetchone()
    return int(r[0]) if r else None


_FC_CACHE = {}


def _fc_obj(con, fid):
    if fid not in _FC_CACHE:
        if len(_FC_CACHE) > 20000:
            _FC_CACHE.clear()
        _FC_CACHE[fid] = store.get(con, fid)
    return _FC_CACHE[fid]


def _current_settlements(con, league):
    cur = {}
    for _, s in store.settlements(con):
        if league and s.event_id and not s.event_id.startswith(league + ":"):
            continue
        k = (s.platform, s.offer_id, s.side)
        if k not in cur or s.revision > cur[k].revision:
            cur[k] = s
    return list(cur.values())


# ------------------------------------------------------------------------------------------------------------ results
def _boot_ci(groups, stat_fn, reps=400, seed=7):
    """95% interval by resampling whole games (groups = [[row, ...], ...])."""
    if len(groups) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        pick = [x for _ in groups for x in groups[rng.randrange(len(groups))]]
        v = stat_fn(pick)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return [_r(vals[int(0.025 * (len(vals) - 1))], 4), _r(vals[int(0.975 * (len(vals) - 1))], 4)]


def results_view(con, league, setts, now, base_meta):
    """One ledger row per (forecast, settled offer side pair). Projection accuracy and probability accuracy are computed
    per forecast; returns appear only where a settlement records an actual stake and payout."""
    rows = []
    seen = set()
    by_offer = defaultdict(list)
    for s in setts:
        by_offer[(s.platform, s.offer_id)].append(s)
    fcs = {}
    for s in setts:
        for fid in s.affected_forecast_ids:
            if fid not in fcs:
                fcs[fid] = store.get(con, fid)
    for (plat, oid), ss in by_offer.items():
        for s in ss:
            for fid in s.affected_forecast_ids:
                fc = fcs.get(fid)
                if fc is None or fid in seen:
                    continue
                seen.add(fid)
                sides = {x.side: x for x in ss}
                any_s = ss[0]
                from .settlement import at_line            # the outcome at the FORECAST's line (C10)
                outcome = next((at_line(x, fc.line, "over")[1] for x in ss if at_line(x, fc.line, "over")[1]), None)
                actual = next((x.source_value for x in ss if x.source_value is not None), None)
                status = "void" if all(x.status == "void" for x in ss) else (
                    "unresolved" if outcome is None else "settled")
                p_out = None
                brier = logloss = None
                if outcome:
                    p = {"over": fc.p_over, "under": fc.p_under, "push": fc.p_push}
                    p_out = p[outcome]
                    brier = sum((p[k] - (1.0 if k == outcome else 0.0)) ** 2 for k in p)
                    logloss = -math.log(max(1e-12, p_out))
                err = (actual - fc.projection_mean) if (actual is not None and fc.projection_mean is not None) else None
                rows.append({"forecast_id": fid, "model_version": fc.model_version, "event_id": fc.event_id,
                             "player_id": fc.player_id, "player_key": fc.player_key, "stat": fc.stat,
                             "line": fc.line, "platform": plat, "offer_id": oid, "forecast_at": fc.forecast_at,
                             "projection_mean": fc.projection_mean, "p_over": fc.p_over, "p_under": fc.p_under,
                             "p_push": fc.p_push, "status": status, "outcome": outcome, "actual": actual,
                             "p_outcome": p_out, "brier": _r(brier, 6), "log_loss": _r(logloss, 6),
                             "error": _r(err, 4), "settlement_ids": [x.settlement_id for x in ss],
                             "settlement_revision": max(x.revision for x in ss), "rule_version": any_s.rule_version,
                             "settled_at": max((x.settled_at or "") for x in ss) or None,
                             "payouts": [{"side": x.side, "stake": x.stake, "payout": x.payout} for x in ss
                                         if x.stake is not None and x.payout is not None]})
    rows.sort(key=lambda r: (r["settled_at"] or "", r["forecast_id"]), reverse=True)
    summary = {}
    for mv in sorted({r["model_version"] for r in rows}):
        rr = [r for r in rows if r["model_version"] == mv and r["status"] == "settled"]
        groups = defaultdict(list)
        for r in rr:
            groups[r["event_id"]].append(r)
        g = list(groups.values())
        errs = [r for r in rr if r["error"] is not None]

        def mae(x):
            e = [abs(r["error"]) for r in x if r["error"] is not None]
            return sum(e) / len(e) if e else None

        def bias(x):
            e = [r["error"] for r in x if r["error"] is not None]
            return sum(e) / len(e) if e else None

        def mb(x):
            e = [r["brier"] for r in x if r["brier"] is not None]
            return sum(e) / len(e) if e else None

        def ml(x):
            e = [r["log_loss"] for r in x if r["log_loss"] is not None]
            return sum(e) / len(e) if e else None
        summary[mv] = {
            "settled": len(rr), "games": len(g),
            "void": sum(1 for r in rows if r["model_version"] == mv and r["status"] == "void"),
            "pushes": sum(1 for r in rr if r["outcome"] == "push"),
            "projection": {"n": len(errs), "mae": _r(mae(rr), 4), "mae_ci": _boot_ci(g, mae),
                           "bias": _r(bias(rr), 4), "bias_ci": _boot_ci(g, bias)},
            "probability": {"n": len(rr), "brier": _r(mb(rr), 5), "brier_ci": _boot_ci(g, mb),
                            "log_loss": _r(ml(rr), 5), "log_loss_ci": _boot_ci(g, ml)},
            "returns": {"recorded": sum(len(r["payouts"]) for r in rows if r["model_version"] == mv)}}
    return {**base_meta, "rows": rows[:2000], "total_rows": len(rows), "summary": summary,
            "method": {"brier": "three-outcome Brier (over / under / push) on the forecast's unconditional chances",
                       "log_loss": "-ln(chance the forecast gave the settled outcome)",
                       "error": "settled value minus the forecast's projection mean",
                       "ci": "95% bootstrap interval, whole games resampled (400 draws, seed 7)",
                       "returns": "only stakes and payouts a settlement record states; none are assumed"}}


# ---------------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.snapshots")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "history"):
        s = sub.add_parser(name)
        s.add_argument("leagues", nargs="*", default=list(LEAGUES))
        s.add_argument("--now", default=None)
        s.add_argument("--cache-only", action="store_true")
        s.add_argument("--max-players", type=int, default=400)
        s.add_argument("--no-history", action="store_true")
    a = p.parse_args(argv)
    flags.require("SPORTS_INGEST")
    now = utc(a.now) if a.now else now_utc()
    out = {}
    with store.opened() as con:
        for lg in a.leagues:
            if lg not in LEAGUES:
                raise SystemExit(f"league {lg}: nba or nfl")
            if a.cmd == "history":
                out[lg] = ingest_history(con, lg, board_player_ids(con, lg, now), cache_only=a.cache_only,
                                         max_players=a.max_players)
            else:
                out[lg] = build(con, lg, now=now, history=not a.no_history)
    print(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    try:
        main()
    except ContractError as e:
        raise SystemExit(str(e))
