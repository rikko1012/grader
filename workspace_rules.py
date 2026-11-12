"""Research workspace: which entries a platform takes across CS2, LoL, NBA and NFL, and when EV can be shown. Pure functions, no I/O.

entry_status decides one entry at a time, per combination. platform_rules.entry_check is called unchanged for its published
refusals; its always-on PrizePicks scoring-chart note is NOT read as "not valid" (it is attached to every entry, so reading
it that way would refuse all of them). A combination no platform page covers is not_published: the entry is not offered as
valid and the member is told that fact. Sources and page dates are in TABLE and are served at /preview/workspace/api/rules.

The Underdog table is a copy of sports/payouts.py (CS2 never imports sports; tests/test_workspace_rules.py reads that file
and checks the two match). EV is workspace-only: never on a receipt, never on Results."""
import json
import re
from functools import lru_cache
from pathlib import Path

import results_view

import platform_rules
import payout_reference

RULES_VERSION = "ws-rules-2026-11-03-reference"
READ_AT = "2026-11-02"

PP_PICKS = "https://www.prizepicks.com/help-center/player-picks"
UD_PAYOUTS = "https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts"
UD_LINEUP = "https://app.underdogsports.com/rules/pick-em#lineup-restrictions"
UD_CORRELATED = "https://help.underdogsports.com/en/articles/11010091-correlated-picks-how-stacking-players-affects-your-payout-multiplier"
UD_TIES = "https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection"
PP_TIES = "https://www.prizepicks.com/help-center/dnps-reboots-and-ties"
PP_PAGE = "2026-10-19"
UD_PAYOUT_PAGE = "2026-09-25"
UD_CORRELATED_PAGE = "2026-09-09"
UD_TIES_PAGE = "2026-08-26"

UD_STANDARD = {2: 3.5, 3: 6.5, 4: 12.0, 5: 20.0, 6: 35.0, 7: 65.0, 8: 120.0}
UD_FLEX = {3: {0: 3.25, 1: 1.09}, 4: {0: 6.0, 1: 1.4}, 5: {0: 10.0, 1: 2.5}, 6: {0: 25.0, 1: 2.6, 2: 0.25},
           7: {0: 40.0, 1: 2.75, 2: 0.5}, 8: {0: 80.0, 1: 3.0, 2: 1.0}}
UD_FLEX_LISTED = (3, 5, 6, 7, 8)        # 4-pick flex: the help article says 6x / 1.4x, the rules table 7.2x / 1.8x

EV_MIN_CHANCE = 0.57                    # Results cover 57% and up (results_view.RULE)
EV_MIN_MATCHES = 300
EV_MAX_GAP = 0.03
EV_MAX_AGE_H = 24
EV_NOT_VALIDATED = {"lol": "LoL chances have no settled record at real lines yet.", "nba": "Scout rates EV only on chances that passed its settled-results check; NBA chances have not.",
                    "nfl": "Scout rates EV only on chances that passed its settled-results check; NFL chances have not."}

SPORT = {"cs2": "CS2", "lol": "LoL", "nba": "NBA", "nfl": "NFL"}
LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}
STATUS_VALID, STATUS_REFUSED, STATUS_NOT_PUBLISHED = "valid", "refused", "not_published"
CHECK_COMBINATION = 3                   # index in the five member checks


def _rule(status, text, source=None, page_date=None, note=None):
    return {"status": status, "text": text, "source": source, "page_date": page_date, "read_at": READ_AT, "note": note}


