# Scout Sports workspace (UI agent, snapshot ui-snap-1)

Member page `/sports` (`web/sports.html` + `web/sports/{shell,stats,app}.js`, `sports.css`; every class `sw-` under
`body.sw`, so it cannot touch esports pages or Slip Studio's `ss-` classes). Behind `SPORTS_UI` (page) and
`SPORTS_API` (data). Hash routes: `#/slate`, `#/board`, `#/player/<player id>?stat=&q=<offer record>`,
`#/game/<event id>`, `#/results`, `#/picks`, `#/slips` (Slip Studio inside the shell; `/sports/slips.html` redirects
there); `?lg=nba|nfl`. Any other hash route shows "Page not found" (never a silent fall-back to the Slate).

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
- Card actions (ux, `web/sports/picks.js` registers `ScoutSportsApp.hooks.renderActions`; board, match view and player card): an Over / Under selector (native radios; an
  unoffered side is disabled with its reason; nothing preselected unless only one side is offered), then "Add to slip"
  and "Track pick" (disabled until a side is chosen) with one line of help.
- Slip tray (ux, `web/sports/tray.js`, hooks `addToSlip` + `SportsTray.render` in app.js): Slip Studio's own draft
  (`ss:draft:<league>:<platform>` + `ss:ctx`, synced across tabs by the `storage` event), shown only when the league has
  picks - side panel at >= 1200 px, bottom bar + sheet below; picks, book, count, remove, local validation hint (entry
  size, one pick per player, two teams, started games), "Open Slip Studio"; one book per draft; only books with picks.
- Board filters (ux, `web/sports/filters.js`, hooks `boardFiltersHtml` / `SportsFilters.matches` / `SportsFilters.bind`):
  search, quick stat buttons (10 most common), game strip, then book / all stats / side / more stats (collapsed on
  phones, choice saved in `scout.sports.ux.v1`), chips with remove + Clear all; values saved per league in
  `scout.sports.filters.v1`. The book list = books with a priced offer on the current board (owner 2026-10-24); a saved
  unpriced book falls back to All books and shows no chip.
- Slip Studio: "Add to slip" writes the slips agent's draft (`localStorage ss:draft:<league>:<platform>`, leg format
  sports/slips/README.md); Slip Studio re-validates on load.

## Pick tracking (pk-1, sports/picks.py) and access (2026-10-24)

- Every pick'em line on the board / player card has "Track pick" after the Over / Under selector; a Slip Studio
  copy that the server validated records its legs (surface `slip_copy`). `POST /api/sports/picks/track` re-checks live
  (current offer revision, open, side offered, fresh, not started, forecast made for this offer, displayed chances =
  the forecast record) and answers per pick in the status toast; the pick is spooled and loaded by the worker
  (`picks-ingest`, every 60 s). `#/picks` ("My picks") lists the member's picks with the exact revision / forecast on
  the Source button, the chance shown, the validation text (sports-gp-1) and the result settled at the pick's own line
  (corrections marked). Stake 0, no grade letters, no returns. Dashboard (ux, `web/sports/picks.js` SportsPicks.renderMine, hook
  `renderPicks`): tabs Open / Settled / Voided (arrow keys), rows with player, stat, side, recorded line, platform,
  tracked time (local + zone), chance shown + source (forecast / model), status, result at its own line, corrections;
  "Your results" (W-L-P, hit rate with n decided) kept separate from "Model validation: n of 300 settled props - not yet
  validated" (per league / model version from the manifests). `/picks/mine` now also returns displayed_source,
  forecast_at, event_id, variant and result.outcome. Admin aggregate: `GET /api/sports/admin/picks`
  (`python scripts/sports_picks_report.py`).
- With no forecasts published the board says so, and Kalshi rows show the market chance (YES bid / ask mid).
- Esports nav link -> `/sports` (server.py, SPORTS_UI); with `SPORTS_UI=owner` only allow-listed members see the page,
  link and token (RUNBOOK section 7). Token refresh: the page and Slip Studio retry once on 401 with a fresh token.

## Known limits

- `ContextSnapshot.to_dict()` overwrites the context's own `kind` with "context" (sc-1); the snapshot reads the kind
  from the stored identity (`source_key`). Contracts owner should fix the field name clash.
