"""Slip Studio leg selection sq-1: which legs a generated slip uses, and what the slip adder says about a leg. Pure functions, no I/O.

Why (owner 2026-11-04, "it's wonky ... not just random props"): the on-page generator (web/upgrade.js generateBatch, until sq-1)
kept only props whose published chance sat inside a target window (default 60% +/- 5, so every prop above 65% was dropped),
paired each anchor with the least-used players sorted by closeness to the anchor's chance, and treated NBA/NFL research letters
like CS2 live grades. Inside the window the published chance does not separate on settled CS2 results (P1: 55-60% won 52.3%,
60-65% won 48.5%), so the slips were close to a random draw from the window.

sq-1, in plain words:
  1. Pool. Only PrizePicks / Underdog lines that are open now: the platform feed reads ok, the start is at least LEAD_S away, the
     row is not held, Scout's side is offered, the chance is a number. CS2 needs a live letter (A+ to B) with a stake, or a 0U
     stake that only the same-match exposure cap took away. LoL needs the model's letter (A+ to B; LoL letters carry 0 units).
     NBA / NFL letters are research estimates: left out unless the member asks for them, and then marked. A held or contained
     prop is never a leg (a letter held by letter_containment lc-1: "contained", no rating). A degenerate chance
     (DEGENERATE_P or more, or a projection near zero against the line) is never a leg.
  2. Rank. Each leg's chance to hit is what its band has won on settled results: the band is sport x stat x side x chance band
     (BANDS). The band's settled hit rate is shrunk by PRIOR_K picks toward its stat's settled line at the leg's chance (least
     squares of hit on printed chance per sport x stat, itself shrunk by LINE_K picks toward "the published chance is right"). So a
     stat whose chances separate on settled results (LoL kills) keeps its order, and one whose chances do not (CS2 above 55%: P1
     slope near zero) ranks by the bands that actually held up. research/slip_quality_2026_11_04/variants.py compares this rule
     with the published chance and two earlier drafts. CS2 bands come from the P1 export (standard board lines, pushes excluded),
     LoL bands from the LoL worker's settled pre-match forecasts on offered sides; nothing settled = the published chance. Research
     estimates have no settled record: their distance from 50% counts RESEARCH_WEIGHT. The published chance shown never changes.
  3. Build. A slip maximises the chance all its legs hit: the product of the ranked chances, with legs from different matches
     treated as independent. A push is not a win: a whole-number line's push chance is taken off. Hard rules inside a slip: one
     leg per player (so no conflicting sides), one leg per match unless the member allows same-match legs on a platform that
     publishes them (Underdog) - then the slip says the legs share a match and no all-hit chance is given - the platform's entry
     rules (workspace_rules.entry_status: two teams, sizes, one platform, mixed sports only where published), 2-6 picks.
  4. Batch. Slips are built one after another. No repeat props: a prop (player x match x stat x period, every line and side) is
     used once in the batch. Repeats allowed: a prop or player already used costs REUSE_PENALTY per use, so the batch still spreads
     out; the same slip is never built twice. Each slip carries one line saying why these legs.
  5. Adder. advise() answers for a member's own slip: conflicts and same-player / same-match facts per leg, the leg's band record,
     and the best replacement (same platform, fits the other legs) when a leg is not in the pool or a pool leg ranks clearly higher.
     Nothing is dropped or reordered: a replacement is only ever applied by the member, in place.

Search: exact branch-and-bound per slip over the best POOL_CAP legs (NODE_LIMIT nodes, reported when reached). Payout: Underdog's
published table when every pick is a 1.0x pick; PrizePicks' verified help-center table (owner 2026-11-05) on standard lines.
EV is never computed here (workspace_rules.
ev_status decides it, only with a verified payout and validated chances)."""
import math
from datetime import datetime, timezone

import workspace_rules as rules

