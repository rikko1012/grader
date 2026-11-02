"""The shapes every sport adapter speaks. Chances follow sports/contracts Forecast: unconditional p_over / p_under / p_push."""
from dataclasses import dataclass, field


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
        if min(self.p_over, self.p_under, self.p_push) < 0 or abs(self.p_over + self.p_under + self.p_push - 1) > 1e-6:
            raise ValueError("chances must be non-negative and sum to 1")

    @property
    def p_over_given_no_push(self):
        return self.p_over / (self.p_over + self.p_under)

    @property
    def p_under_given_no_push(self):
        return self.p_under / (self.p_over + self.p_under)


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
