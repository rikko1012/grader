#!/usr/bin/env python3
"""
comparison.py - projection-comparison layer (2026-10-09). CONTEXT ONLY: nothing here changes a projection, a chance,
a grade or a stake.

For one graded prop it shows:
- our projection, its uncertainty, and our Over / Under / push chances at EVERY line on offer for the same market;
- the book lines (betting thresholds, never called projections) with their payouts / prices and timestamps;
- independent projections - when a genuinely independent source is connected (none is today: see SOURCES);
- internal comparisons (our previous model) - labelled internal, never counted as independent opinions;
- evidence against the selected side.

Joins are exact: same player (name + verified team), same stat, same map scope, same scheduled match (start within
JOIN_START_MIN). Anything else is rejected and the reason is recorded, never compared. Lines older than STALE_MIN are
flagged. Agreement is context: no consensus weighting moves a printed chance until it beats the current model on
timestamped forecasts (forward test).
"""
import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "cmp-2"   # cmp-2 (2026-10-09): no value claim, same-side internal comparison, name aliases, supports / concern
JOIN_START_MIN = 45            # same match: scheduled starts within 45 min
STALE_MIN = 15                 # a book line older than this (board refresh is 5 min) is flagged stale

# Every source checked on 2026-10-09 and what it actually provides. "projection" = a predicted total from someone
# else's model; "book_line" = a betting threshold (with payout); "market" = prices on other outcomes.
SOURCES = (
    {"id": "underdog", "kind": "book_line", "status": "connected",
     "note": "Maps 1-2 / Map 1 kills and headshots thresholds, payout multiplier and displayed price per side, alternate lines"},
    {"id": "prizepicks", "kind": "book_line", "status": "unavailable",
     "note": "public API answers with a CAPTCHA (bot protection) - not bypassed"},
    {"id": "oddspapi", "kind": "book_line", "status": "unchecked",
     "note": "free quota exhausted during the 2026-10-09 inventory - player-prop coverage not yet known"},
    {"id": "polymarket", "kind": "market", "status": "no player props",
     "note": "match / map winners and odd/even total kills only"},
)
INDEPENDENT = ()               # connected independent projection feeds: none accessible today


def _norm(s):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _team_same(a, b):
    """Exact name variants only (hltv.team_name_variants) - an academy never matches its main team."""
    if not a or not b:
        return None
    try:
        import hltv
        va, vb = set(hltv.team_name_variants(a)), set(hltv.team_name_variants(b))
    except Exception:
        va, vb = {_norm(a)}, {_norm(b)}
    return bool(va & vb)


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def probs_at(dist, line, _cache=None):
    """(over, under, push) of the grade's OWN distribution at another line (same cut rule as the grader)."""
    k = (dist or {}).get("kind")
    if k == "normal":
        import perf
        return perf.probs(dist["lam"], dist["sd"], line)
    if k == "mix":
        import grader
        return grader.mix_three(line, [tuple(c) for c in dist["cases"]])
    if k == "hs_bb":
        import perf
        pmf = _cache.get("pmf") if _cache is not None else None
        if pmf is None:
            pmf = perf.hs_pmf(dist["lam_kills"], dist["scope"], dist["hs_per_kill"], c=dist.get("c"))
            if _cache is not None:
                _cache["pmf"] = pmf
        return (sum(v for y, v in pmf.items() if y > line), sum(v for y, v in pmf.items() if y < line),
                sum(v for y, v in pmf.items() if y == line))
    return None


