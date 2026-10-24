> Integration correction: use the EXISTING 5 GB disk ($8.25/month base), not the historical 10 GB sizing proposal below. No enlargement is approved. SPORTS_STORE_MAX_MB=4000 counts the entire data directory; SPORTS_WORKER_MAX_MB=310 is RSS and SPORTS_WORKER_MAX_VM_MB=320 is virtual memory for measured ingestion/settlement jobs. Keep SPORTS_TRAIN=0 until model-job virtual memory is measured. See RETENTION.md for the short measured runway; proposed larger sizes below are planning scenarios only.

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

Peak RSS measured 2026-10-24 on a copy of the real data (each job alone, `scripts/load_sports_jobs.py`; results in
`sports/research/results/starter_2026-10-24/`). Memory of the store-reading jobs grows with the data (see 13.4).

| Job | Command | Every (Starter) | Class | Cap MB | Measured peak MB | Flag |
|---|---|---|---|---|---|---|
| assistant | `sports.assistant.runner --once` | on demand (a queued question, checked every 2 s) | light | 120 | not measurable here (no SDK / key) | SPORTS_ASSISTANT |
| prebuilt | `sports.service snapshots` (slip pool) | 90 s | heavy | 160 | 105-109 | SPORTS_API |
| ui-snapshots | `sports.snapshots build nba nfl` | 180 s | heavy | 250 | 135-200 | SPORTS_INGEST |
| slips-ingest | `sports.slips ingest` | 5 min | light | 60 | 23 | SPORTS_INGEST |
| ingest | `sports.worker ingest nba nfl --sources espn,pp,ud,injuries,oddsapi,polymarket,books,games` (leagues one at a time) | 15 min | exclusive | 300 | 271-274 (328 with both leagues in parallel) | SPORTS_INGEST |
| ingest-kalshi | `sports.worker ingest nba nfl --sources kalshi` | 60 min | heavy | 240 | 147-192 | SPORTS_INGEST |
| settle | `sports.worker settle nba nfl` | 60 min | heavy | 120 | 58 | SPORTS_INGEST |
| slips-settle | `sports.slips settle` | 60 min | light | 60 | 23 | SPORTS_INGEST |
| model-forecast | `sports.worker train forecast nba nfl` (incremental: unchanged runs add 0 records) | 60 min | exclusive | 340 | 272-311 | SPORTS_TRAIN |
| model-learn | `sports.worker train learn` | 60 min | heavy | 250 | 78-218 | SPORTS_TRAIN |
| model-snapshot | `sports.worker train snapshot` | 30 min | heavy | 250 | 77-217 | SPORTS_TRAIN |
| model-fit | `sports.worker train fit` | 7 days | exclusive | 340 | 306-309 | SPORTS_TRAIN |
| lab | `sports.lab.runner --once` | 15 min | exclusive | 340 | 34 idle (training up to the fit size) | SPORTS_TRAIN |
| history | `sports.snapshots history nba nfl --max-players 400` | daily | heavy | 200 | 107 (40 players) | SPORTS_INGEST |
| archive | `sports.archive pass` (13.3) | 6 h | heavy | 200 | 140 (80k rows) | SPORTS_INGEST |
| archive-verify | `sports.archive verify` | daily | heavy | 120 | - | SPORTS_INGEST |

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
2. **Instance type**: `Starter` (512 MB, 0.5 CPU) - section 13.1 for the memory budget.
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

