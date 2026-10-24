# Scout Sports workspace (UI agent, snapshot ui-snap-1)

Member page `/sports` (`web/sports.html` + `web/sports/{shell,stats,app}.js`, `sports.css`; every class `sw-` under
`body.sw`, so it cannot touch esports pages or Slip Studio's `ss-` classes). Behind `SPORTS_UI` (page) and
`SPORTS_API` (data). Hash routes: `#/slate`, `#/board`, `#/player/<player id>?stat=&q=<offer record>`,
`#/game/<event id>`, `#/results`; `?lg=nba|nfl`. Slip Studio is `web/sports/slips.html` (slips agent).

## Data path

Worker (never the web process) builds JSON snapshots from the canonical store:

    SPORTS_INGEST=1 python -m sports.snapshots history nba nfl   # ESPN game logs -> typed records (espn_gamelog)
    SPORTS_INGEST=1 python -m sports.snapshots build nba nfl     # <SPORTS_DATA_DIR>/snapshots/<league>/...

Measured 2026-10-23 on the real scratch store (offers 28k, 1.3k sharp-2 forecasts, 275k history records): build
16 s for both leagues; board.json 0.69 MB (NBA) / 0.58 MB (NFL), board_more 0.6 MB, player files 20-90 KB. History
adds ~300 MB to canonical.db for ~760 players x 2 seasons (re-ingest skips unchanged logs by hash).

The page fetches the token on the esports origin (`GET /api/sports/token`), then `api_base + /api/sports/...` with
`Authorization: Bearer`. Handlers (`sports/api_board.py`, routes in `sports/web.py`) read snapshot bytes only (mtime
cache, bounded 96 files / 48 MB); they never open the store.

| Endpoint (MEMBER, GET, `league=nba|nfl`) | File | Surface |
|---|---|---|
| `/api/sports/manifest` | manifest.json | freshness banner + footer (build time, per-platform last observation, model counts) |
| `/api/sports/slate` | slate.json | Slate + Match context (games, ESPN start + provenance, injuries, coverage) |
| `/api/sports/board[&part=more]` | board.json / board_more.json | Prop board (primary / secondary stats) |
| `/api/sports/forecast&id=` | forecasts.json | one forecast view (proof of identical numbers) |
| `/api/sports/player&id=` | players/<id>.json | Player Workspace: offers, alternates, Kalshi ladder, forecasts, ladder, pmf, factors |
| `/api/sports/history&id=` | history/<id>.json | per-game history with typed StatObservation record ids |
| `/api/sports/results` | results.json | ledger: projection accuracy, probability accuracy, recorded payouts (separate) |

## Rules the surfaces follow

- Board rows group offers only when `capabilities.compatible` would (same event, player, stat, full game, ordinary
  variant); goblin / demon / promotional lines are counted and shown on the player page, never compared.
- A forecast shows on an offer only if made for that offer record, or for an earlier revision with the same line,
  stat, scope and offered sides (flagged with the revision). A moved line shows no chance.
- Chances are the record's unconditional p_over / p_under / p_push, formatted by one function (`stats.js pct`), so
  the board, player, match context, Slip Studio leg and API record agree (browser test). An unoffered side shows its
  chance labelled "not offered" and has no slip button.
- Alternate-line ladder and distribution chart are read from the forecast's own distribution and published only when
  it reproduces the stored chance within 0.5 points (sharp-2 two-rung blends are withheld with the reason).
- Model explanation = the record's reason codes in words; drivers only from a `contributions` field (none recorded by
  sharp-2). Owner factor list: only factors computed from held data (minutes / FGA / 3PA / FTA, targets / receptions /
  carries / pass attempts with L3/L5/L10/season n, rest days, back-to-back, venue, teammates on the injury report); the
  rest are listed as "not in Scout data".
- History windows L5/L10/L20/season + slider L1-L20: n, date window, mean, median, sd, range, strict Over / Under with
  pushes separate and the denominator; DNP games excluded and counted. Splits are labelled descriptive.
- Slip Studio: "+ Over / + Under" writes the slips agent's draft (`localStorage ss:draft:<league>:<platform>`, leg format
  sports/slips/README.md); Slip Studio re-validates on load.

## Known limits

- `ContextSnapshot.to_dict()` overwrites the context's own `kind` with "context" (sc-1); the snapshot reads the kind
  from the stored identity (`source_key`). Contracts owner should fix the field name clash.
- The service sends JSON without compression; board.json is ~0.7 MB per league per load (cached 60 s in the page).
  Gzip in `sports/service.py` (Accept-Encoding) would cut it ~5x.
- Esports pages do not link to `/sports` yet: one flag-guarded link in the esports shell is the service / release
  owner's change.
