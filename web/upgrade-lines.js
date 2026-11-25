/* Many lines for one player and stat (owner 2026-11-11: "these books have many lines for the same player and it sometimes can get
 * confusing to find them"). Pure grouping for the board: every line of one event + player + stat + period is one board row with a
 * ladder of its lines. Nothing here calculates a grade, chance or price: the records are the server's, only their order and the row
 * they sit in are decided here. No DOM, so node can test it (tests/test_board_lines.py).
 */
const num = v => typeof v === 'number' && Number.isFinite(v);
const PICKEM = new Set(['prizepicks', 'underdog']);
const pickem = r => PICKEM.has(r.offer.platform) || r.extra?.kind === 'pickem';
const prices = r => ['over','under'].map(s => r.offer[s]?.price).filter(p => num(p) && p > 1)
  .concat((r.offer.reported_prices || []).filter(p => num(p) && Math.abs(p) >= 100).map(p => p < 0 ? 1 + 100 / -p : 1 + p / 100));
// Presentation only. A price never proves that a provider calls a line its main line.
export function bookLineRank(r) {
  if (pickem(r)) return 0;
  if (prices(r).some(p => p <= 1.2)) return 3; // -500 or shorter: never the default headline ahead of normal prices.
  return r.market.line_type === 'standard' ? 0 : ['alternate','ladder rung'].includes(r.market.line_type) ? 2 : 1;
}
const balance = r => { const ps = prices(r); return ps.length ? Math.max(...ps.map(p => Math.abs(1 / p - 0.5))) : Infinity; };

export const propKey = r => [r.sport, r.event.id, r.player.key, r.market.stat, r.market.period].join('|');
export const playerKey = r => [r.sport, r.event.id, r.player.key].join('|');
export const lineId = r => [r.sport, r.offer.platform, r.offer.offer_id].join('|');

/* The line a row shows when the sort does not rank lines itself (start time, player name): a graded line first, then a line with a
   Grader side, then a PrizePicks / Underdog line (the ones a slip can hold), then a standard line, then the line nearest the
   projection. Lower sorts first. */
export function mainRank(r) {
  const s = r.scout || {}, line = Number(r.offer.line);
  return [bookLineRank(r), s.grade && !s.hold ? 0 : 1, s.side ? 0 : 1, PICKEM.has(r.offer.platform) ? 0 : 1, r.market.line_type === 'standard' ? 0 : 1,
    num(s.projection) && Number.isFinite(line) ? Math.abs(line - s.projection) : Infinity];
}
const cmp = (a, b) => { for (let i = 0; i < a.length; i++) { if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1; } return 0; };
export function pickMain(lines, ranked) {
  if (!lines.length) return null;
  if (lines.every(r => !pickem(r))) {
    // In a sportsbook view, a high chance at a short price must not replace the main line.
    // Where no main classification exists, show the most balanced quote, without calling it main.
    return lines.slice().sort((a,b) => bookLineRank(a)-bookLineRank(b) || balance(a)-balance(b))[0];
  }
  if (ranked) return lines[0];                          /* the sort ranked the lines: the row shows the one that put it there */
  let best = lines[0], rank = mainRank(best);
  for (let i = 1; i < lines.length; i++) { const k = mainRank(lines[i]); if (cmp(k, rank) < 0) { best = lines[i]; rank = k; } }
  return best;
}

/* The ladder: every line by line value, then the app's name, then the line type. */
export function ladder(lines) {
  return lines.slice().sort((a, b) => bookLineRank(a) - bookLineRank(b) || Number(a.offer.line) - Number(b.offer.line)
    || String(a.offer.platform_label || a.offer.platform).localeCompare(String(b.offer.platform_label || b.offer.platform))
    || String(a.market.line_type).localeCompare(String(b.market.line_type)));
}

/* The best line for a side, by the rule the card's "Best line per side" already states: the lowest line for the Over, the highest for
   the Under; at the same line the higher listed price. Only lines whose app offers that side count. One line on offer = nothing to
   mark. */
export function bestFor(lines, side) {
  if (side !== 'over' && side !== 'under') return null;
  /* H6: only even-payout pick'em lines compare by line value (PrizePicks / Underdog standard): a sportsbook or prediction market's far
     alt line with no price was marked "BEST UNDER". Lines that are not comparable are never marked. */
  const offered = lines.filter(r => r.offer[side] && r.offer[side].offered && Number.isFinite(Number(r.offer.line))
    && (r.offer.platform === 'prizepicks' || r.offer.platform === 'underdog') && (r.market.line_type || 'standard') === 'standard' && !r.extra);
  if (offered.length < 2) return null;
  const price = r => { const o = r.offer[side]; return num(o.price) ? o.price : -Infinity; };
  let best = offered[0];
  for (const r of offered.slice(1)) {
    const d = Number(r.offer.line) - Number(best.offer.line);
    if ((side === 'over' ? d < 0 : d > 0) || (d === 0 && price(r) > price(best))) best = r;
  }
  return best;
}

/* sorted: the board's filtered, sorted records. universe: every current record of the board (same source, before the filters), so a
   row's ladder lists every line of its prop and says how many the filters matched. ranked: the sort orders lines by their own
   value. Rows keep the order of their first matched line. */
export function groupProps(sorted, universe, ranked) {
  const all = new Map();
  for (const r of universe) { const k = propKey(r); let a = all.get(k); if (!a) all.set(k, a = []); a.push(r); }
  const groups = new Map();
  for (const r of sorted) { const k = propKey(r); let g = groups.get(k); if (!g) groups.set(k, g = { key: k, player: playerKey(r), matched: [] }); g.matched.push(r); }
  const out = [];
  for (const g of groups.values()) {
    const every = all.get(g.key) || g.matched, ids = new Set(g.matched.map(lineId));
    g.main = pickMain(g.matched, ranked);
    g.lines = ladder(every.length >= g.matched.length ? every : g.matched);
    g.match = ids;
    g.total = g.lines.length;
    g.values = new Set(g.lines.map(r => Number(r.offer.line))).size;
    g.books = new Set(g.lines.map(r => r.offer.platform)).size;
    g.best = bestFor(g.lines, g.main.scout && g.main.scout.side);
    out.push(g);
  }
  return out;
}

/* Maps scopes longest first (owner 2026-11-12: Maps 1-2 lines before Map 1 lines; LoL BO5 Maps 1-3 first); other periods keep their order. */
const PERIOD_RANK = new Map([['maps13', 0], ['maps12', 1], ['map1', 2]]);
export const periodRank = p => (PERIOD_RANK.has(p) ? PERIOD_RANK.get(p) : 3);

/* Rows by player: each player once (in the order of their first row), their props under them by stat then period. */
export function groupPlayers(groups) {
  const players = new Map();
  for (const g of groups) { let p = players.get(g.player); if (!p) players.set(g.player, p = { key: g.player, first: g.main, props: [] }); p.props.push(g); }
  for (const p of players.values()) {
    const order = new Map();
    p.props.forEach((g, i) => { const k = g.main.market.stat; if (!order.has(k)) order.set(k, i); });
    p.props.sort((a, b) => order.get(a.main.market.stat) - order.get(b.main.market.stat) || periodRank(a.main.market.period) - periodRank(b.main.market.period) || String(a.main.market.period).localeCompare(String(b.main.market.period)));
    p.lines = p.props.reduce((n, g) => n + g.total, 0);
  }
  return [...players.values()];
}
