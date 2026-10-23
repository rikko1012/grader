"""Canonical sports data contracts, version sc-1.

Every record that crosses a module boundary (ingestion -> storage -> model -> UI -> settlement) is one of these frozen
dataclasses. They are plain data: no network, no storage, no model code. Each record type has

  KIND             the storage kind ("event", "offer", ...)
  identity()       the stable SOURCE IDENTITY a revision chain hangs on (e.g. platform + provider offer id)
  content_hash()   sha256 of the canonical JSON of every field except the observation-time fields (HASH_EXCLUDE), so a
                   re-observation of the same content is a duplicate and a change is a new revision
  validate()       -> list of problems (empty = valid); validate_or_raise() raises ContractError

Conventions (owner brief section 3):
- every timestamp is a UTC ISO string "YYYY-MM-DDTHH:MM:SS[.ffffff]Z" (utc() normalises); local time is a display concern
- source_updated_at (the source's own update time), observed_at (when our server read it) and source_time_unknown
  (the source gives no usable update time) are separate fields; a cache-read time is never quote freshness
- known_at is when the information was available to us; historical joins require known_at <= forecast_at
- unknown is not false: tri-state facts use TRI = ("yes", "no", "unknown") and default to "unknown"
- probabilities in a Forecast are UNCONDITIONAL (p_over + p_under + p_push = 1); the conditional-on-no-push values are
  separate, explicitly named fields
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime, timezone
from typing import ClassVar, Optional

SCHEMA_VERSION = "sc-1"

LEAGUES = ("nba", "nfl", "wnba", "cbb", "cfb")
SPORT_OF = {"nba": "basketball", "wnba": "basketball", "cbb": "basketball", "nfl": "football", "cfb": "football"}
TRI = ("yes", "no", "unknown")
EVENT_STATUS = ("scheduled", "in_progress", "final", "postponed", "cancelled", "suspended", "unknown")
SEASON_TYPES = ("preseason", "regular", "postseason", "unknown")
SCOPES = ("full_game", "first_half", "second_half", "q1", "q2", "q3", "q4", "first_quarter_group", "regulation",
          "unknown")
SIDES = ("over", "under")
PRICE_FORMATS = ("none", "decimal", "american", "exchange_yes_no", "payout_multiplier")
VARIANTS = ("standard", "goblin", "demon", "alternate", "balanced", "ladder_rung", "main", "unknown")
AVAILABILITY = ("open", "suspended", "removed", "unknown")
PARTICIPATION = ("played", "did_not_play", "inactive", "unknown")
CONTEXT_KINDS = ("injury", "starter", "teammate_absent", "expected_minutes", "expected_snaps", "role", "weather",
                 "venue", "news")
SETTLEMENT_STATUS = ("win", "loss", "push", "void", "unresolved")
OUTCOMES = ("over", "under", "push")

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
PROB_TOL = 1e-9


class ContractError(ValueError):
    pass


def utc(value) -> Optional[str]:
    """Any ISO time with an offset (or a datetime) -> canonical UTC 'YYYY-MM-DDTHH:MM:SS[.ffffff]Z'; None stays None.
    A naive time is REJECTED (ContractError): a time without a zone has no provenance."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        t = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s[:16]) and len(s) == 16:
            raise ContractError(f"time without zone: {value!r}")
        try:
            t = datetime.fromisoformat(s)
        except ValueError as e:
            raise ContractError(f"unreadable time {value!r}") from e
    if t.tzinfo is None:
        raise ContractError(f"time without zone: {value!r}")
    t = t.astimezone(timezone.utc)
    if t.microsecond:
        return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ").rstrip("0").replace(".Z", "Z")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_utc() -> str:
    return utc(datetime.now(timezone.utc).replace(microsecond=0))


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _freeze(v):
    """lists -> tuples, dicts -> sorted tuple of pairs is NOT used (dicts stay dicts but are copied)."""
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    return v


