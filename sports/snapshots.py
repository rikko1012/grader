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

from . import (board_view, cache, capabilities, config, context_reconcile, fair, flags, forecast_detail, game_lines,
               identity, market_view, markets, scout_chance, store)
from .names import team_abbr
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
    "basketball": ["Likely defender", "Potential assists"],
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
def _latest(con, kind, where="", args=(), as_of=None, limit=10 ** 7, index=None):
    return list(_iter_latest(con, kind, where, args, as_of, limit, index))


def _iter_latest(con, kind, where="", args=(), as_of=None, limit=10 ** 7, index=None):
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
                           f"last_source_updated_at FROM records{' INDEXED BY ' + index if index else ''} WHERE {cond}", a):
        k = (row["source"], row["source_key"])
        cur = best.get(k)
        if cur is None or row["revision"] > cur["revision"]:
            best[k] = row
    rows = list(best.values())[:int(limit)]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        pay = {r["record_id"]: r["payload"] for r in con.execute(
            f"SELECT record_id, payload FROM records WHERE record_id IN ({','.join('?' * len(chunk))})",
            [r["record_id"] for r in chunk])}
        for row in chunk:
            yield (row["record_id"], from_dict(store._unpack(pay[row["record_id"]])),
                        {"known_at": row["known_at"], "revision": row["revision"], "last_seen_at": row["last_seen_at"],
                         "last_source_updated_at": row["last_source_updated_at"], "source_key": row["source_key"]})


def _all_forecasts(con, league, as_of, event_ids=None):
    """Forecasts of this league known at as_of - of `event_ids` only when given (the slate's games; records_event)."""
    if event_ids is None:
        rows = con.execute("SELECT record_id, payload FROM records WHERE kind='forecast' AND league=? AND known_at <= ?",
                           (league, as_of)).fetchall()
        return [from_dict(store._unpack(r["payload"])) for r in rows]
    return _iter_forecasts(con, event_ids, as_of)


