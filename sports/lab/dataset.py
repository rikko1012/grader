"""Spec + source rows -> the experiment's dataset (saved verbatim; replay retrains from this snapshot, never from a
newer read of the sources).

Point-in-time: a target row's features use only that player's earlier PLAYED games with start < target start and
known_at <= target start - FORECAST_LEAD (60 min, the historical forecast time). Partitions are by game start
(train_start <= start < train_end <= tune < tune_end <= test < test_end), so every row of one game lands in one
partition. A target row is eligible when the player played and has >= min_prior earlier played games.

Lines for calibration: "offered" = the latest ordinary full-game offer line for that player + game known before the
forecast time (canonical source only); otherwise a labelled "reference" line floor(prior_mean) + 0.5 (NOT an offered
line - reported separately).
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import timedelta

from .. import capabilities, store
from ..contracts import parse_utc
from . import features as freg

FORECAST_LEAD = timedelta(minutes=60)
AUX_FOR = {"min_l5": "min", "starter_l5": "starter", "targets_l5": "targets", "carries_l5": "carries",
           "pass_att_l5": "pass_att", "snap_pct_l5": "snap_pct"}


def _z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def bound(day):
    return f"{day}T00:00:00Z" if len(day) == 10 else day


def split_of(start, spec):
    if bound(spec["train_start"]) <= start < bound(spec["train_end"]):
        return "train"
    if bound(spec["train_end"]) <= start < bound(spec["tune_end"]):
        return "tune"
    if bound(spec["tune_end"]) <= start < bound(spec["test_end"]):
        return "test"
    return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _features(prior, stat, names):
    vals = [p["v"][stat] for p in prior]
    out = {}
    for f in names:
        if f == "prior_mean":
            out[f] = _mean(vals)
        elif f in ("l5_mean", "l10_mean", "l20_mean"):
            out[f] = _mean(vals[-int(f[1:-5]):])
        elif f == "prior_sd":
            out[f] = statistics.stdev(vals) if len(vals) > 1 else None
        elif f == "prior_n":
            out[f] = float(min(len(vals), 82))
        elif f in AUX_FOR:
            out[f] = _mean([p["v"].get(AUX_FOR[f]) for p in prior[-5:]])
    return out


def _offer_lines(league, stat, cutoff):
    """(event_id, player_id) -> [(known_at, line, record_id)] ordinary full-game offers (canonical store)."""
    out = {}
    try:
        with store.opened(read_only=True) as con:
            for rid, o in store.offers(con, league=league, stat=stat, as_of=cutoff, limit=200000):
                if o.scope != "full_game" or o.promotional or not capabilities.is_ordinary(o):
                    continue
                if o.event_id and o.player_id:
                    out.setdefault((o.event_id, o.player_id), []).append((o.known_at or o.observed_at, o.line, rid))
    except FileNotFoundError:
        pass
    return out


def build(spec, raw):
    stat, names = spec["stat"], list(spec["features"])
    for f in ("prior_mean",):
        if f not in names:
            names.append(f)                               # always computed: eligibility + the baseline + ref line
    by_player = {}
    for r in raw:
        by_player.setdefault(r["player_id"], []).append(r)
    lines = _offer_lines(spec["league"], stat, spec["cutoff"]) if spec["source"] == "canonical" else {}
    rows = []
    for pid, rs in by_player.items():
        rs.sort(key=lambda r: (r["start_utc"], r["row_key"]))
        played = [r for r in rs if r["played"] and r["v"].get(stat) is not None]
        for r in rs:
            sp = split_of(r["start_utc"], spec)
            if sp is None or not r["played"] or r["v"].get(stat) is None:
                continue
            fat = _z(parse_utc(r["start_utc"]) - FORECAST_LEAD)
            prior = [p for p in played if p["start_utc"] < r["start_utc"] and p["known_at"] <= fat]
            if len(prior) < spec["min_prior"]:
                continue
            x = _features(prior, stat, names)
            if "home" in names:
                x["home"] = None if r["home"] is None else float(r["home"])
            if "rest_days" in names:
                x["rest_days"] = float(min(10, (parse_utc(r["start_utc"]).date() -
                                                parse_utc(prior[-1]["start_utc"]).date()).days))
            line, src, lref = None, None, None
            cand = [c for c in lines.get((r["game_id"], r["player_id"]), []) if c[0] <= fat]
            if cand:
                k, line, lref = max(cand)
                src = "offered"
            elif x.get("prior_mean") is not None:
                line, src = math.floor(x["prior_mean"]) + 0.5, "reference"
            rows.append({"row_key": r["row_key"], "game_id": r["game_id"], "player_id": pid,
                         "start_utc": r["start_utc"], "forecast_at": fat, "split": sp, "y": float(r["v"][stat]),
                         "x": {k: (None if v is None else round(float(v), 6)) for k, v in sorted(x.items())},
                         "line": line, "line_source": src, "line_ref": lref, "refs": r["refs"][:4],
                         "prior_games": len(prior)})
    rows.sort(key=lambda r: (r["start_utc"], r["row_key"]))
    return rows


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def counts(rows):
    out = {}
    for sp in ("train", "tune", "test"):
        rr = [r for r in rows if r["split"] == sp]
        out[sp] = {"rows": len(rr), "games": len({r["game_id"] for r in rr}),
                   "players": len({r["player_id"] for r in rr}),
                   "first_start": rr[0]["start_utc"] if rr else None, "last_start": rr[-1]["start_utc"] if rr else None,
                   "offered_lines": sum(r["line_source"] == "offered" for r in rr)}
    return out


def validate_features(spec):
    ok = set(freg.available(spec["league"], spec["source"]))
    bad = [f for f in spec["features"] if f not in ok]
    if bad:
        raise ValueError(f"features not available for {spec['league']} / {spec['source']}: {bad}")
