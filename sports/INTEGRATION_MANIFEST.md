# Scout Sports integration manifest

Integration branch `feature/scout-sports-nfl-nba` (worktree `.claude/worktrees/sports`). Later agents (model, UI,
slips, assistant / lab) branch from this branch; the coordinator merges them back. Production sports flags stay OFF;
nothing here is deployed or enabled for members.

## Base and pins

| Item | Value |
|---|---|
| Base | `origin/main` **2fbfc0e** (2026-10-23, "Amendment AM-1 ... docs only"; esports live: CS2 tag aa6696d5d6, LoL) |
| origin/main since | advanced to **0ee4d3e** (PR #14 veto/vserve, PR #12 research/map-veto) while this branch was built - NOT merged here; merge main before the PR |
| Data foundation | `origin/claude/dreamy-wozniak-kefq4r` @ **d393292** - 4 commits (dbb1c1d, d704cdf, b6b43bd, d393292; 2026-10-23 20:13-20:21 UTC), directly on 2fbfc0e, 44 files +3,063 / -2 |
| Merge | merge commit **7c888b7** (`--no-ff`, no conflicts, d393292 merged unmodified; corrections are later commits) |
| Contracts | `sc-1` (`sports/contracts/schema.py`) |
| Store schema | migration level 1 (`sports/store.py`, `<SPORTS_DATA_DIR>/canonical.db`) |
| Adapters | `ad-1`; capabilities `cap-1`; team aliases `ta-1`; Underdog payouts `ud-payouts-2026-10-23` |
| Research versions | `sharp-1` (data branch, unchanged, importable via `legacy.py`), `sharp-2` (corrected market-only baseline, research only) |

## Esports baseline and non-regression

- Esports suite at the base (2fbfc0e): `python -m pytest tests lol/tests -q` -> **1731 passed, 5 skipped** (310.6 s).
- Production `/health` (read-only, 2026-10-23 ~20:32 UTC, just after a restart at 20:31:57): ok, memory 314 MB
  web RSS (container 629 / 2,148 MB, 0 OOM events), threads 21, HLTV pool 5 open / 16 max, disk 74.5% used
  (1,309 MB free of 5,196 MB on /data), ops state "fail" only because the Underdog / PrizePicks feeds were still
  loading after the restart (Thunderpick / Polymarket ok). Snapshot kept in the foundation scratch folder.
- After integration (commit b119ec1): esports suite **1731 passed, 5 skipped** (281 s) - unchanged; the CI esports
  step restored byte-identical.

## Review of d393292, file by file

| File | Verdict | Notes |
|---|---|---|
| `sports_scout.py`, `sports/__main__.py`, `sports/__init__.py` | keep | CLI launchers; no esports import |
| `sports/config.py` | keep | `SPORTS_DATA_DIR` > `<CS2_DATA_DIR>/sports` > `sports/.data`; league table (ids read 2026-10-23) |
| `sports/cache.py` | keep | separate SQLite answer cache, zlib rows, never fatal |
| `sports/net.py` | **fixed** | per-host pacing kept; retry sleep was a fixed 3 s / 6 s -> exponential backoff with jitter (`net.backoff`) |
| `sports/lines.py` | keep (wrapped) | PrizePicks partner API + Underdog public board. Its parsers silently drop promos / scopes / combos; the canonical path re-parses the raw body in `adapters.py` so every drop is a typed record or a counted quarantine reason. `lines.jsonl` archive kept for sharp-1 |
| `sports/kalshi.py` | keep (wrapped) | correct "N+" -> Over N-0.5 reading; no liquidity / in-play / freshness handling (AUDIT.md 2) |
| `sports/espn.py` | **fixed** | date-RANGE scoreboard answers HTTP 400 (2026-10-23) -> day-by-day `scoreboard_raw`; finals cached forever (corrections missed) - canonical settlement re-reads |
| `sports/oddsapi.py` | keep (wrapped) | off without `ODDS_API_KEY` (not set: no counts) |
| `sports/pinnacle.py` | keep, unused | public guest key in source; refuses US locations - never worked around; no adapter |
| `sports/markets.py`, `sports/names.py`, `player_aliases.json` | keep | exact whole-label stat maps; exact normalised names; 3 corroborated alias groups |
| `sports/fair.py`, `dispersion.json`, `fit_dispersion.py` | keep (audited) | transformation audited (AUDIT.md 3); dispersion fit unvalidated in the branch |
| `sports/edges.py` | keep (research) | ranks by `chance` vs single 53.45% Underdog reference (AUDIT.md 1) |
| `sports/projection.py` | keep (research) | recency-weighted history baseline, display only |
| `sports/record.py` | keep (research record sharp-1) | stores conditional chance only; ESPN range bug made settlement pend; report OK |
| `sports/scout.py` | keep | CLI; `watch` loop = the data branch's recorder (not run here) |
| `sports/tests/*` (6 files, 44 tests, 12 fixtures) | keep; 1 edited | `test_sports_isolation.py` now allows the one server hook (proved by `test_isolation_server.py`) |
| `sports/README.md` | keep + pointer | "first live record" numbers unverifiable here (below) |
| `.github/workflows/lol-full-history.yml` | **changed back + new job** | the branch appended `sports/tests` to the esports job, which `full-history` (manual LoL collection) `needs:` - a sports failure would have blocked esports CI / collection. Restored the esports step byte-identical; sports runs in its own `sports-offline-tests` job |

Source licences / terms (checked 2026-10-23)

| Source | Status |
|---|---|
| ESPN public site APIs | unofficial / undocumented; no licence for third-party product use; ESPN/Disney terms govern site use. Facts used for settlement and stats; no ESPN editorial text stored. **Blocker: legal review before member display** |
| Kalshi public REST | market data readable without a key; developer agreement / data terms NOT read (429 from kalshi.com). **Blocker: display rights unverified** |
| PrizePicks partner-api / Underdog public board | no published third-party API terms; same posture as the live esports readers. PrizePicks payouts in-app only (unverified) |
| The Odds API | terms (2026-08-31): commercial display allowed when not the primary product; no redistribution; storage + model training allowed. Needs `ODDS_API_KEY` (not set) |
| nflverse-data | CC-BY-4.0 (attribution) - offline dispersion fit input |
| sportsdataverse NBA box scores | MIT code; data ESPN-derived (ESPN terms) - offline fit input |
| Pinnacle guest API | unofficial; geo-refused from the US; not used |
| Repos cited in sports/README.md | as listed there (no code copied; AGPL repo method only) |

## Live recorded data (3,656 NBA / 3,247 NFL forecasts)

**Not reachable from here; not in git; not on the production disk.**
- Git: no `.db` / `.jsonl` / data file under `sports/` on any of the 35 remote refs; `sports/.gitignore` excludes
  `sports/.data/`.
- Render disk: the read-only admin listing `/admin/disk` (2026-10-23 ~20:35 UTC) shows `/data` with no `sports/`
  folder (top entries .cache, picks.db, lol/center, comparison_snapshots.jsonl, ...). The production server never
  ran sports code (server.py on main has no sports import; render.yaml has no sports service). The read-only
  `/admin/lol/query.json` endpoint opens only the two LoL databases (and its authorizer refuses ATTACH).
- Local machine: no `sports.db`, `lines.jsonl` or `lines_state_*.json` under the repo, Downloads, OneDrive,
  Documents or the session temp folders.
- Therefore the rows live in the originating cloud session's working copy - `sports/.data/sports.db` (and
  `sports/.data/lines.jsonl`) of the `claude/dreamy-wozniak-kefq4r` checkout on that session's VM (it says "checked
  from an Ohio server"). That session is not reachable from here. The numbers (and the "8.6-point gap") are
  unverified observations. To recover: export that file from the session (or re-run `record`), then
  `python -m sports.legacy <path/to/sports.db>` imports every row as a `sharp-1` research forecast (tested).
  Those forecasts were all for games not yet played on 2026-10-23, so none was settled.