- The service sends JSON without compression; board.json is ~0.7 MB per league per load (cached 60 s in the page).
  Gzip in `sports/service.py` (Accept-Encoding) would cut it ~5x.
- Picks are keyed by the token subject (HMAC of SPORTS_TOKEN_SECRET): rotating that secret starts a new "My picks"
  list for every member (the old picks stay in picks.db and in the admin aggregate).
- The owner's browser token is a member token; the admin aggregate is read with the report script (admin token minted
  from the shared secret), not in the page.

## uifix (owner audit 2026-10-24)

- Member wording for raw codes lives in `web/sports/labels.js` (event status, slip exclusions, gate reasons, variants,
  injuries "Out · Shoulder", seasons "2026-27", league search placeholders). Window rows crossing seasons say "spans 2
  seasons" and the finals row, chart and games table mark the season break. The chart no longer prints a value on every
  bar: the finals row is the one list of finals.
- One coverage definition (Slate + Match context): props = markets Scout holds for the game; "lines with Scout
  projection" = book lines carrying a forecast made for that exact line (`coverage.forecasts`).
- Match context highlights Slate in the nav (not Prop board). Slip Studio is `#/slips` (same header, league toggle, nav).
- Player page mount points: `<div id="sw-without">` (sheets agent: `SportsWithout.render(el, player, games, {stat, line,
  league})`); Matchup panel = factor-engine rows (`factors.engine.by_stat[stat]`, keys dvp / dvp_rank / dvp_allowed_pg /
  opp_pos_allowed / opp_pos_rank / opp_pos_allowed_pg / opp_def_rank) + the player's own games vs tonight's opponent.

### Game lines / team context (rendered only when the slate snapshot carries them; nothing blank)

    event.game_lines = {
      "spread":    {"home": -4.5, "away": 4.5, "source": "espn_pickcenter", "source_label": "ESPN (DraftKings)",
                    "observed_at": iso, "history": [[iso, home_spread], ...]},
      "total":     {"line": 221.5, "source_label": ..., "observed_at": iso, "history": [[iso, total], ...]},
      "moneyline": {"home": -180, "away": 150 (American)  OR  "home_p": 0.62, "away_p": 0.38 (Kalshi win chance),
                    "source_label": ..., "observed_at": iso, "history": [[iso, home value], ...]}}
    event.team_context = {"as_of": iso, "source": "...", "home"|"away": {"pace": {"value", "rank", "n"},
      "def_rating": {"value", "rank", "n"}, "def_vs_pos": [{"pos", "stat_label", "rank", "allowed_pg", "n"}]}}

A sparkline is drawn when `history` holds 2+ snapshots.
## Prop board table (board agent, 2026-10-24: rows pr-1, confidence cf-1, hit rates bv-1)

`#/board` opens as a sortable table (default sort: Edge, high first); "Cards" is a toggle saved on the device
(`localStorage scout.sports.board.v1` = {view, sort, dir}). Module `web/sports/table.js` (+ `table.css`, classes
`sw-tb-*`). Desktop: dense table with sortable headers (Player, Scout, Edge (pts), L10; Start time and Player also
in the "Sort by" select that replaces the header on phones). Phones (<= 760 px): each player is one compact block of
two-line rows - no sideways scroll at 360 px. Started games are never listed (the game's `started` flag / status
from the snapshot when present, else `start_utc <= now`). Books without a usable price are not shown (stats.js
`quoteShown`).

Sort indicator (2026-11-04, audit P2): a board where no row has an edge (every market held by its gate) is ordered by
start time, soonest first; the header arrow / `aria-sort` and the phone "Sort by" select then show that order
(`effectiveSort`, a `sw-tb-sortnote` line says so) and the saved preference stays "edge" until an edge appears. Market
column fallback behind `SPORTS_MARKET_FALLBACK` (snapshot `mk.fb`, written by the worker only when the consensus is
None): the listed books' own readings at exactly the headline line, equal-weight log-odds, labelled "books, stale" with
the oldest age in the title. It is a label for an otherwise empty cell - never a lean, edge, gap, confidence or sort
input (`board_view.market_at` / `marketAt` read `c` and `at` only).

