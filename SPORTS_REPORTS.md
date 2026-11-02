# Sports reports from the local Scout project

Run `python scripts/sports_read_reports.py` with the existing `ADMIN_TOKEN` in
the process environment. Do not put tokens in prompts, command arguments, git,
or the report folder. This uses the existing authenticated Scout token issuer;
it creates no persistent credentials and changes no permissions.

Reports are saved under ignored `output/sports-reports/`. Read `manifest.json`
first: only entries marked `ok: true` were refreshed during this run. Older files
may remain after a failed request; do not present them as newly retrieved.

- `nba-results.json`, `nfl-results.json`: existing results-ledger snapshots.
- `results-summary.json`: existing historical results summary for both leagues.
- `gradecheck.json`: saved daily check, existing containment and recorded decisions.
- `learning.json`: saved scorecard and existing validation status.
- `operations.json`: scheduler outcomes, memory, feed freshness and alerts.

This downloads the reports the service already exposes, not an exhaustive database
backup. Preserve source timestamps and the reports' population definitions.
Missing snapshots and HTTP failures remain explicit. Do not infer that a fetch
time is a data-update time. No training, settlement, recalculation, model
promotion, betting action or containment change is performed.

If authentication is unavailable locally, stop and configure existing authorized
access once. Do not ask the owner to repeatedly run report commands in Render,
do not scrape credentials, and do not weaken endpoint authentication.