@dataclass(frozen=True)
class _Record:
    KIND: ClassVar[str] = ""
    HASH_EXCLUDE: ClassVar[tuple] = ("observed_at", "known_at", "schema_version")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.KIND
        return d

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        kw = {k: _freeze(v) for k, v in d.items() if k in names}
        for f in fields(cls):                      # nested dataclasses (SideQuote, Alias, ...) from plain dicts
            sub = _NESTED.get((cls.__name__, f.name))
            if sub and kw.get(f.name) is not None:
                kw[f.name] = tuple(x if isinstance(x, sub) else sub(**x) for x in kw[f.name])
        return cls(**kw)

    def hash_payload(self) -> dict:
        d = self.to_dict()
        for k in self.HASH_EXCLUDE:
            d.pop(k, None)
        return d

    def content_hash(self) -> str:
        """128-bit (32 hex) sha256 prefix of the canonical content."""
        return hashlib.sha256(_canon(self.hash_payload()).encode("utf-8")).hexdigest()[:32]

    def identity(self) -> tuple:                  # (source, source_key)
        raise NotImplementedError

    def validate(self) -> list:
        return []

    def validate_or_raise(self):
        errs = self.validate()
        if errs:
            raise ContractError(f"{self.KIND}: " + "; ".join(errs))
        return self


def _ts_errors(obj, names, required=()):
    out = []
    for n in names:
        v = getattr(obj, n)
        if v is None:
            if n in required:
                out.append(f"{n} is required")
        elif not isinstance(v, str) or not _TS.match(v):
            out.append(f"{n} must be UTC 'YYYY-MM-DDTHH:MM:SSZ' (got {v!r})")
    return out


def _enum(obj, name, allowed):
    v = getattr(obj, name)
    return [] if v in allowed else [f"{name}={v!r} not in {allowed}"]


def _prob(v, name):
    if v is None:
        return []
    if not isinstance(v, (int, float)) or math.isnan(v) or not (0.0 <= v <= 1.0):
        return [f"{name}={v!r} is not a probability"]
    return []


# --------------------------------------------------------------------------------------------------------- identities
@dataclass(frozen=True)
class SourceId:
    source: str
    source_id: str


@dataclass(frozen=True)
class Alias:
    """A dated name / abbreviation a source used. valid_to None = still in use. Aliases are never fuzzy: an alias is
    added only with corroboration (same team, same game) and keeps where it came from."""
    alias: str
    source: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence: Optional[str] = None


@dataclass(frozen=True)
class Reschedule:
    from_utc: Optional[str]
    to_utc: Optional[str]
    seen_at: str
    source: str


@dataclass(frozen=True)
class Team(_Record):
    KIND: ClassVar[str] = "team"
    team_id: str                                   # canonical, e.g. "nba:team:espn:2"
    league: str
    abbr: str                                      # ESPN's abbreviation (the canonical one)
    name: str
    source_ids: tuple = ()                         # SourceId...
    aliases: tuple = ()                            # Alias...
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return ("canonical", self.team_id)

    def validate(self):
        e = _enum(self, "league", LEAGUES) + _ts_errors(self, ("observed_at", "known_at"))
        if not self.team_id or not self.abbr:
            e.append("team_id and abbr are required")
        return e


@dataclass(frozen=True)
class Player(_Record):
    KIND: ClassVar[str] = "player"
    player_id: str                                 # canonical, e.g. "nba:player:espn:3134907"
    league: str
    name: str
    position: Optional[str] = None
    status: str = "unknown"                        # active / inactive / injured_reserve / unknown (league roster status)
    source_ids: tuple = ()
    aliases: tuple = ()
    resolved: bool = True                          # False = provisional id (no corroborated ESPN athlete yet)
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return ("canonical", self.player_id)

    def validate(self):
        e = _enum(self, "league", LEAGUES) + _ts_errors(self, ("observed_at", "known_at"))
        if not self.player_id or not self.name:
            e.append("player_id and name are required")
        return e


@dataclass(frozen=True)
class Membership(_Record):
    """A player on a team for a dated interval (trades = a closed interval + a new one; never an overwrite)."""
    KIND: ClassVar[str] = "membership"
    player_id: str
    team_id: str
    valid_from: str
    valid_to: Optional[str] = None
    source: str = "espn"
    role: str = "unknown"                          # starter / rotation / bench / unknown (as of valid_from)
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.source, f"{self.player_id}|{self.team_id}|{self.valid_from}")

    def validate(self):
        e = _ts_errors(self, ("valid_from", "valid_to", "observed_at", "known_at"), required=("valid_from",))
        if self.valid_to and self.valid_from and self.valid_to < self.valid_from:
            e.append("valid_to before valid_from")
        return e


