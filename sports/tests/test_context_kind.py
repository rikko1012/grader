"""The stored dict of a ContextSnapshot keeps the context's own kind (injury, starter, ...) - before, the record-type tag
"context" overwrote it, so a stored injury came back with kind "context"."""
from sports.contracts import schema


def test_context_kind_survives_a_round_trip():
    c = schema.ContextSnapshot(league="nba", kind="injury", subject_id="p1", event_id="e1", status="out", present="yes",
                               observed_at="2026-10-23T22:00:00Z")
    d = c.to_dict()
    assert d["kind"] == "context" and d["context_kind"] == "injury"
    back = schema.from_dict(d)
    assert isinstance(back, schema.ContextSnapshot) and back.kind == "injury" and back == c