_PUBLISHED, _NOT, _SCOUT = "published", "not_published", "scout"
# one row per combination. "published": the platform states the answer on the page named, and every such row names that page.
# "not_published": no page answers it. "scout": a limit of Scout's own, which no platform page states, so it carries no source.
TABLE = {
    "entry_size": {"label": "Entry size",
                   "prizepicks": _rule(_PUBLISHED, "2 to 6 picks", PP_PICKS, PP_PAGE,
                                       "Colorado's minimum of 3 is not checked: Scout does not hold a member's state."),
                   "underdog": _rule(_PUBLISHED, "Standard 2 to 8 picks, flex 3 to 8 picks", UD_PAYOUTS, UD_PAYOUT_PAGE)},
    "two_teams": {"label": "Two or more teams",
                  "prizepicks": _rule(_PUBLISHED, "Required", PP_PICKS, PP_PAGE),
                  "underdog": _rule(_PUBLISHED, "Required", UD_LINEUP, None, "This rules page shows no date.")},
    "same_player": {"label": "Same player twice",
                    "prizepicks": _rule(_PUBLISHED, "A player counts once per lineup, unless Stacks are available for that player", PP_PICKS, PP_PAGE,
                                        "The boards Scout reads carry no Stacks, so Scout refuses a repeat."),
                    "underdog": _rule(_PUBLISHED, "Same side refused", UD_LINEUP)},
    "both_sides": {"label": "Both sides of one prop",
                   "prizepicks": _rule(_PUBLISHED, "Refused by the same-player rule", PP_PICKS, PP_PAGE),
                   "underdog": _rule(_NOT, "Underdog publishes only the same-side example. Scout does not offer this")},
    "mixed_sports": {"label": "Mixed sports",
                     "prizepicks": _rule(_PUBLISHED, "Allowed for the most part: selections can include different sports, and some sports "
                                                     "add their own limits", PP_PICKS, PP_PAGE, "Each sport's own chart is in the PrizePicks app."),
                     "underdog": _rule(_NOT, "Underdog does not publish this. Its rules page, help search and Pick'em page "
                                             "were searched. Scout does not offer this")},
    "opposing_players": {"label": "Opposing players of one match",
                         "prizepicks": _rule(_NOT, "Each sport's chart is in the PrizePicks app, and its MLB example shows such limits "
                                                   "exist. Scout does not offer this"),
                         "underdog": _rule(_PUBLISHED, "Allowed. The payout may shift", UD_CORRELATED, UD_CORRELATED_PAGE)},
    "teammates_plus_other": {"label": "Teammates plus another team",
                             "prizepicks": _rule(_PUBLISHED, "Allowed by the two-team rule", PP_PICKS, PP_PAGE),
                             "underdog": _rule(_PUBLISHED, "Allowed", UD_LINEUP)},
    "goblins_demons": {"label": "Goblins and demons",
                       "prizepicks": _rule(_PUBLISHED, "Allowed in any number", PP_PICKS, PP_PAGE,
                                           "Scout's slip builder leaves goblins out unless you allow them in Slip Studio."),
                       "underdog": _rule(_SCOUT, "Scout grades no Underdog alternate lines, so they never reach this board")},
    "league_limits": {"label": "League limits",
                      "prizepicks": _rule(_SCOUT, "CS2, LoL, NBA and NFL charts are in the PrizePicks app. Scout applies the published limits above"),
                      "underdog": _rule(_PUBLISHED, "Lineup rules apply to every sport", UD_LINEUP, None, "This rules page shows no date.")},
    "ties": {"label": "A pick that ties the line",
             "prizepicks": _rule(_PUBLISHED, "Not correct and not incorrect: the payout drops as for a DNP", PP_TIES, PP_PAGE),
             "underdog": _rule(_PUBLISHED, "Voided: the entry pays for the remaining picks", UD_TIES, UD_TIES_PAGE)},
    "two_platforms": {"label": "Two platforms in one entry",
                      "prizepicks": _rule(_SCOUT, "Scout splits picks into one entry per platform"),
                      "underdog": _rule(_SCOUT, "Scout splits picks into one entry per platform")},
}


def rules_payload():
    return {"version": RULES_VERSION, "read_at": READ_AT, "payout_source": UD_PAYOUTS, "payout_page_date": UD_PAYOUT_PAGE,
            "rules": [dict(v, key=k) for k, v in TABLE.items()]}


