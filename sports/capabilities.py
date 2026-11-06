"""Which markets Sports may grade, and the handlers that refuse the rest.

Status per (league, market):
  supported      single-stat full-game player props with typed data end to end (offer -> ESPN box score -> settlement):
                 NBA points / rebounds / assists / threes; NFL pass / rush / receiving yards, carries, receptions
  research_only  typed and recordable, but NOT gradable: combos (need a joint model, never a sum of independent
                 chances), other single stats not in the supported list
  unsupported    a handler exists (so offers are typed and counted) but it refuses to grade: touchdown scorer,
                 double-double / triple-double, game spread, game total, fantasy score, first-basket, ...

gate(offer) is the single check every model / slip / UI path calls before a forecast can be graded. It refuses:
unknown stat, unsupported market, non full-game scope, promotional lines, unresolved event / player, offers that list no
side, and removed offers. Side availability is per offer (OfferSnapshot.sides), never a platform-wide assumption.

compatible(a, b) says whether two offers are the SAME market (same canonical event, player, stat, scope, and both
ordinary lines) - the only case where prices from two books may be aligned or compared.
"""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED = {
    "nba": ("points", "rebounds", "assists", "threes"),
    "nfl": ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec"),
}
COMBOS = {"pra", "pr", "pa", "ra", "stocks", "rush_rec_yds", "pass_rush_yds"}
ORDINARY_VARIANTS = {"standard", "balanced", "main", "ladder_rung"}
CAPABILITY_VERSION = "cap-1"
ONE_SIDED_PLATFORMS = {"underdog"}


def variant_of(offer):
    """The line's REAL type (audit 2026-11-05 S1/S2). An Underdog line that lists only one side (higher-only at a
    changed payout, e.g. Austin Reaves 1Q Points 3.5 Over 0.86x) is a different market from the two-sided main line:
    'one_sided', never ordinary. New rows are stored that way by adapters.ud_offers; rows stored before carry
    'balanced' and are read as one_sided here, so no production row is rewritten. offer = OfferSnapshot or an offer
    view dict (snapshots.offer_view: platform, variant, sides [{side, offered}])."""
    get = offer.get if isinstance(offer, dict) else (lambda k, d=None: getattr(offer, k, d))
    v = get("variant")
    if v != "balanced" or get("platform") not in ONE_SIDED_PLATFORMS:
        return v
    on = sum(1 for x in (get("sides") or ())
             if (x.get("offered") if isinstance(x, dict) else getattr(x, "offered", False)))
    return "one_sided" if on == 1 else v


def is_ordinary(offer):
    return variant_of(offer) in ORDINARY_VARIANTS


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
