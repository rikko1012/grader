"""The shapes every sport adapter speaks. Chances follow sports/contracts Forecast: unconditional p_over / p_under / p_push."""
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class Offer:
    sport: str
    league: str
    event_id: str
    player: str
    stat: str
    scope: str
    line: float
    book: str
    variant: str = "standard"
    start_utc: str | None = None


@dataclass(frozen=True)
class Prediction:
    p_over: float
    p_under: float
    p_push: float = 0.0
    projection: float | None = None
    versions: dict = field(default_factory=dict)
    reason_codes: tuple = ()

    def __post_init__(self):
        probabilities = (self.p_over, self.p_under, self.p_push)
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p)
               or not 0 <= p <= 1 for p in probabilities):
            raise ValueError("chances must be finite numbers between 0 and 1")
        if abs(sum(probabilities) - 1) > 1e-6:
            raise ValueError("chances must be non-negative and sum to 1")
        if self.projection is not None and (isinstance(self.projection, bool)
                or not isinstance(self.projection, (int, float)) or not math.isfinite(self.projection)):
            raise ValueError("projection must be a finite number or None")

    @property
    def p_over_given_no_push(self):
        decided = self.p_over + self.p_under
        return self.p_over / decided if decided else None

    @property
    def p_under_given_no_push(self):
        decided = self.p_over + self.p_under
        return self.p_under / decided if decided else None


@dataclass(frozen=True)
class MarketQuote:
    p_over: float
    p_under: float
    source: str
    width: float | None = None
    age_s: float | None = None


@dataclass(frozen=True)
class Settlement:
    status: str                # win / loss / push / void; a push is reported apart, never a win
    actual: float | None = None


@dataclass(frozen=True)
class Leg:
    """Immutable selected offer, separate from a mutable board row or live quote."""
    sport: str
    league: str
    event_id: str
    match_key: str
    team_key: str
    player: str
    stat: str
    scope: str
    line: float
    side: str
    book: str
    variant: str
    p_cal: float | None
    letter: str | None
    start_utc: str
    offer_revision: str
    added_at: str

    def __post_init__(self):
        from datetime import datetime
        if self.sport not in ('cs2', 'lol', 'nba', 'nfl') or self.side not in ('over', 'under'):
            raise ValueError('unsupported sport or side')
        for name in ('league', 'event_id', 'match_key', 'team_key', 'player', 'stat', 'scope', 'book', 'variant', 'offer_revision'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} is required')
        if isinstance(self.line, bool) or not isinstance(self.line, (int, float)) or not math.isfinite(self.line):
            raise ValueError('line must be finite')
        if self.p_cal is not None and (isinstance(self.p_cal, bool) or not isinstance(self.p_cal, (int, float))
                or not math.isfinite(self.p_cal) or not 0 <= self.p_cal <= 1):
            raise ValueError('p_cal must be a finite chance or None')
        if self.letter is not None and self.p_cal is None:
            raise ValueError('a letter requires a calibrated chance')
        for value in (self.start_utc, self.added_at):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('leg timestamps require a timezone')
