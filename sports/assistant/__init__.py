"""Prop Grader Sports research assistant (owner brief 11): a read-only question answerer over typed Grader retrieval tools.

  config.py        settings from the environment (model id, quotas, limits); status() never shows the API key
  dates.py         explicit date routing: 'last night', 'this season', ... -> UTC ranges that are echoed back
  tools.py         typed, read-only retrieval tools; results keep historical stats / market quotes / forecasts apart
  grounding.py     every number in an answer is checked against the tool results; unsupported ones are removed
  policy.py        input limits, refused intents (wager execution, EV / arbitrage), secret redaction
  llm.py           the Claude client (Anthropic SDK, streaming tool loop) and the scripted test double
  orchestrator.py  one question -> tool loop -> grounded, cited answer events (sequence ids)
  runs.py          assistant.db: runs, ordered events (resume), cancellation, quotas, answer cache
  runner.py        the separate process that executes queued runs (never the web process)

See sports/assistant/README.md for the HTTP API, the runbook and the guarantees.
"""
