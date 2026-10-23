# Scout Sports foundation API (for the model, UI, slips and assistant / lab agents)

Branch from `feature/scout-sports-nfl-nba`. Work only in your owned paths; never import a CS2 / LoL module from
`sports/` and never import `sports` from esports code (the single server hook is the only exception -
`sports/tests/test_isolation_server.py` and `test_sports_isolation.py` fail otherwise). Read `AUDIT.md` first: it says
what is and is not established about the data.

## 1. Flags and processes

| Flag (env) | Default | Gates |
|---|---|---|
| `SPORTS_UI` | off | `GET /sports` (member page; serves `web/sports.html` when it exists, else a placeholder) |
| `SPORTS_API` | off | `GET /api/sports/*` |
| `SPORTS_INGEST` | off | worker `ingest`, `loop`, `settle`, `verify`, `forecast` |
| `SPORTS_TRAIN` | off | worker `train` (register your training job there) |

On = `1/true/yes/on`. `server.py` checks `SPORTS_UI` / `SPORTS_API` itself before importing anything from `sports`.
The web process only READS the store (`store.opened(read_only=True)`); all writes happen in the worker process.

Other environment: `SPORTS_DATA_DIR` (default `<CS2_DATA_DIR>/sports`, else `sports/.data`),
`SPORTS_WORKER_CONCURRENCY` (2), `SPORTS_JOB_TIMEOUT_S` (300), `SPORTS_JOB_ATTEMPTS` (3), `SPORTS_WORKER_MAX_MB` (768),
`SPORTS_STORE_MAX_MB` (1500), `ODDS_API_KEY` (optional, not set).

## 2. Worker commands

```
python -m sports.worker migrate                          # create / upgrade canonical.db (additive)
SPORTS_INGEST=1 python -m sports.worker ingest nba nfl   # one cycle: ESPN events+rosters+injuries, PP, UD, Kalshi
SPORTS_INGEST=1 python -m sports.worker loop nba nfl --every 10 --kalshi-every 30
SPORTS_INGEST=1 python -m sports.worker settle nba nfl   # ESPN finals (re-read 4 days) -> stats -> settlements
SPORTS_INGEST=1 python -m sports.worker verify nba --date 2026-10-22   # Kalshi settled results vs ESPN box scores
SPORTS_INGEST=1 python -m sports.worker forecast nba nfl # sharp-2 market-only research forecasts
python -m sports.worker coverage [--json]                # counts + source documentation (read-only)
python -m sports.worker jobs                             # job ledger
python -m sports.legacy <sports.db>                      # import a sharp-1 log as research forecasts
python -m sports.research.audit_line_shift               # the AUDIT.md section 3 study
```
Jobs are idempotent (`job_key` = job + league + sources + time bucket; content-hashed writes) and resumable
(`JobContext.checkpoint(cursor)`; a job that died is retried from its cursor). Add a job:
`Jobs(con).run(key, "name", lambda ctx: ...)`.

## 3. Record types (`sports.contracts`, version sc-1)

All frozen dataclasses with `validate()`, `identity()` -> (source, source_key), `content_hash()`, `to_dict()`,
`contracts.from_dict()`, `contracts.json_schema(kind)`. Times are UTC strings (`contracts.utc()` normalises; naive
times are refused). Tri-state facts use `"yes" / "no" / "unknown"` - unknown is never false.

| Kind | Class | Identity | Key fields |
|---|---|---|---|
| `team` | `Team` | canonical team_id `nba:team:espn:<id>` | abbr (ESPN), aliases (dated) |
| `player` | `Player` | `nba:player:espn:<id>` | name, position, status, resolved |
| `membership` | `Membership` | player + team + valid_from | valid_to closes a stint (trades) |
| `event` | `Event` | `nba:espn:<id>` | start_utc + start_source, status, reschedules (history), season |
| `stat` | `StatObservation` | event + participant + scope + stat | value, participation (played / did_not_play / inactive / unknown), includes_overtime, final; a correction = new revision |
| `offer` | `OfferSnapshot` | platform + provider offer id | event_id / player_id (None = unresolved, never graded), stat (None = unknown label, `source_stat` verbatim), scope, line, sides (`SideQuote`: offered, price_format, price / bid / ask / multiplier), variant, promotional, availability, listed_start_utc + listed_time_kind (`start` or Kalshi `expected_expiration`), source_updated_at / observed_at / source_time_unknown, liquidity, extras |
| `context` | `ContextSnapshot` | source + kind + subject + event | status, present (tri-state), value, as_of |
| `forecast` | `Forecast` | forecast_id = `fc-` + content hash (immutable) | offer_snapshot_ids, input_snapshot_ids, versions (model / artifact / data / calibration / grade policy), projection, distribution, **unconditional** p_over / p_under / p_push (sum 1), named p_*_given_no_push, sides_available, chosen_side, grade, stake_units, reason_codes, seed, research_only |
| `settlement` | `Settlement` | platform + offer + side | status win / loss / push / void / unresolved, outcome, source_value, rule_version, revision + supersedes, affected_forecast_ids |