VERSION = "sq-1"
PLATFORMS = ("prizepicks", "underdog")
ENTRY_TYPES = {"prizepicks": ("power", "flex"), "underdog": ("standard", "flex")}
SPORTS = ("cs2", "lol", "nba", "nfl")
RESEARCH_SPORTS = ("nba", "nfl")
SPORT_LABEL = {"cs2": "CS2", "lol": "LoL", "nba": "NBA", "nfl": "NFL"}
CS2_GRADES = ("A+", "A", "A-", "B+", "B")          # B- left out, as the studio and batch builders (no evidence it belongs in slips)
LOL_GRADES = ("A+", "A", "A-", "B+", "B")
RESEARCH_GRADES = ("A", "B", "C")                   # a research "Lean" is not a quality leg
BANDS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80), (0.80, 1.0001))
PRIOR_K = 150                                       # settled picks of shrinkage of a band toward its stat's line
LINE_K = 300                                        # settled picks of shrinkage of a stat's line toward "the published chance is right"
RESEARCH_WEIGHT = 0.5
DEGENERATE_P = 0.95
NEAR_ZERO_PROJ = 0.10                               # projection under 10% of the line: a near-zero projection, never a leg
LEAD_S = 120                                        # a line that starts within 2 minutes is not offered for a new slip
MIN_PICKS, MAX_PICKS, MAX_SLIPS = 2, 6, 12
POOL_CAP = 60
NODE_LIMIT = 20000
REUSE_PENALTY = 0.35                                # log-chance per earlier use of the same prop or player (repeats allowed)
REPLACE_GAIN = 0.02                                 # a replacement is suggested when it ranks this much higher
STAT_WORD = {"hs": "HS", "kills": "Kills", "assists": "Assists", "deaths": "Deaths"}


# ---------------------------------------------------------------------------------------------------------------- calibration

def band_index(p):
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= p < hi:
            return i
    return None


def band_label(i):
    lo, hi = BANDS[i]
    return f"{round(lo * 100)}–{min(100, round(hi * 100))}%"


def units_from_p1(export):
    """CS2 settled units from the stored P1 export: the favoured side at its printed chance, hit or not (pushes are already out)."""
    out = []
    for r in (export or {}).get("rows") or []:
        p, y = r.get("p"), r.get("over")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or y not in (0, 1) or not 0 < p < 1:
            continue
        over = p >= 0.5
        out.append({"sport": "cs2", "stat": r.get("prop"), "side": "over" if over else "under", "printed": max(p, 1 - p),
                    "hit": int(y if over else 1 - y), "match": r.get("match"), "start": r.get("start")})
    return out


def units_from_lol(rows):
    """LoL settled units: rows of {stat, p_over, p_under, model_side, sid, over_hit, series_start} (graded, side offered)."""
    out = []
    for r in rows or []:
        po, pu, side, y = r.get("p_over"), r.get("p_under"), r.get("model_side"), r.get("over_hit")
        if side not in ("over", "under") or y not in (0, 1) or not isinstance(po, (int, float)) or not isinstance(pu, (int, float)):
            continue
        printed = po if side == "over" else pu
        if not 0.5 <= printed < 1:
            continue
        out.append({"sport": "lol", "stat": r.get("stat"), "side": side, "printed": printed,
                    "hit": int(y if side == "over" else 1 - y), "match": r.get("sid"), "start": r.get("series_start")})
    return out


def _cell(us):
    n = len(us)
    return {"n": n, "matches": len({u["match"] for u in us}), "printed": sum(u["printed"] for u in us) / n,
            "observed": sum(u["hit"] for u in us) / n} if n else None


def _line(us, k_line):
    """Settled hit against the printed chance for one sport x stat: least squares hit = a + b x (printed - 0.5), shrunk toward
    a = 0.5, b = 1 (the published chance is right) by k_line picks. A stat whose chances do not separate gets a flat line."""
    n = len(us)
    xs = [u["printed"] - 0.5 for u in us]
    ys = [u["hit"] for u in us]
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx > 1e-12 else 1.0
    a = my - b * mx
    w = n / (n + k_line)
    return {"n": n, "matches": len({u["match"] for u in us}), "a": w * a + (1 - w) * 0.5, "b": w * b + (1 - w) * 1.0}


