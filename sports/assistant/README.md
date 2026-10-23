# Scout Sports - research assistant and model lab (agent "assist")

Owner brief section 11. Both are behind `SPORTS_API` (routes) and stay OFF in production; the assistant has its own
switch `SPORTS_ASSISTANT` and the lab trains only with `SPORTS_TRAIN`.

## Assistant

### Processes
| Process | What it does | Never does |
|---|---|---|
| sports service (`python -m sports.service`) | `/api/sports/assistant/*` handlers: validate, refuse-intent screen, cache lookup, quota, enqueue, read stored events | call the model, read the API key, write the canonical store |
| assistant runner (`python -m sports.assistant.runner`) | claims queued runs, runs the tool loop against a read-only store connection, writes events | write the canonical store |

`assistant.db` (under `SPORTS_DATA_DIR`) holds runs, events, quotas, the answer cache and the runner heartbeat.

### HTTP (route table lines in `sports/web.py`)
| Route | Access | Request | Response |
|---|---|---|---|
| `GET /api/sports/assistant/status` | MEMBER | - | `available`, `reason` (off / starting / unavailable), today's `quota`, limits |
| `POST /api/sports/assistant/ask` | MEMBER | `{"question", "league": "nba"\|"nfl", "page_context"?}` | 202 `{run_id, poll_ms, quota}`; 200 `{run_id, cached: true}` (cache) or `{refused}`; 400 / 409 (one at a time) / 413 (too long) / 429 (daily limit) / 503 (`assistant_unavailable`) |
| `GET /api/sports/assistant/stream?run=&after=N[&format=sse]` | MEMBER (owner of the run only, else 404) | - | `{events: [{seq, type, data, at}], last_seq, done, poll_ms}`; `format=sse` gives the same events as `id:/event:/data:` |
| `POST /api/sports/assistant/cancel` | MEMBER (owner) | `{"run_id"}` | queued -> cancelled at once (question refunded); running -> runner stops at the next stream event |
| `GET /api/sports/admin/assistant` | ADMIN | - | `config.status()` (model, effort, limits, `api_key_present` bool - never key material), runner heartbeat, queue counts |

Event types: `status`, `dates` (resolved ranges, echoed), `tool_call`, `tool_result` (category, rows, ids, window, notes),
`delta` (grounded sentence text + flags), `final` (answer, citation map id -> {tool, category, as_of}, flags, dates,
tools_used, usage, model, prompt_version, removed_numbers, cached), `cancelled`, `error`.

Streaming = numbered, stored events read by polling (`after=<last seq>`): a reconnect or reload resumes exactly where it
stopped; nothing is re-generated. True push streaming (SSE held open) needs the service to support a handler that
yields chunks (generator body, chunked transfer, per-connection timeout and a cap on open streams); not required.

### Guarantees (tests: `sports/tests/test_assistant.py`)
- Typed read-only tools (`tools.py`): closed JSON schemas validated before running; unknown tools refused; results keep
  `historical_stats`, `market_quotes`, `forecasts`, `reference` separate; every number sits in a dict with a
  `record_id` / `agg_id` / `forecast_id` and `as_of`.
- Date routing (`dates.py`, rule ds-1): phrases resolved to UTC ranges over US Eastern game days (DST exact) and echoed.
- Grounding (`grounding.py`): every number / date / clock time in the answer must be in the tool results (or typed by the
  member); others are replaced by `[number removed]` and counted; forged citations are removed; citations are added.
- Injection: feed and page text are data (cleaned, citation markers neutralised, page context in an untrusted block);
  no tool can write, bet or change settings; key-like strings are redacted from every output and logged error.
- No wager execution, no EV / arbitrage: refused before any model call (no quota used).
- Quotas: per member per UTC day (questions reserved atomically at enqueue, refunded on failure; model tokens);
  one active question per member; input caps (question 1,000 chars, page context 4,000, body 64 KB at the service);
  output caps (max tokens per turn, 4,000-char answer, 6 tool rounds, 180 s per run).
- Cache: identical question + league + page context + resolved dates + store version + model + prompt version,
  10 minutes, no quota.

### Model client (`llm.py`)
Official `anthropic` SDK, imported only in the runner. Model `SPORTS_ASSIST_MODEL` (default `claude-opus-5-5`), adaptive
thinking, explicit `output_config.effort` (`SPORTS_ASSIST_EFFORT`, default medium), streaming, frozen system prompt +
tool list as the cached prefix, server-side refusal fallbacks (`fallbacks: "default"`, beta
`server-side-fallback-2026-07-01`; `SPORTS_ASSIST_FALLBACKS=0` turns them off), eager tool-input streaming with local
validation, typed SDK errors mapped to codes.

### Enable (staging) / rollback
1. `pip install anthropic` in the runner's environment (not in `requirements.txt` yet - the coordinator decides).
2. Set `ANTHROPIC_API_KEY` as a secret on the runner service only. Do not set it on the esports web service.
3. `SPORTS_API=1 SPORTS_ASSISTANT=1` on the sports service; start `python -m sports.assistant.runner` with the same
   `SPORTS_DATA_DIR`. Check `GET /api/sports/admin/assistant` -> `config.enabled`, `runner.alive`.
Rollback: `SPORTS_ASSISTANT=0` (routes answer 503 assistant_unavailable) and stop the runner. Delete `assistant.db` to
drop stored questions; no other data depends on it. Runs older than 7 days are purged automatically.

## Model lab (admin only)

| Route (`/api/sports/admin/lab/*`, ADMIN) | |
|---|---|
| `GET overview` | feature registry lf-1 (definitions, availability time, missingness, hypothesis, treatment, validated_effect = null), algorithms, targets, data sources available, model registry (read-only adapter), experiments |
| `POST experiments` | spec `{league, stat, source: ds-1\|canonical, algorithm: prior_mean\|shrunk_mean\|ridge, features[], seed, train_start, train_end, tune_end, test_end, min_prior, cutoff?}` -> idempotent id `lx-<hash>` |
| `GET experiment?id=` | spec, features + definitions, dates, train / tune / test rows / games / players, algorithm + version + seed, fitted parameters, metrics vs the prior_mean baseline on the same rows, test MAE difference with game-block bootstrap 95% CI, 80% interval coverage, calibration (offered lines; reference lines labelled), inputs / outputs hashes, replay history |
| `GET export?id=&format=csv\|json` | per-row predictions with line source and source record refs |
| `POST replay` `{id}` | queue a replay from the saved dataset |

Training: `SPORTS_TRAIN=1 python -m sports.lab.runner [--once]` (separate process; resumable from the saved dataset;
3 attempts). Every result is retrained from its saved inputs right after training; a hash mismatch makes it
`failed_replay` (not releasable). Lab outputs are `model_version lab-<id>`, `production: false`,
`replaces_production: false`; the lab never writes the canonical store, the model folder or a registry.

Model registry: the model agent's `sports.model.registry` is not in the build yet. Interface the lab reads when it lands:
`list_versions() -> [{model_version, role, artifact_version, data_version, calibration_version, registered_at, gate}]`
and optional `card(model_version)`. Until then the overview shows the declared version constants and says the API is
missing. The ds-1 adapter reads `<SPORTS_DATA_DIR>/model/history.db` (tables `games`, `nba_pg`, `nfl_pg`) read-only.

Page: `web/sports/lab.html` (served at `/sports/lab.html`); the admin token is typed into the page, kept in
sessionStorage and sent only as the `X-Admin-Token` header to `/api/sports/token`.