def _rss_mb():
    try:
        with open("/proc/self/statm") as fh:
            return round(int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
    except (OSError, ValueError, AttributeError):
        try:
            import psutil
            return round(psutil.Process().memory_info().rss / 1e6, 1)
        except Exception:                               # noqa: BLE001
            return None


def _stage(mem, name):
    """RSS after a build stage -> manifest build_rss_mb and stderr (a build killed over its cap leaves the stage it
    reached in the service log)."""
    mb = _rss_mb()
    mem.append([name, mb])
    sys.stderr.write(f"  snapshots stage {name}: rss {mb} MB\n")
    sys.stderr.flush()


def _last_read(con, league, as_of):
    """platform -> newest re-confirmation of any current offer of this league. Walks records_feed_seen from the newest
    sighting per platform: the GROUP BY read every offer revision the league ever stored (2026-10-31: NBA build)."""
    out = {}
    plats = [r[0] for r in con.execute(
        "WITH RECURSIVE p(x) AS (SELECT MIN(platform) FROM records WHERE kind = 'offer' UNION ALL "
        "SELECT (SELECT MIN(platform) FROM records WHERE kind = 'offer' AND platform > x) FROM p WHERE x IS NOT NULL) "
        "SELECT x FROM p WHERE x IS NOT NULL")]
    for plat in plats:
        r = con.execute(f"SELECT COALESCE(last_seen_at, observed_at) FROM records INDEXED BY records_feed_seen "
                        f"WHERE kind = 'offer' AND platform = ? "
                        f"AND league = ? AND {store.CURRENT_SQL} AND known_at <= ? "
                        f"ORDER BY COALESCE(last_seen_at, observed_at) DESC LIMIT 1", (plat, league, as_of)).fetchone()
        if r and r[0]:
            out[plat] = r[0]
    return out


def _unmatched_offers(con, league, as_of):
    """Offers whose latest revision has no game: starts from the rows with no event_id, not every revision stored."""
    return con.execute(
        f"SELECT COUNT(*) FROM (SELECT source, source_key, MAX(revision) AS rv FROM records INDEXED BY records_event "
        f"WHERE kind = 'offer' "
        f"AND event_id IS NULL AND league = ? AND {store.CURRENT_SQL} AND known_at <= ? GROUP BY source, source_key) c "
        f"WHERE NOT EXISTS (SELECT 1 FROM records x WHERE x.kind = 'offer' AND x.source = c.source "
        f"AND x.source_key = c.source_key AND x.revision > c.rv AND x.league = ? AND x.late = 0 AND x.known_at <= ?)",
        (league, as_of, league, as_of)).fetchone()[0]


def _iter_forecasts(con, event_ids, as_of):
    """One decoded forecast at a time (a generator: the caller keeps only what it needs)."""
    for i in range(0, len(event_ids), 400):
        part = list(event_ids[i:i + 400])
        for r in con.execute(f"SELECT payload FROM records WHERE kind='forecast' AND event_id IN "
                             f"({','.join('?' * len(part))}) AND known_at <= ?", part + [as_of]):
            yield from_dict(store._unpack(r["payload"]))


def _offer_revisions(con, league, as_of, event_ids=None):
    """record_id -> (platform, offer_id, revision) for every stored offer revision (to attach older-revision forecasts)
    - of `event_ids` only when given."""
    out = {}
    if event_ids is None:
        q, a = "SELECT record_id, source, source_key, revision FROM records WHERE kind='offer' AND league=? "                "AND known_at <= ?", [(league, as_of)]
    else:
        ids = list(event_ids)
        a = [tuple(ids[i:i + 400]) + (as_of,) for i in range(0, len(ids), 400)]
        q = None
    for args in a:
        sql = q or (f"SELECT record_id, source, source_key, revision FROM records WHERE kind='offer' AND event_id IN "
                    f"({','.join('?' * (len(args) - 1))}) AND known_at <= ?")
        for r in con.execute(sql, args):
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


GAMELOG_CURRENT_TTL_S = 3 * 3600      # a current-season log is re-read after 3 h (a finished game shows the same night)


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
    body = cache.fetch("espn:gamelog", key, GAMELOG_CURRENT_TTL_S, lambda: net.get_json(url))
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
    for _, o, _ in _iter_latest(con, "offer", "league = ?", [league], now):
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
    # least recently checked first (never checked first of all), so small frequent runs cover every board player
    # instead of re-reading the same first max_players in id order
    order = sorted(player_ids, key=lambda p: (cache.get("ui:gamelog_checked", p) or "", p))
    for pid in order[:max_players]:
        cache.put("ui:gamelog_checked", pid, observed_at)
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


FRESHNESS_RULE = ("a quote's age is measured from Scout's own last observation (last_seen_at) - the source's update "
                  "time is shown only when the source gives one (source_time_unknown stays true otherwise; no source "
                  "time is ever invented)")
STATUS_LABELS = {"scheduled": "Scheduled", "in_progress": "Live", "final": "Final", "postponed": "Postponed",
                 "cancelled": "Cancelled", "delayed": "Delayed", "suspended": "Suspended"}


def _locked(e, now):
    """A game whose lines are closed to new picks: started (by its ESPN start) or no longer scheduled."""
    return bool(e.start_utc <= now or (e.status or "scheduled") not in ("scheduled",))


def status_label(e, now):
    st = e.status or "scheduled"
    if st == "scheduled" and e.start_utc <= now:
        return "Started"
    return STATUS_LABELS.get(st, st.replace("_", " ").capitalize())


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
def _events_of(con, ids, before, cache):
    """event_id -> preferred event row, each game decoded once per build (a player's history repeats every teammate's)."""
    need = [x for x in ids if x not in cache]
    for x in need:
        cache[x] = None
    for i in range(0, len(need), 400):                  # one query per 400 games (was one per game)
        chunk = need[i:i + 400]
        for row in _latest(con, "event", f"event_id IN ({','.join('?' * len(chunk))})", chunk, before):
            cur = cache[row[1].event_id]
            if cur is None or (row[1].start_source == "espn" and cur[1].start_source != "espn"):
                cache[row[1].event_id] = row
    return {x: cache[x] for x in ids if cache[x] is not None}


def player_games(con, league, pid, before, teams, ev_cache=None):
    """Per-game history of one player from typed StatObservations of games that started before `before`. Only the
    MAX_GAMES newest games' stats are decoded: whole seasons of an NBA regular made this the build's slowest loop."""
    fam = FAMILY[league]
    ev_cache = {} if ev_cache is None else ev_cache
    # INDEXED BY: unforced, SQLite walked every stat row in the store here, once per player (2026-10-31)
    ids = [r[0] for r in con.execute(f"SELECT DISTINCT event_id FROM records INDEXED BY records_player "
                                     f"WHERE kind = 'stat' AND player_id = ? AND {store.CURRENT_SQL} AND known_at <= ?",
                                     (pid, before)) if r[0]]
    evs = _events_of(con, ids, before, ev_cache)
    newest = sorted((x for x, r in evs.items() if r[1].start_utc and r[1].start_utc < before),
                    key=lambda x: evs[x][1].start_utc, reverse=True)[:MAX_GAMES]
    if not newest:
        return []
    rows = _latest(con, "stat", f"player_id = ? AND event_id IN ({','.join('?' * len(newest))})", [pid, *newest], before,
                   index="records_player")
    by_ev = defaultdict(dict)
    for rid, obs, _ in rows:
        if not obs.final:
            continue
        stat_key = (obs.scope, obs.stat)
        cur = by_ev[obs.event_id].get(stat_key)
        if cur is None or (obs.source == "espn" and cur[1].source != "espn"):  # box score beats the game log
            by_ev[obs.event_id][stat_key] = (rid, obs)
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
        scoped_stats = stats
        stats = {stat: value for (scope, stat), value in scoped_stats.items() if scope == 'full_game'}
        period_values = {}
        for period in {scope for scope, stat in scoped_stats if scope != 'full_game'}:
            period_parts = {stat: value[1].value for (scope, stat), value in scoped_stats.items()
                            if scope == period and value[1].participation == 'played'}
            period_values[period] = {mk: sum(period_parts[p] for p in spec['parts'])
                                    for mk, spec in markets.MARKETS[fam].items()
                                    if all(period_parts.get(p) is not None for p in spec['parts'])}
        parts = {s: v[1].value for s, v in stats.items()}
        part_ids = {s: v[0] for s, v in stats.items()}
        played = next(iter(stats.values()))[1].participation if stats else 'unknown'
        values = {}
        for mk, spec in markets.MARKETS[fam].items():
            if played == "did_not_play":
                continue
            if all(p in parts and parts[p] is not None for p in spec["parts"]):
                values[mk] = sum(parts[p] for p in spec["parts"])
        games.append({"event_id": eid, "event_record_id": erid, "start_utc": ev.start_utc, "season": ev.season,
                      "season_type": ev.season_type, "team": _team_view(teams, team) if team else None,
                      "opponent": _team_view(teams, opp) if opp else None, "home": home, "participation": played,
                      "parts": parts, "values": values, "period_values": period_values, "record_ids": part_ids,
                      "source": sorted({v[1].source for v in scoped_stats.values()}),
                      "observed_at": max(v[1].observed_at for v in scoped_stats.values())})
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


def _engine_context(league, fam, now, pids, markets_rows, events, cur_team, root=None):
    """Factor-engine context per board player (sports/player_context.py). The engine's league load does not fit this
    3-minute job once the game store holds whole seasons (measured 319 MB RSS vs its 250 MB cap with 1,724 games), so
    by default this build only WRITES what it wants (<league>/context_wanted.json) and EMBEDS the latest result of the
    scheduled player-context job (<league>/player_context.json, sports/player_context.main). SPORTS_CONTEXT_INLINE=1
    computes it here instead (tests / a workstation)."""
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
    if root is not None and os.environ.get("SPORTS_CONTEXT_INLINE", "").strip().lower() not in ("1", "true", "yes"):
        _write(root / "context_wanted.json", {"as_of": now, "wanted": wanted})
        return player_context.read_result(root, now, set(wanted))
    try:
        return player_context.build(league, fam, now, wanted)
    except Exception as ex:                                     # noqa: BLE001 - context never breaks a build
        return {}, {"state": f"failed: {type(ex).__name__}: {str(ex)[:120]}"}


# --------------------------------------------------------------------------------------------------------- build
def build(con, league, now=None, base=None, history=True, results=True):
    """Build every snapshot file of one league. -> summary counts."""
    t0 = time.time()
    mem, stage_s = [], []
    def stage(name):
        _stage(mem, name)
        stage_s.append([name, round(time.time() - t0, 1)])   # seconds since the build started
        sys.stderr.write(f"  snapshots stage {name}: {stage_s[-1][1]} s\n")
    now = now or now_utc()
    fam = FAMILY[league]
    root = snap_root(base) / league
    ev_cache = {}
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
    # offers on these games. BOUNDED to the slate's events (fresh 2026-10-24): the former league-wide decode of every
    # current offer + every forecast + every offer revision grew with the store and pushed this job over its cap
    # (production 2026-10-25 01:00Z: killed_over_cap 254 > 250 MB six times in a row -> the board stopped refreshing).
    ev_ids = sorted(events)
    ev_in = ",".join("?" * len(ev_ids)) or "NULL"
    last_read = _last_read(con, league, now)                # platform -> newest re-confirmation of any of its offers
    offers = [(rid, o, m) for rid, o, m in _latest(
        con, "offer", f"league = ? AND (source, source_key) IN (SELECT source, source_key FROM records WHERE "
                      f"kind = 'offer' AND league = ? AND event_id IN ({ev_in}))", [league, league, *ev_ids], now)
        if o.event_id in events and o.availability == "open"]
    unmatched = _unmatched_offers(con, league, now)
    stage("offers read")
    revs = _offer_revisions(con, league, now, ev_ids)
    by_rid = {rid: (rid, o, m) for rid, o, m in offers}
    by_ident = {(o.platform, o.offer_id): (rid, o, m) for rid, o, m in offers}
    # current offer rid -> [(forecast, rid, for_revision)]: STREAMED and reduced on the fly to the newest forecast per
    # (offer, model) - the only ones _choose can pick - so memory follows offers x models, not every forecast ever
    # made on the slate (model-forward re-forecasts every 30 min)
    best = {}
    for fc in _all_forecasts(con, league, now, ev_ids):
        for orid in fc.offer_snapshot_ids:
            if orid in by_rid:
                hit = (fc, orid, None)
            else:
                r = revs.get(orid)
                if not r:
                    continue
                cur = by_ident.get((r[0], r[1]))
                if cur is None:
                    continue
                crid, co, _ = cur
                if not (co.line == fc.line and co.stat == fc.stat and co.scope == fc.scope
                        and tuple(sorted(co.offered_sides())) == tuple(sorted(fc.sides_available))):
                    continue
                hit = (fc, crid, r[2])
            k = (hit[1], fc.model_version)
            if k not in best or fc.forecast_at > best[k][0].forecast_at:
                best[k] = hit
    attached = defaultdict(list)
    for (crid, _mv), hit in best.items():
        attached[crid].append(hit)
    fc_objs = {hit[0].forecast_id: hit[0] for hit in best.values()}     # the decoded forecasts the files need
    del best, revs
    stage("offers + forecasts read")
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
        if row["team"] is None and o.team:              # N9: the first offer may carry no team (Kalshi); any later one does
            row["team"] = o.team
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
    for row in markets_rows.values():                   # N10: the forecast is per MARKET + LINE, not per offer
        same_line_forecasts(row, forecasts)
    # sort + slim the board: Kalshi rungs nearest the pick'em lines only (full ladder in the player file)
    # what each displayed forecast is (scout / informed / market), its 80% range, validation, used inputs, PMF
    stage("market rows")
    fd_info = forecast_detail.enrich(forecasts, league,
                                     with_pmf_for={k for k, f in forecasts.items() if f.get("kind") != "market"})
    stage("forecast detail")
    run = forward_run(league)
    board_rows = []
    dnp = market_view.dnp_share(league)
    mv_stats = {"rows": 0, "with_consensus": 0, "books_listed": 0, "books_in_consensus": 0}
    for row in markets_rows.values():
        if events[row["event_id"]][1].start_utc <= now:     # started games leave the board (shown as started on the slate)
            continue
        ev_ = events[row["event_id"]][1]
        if row["team_id"] not in (ev_.home_team_id, ev_.away_team_id):
            row["team_id"] = None                         # a membership of a team not in this game (stale after a trade)
        if row["team_id"] is None:                       # N9: no usable roster membership: the offer's team code
            want = team_abbr(league, row["team"])         # matched to THIS game's two teams
            for tid_ in (ev_.home_team_id, ev_.away_team_id):
                t_ = teams.get(tid_)
                if want and t_ is not None and team_abbr(league, t_.abbr) == want:
                    row["team_id"] = tid_
        if row["team_id"] is not None and teams.get(row["team_id"]) is not None:
            row["team"] = team_abbr(league, teams[row["team_id"]].abbr) or row["team"]
        elif row["team"]:
            row["team"] = team_abbr(league, row["team"])
        mv = market_view.row_view(row_targets.get(row["key"], []), row_items.get(row["key"], []),
                                  events[row["event_id"]][1], now, league, dnp, fallback=market_view.fallback_on())
        row["market"] = mv
        scout_line_reads(row, forecasts)
        for q in row["quotes"]:                          # THE Scout-chance definition (sports/scout_chance.py)
            if scout_chance.is_scout_chance(q, forecasts, False):
                q["sc"] = 1
        row["sc"] = sum(1 for q in row["quotes"] if q.get("sc"))
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

    stage("board rows")
    # same-game injury reconciliation (SPORTS_CONTEXT_RECONCILE, default off): display only, nothing reads it back
    ctx_check, ev_times = None, defaultdict(set)
    if context_reconcile.on():
        for f in forecasts.values():
            if f.get("event_id") in events and events[f["event_id"]][1].start_utc > now and f.get("forecast_at"):
                ev_times[f["event_id"]].add(f["forecast_at"])
        ctx_check = context_reconcile.Checker(con, league, now, {t for ts in ev_times.values() for t in ts})

        def _cc_name(pid_):
            p_ = all_inj_players.get(pid_) or players.get(pid_)
            if p_ is None:
                rs_ = _latest(con, "player", "player_id = ?", [pid_], now)
                p_ = rs_[0][1] if rs_ else None
            return p_.name if p_ else pid_

        def _cc_event(eid_):
            e_ = events[eid_][1]
            tp = {t_: sorted(team_players.get(t_, ())) for t_ in (e_.away_team_id, e_.home_team_id)}
            return ctx_check.event_block(eid_, tp, ev_times.get(eid_, ()), _cc_name, cur_team,
                                         lambda t_: team_abbr(league, teams[t_].abbr) if t_ in teams else None)
    # slate
    slate = []
    glines = game_lines.game_lines(league, {eid: e for eid, (_, e, _) in events.items()})
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
                      "locked": _locked(e, now), "status_label": status_label(e, now),
                      "coverage": {"markets": len(rows), "primary": sum(1 for r in rows if r["primary"]),
                                   "forecasts": sum(1 for r in rows for q in r["quotes"] if q["forecast_id"]),
                                   "scout_chances": scout_chance.event_counts(rows, forecasts, e.start_utc <= now),
                                   "players": len({r["player_id"] for r in rows}), "by_platform": dict(plat)},
                      "game_lines": glines.get(eid),
                      "injuries": inj})
        if ctx_check is not None and eid in ev_times:
            blk = _cc_event(eid)
            if blk:
                slate[-1]["context_check"] = blk

    # models / validation state (facts: counts)
    # settled counts come from the results ledger (forecast <-> settlement linked by offer identity,
    # settled_forecast_links); the ledger itself is written by the settle job (write_results) - not every 3 minutes
    res_summary = (_read_json(root / "results.json") or {}).get("summary") or {}
    if results:
        res_obj = results_view(con, league, _current_settlements(con, league), now,
                               {"version": SNAP_VERSION, "league": league, "built_at": now_utc(), "as_of": now})
        res_summary = res_obj["summary"]
    models = defaultdict(lambda: {"forecasts": 0, "research_only": True})
    for f in forecasts.values():
        mm = models[f["model_version"]]
        mm["forecasts"] += 1
        mm["research_only"] = mm["research_only"] and f["research_only"]
    for mv in res_summary:
        models[mv]
    model_list = [{"model_version": k, **v, "settled": int((res_summary.get(k) or {}).get("settled") or 0)}
                  for k, v in sorted(models.items())]

    manifest = {"version": SNAP_VERSION, "league": league, "built_at": now_utc(), "as_of": now,
                "forecast_run": {k: run.get(k) for k in ("forecast_at", "finished_at", "history_through")},
                "forecast_detail": fd_info,
                "store_schema_level": _schema_level(con), "contracts": "sc-1",
                "capabilities": capabilities.CAPABILITY_VERSION, "models": model_list,
                "model_preference": list(models_pref()), "platforms": {k: dict(v) for k, v in platform_seen.items()},
                "market_view": {"version": market_view.DISPLAY_VERSION, **mv_stats,
                                **({"fallback": True} if market_view.fallback_on() else {}),    # flag off: the key is absent
                                "rule": "mc-1 rule (sports/model/market.py quote_prob filters + equal-weight log-odds "
                                        "per source, nearest line moved with dispersion.json); target platform excluded",
                                "sources": "every verified source: kalshi, polymarket, oddsapi:<book>, fanduel, "
                                           "betrivers (owner decision 2026-10-23); registered mc-1 forecasts keep "
                                           "REGISTER.md 10"},
                "sources": source_coverage(platform_seen, last_read),
                "scout_chance": {"definition": scout_chance.DEFINITION, "models": list(scout_chance.MODELS),
                                 "text": scout_chance.TEXT,
                                 "markets": sum(1 for r in board_rows if r.get("sc")),
                                 "quotes": sum(r.get("sc", 0) for r in board_rows)},
                "freshness": {"age_from": "last_seen_at", "rule": FRESHNESS_RULE,
                              "source_time_unknown": {k: v["source_time_unknown"] for k, v in platform_seen.items()
                                                      if v["source_time_unknown"]}},
                "counts": {"events": len(slate), "markets": len(board_rows), "offers": len(offers),
                           "forecasts": len(forecasts), "unmatched_offers": unmatched, "players": len(pids)}}
    base_meta = {"version": SNAP_VERSION, "league": league, "built_at": manifest["built_at"], "as_of": now}
    sizes = {}
    sizes["slate"] = _write(root / "slate.json", {**base_meta, "events": slate, "platforms": manifest["platforms"],
                                                    "counts": manifest["counts"]})
    board_meta = {**base_meta, "models": model_list,
                  "stats": {k: v["label"] for k, v in markets.MARKETS[fam].items()},
                  "events": {eid: {"start_utc": e.start_utc, "status": e.status, "home": _team_view(teams, e.home_team_id),
                                   "away": _team_view(teams, e.away_team_id), "started": e.start_utc <= now,
                                   "locked": _locked(e, now), "status_label": status_label(e, now)}
                             for eid, (_, e, _) in events.items()},
                  "injuries": {pid: injuries[pid] for pid in pids if pid in injuries},
                  "format": "compact quotes: r record id, p platform, l line, v variant, o offered [over, under], "
                            "m multipliers [over, under], k Kalshi YES [bid, ask], t last seen, su source updated, "
                            "g gradable, f forecast id; fc forecasts: m model, mu mean, po/pu/pp chances, at time, "
                            "ro research only, rv offer revision it was made for"}
    sizes["forecasts"] = _write(root / "forecasts.json", {**base_meta, "forecasts": {
        k: ({**f, "detail": {x: y for x, y in f["detail"].items() if x != "pmf"}} if f.get("detail") else f)
        for k, f in forecasts.items()}})

    stage("slate+board files")
    # players
    n_players = 0
    pl_dir, hi_dir = root / "players", root / "history"
    ctx_by_pid, ctx_summary = _engine_context(league, fam, now, pids, markets_rows, events, cur_team, root)
    manifest["player_context"] = ctx_summary
    sanity_log = []
    view_out = board_view.StreamWriter(root / "board_view.json",       # bv-1 hit rates, streamed per player
                                       {**base_meta, "part": "view", "rule": board_view.VERSION})
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
        games = player_games(con, league, pid, now, teams, ev_cache) if history else []
        vrows = board_view.player_rows(rows, games)
        view_out.add(vrows)
        for r in rows:                                   # vg-1 gate + sg-1 sanity guard on every Scout view (N1/N2)
            # sg-2 history rules (thin / stale) read the bv-1 row only when the history was actually read
            log = guard_row(league, r, forecasts, vrows.get(r["key"]) if history else None, now)
            if events[r["event_id"]][1].start_utc > now:      # the admin log lists games still on the board
                sanity_log.extend(log)
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
                fc = fc_objs.get(fid) or _fc_obj(con, fid)
                spec = markets.market(fam, r["stat"]) or {"shape": "count"}
                lad, why = ladder(fc, ladder_lines(fc.line, spec["shape"], book_lines))
                chart = pmf(fc)
                saved = ((forecasts.get(fid) or {}).get("detail") or {}).get("pmf")
                if lad is None and saved:                    # sm-1 / mi-1: the forecast's own saved PMF
                    lad, why, chart = pmf_ladder(fc, saved, ladder_lines(fc.line, spec["shape"], book_lines)), None,                         pmf_chart(saved)
                fdetail[fid] = {"ladder": lad, "ladder_withheld": why, "pmf": chart}
        pfile = {
            **base_meta, "player_id": pid, "player": p.name if p else (rows[0]["player"] if rows else pid),
            "position": p.position if p else None, "player_record_id": None, "status": p.status if p else None,
            "team": _team_view(teams, tid) if tid else None, "injury": injuries.get(pid), "next_event": next_ev,
            "markets": rows, "forecast_detail": fdetail,
            "forecasts": {fid: forecasts[fid] for fid in fdetail if fid in forecasts},
            "factors": dict(factors(fam, games, next_ev, mates_out), engine=ctx_by_pid.get(pid),
                            not_held=[x["factor"] for x in (ctx_by_pid.get(pid) or {}).get("not_held", [])]
                            or NOT_HELD[fam]),
            "history": {"games": len(games), "sources": sorted({s for g in games for s in g["source"]})}}
        if ctx_check is not None and next_ev and next_ev["event_id"] in ev_times:
            pblk = context_reconcile.Checker.player_block(_cc_event(next_ev["event_id"]), pid, tid)
            if pblk:
                pfile["context_check"] = pblk
        _write(pl_dir / f"{safe_name(pid)}.json", pfile)
        _write(hi_dir / f"{safe_name(pid)}.json", {**base_meta, "player_id": pid, "family": fam, "games": games})
        n_players += 1
    sizes["board_view"] = view_out.close()
    # board files AFTER the player pass: each quote carries its guard result (x) computed with the player's history
    prim = [r for r in board_rows if r["primary"]]
    sec = [r for r in board_rows if not r["primary"]]
    sizes["board"] = _write(root / "board.json", {**board_meta, "part": "primary",
                                                    "secondary_markets": len(sec),
                                                    "markets": [compact_row(r, league) for r in prim],
                                                    "fc": compact_forecasts(forecasts, prim)})
    sizes["board_more"] = _write(root / "board_more.json", {**board_meta, "part": "secondary",
                                                              "markets": [compact_row(r, league) for r in sec],
                                                              "fc": compact_forecasts(forecasts, sec)})
    manifest["sanity"] = write_sanity(root, sanity_log, base_meta, board_rows)
    manifest["cal_mi"] = write_cal_index(root, base_meta, board_rows)
    if results:
        sizes["results"] = _write(root / "results.json", {**res_obj, **base_meta})
    manifest["sizes"] = sizes
    if ctx_check is not None:
        manifest["context_check"] = ctx_check.summary()
    stage("players")
    manifest["build_s"] = round(time.time() - t0, 2)
    manifest["build_rss_mb"] = mem
    manifest["build_stage_s"] = stage_s
    if os.getenv('SCOUT_MARKET_TOOLS') == '1':
        from . import market_tools
        # No rule-equivalence registry or validated candidate is inferred from
        # a display grade. Until those pass, the feed exposes exclusions only.
        _write(root / 'market_tools.json', market_tools.build(offers, now))
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
    if q.get("sc"):
        out["sc"] = 1
    if q.get("x"):
        out["x"] = q["x"]
    if q.get("cal"):                                     # cal-mi-1 displayed chance (raw sm-1 chance in ca.raw)
        out["ca"] = q["cal"]
    if o.get("price_format") == "decimal" or u.get("price_format") == "decimal":    # a book's own decimal prices
        out["d"] = [o.get("price") if o.get("offered") else None, u.get("price") if u.get("offered") else None]
    return out