Revision rule for offers: a new revision only when what is offered changes (line, stat, scope, sides offered,
bid / ask / odds / modifier, variant, promo flag, availability, listed start). Book sizes, liquidity, extras and the
source's own "touched" time do not create revisions; `last_seen_at` / `seen_count` record sightings.

## 4. Storage API (`sports.store`)

```python
from sports import store
with store.opened() as con:                       # worker (read-write; migrates)
    store.put(con, rec)                           # -> {"status": inserted|duplicate|revision, "record_id", "revision"}
    store.put_many(con, recs)                     # one transaction; invalid -> quarantine
    store.quarantine(con, kind, source, key, reason, payload)
with store.opened(read_only=True) as con:         # web / UI / assistant
    store.offers(con, league="nba", event_id=None, player_id=None, stat=None, platform=None, as_of=None)
    store.events(con, league="nfl", as_of=None)
    store.history(con, player_id, "pts", before=forecast_at)      # chronology + known_at enforced
    store.forecasts(con, league=None, event_id=None, player_id=None, model_version="sharp-2")
    store.settlements(con, offer_id=None, platform=None, forecast_id=None)   # every revision, oldest first
    store.latest(con, kind, source, key, as_of=None); store.revisions(con, kind, source, key)
    store.assert_known_before(con, input_record_ids, forecast_at)  # raise LeakageError on a leak
    store.counts(con); store.quarantine_counts(con)
```
Point-in-time: pass `as_of` (or use `history(before=...)`) for every historical join; a record is usable only if its
`known_at <= forecast_at` and its event started before the forecast. Box-score parts: `pts reb ast fg3m stl blk tov min`
(basketball), `pass_yds pass_att pass_cmp pass_td pass_int rush_yds rush_att rec rec_yds targets` (football) - market
keys map to parts in `markets.MARKETS`.

## 5. Capability gate, identity, payouts, settlement

- `capabilities.gate(offer)` -> (ok, reasons): call it before forecasting, grading, slipping or displaying a grade.
  Supported: NBA points / rebounds / assists / threes; NFL pass / rush / receiving yards, rush attempts (carries),
  receptions. Combos are `research_only` (need a joint model), TD / double-double / spread / total have refusing
  handlers (`capabilities.HANDLERS`). `capabilities.compatible(a, b)` = same market (event, player, stat, scope,
  ordinary lines) - the only case where two books' prices may be compared.
- `identity`: `us_date()` (US Eastern, DST-exact), `Teams.resolve()` (exact aliases), `EventIndex.resolve(day, away,
  home)`, `Rosters.resolve(name, team_ids)`; `adapters.Resolver` wraps them.
- `payouts`: Underdog standard / flex tables and break-evens, `ud_entry_payout` (modifier product = labelled
  assumption), `straight_ev`, `kalshi_taker_fee` (unverified coefficient). PrizePicks: `PP_TABLE is None`.
- `settlement.settle_offer(offer, obs_by_stat, event, settled_at)` and `settlement.record(con, settlements)` (revision
  chaining). Platform rules in `settlement.SETTLE_RULES`.

## 6. Writing a model (model agent)

- Produce `Forecast.build(...)` records with your `model_version`, artifact / data / calibration versions and seed;
  `research_only=True` until your registered promotion gate passes. Grades and stakes stay `None` until the grade
  policy exists.
- Use ONE distribution per prop for every line and both sides; unconditional probabilities.
- Run `sports.model_checks` on your model (`check_bounds_and_sum`, `check_push`, `check_monotonic`, `check_replay`,
  `check_forecast`): add your callables to `sports/tests/test_model_properties.py` `MODELS`.
- Baselines available: `sharp2.forecast()` (market-only, Kalshi midpoint + sharp-1 line shift, corrected gates) and
  the data branch's `projection.baseline()` (history, display only).

## 7. Web (UI agent)

`sports/web.py` routes (behind the flags, members via `allowed()`, admin via token):
`/api/sports/status`, `/api/sports/offers?league=&stat=&platform=&event_id=&player_id=&limit<=500`
(typed offers + `gradable` + `not_gradable_because`), `/api/sports/admin/coverage`. Add routes in `web.handle` only;
answers are cached 30 s in-process (256 entries max). Put the page at `web/sports.html` with scoped CSS.

## 8. Coverage

`coverage.report(con)` / `/api/sports/admin/coverage` / `python -m sports.worker coverage`: per platform and league -
offers, capability status, gradable now, variants, scopes, promotional, availability, event / player resolution,
side availability, unknown stat labels, quarantine reasons; plus `coverage.SOURCES` (fields, leagues, depth, cadence,
access, usage terms, failure behaviour, rate limits) and `coverage.verify_kalshi_vs_espn(lg, date)`.
