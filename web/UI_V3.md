# v3 interface ("precision instrument")

The owner approved the v3 design on Nov 19-20 ("Yes that's the right direction"). It is built here behind a server-side flag that
is **off by default**.

- **Design source:** `cs2_scout_ops/logs/design_1119/v3/`
  - `system/SYSTEM.md`, `tokens.css`, `components.css`, `shell.py`, `art/`
  - the specimen
  - the owner shots `owner/01-14`
  - the A/B/C/D/player mocks
- **Display only.** It changes no grading, API, stake or wording rule.
- **With the flag off:** every page, asset and route is byte-for-byte what it was. `tests/test_ui_v3_gate.py` checks this.

## Switches

| Switch | Who | How |
|---|---|---|
| `SCOUT_UI_V3=1` (Render env, read per request, no deploy) | everyone, members and signed-out visitors | unset / `0` = off (the default) |
| `/preview/v3` | the owner only: the admin token (`/preview/v3?token=…`) or the `SPORTS_UI_ALLOW` list (signed in) | sets the HttpOnly cookie `scout_v3` (an HMAC of `ADMIN_TOKEN`, never the token) for this browser, then opens `/#all-sports` |
| `/preview/v3?to=landing` | the owner | the same, then opens `/preview/v3/landing` (the signed-out page in v3) |
| `/preview/v3?off=1` | the owner | clears the cookie |

Rules for anyone who is not the owner:

- A member who sends `scout_v3=1`, a copied value or `?ui=v3` gets today's bytes and ETag.
- `/preview/v3` and `/v3/*` give the unknown-route answer.
- Without `ADMIN_TOKEN` there is no preview at all.

v3 pages always get their own `send_shared` cache key (`…, "v3"`), so their ETag differs from the off page.

## How a page becomes v3

`server.py` → `ui_v3.page(body, name)`, memoised per body object with a single-flight lock. It changes four things:

1. `<html>` gets `data-ui-v3="1" data-v3-page="<name>" data-sc-sport="<sport>"`.
2. Before `</head>` it adds:
   - a theme script (`localStorage['scout-theme']` → `html[data-theme]`, before paint; no stored value follows the OS);
   - `<link rel=stylesheet href=/v3/<bundle>.css>`;
   - Outfit 400/500/600, only when the page loads no Outfit itself.
3. Before `</body>` it adds `<script src=/v3/<bundle>.js defer>`.
4. It forces the UI_NEXT head switch off (`NEXT=false`), so v3 always sits on the current design's hub, never on the 2026-11-11 redesign.

The page files on disk are never edited.

| Page (route) | name | bundle | sport accent |
|---|---|---|---|
| `/` member (CS2 + hub) | `cs2` | member | cs2 |
| `/lol` | `lol` | member | lol |
| `/sports`, `/sports/` | `sports` | member | nba |
| `/sports/results` | `sports-results` | member | nba |
| `/record` (public; needs `SCOUT_PUBLIC_RECORD=1`) | `record` | record | cs2 |
| `/` signed out, `/preview/v3/landing` | `landing` | landing | cs2 |

## Bundles (fixed names; the server concatenates `web/v3/` files in this order)

| Route | Files |
|---|---|
| `/v3/member.css` | tokens, components, shell, **board, slips, picks, player, account, views, faces** (`.css`) |
| `/v3/member.js` | shell, **board, slips, picks, player, account, views, faces** (`.js`) |
| `/v3/record.css` · `.js` | tokens, components, shell, **record** · shell, **record** |
| `/v3/landing.css` · `.js` | tokens, components, shell, **landing** · shell, **landing** |
| `/v3/art/<scene>.svg` | the 19 scenes in `web/v3/art/` (fixed list `ui_v3.ART`) |

Each bundle is rebuilt only when one of its files changes (mtime). JS files are joined with `;`.

**A syntax error in any one JS file stops the whole bundle.** `tests/test_ui_v3_gate.py` runs `node --check` on every bundle. Keep each file one IIFE.

Adding a new file to a bundle is a one-line change to `ui_v3.BUNDLES`. Ask the shell owner first; that tuple is shared.

