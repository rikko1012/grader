"""
lol.center - the LoL data center (backend only; NOT released to members). Owner, 2026-10-16: no LoL release until the
data center works.

Runs in production as one supervised low-priority process (python -m lol.center.worker, started by server.py, which
never imports lol). It keeps its own SQLite file, DATA_DIR/lol/center/lol.db, separate from every CS2 store and from
the lol scout's cache / history files. Every internal id carries the sport ("lol:player:000123", "lol:team:...",
"lol:series:...", "lol:game:..."), so the same organization in CS2, or its academy, can never match.

- store.py     schema, raw snapshots, source-row revisions, internal ids with source mappings, quarantine
- cargo.py     paced Leaguepedia Cargo client (shared pacer, backoff on rate limits, bot-password login from env)
- ingest.py    explicit fields, deterministic order, bounded pages; checkpointed 14-day windows, newest first
- build.py     canonical series / games / player games (game order from schedule ids; renames; checks)
- coverage.py  actual coverage and gaps by competition, patch and stat
- markets.py   Underdog + PrizePicks LoL offers -> append-only timestamped quote archive
- match.py     offer -> canonical series / team / player, constrained by the schedule
- rules.py     each platform's published settlement rules, with sources and verified flags
- settle.py    exact-scope settlement, versioned (corrections never rewrite a forecast)
- worker.py    the process; writes status / coverage / quarantine / links files for the admin views
"""
SPORT = "lol"