def _ud_quote(cq, q, league):
    """PP_OPEN_LINK (2026-10-29): an Underdog quote also carries "ud", Underdog's link to that line's market screen (the
    current Underdog board's ids - slips/udlink.py). Off, or no known market id: the quote is exactly as before."""
    if q.get("platform") != "underdog" or not league or os.environ.get("PP_OPEN_LINK", "").strip() != "1":
        return cq
    from .slips import udlink
    lk = udlink.link_for(league, q.get("offer_id"))
    return dict(cq, ud=lk) if lk else cq


def compact_row(r, league=None):
    nv = board_view.contain_reason(league, r["stat"]) if league else None
    return {**({"nv": nv} if nv else {}), "k": r["key"], "e": r["event_id"], "pid": r["player_id"], "n": r["player"], "pos": r["position"],
            "tm": r["team"], "tid": r.get("team_id"), "st": r["stat"], "pr": 1 if r["primary"] else 0, "cap": r["capability"],
            "q": [_ud_quote(compact_quote(q), q, league) for q in r["quotes"]], "kn": [compact_quote(q) for q in r["kalshi_near"]],
            "kr": r["kalshi_rungs"], "alt": r["alternates"], "promo": r["promotional"], "mk": r.get("market"),
            "sc": r.get("sc", 0), "hid": scout_chance.hidden_reason(r)}