## Integration changes on top of d393292 (this branch)

- `sports/contracts/` (sc-1), `store.py`, `identity.py`, `team_aliases.json`, `adapters.py`, `capabilities.py`,
  `payouts.py`, `settlement.py`, `coverage.py`, `flags.py`, `worker.py`, `web.py`, `sharp2.py`, `legacy.py`,
  `model_checks.py`, `research/audit_line_shift.py`, `research/results/*`, `AUDIT.md`, `API.md`, this file.
- `server.py`: ONE flag-guarded hook (`Handler._sports_on` + `Handler._sports_route`, one call site in `do_GET`).
- `sports/espn.py` (range fix), `sports/net.py` (jitter), `sports/tests/test_sports_isolation.py` (hook allowance),
  workflow (above).

## Tests

- Sports suite: `python -m pytest sports/tests -q` -> **130 passed** (44 data-branch tests + 86 foundation tests:
  contracts, store, owner-12 contract cases, adapters, worker, web, isolation, payouts, legacy, model properties).
- Esports suite after integration: **1731 passed, 5 skipped** (identical to the base).

## Load test (700 simultaneous members, `scripts/load_sports700.py`, in-process server, fixture upstreams)

Budget set before the run: 0 esports errors; esports p99 within +25% of the base. Each member: 3 esports requests
(+1 sports request when on). Windows workstation, clean runs after socket cool-down.