def _names(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _sports(legs):
    order = list(SPORT)
    return sorted({str(l.get("sport") or "cs2") for l in legs}, key=lambda s: (order.index(s) if s in order else 99, s))


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _member_text(s):
    return re.sub(r"\b(?:lol|nba|nfl):", "", str(s).replace(" - ", ". "))     # a team key carries its sport; the member reads the team


def _used(platform, *keys):
    out = []
    for k in keys:
        r = TABLE[k][platform]
        out.append({"rule": k, "label": TABLE[k]["label"], "status": r["status"], "text": r["text"], "source": r["source"],
                    "page_date": r["page_date"], "read_at": r["read_at"], "note": r["note"]})
    return out


def _problem(code, text):
    return {"check": CHECK_COMBINATION, "code": code, "text": text}


def entry_status(platform, legs, entry_type=None):
    """One entry on one platform. legs: [{sport, book, team, nick | player, side, event}]. A team carries its sport, so NBA and NFL
    Dallas are two teams. -> {platform, sports, entry_type, status, ok, trackable, problems, rules}. status: valid | refused |
    not_published; ok means the rule checks pass, and the caller narrows trackable to entries whose legs passed theirs."""
    legs = list(legs or [])
    sports = _sports(legs)
    p = platform_rules.platform_of(platform)
    if p is None:
        name = str(platform or "This platform")
        return {"platform": platform, "sports": sports, "entry_type": entry_type, "status": STATUS_NOT_PUBLISHED, "ok": False,
                "trackable": False, "rules": [], "rules_version": RULES_VERSION,
                "problems": [_problem(STATUS_NOT_PUBLISHED, f"{name} entry rules are not on file. Scout does not offer this entry.")]}
    label = LABEL[p]
    chk = platform_rules.entry_check([dict(l, book=p) for l in legs], p, entry_type)
    et = chk["entry_type"]
    refused = [_problem("entry", _member_text(r)) for r in chk["refusals"]]
    unpublished = []
    used = ["entry_size", "two_teams", "same_player"]
    books = {platform_rules.platform_of(l.get("book")) or str(l.get("book")) for l in legs if l.get("book")}
    if any(s != "cs2" for s in sports):
        used.append("league_limits")
    if books - {p}:
        used.append("two_platforms")
        refused.append(_problem("entry", "An entry is placed on one platform. Picks from another platform are on this entry."))
    if platform_rules.RULES[p]["entry_size"].get(et) is None:
        unpublished.append(_problem(STATUS_NOT_PUBLISHED, f"{label} publishes no size for a {et} entry. Scout does not offer this entry."))
    if any(not _norm(l.get("team")) for l in legs):
        unpublished.append(_problem(STATUS_NOT_PUBLISHED, "The feed gives no team for a pick here, so the two-team rule cannot be applied. "
                                                          "Scout does not offer this entry."))
    if len(sports) > 1:
        used.append("mixed_sports")
        if p == "underdog":
            named = _names(SPORT.get(s, s) for s in sports)
            unpublished.append(_problem(STATUS_NOT_PUBLISHED, f"Underdog does not publish whether one entry can hold {named} picks. "
                                                              "Scout does not offer this entry."))
    if p == "underdog":
        seen = {}
        for l in legs:
            k = _norm(l.get("nick") or l.get("player"))
            if k:
                seen.setdefault(k, []).append(l)
        for ls in seen.values():
            sides = [str(x.get("side") or "") for x in ls]
            if len(ls) > 1 and len(set(sides)) == len(sides):
                used.append("both_sides")
                who = ls[0].get("nick") or ls[0].get("player")
                unpublished.append(_problem(STATUS_NOT_PUBLISHED, f"{who} is on both sides. Underdog does not publish whether one entry "
                                                                  "can hold that. Scout does not offer this entry."))
    else:
        matches = {}
        for l in legs:
            if l.get("event"):
                matches.setdefault(str(l["event"]), set()).add(_norm(l.get("team")))
        if any(len(t) > 1 for t in matches.values()):
            used.append("opposing_players")
            unpublished.append(_problem(STATUS_NOT_PUBLISHED, "PrizePicks keeps its same-match limits in its app chart. Scout does not "
                                                              "offer opposing players of one match on one PrizePicks entry."))
    problems = refused + unpublished
    status = STATUS_REFUSED if refused else STATUS_NOT_PUBLISHED if unpublished else STATUS_VALID
    ok = status == STATUS_VALID and bool(legs)
    return {"platform": p, "sports": sports, "entry_type": et, "status": status, "ok": ok, "trackable": ok,
            "problems": problems, "rules": _used(p, *dict.fromkeys(used)), "rules_version": RULES_VERSION}


# ---------------------------------------------------------------------------------------------------------------- EV

def flex_return(probs, table=None):
    """Expected payout multiple of a flex entry with its own chance per pick (ud_flex_return in sports/payouts.py with
    every chance equal gives the same number)."""
    tab = table if table is not None else UD_FLEX[len(probs)]
    losses = [1.0]
    for p in probs:
        nxt = [0.0] * (len(losses) + 1)
        for k, w in enumerate(losses):
            nxt[k] += w * p
            nxt[k + 1] += w * (1 - p)
        losses = nxt
    return sum(losses[k] * m for k, m in tab.items() if k < len(losses))


def payout_multiple(entry_type, n):
    """The published all-hit multiple for a plain entry, or None."""
    if entry_type == "flex":
        return UD_FLEX[n][0] if n in UD_FLEX_LISTED else None
    return UD_STANDARD.get(n) if entry_type == "standard" else None


def pp_payout(entry_type, n):
    """PrizePicks' verified standard-line payout (gradecore/pickem_payouts.json, owner sign-off 2026-11-05) for an n-pick power / flex
    entry: {"multiple" (all-hit), "flex" (losses -> multiple, flex only), "source", "page_date"}; None when the table is not
    verified or lists no such entry."""
    b = _pp_file()
    if not b.get("verified") is True or n in ((b.get("not_in_app") or {}).get(entry_type) or ()):   # Flex 2: not in the app
        return None
    row = ((b.get("entries") or {}).get(entry_type) or {}).get(str(n))
    if row is None:
        return None
    src = {"source": b.get("source") or payout_reference.PP_SOURCE, "page_date": b.get("page_date") or payout_reference.PP_PAGE_DATE}
    if isinstance(row, dict):
        flex = {int(k): float(v) for k, v in row.items()}
        return dict(src, multiple=flex.get(0, 0.0), flex=flex)
    return dict(src, multiple=float(row))


PP_FILE = Path(__file__).with_name("gradecore") / "pickem_payouts.json"


@lru_cache(maxsize=1)
def _pp_file():
    """The PrizePicks entry of gradecore/pickem_payouts.json, read as data (this module imports no gradecore code). {} when
    the file cannot be read: no payout, never a guess."""
    try:
        return json.loads(PP_FILE.read_text(encoding="utf-8")).get("prizepicks") or {}
    except (OSError, ValueError):
        return {}


def band_of(cell, p):
    for b in (cell or {}).get("bands") or []:
        if b["lo"] <= p < b["hi"]:
            return b
    return None


def _pts(x):
    return f"{x * 100:.1f}%"


def ev_status(platform, entry_type, legs, gate, now_ts):
    """EV for a pending slip, or the first reason it is absent, in the published order. legs: [{name, sport, match, line,
    multiplier, on_side, p, tag, prop, scope, held?}]; held = the containment banner of a leg whose letter is held (no rating). gate: a callable returning gate_from_export's dict (or None), called only when
    the entry reaches that step. A chance outside CS2 has no validated record, so a verified payout is shown and EV is not.
    -> {"available": False, "reason", "payout"?} or {"available": True, "ev", "hit_chance", "payout"}."""
    def no(reason, **extra):
        return dict({"available": False, "reason": reason}, **extra)

    n = len(legs)
    flex_table = None
    if platform == 'prizepicks':
        # owner sign-off 2026-11-05: the PrizePicks help-center table is verified (gradecore/pickem_payouts.json); standard lines only
        if any(str(l.get('line_type') or l.get('variant') or 'standard').lower() not in ('standard', 'normal') for l in legs):
            return no('Special-line payouts are set on the PrizePicks lineup screen.')
        et = entry_type or "power"
        pp = pp_payout(et, n)
        if pp is None:
            return no(f"PrizePicks lists no {et} payout for {n} picks.")
        if any(l.get("multiplier") not in (None, 1, 1.0) for l in legs):
            return no("PrizePicks does not publish the payout of a modified pick.")
        base, flex_table = pp["multiple"], pp.get("flex")
        payout = {"multiple": base, "entry_type": et, "platform": "prizepicks", "source": pp["source"], "page_date": pp["page_date"]}
    elif platform != 'underdog':
        return no('No verified payout reference for this platform.')
    else:
        et = entry_type or "standard"
        if et == "flex" and n == 4:
            return no("Underdog's pages list two 4-pick flex payouts.")
        base = payout_multiple(et, n)
        if base is None:
            return no(f"Underdog lists no {et} payout for {n} picks.")
        if any(l.get("multiplier") is None and l.get("sport") != "cs2" for l in legs):
            return no("Underdog sent no payout for a pick on this entry.")
        if any(l.get("multiplier") != 1.0 for l in legs):
            return no("Underdog does not publish how multipliers combine.")
        payout = {"multiple": base, "entry_type": et, "source": UD_PAYOUTS, "page_date": UD_PAYOUT_PAGE}
    matches = [l.get("match") for l in legs]
    if any(m is None for m in matches) or len(set(matches)) < n:
        return no("Picks share a match: payouts shift, and no tested joint model exists.")
    if any(float(l.get("line") or 0).is_integer() for l in legs):
        return no("A whole-number line can push, and a push changes the payout tier.", payout=payout)
    held = next((l["held"] for l in legs if l.get("held")), None)
    if held:                                        # letter containment (lc-1): a held leg has no rating
        return no(held, payout=payout)
    other = next((l["sport"] for l in legs if l.get("sport") in EV_NOT_VALIDATED), None)
    if other:
        return no(EV_NOT_VALIDATED[other], payout=payout)
    if any(not l.get("on_side") or l.get("p") is None for l in legs):
        return no("No saved chance for this side.", payout=payout)
    if any(l["p"] < EV_MIN_CHANCE for l in legs):
        return no("Results cover 57% and up.", payout=payout)
    g = gate() if callable(gate) else gate
    if not g or g.get("at_ts") is None:             # a failed or empty read says nothing about how many matches have settled
        return no("The results export is not available.", payout=payout)
    cells = g.get("cells") or {}
    for l in legs:
        cell = cells.get(f"{l.get('tag')}|{l.get('prop')}|{l.get('scope')}") or {}
        have = cell.get("matches") or 0
        if have < EV_MIN_MATCHES:
            return no(f"This model version has {have} of {EV_MIN_MATCHES} settled matches.", payout=payout)
    for l in legs:
        cell = cells[f"{l.get('tag')}|{l.get('prop')}|{l.get('scope')}"]
        b = band_of(cell, l["p"])
        if b is None or not b.get("ci"):
            return no(f"No settled interval for {_pts(l['p'])} on this model version.", payout=payout)
        gap = abs(b["printed"] - b["observed"])
        if not b["ci"][0] <= b["printed"] <= b["ci"][1] or gap > EV_MAX_GAP:
            return no(f"Printed {_pts(b['printed'])}, won {_pts(b['observed'])}, a gap of {gap * 100:.1f} points.", payout=payout)
    age_h = (now_ts - g["at_ts"]) / 3600
    if age_h >= EV_MAX_AGE_H or age_h < -1:         # a second or two ahead is this request's own clock
        return no(f"The results export is {age_h:.0f} hours old.", payout=payout)
    probs = [l["p"] for l in legs]
    hit = 1.0
    for p in probs:
        hit *= p
    if flex_table is not None:
        ev = flex_return(probs, flex_table) - 1.0
    else:
        ev = (hit * base if et in ("standard", "power") else flex_return(probs)) - 1.0
    return {"available": True, "ev": round(ev, 4), "hit_chance": round(hit, 4), "payout": payout}


def edge_cells(gate, now_ts):
    """SCOUT_BOARD_H2H Edge column: per tag|prop|scope, the chance bands [lo, hi) a single CS2 leg passes in ev_status's
    settled-results gate (match count, interval, printed-vs-won gap, export age). Empty when the export is missing or old,
    so the board shows an edge only where the slip check would show EV."""
    g = gate() if callable(gate) else gate
    if not g or g.get("at_ts") is None:
        return {}
    age_h = (now_ts - g["at_ts"]) / 3600
    if age_h >= EV_MAX_AGE_H or age_h < -1:
        return {}
    out = {}
    for k, cell in (g.get("cells") or {}).items():
        if (cell.get("matches") or 0) < EV_MIN_MATCHES:
            continue
        ok = [[b["lo"], b["hi"]] for b in cell.get("bands") or []
              if b.get("ci") and b["ci"][0] <= b["printed"] <= b["ci"][1] and abs(b["printed"] - b["observed"]) <= EV_MAX_GAP]
        if ok:
            out[k] = ok
    return out


def leg_edge_ok(leg, cells):
    """The board-side mirror of ev_status's per-leg steps: CS2 only, a half-point line (no push), a saved chance on the
    shown side at EV_MIN_CHANCE or more, a 1.0x pick, and a chance band that passed edge_cells."""
    if leg.get("sport") != "cs2" or leg.get("sport") in EV_NOT_VALIDATED or leg.get("held"):
        return False
    try:
        if float(leg.get("line")).is_integer():
            return False
    except (TypeError, ValueError):
        return False
    p = leg.get("p")
    if not leg.get("on_side") or p is None or p < EV_MIN_CHANCE or leg.get("multiplier") != 1.0:
        return False
    return any(lo <= p < hi for lo, hi in cells.get(f"{leg.get('tag')}|{leg.get('prop')}|{leg.get('scope')}") or ())


def gate_from_export(export, tags, at_ts=None, B=500, min_matches=EV_MIN_MATCHES):
    """What ev_status needs from the stored P1 export, for the given model tags only: settled matches per exact tag x stat x
    maps on the Results pick rule, and each chance band's printed chance, hit rate and match-grouped interval. The interval is
    resampled only for a cell that can pass the match count: this runs in a request thread, and most cells stop at that step."""
    tags = set(tags or ())
    groups = {}
    for r in (export or {}).get("rows") or []:
        tag, p = r.get("version"), r.get("p")
        if tag not in tags or p is None or r.get("over") not in (0, 1):
            continue
        side_p = max(p, 1 - p)
        if side_p < results_view.RULE:
            continue
        k = f"{tag}|{r.get('prop')}|{r.get('scope') or '12'}"
        groups.setdefault(k, []).append({"match": r.get("match"), "printed": side_p,
                                         "win": int(r["over"] if p >= 0.5 else 1 - r["over"])})
    cells = {}
    for k, picks in groups.items():
        bands, matches = [], len({x["match"] for x in picks})
        for lo, hi, label in results_view.BANDS:
            sub = [x for x in picks if lo <= x["printed"] < hi]
            if sub:
                bands.append({"lo": lo, "hi": hi, "label": label, "picks": len(sub),
                              "printed": round(sum(x["printed"] for x in sub) / len(sub), 4),
                              "observed": round(sum(x["win"] for x in sub) / len(sub), 4),
                              "ci": results_view._ci(sub, B, 7) if matches >= min_matches else None})
        cells[k] = {"matches": matches, "bands": bands}
    return {"at": (export or {}).get("at"), "at_ts": at_ts, "cells": cells}


# ------------------------------------------------------------------------------------------- slip header (SCOUT_SLIP_HEADER)
# The tray / Studio header from gradecore/slip.py, built only on an entry ev_status already answered available=True: a verified
# Underdog payout AND chances that passed the settled-results gate. Anything else returns None and the header fields stay
# absent. workspace_api reads the flag and injects gradecore.slip.evaluate, so this module still imports no gradecore code.

SLIP_HEADER_FLAG = "SCOUT_SLIP_HEADER"


def slip_header_on(env):
    return str((env or {}).get(SLIP_HEADER_FLAG) or "").strip().lower() in ("1", "true", "yes", "on")


def leg_ev(p_win, decimal, p_push=0.0):
    """EV = p_cal x decimal_odds - 1, push-adjusted: a push returns the stake, never a win."""
    return p_win * decimal + p_push - 1.0


def header_payout(entry_type, n, platform="underdog"):
    """payout(outcomes) -> the published gross multiple, or None where no page states it. Any push: Underdog re-prices the
    entry on the remaining picks, which ev_status never lets through, so the header refuses rather than guess. PrizePicks:
    its verified power / flex table (pp_payout)."""
    if platform == "prizepicks":
        pp = pp_payout(entry_type, n)
        if pp is None:
            return None
        if pp.get("flex") is not None:
            ptab = pp["flex"]
            return lambda results: None if "push" in results else ptab.get(sum(x == "loss" for x in results), 0.0)
        pbase = pp["multiple"]
        return lambda results: None if "push" in results else (pbase if all(x == "win" for x in results) else 0.0)
    if entry_type == "standard":
        base = UD_STANDARD.get(n)
        if base is None:
            return None
        return lambda results: None if "push" in results else (base if all(x == "win" for x in results) else 0.0)
    if entry_type == "flex" and n in UD_FLEX_LISTED:
        tab = UD_FLEX[n]
        return lambda results: None if "push" in results else tab.get(sum(x == "loss" for x in results), 0.0)
    return None


def slip_header(ev, entry_type, legs, evaluate):
    """-> {payout_multiple, p_all_win, expected_return, basis, legs?} or None. legs: ev_status's legs, in entry
    order. Per-leg EV is given for a standard entry only: its per-pick price is the all-hit multiple's n-th root.
    No bankroll fraction (quarter-Kelly) is passed on: one stake per line, the grade's units (owner rule, review 2026-11-04)."""
    if not isinstance(ev, dict) or not ev.get("available") or not ev.get("payout"):
        return None
    plat = ev["payout"].get("platform") or "underdog"
    et, legs = entry_type or ("power" if plat == "prizepicks" else "standard"), list(legs or ())
    n = len(legs)
    payout = header_payout(et, n, plat)
    listed = (pp_payout(et, n) or {}).get("multiple") if plat == "prizepicks" else payout_multiple(et, n)
    if payout is None or ev["payout"].get("multiple") != listed:
        return None
    slip_legs = []
    for l in legs:
        p = l.get("p")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 < p < 1 or l.get("match") is None:
            return None
        slip_legs.append({"sport": l.get("sport") or "cs2", "book": plat, "player_id": str(l.get("name") or ""),
                          "match_key": str(l["match"]), "offer_revision": str(l.get("line")),
                          "p_win": float(p), "p_loss": 1.0 - float(p), "p_push": 0.0})
    out = evaluate(slip_legs, payout)
    if not out.get("available") or out.get("net_ev") is None or out.get("quarter_kelly_fraction") is None:
        return None
    if abs(out["net_ev"] - ev["ev"]) > 5e-4:        # two routes to one number: a mismatch shows nothing, never a third figure
        return None
    head = {"payout_multiple": ev["payout"]["multiple"], "entry_type": et, "p_all_win": round(out["p_all_win"], 4),
            "pushes": "excluded", "expected_return": round(out["net_ev"], 4), "basis": out.get("basis")}
    if et in ("standard", "power"):
        price = ev["payout"]["multiple"] ** (1.0 / n)
        head["leg_price"] = round(price, 4)
        head["legs"] = [{"index": i, "line": l.get("line"), "ev": round(leg_ev(l["p"], price), 4)} for i, l in enumerate(legs)]
    return head