def scout_view(q, forecasts):
    """The Scout (sm-1 / mi-1) view at this quote's line: (po, pu, mu, model, forecast_id, 80% interval) or None - the
    offer's own forecast, else the line read of the market's saved distribution (= web/sports/table.js scoutAt)."""
    f = forecasts.get(q.get("forecast_id") or "")
    if f and f.get("kind") != "market" and f.get("p_over") is not None and f.get("p_under") is not None:
        return (f["p_over"], f["p_under"], f.get("projection_mean"), f.get("model_version"), f["forecast_id"],
                (f.get("detail") or {}).get("interval"))
    lr = q.get("line_read")
    if lr and lr.get("po") is not None and lr.get("pu") is not None:
        s = forecasts.get(lr.get("f") or "") or {}
        return (lr["po"], lr["pu"], s.get("projection_mean"), s.get("model_version") or "sm-1", lr.get("f"),
                (s.get("detail") or {}).get("interval"))
    return None


def _view_push(q, forecasts, fid):
    f = forecasts.get(fid or "") or {}
    if q.get("forecast_id") == fid and f.get("p_push") is not None:
        return f["p_push"]
    return (q.get("line_read") or {}).get("pp") or 0.0


def guard_row(league, row, forecasts, view_row, now):
    """Row signal for every quote of one market row with a Scout view (sports/board_view.quote_x): q["x"] = a hold
    {"b", "t", "v"} (vg-1 gate / sg-1 sanity: no lean, edge, confidence or edge rank anywhere - board, sheets, slip
    pool) or the cf-2 signal {"ln", "pl", "e", "g", "c", "cw", "s", "n", "v"}. -> sanity log entries (sg-1 holds with
    their inputs; vg-1 holds are only counted)."""
    out = []
    for q in (row.get("quotes") or []) + (row.get("kalshi") or []) + (row.get("alternates") or []):
        q.pop("x", None)
        sv = scout_view(q, forecasts)
        if sv is None:
            continue
        po, pu, mu, model, fid, iv = sv
        raw = (po, pu)
        line = float(q["line"])
        l10 = ((view_row or {}).get("lines") or {}).get(board_view.line_key(line))
        l10 = list(l10[:4]) if l10 else None
        mkc = board_view.market_at(row.get("market"), q.get("record_id"), line)
        q.pop("cal", None)
        ca = board_view.calibrated(league, row["stat"], po, pu, _view_push(q, forecasts, fid), model, mkc)
        if ca:                                           # cal-mi-1 (REGISTER A8): every surface shows this chance
            ca["f"] = fid
            q["cal"] = ca
            po, pu = ca["po"], ca["pu"]
        x, code = board_view.quote_x(league, row["stat"], po, pu, mu, line, mkc, l10, iv, raw=raw, view=view_row,
                                     now=now)
        q["x"] = x
        if code:
            out.append({"at": now, "event_id": row["event_id"], "key": row["key"], "player": row.get("player"),
                        "player_id": row["player_id"], "stat": row["stat"], "platform": q.get("platform"),
                        "record_id": q.get("record_id"), "line": line, "projection": mu, "p_over": po,
                        "p_under": pu, "lean": board_view.lean_of(po, pu, mu, line), "l10": l10, "model": model,
                        "forecast_id": fid, "via": "forecast" if q.get("forecast_id") == fid else "line_read",
                        "reason": code, "rule": board_view.SANITY_VERSION})
    return out