# ------------------------------------------------------------------------------------------------------------- event
@dataclass(frozen=True)
class Event(_Record):
    KIND: ClassVar[str] = "event"
    event_id: str                                  # canonical, e.g. "nba:espn:401810123"
    league: str
    season: Optional[int]
    season_type: str
    home_team_id: Optional[str]
    away_team_id: Optional[str]
    start_utc: Optional[str]
    start_source: str                              # provenance of start_utc ("espn", ...)
    status: str = "unknown"
    reschedules: tuple = ()                        # Reschedule...
    source_ids: tuple = ()                         # SourceId...
    settlement_source: str = "espn"
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    source_updated_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.start_source, self.event_id)

    @property
    def sport(self):
        return SPORT_OF.get(self.league)

    def validate(self):
        e = (_enum(self, "league", LEAGUES) + _enum(self, "status", EVENT_STATUS)
             + _enum(self, "season_type", SEASON_TYPES)
             + _ts_errors(self, ("start_utc", "observed_at", "known_at", "source_updated_at")))
        if not self.event_id:
            e.append("event_id is required")
        if self.home_team_id and self.home_team_id == self.away_team_id:
            e.append("home and away are the same team")
        for r in self.reschedules:
            e += _ts_errors(r, ("from_utc", "to_utc", "seen_at"))
        return e


# -------------------------------------------------------------------------------------------------- stat observation
@dataclass(frozen=True)
class StatObservation(_Record):
    """One stat for one participant in one event and scope, as one source reported it at one time. A provider
    correction is a new revision of the same identity (store keeps both)."""
    KIND: ClassVar[str] = "stat"
    event_id: str
    league: str
    participant_id: str                            # player_id or team_id
    participant_type: str                          # "player" | "team"
    scope: str                                     # full_game (incl. overtime), first_half, q1, ...
    includes_overtime: str                         # TRI
    stat: str                                      # box-score part key: pts, reb, ast, fg3m, pass_yds, rush_att, ...
    unit: str                                      # "count" | "yards" | "minutes"
    value: Optional[float]
    participation: str                             # PARTICIPATION
    source: str
    source_ref: str                                # e.g. ESPN event id + athlete id
    final: bool                                    # the source marked the game final when read
    observed_at: str
    known_at: str
    source_updated_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.source, f"{self.event_id}|{self.participant_id}|{self.scope}|{self.stat}")

    def validate(self):
        e = (_enum(self, "league", LEAGUES) + _enum(self, "scope", SCOPES) + _enum(self, "includes_overtime", TRI)
             + _enum(self, "participation", PARTICIPATION)
             + _ts_errors(self, ("observed_at", "known_at", "source_updated_at"), required=("observed_at", "known_at")))
        if self.participant_type not in ("player", "team"):
            e.append("participant_type must be player or team")
        if self.value is not None and (not isinstance(self.value, (int, float)) or math.isnan(self.value)):
            e.append("value must be a number or None")
        if self.participation == "did_not_play" and self.value not in (None, 0, 0.0):
            e.append("did_not_play with a non-zero value")
        return e


# ------------------------------------------------------------------------------------------------------------- offer
@dataclass(frozen=True)
class SideQuote:
    """One side of an offer. offered False = NOT OFFERED (never a 50/50). Prices only as the source gives them:
    decimal / american odds, exchange bid/ask (dollars, YES for over, NO for under) or a pick'em payout modifier."""
    side: str
    offered: bool
    price_format: str = "none"
    price: Optional[float] = None                  # decimal or american odds
    bid: Optional[float] = None                    # exchange: best bid for this side's contract (0-1 dollars)
    ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    multiplier: Optional[float] = None             # Underdog payout modifier (NOT a probability, NOT odds)


