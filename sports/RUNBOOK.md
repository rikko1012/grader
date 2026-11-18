> Integration correction: use the EXISTING 5 GB disk ($8.25/month base), not the historical 10 GB sizing proposal below. No enlargement is approved. SPORTS_STORE_MAX_MB=4000 counts the entire data directory; SPORTS_WORKER_MAX_MB=310 is RSS and SPORTS_WORKER_MAX_VM_MB=320 is virtual memory for measured ingestion/settlement jobs. Keep SPORTS_TRAIN=0 until model-job virtual memory is measured. See RETENTION.md for the short measured runway; proposed larger sizes below are planning scenarios only.

> **Current Render resources (as recorded 2026-11-04; the Render dashboard is the source of truth, check it before
> changing a cap):** `scout-sports-api` web service, plan `Standard` (2 GB RAM / 1 CPU, since 2026-10-25), Docker
> `./Dockerfile.sports`, one persistent disk `scout-sports-data` **5 GB** at `/sports-data` (the worker children run
> inside this one service, section 10); the esports service `cs2-scout` keeps its own disk `/data`, and the HLTV
> database is the separate `hltv-db` Postgres (render-db-storage note: 2.9 GB after the 2026-10-16 archive move).
> Any "100 GB disk" or "Starter" figure in an older audit or in sections 1-13 below describes a measurement or a
> proposal, not the current resources.

# Scout Sports - operational runbook

(Sections 1-2 were committed BEFORE any load run; the results and the rest of the runbook follow below.)

## 1. Load-test budgets and stop conditions (written before the run, 2026-10-23)

Problem: the foundation measured sports in the esports web process at **+51% esports p99** under 700 members
(budget then +25%). Target now: **esports p99 within +10%** of the real baseline with sports enabled and ingesting.

Harness (`scripts/load_sports.py`): three separate OS processes - esports server (fixture upstreams, as the foundation
script), sports service (`python -m sports.service`, real scratch store from the 2026-10-23 ingestion: ~28k offers),
load generator - plus an ingestion writer process for the "ingesting" scenario. 700 simultaneous members; each member
makes the same 3 esports requests as the foundation test; with sports on it also makes its sports traffic for the mode:
`service` = 1 token request to esports + 1 sports request straight to the service; `proxy` = 1 sports request through
esports; `inprocess` = 1 sports request answered inside esports (reference only).

Primary metric: p99 of the **esports requests only** (the same 2,100 requests in every run). Token requests are
reported separately and also as "esports incl. token".

Budgets (per scenario, median over 5 runs, baseline = `off` on this code in the same session):
1. esports p99 <= baseline p99 x 1.10 and esports p95 <= baseline p95 x 1.10;
2. esports 5xx = 0 in every run;
3. esports peak RSS <= baseline peak + 10 MB;
4. sports service peak RSS <= 300 MB; db pool in use <= SPORTS_DB_POOL (4) by construction - recorded;
5. sports errors: enabled / ingesting <= 1% non-200 (busy sheds count); failing = fast 503 / refused (< 1 s p99), no
   esports effect; recovering = sports 200s resume after the service is back, without an esports restart.

Stop conditions: stop the series and investigate if any run has an esports 5xx twice in a row, esports RSS > 600 MB, a
server process dies, or the load generator needs connect retries on > 5% of requests (port exhaustion on the Windows
workstation: cool down 60 s and re-run; such a run is reported, not counted).

Decision rule (registered now): choose the option whose median esports p99 passes budget 1; if both pass and are
within 3% of each other choose `service` (separate Render container = separate CPU and memory in production; the proxy
keeps esports threads waiting on sports I/O); otherwise the lower median. If neither passes, sports stays off and the
report says so.

Limits of the test: one Windows workstation (16 logical CPUs) - all processes share the CPU, unlike production where
the sports service is its own Render container (so the local numbers are conservative for `service`); fixture esports
upstreams; synthetic ingestion writer (store revisions at a fixed rate), not the network ingestion. Nothing here
measures more than 700 members.

## 2. Scenarios

| Scenario | What runs |
|---|---|
| off | flags off - the baseline (and the rollback state) |
| inprocess-enabled | SPORTS_API_MODE=inprocess (foundation path, reference) |
| service-enabled / proxy-enabled | sports service up, store ready |
| service-ingesting / proxy-ingesting | + writer process committing offer revisions continuously |
| service-failing / proxy-failing | sports service DOWN (process not running) and, separately, a corrupt store (every query raises) |
| service-recovering / proxy-recovering | burst 1 with the service down, service started, burst 2 immediately after |

## 3. Architecture

**Chosen: option (a) - the sports API is its own process / Render web service (`scout-sports-api`); the esports
server only issues signed member tokens.** `SPORTS_API_MODE=service` (default).

```
 member browser (esports origin, Whop cookie)
   |  GET /sports, /sports/<asset>       -> cs2-scout: static bytes from memory (no data)
   |  GET /api/sports/token              -> cs2-scout: Handler.allowed() (paid Whop check, server side) -> st1 token
   |                                        (sports/gateway.py + sports/authtoken.py: standard library only)
   v
 GET https://scout-sports-api/api/sports/...   Authorization: Bearer st1...   (CORS: our origin only)
   -> scout-sports-api (sports/service.py): verify token on EVERY route -> web.ROUTES handler -> prebuilt snapshot /
      bounded read-only store pool (4) / single-flight cache -> JSON (gzip + ETag/304)
   -> children of the same service (one container, one disk /sports-data): ingest loop, snapshot prebuild, UI
      snapshots, slip spool ingest / settle, history ingestion, assistant runner, lab runner - each behind its flag
```

Options evaluated (measured with `scripts/load_sports.py`, section 11):

| Option | What runs in esports request threads | Result | Verdict |
|---|---|---|---|
| (c) in-process (foundation) | every sports query | series 1: esports p99 x1.64, RSS +39 MB | rejected (foundation +51% reproduced in kind) |
| (b) esports proxies `/api/sports/*` to a private sports service (bounded pool 16, 200 ms slot wait, 2.5 s budget, circuit breaker) | the whole sports request, waiting on I/O | series 1: esports p99 x1.79, RSS +10 MB, 10.7% of sports requests shed at the pool limit | rejected: it keeps esports sockets / threads busy for every sports call; raising the pool would trade sheds for more esports load |
| (a) separate service + st1 tokens | ONE cheap token mint per member per 10 min | series 1: p99 x1.09 / p95 x1.14 (p95 budget missed); series 3 on the final code: section 11 | chosen |

Why (a): it is the only option whose esports-side work is bounded and independent of sports traffic (token
minting = one HMAC per member per token life; the page and assets are static bytes). In production the sports
service has its own container, CPU and memory, so sports query load, ingestion, snapshot builds and the assistant /
lab never share the esports CPU at all - the local test (one workstation, shared CPU) overstates (a)'s cost; the
`service-notoken` diagnostic measures exactly that host-sharing share.

Why the worker is NOT a separate Render background worker: a Render persistent disk attaches to exactly one service,
and the API must read the store / snapshots / spools the worker writes. The worker therefore runs as supervised
child processes of `scout-sports-api` (own memory caps, own process, never in a request thread). If ingestion load
ever hurts the sports API itself, the upgrade path is a separate worker service writing to Postgres (or pushing
snapshots + spools over HTTP) - not a shared disk.

## 4. Member auth across the boundary (st1 tokens, `sports/authtoken.py`)

- **Issuer = the esports server only.** `GET /api/sports/token` (esports origin, Whop session cookie) runs the
  existing server-side `Handler.allowed()` (paid Whop member; load-test header; owner admin token) and only then mints
  a token. Refusals: 401 `members_only` (no paid session), 403 `cross_site` (`Sec-Fetch-Site: cross-site`), 503
  `sports_auth_not_configured` (no secret). Response `{token, exp, ttl_s, api_base}`, `Cache-Control: no-store`.
- **Token** `st1.<claims>.<HMAC-SHA256>`: `aud=scout-sports-api`, `sub` = pseudonymous member id
  (`m_` + HMAC(secret, Whop user)[:20] - the Whop id never reaches the sports service), `role` member / admin (admin
  only when the esports admin token authenticated the request), `ent=paid`, `iat`, `exp` (10 min default, hard cap
  15 min), `jti`. Everything the service relies on is inside the MAC; a client cannot raise its role or lifetime.
- **Verifier = the sports service, on every route** (`web.dispatch`): no token / bad MAC / expired / wrong audience
  -> 401 `members_only`; admin route with a member token -> 403. There is no public data route; the only open path is
  `/api/sports/health` (`{ok, version, store_ready}`, no data). Protected data is never sent to be hidden client-side.
- **Browser flow:** the page (served by esports at `/sports`) fetches the token, then calls
  `<api_base>/api/sports/...` with `Authorization: Bearer <token>`; it refreshes the token before `exp` (and on a
  401). CORS: `SPORTS_ALLOWED_ORIGINS` only; any other `Origin` is refused 403 before work (preflight cached 600 s).
  Bearer tokens, not cookies, cross the origin, so there is no CSRF surface on the sports service.
- **Secret:** `SPORTS_TOKEN_SECRET` (>= 32 chars) on both services. Rotation without downtime: set the new value as
  `SPORTS_TOKEN_SECRET` and the old one as `SPORTS_TOKEN_SECRET_PREV` on scout-sports-api, then the new value on
  cs2-scout; remove `_PREV` after 15 minutes. Missing / short secret = nothing is minted or accepted (fails closed).
- **Revocation:** a member whose Whop access ends keeps sports access only until the current token expires (<= 10 min
  after esports stops minting); esports itself re-checks Whop every 6 h (existing rule).