def match_offers(res, rows, now=None, board_at=None, aliases=()):
    """Book lines for EXACTLY this market. Returns (offers, rejected) - rejected = [(line, reason)].
    aliases: other exact names for this player (the Underdog name the line was requested under) - team, maps,
    opponent and match time are still verified for every row."""
    g = res.get("grade") or {}
    pb = res.get("player_block") or {}
    nm = res.get("next_match") or {}
    nick = (pb.get("player") or {}).get("nick")
    team = pb.get("playing_for") or pb.get("current_team")
    start = _ts(nm.get("start"))
    prop, scope = g.get("prop"), g.get("scope") or "12"
    now = now or datetime.now(timezone.utc)
    offers, rejected, seen = [], [], set()
    for r in rows or []:
        names = {_norm(nick)} | {_norm(a) for a in aliases if a}
        if _norm(r.get("player")) not in names or r.get("prop") != prop:
            continue
        line = r.get("line")
        if (r.get("scope") or "12") != scope:
            rejected.append((line, f"different maps ({r.get('scope')} vs {scope})")); continue
        same = _team_same(r.get("team"), team)
        if same is False:
            rejected.append((line, f"different team ({r.get('team')} vs {team}) - namesake")); continue
        rs = _ts(r.get("start"))
        if not start or not rs:
            rejected.append((line, "match time unknown - can't verify it's the same match")); continue
        if abs((rs - start).total_seconds()) > JOIN_START_MIN * 60:
            rejected.append((line, f"different match time ({r.get('start')} vs {nm.get('start')})")); continue
        if nm.get("opponent") and r.get("opponent") and _team_same(r.get("opponent"), nm.get("opponent")) is False:
            rejected.append((line, f"different opponent ({r.get('opponent')} vs {nm.get('opponent')})")); continue
        dup = (r.get("id"), line, r.get("higher"), r.get("lower"))
        if dup in seen:
            continue                                   # the same offer listed twice counts once
        seen.add(dup)
        offers.append({"source": "underdog", "id": r.get("id"), "line": line, "alt": bool(r.get("alt")),
                       "mult_over": r.get("higher"), "mult_under": r.get("lower"),
                       "price_over": r.get("price_higher"), "price_under": r.get("price_lower"),
                       "updated_at": r.get("updated_at"), "team_verified": same is True,   # updated_at = line last changed
                       # stale = OUR copy of the board is old (an unchanged line is still current)
                       "stale": bool(board_at and now.timestamp() - board_at > STALE_MIN * 60)})
    offers.sort(key=lambda o: (o["line"], o["alt"]))
    return offers, rejected


def _internal(res):
    """Our PREVIOUS model on the same inputs (labelled internal - never an independent opinion)."""
    gi = res.get("grader_inputs") or {}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    out = []
    if pf.get("g7"):
        try:
            import grader
            gg = grader.grade_leg(dict(gi, perf=pf["g7"]))
            out.append({"label": "Our previous model (G7) - internal comparison, not independent",
                        "projection": round(gg.get("lambda_final"), 2), "p_over": gg.get("p_more"),
                        "p_under": round(1 - gg["p_more"], 4) if gg.get("p_more") is not None else None})
        except Exception:
            pass
    return out