SANITY_MAX = 3000
SANITY_LOG_MAX_BYTES = 8 * 1024 * 1024


def write_cal_index(root, base_meta, board_rows):
    """<root>/cal.json = {record_id: the quote's cal-mi-1 chance (with its forecast id f)} - read by tracked picks
    (sports/picks.py) and slips so they record / rank the SAME calibrated chance the board showed. -> manifest summary."""
    from .model import cal
    idx = {}
    for r in board_rows:
        for q in (r.get("quotes") or []) + (r.get("kalshi_near") or []):
            if q.get("cal") and q.get("record_id"):
                idx[q["record_id"]] = q["cal"]
    obj, src = cal.load()
    summary = {"version": cal.VERSION, "enabled": cal.enabled(), "weights": src, "sha": (obj or {}).get("sha"),
               "markets_on": sorted(k for k, p in ((obj or {}).get("params") or {}).items() if p.get("on")),
               "quotes": len(idx), "with_market": sum(1 for v in idx.values() if v.get("mode") == "M")}
    # A9a 6: persist the shown (calibrated) chance beside the offer's OWN forecast (a line read's chance belongs to
    # another line's record, so it is never stored); first write wins; never breaks the snapshot
    items = [(q["cal"].get("f"), r.get("stat"), q.get("line"), q["cal"]) for r in board_rows
             for q in (r.get("quotes") or []) if q.get("cal") and q.get("forecast_id")
             and q["cal"].get("f") == q.get("forecast_id")]
    if items:
        try:
            from .model import shown
            summary["shown_new"] = shown.record(base_meta.get("league"), items, base_meta.get("built_at") or now_utc())
        except Exception as e:                          # noqa: BLE001
            summary["shown_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    _write(root / "cal.json", {**base_meta, **summary, "quotes_by_record": idx})
    return summary


def read_cal_index(league, base=None):
    """{record_id: cal dict} from the last snapshot ({} when absent / unreadable)."""
    try:
        return (_read_json(snap_root(base) / league / "cal.json") or {}).get("quotes_by_record") or {}
    except Exception:                                   # noqa: BLE001
        return {}


def write_sanity(root, entries, base_meta, board_rows):
    """Admin sanity log (GET /api/sports/admin/sanity): <root>/sanity.json = the quotes held NOW with their inputs;
    sanity_log.jsonl = append-only, one line per newly held quote (rotated to .1 past 8 MB). -> manifest summary."""
    prev = {(e.get("record_id"), e.get("forecast_id"), e.get("reason"))
            for e in ((_read_json(root / "sanity.json") or {}).get("entries") or [])}
    gate = {}
    for r in board_rows:
        n = sum(1 for q in (r.get("quotes") or []) + (r.get("kalshi_near") or [])
                if (q.get("x") or {}).get("b") == "gate")
        if n:
            gate[r["stat"]] = gate.get(r["stat"], 0) + n
    reasons = {}
    for e in entries:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    summary = {"rule": board_view.SANITY_VERSION, "gate_rule": board_view.GATE_VERSION, "held": len(entries),
               "reasons": reasons, "gate_held_quotes": gate,
               "gate": {st: board_view.gate_state(st) for st in sorted({r["stat"] for r in board_rows})}}
    # "keys": EVERY held (record_id, forecast_id, reason), never truncated - the slip pool reads it, so a held prop is
    # never a slip leg even when the logged entries are cut at SANITY_MAX
    _write(root / "sanity.json", {**base_meta, **summary, "entries": entries[:SANITY_MAX],
                                  "truncated": max(0, len(entries) - SANITY_MAX),
                                  "keys": [[e.get("record_id"), e.get("forecast_id"), e.get("reason")] for e in entries]})
    new = [e for e in entries if (e.get("record_id"), e.get("forecast_id"), e.get("reason")) not in prev]
    if new:
        lp = root / "sanity_log.jsonl"
        try:
            if lp.exists() and lp.stat().st_size > SANITY_LOG_MAX_BYTES:
                os.replace(lp, lp.with_suffix(".jsonl.1"))
            with open(lp, "a", encoding="utf-8") as fh:
                for e in new:
                    fh.write(json.dumps(e, separators=(",", ":"), default=str) + "\n")
        except OSError:
            pass
    summary["new_logged"] = len(new)
    return summary


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


def same_line_forecasts(row, forecasts):
    """N10 (owner re-audit 2026-10-24): every book quoting the SAME line of the same market shows the same Scout
    forecast. Forecasts are made for the target pick'em offers (forward run), so a sportsbook quote (FanDuel 3.5) had
    no forecast of its own while PrizePicks / Underdog at 3.5 did ("No Scout projection yet"). An ordinary quote
    without its own forecast takes the market's Scout (sm-1 / mi-1) forecast at the identical line - the same saved
    distribution, the same chances - marked fc_via = "same_line" (the forecast record still names the offer it was
    made for). Newest forecast first. -> number of quotes joined."""
    from .forecast_detail import kind
    by_line = {}
    for q in row.get("quotes") or []:
        f = forecasts.get(q.get("forecast_id") or "")
        if f and kind(f.get("model_version")) in ("scout", "informed"):
            k = round(float(q["line"]), 3)
            if k not in by_line or f["forecast_at"] > forecasts[by_line[k]]["forecast_at"]:
                by_line[k] = f["forecast_id"]
    n = 0
    for q in row.get("quotes") or []:
        if q.get("forecast_id"):
            continue
        fid = by_line.get(round(float(q["line"]), 3))
        if fid:
            q["forecast_id"], q["fc_via"], q["other_models"] = fid, "same_line", []
            n += 1
    return n


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


RESULTS_EVENTS_PER_PASS = 4


def _event_settlements(con, event_ids):
    """CURRENT settlement per (platform, offer, side) of these events only (records_event index)."""
    cur = {}
    for r in con.execute(f"SELECT payload FROM records WHERE kind='settlement' AND event_id IN "
                         f"({','.join('?' * len(event_ids))})", list(event_ids)):
        s = from_dict(store._unpack(r["payload"]))
        k = (s.platform, s.offer_id, s.side)
        if k not in cur or s.revision > cur[k].revision:
            cur[k] = s
    return list(cur.values())


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
def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_results(con, league, now=None, base=None):
    """The results ledger file (<league>/results.json) from the CURRENT settlements - called by the settle job right
    after it settles (sports/worker.py cmd_settle), so Results moves when games settle, not with the 3-minute board
    build (which no longer decodes every settlement). -> {"rows", "settled_by_model"}."""
    now = now or now_utc()
    root = snap_root(base) / league
    meta = {"version": SNAP_VERSION, "league": league, "built_at": now_utc(), "as_of": now}
    # STREAMED event by event (settle job cap 120 MB): only events holding a forecast are read, and only the current
    # settlements of those events are decoded (a game's pick'em board settles ~2,000 sides; most carry no forecast)
    fc_events = {r[0] for r in con.execute("SELECT DISTINCT event_id FROM records WHERE kind='forecast' AND league = ?",
                                           (league,))}
    st_events = sorted(fc_events & {r[0] for r in con.execute(
        "SELECT DISTINCT event_id FROM records WHERE kind='settlement' AND event_id LIKE ?", (f"{league}:%",))})
    rows = []
    for i in range(0, len(st_events), RESULTS_EVENTS_PER_PASS):
        part = st_events[i:i + RESULTS_EVENTS_PER_PASS]
        rows += results_view(con, league, _event_settlements(con, part), now, meta, rows_only=True)
    obj = results_from_rows(rows, meta)
    names = player_names(con, {r["player_id"] for r in obj["rows"] if r.get("player_id")}, now)
    for r in obj["rows"]:                              # N7: the player's record name, never the lowercase key
        r["player"] = names.get(r.get("player_id"))
    _write(root / "results.json", obj)
    return {"rows": obj["total_rows"], "settled_by_model": {k: v["settled"] for k, v in obj["summary"].items()}}


def player_names(con, pids, as_of=None):
    """{player_id: name} from the latest Player record of each id (indexed records_player scan, chunked)."""
    out, ids = {}, sorted(p for p in pids if p)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        for _, p, _ in _latest(con, "player", f"player_id IN ({','.join('?' * len(part))})", part, as_of):
            if getattr(p, "name", None):
                out[p.player_id] = p.name
    return out


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


def _boot_mean_ci(groups, field, absolute=False, reps=400, seed=7):
    """Same whole-game weighted mean bootstrap without copying every sampled prop.

    Game totals and counts are sufficient for these five mean statistics. Keep
    group order, random draws, null exclusions and quantiles identical to _boot_ci.
    Temporary memory depends on games, not the number of historical forecasts.
    """
    if len(groups) < 2:
        return None
    totals = []
    for group in groups:
        total, count = 0.0, 0
        for row in group:
            value = row.get(field)
            if value is not None:
                total += abs(value) if absolute else value
                count += 1
        totals.append((total, count))
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        total, count = 0.0, 0
        for _ in groups:
            value, n = totals[rng.randrange(len(totals))]
            total += value
            count += n
        if count:
            vals.append(total / count)
    if not vals:
        return None
    vals.sort()
    return [_r(vals[int(0.025 * (len(vals) - 1))], 4), _r(vals[int(0.975 * (len(vals) - 1))], 4)]


def settled_forecast_links(con, setts):
    """(platform, offer_id) -> [forecast_id] for every stored forecast made on an offer that has a settlement, plus
    {forecast_id: Forecast}. THE link between forecasts and settlements (fresh 2026-10-24, owner audit bug 2): the
    worker settles OFFERS (settlement.settle_offer is called without forecast ids - a forecast is not known to the box
    score), so Settlement.affected_forecast_ids is empty on every production settlement and a ledger keyed on it showed
    0 of 2,672 forecasts settled. A forecast names the offer revision it was made for (offer_snapshot_ids); that record's
    identity (source = platform, source_key = offer id) is the settlement's (platform, offer_id). Read side, so every
    settlement already stored (Oct 21 on) links without rewriting a record; ids a settlement does name still count.
    Bounded: only forecasts of events that have a settlement are decoded (records_event index)."""
    want = {(s.platform, s.offer_id) for s in setts}
    evs = sorted({s.event_id for s in setts if s.event_id})
    fcs, links = {}, defaultdict(list)
    for s in setts:
        for fid in s.affected_forecast_ids:
            if fid not in fcs:
                fcs[fid] = store.get(con, fid)
            if fid not in links[(s.platform, s.offer_id)]:
                links[(s.platform, s.offer_id)].append(fid)
    for i in range(0, len(evs), 400):
        part = evs[i:i + 400]
        rows = con.execute(f"SELECT record_id, payload FROM records WHERE kind='forecast' AND event_id IN "
                           f"({','.join('?' * len(part))})", part).fetchall()
        dec = [(r["record_id"], from_dict(store._unpack(r["payload"]))) for r in rows]
        del rows  # Do not retain packed payloads alongside all decoded forecasts.
        orids = sorted({o for _, fc in dec for o in (fc.offer_snapshot_ids or ())})
        ident = {}
        for j in range(0, len(orids), 500):
            chunk = orids[j:j + 500]
            for r in con.execute(f"SELECT record_id, source, source_key FROM records WHERE record_id IN "
                                 f"({','.join('?' * len(chunk))})", chunk):
                ident[r["record_id"]] = (r["source"], r["source_key"])
        for orid in orids:
            if orid not in ident:                       # archived offer revision (sports/archive.py)
                m = store.row_meta(con, orid)
                if m and m.get("kind") == "offer":
                    ident[orid] = (m["source"], m["source_key"])
        for fid, fc in dec:
            for orid in fc.offer_snapshot_ids or ():
                k = ident.get(orid)
                if k in want and fid not in links[k]:
                    links[k].append(fid)
                    fcs.setdefault(fid, fc)
    return links, fcs


def results_view(con, league, setts, now, base_meta, rows_only=False):
    """One ledger row per (forecast, settled offer side pair). Projection accuracy and probability accuracy are computed
    per forecast; returns appear only where a settlement records an actual stake and payout."""
    rows = []
    seen = set()
    by_offer = defaultdict(list)
    for s in setts:
        by_offer[(s.platform, s.offer_id)].append(s)
    links, fcs = settled_forecast_links(con, setts)
    for (plat, oid), ss in by_offer.items():
        for s in ss[:1]:
            for fid in links.get((plat, oid), ()):
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
                from .results_public import binary_scores
                bs = binary_scores(fc.p_over, fc.p_under, outcome)
                err = (actual - fc.projection_mean) if (actual is not None and fc.projection_mean is not None) else None
                rows.append({"forecast_id": fid, "model_version": fc.model_version, "event_id": fc.event_id,
                             "player_id": fc.player_id, "player_key": fc.player_key, "stat": fc.stat,
                             "line": fc.line, "platform": plat, "offer_id": oid, "forecast_at": fc.forecast_at,
                             "projection_mean": fc.projection_mean, "p_over": fc.p_over, "p_under": fc.p_under,
                             "p_push": fc.p_push, "status": status, "outcome": outcome, "actual": actual,
                             "p_outcome": p_out, "brier": _r(brier, 6), "log_loss": _r(logloss, 6),
                             "brier_binary": _r(bs[1], 6) if bs else None,
                             "log_loss_binary": _r(bs[2], 6) if bs else None,
                             "error": _r(err, 4), "settlement_ids": [x.settlement_id for x in ss],
                             "settlement_revision": max(x.revision for x in ss), "rule_version": any_s.rule_version,
                             "settled_at": max((x.settled_at or "") for x in ss) or None,
                             "payouts": [{"side": x.side, "stake": x.stake, "payout": x.payout} for x in ss
                                         if x.stake is not None and x.payout is not None]})
    if rows_only:
        return rows
    return results_from_rows(rows, base_meta)


def results_from_rows(rows, base_meta):
    """The ledger file body (summary per model + newest 2,000 rows) from ledger rows."""
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

        def mbb(x):
            e = [r.get("brier_binary") for r in x if r.get("brier_binary") is not None]
            return sum(e) / len(e) if e else None
        summary[mv] = {
            "settled": len(rr), "games": len(g),
            "void": sum(1 for r in rows if r["model_version"] == mv and r["status"] == "void"),
            "pushes": sum(1 for r in rr if r["outcome"] == "push"),
            "projection": {"n": len(errs), "mae": _r(mae(rr), 4), "mae_ci": _boot_mean_ci(g, "error", absolute=True),
                           "bias": _r(bias(rr), 4), "bias_ci": _boot_mean_ci(g, "error")},
            "probability": {"n": len(rr), "brier": _r(mb(rr), 5), "brier_ci": _boot_mean_ci(g, "brier"),
                            "log_loss": _r(ml(rr), 5), "log_loss_ci": _boot_mean_ci(g, "log_loss"),
                            "brier_binary": _r(mbb(rr), 5), "brier_binary_ci": _boot_mean_ci(g, "brier_binary"),
                            "brier_binary_n": sum(1 for r in rr if r.get("brier_binary") is not None)},
            "returns": {"recorded": sum(len(r["payouts"]) for r in rows if r["model_version"] == mv)}}
    from . import results_public as rp
    hh = rp.head_to_head(rows)
    return {**base_meta, "rows": rows[:2000], "total_rows": len(rows), "summary": summary,
            "head_to_head": {"version": rp.HH_VERSION, "method": rp.HH_METHOD, "rows": hh},
            "method": {"brier": "three-outcome Brier (over / under / push) on the forecast's unconditional chances: "
                                "the sum over the three outcomes, range 0-2 - a 50/50 forecast with no push scores "
                                "0.50 on it (= 2 x the binary Brier 0.25), so ~0.5 means coin-flip skill, not a bug",
                       "brier_binary": "binary Brier on the chance of Over given no push vs the settled Over / Under "
                                       "(pushes left out): range 0-1, 0.25 = always 50%",
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
        s.add_argument("--no-results", action="store_true", help="leave results.json to the settle job")
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
                out[lg] = build(con, lg, now=now, history=not a.no_history, results=not a.no_results)
    print(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    try:
        main()
    except ContractError as e:
        raise SystemExit(str(e))