## File map: who owns what (parallel work must not collide)

| File | Owner | Scope |
|---|---|---|
| `ui_v3.py`, the v3 hooks in `server.py` (`_v3_on`, `_v3`, `_v3_owner`, `_v3_route`, the call sites) | **SHELL** | flag, preview, bundles, injection. Page groups never edit these. |
| `web/v3/tokens.css`, `web/v3/components.css` | **SHELL** (system copy) | Verbatim copies of the approved system. One deliberate change: the sport attribute is `data-sc-sport`, because `web/record.js` and `web/sports/expansion.js` already query `[data-sport]`. Change the system upstream first, then copy. |
| `web/v3/shell.css`, `web/v3/shell.js` | **SHELL** | header, phone bottom bar, More sheet, theme, states, art loader, hub bridge |
| `web/v3/art/*.svg` | **SHELL** | exported unchanged from `system/art/svg` (`export.py`) |
| `web/v3/board.css` · `board.js` | **A boards** | hub board section (`#hub-board-section`: sport tabs, toolbar, rows, By game), LoL and sports boards |
| `web/v3/slips.css` · `slips.js` | **B slips** | Slip Studio (`#hub-studio-section`), the slip column / tray (`.h-slip`, `.h-mobile-slip`), book dialog, share card |
| `web/v3/picks.css` · `picks.js` | **C** | hub My picks, Record, EV, Arbitrage, Sportsbooks sections; line moves / bumps; moneylines in member pages |
| `web/v3/record.css` · `record.js` | **C** | public `/record` page |
| `web/v3/player.css` · `player.js` | **PLAYER** | hub player card (`#hub-card`, `#hub-detail`, `upgrade-cards.js` output) |
| `web/v3/landing.css` · `landing.js` | **D** | signed-out landing |
| `web/v3/account.css` · `account.js` | **D** | member-side account / help / paywall / sign-in states, nav extras |
| `web/v3/views.css` · `views.js` | **VIEWS** | Moneylines (`#/tools?v3=moneylines`: a v3 section in the hub; `/api/moneylines` + `/api/moneyline-games`, the classic tab's rules, read only while open), Line moves and Lines differ (`#/board?v3=moves\|differ`: the board with the hub's own `#hub-movedOnly` / `#hub-diff` on, a note, Views + More entries, each book's line per row in Lines differ). `tests/test_ui_v3_views_browser.py` |
| `tests/test_ui_v3_<group>_browser.py` | each group | its own browser tests (see below); `tests/test_ui_v3_browser.py` + `tests/test_ui_v3_gate.py` are SHELL's |

Existing files (`web/*.html`, `upgrade-*.js/css`, `sports/*`) stay untouched: v3 restyles by CSS and, where markup must change, by
small DOM additions from the group's own JS. Every field the page shows today must stay.

## Writing a page group's CSS / JS

**Scope.**
- Scope every rule under `html[data-ui-v3]`.
- The hub's sheets are added by `upgrade.js` at run time, after the v3 sheet, so equal specificity loses. Prefix hub overrides with `html[data-ui-v3] #u-hub …`. Use `!important` only where the hub itself does.

**Bare tags.**
- The pages style bare tags globally (e.g. `index.html` styles `header` with negative margins and `!important` padding).
- Do not add bare `<header>` or `<nav>` elements. Use `div` with a role, as the shell does.

**Inert.** The open hub makes every other `<body>` child `inert`.
- Put new UI inside `#u-hub`, or call `ScoutV3._keepLive(node)` for a fixed overlay that must stay usable.

**`ScoutV3` API** (from `shell.js`):

| Call | Does |
|---|---|
| `ICON` | the approved icon set |
| `esc` | HTML escaping |
| `state(el, 'loading'\|'empty'\|'error'\|'boot', opts)` | the shared states |
| `art(name)` / `mountArt(el, name)` | inline scene SVG (the CSS needs it inline: `.sc-scene__art>svg`, `.sc-lit__art>svg`) |
| `mapArt('de_dust2')` → `'dust2'` | `''` when there is no scene; never guess |
| `HUE[map]` | the value for `.sc-scene{--h}` |
| `toast(text)` | a toast |
| `setTheme` / `theme` | the theme |
| `setSport(k)` | the accent |
| `onHub(fn)` | runs `fn` when the hub is in the page |

**Map art stays subtle** (dusk haze, as approved). In data cards the CSS hides the grid and landmark layers.

**Data.**
- Use only the page's existing data and controls: no mock data in `web/`, no new API, no new dependency.
- Fonts are Outfit (+ DM Mono where the page already uses it).

**Hard rules** (from SYSTEM.md and the owner):
- "Lean Over" / "Lean Under"; Over and Under spelled out.
- Chances to 0.1%; one stake per line; a missing value is "—", never 0.
- No hedge or status labels. The only approved ones are "Model estimate" and "Book · checked N min ago".
- Benched players are hidden.
- No Results page and no hypothetical returns.
- My Picks stays separate from Public Record.
- The Lab is owner-only.

**Player page restore list** (PLAYER group):
- VRS rank changes (▲/▼)
- the favourite star
- the Lines / Stats / Ask tabs
- the past-series chart
- every v2 prop field
- the 2-map combine (HLTV pooling, `design_1119/v3/player/test_pooled.py`)
- compact map cards
- the combined bar. Never show "Pick 2 maps to combine them" when 2 are picked.

## The shell (what is already done)

**Header (≥1024px).** One `.sc-appbar`:
- 1T + Scout;
- the hub's nav: Board · Slips · EV · Arbitrage · Sportsbooks · My picks · Record · Tools. EV, Arbitrage and Sportsbooks show only when the hub has them, i.e. their flag is on;
- search, the Slip pill and the theme toggle.

**Phone bottom bar (<1024px).** Board · Slip · My picks · Record · More.

**How it drives the hub.**
- Nav items press the hub's own sidebar buttons, so routes, history and paint stay the hub's.
- The current item, the slip count and the alert counts mirror the hub's own elements.
- The hub's sidebar and phone tab bar are hidden under v3. The sidebar keeps a 0-width grid column.
- The hub starts below the 56px header.

**More** (phone bottom-bar tab and a desktop header button).
- One v3 sheet. With the hub in the page, its rows are read on each open from:
  - the hub's own More list (`#hub-more`: Sportsbooks, By game, Tools, Alerts, Auto-build, Tracked slips, Moneylines, Single prop grader, Match research, Classic board);
  - the sidebar's EV and Arbitrage (when their flag is on);
  - the sidebar foot.
- Each row presses that hub control, so it behaves exactly as today. The hub's own More sheet is hidden under v3.
- Without a hub (classic view) the sheet is a short list of plain links: hub routes (By game, Tools, Alerts) and the classic tools (Moneylines, Single prop grader, Match research, Classic board).

**Hub overlays.** `#u-hub` gets `transform: translateZ(0)`, so its fixed layers (player card, popovers) start below the header. The phone card sheet ends above the bottom bar.

**Classic views** (`?view=classic`). The shell does not recolour or re-font the page. It pads the body for the bar and keeps classic sticky headers at `top: 56px`.

**Theme.** Light/dark use the same `scout-theme` key as the hub. With no stored value, the OS setting is followed.

**Public pages.** `record` and `landing` load the `ScoutV3` API and tokens but no member header. Their own header belongs to C and D.

## Testing with the flag on

**Unit / gate.** `pytest tests/test_ui_v3_gate.py`.

**Browser.** `pytest tests/test_ui_v3_browser.py`. Set `V3_SHOTS=<folder>` to keep screenshots. It reuses `tests/test_upgrade_browser.py`'s `browser` / `env` / `context` fixtures, which give a real `server.Handler` with fixture data and a member cookie. To render a page in v3:

```python
from tests.test_ui_v3_browser import v3_page, GROUPS, PHONE, DESK      # GROUPS: board, slips, picks, record-hub, lol, sports, record, landing
def test_my_group(browser, env, monkeypatch):
    c, p = v3_page(browser, env, monkeypatch, "board", PHONE, "light")  # sets SCOUT_UI_V3=1 and the stored theme, waits for the page + ScoutV3
    ...
    c.close()
```

Or set it up by hand:
- call `monkeypatch.setenv("SCOUT_UI_V3", "1")` in any test on the `env` fixture (it is read per request);
- for the theme, add an init script that sets `localStorage['scout-theme']`.

The owner path is `GET /preview/v3?token=<ADMIN_TOKEN>`, then use the cookie (see `test_a_member_cannot_reach_the_preview_and_the_owner_can`).

**Required per group.** 390x844 and 1280, dark and light, with:
- no sideways scroll (`no_h_scroll`);
- no `pageerror`;
- the page's own fields still present.

**Screenshot runs (polish1).** Two switches in `tests/conftest.py`, off by default, act on `tests/test_ui_v3_*` only:
- `V3_FONTS=1` lets Outfit / DM Mono load from Google Fonts (normally aborted) and waits for them before each screenshot, so shots
  compare with the mocks' typography.
- `V3_PROD=1` turns on the member display features production has on (`design_1119/v3/A/data/me_features.json`): `SCOUT_BOARD_H2H`,
  `SCOUT_PARITY_*`, `SCOUT_PICKEM_EV`, `SCOUT_MARKET_EDGE`, `SCOUT_MARKET_TOOLS`, `SCOUT_SOCCER_ENABLED`, `SCOUT_TENNIS_ENABLED`. The
  Edge / H2H / SZN columns, n/N hit counts, per-book O/U prices, EV / Arbitrage nav and the soccer / tennis tabs come from these flags,
  not from v3. With the expansion flags on and no fixture feed, the board shows the hub's own "Source updates" note.

Full set: `V3_SHOTS=<dir> V3_BOARD_SHOTS=<dir> V3_FONTS=1 V3_PROD=1 pytest tests/test_ui_v3_*browser.py tests/test_ui_v3_gate.py`.

**Flag off.** The existing suites stay unchanged and passing.

**Load.**
- `python scripts/load_ui_v3_700.py . 700`: 700 members on pages + bundles + art, flag on and off; fails on any 5xx, a wrong status, or more than one build per page.
- Also run the esports `scripts/load_audit700.py` (with `UPGRADE_LOAD=1 SCOUT_UI_V3=1 … workspace`).
- Run them for any change to `ui_v3.py` or `server.py`.

## CS2 player stats: HLTV only (claude/ui-v3-maps, 2026-11-20)

Owner, Nov 20: "stop using BO3.gg we have HLTV". The v3 CS2 card's hero stat grid, map cards and 2-map combine read only
`GET /api/upgrade/player-maps?sport=cs2&book=&id=` (`workspace_api.get_player_maps` -> `hltv_maps.card_doc`).

- **Data.** His HLTV per-map raw totals, last 12 months and last 3 months (maps, rounds, kills, deaths, headshots + the kills/maps of
  maps with a headshot row, rating/ADR/KAST x rounds, map wins). Exact HLTV id as the projections (`hltv._player_ids`), HLTV-sourced rows only.
- **Gate.** v3 only: with v3 off the route is the unknown-route answer, byte for byte. Members only; the player is the board row's, never the request's.
- **Load.** `srv.shared` per board row, plus one HLTV read per player (`hltv.single_flight`) through 3 read slots. Cached 15 min, with a disk copy.
  A failed read keeps the last good answer; with none, the endpoint answers 503 and the card shows a retry, never "—".
- **Pool.** `hltv_maps.pool_for`: the match's own HLTV veto pool (veto-1), else the most common complete pool of the last 7 days of
  HLTV vetoes, else `hltv_maps.ACTIVE_DUTY`. Update that constant when Valve changes Active Duty.
  Maps outside the pool sit in a closed "Outside the current pool" group, with no combine and no map chance.
- **Display.** Names come from `hltv_maps.NAMES` ("Dust 2", never "de_dust2"). The combine sums the two maps' totals, then divides.
- **Tests.** `tests/test_v3_player_maps.py` (maths, ids, pool, names, cache, 700 concurrent) and `tests/test_ui_v3_player_browser.py` (390 / 1280, every pair).
- **Every tab, HLTV only (owner Nov 20, emphatic: "no BO3.gg data anywhere on member-page stats - HLTV only").** `player.js hltvOnly`
  rewrites the CS2 `/line` doc before the hub paints it (the classic card never runs it): no BO3.gg stats / per-map / teammates / trade,
  opening and rest days / sample flags, no BO3.gg rank band or AWP note, no CS API / FACEIT panels, no "Saved with this forecast" grader
  inputs, no context-only grade factors read from them. The factor table's KPR (3 / 12 mo), HS% (3 mo), maps (12 mo) and days since his
  last map are the player-maps HLTV totals (`cs2_card.hltv_v3`); the rest stake rule stays the grade's own yes / no. Hit counts
  (L5 / L10 / L20, Why, Ask) are HLTV series only: the verified one, else `history.hltv_series` (added by `/line` when HLTV does not
  reproduce the saved L10). The Genie's payload on a v3 request goes through the same rules in `v3_hltv.scrub`. Grades, chances, stakes and
  projections are untouched. Test: `tests/test_v3_hltv_only_browser.py` (real stadodo docs, every tab, 390 / 1280, deny-set from the doc's
  BO3.gg fields).

## Card pictures (`faces`, owner 2026-11-20: "Add player and team pictures to cards also")

- **Files:** `web/v3/faces.css` · `faces.js` (last in the member bundle), `img_cache.py`, `img_index.py`, `sports/expansion_images.py`.
  Shared lines touched: one `ui_v3.BUNDLES` entry each for member.css / member.js, one `server.py` dispatch line + `Handler._img_route`,
  one line in `workspace_expansion._build` (keeps the sports service's `images` map beside the board), and the research body /
  collectors in `sports/expansion_board.py`.
- **Routes (v3 on only; off = the unknown-route answer):** `/img/<p|t>/<sport>/<token>.webp` (stored files only, public, 30-day
  immutable cache + ETag, 404 for 60 s when not ready) and `/img/index/<sport>.json` (members; which board players have a READY picture).
- **Sources, exact ids only:** NBA / NFL ESPN athlete id + team abbreviation, soccer / tennis ESPN athlete id where resolved, then the
  PrizePicks picture of the offer's own player id (shared art and placeholders dropped). Hosts: `a.espncdn.com`, `static.prizepicks.com`.
- **CS2: no pictures (owner 2026-11-20 "stop using BO3.gg, we have HLTV").** BO3.gg is not a picture source, and the HLTV scraper
  DB stores no picture URLs (`players`: playerid, name; `teams`: teamid, name, country), so CS2 cards keep the hub's monogram and
  show no team logo. No `/img/*/cs2/*` route, no CS2 list, nothing registered or stored; `faces.js` never asks for one.
  LoL: monograms until the owner approves lolesports images.
- **Desktop rows:** where `board.js` already drew its initial (`.sc-bd-av`) and that sport has pictures, `faces.js` replaces it with
  the picture box (never two avatars). Phone rows show no avatar (polish2, mock 06); the card hero, slip legs and My picks do.
- **Never on a member request:** one warmer thread fetches (paced, https, no redirects, 4 MB / 4.2 MP caps; the pixel cap is read
  from the header before any decode, JPEGs decode at reduced scale), re-encodes WEBP (160 px headshot, 128 px logo on its own
  contrast disc) and writes atomically under `.cache/img` (20k files / 200 MB bound).
- **The picture list never makes a member wait:** one request rebuilds a sport's list; every other request gets the last list (an
  empty one before the first build) at once. Stored pictures move the list at most every 30 s while the warmer works through its
  queue, and at once when it is idle.
- **Which card gets which picture (review 2026-11-20):** the card's own sport (row / card record key, leg or Auto-build sport tag,
  receipt sport) and only that sport's list; LoL or an unknown sport keeps the monogram. The open card uses its exact player id
  (its line doc, or the NBA / NFL hotlink id) or nothing; an id without a ready picture keeps the monogram. Rows, legs and Auto-build
  legs without an id use that sport's own board (a name only when one board player has it).
- **Load:** `python scripts/load_v3_images_700.py . 700` (cold / warm / off phases, slow stub upstream).
