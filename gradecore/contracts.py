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