**Shared row helper - reuse it, do not re-implement it:** `SportsRows.propRow(market, offer, {fc, view, now})`
(web/sports/table.js; `require()`-able in node) and, server side, `sports/board_view.py` (`row_summary`,
`hit_counts`, file `board_view.json`, `GET /api/sports/board?league=..&part=view`). Sheets (PrizePicks / Underdog /
consistency) use the same functions so a row means the same thing everywhere.

One row = one player + stat + the offer shown (the member's "Show first" book when it prices the market - every book
with a price, Kalshi included - else PrizePicks, then Underdog, then the snapshot's neutral order; never chosen by
chance or edge). A player's props are grouped: one row per player (the best prop under the current sort), "+N more
stats" expands the others in place.

| Column | Definition |
|---|---|
| Lean | **Over** only when Scout's own distribution gives P(Over) > 50% at this line AND the projected mean is above the line; **Under** mirrored; otherwise **No lean**. Market-only records (mc-1, sharp-*, mkt-*) are not Scout's view: "No Scout chance". A lean on a side the book does not offer is shown with "not offered here". |
| Scout | projected mean + unconditional Over / Under % at this line: the offer's own forecast (kind scout / informed) or the same saved distribution read at this line (`lr`). |
| Market | market estimate at THIS line for this offer (`mk.at[record]`, or the headline only when it is at the same line); the lean side's chance (Over when no lean). Never another line's number. |
| Edge (pts) | 100 x (Scout chance of the lean side - market chance of that side at that line). Probability points, not expected value or return; no money claims. Blank without a lean or a market chance. |
| Model vs market gap | badge when abs(Scout P(Over) - market P(Over)) >= 10 points. |
| L10 / L5 | bv-1: last 10 / 5 PLAYED games with a value for the stat (DNP excluded; same filter as `windowSummary`), strict: "↑ over ↓ under", pushes shown separately ("·1P"). When the last 10 span two seasons the seasons are named (NBA "2025-26 + 2026-27"). |
| Conf. | cf-1 below, always labelled **Research** until forward validation passes (no grade letters). |

**Server row signal (sanity agent, owner re-audit 2026-10-24 N1-N4; sports/board_view.py):** the snapshot computes
each quote's row signal and the page renders it (`q.x`): **vg-1** a market whose sm-1 model failed its registered gate
(registry.json; NFL: all five) shows projection + chances only, labelled "NFL model not validated - projection only" -
no lean, edge, gap flag, confidence or edge rank; **sg-1** sanity hold (projection < 25% / > 4x the line for lines >= 2,
or >= 60% of >= 5 strict L10 games on the side opposite the lean) - same suppression, logged with inputs to
`<league>/sanity.json` (GET /api/sports/admin/sanity; its `keys` list = every held quote, read by the slip pool and
tracked picks); **sg-2** (2026-11-05) adds holds for broken inputs at every line size: projection at the model floor
(<= 0.05), a 100% chance shown or raw (>= 99.9%, cal-mi passthrough included), fewer than 3 games with the stat, no game
with the stat in a year, and (lines < 2) a projection under 25% of a line the player cleared in >= 30% of >= 5 L10
games - holds only, no chance or letter of an unheld row changes; **cf-2** = cf-1 + market estimate from one source -> max 2 +
|gap| > 15 pts -> max 2 (stale-input cap still applied by the page at render time); lean additionally needs a POSITIVE
edge for that side when a market chance exists (N4); **edge sort key** = edge, minus 200 for a single-source or
> 15-pt-gap row, so such a row never ranks above a multi-source row. Rows without `q.x` (older snapshots) fall back to
the cf-1 rule below.

