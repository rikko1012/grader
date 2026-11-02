from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from line_advantage import QuoteEvidence, find_line_advantages

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
TARGET = QuoteEvidence("cs2", "cs2", "hltv:1", "hltv-player:1", "kills", "12",
                       "book-a", "a1", "rev-1", 30.5, NOW, NOW + timedelta(hours=1),
                       True, True)
OTHER = replace(TARGET, book="book-b", offer_id="b1", line=29.5)


def find(target=TARGET, others=None, side="over"):
    return find_line_advantages(target, [OTHER] if others is None else others,
                                side=side, now=NOW)


def test_same_pick_easier_line_no_probability_or_grade_transfer():
    r, = find()
    assert r.threshold_improvement == 1
    assert r.requires_new_forecast and r.payout_comparison == "not_evaluated"
    assert not hasattr(r, "grade") and not hasattr(r, "probability")
    assert r.revision == "rev-1"


def test_under_needs_higher_line_and_never_reverses_side():
    assert not find(side="under")
    assert find(others=[replace(OTHER, line=32.5)], side="under")[0].threshold_improvement == 2


@pytest.mark.parametrize("field,value", [
    ("sport", "lol"), ("league", "other"), ("event_id", "hltv:2"),
    ("player_id", "hltv-player:2"), ("stat", "hs"), ("period", "1"),
    ("player_id", ""), ("revision", ""), ("offer_id", ""),
    ("start_at", NOW + timedelta(hours=2)), ("start_at", NOW),
    ("observed_at", NOW + timedelta(seconds=1)),
    ("observed_at", NOW - timedelta(seconds=601)),
    ("observed_at", NOW.replace(tzinfo=None)), ("active", False),
    ("over_available", False), ("feed_state", "stale"), ("feed_state", None),
    ("variant", "goblin"), ("line", float("nan")), ("line", float("inf")),
    ("line", -1), ("line", True), ("line", 30.5), ("book", "book-a"),
])
def test_rejects_incompatible_or_unverified_offer(field, value):
    assert not find(others=[replace(OTHER, **{field: value})])


def test_duplicate_identical_offer_counted_once():
    assert len(find(others=[OTHER, OTHER])) == 1


def test_current_withdrawal_or_returned_revision_cannot_be_ignored():
    assert not find(others=[OTHER, replace(OTHER, active=False, revision="rev-2")])
    assert not find(others=[OTHER, replace(OTHER, revision="rev-3")])


def test_target_moved_or_withdrawn_requires_recheck():
    assert not find(others=[OTHER, replace(TARGET, line=31.5, revision="rev-2")])
    assert not find(target=replace(TARGET, active=False))


def test_freshness_uses_observation_time_and_accepts_boundary():
    assert find(others=[replace(OTHER, observed_at=NOW-timedelta(seconds=600))])


def test_invalid_arguments():
    for side in ["Less", "", None]:
        with pytest.raises(ValueError):
            find(side=side)
    with pytest.raises(ValueError):
        find_line_advantages(TARGET, [OTHER], side="over", now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        find_line_advantages(TARGET, [OTHER], side="over", now=NOW, max_age_seconds=float("nan"))