@dataclass(frozen=True)
class OfferSnapshot(_Record):
    KIND: ClassVar[str] = "offer"
    # A REVISION = a change of what is offered: line, stat, scope, sides offered, prices (bid / ask / odds / payout
    # modifier), variant, promotional flag, availability, listed start. Bookkeeping that moves without the offer
    # changing is recorded with each revision but does not create one (measured 2026-10-23: PrizePicks touches
    # updated_at on ~28% of unchanged projections per 15 min; Kalshi book sizes move on most markets):
    # liquidity, book sizes, extras, source_updated_at, observed_at / known_at.
    HASH_EXCLUDE: ClassVar[tuple] = ("observed_at", "known_at", "schema_version", "liquidity", "extras",
                                     "source_updated_at")

    def hash_payload(self) -> dict:
        d = super().hash_payload()
        d["sides"] = [{k: v for k, v in s.items() if k not in ("bid_size", "ask_size")} for s in d["sides"]]
        return d
    platform: str                                 # prizepicks / underdog / kalshi / oddsapi:<book>
    offer_id: str                                  # provider's offer / market id (stable source identity)
    market_id: Optional[str]                       # provider's market grouping id (Kalshi event ticker, ...)
    league: str
    event_id: Optional[str]                        # canonical event, None = unresolved (then never graded)
    event_ref: Optional[str]                       # the source's own game reference ("CHI @ MIL", ticker, id)
    player_id: Optional[str]                       # canonical player, None = unresolved
    player_name: str                               # as the source printed it
    player_key: str                                # names.key(player_name) - exact normalised, never fuzzy
    team: Optional[str]                            # the source's team abbreviation
    stat: Optional[str]                            # canonical market key (markets.py) or None = unknown stat
    source_stat: str                               # the source's own stat label, kept verbatim
    scope: str
    line: float
    sides: tuple                                   # SideQuote...
    variant: str                                   # standard / goblin / demon / balanced / ladder_rung / main ...
    promotional: bool
    availability: str
    rule_version: str                              # platform_rules / payout table version this offer is read under
    listed_start_utc: Optional[str]                # the start time the SOURCE lists (provenance in listed_time_kind)
    listed_time_kind: str                          # "start" | "expected_expiration" (Kalshi) | "unknown"
    source_updated_at: Optional[str]
    observed_at: str
    source_time_unknown: bool
    liquidity: tuple = ()                          # (("volume", x), ("open_interest", y), ("last_price", z)) as given
    extras: tuple = ()                             # verbatim source fields outside the contract, e.g. Underdog's
                                                   # UNDOCUMENTED decimal_price: (("ud_decimal_over", 1.77), ...)
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.platform, self.offer_id)

    def side(self, name) -> Optional[SideQuote]:
        return next((s for s in self.sides if s.side == name), None)

    def offered_sides(self):
        return tuple(s.side for s in self.sides if s.offered)

    def liquidity_dict(self):
        return dict(self.liquidity)

    def validate(self):
        e = (_enum(self, "league", LEAGUES) + _enum(self, "scope", SCOPES) + _enum(self, "variant", VARIANTS)
             + _enum(self, "availability", AVAILABILITY)
             + _ts_errors(self, ("listed_start_utc", "source_updated_at", "observed_at", "known_at"),
                          required=("observed_at",)))
        if self.listed_time_kind not in ("start", "expected_expiration", "unknown"):
            e.append("listed_time_kind invalid")
        if self.source_time_unknown and self.source_updated_at:
            e.append("source_time_unknown with a source_updated_at")
        if not self.source_time_unknown and not self.source_updated_at:
            e.append("source_updated_at missing: set source_time_unknown=True instead of inventing one")
        if not isinstance(self.line, (int, float)) or not math.isfinite(self.line):
            e.append(f"line={self.line!r} invalid")        # negative lines are real (rush yards -0.5)
        names = [s.side for s in self.sides]
        if sorted(names) != sorted(set(names)) or not set(names) <= set(SIDES):
            e.append("sides must be distinct over/under")
        for s in self.sides:
            if s.price_format not in PRICE_FORMATS:
                e.append(f"{s.side}: price_format {s.price_format!r}")
            for nm in ("bid", "ask"):
                v = getattr(s, nm)
                if v is not None and not (0.0 <= v <= 1.0):
                    e.append(f"{s.side}.{nm}={v} outside 0-1")
            if s.bid is not None and s.ask is not None and s.bid > s.ask:
                e.append(f"{s.side}: crossed quote bid {s.bid} > ask {s.ask}")
            if s.multiplier is not None and s.multiplier <= 0:
                e.append(f"{s.side}: multiplier must be > 0")
            if not s.offered and (s.price is not None or s.multiplier is not None):
                e.append(f"{s.side}: a price on a side that is not offered")
        if self.known_at and self.known_at < self.observed_at:
            e.append("known_at before observed_at")
        return e