def calibration_table(units, k=PRIOR_K, k_line=LINE_K):
    """The settled record sq-1 ranks by: {cells: {sport|stat|side|band: {n, matches, printed, observed}}, lines: {sport|stat: line}}."""
    groups, stats = {}, {}
    for u in units or ():
        i = band_index(u["printed"])
        if i is None or u.get("stat") is None:
            continue
        groups.setdefault((u["sport"], u["stat"], u["side"], i), []).append(u)
        stats.setdefault((u["sport"], u["stat"]), []).append(u)
    cells = {f"{s}|{st}|{sd}|{i}": _cell(us) for (s, st, sd, i), us in groups.items()}
    lines = {f"{s}|{st}": _line(us, k_line) for (s, st), us in stats.items()}
    return {"version": VERSION, "k": k, "k_line": k_line, "cells": cells, "lines": lines, "sports": sorted({u["sport"] for u in units or ()})}


def ranked_chance(sport, stat, side, p, p_push, research, table):
    """(chance to hit used for ranking, band record or None). p is the published unconditional chance of the side.
    The band's settled hit rate, shrunk by k picks toward its stat's line at this chance; no record at all = the published chance."""
    push = p_push if isinstance(p_push, (int, float)) and 0 <= p_push < 1 else 0.0
    if research:
        return 0.5 + (p - 0.5) * RESEARCH_WEIGHT, None
    cond = p / (1 - push) if push else p
    i = band_index(cond)
    t = table or {}
    line = (t.get("lines") or {}).get(f"{sport}|{stat}")
    cell = (t.get("cells") or {}).get(f"{sport}|{stat}|{side}|{i}") if i is not None else None
    base = line["a"] + line["b"] * (cond - 0.5) if line else cond
    if not cell:
        return min(0.99, max(0.01, base)) * (1 - push), None
    q = (cell["n"] * cell["observed"] + t["k"] * base) / (cell["n"] + t["k"])
    q = min(0.99, max(0.01, q))
    rec = {"band": band_label(i), "stat": stat, "side": side, "n": cell["n"], "matches": cell["matches"],
           "printed": round(cell["printed"], 4), "observed": round(cell["observed"], 4)}
    return q * (1 - push), rec


def band_text(rec, sport, line_type="standard"):
    if not rec or not rec.get("n"):
        return "No settled record for this band yet"
    alt = " (standard lines)" if sport == "cs2" and line_type in ("goblin", "demon") else ""
    return (f"{SPORT_LABEL.get(sport, sport)} {STAT_WORD.get(rec['stat'], rec['stat'])} {rec['side'].title()} {rec['band']}{alt}: "
            f"won {rec['observed'] * 100:.1f}% of {rec['n']} settled")


# ---------------------------------------------------------------------------------------------------------------- pool

