"""Which markets Sports may grade, and the handlers that refuse the rest.

Status per (league, market):
  supported      single-stat full-game player props with typed data end to end (offer -> ESPN box score -> settlement):
                 NBA points / rebounds / assists / threes; NFL pass / rush / receiving yards, carries, receptions
  research_only  typed and recordable, but NOT gradable: combos (need a joint model, never a sum of independent
                 chances), other single stats not in the supported list
  unsupported    a handler exists (so offers are typed and counted) but it refuses to grade: touchdown scorer,
                 double-double / triple-double, game spread, game total, fantasy score, first-basket, ...

gate(offer) is the single check every model / slip / UI path calls before a forecast can be graded. It refuses:
unknown stat, unsupported market, non full-game scope, promotional lines, one-sided lines (owner 2026-11-06), unresolved
event / player, offers that list no side, and removed offers. Side availability is per offer (OfferSnapshot.sides), never a platform-wide assumption.

compatible(a, b) says whether two offers are the SAME market (same canonical event, player, stat, scope, and both
ordinary lines) - the only case where prices from two books may be aligned or compared.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

SUPPORTED = {
    "nba": ("points", "rebounds", "assists", "threes"),
    "nfl": ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec"),
}
COMBOS = {"pra", "pr", "pa", "ra", "stocks", "rush_rec_yds", "pass_rush_yds"}
# one_sided (audit 2026-11-05 S1/S2): an Underdog line that LISTS only one side (adapters.ud_offers). It stays an
# ordinary line for PLACEMENT (it sits on the board row beside the other quotes, labelled by its side: "Over only" /
# "Under only", never Standard). cap-2, owner decision 2026-11-06: a one-sided line is NOT graded - gate() refuses it,
# so it gets no forecast, grade, letter, stake, slip leg, tracked pick, record entry or EV. Quarter / half lines are
# refused by their scope as before. The type is the STORED variant (the sides Underdog lists), never today's offered
# sides, so a passing suspension of one side never changes a line's grading.
ORDINARY_VARIANTS = {"standard", "balanced", "main", "ladder_rung", "one_sided"}
UNGRADED_VARIANTS = {"one_sided"}
ONE_SIDED_REASON = "one-sided line (Over only or Under only): not graded"
CAPABILITY_VERSION = "cap-2"
# NBA_STATS8 (round 2 2026-11-21, track nba-stats): the nba8-1 joint model (sports/model/nba8.py) covers PRA, P+R, P+A,
# R+A, steals, blocks, Blks+Stls and turnovers. Only the stats that PASSED the pre-registered real-line walk-forward
# (cs2_scout_ops/logs/audit_1121/round2/nba-stats/PREREG.md + walkforward_results.json) are listed here.
# DEFAULT OFF (round-2 review 2026-11-21): the TEST pass came almost entirely from Underdog balanced steals, which
# Underdog stopped offering on Nov 19; on PrizePicks standard steals went 148-134 (52.5%, CI 47.2-57.9) - below the
# pass rule. Only NBA_STATS8=1/true/yes/on letters the passed stats (0U, rg-1 letter, tested population only - see
# nba8_tested); unset / anything else = cap-2 unchanged, member board / slips / record identical to before.
# The forward SHADOW (NBA_STATS8_SHADOW, default on, rollback NBA_STATS8_SHADOW=0) records nba8-1 for all 8 stats in
# model/shadow.db only (never the canonical store, never a member page) so PrizePicks-standard evidence accumulates
# for a forward re-test (100+ rows, 20+ games, CI low > 50%) before anyone sets NBA_STATS8=1.
# The failed stats stay research_only with their existing reason. Frozen list: never edited without a new test.
NBA_STATS8_PASSED = frozenset({"steals"})       # walk-forward 2026-11-21: steals PASS; the other 7 failed
NBA_STATS8_VERSION = "nba8-1"
# The validation state nba8-1 carries for a passed stat - the same state as sm-1's NBA stats (gate passed on history /
# real lines, stakes stay 0U). Kept here, not in model/artifacts/registry.json: that file is part of the frozen
# artifact package (sports/model/fwd.py verifies its sha256).
NBA_STATS8_STATE = "history-validated"


_ON = ("1", "true", "yes", "on")
# the walk-forward population (PREREG.md "Graded population"): PrizePicks standard + Underdog balanced, full game,
# not promotional. FanDuel / Polymarket / any other book and every other variant were never tested.
NBA_STATS8_TESTED = frozenset({("prizepicks", "standard"), ("underdog", "balanced")})
# every nba8-1 market (sports/model/nba8.MARKETS) - the shadow records all 8, passed or not
NBA_STATS8_ALL = ("pra", "pr", "pa", "ra", "steals", "blocks", "stocks", "turnovers")


def nba_stats8_on():
    """NBA_STATS8 - default OFF; only 1/true/yes/on turns the nba8-1 letters on."""
    return os.environ.get("NBA_STATS8", "").strip().lower() in _ON


def nba_stats8_shadow_on():
    """NBA_STATS8_SHADOW - default ON (shadow.db only, never shown); 0/false/off/no stops the shadow recording."""
    return os.environ.get("NBA_STATS8_SHADOW", "1").strip().lower() not in ("0", "false", "off", "no")


def nba8_tested(offer):
    """True when this offer is in the population the nba8-1 walk-forward scored: PrizePicks standard or Underdog
    balanced, full game, not promotional, an event, and BOTH sides offered right now (a balanced Underdog line with one
    side suspended is not a two-sided line at this moment). Flag independent (the shadow uses it too)."""
    get = offer.get if isinstance(offer, dict) else (lambda k, d=None: getattr(offer, k, d))
    if (get("platform"), variant_of(offer)) not in NBA_STATS8_TESTED:
        return False
    if get("scope", "full_game") != "full_game" or get("promotional") or get("event_id") is None:
        return False
    on = {(x.get("side") if isinstance(x, dict) else getattr(x, "side", None)) for x in (get("sides") or ())
          if (x.get("offered") if isinstance(x, dict) else getattr(x, "offered", False))}
    return {"over", "under"} <= on


def nba8_models(base):
    """A model-name tuple plus nba8-1 only while NBA_STATS8 is on (off -> `base` exactly as before)."""
    return tuple(base) + (NBA_STATS8_VERSION,) if nba_stats8_on() else tuple(base)


def nba_stats8_markets():
    """The nba8-1 stats gradable right now (empty with NBA_STATS8=0)."""
    return NBA_STATS8_PASSED if nba_stats8_on() else frozenset()


def gradable_stats(league):
    """SUPPORTED plus the live NBA_STATS8 stats (same order; flag off / nothing passed -> SUPPORTED[league])."""
    base = tuple(SUPPORTED.get(league, ()))
    if league != "nba":
        return base
    return base + tuple(sorted(nba_stats8_markets() - set(base)))
ONE_SIDED_PLATFORMS = {"underdog"}


def variant_of(offer):
    """The line's stored type. 'one_sided' comes only from the adapter, which reads the sides Underdog LISTS (offered
    or suspended), so a passing suspension of one side never changes the type (audit follow-up 2026-11-06: the
    earlier re-read of stored 'balanced' rows by OFFERED sides flipped a two-sided line to one_sided while one side
    was suspended). offer = OfferSnapshot or an offer view dict."""
    get = offer.get if isinstance(offer, dict) else (lambda k, d=None: getattr(offer, k, d))
    return get("variant")


def is_ordinary(offer):
    return variant_of(offer) in ORDINARY_VARIANTS


def is_one_sided(offer):
    """Owner 2026-11-06: a stored one-sided line is shown (Over only / Under only) and never graded."""
    return variant_of(offer) in UNGRADED_VARIANTS


def only_side(offer):
    """'over' / 'under' for a one-sided line: its offered side (the adapter stores both sides, the unlisted one as not
    offered), else None (not one-sided, or its one side is suspended right now)."""
    if not is_one_sided(offer):
        return None
    get = offer.get if isinstance(offer, dict) else (lambda k, d=None: getattr(offer, k, d))
    on = {(x.get("side") if isinstance(x, dict) else getattr(x, "side", None)) for x in (get("sides") or ())
          if (x.get("offered") if isinstance(x, dict) else getattr(x, "offered", False))}
    return next(iter(on)) if len(on) == 1 and on <= {"over", "under"} else None


@dataclass(frozen=True)
class Handler:
    name: str
    kind: str                     # single / combo / binary / spread / total
    gradable: bool
    reason: str = ""

    def can_grade(self):
        return (True, None) if self.gradable else (False, self.reason)


HANDLERS = {
    "single": Handler("single", "single", True),
    "single_research": Handler("single_research", "single", False,
                               "single stat outside the supported list: recorded for research, not graded"),
    "joint": Handler("joint", "combo", True),
    "combo": Handler("combo", "combo", False,
                     "combo market: needs a validated joint (covariance) model - sums of independent chances refused"),
    "touchdown": Handler("touchdown", "binary", False,
                         "touchdown scorer is a binary event: needs an explicit binary model - not built"),
    "double_double": Handler("double_double", "binary", False,
                             "double-double / triple-double needs a joint distribution of several stats - not built"),
    "spread": Handler("spread", "spread", False, "game spread: team-level model and push/OT rules not built"),
    "total": Handler("total", "total", False, "game total: team-level model and push/OT rules not built"),
    "unknown": Handler("unknown", "unknown", False, "stat not recognised: never mapped to another stat"),
}
# source stat labels that name a refused market family (matched whole, lower-case)
_FAMILY_WORDS = {"touchdown": ("touchdowns", "anytime td", "anytime tds", "rush tds", "rec tds", "rush+rec tds",
                               "pass+rush+rec tds", "td scorer", "rush_rec_tds", "first_touchdown_scored",
                               "period_first_touchdown_scored", "last_touchdown_scored", "rushing_tds",
                               "receiving_tds"),
                 "double_double": ("double-double", "double double", "triple-double", "triple double",
                                   "double_doubles", "triple_doubles"),
                 "spread": ("spread", "point spread"), "total": ("total points (game)", "game total")}


@dataclass(frozen=True)
class Capability:
    status: str                   # supported / research_only / unsupported
    handler: Handler
    reason: str | None

    @property
    def gradable(self):
        return self.status == "supported"


def classify(league, stat, source_stat=None) -> Capability:
    if stat is None:
        lab = (source_stat or "").strip().lower()
        for fam, words in _FAMILY_WORDS.items():
            if lab in words:
                return Capability("unsupported", HANDLERS[fam], HANDLERS[fam].reason)
        return Capability("unsupported", HANDLERS["unknown"], HANDLERS["unknown"].reason)
    if stat in SUPPORTED.get(league, ()):
        return Capability("supported", HANDLERS["single"], None)
    if league == "nba" and stat in nba_stats8_markets():          # NBA_STATS8: walk-forward-passed nba8-1 stats
        return Capability("supported", HANDLERS["joint" if stat in COMBOS else "single"], None)
    if stat in COMBOS:
        return Capability("research_only", HANDLERS["combo"], HANDLERS["combo"].reason)
    return Capability("research_only", HANDLERS["single_research"], HANDLERS["single_research"].reason)


def gate(offer) -> tuple:
    """-> (ok, [reason, ...]) for grading this OfferSnapshot."""
    why = []
    cap = classify(offer.league, offer.stat, offer.source_stat)
    if not cap.gradable:
        why.append(cap.reason)
    if offer.scope != "full_game":
        why.append(f"scope {offer.scope}: only full-game markets are graded")
    if offer.promotional:
        why.append("promotional line: never graded or mixed with ordinary lines")
    if variant_of(offer) not in ORDINARY_VARIANTS:
        why.append(f"variant {variant_of(offer)}: alternate payout line, kept apart from ordinary lines")
    if is_one_sided(offer):
        why.append(ONE_SIDED_REASON)
    if offer.event_id is None:
        why.append("event not resolved to a canonical event")
    if offer.player_id is None:
        why.append("player not resolved to a canonical player")
    if not offer.offered_sides():
        why.append("no side offered")
    if offer.availability != "open":
        why.append(f"offer {offer.availability}")
    return (not why, why)


def compatible(a, b) -> tuple:
    """Same market on two platforms/books? -> (ok, reason)."""
    if a.league != b.league:
        return False, "different league"
    if a.event_id is None or a.event_id != b.event_id:
        return False, "different or unresolved event"
    if a.player_id is None or a.player_id != b.player_id:
        return False, "different or unresolved player"
    if a.stat is None or a.stat != b.stat:
        return False, "different stat"
    if a.scope != b.scope:
        return False, f"different scope ({a.scope} vs {b.scope})"
    if a.promotional or b.promotional:
        return False, "promotional line"
    if variant_of(a) not in ORDINARY_VARIANTS or variant_of(b) not in ORDINARY_VARIANTS:
        return False, "alternate-payout variant"
    return True, None


def table():
    """For coverage / docs: every market key the parsers know, with its status per league."""
    from . import markets
    out = {}
    for lg, fam in (("nba", "basketball"), ("nfl", "football")):
        out[lg] = {mk: classify(lg, mk).status for mk in markets.MARKETS[fam]}
    return out
