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

VERSION = "cmp-3"   # cmp-3: stable Underdog->player ids (no requested-name alias), ambiguity, grounded evidence (basis)
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


def match_offers(res, rows, now=None, board_at=None, id_map=None):
    """Book lines for EXACTLY this market. Returns (offers, rejected) - rejected = [(line, reason)].
    Identity: Underdog's player id -> our BO3.gg player id (id_map, learnt from board grades) when known; otherwise
    the exact normalised name. Team, opponent, maps and match time are verified for EVERY row either way. Two
    different Underdog players fitting the same market = ambiguous -> nothing matched."""
    g = res.get("grade") or {}
    pb = res.get("player_block") or {}
    nm = res.get("next_match") or {}
    nick = (pb.get("player") or {}).get("nick")
    our_id = (pb.get("player") or {}).get("id")
    id_map = id_map or {}
    team = pb.get("playing_for") or pb.get("current_team")
    start = _ts(nm.get("start"))
    prop, scope = g.get("prop"), g.get("scope") or "12"
    now = now or datetime.now(timezone.utc)
    offers, rejected, seen = [], [], set()
    for r in rows or []:
        if r.get("prop") != prop:
            continue
        mapped = id_map.get(r.get("ud_player_id")) if r.get("ud_player_id") is not None else None
        if mapped is not None:
            if our_id is None or mapped != our_id:
                if _norm(r.get("player")) == _norm(nick):
                    rejected.append((r.get("line"), "different player (Underdog id belongs to another player)"))
                continue
            ident = "id"
        else:
            if _norm(r.get("player")) != _norm(nick):
                continue
            ident = "name"
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
        offers.append({"source": "underdog", "id": r.get("id"), "line": line, "alt": bool(r.get("alt")), "identity": ident,
                       "ud_player_id": r.get("ud_player_id"),
                       "mult_over": r.get("higher"), "mult_under": r.get("lower"),
                       "price_over": r.get("price_higher"), "price_under": r.get("price_lower"),
                       "updated_at": r.get("updated_at"), "team_verified": same is True,   # updated_at = line last changed
                       # stale = OUR copy of the board is old (an unchanged line is still current)
                       "stale": bool(board_at and now.timestamp() - board_at > STALE_MIN * 60)})
    if len({o["ud_player_id"] for o in offers if o["ud_player_id"] is not None}) > 1:
        rejected += [(o["line"], "ambiguous: two different Underdog players fit this market") for o in offers]
        offers = []
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


