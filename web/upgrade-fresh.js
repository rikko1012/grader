/* Price freshness for the page (owner 2026-11-14): "For an older price, show the source and age beside the number, for example
 * 'DraftKings · checked 42 min ago.' If it's too old to use, exclude it from Best Price, EV, Arbitrage and slip generation. A missing
 * value should never become a zero."
 *
 * Pure helpers, no DOM, so node can test them (tests/test_price_freshness_1114.py) and the board / card can adopt them without
 * touching each other's files. The limits are the server's (gradecore.fair.FRESHNESS_S: sportsbook 30 min, exchange 5 min;
 * price_freshness.py is the server's copy of these rules). Pick'em app lines (PrizePicks / Underdog board lines, direct pick'em
 * readers) are not prices that go stale this way: isFresh() leaves them to their feed's own rules (always true here).
 *
 * A quote can be any shape the server sends: a board record ({offer: {platform, platform_label, seen_at}}), a book row
 * ({platform, platform_label, kind, seen_at}), a Market edge best entry ({book, label, at}), a +EV source ({book, label, kind,
 * seen_at}), a Price check leg ({book, book_label, kind, seen_at}) or a Market edge "bk" row ([label, line, book, over, under, at],
 * read through bkQuote()).
 */
export const LIMITS_S = Object.freeze({sportsbook: 1800, exchange: 300});
export const FUTURE_SLACK_S = 60;
const EXCHANGES = new Set(['kalshi', 'polymarket']);
const PICKEM = new Set(['prizepicks', 'underdog']);
const base = p => String(p ?? '').split(':').pop().toLowerCase();

export function quoteFields(q) {
  const x = q || {}, o = x.offer || {};
  const platform = x.platform ?? x.book ?? o.platform ?? null;
  const seen = x.seen_at ?? x.at ?? x.seen ?? o.seen_at ?? null;
  const label = x.label ?? x.book_label ?? x.platform_label ?? o.platform_label ?? x.source ?? platform ?? '';
  return {platform, seen, label: String(label ?? ''), kind: x.kind ?? null, extraKind: x.extra?.kind ?? null};
}

/* 'pickem' | 'exchange' | 'sportsbook' */
export function quoteKind(q) {
  const f = quoteFields(q);
  if (PICKEM.has(base(f.platform)) || f.kind === 'pickem' || f.extraKind === 'pickem') return 'pickem';
  if (['mid', 'exchange', 'prediction'].includes(f.kind) || EXCHANGES.has(base(f.platform))) return 'exchange';
  return 'sportsbook';
}

/* the time the price was seen, in ms, or null (an ISO string, or epoch seconds / ms) */
export function seenMs(q) {
  const s = quoteFields(q).seen;
  if (typeof s === 'number') return Number.isFinite(s) ? (s > 1e12 ? s : s * 1000) : null;
  if (typeof s !== 'string' || !s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
}

/* seconds since the price was seen, or null when its time is missing (never 0) */
export function ageSeconds(q, nowMs = Date.now()) {
  const t = seenMs(q);
  return t == null || !Number.isFinite(nowMs) ? null : (nowMs - t) / 1000;
}

/* true when this price may be used for Best price, EV, Arbitrage or a generated slip */
export function isFresh(quote, nowMs = Date.now(), limits = LIMITS_S) {
  const kind = quoteKind(quote);
  if (kind === 'pickem') return true;
  const a = ageSeconds(quote, nowMs), lim = (limits || LIMITS_S)[kind];
  return a != null && Number.isFinite(lim) && a >= -FUTURE_SLACK_S && a <= lim;
}

/* 'checked just now' / 'checked 42 min ago' / 'checked 3 h ago' / 'checked 2 d ago'; null when the age is unknown */
export function ageText(sec) {
  if (typeof sec !== 'number' || !Number.isFinite(sec) || sec < -FUTURE_SLACK_S) return null;
  if (sec < 60) return 'checked just now';
  if (sec < 90 * 60) return `checked ${Math.floor(sec / 60)} min ago`;
  if (sec < 48 * 3600) return `checked ${Math.floor(sec / 3600)} h ago`;
  return `checked ${Math.floor(sec / 86400)} d ago`;
}

/* 'DraftKings · checked 42 min ago'; only the source when the time is missing */
export function quoteAgeText(quote, nowMs = Date.now()) {
  const label = quoteFields(quote).label.trim(), words = ageText(ageSeconds(quote, nowMs));
  if (!words) return label;
  return label ? `${label} · ${words}` : words[0].toUpperCase() + words.slice(1);
}

/* a Market edge "bk" row ([label, line, book, over, under, at]) as a quote */
export const bkQuote = row => ({label: row?.[0], line: row?.[1], book: row?.[2], over: row?.[3], under: row?.[4], seen_at: row?.[5] ?? null});

/* the best of `quotes` for a side among the fresh ones only: better(a, b) says whether a beats b. null when none is fresh. */
export function bestFresh(quotes, better, nowMs = Date.now(), limits = LIMITS_S) {
  let best = null;
  for (const q of quotes || []) { if (!isFresh(q, nowMs, limits)) continue; if (best == null || better(q, best)) best = q; }
  return best;
}
