"""Item 6 (2026-10-14 owner): no hypothetical 'average return per 1U' in the slip panel; a payout only when the member
entered it for that exact entry, labelled; the card names the price actually used with its own age and whether the odds
changed the grade."""
from pathlib import Path

HTML = (Path(__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_no_average_return_or_saved_standard_payout_in_the_slip_panel():
    for gone in ("back per 1U", "average return at", "Average return =", "your saved standard", "retLine("):
        assert gone not in HTML, gone
    assert "Payout you entered for this exact entry" in HTML and "No return is computed" in HTML


def test_card_shows_the_used_price_age_and_the_odds_effect():
    assert "when graded)" in HTML and "oi.quote_age_s" in HTML
    assert "Effect of these odds on this grade" in HTML and "none - context only" in HTML