def panel(res, rows, board_at=None, now=None, id_map=None):
    """The comparison panel for one graded prop (None when there is no grade)."""
    g = res.get("grade") or {}
    dist = g.get("dist")
    if not dist or g.get("lambda_final") is None:
        return None
    now = now or datetime.now(timezone.utc)
    sd = g.get("sd") or 0
    sim = res.get("simulation") or {}
    lo, hi = (sim.get("p10"), sim.get("p90")) if sim.get("p10") is not None else (g["lambda_final"] - 1.2816 * sd, g["lambda_final"] + 1.2816 * sd)
    offers, rejected = match_offers(res, rows, now, board_at, id_map)
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
    # evidence, each with its basis: "model" = an input / output of the calculation that made the chance;
    # "stake" = used only by a stake rule; "context" = shown for context, NOT in the projection; "data" = data status
    perf_on = str(g.get("projection_source") or "").startswith("perf")
    rf = (res.get("player_block") or {}).get("recent_form") or {}
    supports, against = [], []
    def add(lst, text, basis):
        lst.append({"text": text, "basis": basis})
    if side:
        gap = g["lambda_final"] - g["line"]
        if (gap > 0) == (side == "Over") and abs(gap) >= 1.0:
            add(supports, f"Projection {g['lambda_final']:.1f} is {abs(gap):.1f} {'above' if gap > 0 else 'below'} the line", "model")
        if abs(gap) < 1.0:
            add(against, f"Projection {g['lambda_final']:.1f} is within 1 of the line", "model")
        if rf.get("decided"):
            hit = rf.get("overs", 0) if side == "Over" else rf["decided"] - rf.get("overs", 0)
            # perf model: last-10 is not in the projection (context, or a stake rule when a FORM / TAPE FIGHT fired);
            # the older pipeline blends recent form into its projection (model)
            basis = "model" if not perf_on else "stake" if (g.get("form_fight") or g.get("tape_fight")) else "context"
            txt = f"Last {rf['decided']} decided matches: {side} at this line {hit}/{rf['decided']}"
            if hit / rf["decided"] >= 0.6:
                add(supports, txt, basis)
            elif hit / rf["decided"] < 0.5:
                add(against, txt, basis)
    internal = _internal(res)
    for it in internal:
        it["p_side"] = (it.get("p_over") if side == "Over" else it.get("p_under")) if side else None
        if side and it["p_side"] is not None:
            if it["p_side"] < 0.5:
                add(against, f"Our previous model has the {side} at {it['p_side']:.0%}", "context")
            elif it["p_side"] >= 0.55:
                add(supports, f"Our previous model also has the {side} ({it['p_side']:.0%})", "context")
    for f in g.get("flags") or []:
        if f.startswith("OLD PROJECTION"):
            add(against, "Projection from the older model (no HLTV data for this player or matchup)", "data")
        elif f.startswith("PROVISIONAL"):
            add(against, "Provisional projection from BO3.gg history (HLTV has none)", "data")
        elif "thin history" in f:
            add(against, "Thin history", "data")
    sens = None
    if side and any(o["line"] == g.get("line") and not o["alt"] for o in offers):   # a one-kill / one-HS worse line
        nl = g["line"] + (1.0 if side == "Over" else -1.0)
        nxt = probs_at(dist, nl, cache)
        if nxt and nxt[0] + nxt[1]:
            sens = {"line": nl, "chance": round((nxt[0] if side == "Over" else nxt[1]) / (nxt[0] + nxt[1]), 4)}
    return {
        "version": VERSION, "at": now.isoformat(timespec="seconds"),
        # the one-line concern prefers what the calculation itself says over context
        "supports": supports, "concern": next((x for x in against if x["basis"] in ("model", "data")), against[0] if against else None),
        "one_worse": sens, "projection_in_model": perf_on,
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


def line_check(board, player, prop, scope, line=None, team=None, start=None, now=None, pid=None, id_map=None, failed=False):
    """Before a pick goes on a slip / is copied: what Underdog shows NOW for this player / stat / maps.
    status: "verified" (the exact standard line is up) / "changed" (other line(s) for the same player + market) /
    "unavailable" (Underdog shows nothing for it) / "ambiguous" (two Underdog players fit) / "failed" (feed error).
    Identity as match_offers: Underdog id -> our player id when known (pid), else the exact name; team + match time."""
    now = now or datetime.now(timezone.utc)
    at = (board or {}).get("at") or 0
    if failed or (board or {}).get("error") and not (board or {}).get("rows"):
        return {"status": "failed", "at": at, "rows": [], "note": "Couldn't reach Underdog to check this line"}
    id_map = id_map or {}
    st = _ts(start)
    out = []
    for r in (board or {}).get("rows") or []:
        if r.get("prop") != prop or (r.get("scope") or "12") != (scope or "12"):
            continue
        mapped = id_map.get(r.get("ud_player_id")) if r.get("ud_player_id") is not None else None
        if mapped is not None and pid:
            if str(mapped) != str(pid):
                continue
        elif _norm(r.get("player")) != _norm(player):
            continue
        if team and _team_same(r.get("team"), team) is False:
            continue
        rs = _ts(r.get("start"))
        if st and rs and abs((rs - st).total_seconds()) > JOIN_START_MIN * 60:
            continue
        out.append({k: r.get(k) for k in ("id", "line", "higher", "lower", "alt", "start", "team", "opponent", "ud_player_id")})
    try:
        want = float(line) if line not in (None, "") else None
    except ValueError:
        want = None
    base = {"at": at, "stale": bool(at and now.timestamp() - at > STALE_MIN * 60), "rows": sorted(out, key=lambda o: (o["line"], o["alt"]))}
    if len({o["ud_player_id"] for o in out if o["ud_player_id"] is not None}) > 1:
        return dict(base, status="ambiguous", rows=[], note="Two different Underdog players fit - not matched")
    same = [o for o in out if want is not None and o["line"] == want and not o["alt"]]
    if same:
        return dict(base, status="verified", note=None)
    if out:
        std = [o for o in out if not o["alt"]]
        return dict(base, status="changed", new_line=(std or out)[0]["line"],
                    note="Underdog now shows " + ", ".join(str(o["line"]) + (" (alt)" if o["alt"] else "") for o in base["rows"]))
    return dict(base, status="unavailable", note="Underdog has no line for this player / stat / maps right now")
