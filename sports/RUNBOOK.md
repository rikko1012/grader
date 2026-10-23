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
