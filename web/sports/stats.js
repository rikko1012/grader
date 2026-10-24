/* Scout Sports - pure display functions (no DOM). Shared by every surface so one record always renders the same way.
   Loaded in the page as window.SportsStats and in node tests via require(). */
(function (root) {
  "use strict";

  // ---------------------------------------------------------------- number formats (one place: identical everywhere)
  function pct(p) {                       // unconditional chance -> "48.9%" (0.1% precision); null -> "—"
    if (p === null || p === undefined || !isFinite(p)) return "—";
    return (Math.round(p * 1000) / 10).toFixed(1) + "%";
  }
  function line(x) {                      // 18.5 -> "18.5", 4 -> "4"
    if (x === null || x === undefined || !isFinite(x)) return "—";
    return Math.abs(x - Math.round(x)) < 1e-9 ? String(Math.round(x)) : String(Math.round(x * 10) / 10);
  }
  function num(x, d) {                    // projection / averages
    if (x === null || x === undefined || !isFinite(x)) return "—";
    return Number(x).toFixed(d === undefined ? 1 : d);
  }
  function cents(x) { return x === null || x === undefined ? "—" : Math.round(x * 100) + "¢"; }
  function mult(x) { return x === null || x === undefined ? "" : (Math.round(x * 100) / 100) + "x"; }

  // forecast record (full view or compact board copy) -> the numbers every surface shows
  function forecastNumbers(f) {
    if (!f) return null;
    var full = f.p_over !== undefined;
    return {
      id: f.forecast_id || f.id || null,
      model: full ? f.model_version : f.m,
      at: full ? f.forecast_at : f.at,
      mean: num(full ? f.projection_mean : f.mu, 1),
      over: pct(full ? f.p_over : f.po),
      under: pct(full ? f.p_under : f.pu),
      push: pct(full ? f.p_push : f.pp),
      hasPush: (full ? f.p_push : f.pp) > 0
    };
  }

  // ------------------------------------------------------------------------------------------- outcomes at a line
  function outcome(value, ln) {          // strict: Over if value > line, Under if value < line, Push if equal
    if (value === null || value === undefined) return null;
    if (value > ln) return "over";
    if (value < ln) return "under";
    return "push";
  }

  function median(xs) {
    if (!xs.length) return null;
    var s = xs.slice().sort(function (a, b) { return a - b; });
    var m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function sd(xs) {
    if (xs.length < 2) return null;
    var mu = xs.reduce(function (a, b) { return a + b; }, 0) / xs.length;
    var v = xs.reduce(function (a, b) { return a + (b - mu) * (b - mu); }, 0) / (xs.length - 1);
    return Math.sqrt(v);
  }

  /* games: newest first [{start_utc, participation, values:{stat:v}, season, ...}]
     -> summary of the first n PLAYED games with a value for this stat (n = null: all given) */
  function windowSummary(games, stat, ln, n) {
    var played = games.filter(function (g) {
      return g.participation === "played" && g.values && g.values[stat] !== undefined && g.values[stat] !== null;
    });
    var w = n ? played.slice(0, n) : played;
    var vals = w.map(function (g) { return g.values[stat]; });
    var out = {n: vals.length, requested: n || null, from: w.length ? w[w.length - 1].start_utc : null,
               to: w.length ? w[0].start_utc : null, mean: null, median: null, sd: null, min: null, max: null,
               over: 0, under: 0, push: 0, line: ln, denominator: vals.length};
    if (!vals.length) return out;
    out.mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
    out.median = median(vals);
    out.sd = sd(vals);
    out.min = Math.min.apply(null, vals);
    out.max = Math.max.apply(null, vals);
    if (ln !== null && ln !== undefined) {
      vals.forEach(function (v) { out[outcome(v, ln)] += 1; });
    }
    return out;
  }

  function seasonGames(games) {
    var played = games.filter(function (g) { return g.participation === "played"; });
    if (!played.length) return {season: null, games: []};
    var s = played[0].season;
    return {season: s, games: games.filter(function (g) { return g.season === s; })};
  }

  // splits with sample sizes: key fn -> label
  function splits(games, stat, ln, nextOpponent) {
    var rows = [];
    function add(label, gs) {
      var s = windowSummary(gs, stat, ln, null);
      rows.push({label: label, n: s.n, mean: s.mean, median: s.median, over: s.over, under: s.under, push: s.push});
    }
    add("Home", games.filter(function (g) { return g.home === true; }));
    add("Away", games.filter(function (g) { return g.home === false; }));
    if (nextOpponent && nextOpponent.team_id) {
      add("vs " + (nextOpponent.abbr || "this opponent"), games.filter(function (g) {
        return g.opponent && g.opponent.team_id === nextOpponent.team_id;
      }));
    }
    add("Regular season", games.filter(function (g) { return g.season_type === "regular"; }));
    add("Postseason", games.filter(function (g) { return g.season_type === "postseason"; }));
    return rows.filter(function (r) { return r.n > 0; });
  }

  // ---------------------------------------------------------------------------------------------------- time
  function localTime(iso, opts) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d)) return "—";
    var o = opts || {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                     timeZoneName: "short"};
    try { return new Intl.DateTimeFormat(undefined, o).format(d); } catch (e) { return d.toISOString(); }
  }
  function shortDate(iso) { return localTime(iso, {month: "short", day: "numeric"}); }
  function age(iso, now) {               // "4 min ago", "2 h 5 min ago"
    if (!iso) return "time unknown";
    var s = Math.max(0, ((now || Date.now()) - new Date(iso).getTime()) / 1000);
    if (s < 90) return "just now";
    var m = Math.round(s / 60);
    if (m < 60) return m + " min ago";
    var h = Math.floor(m / 60);
    if (h < 48) return h + " h " + (m % 60) + " min ago";
    return Math.floor(h / 24) + " days ago";
  }
  function ageSeconds(iso, now) {
    if (!iso) return null;
    return Math.max(0, ((now || Date.now()) - new Date(iso).getTime()) / 1000);
  }

  // ------------------------------------------------------------------------------------------------- filters
  var BOOKS = {prizepicks: "PrizePicks", underdog: "Underdog", kalshi: "Kalshi", polymarket: "Polymarket",
               fanduel: "FanDuel", betrivers: "BetRivers", thunderpick: "Thunderpick", oddspapi: "OddsPapi",
               oddsapi: "The Odds API"};
  var FEED_BOOKS = {draftkings: "DraftKings", fanduel: "FanDuel", betmgm: "BetMGM", williamhill_us: "Caesars",
                    caesars: "Caesars", betrivers: "BetRivers", espnbet: "ESPN BET", fanatics: "Fanatics",
                    bovada: "Bovada", betonlineag: "BetOnline", pinnacle: "Pinnacle", ballybet: "Bally Bet"};
  function bookName(p) {                  // "oddsapi:draftkings" -> "DraftKings"
    if (!p) return "";
    if (BOOKS[p]) return BOOKS[p];
    var i = p.indexOf(":");
    if (i > 0) {
      var feed = p.slice(0, i), bk = p.slice(i + 1);
      return FEED_BOOKS[bk] || bk;      // one name per book whatever feed carried it (the source dialog names the feed)
    }
    return p;
  }

  // ------------------------------------------------------------------- market view (mv-1, sports/market_view.py)
  // mk = board row "mk": {l, hp, c: {po, pu, pp, n, used}, bk: [{p, l, kind, c, over, under, t, su, age, in, why}], at}
  function marketFor(mk, rid) {           // consensus for one quote (its own platform excluded), else the headline
    if (!mk) return null;
    if (rid && mk.at && Object.prototype.hasOwnProperty.call(mk.at, rid)) return mk.at[rid];
    return mk.c || null;
  }
  function marketLabel(c) {               // "Market estimate · 3 books" - never "Kalshi market chance"; "" if no book
    if (!c || !c.n) return "";
    return "Market estimate · " + c.n + (c.n === 1 ? " book" : " books");
  }
  // ---------------------------------------------------------------- which numbers are shown (owner 2026-10-24)
  var STALE_S = 900;                      // the frozen 15-minute quote rule (mc-1); older book prices are not shown
  var PICKEM = {prizepicks: 1, underdog: 1};
  function isBook(p) { return !PICKEM[p]; }
  function fresh(t, now) { var a = ageSeconds(t, now); return a !== null && a <= STALE_S; }
  function quoteHasPrice(q) {             // compact board quote: a book row needs a real price of its own
    if (!isBook(q.p)) return true;        // a pick'em line is the line members act on (its price is the payout)
    if (q.k) return q.k[0] !== null && q.k[0] !== undefined && q.k[1] !== null && q.k[1] !== undefined &&
      q.k[1] - q.k[0] <= 0.05 + 1e-9;     // an exchange price is usable only inside the frozen 5-cent spread
    return !!(q.d && (q.d[0] || q.d[1]));
  }
  function quoteShown(q, now) {           // pick'em lines always; a book row only with a usable, fresh price
    return !isBook(q.p) || (quoteHasPrice(q) && fresh(q.t, now));
  }
  function bookUsable(b, now) {           // per-book list entry: counted, or a fresh one-sided price (shown as price)
    if (!fresh(b.t, now)) return false;
    if (b.in) return true;
    return b.kind === "one_sided" && b.why === "not a two-sided priced pair";
  }
  function usableBooks(mk, now) { return mk && mk.bk ? mk.bk.filter(function (b) { return bookUsable(b, now); }) : []; }
  function hiddenBooks(mk, now) { return mk && mk.bk ? mk.bk.filter(function (b) { return !bookUsable(b, now); }) : []; }
  function marketShown(c) { return !!(c && c.n > 0); }
  function pct0(p) {                      // cards: whole-number chances ("45%"); <1% / >99% kept honest
    if (p === null || p === undefined || !isFinite(p)) return null;
    if (p > 0 && p < 0.005) return "<1%";
    if (p < 1 && p > 0.995) return ">99%";
    return Math.round(p * 100) + "%";
  }
  // the three probability types (owner 2026-10-24) - one label per kind, never mixed
  var KIND_LABEL = {scout: "Scout model", informed: "Market-informed model", market: "Market estimate"};
  function kindOf(model) {
    if (!model) return null;
    if (model === "mi-1") return "informed";
    if (model === "mc-1" || model === "sharp-1" || model === "sharp-2" || model.indexOf("mkt-") === 0) return "market";
    return "scout";
  }
  function kindLabel(model) { return KIND_LABEL[kindOf(model)] || ""; }
  function wholeLine(x) { return x !== null && x !== undefined && Math.abs(x - Math.round(x)) < 1e-9; }
  function validationText(settled, target) {      // forward validation progress; grades and stakes withheld
    return "Research estimate · forward validation " + (settled || 0) + " of " + (target || 300) + " settled props";
  }
  function orderQuotes(qs, pref) {        // member's preferred book first; otherwise the snapshot's neutral order
    var rest = qs.slice();
    if (!pref) return rest;
    return rest.filter(function (q) { return q.p === pref; }).concat(rest.filter(function (q) { return q.p !== pref; }));
  }
  var INJ = {out: "Out", questionable: "Questionable", doubtful: "Doubtful", probable: "Probable",
             "day-to-day": "Day-to-day", day_to_day: "Day-to-day", dtd: "Day-to-day", gtd: "Game-time decision",
             "injured reserve": "Injured reserve", ir: "Injured reserve", suspension: "Suspended", suspended: "Suspended",
             "out for season": "Out for season", pup: "PUP list", "not active": "Inactive", inactive: "Inactive"};
  function injuryLabel(s) {               // ESPN status words -> one readable label
    if (!s) return "";
    var k = String(s).trim().toLowerCase();
    return INJ[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  // which kind of number a forecast record is: Scout's own model, or a model read from market prices
  var MARKET_MODELS = {"mc-1": "market consensus", "sharp-2": "Kalshi ladder", "sharp-1": "sharp books"};
  function modelSource(m) {
    if (!m) return null;
    if (m.indexOf("mkt-") === 0) return {kind: "market", label: "Market model · " + bookName(m.slice(4))};
    if (MARKET_MODELS[m]) return {kind: "market", label: "Market model · " + m + " (" + MARKET_MODELS[m] + ")"};
    if (m === "mi-1") return {kind: "scout", label: "Scout mi-1 (market-informed)"};
    return {kind: "scout", label: "Scout " + m};
  }

  var KINDS = {devig: "both sides, no-vig", mid: "midpoint", one_sided: "one side priced only - no chance",
               none: "no price"};
  function dec(x) { return x === null || x === undefined ? "—" : (Math.round(x * 100) / 100).toFixed(2); }
  function bookPrice(b) {                 // the book's own price, as the book gives it
    if (b.p === "kalshi" || b.p === "polymarket") {
      var o = b.over || [];
      return "YES " + cents(o[0]) + " / " + cents(o[1]);
    }
    var parts = [];
    if (b.over) parts.push("O " + dec(b.over));
    if (b.under) parts.push("U " + dec(b.under));
    return parts.join(" · ") || "no price";
  }
  function bookChance(b) {                // the book's own chance of Over at its own line (no push), or why none
    if (b.c === null || b.c === undefined) return b.kind === "one_sided" ? "price only" : "—";
    return pct(b.c);
  }
  function bookStatus(b) {                // in the consensus, or the frozen filter's reason
    return b.in ? "in the market chance" : "not counted: " + (b.why || "not eligible");
  }

  function matchesFilters(row, f) {       // row = decoded board market
    if (f.q) {
      var q = f.q.toLowerCase();
      var hay = (row.n + " " + (row.tm || "") + " " + (row.matchup || "")).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    if (f.stat && f.stat !== "all" && row.st !== f.stat) return false;
    if (f.ev && f.ev !== "all" && row.e !== f.ev) return false;
    var quotes = row.q.concat(row.kn);
    if (f.book && f.book !== "all") quotes = quotes.filter(function (x) { return x.p === f.book; });
    if (!quotes.length) return false;
    if (f.side && f.side !== "any") {
      quotes = quotes.filter(function (x) {
        if (f.side === "over") return x.o[0] === 1;
        if (f.side === "under") return x.o[1] === 1;
        if (f.side === "both") return x.o[0] === 1 && x.o[1] === 1;
        return true;
      });
      if (!quotes.length) return false;
    }
    return true;
  }

  var api = {pct: pct, line: line, num: num, cents: cents, mult: mult, forecastNumbers: forecastNumbers,
             outcome: outcome, median: median, sd: sd, windowSummary: windowSummary, seasonGames: seasonGames,
             splits: splits, localTime: localTime, shortDate: shortDate, age: age, ageSeconds: ageSeconds,
             BOOKS: BOOKS, matchesFilters: matchesFilters, bookName: bookName, marketFor: marketFor,
             marketLabel: marketLabel, bookPrice: bookPrice, bookChance: bookChance, bookStatus: bookStatus,
             KINDS: KINDS, STALE_S: STALE_S, isBook: isBook, fresh: fresh, quoteHasPrice: quoteHasPrice,
             quoteShown: quoteShown, bookUsable: bookUsable, usableBooks: usableBooks, hiddenBooks: hiddenBooks,
             marketShown: marketShown, plural: plural, modelSource: modelSource, pct0: pct0, kindOf: kindOf,
             kindLabel: kindLabel, KIND_LABEL: KIND_LABEL, wholeLine: wholeLine, validationText: validationText,
             orderQuotes: orderQuotes, injuryLabel: injuryLabel};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SportsStats = api;
})(this);