**cf-1 confidence (deterministic; thresholds, no weights; display only - never a grade, stake or ranking input):**
no Scout chance -> none; no lean -> 1; otherwise start at 1 and add one step for each threshold met:
Scout chance of the lean side >= 55%, >= 60%, edge >= +3 pts, edge >= +6 pts (the edge steps need a market chance,
so without one the maximum is 3). Then caps, lowest applies: edge < 0 (market prices the lean side higher) -> max 2;
thin history (fewer than 10 played games in the L10 window) -> max 2; stale inputs (forecast older than 24 h, or the
line not re-confirmed by its book within the 15-minute rule) -> max 2; wide distribution (80% range wider than 1.5x
the line) -> max 3; model vs market gap >= 10 pts -> max 3. The tooltip lists the steps and caps that applied. The
thresholds are fixed display buckets, not fitted; they will be checked against settled forward results before the
"Research" label is removed.

Validation state is shown once at the top of the board (per Scout model: settled of 300); model versions, record ids,
the forecast run line and the Source buttons sit behind each card's "Details" toggle.

Images: ESPN headshots (`a.espncdn.com/i/headshots/<lg>/players/full/<ESPN id>.png`) and team logos
(`a.espncdn.com/i/teamlogos/<lg>/500/<ESPN abbr>.png`) only for ESPN ids / abbreviations already in the store
(owner-attested ESPN rights, 2026-10-23); lazy-loaded, 32 px, alt text, initials fallback on error; no other host.

## Compare (compare agent, cmp-1, 2026-10-25)

`#/compare` (nav "Compare" with a count; module `web/sports/compare.js` + `compare.css`, classes `sw-cmp-*`). Members add
up to 6 props (any game, stat, book) with **Compare**: in the Add to slip / Track pick row of board cards and the player
card (app.js `HOOKS.afterActions`), and a small toggle next to the stat on Prop board table rows and PrizePicks / Underdog
sheet rows (standard lines only). Stored per league on the device (`localStorage scout.sports.compare.v1`).
Each compared prop is `SportsRows.propRow` (lean, Scout chance at that line, market chance at that line + n books, edge,
research grade, L5/L10 at that line, cf-2 confidence, projection + 80% range, line/forecast age, sanity hold, NFL label)
plus the existing Add to slip / Track pick selector. **Better line rule (stated on the page):** eligible = lean + positive
edge + that side offered; order edge -> Scout chance of the lean side -> confidence (then player/stat/line/book for
determinism); others "No pick" with the reason. Same player + stat on several books: Over -> lowest line, Under -> highest
(books offering that side; tie -> higher Scout chance), chances from the market's saved distribution. Two props from one
game: "Same game - results are related", never a joint chance. Share link `#/compare?lg=..&c=<platform>~<offer id>~<line>,..`
is checked by `GET /api/sports/compare/check` (sports/api_compare.py, board snapshots only, index cached per file change):
ok / moved (old -> new line) / pulled / started; refused items are listed with what changed. Tests: test_ui_compare.py +
ui_compare_test.js.

## Results layout (rx-1, owner 2026-10-25: "easier to understand, less crowded")

`#/results` (app.js `renderResults`) and `/sports/results` (`results_public.js`) share one renderer
(`SportsResultsPublic.summaryHtml / detailsHtml / bindRecent`, CSS `web/sports/results.css`, classes `rx-*`). One league
at a time (app header toggle; public page NBA / NFL buttons, `?lg=`). Order: validation line ("1,566 settled props ·
under review (needs 300 + passing checks)"; "NFL model not validated" when no league stat passed its sm-1 gate,
`model_gate`) -> Scout's record (settled leans W-L-P, hit rate on decided picks, game-clustered 95% range) -> research
grade tiles A / B / C (hit rate + n) -> one sentence "Scout vs the market on the same props" (the all-stats hh-1 row vs
mc-1, else the row with most shared props; better / worse when the Brier-difference 95% interval excludes 0, "No clear
difference" when it includes 0, "Too few games" without one) -> 10 newest settled leans + "Show more" (10 at a time) ->
Details: every technical table in collapsed `<details>` with a one-sentence plain explanation each; model codes only
there. Data: unchanged endpoints; `results_public.json` gains per league `recent` (newest 50 settled Scout leans, one per
prop, rg-1 grade, player name) and `model_gate`. The in-app tab reads `/api/sports/results/public` for the summary and
`/api/sports/results` for the per-model tables and the ledger; without the public file it says the summary is being
prepared and still shows Details. Tests: test_ui_results.py + ui_results_test.js.
