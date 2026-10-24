# Sports research data (committed copies)

The forecast log and the lines archive live on one machine's disk (`<SPORTS_DATA_DIR>`). Copies that must outlive that
machine are committed here.

| File | What |
|---|---|
| `forecasts/sharp-1_2026-10-23.jsonl.gz` | The first real forecast log: 6,903 pre-game forecasts (`sharp-1`), recorded 2026-10-23 20:14Z (NBA, 3,656, games 23:00Z Oct 23 - 02:00Z Oct 24) and 20:18Z (NFL, 3,247, games Oct 25 - 27). No settlements yet. Written before the `role` / `book_consensus` columns existed |
| `lines/lines_2026-10-23.jsonl.gz` | The PrizePicks / Underdog lines archive behind it (every line seen, with first / last seen times) |

Restore and settle:
```
python sports_scout.py import-forecasts sports/research/forecasts/sharp-1_2026-10-23.jsonl.gz   # idempotent
python sports_scout.py settle                                                                   # after the games
python sports_scout.py report
python sports_scout.py export-forecasts sports/research/forecasts/sharp-1_<date>.jsonl.gz      # a new copy
```