# ----------------------------------------------------------------------------------------------------------- context
@dataclass(frozen=True)
class ContextSnapshot(_Record):
    """One context fact as of a time. status/value 'unknown' is NOT 'no': an absent injury report is unknown."""
    KIND: ClassVar[str] = "context"
    league: str
    kind: str                                      # CONTEXT_KINDS
    subject_id: str                                # player_id / team_id / event_id
    event_id: Optional[str]
    status: str                                    # source wording normalised: out / doubtful / questionable / ... / unknown
    present: str                                   # TRI: does the fact hold (e.g. starter? absent?) - "unknown" by default
    value: Optional[float] = None                  # expected minutes / snaps, temperature, ...
    detail: Optional[str] = None
    source: str = "espn"
    source_updated_at: Optional[str] = None
    as_of: Optional[str] = None                    # when the fact was true per the source
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    source_time_unknown: bool = True
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.source, f"{self.kind}|{self.subject_id}|{self.event_id or ''}")

    def validate(self):
        e = (_enum(self, "league", LEAGUES) + _enum(self, "kind", CONTEXT_KINDS) + _enum(self, "present", TRI)
             + _ts_errors(self, ("source_updated_at", "as_of", "observed_at", "known_at"), required=("observed_at",)))
        return e


# ---------------------------------------------------------------------------------------------------------- forecast
@dataclass(frozen=True)
class Forecast(_Record):
    """Immutable. forecast_id = hash of the content, so the same inputs + versions + seed give the same id (replay).
    p_over / p_under / p_push are UNCONDITIONAL and sum to 1; p_*_given_no_push are the conditional values, named."""
    KIND: ClassVar[str] = "forecast"
    HASH_EXCLUDE: ClassVar[tuple] = ("observed_at", "known_at", "schema_version", "forecast_id")
    forecast_id: str
    forecast_at: str
    league: str
    event_id: Optional[str]
    player_id: Optional[str]
    player_key: str
    stat: str
    scope: str
    line: float
    offer_snapshot_ids: tuple                      # store record ids of the offers this forecast is FOR
    input_snapshot_ids: tuple                      # store record ids of every input (quotes, stats, context)
    model_version: str
    artifact_version: Optional[str]
    data_version: Optional[str]
    calibration_version: Optional[str]
    grade_policy_version: Optional[str]
    projection_mean: Optional[float]
    projection_median: Optional[float]
    distribution: tuple                            # (("family", "negbin"), ("mean", ..), ("var_a", ..), ...) or ref
    p_over: float
    p_under: float
    p_push: float
    p_over_given_no_push: Optional[float]
    p_under_given_no_push: Optional[float]
    sides_available: tuple                         # sides the offer really lists
    chosen_side: Optional[str]
    grade: Optional[str]
    stake_units: Optional[float]
    stake_policy_version: Optional[str]
    reason_codes: tuple = ()
    seed: Optional[int] = None
    research_only: bool = True                     # True = never shown to members as a pick
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return ("forecast", self.forecast_id)

    @classmethod
    def build(cls, **kw):
        """Fills forecast_id from the content (deterministic)."""
        kw.setdefault("forecast_id", "")
        tmp = cls(**kw)
        return cls(**{**kw, "forecast_id": "fc-" + tmp.content_hash()[:24]})

    def validate(self):
        e = (_enum(self, "league", LEAGUES) + _enum(self, "scope", SCOPES)
             + _ts_errors(self, ("forecast_at", "observed_at", "known_at"), required=("forecast_at",)))
        for nm in ("p_over", "p_under", "p_push", "p_over_given_no_push", "p_under_given_no_push"):
            e += _prob(getattr(self, nm), nm)
        if not e:
            if abs(self.p_over + self.p_under + self.p_push - 1.0) > 1e-6:
                e.append(f"p_over+p_under+p_push = {self.p_over + self.p_under + self.p_push:.9f} != 1")
            half = abs(self.line - round(self.line)) > 1e-9
            if half and self.p_push > PROB_TOL:
                e.append("half-point line with push probability")
            dec = 1.0 - self.p_push
            if self.p_over_given_no_push is not None and dec > 0:
                if abs(self.p_over_given_no_push - self.p_over / dec) > 1e-6:
                    e.append("p_over_given_no_push != p_over / (1 - p_push)")
            if self.p_under_given_no_push is not None and dec > 0:
                if abs(self.p_under_given_no_push - self.p_under / dec) > 1e-6:
                    e.append("p_under_given_no_push != p_under / (1 - p_push)")
        if self.chosen_side is not None:
            if self.chosen_side not in SIDES:
                e.append("chosen_side invalid")
            elif self.chosen_side not in self.sides_available:
                e.append(f"chosen_side {self.chosen_side} is not offered")
        if not set(self.sides_available) <= set(SIDES):
            e.append("sides_available invalid")
        if self.forecast_id and self.forecast_id != "fc-" + self.content_hash()[:24]:
            e.append("forecast_id does not match content (forecasts are immutable)")
        if not self.model_version:
            e.append("model_version is required")
        return e


