"""Bug sweep 2026-11-06, research assistant: #3 / #33 month-prefix words read as dates, #34 tool-JSON retry repeats
streamed text, #35 finish() after fail_stale refunds twice, #36 forecasts tool says truncated at exactly `limit`."""
from datetime import datetime, timezone
from types import SimpleNamespace

from sports import store
from sports.assistant import config, dates, grounding, llm, runner, runs, tools
from sports.tests import helpers as h
from sports.tests.test_assistant import KAT, NOW, _ctx, _FakeStream, seeded  # noqa: F401 (seeded = fixture)

SCAN_NOW = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------ #33 date scan
def test_month_prefix_words_are_not_dates():
    for q in ("Will Mark 3 threes again?", "markets 2 ago", "Can Junior 5 hit?", "Does Mayo 12 points?",
              "how did Octavius 2 do", "Marcus 3 assists", "His usage declined 4 games"):
        assert dates.scan(q, "nba", SCAN_NOW) == [], q


def test_real_month_words_still_resolve():
    for q, day in (("Mar 3", "2026-03-03"), ("March 3rd", "2026-03-03"), ("Sept. 14, 2025", "2025-09-14"),
                   ("sep 14", "2026-09-14"), ("September 9", "2026-09-09"), ("June 5", "2026-06-05"),
                   ("Jun 5", "2026-06-05"), ("Oct. 31st", "2026-10-31"), ("how did he do on May 12?", "2026-05-12")):
        got = dates.scan(q, "nba", SCAN_NOW)
        assert got and got[0]["us_dates"] == [day], q
    assert dates.resolve("Mark 3", "nba", SCAN_NOW) is None and dates.resolve("markets 2", "nba", SCAN_NOW) is None


# ------------------------------------------------------------------------------------------------- #3 grounding
def test_grounding_keeps_grounded_numbers_after_month_prefix_words():
    idx = grounding.Index().add({"record_id": "r1", "games": 4, "pts": 3, "n": 2})
    assert grounding.check("His usage declined 4 games in a row [ref:r1].", idx)[:2] == (
        "His usage declined 4 games in a row [ref:r1].", [])
    assert grounding.check("Marcus 3 assists [ref:r1].", idx)[:2] == ("Marcus 3 assists [ref:r1].", [])
    # a real date not in the results is still removed; one present is kept
    idx.add({"record_id": "r2", "at": "2026-03-03T01:00:00Z"})
    assert grounding.check("On March 3 he scored [ref:r2].", idx)[1] == []
    text, flags = grounding.check("On Mar 4 he scored [ref:r2].", idx)[:2]
    assert grounding.REMOVED in text and flags == [{"kind": "unsupported_date", "value": "Mar 4"}]


# ------------------------------------------------------------------------------------------- #34 tool-JSON retry
class _BadJsonStream(_FakeStream):
    """Streams its text, then fails like the SDK on unparseable tool input."""

    def __init__(self, events, snapshot):
        super().__init__(events, None)
        self.current_message_snapshot = snapshot

    def __iter__(self):
        yield from self.events
        raise ValueError("Unable to parse tool parameter JSON")


def test_tool_json_retry_does_not_repeat_streamed_text(monkeypatch):
    monkeypatch.setattr(config, "fallbacks_on", lambda: False)
    calls = {"n": 0}
    final = SimpleNamespace(content=[SimpleNamespace(type="text", text="Points: 21.5. Over.")], stop_reason="end_turn",
                            usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                                                  cache_creation_input_tokens=0))
    part = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=7, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))

    def stream(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _BadJsonStream([SimpleNamespace(type="text", text="Points: 21.5. ")], part)
        return _FakeStream([SimpleNamespace(type="text", text="Points: 21.5. "), SimpleNamespace(type="text", text="Over.")],
                           final)
    client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    got = []
    r = llm.AnthropicLLM(client=client).turn("s", [], [], got.append, lambda: False)
    assert "".join(got) == "Points: 21.5. Over."                    # was 'Points: 21.5. Points: 21.5. ...'
    assert r.blocks == [{"type": "text", "text": "Points: 21.5. Over."}]
    assert r.usage == {"input_tokens": 200, "output_tokens": 27}     # the failed attempt's tokens count too


# --------------------------------------------------------------------------------- #35 finish after fail_stale
def test_finish_after_fail_stale_never_refunds_twice():
    con = runs.connect()
    first = runs.create_run(con, "member:m_9", "nba", "q1")
    runs.claim(con)
    runs.finish(con, first, "done")                                   # an earlier answered question
    rid = runs.create_run(con, "member:m_9", "nba", "q2")
    runs.claim(con)
    assert runs.quota(con, "member:m_9")["questions_used"] == 2
    con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00Z' WHERE run_id=?", (rid,))
    assert runs.fail_stale(con, 360) == [rid]
    assert runs.quota(con, "member:m_9")["questions_used"] == 1
    assert runs.finish(con, rid, "failed", {"input_tokens": 5, "output_tokens": 1}, refund=True) is False
    assert runs.quota(con, "member:m_9")["questions_used"] == 1        # was 0: refunded a second time
    assert runs.finish(con, rid, "done") is False and runs.get_run(con, rid)["state"] == "failed"
    assert runs.append(con, rid, "delta", {"text": "late"}) is None
    assert [e["type"] for e in runs.events_after(con, rid)][-1] == "error"
    con.close()


def test_process_blocked_past_fail_stale_keeps_the_failed_state(seeded):
    con = runs.connect()
    first = runs.create_run(con, "member:m_8", "nba", "q1")
    runs.claim(con)
    runs.finish(con, first, "done")
    rid = runs.create_run(con, "member:m_8", "nba", "How did Karl-Anthony Towns do?")
    run = runs.claim(con)

    def blocked_turn(messages):                                       # the turn outlives fail_stale's cutoff
        c2 = runs.connect()
        c2.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00Z' WHERE run_id=?", (rid,))
        runs.fail_stale(c2, 360)
        c2.close()
        return [{"type": "text", "text": "He scored a lot."}]
    state = runner.process(run, llm.ScriptedLLM([blocked_turn]), now=NOW)
    assert state == "failed" and runs.get_run(con, rid)["state"] == "failed"
    types = [e["type"] for e in runs.events_after(con, rid, 0, 1000)]
    assert types.count("error") == 1 and "final" not in types and types[-1] == "error"
    assert runs.quota(con, "member:m_8")["questions_used"] == 1
    con.close()


# --------------------------------------------------------------------------------------- #36 forecasts truncated
def _add_forecasts(con, n):
    pp = con.execute("SELECT record_id FROM records WHERE kind='offer' ORDER BY record_id LIMIT 1").fetchone()[0]
    for i in range(n):
        p = 0.50 + i / 100
        store.put(con, h.forecast(offer_snapshot_ids=(pp,), line=20.5, p_over=p, p_under=1 - p, p_push=0.0,
                                  p_over_given_no_push=None, p_under_given_no_push=None, model_version=f"sharp-{i + 3}"))


def test_forecasts_not_truncated_at_exactly_limit(seeded):
    _add_forecasts(seeded, 9)                                         # + the seeded one = exactly 10
    out = tools.run(_ctx(seeded), "forecasts", {"league": "nba", "player_id": KAT})
    assert len(out["rows"]) == 10 and out["truncated"] is False


def test_forecasts_truncated_when_more_than_limit(seeded):
    _add_forecasts(seeded, 10)                                        # 11 stored
    out = tools.run(_ctx(seeded), "forecasts", {"league": "nba", "player_id": KAT})
    assert len(out["rows"]) == 10 and out["truncated"] is True