def panel(res, rows, board_at=None, now=None, aliases=()):
    """The comparison panel for one graded prop (None when there is no grade)."""
    g = res.get("grade") or {}
    dist = g.get("dist")
    if not dist or g.get("lambda_final") is None:
        return None
    now = now or datetime.now(timezone.utc)
    sd = g.get("sd") or 0
    sim = res.get("simulation") or {}
    lo, hi = (sim.get("p10"), sim.get("p90")) if sim.get("p10") is not None else (g["lambda_final"] - 1.2816 * sd, g["lambda_final"] + 1.2816 * sd)
    offers, rejected = match_offers(res, rows, now, board_at, aliases)
    cache = {}
    for o in offers:
        po, pu, pp = probs_at(dist, o["line"], cache)
        dec = po + pu
        o.update(p_over=round(po, 4), p_under=round(pu, 4), p_push=round(pp, 4),
                 over_if_decided=round(po / dec, 4) if dec else None, under_if_decided=round(pu / dec, 4) if dec else None)
        side = "Over" if po >= pu else "Under"
        o["model_side"], o["model_chance"] = side, o["over_if_decided"] if side == "Over" else o["under_if_decided"]
        o["vs_projection"] = round(g["lambda_final"] - o["line"], 2)
    # NO value ranking: what an entry actually pays is not verified. Only at EQUAL payouts is one threshold plainly
    # better (lower for an Over, higher for an Under); lines with different payouts are listed, never ranked.
    best = {}
    for s in ("over", "under"):
        cand = [o for o in offers if not o["stale"] and o[f"mult_{s}"] is not None]
        for m in {o[f"mult_{s}"] for o in cand}:
            same = [o for o in cand if o[f"mult_{s}"] == m]
            if len({o["line"] for o in same}) >= 2:
                b = min(same, key=lambda o: o["line"]) if s == "over" else max(same, key=lambda o: o["line"])
                best.setdefault(s, []).append({"line": b["line"], "chance": b[f"{s}_if_decided"], "mult": m})
    payouts_differ = {s: len({o[f"mult_{s}"] for o in offers if o[f"mult_{s}"] is not None}) > 1 for s in ("over", "under")}
    v = res.get("verdict") or {}
    side = "Over" if v.get("side") == "More" else "Under" if v.get("side") == "Less" else None
    against = []
    rf = (res.get("player_block") or {}).get("recent_form") or {}
    if side and rf.get("decided"):
        hit = rf.get("overs", 0) if side == "Over" else rf["decided"] - rf.get("overs", 0)
        if hit / rf["decided"] < 0.5:
            against.append(f"Last {rf['decided']} decided matches: {side} at this line {hit}/{rf['decided']}")
    internal = _internal(res)
    for it in internal:
        it["p_side"] = (it.get("p_over") if side == "Over" else it.get("p_under")) if side else None
        if side and it["p_side"] is not None and it["p_side"] < 0.5:
            against.append(f"Our previous model has the {side} at {it['p_side']:.0%} (internal)")
    if side and abs(g["lambda_final"] - g["line"]) < 1.0:
        against.append(f"Projection {g['lambda_final']:.1f} is within 1 of the line")
    for f in g.get("flags") or []:
        if f.startswith("OLD PROJECTION"):
            against.append("Projection from the older model (no HLTV data for this player or matchup)")
        elif f.startswith("PROVISIONAL"):
            against.append("Provisional projection from BO3.gg history (HLTV has none)")
        elif "thin history" in f:
            against.append("Thin history")
    supports = []
    if side:
        gap = g["lambda_final"] - g["line"]
        if (gap > 0) == (side == "Over") and abs(gap) >= 1.0:
            supports.append(f"Projection {g['lambda_final']:.1f} is {abs(gap):.1f} {'above' if gap > 0 else 'below'} the line")
        if rf.get("decided"):
            hit = rf.get("overs", 0) if side == "Over" else rf["decided"] - rf.get("overs", 0)
            if hit / rf["decided"] >= 0.6:
                supports.append(f"Last {rf['decided']} decided matches: {side} at this line {hit}/{rf['decided']}")
        for it in internal:
            if it["p_side"] is not None and it["p_side"] >= 0.55:
                supports.append(f"Our previous model also has the {side} ({it['p_side']:.0%}, internal)")
    one = next((o for o in offers if o["line"] == g.get("line") and not o["alt"]), None)
    sens = None
    if side and one is not None:                          # how much a one-kill / one-HS move in the line matters
        step = 1.0
        nxt = probs_at(dist, g["line"] + (step if side == "Over" else -step), cache)
        if nxt:
            d_ = nxt[0] + nxt[1]
            sens = {"line": g["line"] + (step if side == "Over" else -step),
                    "chance": round((nxt[0] if side == "Over" else nxt[1]) / d_, 4) if d_ else None}
    return {
        "version": VERSION, "at": now.isoformat(timespec="seconds"),
        "supports": supports, "concern": against[0] if against else None, "one_worse": sens,
        "payouts_differ": payouts_differ,
        "ours": {"projection": round(g["lambda_final"], 2), "sd": round(sd, 2), "range80": [round(lo, 1), round(hi, 1)],
                 "line": g.get("line"), "side": side, "chance": v.get("p")},
        "independent": {"count": len(INDEPENDENT), "projections": [], "consensus": None,
                        "note": "No independent projection source is connected - checked: "
                                + "; ".join(f"{s['id']}: {s['status']}" for s in SOURCES if s["kind"] != "book_line" or s["status"] != "connected")},
        "internal": internal,
        "offers": offers, "rejected": [{"line": l, "reason": r} for l, r in rejected],
        "best_line": best, "evidence_against": against,
        "board_at": board_at, "stale_board": bool(board_at and now.timestamp() - board_at > STALE_MIN * 60),
        # the grade's own chance can sit below the distribution's at its line (old-pipeline safety margins / kills cap)
        "grade_vs_table": (round(v.get("p") - next((o["model_chance"] for o in offers if o["line"] == g.get("line") and not o["alt"]
                                                    and o["model_side"] == side), v.get("p")), 4) if v.get("p") is not None else None),
    }


