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

VERSION = "cmp-5"   # cmp-5: book-aware line checks (PrizePicks market types, feed state); cmp-4: PrizePicks lines (via the HLTV scraper database) next to Underdog's; cmp-3: cmp-3: stable Underdog->player ids (no requested-name alias), ambiguity, grounded evidence (basis)
JOIN_START_MIN = 45            # same match: scheduled starts within 45 min
STALE_MIN = 15                 # a book line older than this (board refresh is 5 min) is flagged stale

# Every source checked on 2026-10-09 and what it actually provides. "projection" = a predicted total from someone
# else's model; "book_line" = a betting threshold (with payout); "market" = prices on other outcomes.
SOURCES = (
    {"id": "underdog", "kind": "book_line", "status": "connected",
     "note": "Maps 1-2 / Map 1 kills and headshots thresholds, payout multiplier and displayed price per side, alternate lines"},
    {"id": "prizepicks", "kind": "book_line", "status": "connected",
     "note": "via the HLTV scraper's database (its props workflow reads PrizePicks about hourly; PrizePicks shows this "
             "server a CAPTCHA, not bypassed): Map 1 / Maps 1-2 / Maps 1-3 kills and headshots, standard and goblin / demon"},
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


# Explicit, source-backed team aliases (Underdog name, our BO3.gg name). Exact pairs only - region or roster-version
# suffixes are NEVER stripped globally (".AU" or "v6" can mark a different team / lineup). Each entry records its proof.
VERIFIED_TEAM_ALIASES = (
    ("Ground Zero.AU", "Ground Zero", "2026-10-09: all 5 Underdog players (Omichella, Sliimey, apocdud, hazr, vision) resolved by "
                                      "their own board grades to BO3.gg 'Ground Zero' in ground-zero-vs-killzone-09-10-2026; opponent KZG on both"),
    ("Desi Boyz", "Desi Boyz v6", "2026-10-09: all 4 listed Underdog players (MoMo, Shooter, jsfeltner, mason) resolved to BO3.gg "
                                  "'Desi Boyz v6' in desi-boyz-v6-vs-empire-10-10-2026; opponent EMPIRE on both"),
)


def _team_same(a, b):
    """Exact name variants only (hltv.team_name_variants) plus VERIFIED_TEAM_ALIASES - an academy never matches its
    main team."""
    if not a or not b:
        return None
    pair = {_norm(a), _norm(b)}
    if any(pair == {_norm(u), _norm(o)} for u, o, _ in VERIFIED_TEAM_ALIASES):
        return True
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


def record_for_scope(pb, scope):
    """His last-10 record that matches the line's maps: Map 1 lines -> his Map 1 totals, Maps 1-2 -> Maps 1-2 totals,
    Maps 1-3 -> his Maps 1-3 totals (records.py, every map played up to map 3). Never a Maps 1-2 record for another scope."""
    sc = scope or "12"
    return (pb.get("recent_form_map1") or {}) if sc == "1" else (pb.get("recent_form_123") or {}) if sc == "123" else (pb.get("recent_form") or {})


def source_label(g):
    """WHERE the projection came from - a source label, not a quality judgement."""
    ps = str(g.get("projection_source") or "")
    if ps.startswith("perf") and not ps.endswith("-bo3"):
        return f"HLTV (ends {g['hltv_ends']})" if g.get("hltv_ends") else "HLTV"
    if ps.endswith("-bo3"):
        return "BO3.gg"
    return "older model (HLTV unreachable)" if "unreachable" in ps else "older model (BO3.gg)"


def data_status(g, v):
    """Data QUALITY, kept apart from the source and from the model's confidence: ["ok" | "limited" | "verify", reason].
    "verify" only for a concrete unresolved issue: no match found (identity), roster not confirmed, stand-in, or stale
    inputs (long gap since the last official map). A BO3.gg-only source is NOT by itself a reason to verify."""
    fl = " | ".join(g.get("flags") or [])
    if (v or {}).get("state") == "SCOUT" or "0 units: SCOUT" in fl:
        return ["verify", "no upcoming match found"]
    for key, why in (("Gate C: roster", "roster not confirmed"), ("STAND-IN", "stand-in"), ("RUST:", "stale: long gap since the last official map")):
        if key in fl:
            return ["verify", why]
    if "HLTV STALE" in fl:
        return ["verify", f"HLTV history ends {g.get('hltv_ends') or 'early'} - newer matches only on BO3.gg"]
    if "thin history" in fl or "Gate D" in fl or "Thin data" in fl:
        return ["limited", "small recent sample"]
    return ["ok", ""]


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


def match_offers(res, rows, now=None, board_at=None, id_map=None, other_scope=None):
    """Book lines for EXACTLY this market. Returns (offers, rejected) - rejected = [(line, reason)].
    Identity: Underdog's player id -> our BO3.gg player id (id_map, learnt from board grades) when known; otherwise
    the exact normalised name. Team, opponent, maps and match time are verified for EVERY row either way. Two
    different Underdog players fitting the same market = ambiguous -> nothing matched.
    other_scope (a list, optional): filled with the SAME player / team / match's lines on OTHER maps ({source, line,
    scope, alt, odds_type}) - 2026-10-12: Spinx's 26 HS was a Maps 1-3 line (BO5) graded by a member on Maps 1-2."""
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
        if (r.get("scope") or "12") != scope:          # same player, team and match - other maps: never compared, named
            rejected.append((line, f"different maps ({r.get('scope')} vs {scope})"))
            if other_scope is not None and (r.get("book"), line, r.get("scope")) not in {(o["source"], o["line"], o["scope"]) for o in other_scope}:
                other_scope.append({"source": r.get("book") or "underdog", "line": line, "scope": r.get("scope") or "12",
                                    "alt": bool(r.get("alt")), "odds_type": r.get("odds_type")})
            continue
        dup = (r.get("id"), line, r.get("higher"), r.get("lower"))
        if dup in seen:
            continue                                   # the same offer listed twice counts once
        seen.add(dup)
        offers.append({"source": r.get("book") or "underdog", "id": r.get("id"), "line": line, "alt": bool(r.get("alt")), "identity": ident,
                       "odds_type": r.get("odds_type"), "open_line": r.get("open_line"), "moves": r.get("moves"),
                       "ud_player_id": r.get("ud_player_id"),
                       "mult_over": r.get("higher"), "mult_under": r.get("lower"),
                       "price_over": r.get("price_higher"), "price_under": r.get("price_lower"),
                       "updated_at": r.get("updated_at"), "team_verified": same is True,   # updated_at = line last changed
                       # stale = OUR copy of the board is old (an unchanged line is still current)
                       "stale": bool(r["stale"]) if "stale" in r     # PrizePicks: the age of its own feed (hltv.py)
                       else bool(board_at and now.timestamp() - board_at > STALE_MIN * 60)})
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
                        "p_under": round(1 - gg["p_more"], 4) if gg.get("p_more") is not None else None,
                        # chances at the GRADED line (the same line as ours.chance_at_line); no spread - the model's spread is
                        # part of the private recipe (protect_recipe strips grade.sd; spread_differs says it in words)
                        "line": (res.get("grade") or {}).get("line")})
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
    other = []
    offers, rejected = match_offers(res, rows, now, board_at, id_map, other_scope=other)
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
    # the record that matches the line's maps (2026-10-10: a Map 1 line was compared with Maps 1-2 totals - "10/10
    # Overs" next to a Map 1 history of 6/10); Maps 1-3 has no record of its own, so no line comparison at all
    rf = record_for_scope(res.get("player_block") or {}, g.get("scope"))
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
    # our chance at the GRADED line, side as graded, pushes left out exactly like the card (2026-10-11: the page took
    # "Our model" at Underdog's line next to the previous model at the graded line - two different lines in one row)
    p_line = (g.get("p_more") if side == "Over" else g.get("p_less")) if side else None
    for it in internal:
        it["p_side"] = (it.get("p_over") if side == "Over" else it.get("p_under")) if side else None
        # a higher projection with a LOWER Over chance (or the reverse) comes from a different spread, not an error
        dp = (it.get("projection") or 0) - g["lambda_final"]
        do = (it.get("p_over") if it.get("p_over") is not None else 0.5) - (g.get("p_more") if g.get("p_more") is not None else 0.5)
        it["spread_differs"] = bool(abs(dp) >= 0.05 and abs(do) >= 0.001 and (dp > 0) != (do > 0))
        if side and it["p_side"] is not None:
            if it["p_side"] < 0.5:
                add(against, f"Our previous model has the {side} at {it['p_side']:.0%}", "context")
            elif it["p_side"] >= 0.55:
                add(supports, f"Our previous model also has the {side} ({it['p_side']:.0%})", "context")
    for f in g.get("flags") or []:
        if f.startswith("OLD PROJECTION") and "HLTV database unreachable" in f:
            add(against, "HLTV database unreachable right now - temporary projection from the older model", "data")
        elif f.startswith("OLD PROJECTION"):
            add(against, "Projection from the older model (no HLTV data for this player or matchup)", "data")
        elif f.startswith("HLTV STALE"):
            add(against, f"HLTV history ends {g.get('hltv_ends') or 'early'} - his newer matches are only on BO3.gg", "data")
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
        "ours": {"projection": round(g["lambda_final"], 2), "range80": [round(lo, 1), round(hi, 1)],
                 "line": g.get("line"), "side": side, "chance": v.get("p"), "chance_at_line": p_line,
                 "p_over_at_line": g.get("p_more")},
        "independent": {"count": len(INDEPENDENT), "projections": [], "consensus": None,
                        "note": "No independent projection source is connected - checked: "
                                + "; ".join(f"{s['id']}: {s['status']}" for s in SOURCES if s["kind"] != "book_line" or s["status"] != "connected")},
        "internal": internal,
        "offers": offers, "rejected": [{"line": l, "reason": r} for l, r in rejected],
        "other_scope": sorted(other, key=lambda o: (o["scope"], o["source"], o["line"])),
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


