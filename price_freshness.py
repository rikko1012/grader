"""Price freshness in one place (owner 2026-11-14): "For an older price, show the source and age beside the number, for example
'DraftKings · checked 42 min ago.' If it's too old to use, exclude it from Best Price, EV, Arbitrage and slip generation. A missing
value should never become a zero."

  - limits: the caller passes its own (gradecore.fair.FRESHNESS_S: sportsbook 30 min, exchange 5 min, or workspace_ev.price_limits()
    for a cached build) - the limits the +EV board, Market edge, Price check and the price slips already apply. Nothing here
    loosens or adds a limit, and this module imports no gradecore (display helper only).
  - fresh(): a price with no time, a time that is not a number, or a time more than FUTURE_SLACK_S in the future is not fresh.
  - age_text() / checked_label(): the words beside an older price. A missing time gives no age words (never "0 min ago").
    web/upgrade-fresh.js is the page's copy of the same rules (tests/test_price_freshness_1114.py keeps the two equal).

Pick'em app lines (PrizePicks / Underdog board lines) are not prices that go stale this way: their feed state rules stay as
they are, and nothing here is applied to them. Pure functions: no I/O, no provider request."""
import math

FUTURE_SLACK_S = 60.0
EXCHANGES = ("kalshi", "polymarket")


def kind_of(platform=None, kind=None):
    """'exchange' for a prediction market's bid / ask (a 'mid' row, an exchange quote, Kalshi / Polymarket), else 'sportsbook'."""
    if kind in ("mid", "exchange", "prediction") or str(platform or "").split(":")[-1].lower() in EXCHANGES:
        return "exchange"
    return "sportsbook"


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def age_s(seen_ts, now):
    """Seconds since the price was seen, or None when its time is missing (never 0)."""
    if not _finite(seen_ts) or not _finite(now):
        return None
    return float(now) - float(seen_ts)


def fresh(seen_ts, now, limit):
    """True only for a price seen within `limit` seconds (a clock up to FUTURE_SLACK_S ahead is allowed)."""
    a = age_s(seen_ts, now)
    return a is not None and _finite(limit) and -FUTURE_SLACK_S <= a <= float(limit)


def age_text(age):
    """'checked just now' / 'checked 42 min ago' / 'checked 3 h ago' / 'checked 2 d ago'; None when the age is unknown."""
    if not _finite(age) or age < -FUTURE_SLACK_S:
        return None
    if age < 60:
        return "checked just now"
    if age < 90 * 60:
        return f"checked {int(age // 60)} min ago"
    if age < 48 * 3600:
        return f"checked {int(age // 3600)} h ago"
    return f"checked {int(age // 86400)} d ago"


def checked_label(label, seen_ts, now):
    """'DraftKings · checked 42 min ago'; only the source when the time is missing."""
    words = age_text(age_s(seen_ts, now))
    label = str(label or "").strip()
    if not words:
        return label or None
    return f"{label} · {words}" if label else words[0].upper() + words[1:]