# -------------------------------------------------------------------------------------------------------- settlement
@dataclass(frozen=True)
class Settlement(_Record):
    """One settlement of one offer/side under one platform rule version. A provider correction or a rule fix is a NEW
    revision (supersedes = the earlier settlement_id); the earlier one is kept."""
    KIND: ClassVar[str] = "settlement"
    settlement_id: str
    platform: str
    offer_id: str
    side: str
    line: float
    event_id: Optional[str]
    player_id: Optional[str]
    stat: str
    status: str                                    # SETTLEMENT_STATUS
    outcome: Optional[str]                         # over / under / push, None when void / unresolved
    source_value: Optional[float]                  # the settled stat value
    source: str
    source_ref: Optional[str]                      # the StatObservation record id(s) it came from
    rule_version: str
    affected_forecast_ids: tuple = ()
    revision: int = 1
    supersedes: Optional[str] = None
    reason: Optional[str] = None
    stake: Optional[float] = None
    payout: Optional[float] = None                 # actual payout if known (never assumed)
    settled_at: Optional[str] = None
    observed_at: Optional[str] = None
    known_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def identity(self):
        return (self.platform, f"{self.offer_id}|{self.side}")

    def validate(self):
        e = (_enum(self, "status", SETTLEMENT_STATUS) + _enum(self, "side", SIDES)
             + _ts_errors(self, ("settled_at", "observed_at", "known_at"), required=("settled_at",)))
        if self.outcome is not None and self.outcome not in OUTCOMES:
            e.append("outcome invalid")
        if self.status in ("win", "loss", "push") and self.outcome is None:
            e.append(f"status {self.status} needs an outcome")
        if self.status == "push" and self.outcome != "push":
            e.append("push status needs a push outcome")
        if self.status in ("win", "loss") and self.outcome == "push":
            e.append("a push outcome cannot win or lose")
        if self.status == "win" and self.outcome != self.side:
            e.append("win but outcome is not the chosen side")
        if self.status == "loss" and self.outcome == self.side:
            e.append("loss but outcome is the chosen side")
        if self.source_value is not None and self.outcome is not None:
            want = "over" if self.source_value > self.line else ("under" if self.source_value < self.line else "push")
            if want != self.outcome:
                e.append(f"outcome {self.outcome} does not follow value {self.source_value} vs line {self.line}")
        if self.revision < 1 or (self.revision > 1 and not self.supersedes):
            e.append("a revision > 1 must name the settlement it supersedes")
        return e


_NESTED = {("Team", "source_ids"): SourceId, ("Team", "aliases"): Alias, ("Player", "source_ids"): SourceId,
           ("Player", "aliases"): Alias, ("Event", "reschedules"): Reschedule, ("Event", "source_ids"): SourceId,
           ("OfferSnapshot", "sides"): SideQuote}

RECORD_TYPES = {c.KIND: c for c in (Team, Player, Membership, Event, StatObservation, OfferSnapshot, ContextSnapshot,
                                    Forecast, Settlement)}


def from_dict(d: dict):
    """A stored dict (with "kind") -> its record. Tuples-of-pairs fields (liquidity, distribution) come back as tuples."""
    cls = RECORD_TYPES[d["kind"]]
    d = dict(d)
    for k in ("liquidity", "distribution", "extras"):
        if k in d and d[k] is not None:
            d[k] = tuple(tuple(x) for x in d[k])
    return cls.from_dict(d)


def json_schema(kind: str) -> dict:
    """A JSON Schema (draft 2020-12) description of one record type, generated from the dataclass (documentation and
    cross-language consumers; validate() remains the authority)."""
    cls = RECORD_TYPES[kind]
    props, req = {}, []
    for f in fields(cls):
        t = str(f.type)
        js = {"type": "string"}
        if "float" in t:
            js = {"type": ["number", "null"]} if "Optional" in t else {"type": "number"}
        elif "int" in t:
            js = {"type": ["integer", "null"]} if "Optional" in t else {"type": "integer"}
        elif "bool" in t:
            js = {"type": "boolean"}
        elif "tuple" in t:
            js = {"type": "array"}
        elif "Optional" in t:
            js = {"type": ["string", "null"]}
        props[f.name] = js
        if f.default is MISSING and f.default_factory is MISSING:
            req.append(f.name)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": f"{cls.__name__} ({SCHEMA_VERSION})",
            "type": "object", "properties": props, "required": req}