- Tests: `sports/tests/test_service.py` (tamper, role / TTL escalation, wrong secret, expiry, skew, rotation, no
  secret, every route refused without a role, admin vs member, CORS, esports cookies ignored by the service, the proxy
  never forwards the esports admin token) and `test_isolation_server.py` (esports end to end in a child process).

## 5. Resource limits (all environment-tunable; Starter values from deploy/render.sports-api.yaml)

| Where | Limit | Starter value (code default) | On breach |
|---|---|---|---|
| sports service | concurrent requests `SPORTS_SERVICE_THREADS` | 16 | wait `SPORTS_SERVICE_QUEUE_MS` 200 ms, then canned 503 `sports_busy` |
| sports service | per-query / single-flight wait `SPORTS_REQUEST_BUDGET_S` | 4 s | SQLite progress handler interrupts -> 503 `sports_busy` |
| sports service | read-only store connections `SPORTS_DB_POOL` | 2 (4) | wait `SPORTS_DB_WAIT_S` 1 s -> 503 `sports_busy` |
| sports service | answer cache `SPORTS_CACHE_MAX` | 64 entries (256), 30 s TTL, single-flight | oldest quarter evicted |
| sports service | UI board file cache `SPORTS_BOARD_CACHE_MB` | 16 MB (48) | oldest third evicted |
| sports service | gzip copies `SPORTS_GZIP_CACHE_MB` | 12 MB | oldest evicted |
| sports service | archive file cache `SPORTS_ARCHIVE_CACHE_MB` | 32 MB | LRU |
| sports service | prebuilt keys in memory `SPORTS_PREBUILT_KEYS_IN_MEMORY` | 8 | LRU |
| sports service | POST body `SPORTS_BODY_MAX` | 64 KB | 413, unread |
| sports service | RSS `SPORTS_SERVICE_MAX_MB` | 160 MB (measured ~34 MB idle) | stop accepting, drain 20 s, exit 3 (Render restarts) |
| job children | memory reservation `SPORTS_CHILD_BUDGET_MB` | 310 MB | the job waits in the queue (section 5a) |
| job children | per-job RSS cap `SPORTS_JOB_<NAME>_CAP_MB` | section 5a | child killed (`killed_over_cap`), retried with backoff, `job_over_cap` page after 2 in a row |
| job children | per-job timeout | section 5a | child killed (`timeout`) |
| worker child | address space `SPORTS_WORKER_MAX_VM_MB` | 320 MB | Measured ingest/settlement only; model jobs stay off pending measurement |
| worker child | entire data directory `SPORTS_STORE_MAX_MB` | 4,000 MB | ingestion pauses itself (`store_full_ingest_paused`) |
| esports gateway (proxy mode only) | in-flight / slot wait / timeout | 16 / 200 ms / 2.5 s | 503 `sports_busy` / `sports_unavailable` |
| esports gateway (proxy mode only) | circuit breaker | 5 consecutive failures -> 5 s open | immediate 503 |

### 5a. Worker-side jobs (`sports/scheduler.py`: ONE scheduler thread, every job a child process of the service)

Admission (memory-reservation queue): a job starts only when the caps of the running children plus its own cap fit
`SPORTS_CHILD_BUDGET_MB` (310); at most ONE heavy child at a time; an **exclusive** job waits until nothing else runs
and nothing else starts while it runs (so ingestion is paused during forecasting / training); priority with aging
(a job that keeps losing moves up, `SPORTS_JOB_AGING_S` 120 s per level); a job blocked only by the heavy slot lets
smaller jobs that fit start, a waiting exclusive job holds everything behind it. Every child: nice 10 /
BELOW_NORMAL, `oom_score_adj` 900 (the kernel kills a child before the service), single-thread BLAS,
`MALLOC_ARENA_MAX=2`; an RSS watchdog (0.25 s) kills it above its cap.

**Plan-aware sizing (2026-10-25, Standard 2 GB / 1 CPU).** The plan size is `SPORTS_MEMORY_MB`, else the container's
cgroup memory limit, else 512 (ops `scheduler.memory_mb` / `memory_source` show which). From it:
reservation total = 75% of the plan minus the service's ~70 MB (512 -> 314, 2048 -> 1466; `SPORTS_CHILD_BUDGET_MB`
overrides); every cap in the table below is the Starter-measured cap x min(plan / 512, 7/3) (2 GB: board builds
300 -> 700, collectors 300 -> 700, model jobs 340 -> 793; `SPORTS_JOB_<NAME>_CAP_MB` still overrides); heavy children at
once = 2 from 1.5 GB (`SPORTS_MAX_HEAVY` overrides), and two run together only when both declare their store write
locks, share none and are not one league's collector beside the same league's board build - in practice one
league's board build beside the other league's collector (at 2 GB the collectors are heavy, not exclusive). Model /
lab jobs stay exclusive. Jobs without declared locks still run one heavy at a time. Since 2026-10-31 sheets, settle,
player-context and history declare theirs (they waited ~50 min for an empty heavy slot): settle and history share
locks with the collectors so they never run beside one, and the slip pool goes first when 5 min overdue like the board
builds; a league collector goes first at 15 min overdue (5 min stopped settle and forecasts in replay). Since the 2026-11-08 review mapping-audit (read-only, own file), archive and archive-verify (canonical.db writers: lock `canonical` keeps them off every store writer, `archive` off each other) declare theirs too: a lockless heavy job needs BOTH slots empty, and a freed slot is always backfilled first, so mapping-audit sat 4 h unstarted. settle-post and results-public share `season-results` (the two full-season builds never hold both slots). Only the SPORTS_TRAIN jobs model-learn / model-snapshot stay lockless. On 1 CPU a pair slows both jobs; a job slot is only worth having if the timeouts still hold (sheets 420 s).

**Books job (2026-11-06, owner: "pull FanDuel and BetRivers every 5-10 min, every 2-3 min in the 2 hours before games, so they stay under the 30-min stale cutoff").** Inside the ingest cycle the books were read every 36-60 min (the cycle waits behind settle, which shares its locks and ran ~20 min), so no NBA / NFL row had a market chance (15-min rule) and the Tools +EV sheet dropped ~1,000-2,000 quotes as older than 30 min. The freshness rules stay; the books now run as the light `books` job (no heavy slot, no shared lock: its writes are short per-game transactions) on the direct ladder. Politeness: one request per tab / game at 1 req/s per host (net.py), no retry inside a call, a 429 / 5xx cools the provider down 60 s doubling to 30 min, a 401 / 403 for 6 h, Retry-After obeyed in full - and the minute check sends nothing while a provider cools down.

**Sports jobs fix (2026-11-08).** Live ops at 13.8 h uptime: sheets killed over its cap 13 times (361 > 360 MB, in the
results step), settle timed out 5 times in a row at 1,500 s (4 h backoff: settlement every ~4.5 h), model-forward
exited 1 four times. Causes and fixes (no grade, chance, stake, side, projection or settlement change; equality tests in
`sports/tests/test_sports_jobs_1108.py`):
- *results (public Results file)*: one ~1.5 KB dict per forecast revision of the season, both leagues alive at once, and
  a bootstrap that copied every sampled row per draw. Now the rows are reduced as they stream (newest per line key), one
  league at a time, the bootstrap works from per-game values (same draws, same sums - bit-identical), archived offer
  identities come from the metadata view (no archive file decode), and it is its own `results-public` job (sheets runs
  with `--skip-results`). The file is identical apart from `built_at`.