def _ts(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def prop_key(rec):
    return (rec["sport"], str(rec["event"]["id"]), rec["player"]["key"], rec["market"]["stat"], rec["market"]["period"])


def leg_key(book, row_id, side):
    return f"{book}|{row_id}|{side}"


def exclusion(rec, now, feeds=None, research_ok=False):
    """None when this record is a pool leg, else the reason it is not. feeds: {platform: {"state"}} of the record's sport."""
    sport, sc, offer = rec.get("sport"), rec.get("scout") or {}, rec.get("offer") or {}
    book, side = offer.get("platform"), sc.get("side")
    if book not in PLATFORMS:
        return "platform"
    if sport not in SPORTS:
        return "sport"
    if (rec.get("state") or {}).get("kind") != "ok":
        return "held"
    if sc.get("hold"):
        return "held"
    if sc.get("letter_held"):                       # letter containment lc-1: a held letter has no rating, never a leg
        return "contained"
    if feeds is not None and ((feeds or {}).get(book) or {}).get("state") != "ok":
        return "feed"
    ev = rec.get("event") or {}
    start = _ts(ev.get("lock_utc")) or _ts(ev.get("start_utc"))
    if ev.get("status") == "started" or start is None or (start - now).total_seconds() < LEAD_S:
        return "started"
    if side not in ("over", "under") or not (offer.get(side) or {}).get("offered"):
        return "side"
    p = sc.get("p_over") if side == "over" else sc.get("p_under")
    if not _num(p) or not 0 < p < 1:
        return "chance"
    proj, line = sc.get("projection"), offer.get("line")
    if p >= DEGENERATE_P or (_num(proj) and _num(line) and line > 0 and proj < NEAR_ZERO_PROJ * line):
        return "degenerate"
    if not (rec.get("player") or {}).get("team"):
        return "team"
    grade = sc.get("grade")
    if sport == "cs2":
        if (sc.get("validation") or {}).get("kind") != "live" or grade not in CS2_GRADES:
            return "grade"
        units, why = sc.get("units"), str(sc.get("stake_reason") or "")
        if not (_num(units) and units > 0) and not why.startswith("same-match"):
            return "stake"
    elif sport == "lol":
        if grade not in LOL_GRADES:
            return "grade"
    else:
        if grade not in RESEARCH_GRADES:
            return "grade"
        if not research_ok:
            return "research"
    return None


def make_leg(rec, table):
    sc, offer, m = rec["scout"], rec["offer"], rec["market"]
    side = sc["side"]
    p = sc["p_over"] if side == "over" else sc["p_under"]
    research = rec["sport"] in RESEARCH_SPORTS
    # ec-1: the band table is fitted on the model's own chance, so a calibrated row is ranked at its raw chance (p_raw); the
    # printed chance p is unchanged
    p_rank = sc["p_raw"] if isinstance(sc.get("p_raw"), (int, float)) and not isinstance(sc.get("p_raw"), bool) else p
    q, band = ranked_chance(rec["sport"], m["stat"], side, p_rank, sc.get("p_push"), research, table)
    start = rec["event"].get("lock_utc") or rec["event"].get("start_utc")
    return {"key": leg_key(offer["platform"], offer["offer_id"], side), "sport": rec["sport"], "book": offer["platform"], "row_id": str(offer["offer_id"]),
            "side": side, "line": offer.get("line"), "line_type": m.get("line_type") or "standard", "event": str(rec["event"]["id"]),
            "event_label": rec["event"].get("label") or "", "start": start, "player": rec["player"].get("name") or "", "pkey": rec["player"]["key"],
            "team": rec["player"].get("team") or "", "stat": m["stat"], "stat_label": m.get("stat_label") or m["stat"], "period": m["period"],
            "period_label": m.get("period_label") or m["period"], "p": p, "p_push": sc.get("p_push") or 0.0, "rank_p": q, "band": band,
            "research": research, "grade": sc.get("grade"), "multiplier": (offer.get(side) or {}).get("payout"), "model_version": sc.get("model_version"),
            "prop": prop_key(rec)}


def pool(records, now=None, feeds=None, research_ok=False, table=None, exclude=()):
    """(legs ranked best first, {reason: count}). feeds: {sport: {platform: {"state"}}} or None to skip the feed check."""
    now = now or datetime.now(timezone.utc)
    skip = set(exclude or ())
    legs, why = [], {}
    for rec in records or ():
        try:
            r = exclusion(rec, now, None if feeds is None else feeds.get(rec.get("sport")) or {}, research_ok)
        except (KeyError, TypeError, AttributeError):
            r = "invalid"
        if r is None:
            leg = make_leg(rec, table)
            if leg["key"] in skip:
                r = "excluded"
            else:
                legs.append(leg)
        if r is not None:
            why[r] = why.get(r, 0) + 1
    legs.sort(key=lambda l: (-l["rank_p"], -l["p"], l["key"]))
    seen = {}
    for l in legs:                                          # rank on its own platform: the slip a member builds is one platform
        seen[l["book"]] = seen.get(l["book"], 0) + 1
        l["rank"] = seen[l["book"]]
    return legs, why


# ---------------------------------------------------------------------------------------------------------------- rules

def _rule_leg(l):
    return {"sport": l["sport"], "book": l["book"], "team": l["team"] if l["sport"] == "cs2" else f"{l['sport']}:{l['team']}",
            "nick": l["player"], "side": l["side"], "event": f"{l['sport']}|{l['event']}"}


def same_match_allowed(platform):
    """Underdog publishes that opposing players of one match are allowed (the payout may shift); PrizePicks keeps its same-match
    limits in its app chart, so Scout does not offer them there."""
    return platform == "underdog"


def problems(legs, platform, entry_type=None, same_match=False):
    """Every hard rule a slip breaks, in member words ([] = the slip passes)."""
    out = []
    n = len(legs)
    if not MIN_PICKS <= n <= MAX_PICKS:
        out.append(f"A generated slip has {MIN_PICKS} to {MAX_PICKS} picks.")
    if any(l["book"] != platform for l in legs):
        out.append("An entry is placed on one platform.")
    players, matches, props = {}, {}, {}
    for l in legs:
        pk = (l["sport"], l["pkey"])
        if pk in players:
            prev = players[pk]
            if prev["prop"] == l["prop"] and prev["side"] != l["side"]:
                out.append(f"{l['player']}: Over and Under on the same prop conflict.")
            else:
                out.append(f"{l['player']} is on this slip twice. One leg per player.")
        players[pk] = l
        mk = (l["sport"], l["event"])
        if mk in matches and not (same_match and same_match_allowed(platform)):
            out.append(f"{l['player']} and {matches[mk]['player']} share a match. One leg per match.")
        matches.setdefault(mk, l)
    e = rules.entry_status(platform, [_rule_leg(l) for l in legs], entry_type)
    out.extend(p["text"] for p in e["problems"])
    return out


# ---------------------------------------------------------------------------------------------------------------- search

LINE_RULES = ("no_goblins", "one_goblin", "standard", "all")
LINE_RULE_DEFAULT = "no_goblins"      # the member routes' default (workspace_api._gen_opts / post_slip_advice); the library default stays "all"


def line_ok(l, lines):
    """The member's goblin rule for one leg Scout picks: standard = no goblins or demons, no_goblins = no goblins (demons allowed),
    one_goblin / all = any line type (one_goblin is capped per slip by max_goblins)."""
    lt = (l or {}).get("line_type") or "standard"
    if lines == "standard":
        return lt == "standard"
    if lines == "no_goblins":
        return lt != "goblin"
    return True


def max_goblins(lines):
    return 1 if lines == "one_goblin" else None


# Maps scope preference (owner 2026-11-12: "map 1-2 lines ... prioritized in slip builder over map 1 lines unless the map 1 line has
# more value"). When one player's stat in one match is on offer for more than one maps scope, the builder uses the LONGEST scope
# (Maps 1-3, then Maps 1-2, then Map 1); a shorter scope replaces it only when its value - the leg's rank_p, the per-leg number the
# search already ranks by - is strictly higher. Ties go to the longer scope. The other scope's legs of that player + stat are left
# out of the candidates, so one slip never holds both (one leg per player already held that) and a batch with repeats allowed never
# trades one scope for the other. With "No repeat props" the preferred leg, once used, frees its other scope for a later slip
# (a different prop, as before). Grades, chances, eligibility and the search itself are unchanged.
SCOPE_MAPS = {"maps13": 3, "maps12": 2, "map1": 1}


def scope_choice(legs, value=lambda l: l["rank_p"]):
    """(legs kept in their order, [{kept, dropped, margin}] one per player + stat whose scope decided). Only legs whose period is a
    known maps scope are grouped; everything else passes through."""
    groups = {}
    for l in legs:
        n = SCOPE_MAPS.get(l.get("period"))
        if n is not None:
            groups.setdefault((l["sport"], l["book"], l["event"], l["pkey"], l["stat"]), {}).setdefault(n, []).append(l)
    drop, notes = set(), []
    for g in groups.values():
        if len(g) < 2:
            continue
        best = {n: max(ls, key=value) for n, ls in g.items()}
        win = max(best, key=lambda n: (value(best[n]), n))          # strictly more value wins; equal value: the longer scope
        longest = max(g)
        for n, ls in g.items():
            if n != win:
                drop.update(id(x) for x in ls)
        notes.append({"kept": best[win], "longest": best[longest], "margin": value(best[win]) - value(best[longest])})
    return [l for l in legs if id(l) not in drop], notes


def _score(l, uses):
    return math.log(max(l["rank_p"], 1e-9)) - REUSE_PENALTY * uses(l)


def _best_slip(cands, size, platform, entry_type, same_match, mix, uses, taken, goblin_cap=None):
    """Exact branch-and-bound for one slip. -> (legs or None, nodes, exact)."""
    scored = sorted(((_score(l, uses), l) for l in cands), key=lambda t: (-t[0], t[1]["key"]))[:POOL_CAP]
    scores = [s for s, _ in scored]
    legs = [l for _, l in scored]
    n = len(legs)
    best = {"score": -math.inf, "legs": None}
    st = {"nodes": 0, "cut": False}
    allow_same = same_match and same_match_allowed(platform)
    chosen, players, matches, sports = [], set(), {}, {}
    gob = [0]

    def leaf(score):
        sig = frozenset(l["key"] for l in chosen)
        if sig in taken:
            return
        if mix == "mixed" and len({l["sport"] for l in chosen}) < 2:
            return
        if len({_rule_leg(l)["team"] for l in chosen}) < 2:
            return
        if problems(chosen, platform, entry_type, same_match):
            return
        best["score"], best["legs"] = score, list(chosen)

    def dfs(i, score):
        st["nodes"] += 1
        if st["nodes"] > NODE_LIMIT:
            st["cut"] = True
            return
        need = size - len(chosen)
        if need == 0:
            if score > best["score"]:
                leaf(score)
            return
        if n - i < need or score + sum(scores[i:i + need]) <= best["score"]:
            return
        for j in range(i, n):
            if n - j < need or score + sum(scores[j:j + need]) <= best["score"]:
                return
            l = legs[j]
            pk, mk = (l["sport"], l["pkey"]), (l["sport"], l["event"])
            if pk in players or (mk in matches and not allow_same):
                continue
            if platform == "underdog" and chosen and l["sport"] != chosen[0]["sport"]:
                continue                                    # Underdog publishes no mixed-sport entry
            if mix == "single" and chosen and l["sport"] != chosen[0]["sport"]:
                continue
            g = 1 if l.get("line_type") == "goblin" else 0
            if g and goblin_cap is not None and gob[0] >= goblin_cap:
                continue                                    # the member's goblin rule: at most goblin_cap goblins per slip
            chosen.append(l); players.add(pk); matches[mk] = matches.get(mk, 0) + 1; gob[0] += g
            dfs(j + 1, score + scores[j])
            chosen.pop(); players.discard(pk); matches[mk] -= 1; gob[0] -= g
            if not matches[mk]:
                del matches[mk]
            if st["cut"]:
                return

    dfs(0, 0.0)
    return best["legs"], st["nodes"], not st["cut"]


def all_hit(legs):
    """Product of the ranked chances (pushes already taken off), or None when two legs share a match."""
    if len({(l["sport"], l["event"]) for l in legs}) < len(legs):
        return None
    out = 1.0
    for l in legs:
        out *= l["rank_p"]
    return out


def payout(platform, entry_type, legs):
    """The published all-hit multiple, or None: Underdog's table when every pick is a 1.0x pick; PrizePicks' verified help-center
    table (owner sign-off 2026-11-05) when every pick is a standard line with no modifier (goblin / demon / promo stay unknown)."""
    if platform == "prizepicks":
        if any((l.get("line_type") or "standard") != "standard" or l.get("multiplier") not in (None, 1, 1.0) for l in legs):
            return None
        pp = rules.pp_payout(entry_type or "power", len(legs))
        return None if pp is None else {"multiple": pp["multiple"], "entry_type": entry_type or "power", "source": pp["source"],
                                        "page_date": pp["page_date"]}
    if platform != "underdog" or any(l.get("multiplier") != 1.0 for l in legs):
        return None
    m = rules.payout_multiple(entry_type or "standard", len(legs))
    return None if m is None else {"multiple": m, "entry_type": entry_type or "standard", "source": rules.UD_PAYOUTS, "page_date": rules.UD_PAYOUT_PAGE}


def why_line(legs, pool_size, first, reused):
    matches = len({(l["sport"], l["event"]) for l in legs})
    ranks = ", ".join(str(l["rank"]) for l in sorted(legs, key=lambda l: l["rank"]))
    head = "Best all-hit chance on the board" if first else "Best all-hit chance left" + ("" if reused else " with unused props")
    one = "one leg each" if matches == len(legs) else "same-match legs"
    research = sum(1 for l in legs if l["research"])
    tail = f"; {research} research estimate{'s' if research != 1 else ''}" if research else ""
    return f"{head}: {matches} matches, {one}; legs rank {ranks} of {pool_size} by settled hit rate{tail}."


def build(legs, opts):
    """One batch from a ranked pool. opts: {platform, entry_type, count, size, mix: any|mixed|single, same_match: bool,
    no_repeat: bool, lines: no_goblins|one_goblin|standard|all (missing = all, so the frozen research scripts reproduce;
    the member routes always pass the member's rule)}. -> {slips, shortage, search}."""
    platform, et = opts["platform"], opts.get("entry_type")
    size, count = int(opts["size"]), int(opts["count"])
    mix, same_match, no_repeat = opts.get("mix") or "any", bool(opts.get("same_match")), bool(opts.get("no_repeat"))
    lines = opts.get("lines") or "all"
    cap = max_goblins(lines)
    cands = [l for l in legs if l["book"] == platform and line_ok(l, lines)]
    used_prop, used_player, taken, slips, nodes, exact = {}, {}, set(), [], 0, True

    def uses(l):
        return 0 if no_repeat else used_prop.get(l["prop"], 0) + used_player.get((l["sport"], l["pkey"]), 0)

    for k in range(count):
        free = scope_choice([l for l in cands if not (no_repeat and l["prop"] in used_prop)])[0]
        got, nn, ex = _best_slip(free, size, platform, et, same_match, mix, uses, taken, cap)
        nodes += nn
        exact = exact and ex
        if not got:
            break
        reused = any(used_prop.get(l["prop"]) or used_player.get((l["sport"], l["pkey"])) for l in got)
        taken.add(frozenset(l["key"] for l in got))
        for l in got:
            used_prop[l["prop"]] = used_prop.get(l["prop"], 0) + 1
            used_player[(l["sport"], l["pkey"])] = used_player.get((l["sport"], l["pkey"]), 0) + 1
        got = sorted(got, key=lambda l: l["rank"])
        slips.append({"platform": platform, "entry_type": et, "legs": got, "all_hit": all_hit(got), "payout": payout(platform, et, got),
                      "same_match": len({(l["sport"], l["event"]) for l in got}) < len(got), "why": why_line(got, len(cands), k == 0, reused)})
    shortage = None
    if len(slips) < count:
        label = rules.LABEL.get(platform, platform)
        by_sport = {}
        for l in cands:
            by_sport.setdefault(l["sport"], set()).add(l["event"])
        one_sport = platform == "underdog" or mix == "single"
        matches = max((len(v) for v in by_sport.values()), default=0) if one_sport else sum(len(v) for v in by_sport.values())
        if len(cands) < size:
            dropped = any(l["book"] == platform and not line_ok(l, lines) for l in legs)    # name the rule only when it removed a leg
            rule = {"no_goblins": " without goblins", "standard": " without goblins or demons"}.get(lines, "") if dropped else ""
            shortage = f"Only {len(cands)} eligible legs on {label}{rule} right now."
        elif matches < size and not same_match:
            where = f"one sport on {label}" if one_sport else label
            mw = "match" if matches == 1 else "matches"           # T18 (UI sweep 2026-11-12): "1 matches"
            shortage = (f"The eligible legs for {where} come from {matches} {mw} at most; a {size}-pick slip needs {size} different matches."
                        if one_sport else f"The eligible legs on {label} come from {matches} {mw}; a {size}-pick slip needs {size} different matches.")
        elif no_repeat:
            shortage = "No props were repeated: the unused eligible legs could not form more valid slips."
        else:
            shortage = "Every other valid combination of the eligible legs is already in this batch."
    return {"slips": slips, "shortage": shortage, "search": {"nodes": nodes, "exact": exact, "node_limit": NODE_LIMIT, "pool_cap": POOL_CAP}}


# ---------------------------------------------------------------------------------------------------------------- adder

def _brief(l):
    return {"key": l["key"], "sport": l["sport"], "book": l["book"], "row_id": l["row_id"], "side": l["side"], "player": l["player"],
            "line": l["line"], "stat_label": l["stat_label"], "period_label": l["period_label"], "p": l["p"], "rank": l["rank"],
            "band": band_text(l["band"], l["sport"], l["line_type"]) if not l["research"] else "Research estimate"}


def advise(slip, legs_by_key, ranked, entry_type=None, lines="all"):
    """slip: [{sport, book, row_id, side, player, team, event, stat, period}] in the member's order (never reordered).
    legs_by_key: every pool leg by key (all platforms); ranked: pool legs best first. -> [{key, notes, in_pool, rank?, band?,
    replacement?}] one per leg, same order. lines: the member's goblin rule for what Scout suggests; a leg the member chose is never
    flagged for its line type. Each slip leg may carry line_type (the route reads it from the board), so a goblin the member added
    that is not in the pool still counts toward "at most 1 goblin"."""
    out = []
    who = lambda x: x.get("player_key") or x.get("player")
    for i, s in enumerate(slip):
        key = leg_key(s.get("book"), s.get("row_id"), s.get("side"))
        me = legs_by_key.get(key)
        notes = []
        for j, o in enumerate(slip):
            if i == j or o.get("book") != s.get("book"):
                continue
            same_player = o.get("sport") == s.get("sport") and bool(who(o)) and who(o) == who(s)
            if same_player and o.get("stat") == s.get("stat") and o.get("period") == s.get("period") and o.get("side") != s.get("side"):
                notes.append({"code": "conflict", "text": f"Conflicts with {o.get('side', '').title()} {o.get('line')} on the same prop."})
            elif same_player:
                notes.append({"code": "same_player", "text": f"{s.get('player')} is on this entry twice. One leg per player."})
            elif o.get("sport") == s.get("sport") and o.get("event") and o.get("event") == s.get("event"):
                allowed = same_match_allowed(s.get("book"))
                notes.append({"code": "same_match", "text": f"Same match as {o.get('player')}: results can move together."
                              + ("" if allowed else " PrizePicks same-match limits are in its app; Scout does not offer this.")})
        row = {"key": key, "notes": notes, "in_pool": me is not None}
        if me is not None:
            row.update(rank=me["rank"], band=_brief(me)["band"], rank_p=round(me["rank_p"], 4))
        others = [legs_by_key.get(leg_key(o.get("book"), o.get("row_id"), o.get("side"))) or _loose(o) for j, o in enumerate(slip) if j != i]
        others = [o for o in others if o and o.get("book") == s.get("book")]
        bar = (me["rank_p"] + REPLACE_GAIN) if me is not None else -1.0
        base = set(problems(others, s.get("book"), entry_type)) if others else set()     # what the other legs already break is theirs
        for c in ranked:                                    # best first: the first fitting leg above the bar is the answer
            if c["rank_p"] <= bar:
                break
            if c["book"] != s.get("book"):
                continue
            if not line_ok(c, lines):
                continue
            if (lines == "one_goblin" and c.get("line_type") == "goblin"
                    and any((o.get("line_type") or "standard") == "goblin" for o in others)):
                continue
            if c["key"] == key or any(c["key"] == o.get("key") for o in others):
                continue
            if any((c["sport"], c["pkey"]) == (o["sport"], o.get("pkey")) for o in others):
                continue
            if any((c["sport"], c["event"]) == (o["sport"], o.get("event")) for o in others):
                continue
            if s.get("book") == "underdog" and others and any(o["sport"] != c["sport"] for o in others):
                continue
            if others and set(problems([c] + others, s.get("book"), entry_type)) - base:
                continue
            row["replacement"] = _brief(c)
            break
        out.append(row)
    return out


def _loose(o):
    """A slip leg that is not in the pool, in the shape the rule checks read."""
    if not o.get("sport") or not o.get("book"):
        return None
    return {"key": leg_key(o.get("book"), o.get("row_id"), o.get("side")), "sport": o["sport"], "book": o["book"], "pkey": o.get("player_key") or o.get("player"),
            "player": o.get("player") or "", "team": o.get("team") or "", "event": str(o.get("event") or ""), "side": o.get("side"),
            "line_type": o.get("line_type") or "standard",
            "prop": (o["sport"], str(o.get("event") or ""), o.get("player_key") or o.get("player"), o.get("stat"), o.get("period"))}