def line_check(board, player, prop, scope, line=None, team=None, start=None, now=None, pid=None, id_map=None, failed=False,
               book="underdog", odds_type="standard"):
    """Before a pick goes on a slip / is copied: what the platform shows NOW for this player / stat / maps.
    status: "verified" (the exact line of the same market type is up and the feed is fresh) / "changed" (other line(s)
    for the same player + market) / "unavailable" (nothing for it) / "ambiguous" (two players fit) / "failed" (feed error
    or, for PrizePicks, a stale feed - a stale line is never reported as verified).
    Identity as match_offers: provider id -> our player id when known (pid), else the exact name; team + match time.
    odds_type: standard / goblin / demon - a goblin pick is only verified against a goblin line (PrizePicks)."""
    name = "PrizePicks" if book == "prizepicks" else "Underdog"
    now = now or datetime.now(timezone.utc)
    at = (board or {}).get("at") or 0
    if failed or (board or {}).get("error") and not (board or {}).get("rows"):
        return {"status": "failed", "book": book, "at": at, "rows": [], "note": f"Couldn't reach {name} to check this line"}
    if book == "prizepicks" and (board or {}).get("state") not in ("ok", "empty"):
        return {"status": "failed", "book": book, "at": at, "rows": [],
                "note": f"PrizePicks feed is {(board or {}).get('state') or 'unavailable'} - this line can't be confirmed right now"}
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
        out.append(dict({k: r.get(k) for k in ("id", "line", "higher", "lower", "alt", "start", "team", "opponent", "ud_player_id")},
                        odds_type=r.get("odds_type") or ("alt" if r.get("alt") else "standard")))
    try:
        want = float(line) if line not in (None, "") else None
    except ValueError:
        want = None
    base = {"book": book, "at": at, "stale": bool(at and now.timestamp() - at > STALE_MIN * 60) if book == "underdog" else False,
            "rows": sorted(out, key=lambda o: (o["line"], o["alt"]))}
    if len({o["ud_player_id"] for o in out if o["ud_player_id"] is not None}) > 1:
        return dict(base, status="ambiguous", rows=[], note=f"Two different {name} players fit - not matched")
    kind = odds_type or "standard"
    match_kind = (lambda o: not o["alt"]) if kind == "standard" else (lambda o: o["odds_type"] == kind)
    same = [o for o in out if want is not None and o["line"] == want and match_kind(o)]
    if same:
        return dict(base, status="verified", note=None)
    if out:
        pref = [o for o in out if match_kind(o)] or [o for o in out if not o["alt"]] or out
        lab = lambda o: str(o["line"]) + ("" if not o["alt"] else f" ({o['odds_type'] if book == 'prizepicks' else 'alt'})")
        return dict(base, status="changed", new_line=pref[0]["line"], new_odds_type=pref[0]["odds_type"],
                    note=f"{name} now shows " + ", ".join(lab(o) for o in base["rows"]))
    return dict(base, status="unavailable", note=f"{name} has no line for this player / stat / maps right now")