- *review follow-up (same day)*: the in-memory reduction still kept one row per line key of the season (~0.6 MB per
  game; a full NBA season ~740 MB against the 360 MB cap) and the settle-post ledger (`snapshots.write_results`) still
  held every ledger row (~2 MB per game). Both now spill to a throwaway SQLite file in `<DATA_DIR>/tmp`
  (`sports/spill.py`: no journal, 2 MB page cache, deleted at the end, a killed run's file swept after 6 h): rows go to
  disk as they stream, the newest-per-key choices and every ordering run in SQL with the list code's tie rules (the
  sorts carry keys only), and the summaries are fed one decoded row at a time (per-game numbers + the means' value
  lists; the ledger's means are `sum()` over an ordered SQL read). Both files are identical to ecfa81a's (frozen
  copies in `sports/tests/ref_*_ecfa81a.py`). Process peak on synthetic seasons of 9 / 27 / 81 games: Results 64 / 65 /
  66 MB (ecfa81a 65 / 91 / 182), ledger 69 / 71 / 76 MB (ecfa81a 71 / 118 / 270); the store read alone peaks ~60 MB.
  settle-post now runs the scorecards (resumable per event) BEFORE the ledger, so a killed ledger cannot starve them.
- *settle*: settling took ~64 s; the results ledger + model scorecard after it ~1,000 s and growing (every event of the
  last 30 days re-decoded each run; the shadow scorecard's mark was only saved at the end, so a killed run started over).
  `settle --no-post` now settles only and starts `settle-post`; the scorecards skip unchanged events (index-only
  signature: settlement + forecast record count / newest rid / rid sum; shadow adds its forecast rows) and commit
  per-event progress. Scorecard rows are identical to the full re-read.
- *model-forward*: its offer loop kept a streaming read cursor open across `store.put`; once the books job
  (beside_exclusive since 2026-11-06) committed, the stale WAL snapshot could not become a write transaction
  (SQLITE_BUSY_SNAPSHOT, "database is locked", no busy wait) and the run exited 1. The offers are now read into a list
  first (same rows, same order).
Check after deploy: ops `scheduler.jobs` settle / settle-post / sheets / results-public / model-forward `last_ok_at` set
and `failures_in_a_row` 0; `results_stages.last_ok`; `settle_post_stages` - the ledger's production run time was never
measured on its own (only "ledger + scorecards ~1,000 s" before the split), so read the first runs' `model_learning` /
`results` stage seconds against the 1,500 s timeout (2,400 s until the stall fix below) and the 900 s cadence; settle-post `last_exit.peak_rss_mb` against
its 270 MB cap; mapping-audit / archive `last_start` recent (they were starved of a heavy slot).

**Bounded waits (scheduler stall 2026-11-09, after c31637d).** At 72 min uptime model-forward (exclusive) had been
queued 71 min with 0 starts, settle 0 starts, and picks-ingest / slips-ingest / picks-settle / pregame-news had not run
for ~60 min (member picks not recorded or settled), while board builds, collectors, prebuilt and books kept running.
Replay (`python scripts/sim_sports_scheduler.py --ref c31637d --ref ecfa81a`, production job table, live run times,
`sports/tests/sched_sim.py`): on Standard the two heavy slots are oversubscribed by member-facing jobs that are always
urgent (board builds every 60 s at 140-220 s, prebuilt every 45 s at ~60 s, collectors every 3-5 min at 290-360 s -
~3 slots of demand). Urgent jobs passed a waiting exclusive job, so the system never drained; the waiting exclusive
job held every later start, LIGHT jobs included; a non-urgent heavy job (settle) never got a slot. The rule predates
c31637d (ecfa81a replays the same first hour); there model-forward exited 1 every run and sat in its 1-8 h failure
backoff, so most of the time nothing waited. c31637d fixed that exit 1 - and the boot made it due at once.
Rules now (scheduling only; no grade, chance, stake, projection or settlement change; `sports/tests/test_sports_stall_1108.py`):
- LIGHT jobs run beside a waiting or running exclusive job whenever the reservation total still holds the exclusive job
  (like books); the exclusive job never waits for them.
- An exclusive job that has waited `SPORTS_JOB_EXCL_DRAIN_S` (600 s) stops every other non-light start, urgent ones
  included; the running jobs finish and it starts (model-forward every ~42 min in the replay, longest wait ~15 min).
- Fill-in (review 2026-11-09): while an exclusive job waits - and past an urgent job held on a store lock - a heavy
  job may still take an idle heavy slot when the LONGEST of its last 3 runs ends before the SHORTEST recent run of the
  jobs that are waited for (minus their time so far; a job with no recorded run counts its timeout). It ends first,
  so the waiting job starts no later; if an estimate is wrong the delay is at most that one short run. Never before
  a job's first recorded run. Run times persist in `DATA_DIR/scheduler_runs.json` (heavy / exclusive jobs, written
  at each exit; unreadable = learn again), so a deploy starts warm. Before this, the drain held every board build
  for ~5 min per model-forward run with one heavy slot empty while a collector finished, and an urgent collector
  waiting on the archive's canonical lock held the free slot for the archive's whole run (c31637d too).
- settle is urgent after 15 min overdue on Standard; any other non-exclusive job overdue past `SPORTS_JOB_STARVE_S`
  (3,600 s) counts as urgent and ages from then on, so it takes a turn between the board builds.
- settle-post never starts while settle is due (`yields_to`), and `--budget-s` (`SPORTS_SETTLE_POST_BUDGET_S`, 600 s)
  stops its scorecards between events (per-event marks keep the progress; shadow's closing mark is not advanced), then
  the ledger runs; timeout 1,500 s. Its first production run held model-daily - and so settle - for the whole 2,400 s
  kill; that kill lost no scorecard work (marks commit per event) but the ledger never ran.
Trade-off (replay over the FULL 6 h incl. the boot / after-deploy hour, median / worst of the longest start-to-start
gap over 8 jitter seeds, `python scripts/sim_sports_scheduler.py --ref c31637d --seeds 8`; "long" = settle-post
900 s, grades-daily 1,200 s, archive 1,500 s; cold = first deploy with no run-times file):

| job | c31637d | this fix, cold | this fix, warm | c31637d long | this fix long (cold) |
|---|---|---|---|---|---|
| ui-snapshots-nba (board) | 19.4 / 25.8 | 21.7 / 28.0 | 20.2 / 22.2 | 35.8 / 38.3 | 27.4 / 29.8 |
| ui-snapshots-nfl (board) | 19.2 / 21.8 | 20.9 / 24.3 | 23.3 / 26.5 | 30.4 / 37.4 | 27.2 / 28.7 |
| prebuilt | 17.0 / 17.2 | 14.3 / 15.3 | 14.0 / 17.5 | 30.4 / 34.7 | 22.2 / 23.3 |
| ingest-nba (line collector) | 26.8 / 27.1 | **36.7 / 39.2** | 35.9 / 40.3 | 36.8 / 38.3 | **56.7 / 58.6** |
| ingest-nfl (line collector) | 26.8 / 27.1 | 26.9 / 29.5 | 30.2 / 32.8 | 36.8 / 38.3 | **51.5 / 54.1** |
| model-forward longest wait | 75.2 (4.8 runs) | 15.2 (8.8 runs) | 15.6 | 92.7 (4 runs) | 29.0 (8 runs) |
| idle heavy slot a due board build fitted (min / 6 h) | 22.1 | 0 | 0 | 27.6 | 0 |

(eeb392a, the stall fix before the fill-in: boards 36.9 / 35.5, prebuilt 31.4, long boards 52.8 - the review's
finding.) The boards and prebuilt are back at c31637d or better; the collectors are not: they share the canonical
lock with settle, history, archive and archive-verify, which now actually run (c31637d: settle 0 starts in the first
72 min, history / archive starved), and a collector (290-360 s) is too long to fill in, so each model-forward run
costs it the drain tail + ~5 min. With `SPORTS_JOB_EXCL_DRAIN_S=0` the NBA collector is 30.1 (long 50.0) but
model-forward waits up to ~116 min (long ~200). Raise `SPORTS_JOB_EXCL_DRAIN_S` to favour the collectors; 0
restores the old exclusive hold (lights stay unblocked).
Check after deploy: ops `scheduler.jobs` picks-ingest `last_start` within 2 min; model-forward `starts` rising every
30-45 min; settle `starts` every 15-40 min; no `job_starved` alert for a light job; heavy jobs' `recent_ran_s`
filling in (and `DATA_DIR/scheduler_runs.json` present after the first heavy exit); ingest-nba / ingest-nfl
`last_start` gaps against the table above.

**Standard cadences (code defaults when the plan is >= 1.5 GB, 2026-10-25; Starter in brackets):** collector per
league 3 / 5 / 10 min near tip / game day / otherwise [5 / 10 / 15]; Kalshi 10 / 30 min [20 / 60]; board build per
league every 60 s and right after each collector cycle [180 s]; slip pool 45 s [90 s]; settle, settle-post (results
ledger + scorecards, scorecards bounded by --budget-s 600 s), picks-settle, slips-settle every 15 min [60 min]; results-public 30 min. Model-forward stays 30 min (exclusive: on 1 CPU a 15-min cycle
would pause every other job ~20% of the time). The board build runs beside the OTHER league's collector, so pages keep
updating while lines are collected.

**Render env on scout-sports-api (Standard).** Coherent with the values saved from the cloud runbook:
`SPORTS_MEMORY_MB=2048` (set it; the cgroup read is the fallback), `SPORTS_CHILD_BUDGET_MB=1200` (kept: it IS the
reservation total; caps then scale x1.8 so a board build + collector pair, 540 + 540 MB, plus 120 MB of light jobs
fit it), `SPORTS_HEAVY_SLOTS=2` (kept: read as SPORTS_MAX_HEAVY), `SPORTS_SERVICE_MAX_MB=400` (kept: the service's
own drain-and-restart ceiling; 400 + 1200 = 1600 of 2048 MB). DELETE any `SPORTS_JOB_<NAME>_EVERY_S` left from Starter
(`SPORTS_JOB_INGEST_EVERY_S`, `SPORTS_JOB_INGEST_KALSHI_EVERY_S`, `SPORTS_JOB_UI_SNAPSHOTS_EVERY_S`,
`SPORTS_JOB_PREBUILT_EVERY_S`): an explicit interval pins that job and switches its game-day cadence off.

**Security headers (2026-11-04, both services, all off by default; owner switches them on).** `SCOUT_CSP` unset / `0`
= no Content-Security-Policy header (today's behaviour); `report` = `Content-Security-Policy-Report-Only` (switch this on
first and read the browser console / `SCOUT_CSP_REPORT_URI` for a week); `1` = enforced. `SCOUT_FRAME_ANCESTORS`
(space / comma list, default `'self' https://whop.com https://*.whop.com`) is the `frame-ancestors` directive on both
services - Whop keeps framing the member pages, and no X-Frame-Options header is ever sent. The esports default policy
keeps the pages' inline scripts / styles, Google Fonts, ESPN images and, in service mode, `SPORTS_API_BASE` in
`connect-src` (`SCOUT_CSP_CONNECT` adds origins; `SCOUT_CSP_POLICY` replaces the whole policy, used as written); the
sports service default is the strict API shape (`default-src 'none'`). `SCOUT_HSTS=1` adds `Strict-Transport-Security`
(`SCOUT_HSTS_MAX_AGE`, default 31536000): Render terminates TLS at its edge and does not add HSTS for the app. The
switches are read per response (no deploy to change them); bodies never change. Display switch of the same release:
`SPORTS_MARKET_FALLBACK=1` lets the snapshot build write `mk.fb` (the books' own stale readings for the Market column
only; never a lean / edge / sort input). Tests: tests/test_csp_headers.py, sports/tests/test_csp_headers.py,
sports/tests/test_market_fallback.py.

Peak RSS measured 2026-10-24 on a copy of the real data (each job alone, `scripts/load_sports_jobs.py`; results in
`sports/research/results/starter_2026-10-24/`). Memory of the store-reading jobs grows with the data (see 13.4).

| Job | Command | Every (Starter) | Class | Cap MB | Measured peak MB | Flag |
|---|---|---|---|---|---|---|
| assistant | `sports.assistant.runner --once` | on demand (a queued question, checked every 2 s) | light | 120 | not measurable here (no SDK / key) | SPORTS_ASSISTANT |
| prebuilt | `sports.service snapshots` (slip pool) | 90 s | heavy | 160 | 105-109 before forecasts grew; killed at 290-301 on 2026-11-18. Now reads only forecasts for the platform's current offer revisions (synthetic 115k-forecast store: 247 -> 60) | SPORTS_API |
| ui-snapshots-nba / ui-snapshots-nfl | `sports.snapshots build <league> --no-results` (one process per league; slate games only, 2026-10-24) | 180 s AND right after every collector cycle of that league | heavy | 300 | live read 2026-10-25: NBA 90, NFL 137 (both in one process 152; prod both 251-265 = killed at 250). Stage RSS in manifest build_rss_mb, seconds in build_stage_s + service log "snapshots stage". Timeout 420 s on Standard. Overdue 300 s = urgent: goes first and later jobs may not take its slot (2026-10-31) | SPORTS_INGEST |
| slips-ingest | `sports.slips ingest` | 5 min | light | 60 | 23 | SPORTS_INGEST |
| books | `sports.worker books nba nfl` (FanDuel / BetRivers own feeds + OddsPapi when its guard allows; 2026-11-06 book-cadence). Each run first checks books.db (`books.worker.pending`, no request) and exits in ~1 s unless a game list, a game on the **direct** ladder (3 / 5 / 8 / 8 min inside 2 / 8 / 24 / 72 h of the start, then 12 h inside 7 days; the 24-72 h rung was 3 h until the 2026-11-06 site audit N1) or a blocked-book probe is due; then the ESPN resolver (shared caches) and one `run_once` pass. Store cap / `SPORTS_DISK_FREE_FLOOR_MB` checked before any write (exit 3 when paused). `SPORTS_BOOKS_JOB=0` = rollback (books back inside ingest-<league>) | every 60 s when a league is hot or has a game within 72 h (SPORTS_BOOKS_GAMEDAY_H, site audit N1), 180 s otherwise (SPORTS_BOOKS_HOT_S / _GAMEDAY_S / _IDLE_S) | light | 60 | 46 local, full 17-game NBA slate at real volume (28.6k side quotes, fake feeds); due check 35 MB / 0.6 s | SPORTS_INGEST |
| ingest-nba / ingest-nfl | `sports.worker ingest <league> --bucket-min 1 --sources espn,pp,ud,injuries,oddsapi,polymarket,games` (one job per league; `books` only with SPORTS_BOOKS_JOB=0) | GAME-DAY cadence per league: 5 min from 2 h before its earliest upcoming tip, 10 min on a game day (tip within 18 h or a game under way), 15 min otherwise (SPORTS_INGEST_HOT_S / _GAMEDAY_S / _IDLE_S / _HOT_H / _GAMEDAY_H) | exclusive | 300 | prod 191-197 both leagues together | SPORTS_INGEST |
| ingest-kalshi | `sports.worker ingest nba nfl --bucket-min 1 --sources kalshi` | 20 min in a league's 2 h pre-tip window, else 60 min (SPORTS_KALSHI_HOT_S / _IDLE_S) | heavy | 240 | prod 70-84 | SPORTS_INGEST |
| settle | `sports.worker settle nba nfl --no-post` (+ catch-up of unsettled games since SPORTS_SETTLE_FLOOR, 40 per run); settling only since 2026-11-08 - starts `settle-post` when it exits 0 | 60 min | heavy | 120 | 58 | SPORTS_INGEST |
| settle-post | `sports.worker settle-post nba nfl --budget-s 600`: the model + shadow scorecards (stopped between events after the budget, resumed next run; never started while settle is due - stall 2026-11-09) (learning.on_settled / shadow.settle), then results.json per league (streamed per event through a spill file, sports/spill.py). Reads the store only; an event whose settlement / forecast records are unchanged since its last full pass is skipped without decoding, and each event's mark commits with its rows (a killed run resumes). Locks model-daily (never beside settle or grades-daily) and season-results (never beside results-public). Stages: ops `settle_post_stages` | 60 min and right after each settle | heavy | 150 (x1.8 = 270 MB on Standard; was 340 = 612 MB, review 2026-11-08: no board build fit beside it and a light job) | synthetic season (180k forecasts, 116 games, 2026-11-08): scorecards 56 MB / 38 s first pass, 0.5 s once events are marked (old: 25 s every run); ledger before the spill 302 MB, after: process peak flat with the season (69 / 71 / 76 MB at 9 / 27 / 81 games, ecfa81a 71 / 118 / 270) | SPORTS_INGEST |
| sheets | `sports.sheets build nba nfl --skip-results` (pick sheets + "without" files; ops `sheets_stages`) | 5 min | heavy | 200 | 52 (sheets steps, 2026-11-08 prod) | SPORTS_INGEST |
| results-public | `sports.sheets build nba nfl --only results --force` (public Results page file `results_public.json`; own job since 2026-11-08, ops `results_stages`; lock season-results: never beside settle-post) | 30 min | heavy | 200 | process peak flat with the season since the spill (64 / 65 / 66 MB at 9 / 27 / 81 synthetic games; ecfa81a 65 / 91 / 182; prod old in-sheets path 361-365 MB, ~230 s). Spill file ~0.8 MB per game on disk while it runs | SPORTS_INGEST |
| slips-settle | `sports.slips settle` | 60 min | light | 60 | 23 | SPORTS_INGEST |
| pregame-news | `sports.pregame_news run nba nfl` (pgn-1 research archive: ESPN injury report + depth charts through the shared caches, game summaries of games starting within 2 h, max 20 per league; change-only rows in `pregame_news.db`; PREGAME_NEWS=0 turns it off) | 15 min | light | 80 | 38 local (30 depth teams, 15 games, 3 passes in 0.4 s) | SPORTS_INGEST |
| grades-daily | `sports.model.daily run` (REGISTER A9a: cal-mi fit -> gradecheck -> containment; exits at once unless due: once per game-day, after 10:00 UTC, only when something new settled) | 30 min check | heavy | 300 | 72-96 synthetic (3.5k-10k props, 6-131 s) | SPORTS_INGEST |
| model-forecast | `sports.worker train forecast nba nfl` (incremental: unchanged runs add 0 records) | 60 min | exclusive | 340 | 272-311 | SPORTS_TRAIN |
| model-learn | `sports.worker train learn` | 60 min | heavy | 250 | 78-218 | SPORTS_TRAIN |
| model-snapshot | `sports.worker train snapshot` | 30 min | heavy | 250 | 77-217 | SPORTS_TRAIN |
| model-fit | `sports.worker train fit` | 7 days | exclusive | 340 | 306-309 | SPORTS_TRAIN |
| lab | `sports.lab.runner --once` | 15 min | exclusive | 340 | 34 idle (training up to the fit size) | SPORTS_TRAIN |
| history | `sports.snapshots history nba nfl --max-players 400` | daily | heavy | 200 | 107 (40 players) | SPORTS_INGEST |
| archive | `sports.archive pass --time-budget-s 540 --deadline-s 780` (13.3; locks canonical + archive since 2026-11-08; ends itself at the deadline with exit 0, job timeout 1800 s is the backstop only; stage timings in `archive/last_pass.json` and ops `archive.last_pass`) | 6 h | heavy | 200 | 140 (80k rows) | SPORTS_INGEST |
| archive-verify | `sports.archive verify` (locks canonical + archive) | daily | heavy | 120 | - | SPORTS_INGEST |
| player-context | `sports.player_context build nba nfl` (factor-engine context for the player page; ui-snapshots embeds the result per player) | 30 min | heavy | 260 | 170 (1,724 games, 13 s) | SPORTS_INGEST |
| games-backfill | `sports.games.backfill_job --budget 120` (game store: current + previous season, resumable; ~2 h to fill, then a no-op) | 15 min | light | 60 | 43-47 | SPORTS_INGEST |

Flags are read when the service starts: flip a flag, then restart (Render: Save in the Environment tab restarts).
`/api/sports/admin/ops` -> `scheduler.jobs.<name>` shows each job's last exit, peak RSS, cap, over-cap kills, queue
time; `scheduler.stats` the peak reservation and peak children RSS.

### 5b. Write path from handlers (blessed 2026-10-23)

Handlers never write the canonical store or any SQLite file. The only handler-caused write is a **spool**: bounded,
non-blocking in-memory queue (full = dropped + counted) -> one writer thread -> append-only
`<SPORTS_DATA_DIR>/<name>_spool/<UTC date>.jsonl` with a per-day byte cap; a scheduled worker job loads it into its own
database idempotently. Reference: the slips spool (`sports/slips/log.py`: 2,000 entries, 50 MB/day; loaded by
`slips-ingest`). Transport = the shared disk, valid because the service and every worker child run in ONE Render
service on ONE disk. If the worker is ever split into its own Render service, spools must switch to a push transport
(HTTP POST to the worker) - Render disks cannot be shared.

Member picks (sports/picks.py pk-1) use the same pattern: `picks_spool/` (2,000 entries, 20 MB/day) -> scheduler jobs
`picks-ingest` (every 60 s, light, cap 60 MB) and `picks-settle` (hourly, light, cap 80 MB) -> `picks.db` (append-only,
idempotent by pick id; settlement revisions on corrections).

### 5c. Prebuilt snapshots

Heavy shared answers are registered in `web.SNAPSHOTS` and built by the `prebuilt` job; `web.cached` serves the
prebuilt value (HMAC-tagged pickle, ONE FILE PER KEY under `<SPORTS_DATA_DIR>/prebuilt/<name>/`, key derived from
SPORTS_TOKEN_SECRET; the service loads only the keys members ask for) and only falls back to a request-time build on
a miss (counted in `/api/sports/admin/ops` -> `prebuilt.stats`). The slips leg pool is the first entry (served when at
most 270 s old).

## 6. The Render service (owner created `scout-sports-api` on 2026-10-24 - apply these settings by hand)

We have no Render API access; these are the owner's clicks on the EXISTING service (never create a second one). The
service is declared only in `deploy/render.sports-api.yaml` (reference, not a Blueprint); the root `render.yaml` stays
esports-only (auto-synced - a service declared there would be created and billed by a merge).

1. **scout-sports-api -> Settings**: Branch = the branch / commit the coordinator names for staging; **Dockerfile
   path** `./Dockerfile.sports`; **Docker Command** `python -m sports.service`; **Health Check Path**
   `/api/sports/health`; **Auto-Deploy** `No`.
2. **Instance type**: `Standard` (2 GB / 1 CPU) is the current plan (2026-10-25, "Plan-aware sizing" above); the
   `Starter` (512 MB, 0.5 CPU) figures in section 13.1 are the original measurements, not the current plan.
3. **Disks**: `scout-sports-data`, mount `/sports-data`, **5 GB** (existing approved disk; section 13.3; enlargement needs owner approval).
4. **Environment**: every key in `deploy/render.sports-api.yaml` with its value. Secrets (already set by the owner or to
   be set there, never in git): `SPORTS_TOKEN_SECRET` (the same value as cs2-scout), `ADMIN_TOKEN` (same as
   cs2-scout), `ODDS_API_KEY`, `SPORTS_ALLOWED_ORIGINS` (`https://cs2-scout.onrender.com`), `ANTHROPIC_API_KEY` (only
   when the assistant is enabled). All `SPORTS_*` flags `0`.
5. **Manual Deploy** of the named commit. Check `https://scout-sports-api.onrender.com/api/sports/health` ->
   `{"ok": true, "version": "svc-1", "store_ready": false}` and `/api/sports/status` -> 401.
6. **cs2-scout -> Environment**: `SPORTS_UI=0`, `SPORTS_API=0`, `SPORTS_API_MODE=service`,
   `SPORTS_API_BASE=https://scout-sports-api.onrender.com`, `SPORTS_TOKEN_SECRET` (secret, same value). Member access
   stays OFF.

## 7. Staging (only scout-sports-api; members stay off) and later enable

**Staging** (the coordinator's flags: `SPORTS_API=1` + `SPORTS_INGEST=1` on scout-sports-api only; cs2-scout member
flags `SPORTS_UI=0`, `SPORTS_API=0`):

1. scout-sports-api `SPORTS_INGEST=1` and `SPORTS_API=1` -> Save (restart). The scheduler starts the jobs whose flags
   are on: collector every 15 min, Kalshi hourly, settle hourly, UI snapshots every 3 min, prebuilt every 90 s,
   history daily, archive every 6 h (it has nothing to do until offers are 72 h old). `SPORTS_TRAIN` stays `0` (no
   model forecasts on staging unless the coordinator asks; then see 13.1 - forecast / fit run exclusive).
2. About 20 min after the deploy, from a checkout of the deployed commit:
   `ADMIN_TOKEN=... SPORTS_TOKEN_SECRET=... python scripts/load_sports_staging_check.py --api https://scout-sports-api.onrender.com`
   (secrets from the environment, never printed; exit 0 = pass). It checks health, the auth round trip (minted
   token; no / tampered token 401; CORS), each job's last run / peak RSS vs cap / over-cap kills, feed ages, store /
   archive / disk use, service RSS, gentle latency (10 sequential per route + a 40-request parallel burst) and alerts.
   `python -m sports.service --check https://scout-sports-api.onrender.com` prints the alerts alone.
3. Render -> scout-sports-api -> Metrics: memory stays under ~450 MB including the children (13.1); restarts 0.
4. Pass = staging check exit 0 once (~20 min after the deploy), no `job_over_cap`, no paging alert. No multi-hour
   wait (owner 2026-10-24: the six-hour re-check is dropped); the archive (6 h) and history (daily) jobs are watched by
   the section 9 alerts as they run.

**Owner-first access (`SPORTS_UI=owner`, 2026-10-24).** cs2-scout serves `/sports`, `/sports/<asset>`, the esports
nav link and `/api/sports/token` ONLY to a paid Whop member (existing `Handler.allowed()`, checked first) whose allow id is
in `SPORTS_UI_ALLOW` (comma-separated). Everyone else gets exactly the flags-off answer (unknown route; esports pages
byte-identical - `test_isolation_server.py::test_everyone_else_gets_exactly_the_flags_off_answer`). Other
`/api/sports/*` paths keep following `SPORTS_API` (leave it `0` on cs2-scout). Allow id = `"sa_" +
sha256("scout-sports-allow|" + <Whop user id>)[:24]` (`server.sports_allow_id` = `sports.gateway.allow_id`; not tied to
`SPORTS_TOKEN_SECRET`, so a rotation keeps it). The owner reads his own id: set `SPORTS_UI=owner` (empty allow list =
nobody), sign in at cs2-scout, open `https://cs2-scout.onrender.com/api/sports/whoami?token=<ADMIN_TOKEN>` (admin token
only; anyone else gets the unknown route) -> `{"allow_id": "sa_..."}`; or `python -m sports.gateway allow-id <Whop user
id>`. Set `SPORTS_UI_ALLOW=sa_...` -> Save. cs2-scout env: `SPORTS_UI=owner`, `SPORTS_UI_ALLOW=sa_...`,
`SPORTS_API=0`, `SPORTS_API_MODE=service`, `SPORTS_API_BASE=https://scout-sports-api.onrender.com`,
`SPORTS_TOKEN_SECRET` (same value as scout-sports-api). scout-sports-api: `SPORTS_ALLOWED_ORIGINS` includes
`https://cs2-scout.onrender.com` (verified live 2026-10-24: preflight 204 with that origin). Rollback: `SPORTS_UI=0`.

Load (2026-10-24, Windows workstation, `python scripts/load_sports700.py <dir> off|owner`, 700 simultaneous paid members,
3 esports requests each = 2,100; owner mode: member u0 allow-listed and also loads /sports + mints a token, the rest
esports-only), 5 alternating runs each: esports p95 / p99 median off 1035.0 / 1090.6 ms, owner 1046.7 / 1096.1 ms
(x1.011 / x1.005), 0 5xx in all 10 runs, peak RSS 94-98 MB both; owner got /sports 200 + token 200. One run per mode had
client connect retries (6,771 / 7,358 - Windows ephemeral-port exhaustion in the load generator) and 17-23 s tails; the
medians include them.

**Member enable** (only after the release approval; unchanged order): cs2-scout `SPORTS_API=1` (token issuing; verify
an admin token with `curl -H "X-Admin-Token: $ADMIN_TOKEN" https://cs2-scout.onrender.com/api/sports/token`), then
`SPORTS_UI=1`; watch section 9 for an hour. Assistant: `ANTHROPIC_API_KEY` + `SPORTS_ASSISTANT=1`. Model forecasts /
lab: `SPORTS_TRAIN=1` (fits Starter - exclusive jobs, 13.1).

## 8. Disable / rollback (no database restore, no esports rollback)

- **Fastest kill switch (no esports restart):** scout-sports-api `SPORTS_API=0` -> Save (every data route answers 404
  within ~1 min), or **Suspend** scout-sports-api (sports requests fail at the sports origin only; the page shows its
  error state). Esports keeps serving; its token endpoint keeps minting tokens nobody can use for data.
- **Full rollback:** cs2-scout `SPORTS_UI=0` and `SPORTS_API=0` -> Save at a quiet window. With both off the esports
  process imports nothing from `sports/` and `/sports`, `/sports/*`, `/api/sports/*` (token included) are
  byte-identical to unknown routes (`test_rollback_is_flags_off_even_with_the_service_configured`, run in a child
  process with every other sports setting still present).
- **Stop ingestion only:** scout-sports-api `SPORTS_INGEST=0` -> Save. The store stays as it is (slip spools keep
  accumulating up to their daily cap and are loaded when ingestion is back).
- **Stop the assistant / lab only:** `SPORTS_ASSISTANT=0` / `SPORTS_TRAIN=0` -> Save.
- The sports disk is kept; re-enabling resumes from it (jobs are idempotent / resumable). Removing the sports service
  never touches the esports disk `/data`.

## 9. Monitoring and alerts

`GET https://scout-sports-api.onrender.com/api/sports/admin/ops` (header `X-Admin-Token`) returns the service counters
(requests, 5xx, `rejected_busy`, `unauthorized`, `forbidden_origin`, `inflight` / `peak_inflight`), RSS, threads,
`db_pool` (`in_use`, `peak_in_use`, `waits_timed_out`, `queries_interrupted`), cache, flags, worker state (`pid`,
`starts`, `last_exit`, `next_start`), feed freshness per platform (`age_min` of the newest offer sighting), store
size, and **`alerts`**. `python -m sports.service --check <url>` prints the alerts and exits 0 / 1 (warn) / 2 (page):
run it from a cron / uptime monitor every 5 min (Render has no custom alert rules; its own notifications cover failed
deploys and health-check failures).

| Alert | Level | Rule (`service.ALERT_RULES`, env-tunable) | First response |
|---|---|---|---|
| worker_down | page | ingest on and the collector (`ingest` job) has not finished OK for 3 cycles | ops `scheduler.jobs.ingest.last_exit`; Render logs |
| scheduler_down | page | the job scheduler thread is not running | restart the service |
| feed_stale | warn | ingest on and newest sighting older than 45 min (Kalshi 150 min) | check that source; a stale feed is never back-filled from another stat / scope / source |
| store_large | warn | store >= 4 GB or 80% of `SPORTS_STORE_MAX_MB` | plan disk resize / retention |
| store_full_ingest_paused | page | store >= `SPORTS_STORE_MAX_MB` | grow the disk, raise the cap |
| service_memory_high | warn | RSS > 80% of `SPORTS_SERVICE_MAX_MB` | find the handler holding large answers; the watchdog restarts at 100% |
| service_shedding | warn | (busy + 5xx) > 1% of requests | `peak_inflight` / `db_pool`: raise threads / pool or fix the slow route |
| store_slow | warn | pool waits timed out or queries interrupted | the heavy route must answer from a snapshot |
| token_secret_missing | page | secret unset / short | set `SPORTS_TOKEN_SECRET` on both services |
| job_failing | warn | any job failed 3 times in a row | ops `jobs.<name>.last_exit`; Render logs |
| job_over_cap | page | a job killed at its memory cap twice in a row | section 13.4 |
| job_starved | warn | a job queued for > 30 min | section 13.4 |
| history_storage | warn | soccer / tennis history store >= 80% of its budget, or at it (`degraded`) | raise `SCOUT_<SPORT>_HISTORY_MAX_MB` if the disk allows; at the budget the backfill is off, live matches still settle |
| mapping_audit_truncated | warn | the hourly mapping audit stopped at its time budget | counts cover part of the offers; the next run resumes after the stop. Persistent: check the archive pass / store read speed |
| disk_80 / disk_90 | warn / page | the sports disk >= 80 % / 90 % used | section 13.3 (enlarge) |
| snapshot_stale | warn | API on and a prebuilt snapshot older than 3x its max age (or never built) | the `snapshots` job; members fall back to request-time builds (slower, still correct) |

Esports side: `/health` (unchanged) and, with `SPORTS_API=1`, `/api/sports/admin/gateway` (admin token):
`token_minted`, `token_refused`, `not_here`, `pages`, mode.

## 10. On-call checks

1. Is esports affected? cs2-scout `/health`: `traffic.errors_5xx`, `avg_server_ms`, `memory_mb` vs the usual. If yes,
   kill switch (section 8) first, investigate second.
2. `python -m sports.service --check https://scout-sports-api.onrender.com` - act on pages.
3. Render -> scout-sports-api -> Metrics: memory under ~450 MB (13.1); disk vs the `disk_80` alert (13.3).
4. A member reports "Sports unavailable": `/api/sports/health` up? Token endpoint 401 for that member = their Whop
   access, not sports; 503 `sports_auth_not_configured` = the secret is missing on one side.
5. Before enabling any sports change: re-run section 11's load script on it against the section 1 budgets.
6. Grade check (REGISTER A9, gc-1; read-only, scout-sports-api Shell, repo root):
   `python -m sports.model.tune gradecheck` (both leagues) / `--league nfl` / `--days 14`. Prints one compact JSON
   (the owner pastes it back): per league the counts, overall / stat / letter / side / platform / variant / home-away /
   favourite / line-distance / market-chance / weekday / back-to-back / forecast-age / artifact slices (n, W-L-P, hit,
   stated, game-clustered 95% interval, Over balance), the vg-1 / sg-1 / N4 guard counts, cf-2 bins, the cal-mi-1
   walk-forward (A8a) and the A9 5 verdict (release / keep / contain / insufficient). It reads learning.db,
   forecasts.db, canonical.db, model/history.db and games.db under SPORTS_DATA_DIR and writes nothing.
7. Daily grades (REGISTER A9a, job `grades-daily`; nothing to paste - it runs ON the service). What runs: once per
   game-day after 10:00 UTC, when learning.db has a settlement newer than the last run: (1) the cal-mi fit (A8 + A8a,
   the same path as `python -m sports.model.cal fit`) with hysteresis - a market switches ON the day it passes and OFF
   only after 2 consecutive daily fails -> `model/cal_mi.json`, one line per market in `model/cal_mi_log.jsonl`;
   (2) gradecheck on both leagues, scoring the chance members see (cal-mi chance on markets that are ON) ->
   `model/gradecheck.json` + `model/gradecheck/gradecheck_<day>.json` (30 kept); (3) letters per league from the A9 5
   verdict (release = shown, contain = hidden, contain:<stat> = that stat hidden, keep / insufficient = unchanged) ->
   `model/containment.json` with its decision log. Seed (no file yet): NBA + NFL contained (owner-run gradecheck
   2026-10-28). Since ct-1b (owner decision 2026-11-08, REGISTER A9b note) NBA and NFL letters are RELEASED by the
   owner: the verdict above is still computed and logged (`containment.verdict` beside `containment.letters`; log
   entries say "owner release ct-1b: verdict logged, letters shown") but it no longer hides letters, nor does a failed
   pc-1 held-out test. Read it: cs2-scout `/admin/diagnostics?token=...` -> "Sports grade check" (verdict, letters stated vs
   hit, n, CI, letters shown / hidden); JSON: `GET /api/sports/admin/model/gradecheck` (admin st1 token; `?part=headline`
   for the summary) or `python -m sports.model.daily status` in the Shell. Switches: `SPORTS_CAL_AUTOFIT=0` (no daily
   fit; manual `fit` still works), `SPORTS_GRADECHECK_DAILY=0` (no daily gradecheck / containment change),
   `SPORTS_CONTAIN_NBA=1` / `SPORTS_CONTAIN_NFL=1` (the owner's kill switch: hides that league's letters, wins over
   everything incl. the ct-1b release; a 0 is logged and changes nothing; gradecheck.json `containment.env_requests`
   shows what was asked and what applied; members see letters again within one ui-snapshots / sheets cycle after the
   service restarts),
   `SPORTS_DAILY_AFTER_UTC_H` (default 10). Run now from the Shell: `python -m sports.model.daily run --force`.
8. Printed chance pc-1 (REGISTER A10, default ON, no Render variable needed). Every NBA / NFL sm-1 chance on the
   board, cards, sheets, slips, tracked picks and EV is the calibrated chance (`sports/model/chance_cal.py`, committed
   parameters `sports/model/artifacts/chance_cal_pc1.json`); the raw chance stays in the forecast record, `ca.raw`
   and `model/shown.db`. A league whose held-out test failed keeps its letters held - except an owner-released league
   (ct-1b: NBA and NFL since 2026-11-08), where the result is reported only. Status: `python -m
   sports.model.chance_cal status`. Refit in the Shell (writes `model/chance_cal.json`, which wins over the committed
   file): build the exports (`python -m sports.model.gradecore_export --data-dir /sports-data --league nba|nfl`),
   then `python -m sports.model.chance_cal fit --nba <export> --nfl <export> --test-from <first held-out game-day>
   --report <path>`. Off: `SPORTS_CHANCE_CAL=0` (the board shows cal-mi-1 / raw as before).
9. Store read cost (store-slow 2026-11-09). Store migration 8 adds two covering indexes on canonical.db:
   `records_offer_seen` (the board withdrawal read of `_store_offers`, by venue + league) and `records_current_stat`
   (`store.iter_offers(league, stat)`, model-forward's per-market offer list). Before it both reads fetched the table row
   of every stored offer revision, so ingest-<league> and model-forward slowed as the hot table grew (old vs new:
   `python scripts/bench_store_offer_reads.py`). The first writable connection after the deploy builds the indexes once
   (45 s on a 5.46 GB synthetic store on the workstation SSD, +460 MB; longer on the Render disk - the collector that takes it may miss one cycle). The Novig / Gemini
   rows the reverted exchanges release left: `python scripts/sports_exchange_rows.py` (read-only count). They age out
   through the archive pass (72 h unseen); `purge --apply` deletes them and is an owner-approved manual step only.

## 11. Load results (2026-10-23, Windows workstation, 700 simultaneous members)

Evidence: `sports/research/results/load_service_2026-10-23/` (every run as JSON + `REPORT.md` from
`scripts/load_sports_report.py`). Medians over runs [min-max]. "p99 vs off" = against the `off` median of the SAME
series. Each run is 2,100 esports requests (identical arrivals in every mode) + 700 sports flows. Other sessions'
test suites / model studies were running on the same machine during all series (column "other python CPU %" in
REPORT.md; up to ~4 busy cores), so run-to-run spread is wide - read the medians, not single runs.

**Series 1 - the registered comparison** (code 1122c0e + e75cc06, foundation sports routes; 5 runs each):

| Config | Esports p95 ms | Esports p99 ms | p99 vs off | 5xx | Esports peak RSS MB | Sports non-200 | Verdict (section 1) |
|---|---|---|---|---|---|---|---|
| off (baseline) | 59.7 [57-94.1] | 69.6 [63.4-126.7] | 1.00 | 0 | 68.5 | - | - |
| inprocess-enabled (foundation path) | 81.1 [60.6-101.6] | 114.1 [68.5-138.9] | 1.64 | 0 | 107.2 | 0 / 3,500 | FAIL |
| service-enabled | 67.8 [64.3-89.7] | 76.1 [72.6-125.5] | 1.09 | 0 | 69.0 | 1 / 3,500 | FAIL (p95 x1.14) |
| service-ingesting | 103.9 [77.3-127.1] | 120.9 [85.3-180.2] | 1.74 | 0 | 69.3 | 0 / 3,500 | FAIL |
| proxy-enabled | 79.5 [62.4-113.4] | 124.9 [68.8-139.6] | 1.79 | 0 | 78.7 (+10.2) | 374 / 3,500 (busy sheds) | FAIL |
| proxy-ingesting | 82.5 [77.5-126.5] | 93.1 [85.1-178.4] | 1.34 | 0 | 78.6 | 255 / 3,500 | FAIL |

Registered decision rule: neither option passed budget 1, so **sports stays off** (it is) and this is reported as
such. `service` is the better option on every metric and the architecture kept; the residual is analysed below.

**Series 3 - final code** (integration head d57dffb + 22b832a: slips, assistant, UI board / slate / manifest routes,
gzip + ETag, prebuilt slips pool; sports requests send `Accept-Encoding: gzip`; 5 runs each). The last three rows are
DIAGNOSTICS added after series 1 (labelled post-hoc, not part of the registered comparison):

| Config | Esports p95 ms | Esports p99 ms | p99 vs off | 5xx | Esports peak RSS MB | Token p99 ms | Sports non-200 | Sports p99 ms | Service peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|
| off (baseline) | 57.1 [49.7-59.0] | 64.5 [54.7-67.0] | 1.00 | 0 | 68.9 | - | - | - | - |
| service-enabled | 65.5 [63.4-68.5] | 73.3 [68.6-75.1] | **1.14** | 0 | 70.7 | 71.3 | 1 / 3,500 | 310 | 128 |
| service-ingesting | 76.7 [71.9-82.5] | 86.7 [81.7-92.7] | 1.34 | 0 | 71.0 | 85.2 | 0 / 3,500 | 310 | 130 |
| off-ingesting (diag.: writer only, no sports) | 63.1 [62.2-78.5] | 70.4 [68.6-88.2] | 1.09 | 0 | 68.2 | - | - | - | - |
| service-tokenonly (diag.: token requests only) | 67.4 [63.5-74.9] | 73.7 [70.1-84.6] | 1.14 | 0 | 70.3 | 66.8 | - | - | 118 |
| service-notoken (diag.: sports traffic only, zero extra esports requests) | 66.9 [64.0-70.5] | 75.8 [70.5-90.7] | 1.18 | 0 | 70.4 | - | 2 / 3,500 | 605 | 128 |

Service-side, every series-3 enabled run: peak in-flight 32 of 32 threads, `rejected_busy` 0, store pool peak 3 of 4,
0 pool timeouts, 0 interrupted queries; slips pool served from the prebuilt snapshot 216 / 216 times (0 misses);
6 gzip compressions for 323 gzip answers (~10.1 MB sent per run); no alerts. Child processes (measured separately):
`sports.service snapshots` 2.8 s wall, 166 MB peak RSS; `sports.snapshots build nba nfl` 6.0 s, 115 MB.

What the numbers say (and do not say):
- **Budget 1 (+10%) is not met on this machine.** With sports enabled the esports p99 is +8.8 ms (x1.14) and p95
  +8.4 ms (x1.15) over a 64.5 / 57.1 ms baseline. Budgets 2-4 pass in every run: 0 esports 5xx, esports peak RSS
  +1.8 MB, sports service <= 131 MB, pool <= 4.
- The two diagnostics each reproduce the whole effect: the token requests alone (x1.14, sports service idle) and the
  sports traffic alone with NO extra esports request (x1.18). On one shared workstation the cost is CPU contention
  between processes, not work in esports threads. In production the sports traffic runs in another container (no
  shared CPU); the token request remains - 1 per member per 10-minute token life, ~67-85 ms p99 itself.
- "Ingesting" on this machine is dominated by the synthetic writer sharing the CPU (writer alone: x1.09; writer +
  sports: x1.34). The production worker runs in the sports container.
- Not measured: Render containers, more than 700 members, real network ingestion during a burst, browsers
  (preflight requests go to the sports service, not esports).
- **Required before any enable:** re-run `scripts/load_sports.py` (or the same scenario against staging) with the
  sports service on its own Render instance and confirm esports p99 within +10% there. Until then the +10% claim is
  unproven.

**Series 4 - failure modes** (final code, 3 runs each; budget 5):

| Scenario | Esports p99 vs off | Esports 5xx | Sports outcome |
|---|---|---|---|
| service-failing (service process down) | x1.16 | 0 | 700 / 700 connection refused at the sports origin (the Windows client needs ~2 s to report a refused localhost connect; no esports involvement); tokens still minted (p99 45 ms) |
| service-broken (corrupt store, every query raises) | x1.27 | 0 | 592 / 700 fast 503 (p99 96 ms); the 108 routes that do not read the store still answered 200 |
| service-recovering (down -> started -> burst) | x1.26 | 0 | burst 1: 700 refused; service ready 0.5 s after start, prebuilt pool 2.9 s; burst 2: 2,099 / 2,100 200 (one 503 sports_busy) - no esports restart |
| proxy-failing | x1.15 | 0 | 700 / 700 503 (16 upstream errors, then the 16 slots / circuit breaker shed the rest); esports RSS +17 MB while sockets waited |
| proxy-recovering | x1.14 | 0 | burst 2 started 2.9 s after the service came back, inside the breaker's 5 s open window: 700 / 700 503 - recovery waits for the breaker |

Esports never returned a 5xx in any run of any series (0 of 176,400 esports requests across series 1, 3 and 4,
both bursts of the recovering runs included). (REPORT.md's automatic
"Verdict" column applies the enabled budget to the failure rows too; for them budget 5 is the relevant one.)

Rollback = `off`: the flags-off state on the same code is the baseline row of every series (no sports import,
sports paths identical to unknown routes - `test_rollback_is_flags_off_even_with_the_service_configured`).

## 12. Remaining blockers / open items (service scope, updated 2026-10-24)

1. **+10% esports p99 budget FAILED locally (+14%, section 11) - pending staging.** The sports service now exists on
   its own Render instance (owner, 2026-10-24); staging (section 7) must show esports p99 within +10% before member
   enablement. Locally, esports can only be measured with all processes sharing one machine.
2. **Disk runway:** the approved 5 GB reaches its 4 GB alert/pause in roughly 3-12 days in the current preliminary projection; see RETENTION.md. Do not enlarge without approval. Fitting 6 months
   in ~25 GB needs the post-release follow-up: compaction of books.db quotes and games.db market / prop lines
   (collector / lines owners; their readers must use archive-aware views). Lifetime revision counts per offer (which
   raise the 8.2x compaction ratio) are not confirmed yet - re-measure after a week of staging.
3. **Model jobs read every forecast** (learn / snapshot / forecast peaks grew 78 -> 218 MB, 272 -> 311 MB on the test
   copy); forecasts are not archived. Bound those reads before their caps are reached (model / lines owners).
4. The `anthropic` SDK pin (`>=2.0,<3`) and the assistant's beta header were not exercised (no key); watch the first
   enable (`job_failing` for the on-demand `assistant` job). Its memory is not measured (cap 120 MB).
5. Books worker memory was measured only offline (36 MB, no provider calls from here); inside the collector cycle
   (`ingest`, 274 MB measured with live sources including FanDuel / BetRivers / Polymarket / games) it is covered.
6. Source rights: Kalshi (partner) and ESPN member display - **owner attestation 2026-10-23** (not a blocker).

## 13. Starter plan (owner 2026-10-24: "the cheapest plan without getting rid of any data")

### 13.1 Memory budget (512 MB container, measured)

| Part | Measured | Limit |
|---|---|---|
| sports service, idle | 34 MB (slim slip pool) | restart above 160 MB (`SPORTS_SERVICE_MAX_MB`) |
| sports service, 700 simultaneous members (one local core) | 50-52 MB peak | same |
| largest heavy child (ui-snapshots / model-learn / model-snapshot) | 200-218 MB | cap 250, budget 310 |
| exclusive child: collector cycle | 271-274 MB | cap 300, runs alone |
| exclusive child: model forecast | 272-311 MB | cap 340, runs alone |
| exclusive child: model fit | 306-309 MB | cap 340, runs alone |
| worst admitted combination | service ~52 + forecast 311 = **363 MB** (by reservation: 52 + 340 = 392) | 512 MB container |

Never two heavy children; the reservation (caps, not measured values) is what is admitted, so a child that grows
is killed at its cap before the container fills; children are the kernel's first OOM victims (`oom_score_adj` 900).
The NBA fit (308 MB) runs only when no other child runs (exclusive) - ingestion waits for it.

### 13.2 CPU (0.5 CPU) and what members notice

Measured with the service and its job children pinned to ONE local core (the closest local stand-in; Render's 0.5
CPU is slower), 700 simultaneous sports requests (status / offers / slips / assistant status / board / slate /
manifest, gzip): 2,100 / 2,100 answered 200 in 3 runs; sports p99 **1.78-1.92 s**, and **1.86-2.11 s** with a UI
snapshot build looping beside it at low priority (sports/research/results/starter_2026-10-24/final_load.jsonl).
Snapshot serving is file reads; jobs run at nice 10.

Freshness changes members would see on Starter (all in deploy/render.sports-api.yaml, revertible):

| What | Before (Standard plan) | Starter |
|---|---|---|
| pick'em / book / Polymarket lines collected | every 10 min | every 15 min |
| Kalshi prices collected | every 30 min | every 60 min |
| member board / slate / player pages rebuilt | every 60 s | every 3 min |
| slip leg pool rebuilt (served when <= 270 s old) | every 45 s (<= 90 s) | every 90 s |
| assistant answer start | immediate | waits for a running collector cycle (~2-3 min) or forecast run (~30-45 s) |
| model forecasts (SPORTS_TRAIN=1) | - | hourly, exclusive |
| a burst of 700 simultaneous sports requests | p99 ~0.3 s (16 local cores) | p99 ~2 s on one local core; slower on 0.5 CPU |
| history of offers not seen for 72 h | instant | read back from the archive: one record 36 ms, an event's offers 0.67 s, a player's 0.21 s |

### 13.3 Disk: compaction, projection, cost, enlarging

**Compaction (sports/archive.py arch-2, nothing deleted):** every revision of an offer not seen for 72 h moves to
verified lzma files (`<SPORTS_DATA_DIR>/archive/records/`) with a per-identity index in canonical.db; reads span hot
+ archive (13.2). Measured on the real copy (80,470 offer rows, 47,632 identities): hot **1,514 B / row** ->
archived **185 B / row** (70 B file + 115 B index) = **8.2x**. That copy holds ~11 h of collection (1.69 revisions per
identity); price-tick identities collect many more revisions over their life, which this index amortises - but the
lifetime revision count is NOT confirmed from this copy, so the projection uses 8x.

**Growth per day at the Starter cadence** (measured where stated; one night-time 15-min sample - day-time slates
are busier, hence the range):

| Item | MB / day | Basis |
|---|---|---|
| canonical offer revisions (raw) | 440-950 | 2.8 MB per 15-min collector cycle (measured, 1,936 revisions) x 96 + Kalshi ~7 MB x 24 + book-bridge offers (not in the sample: 0 books due) |
| ... after compaction (8x) | **55-120** | the hot 72 h window stays ~1.3-2.9 GB (pages reused) |
| books.db quote history (not compacted) | 35-84 | books README estimate (standard - full ladder) |
| games.db (not compacted) | 48-96 | 0.49 MB per cycle measured x 96 |
| forecasts (incremental) | ~10-30 | estimate; an unchanged run adds 0 (measured) |
| slips spool / slips.db | <= 50 cap, ~0 at launch | |
| **accumulating total** | **~150-330** | |

| Horizon | Data (low - high) |
|---|---|
| start (store, model files, snapshots, history) | ~1 GB |
| 1 month | ~7-14 GB |
| 3 months | ~16-34 GB |
| 6 months | ~29-63 GB |

**Cost** (Starter $7 / month + $0.25 per GB-month):

| Disk | Disk $ / month | Total $ / month | Lasts at the projection (to the 80 % alert) |
|---|---|---|---|
| **5 GB (existing approved)** | 1.25 | **8.25** | preliminary 3-12 days to alert/pause; see RETENTION.md |
| 10 GB (unapproved scenario) | 2.50 | 9.50 | ~3-6 weeks |
| 25 GB | 6.25 | 13.25 | ~2.5-4 months |
| 50 GB | 12.50 | 19.50 | ~5-8 months |
| 80 GB (6 months at the high estimate under 80 %) | 20.00 | 27.00 | 6+ months |

For comparison the previous template was Standard $25 + 20 GB $5 = $30. **The next step that keeps 6 months under ~25
GB** is compacting books.db quotes and games.db market / prop lines the same way (post-release follow-up, owners:
collector / lines code - their readers must read through archive-aware views); option 4 (collecting fewer price
ticks) is the owner's data decision and is NOT applied.

**Enlarge procedure (no data loss):** Render dashboard -> scout-sports-api -> **Disks** -> the disk -> **Edit** ->
a larger size -> **Save**. Render keeps the data; expect a short restart. A disk can be enlarged later, never shrunk
- grow in steps (10 -> 25 -> 50 GB). If `SPORTS_STORE_MAX_MB` (4,000) is ever the limit (`store_full_ingest_paused`),
raise it with the disk. Alerts: `disk_80` (warn) -> plan the enlarge this week; `disk_90` (page) -> enlarge now.

### 13.4 When a memory alert fires

| Alert | Meaning | Do |
|---|---|---|
| `service_memory_high` (RSS > 80 % of 160 MB) | a cache or a handler holds large answers | ops `cache`, `encoding.cached_bodies`, `prebuilt.keys_in_memory`; lower `SPORTS_CACHE_MAX` / `SPORTS_BOARD_CACHE_MB`; the watchdog restarts the service at 160 MB |
| `job_over_cap` (page: a job killed at its cap twice in a row) | that job's memory grew with the data (ui-snapshots, model-learn / -snapshot / -forecast read every forecast and grow) | ops `scheduler.jobs.<name>.last_exit.peak_rss_mb`; raise `SPORTS_JOB_<NAME>_CAP_MB` only while service (~50) + cap <= ~450 MB; otherwise the job's owner must bound its reads (window / stream) - last resort the Standard plan |
| `job_starved` (a job queued > 30 min) | exclusive jobs back to back | check ops `scheduler.stats.deferred_ticks`; lengthen the exclusive jobs' `SPORTS_JOB_<NAME>_EVERY_S` |
| Render event "out of memory" | the container hit 512 MB | the kernel kills a child first; ops / logs show which; treat as `job_over_cap` |

Known growth risk: forecasts and settlements are not archived, and the model learn / snapshot / forecast jobs read
all of them - their peaks grew 78 -> 218 MB on the test copy as forecasts accumulated. They will outgrow their
caps over weeks unless the model jobs bound their reads (reported to the model / lines owners).

### 13.5 Forward forecasts without training (SPORTS_FORECAST, sports/model/fwd.py - owner 2026-10-24)

- **Enable:** scout-sports-api Environment: `SPORTS_FORECAST=1`; `SPORTS_TRAIN` stays `0`. Optional:
  `SPORTS_FORECAST_REFRESH=0` (no NBA box-score refresh), `SPORTS_FORECAST_REFRESH_S` (refresh budget, 120 s),
  `SPORTS_FORECAST_MAX_VM_MB` (address-space backstop, 640), `SPORTS_JOB_MODEL_FORWARD_EVERY_S` (1800),
  `SPORTS_JOB_MODEL_FORWARD_CAP_MB` (340). Disable: `SPORTS_FORECAST=0` (stored forecasts stay; nothing is deleted).
- **What runs** (scheduler job `model-forward`, exclusive - never beside ingestion or any other child): verify
  `sports/model/artifacts/MANIFEST.json` (sha256 of every artifact; sm-1 nba 5ec8adf73a6dad18, nfl cc56aeb8b88c31d8,
  mc-1 8119ce6b61539999) -> on first run install the packaged ds-1 history (10.9 MB gz -> 42.6 MB,
  `<SPORTS_DATA_DIR>/model/fwd/history.db`, sha256-checked) -> append finished NBA games from ESPN (bounded, 32 h
  lag) -> sm-1 / mi-1 / mc-1 / mkt-* forecasts for the current board (incremental: an unchanged offer revision x
  input key writes nothing) -> bounded model snapshot. Never: fit, learn-retrain, study, lab. A tampered artifact
  refuses the run (`refused: frozen artifacts do not verify`), nothing written.
- **Settlement / scorecard:** the settle job (SPORTS_INGEST) scores settled forecasts event by event
  (learning.on_settled - bounded, never retrains) for gate F (>= 300 settled props, >= 100 games).
- **Check it:** `GET /api/sports/admin/model/learning` -> `forecast_run` (finished_at, forecasts_added, per-league
  written / unchanged / skipped, artifact shas, history + NBA refresh, peak_rss_mb, withheld_check) and ops
  `scheduler.jobs.model-forward` (last_exit.code 0, last_exit.peak_rss_mb under 340).
- **Measured** (real-feed store copy, Windows working set): 306-319 MB with 14.6k or 214.6k stored forecasts (does not
  grow with history); snapshot 93 MB (was 218 -> 2,394 MB); scorecard step 51 MB / 0.8 s (was 51 -> 88 MB, 8.6 ->
  109 s with 200k forecasts). Disk: ~5.1 KB per stored forecast; ~7-70 MB / day (sports/research/results/fwd_2026-10-24).
  Linux (sandbox, cgroup memory limit 340 MB, RLIMIT_AS 640, fresh real-feed store, NBA refresh included): first run
  291.9 MB RSS / VmPeak 365 MB / cgroup peak 321.6 MB, 26 s, 3,121 forecasts, no OOM; rerun 288.8 MB, 46 added. With
  RLIMIT_AS 320 (the collector's cap) it fails with MemoryError - keep SPORTS_FORECAST_MAX_VM_MB at 640.
- **NFL refresh** (`fwd.refresh_nfl`, in this job): the current season's nflverse files (schedule, player-week stats,
  snaps, play-by-play) reload into the installed copy at most every 12 h (`SPORTS_FORECAST_NFL_REFRESH_H`); earlier
  seasons untouched; every file is fetched before any table changes, so a source failure keeps the data. Package: 2022-
  2025 complete + 2026 weeks 1-7. Linux run with it (cgroup 340): 289.8 MB RSS, 26 s; a same-data reload changed 0
  forecasts. Off with the NBA refresh: `SPORTS_FORECAST_REFRESH=0`.
- **Limits:** grades withheld / stakes 0 until gate F (sports-gp-1).

### 13.6 Member pick tracking (pk-1, `sports/picks.py`) - UI and test picks

- `/api/sports/picks/check` runs picks.check alone and writes nothing: the research workspace asks it before it tracks.
- Routes `/api/sports/picks/track|mine` and `/api/sports/admin/picks` (no flag: live wherever this API is deployed);
  jobs `picks-ingest` (60 s) / `picks-settle` (hourly) under SPORTS_INGEST.
- Page (`web/sports/app.js`, served by cs2-scout from `main`): "Pick Higher / Lower" (Underdog) / "Pick More / Less"
  (PrizePicks) on gradable pick'em lines before the start (board rows, phone cards, player page) and "My picks"
  (`#/picks`). The page shows them only when `/api/sports/picks/mine` answers, so it stays as today against an API
  without the routes.
- **Test picks** (scratch, aggregated under `test`, never in the member figures): admin-token picks, picks posted with
  `"test": true`, and every pick while scout-sports-api has `SPORTS_PICKS_SCRATCH=1`. Set it for the owner-only
  preview (`SPORTS_UI=owner`); remove it (-> Save) when members get access, so their picks count.

## CHECKPOINT (sports/starter, 2026-10-24)

Done: memory-reservation scheduler + caps (cf1b46b), service memory (ca2900b), compaction arch-1 + store hook
(1bd3d23), archive-spanning reads (9022715), staging check + disk alerts (33a5642), arch-2 per-identity index
(49be244), Starter caps / template / runbook (this commit). Tests: full sports suite (see the commit), esports suite,
700-member esports load with flags off. Remaining after this release: option 2 (books / games compaction), bounded
model-job reads, staging pass (section 7).