# ---------------------------------------------------------------- immutable snapshots (append-only, changes only)
_SNAP_LOCK = threading.Lock()
_LAST = {}


def snapshot_path():
    try:
        from bo3 import DATA_DIR
    except Exception:
        DATA_DIR = os.environ.get("CS2_DATA_DIR") or "."
    return Path(DATA_DIR) / "comparison_snapshots.jsonl"


def snapshot(res, pnl, model_version, key=None):
    """Append the panel with our forecast, every source's state and the versions - only when something changed for
    this line since its last snapshot (history is never overwritten). Returns True when written."""
    if not pnl:
        return False
    g = res.get("grade") or {}
    key = key or f"{_norm((res.get('player_block') or {}).get('player', {}).get('nick'))}|{g.get('prop')}|{g.get('scope')}|{(res.get('next_match') or {}).get('slug')}"
    core = {"ours": pnl["ours"], "offers": [{k: o.get(k) for k in ("id", "line", "mult_over", "mult_under", "price_over", "price_under", "p_over", "p_under")}
                                           for o in pnl["offers"]],
            "internal": pnl["internal"], "independent": pnl["independent"]["count"], "rejected": pnl["rejected"]}
    h = hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()
    with _SNAP_LOCK:
        if _LAST.get(key) == h:
            return False
        rec = dict(pnl, key=key, model_version=model_version, match=(res.get("next_match") or {}).get("slug"),
                   sources=[{k: s[k] for k in ("id", "kind", "status")} for s in SOURCES], hash=h)
        try:
            with open(snapshot_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n"); f.flush(); os.fsync(f.fileno())
            _LAST[key] = h
            return True
        except OSError:
            return False


def line_check(board, player, prop, scope, line=None, team=None, start=None, now=None):
    """Before a pick goes on a slip: what Underdog shows NOW for this player / stat / maps (same team and match time
    when given). same_line = the exact standard line is still up; changed = it isn't (moved, pulled or re-priced)."""
    now = now or datetime.now(timezone.utc)
    at = (board or {}).get("at") or 0
    st = _ts(start)
    out = []
    for r in (board or {}).get("rows") or []:
        if _norm(r.get("player")) != _norm(player) or r.get("prop") != prop or (r.get("scope") or "12") != (scope or "12"):
            continue
        if team and _team_same(r.get("team"), team) is False:
            continue
        rs = _ts(r.get("start"))
        if st and rs and abs((rs - st).total_seconds()) > JOIN_START_MIN * 60:
            continue
        out.append({k: r.get(k) for k in ("id", "line", "higher", "lower", "alt", "start", "team", "opponent")})
    try:
        want = float(line) if line not in (None, "") else None
    except ValueError:
        want = None
    same = [o for o in out if want is not None and o["line"] == want and not o["alt"]]
    return {"at": at, "stale": bool(at and now.timestamp() - at > STALE_MIN * 60), "error": (board or {}).get("error"),
            "rows": sorted(out, key=lambda o: (o["line"], o["alt"])), "same_line": bool(same),
            "changed": want is not None and not same,
            "note": None if same else ("Underdog has no line for this player / stat / maps right now" if not out
                                       else f"Underdog now shows {', '.join(str(o['line']) + (' (alt)' if o['alt'] else '') for o in out)}")}