| Run | Esports requests | 5xx | p50 / p95 / p99 ms | Sports requests | Peak RSS |
|---|---|---|---|---|---|
| base 2fbfc0e | 2,100 | 0 | 110 / 1,052 / 1,097 | - | 94 MB |
| integration, flags OFF | 2,100 | 0 | 105 / 1,017 / 1,080 | - | 94 MB |
| integration, SPORTS_API+UI ON (store of 28k offers) | 2,100 | 0 | 163 / 1,208 / 1,659 | 700 (0 errors, p99 1,202) | 105 MB |
| integration, ON + sports raising on every call | 2,100 | 0 | 126 / 1,062 / 1,524 | 700 (all 503) | 95 MB |

- Flags off: no measurable change (within run-to-run noise). This is the state production keeps.
- **Flags on breaks the +25% esports-p99 budget (+51%)** while the same burst carries 33% more requests. Before an
  enable: serve /api/sports/* from its own process (or behind the existing caches with a request budget) and re-run
  this test against the real staging baseline. An earlier run without single-flight caching pushed RSS to 271 MB
  (466 simultaneous store queries) - fixed by `web._cached` single-flight (tested).

## Release / rollback

- Flags: `SPORTS_UI`, `SPORTS_API`, `SPORTS_INGEST`, `SPORTS_TRAIN` - all default OFF; production stays OFF.
- Rollback: set the flags off (web: no import, routes identical to unknown paths; worker: refuses to run). No
  database restore, no esports rollback. The sports store is a separate file and can be left in place.
- Blockers before any member release: ESPN and Kalshi usage rights (above); a deployed sports worker with its own
  disk budget (measured growth below); settled forward evidence for any model claim; UI / slips / assistant agents.

## Coverage snapshot (real ingestion, scratch store, 2026-10-23 21:15-21:23 UTC)

From `sports/research/results/coverage_run_2026-10-23.json` (`python -m sports.worker coverage --json`). "Gradable" =
passes `capabilities.gate` (supported single-stat market, full game, ordinary line, resolved event + player, a side
offered). Unknown stat labels are typed offers with `stat=None` (handler refuses), not quarantined.

| Platform:league | Offers | Gradable now | Event resolved | Player resolved | Both sides | Over only | Under only |
|---|---|---|---|---|---|---|---|
| prizepicks:nba | 10,606 | 378 | 10,606 | 10,606 | 2,988 | 7,618 | 0 |
| prizepicks:nfl | 6,231 | 357 | 6,231 | 6,229 | 1,624 | 4,607 | 0 |
| underdog:nba | 1,304 | 553 | 1,304 | 1,304 | 1,173 | 131 | 0 |
| underdog:nfl | 3,865 | 518 | 3,865 | 3,864 | 2,662 | 1,202 | 0 |
| kalshi:nba | 2,725 | 2,228 | 2,725 | 2,725 | 2,533 | 151 | 34 |
| kalshi:nfl | 3,354 | 2,966 | 3,354 | 3,354 | 3,299 | 55 | 0 |

- PrizePicks: most lines are goblin / demon alternates (NBA 8,695 of 10,501 on the first read) - typed, never
  ordinary. Quarantined: 1,059 multi-player / non-single-stat projections (reason `multi_player_or_non_single_stat`);
  one NFL line (rush yards -0.5) was quarantined by the first validator and is now accepted (negative lines are real).
- Underdog NFL: 1,357 first-quarter / first-half lines (`period_1_`, `period_1_2_` prefixes) typed with their scope,
  never full game.
- Kalshi: all 6,079 markets read; sharp-2 anchors exclude 963 NBA / 1,972 NFL two-sided markets with no trades and
  no open interest, plus spreads > 5c.
- ESPN: 44 events (NBA 2026-10-22..26, NFL 2026-10-22..30), 58 team rosters -> 2,763 players + memberships, 872
  injury records (status + date only). Verification vs Kalshi settled markets: AUDIT.md section 2.
- The Odds API: not run (no key). Pinnacle: not used (US refusal).
- sharp-2 research forecasts from that store: 1,259 (NBA 701, NFL 558); 547 gradable offers had no eligible
  Kalshi anchor.

## Measured resource numbers (scratch ingestion runs, 2026-10-23 20:56-21:25 UTC)

- One full cycle (ESPN events + rosters for 58 teams, PrizePicks, Underdog, Kalshi 20 series, injuries; NBA + NFL):
  ~36 s per league, run in parallel; worker RSS 110-130 MB; first cycle writes ~50 MB (28,085 offers, 2,763 players,
  2,763 memberships, 872 injury records, 44 events).
- Second cycle 8 minutes later: +5.8 MB (PrizePicks 252 revisions, Underdog 558, Kalshi 1,737 price revisions).
  Estimated steady growth on a full slate: ~10-15 MB/hour at a 10-minute pick'em / 30-minute Kalshi cadence. The
  production esports disk had 1.3 GB free: **the sports worker needs its own volume** (or a much slower cadence);
  `SPORTS_STORE_MAX_MB` (default 1,500) pauses ingestion before it can fill a disk.
