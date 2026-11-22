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
ONE_SIDED_PLATFORMS = {"underdog", "prizepicks"}
# os-2 (audit_1121 round 2, SPORTS_PP_ONESIDED default on; =0 rolls back): a PrizePicks line whose stored sides list ONE
# side (allowed_wager_types "over" or "under") is one-sided too. PrizePicks has no per-side suspension - the adapter sets
# a side's `offered` from the listing itself (sports/adapters.pp_offers) - so the listed sides ARE the line's type here.
# Live 2026-11-21 these were "standard" More-only lines lettered on the unlisted Under (audit: Unders on PrizePicks lines
# below Underdog's hit 44.6%, Overs above it 39.2%).
PP_STANDARD = ("standard", None, "")


def _pp_one_sided(get):
    if get("platform") != "prizepicks" or get("variant") not in PP_STANDARD:
        return False
    from . import grade_rules
    if not grade_rules.pp_onesided_on():
        return False
    on = set()
    for x in get("sides") or ():
        side = x.get("side") if isinstance(x, dict) else getattr(x, "side", None)
        if (x.get("offered") if isinstance(x, dict) else getattr(x, "offered", False)):
            on.add(side)
    return len(on & {"over", "under"}) == 1


def variant_of(offer):
    """The line's stored type. 'one_sided' comes from the adapter, which reads the sides Underdog LISTS (offered
    or suspended), so a passing suspension of one side never changes the type (audit follow-up 2026-11-06: the
    earlier re-read of stored 'balanced' rows by OFFERED sides flipped a two-sided line to one_sided while one side
    was suspended) - and (os-2) from a PrizePicks line that lists one side. offer = OfferSnapshot or an offer view dict."""
    get = offer.get if isinstance(offer, dict) else (lambda k, d=None: getattr(offer, k, d))
    v = get("variant")
    if _pp_one_sided(get):
        return "one_sided"
    return v


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
