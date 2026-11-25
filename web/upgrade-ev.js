/* SCOUT_PICKEM_EV (default off): the pick'em +EV board, route #/ev under Tools (competitor spec 4.1).
 * Every number on this page comes from the server (workspace_ev.py, math in gradecore/ev_board.py, fair.py, payouts.py,
 * line_shift.py). The page only filters and sorts what it is given; filters that change the fair chance are sent back as
 * parameters. Saved views live in this browser only (a per-viewer convenience). */
const KEY = 'scout-ev-view';
const VIEW_V = 2;   /* saved-view format: 2 = saved under the 2-independent-books default */
/* the default filters (Reset, Clear all, and the fall-back when a saved view would hide every leg) */
const DEF_P = () => ({preset: 'equal', method: 'worst', min_books: 2, req: [], w: {}, min_liq: 0, max_width: 0.1});
const DEF_F = () => ({q: '', sport: 'all', lo: '', hi: '', quick: '', evOnly: true});
const S = {doc: null, loading: false, error: null, seq: 0, open: new Set(), showModel: false, restored: false, notice: '',
  p: Object.assign({app: 'prizepicks', entry: '', n: 0}, DEF_P()), f: DEF_F()};
let C = null, box = null;
let generatedBatch = null, draftBusy = false, draftMessage = '';
let generating = false, generateMessage = '', slipCount = 4, noRepeats = true;
const PAGE_SIZE = 50;
/* owner 2026-11-20 ("make EV and arbitrage automatic, people shouldn't have to work that hard to get slips"): ready slips come from
   the server's background scan (workspace_auto.py: every verified entry, built from legs that beat it on 2 independent books), and
   the page re-reads both the prices and the ready slips once a minute while it is on screen. Nothing needs pressing. */
const AUTO_MS = 60000;
const A = {doc: null, error: false, busy: '', msg: {}};
let autoTimer = 0;
/* a card's message follows its slip (book rows + sides), not its position, so a re-read never shows it under another slip */
const sigOf = s => s.platform + ':' + s.entry_type + ':' + s.legs.map(l => l.book + '|' + l.row_id + '|' + l.side).join(',');
async function fetchAuto() {
  if (!C.autoOn) return;
  try { A.doc = await C.api('auto-slips'); A.error = false; }
  catch (e) { A.error = true; }
  render();
}
const onScreen = () => !!box && box.isConnected && !box.closest('[hidden]') && !document.hidden;
const typing = () => { const a = document.activeElement; return !!a && box.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName); };
function tick() {
  if (!onScreen() || typing()) return;
  if (!S.loading && !generating && !draftBusy) fetchDoc();
  fetchAuto();
}
let pageKey = '', marketPage = 0, modelPage = 0;
function pager(kind, page, count) {
  if (count <= PAGE_SIZE) return '';
  return `<nav class="ev-pages" aria-label="${kind === 'market' ? 'Market priced' : 'No book price'} pages"><button data-ev="page" data-kind="${kind}" data-step="-1"${page === 0 ? ' disabled' : ''}>Previous</button><span role="status">${page * PAGE_SIZE + 1} &ndash; ${Math.min((page + 1) * PAGE_SIZE, count)} of ${count}</span><button data-ev="page" data-kind="${kind}" data-step="1"${(page + 1) * PAGE_SIZE >= count ? ' disabled' : ''}>Next</button></nav>`;
}

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (v && v.p && v.f) {
      /* owner 2026-11-12: a view saved before 2 independent books became the default carries the old default (1 book) and the
         old opening entry; those were never the member's choice, so they give way to today's defaults */
      if (v.v !== VIEW_V) { delete v.p.min_books; delete v.p.entry; delete v.p.n; }
      Object.assign(S.p, v.p); Object.assign(S.f, v.f); S.restored = true; return true;
    }
  } catch (e) { /* storage blocked */ }
  return false;
}
/* owner audit 2026-11-06 (T3): a saved view of Required books FanDuel + Polymarket and Sport NBA opened on "0 of 0 legs". A view
   whose filters (not the market) leave no priced leg is never saved, and never restored: the page opens on the defaults instead. */
const serverFiltered = () => { const p = S.p, d = DEF_P(); return p.req.length > 0 || p.preset !== d.preset || p.min_books !== d.min_books || p.min_liq !== d.min_liq || p.max_width !== d.max_width; };
const clientFiltered = () => { const f = S.f; return f.sport !== 'all' || !!f.q || f.lo !== '' || f.hi !== '' || !!f.quick; };
function filtersHideAll(d) {
  if (!d || d.rows.some(keep)) return false;                 // a priced leg passes the filters (Only picks that beat the entry aside)
  return (clientFiltered() && d.rows.length > 0) || (serverFiltered() && !!(d.hidden && d.hidden.count));
}
function query() {
  const p = S.p, out = new URLSearchParams({app: p.app, entry: p.entry, n: String(p.n), preset: p.preset, method: p.method, min_books: String(p.min_books),
    min_liq: String(p.min_liq), max_width: String(p.max_width)});
  if (p.req.length) out.set('req', p.req.join(','));
  if (p.preset === 'custom') out.set('w', Object.entries(p.w).map(([b, v]) => b + ':' + v).join(','));
  return out.toString();
}
async function fetchDoc() {
  const seq = ++S.seq; S.loading = true; S.error = null; render();
  try {
    let d;
    try { d = await C.api('pickem-ev?' + query()); }
    catch (e) {
      // A cold price build may outlast the request. Retry this read once, never a generation action.
      if (e.name !== 'AbortError' || seq !== S.seq) throw e;
      d = await C.api('pickem-ev?' + query());
    }
    if (seq !== S.seq) return; S.doc = d; S.p.app = d.app; S.p.entry = d.entry; S.p.n = d.n; S.docP = JSON.parse(JSON.stringify(S.p));
    if (S.restored) {
      S.restored = false;
      if (filtersHideAll(d)) {
        Object.assign(S.p, DEF_P()); S.f = DEF_F();
        S.notice = 'Your saved view showed no legs right now, so the default view is shown.';
        S.loading = false; return fetchDoc();
      }
    }
  }
  catch (e) {
    if (seq === S.seq) {
      S.error = S.doc ? 'Prices could not be refreshed.' : 'Prices could not be loaded.';
      // bug #54: the board on screen is the last good copy - its app / entry / filters stay the ones it was asked with
      if (S.doc && S.docP) S.p = JSON.parse(JSON.stringify(S.docP));
    }
  }
  if (seq === S.seq) { S.loading = false; render(); }
}

const esc = s => C.esc(s);
const pct = x => typeof x === 'number' ? (x * 100).toFixed(1) + '%' : '—';
const odds = a => typeof a === 'number' ? (a > 0 ? '+' + a : '−' + Math.abs(a)) : '—';
const evText = x => typeof x === 'number' ? (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x * 100).toFixed(1) + '%' : '—';
const evClass = x => typeof x === 'number' ? (x > 0 ? ' is-pos' : ' is-neg') : '';
const age = s => typeof s !== 'number' ? '—' : s < 90 ? Math.round(s) + 's' : s < 5400 ? Math.round(s / 60) + 'm' : Math.round(s / 3600) + 'h';
const side = s => s === 'over' ? 'Over' : 'Under';
const ENTRY = {power: 'Power', flex: 'Flex', standard: 'Standard'};
const MODEL_EV_HIDDEN = ['nba', 'nfl'];
const REASON = {'different line': 'on another line', 'settlement rules differ': 'settles a sit-out differently from the app', stale: 'older than its limit', 'one-sided': 'one side priced only', wide: 'book cut too large', 'different line too far': 'line too far from the app\'s line',
  'different line and no line-shift shape': 'different line, no shape for this stat', 'no valid quote time': 'no time on the price'};
const reason = r => REASON[r] || (r.startsWith('exchange spread') ? 'buy-sell gap too wide' : r.includes('liquidity') ? 'less money on it than your minimum' : r);

/* QOL 2026-11-07: search ignores case and accents (Jokić = jokic) and matches every word typed, in any order ("reaves points") */
const fold = s => String(s ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const textHas = (text, q) => { const t = fold(text); return fold(q).split(/\s+/).filter(Boolean).every(w => t.includes(w)); };
function quickOk(r) {
  const p = r.basis === 'market' ? r.p_market : r.p_model;
  if (S.f.quick === 'favorites') return typeof p === 'number' && p >= 0.6;
  if (S.f.quick === 'longshots') return typeof p === 'number' && p < 0.5;
  return true;
}
function keep(r) {
  const f = S.f, p = r.basis === 'market' ? r.p_market : r.p_model;
  if (f.sport !== 'all' && r.sport !== f.sport) return false;
  if (f.q && !textHas([r.player, r.team, r.event, r.stat_label].join(' '), f.q)) return false;
  if (f.lo !== '' && !(typeof p === 'number' && p * 100 >= Number(f.lo))) return false;
  if (f.hi !== '' && !(typeof p === 'number' && p * 100 <= Number(f.hi))) return false;
  return quickOk(r);
}
function play(r, sports) {
  return `<b>${esc(r.player)}</b><small>${esc(side(r.side))} ${esc(r.line)} ${esc(r.stat_label || '')}${r.period_label ? ' · ' + esc(r.period_label) : ''}</small>`
    + `<small>${esc(r.event || '')}${r.start_utc ? ' · ' + esc(C.when(r.start_utc)) : ''}${sports > 1 ? ' · ' + esc(C.SPORTS[r.sport] || r.sport) : ''}</small>`;
}
function legOdds(r) {
  if (typeof r.american !== 'number') return '<span class="ev-na">Payout not published</span>';
  const m = typeof r.multiplier === 'number' && Math.abs(r.multiplier - 1) > 1e-9 ? `<small>${esc(r.multiplier)}x pick</small>` : '';
  return `<b>${odds(r.american)}</b><small>needs ${pct(r.be)}</small>${m}`;
}
function addBtn(r) {
  return `<button class="ev-add" data-ev="add" data-id="${esc(r.key)}" data-side="${esc(r.side)}" aria-label="Add ${esc(r.player)} ${esc(side(r.side))} ${esc(r.line)} to slip">+ Slip</button>`;
}
function sourcesHtml(r) {
  const rows = r.sources.map(s => `<tr><td>${esc(s.label)}${s.kind === 'exchange' ? ' <small>exchange</small>' : ''}</td><td class="ev-num">${esc(s.line)}${s.shifted ? ` <small>read at ${esc(r.line)}</small>` : ''}</td>`
    + `<td class="ev-num">${pct(s.p)}</td><td class="ev-num">${s.width == null ? '—' : s.kind === 'exchange' ? (s.width * 100).toFixed(0) + '¢ buy-sell gap' : (s.width * 100).toFixed(1) + '% book cut'}</td>`
    + `<td class="ev-num">${typeof s.liquidity === 'number' ? '$' + Math.round(s.liquidity).toLocaleString() : '—'}</td><td class="ev-num">${age(s.age_s)}</td><td class="ev-num">${esc(s.weight)}</td></tr>`).join('');
  const out = (r.left_out || []).map(d => `<li>${esc(d.label)} ${esc(d.line)}: ${esc(reason(d.reason))}${typeof d.age_s === 'number' ? ' · ' + age(d.age_s) + ' old' : ''}</li>`).join('');
  return `<div class="ev-src"><table><thead><tr><th>Book</th><th>Line</th><th>${esc(side(r.side))} chance</th><th>Book cut</th><th>Money on it</th><th>Age</th><th>Counts as</th></tr></thead><tbody>${rows}</tbody></table>`
    + (out ? `<p class="ev-left">Left out (greyed, never priced):</p><ul class="ev-left-list">${out}</ul>` : '') + '</div>';
}
function generatedHtml() {
  if (!generatedBatch) return '';
  const b=generatedBatch, app=b.options.platform==='prizepicks'?'PrizePicks':'Underdog';
  return `<section class="ev-generated" aria-label="Generated EV slips"><header><h3>${b.slips.length} generated EV slips</h3><p>${esc(app)} · ${esc(b.options.entryType)} · ${b.options.size} picks each · ${esc(C.when(b.created_at))}</p><p>${esc(b.note||'Each entry was rechecked against current offers.')} Generated slips stay here during this visit. Displayed chances are Grader’s model chances.</p></header><div class="ev-generated-grid">${b.slips.map((s,i)=>`<article class="ev-generated-card"><h4>Slip ${i+1}</h4><ol>${s.legs.map(l=>`<li><strong>${esc(l.player)}</strong><span>${esc(side(l.side))} ${esc(l.line)} ${esc(l.stat_label)}</span><small>${esc(C.SPORTS[l.sport]||l.sport)} · ${esc(l.period_label)} · ${esc(l.event_label)}</small><small>Grader ${pct(l.shown_p)}</small></li>`).join('')}</ol><footer><button data-ev="copy-generated" data-id="${i}"${draftBusy?' disabled':''}>Recheck & copy</button>${C.bookLinks?`<button data-ev="open-generated" data-id="${i}"${draftBusy?' disabled':''}>Open in ${esc(app)}</button>`:''}</footer></article>`).join('')}</div><p role="status">${esc(draftMessage)}</p></section>`;
}
function rowHtml(r, sports) {
  const id = r.key + '|' + r.side, open = S.open.has(id);
  return `<li class="ev-row"><div class="ev-cell ev-play" data-label="Play">${play(r, sports)}</div>`
    + `<div class="ev-cell" data-label="App leg">${legOdds(r)}</div>`
    + `<div class="ev-cell ev-hit" data-label="To hit"><b>${pct(r.p_market)}</b></div>`
    + `<div class="ev-cell ev-ev${evClass(r.ev)}" data-label="Average return"><b>${evText(r.ev)}</b></div>`
    + `<div class="ev-cell" data-label="Grader"><span>${pct(r.p_model)}</span></div>`
    + `<div class="ev-cell" data-label="Market"><button class="ev-books" data-ev="books" data-id="${esc(id)}" aria-expanded="${open}">${esc(r.books_label || (r.books + (r.books === 1 ? ' book' : ' books')))} ${open ? '▴' : '▾'}</button></div>`
    + `<div class="ev-cell ev-act">${addBtn(r)}</div>${open ? sourcesHtml(r) : ''}</li>`;
}
function modelRowHtml(r, sports) {
  return `<li class="ev-row ev-model-row"><div class="ev-cell ev-play" data-label="Play">${play(r, sports)}</div>`
    + `<div class="ev-cell" data-label="App leg">${legOdds(r)}</div>`
    + `<div class="ev-cell ev-hit" data-label="Grader"><b>${pct(r.p_model)}</b> <span class="ev-badge">No book price</span></div>`
    /* owner audit 2026-11-06 (T5): an NBA / NFL research chance has not held up on settled results (NFL 65%+ won 55.9%), so a leg
       with no book price shows no return figure there; the server sends none either (workspace_ev.build) */
    + (MODEL_EV_HIDDEN.includes(r.sport) || typeof r.ev_model !== 'number' ? '<div class="ev-cell ev-ev"></div>'
      : `<div class="ev-cell ev-ev${evClass(r.ev_model)}" data-label="Average return on Grader's chance"><b>${evText(r.ev_model)}</b></div>`)
    + `<div class="ev-cell ev-act">${addBtn(r)}</div></li>`;
}
/* T3: every filter that differs from the defaults, as a chip above the list; the x removes that one filter */
function chips(d) {
  const p = S.p, f = S.f, D = DEF_P(), out = [];
  const label = b => ((d.options.books || []).find(x => x.id === b) || {}).label || b;
  p.req.forEach(b => out.push(['req:' + b, label(b) + ' required']));
  if (p.preset === 'sharp') out.push(['quick:sharp', 'Top books only']);
  if (p.preset === 'custom') out.push(['preset', 'Each book counted your way']);
  if (p.min_books !== D.min_books) out.push(['min_books', p.min_books === 1 ? '1 book is enough' : 'At least ' + p.min_books + ' independent books']);
  if (p.min_liq !== D.min_liq) out.push(['min_liq', 'At least $' + Number(p.min_liq).toLocaleString() + ' on an exchange price']);
  if (p.max_width !== D.max_width) out.push(['max_width', 'Book cut up to ' + (p.max_width * 100).toFixed(0) + '%']);
  if (f.sport !== 'all') out.push(['sport', C.SPORTS[f.sport] || f.sport]);
  if (f.q) out.push(['q', 'Search: ' + f.q]);
  if (f.lo !== '') out.push(['lo', 'To hit from ' + f.lo + '%']);
  if (f.hi !== '') out.push(['hi', 'To hit up to ' + f.hi + '%']);
  if (f.quick === 'favorites') out.push(['quick:favorites', 'Favorites']);
  if (f.quick === 'longshots') out.push(['quick:longshots', 'Longshots']);
  return out;
}
function chipsHtml(d) {
  const c = chips(d);
  if (!c.length) return '';
  return `<div class="ev-chips" role="group" aria-label="Active filters"><span>Filters on:</span>${c.map(([k, t]) => `<button class="ev-chip-on" data-ev="unchip" data-id="${esc(k)}" aria-label="Remove filter ${esc(t)}">${esc(t)} <span aria-hidden="true">×</span></button>`).join('')}`
    + (c.length > 1 ? '<button class="ev-link" data-ev="clearall">Clear all</button>' : '') + '</div>';
}
function filtersHtml(d) {
  const p = S.p, f = S.f, o = d.options, books = o.books || [];
  const sel = (id, label, opts, v) => `<label>${label}<select id="ev-${id}">${opts.map(([k, t]) => `<option value="${esc(k)}"${String(k) === String(v) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
  const w = p.preset === 'custom' ? `<fieldset class="ev-weights"><legend>How much each book counts</legend>${books.map(b => sel('w-' + b.id, b.label, o.weight_choices.map(x => [x, x === 0 ? 'Off' : x + 'x']), p.w[b.id] ?? 1)).join('')}</fieldset>` : '';
  return `<details class="ev-drawer"${S.drawer ? ' open' : ''}><summary>Filters</summary><div class="ev-drawer-body">`
    + `<div class="ev-quick" role="group" aria-label="Quick filters">${[['sharp', 'Top books only'], ['favorites', 'Favorites'], ['longshots', 'Longshots']].map(([k, t]) => { const on = k === 'sharp' ? p.preset === 'sharp' : f.quick === k; return `<button data-ev="quick" data-id="${k}" aria-pressed="${on}">${on ? '<span aria-hidden="true">✓ </span>' : ''}${t}</button>`; }).join('')}`
    + `</div>`
    + `<div class="ev-grid">${sel('preset', 'How books count', o.presets.map(x => [x, x === 'equal' ? 'All the same' : x === 'sharp' ? 'Top books count more' : 'Choose for each book']), p.preset)}`
    + sel('min_books', 'Fewest independent books', Array.from({length: o.min_books_max}, (_, i) => [i + 1, String(i + 1)]), p.min_books)
    + sel('min_liq', 'Least money on an exchange price', o.min_liq_choices.map(x => [x, x ? '$' + x.toLocaleString() : 'Any']), p.min_liq)
    + sel('max_width', 'Largest book cut', o.max_width_choices.map(x => [x, (x * 100).toFixed(0) + '%']), p.max_width)
    + `<label>To hit from<input id="ev-lo" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(f.lo)}" placeholder="0"></label>`
    + `<label>To hit up to<input id="ev-hi" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(f.hi)}" placeholder="100"></label></div>`
    + `<fieldset class="ev-req"><legend>Required books (up to 2)</legend>${books.map(b => `<label><input type="checkbox" data-ev-req="${esc(b.id)}"${p.req.includes(b.id) ? ' checked' : ''}${!p.req.includes(b.id) && p.req.length >= 2 ? ' disabled' : ''}> ${esc(b.label)}</label>`).join('') || '<span>No books right now</span>'}</fieldset>${w}`
    + `<div class="ev-drawer-foot"><button data-ev="save">Save as my view</button><button data-ev="reset">Reset</button><span id="ev-saved" role="status"></span></div></div></details>`;
}
/* QOL 2026-11-07: every redraw rebuilt the page and dropped keyboard focus (a press on "1 book ▾", "+ Slip", a filter chip or a
   checkbox left focus nowhere). The control in focus is found again after the redraw. */
const cssEsc = s => window.CSS.escape(String(s));   /* the module's own CSS (its stylesheet text) hides the global CSS */
function focusKey(a) {
  if (!a || !box.contains(a)) return null;
  if (a.id) return '#' + cssEsc(a.id);
  if (a.dataset.evReq) return `[data-ev-req="${cssEsc(a.dataset.evReq)}"]`;
  if (!a.dataset.ev) return null;
  return `[data-ev="${cssEsc(a.dataset.ev)}"]` + (a.dataset.id != null ? `[data-id="${cssEsc(a.dataset.id)}"]` : '') + (a.dataset.side ? `[data-side="${cssEsc(a.dataset.side)}"]` : '');
}
function render() {
  if (!box) return;
  const fk = focusKey(document.activeElement);
  paintBox();
  if (fk && !box.contains(document.activeElement)) { const el = box.querySelector(fk); if (el && !el.disabled) el.focus({preventScroll: true}); }
}
/* review 2026-11-20: the ready slips carry their own build time (ev.as_of) and each slip the time its oldest book price passes the
   price-age rule (expires_at). A copy older than ev.max_age_s, or a slip past expires_at, is never shown - also when the minute
   re-read failed and the page still holds the last answer. */
const past = t => !!t && Date.parse(t) <= Date.now();
function autoPart() {
  const ev = A.doc && A.doc.ev;
  if (!ev || ev.state !== 'ok') return null;
  if (ev.as_of && ev.max_age_s && Date.now() - Date.parse(ev.as_of) > ev.max_age_s * 1000) return null;
  return ev;
}
function autoSlips(d) {
  const ev = autoPart();
  if (!ev) return null;
  const out = [];
  ev.entries.forEach((e, ei) => {
    if (e.app !== d.app) return;
    e.slips.forEach((s, si) => {
      if (past(s.expires_at)) return;
      if (S.f.sport !== 'all' && s.legs.some(l => l.sport !== S.f.sport)) return;
      out.push({e, s, id: ei + ':' + si, nth: si});
    });
  });
  return out.slice(0, 8);
}
function readyLeg(l, sports) {
  return `<li><strong>${esc(l.player)}</strong><span>${esc(side(l.side))} ${esc(l.line)} ${esc(l.stat_label || '')}${l.period_label ? ' · ' + esc(l.period_label) : ''}</span>`
    + `<small>${esc(l.event || '')}${l.start_utc ? ' · ' + esc(C.when(l.start_utc)) : ''}${sports > 1 ? ' · ' + esc(C.SPORTS[l.sport] || l.sport) : ''}</small>`
    + `<small class="ev-ready-num">Books ${pct(l.p_market)} · <b class="${evClass(l.ev).trim()}">${evText(l.ev)}</b> average return${l.books_label ? ' · ' + esc(l.books_label) : ''}</small></li>`;
}
function readyHtml(d) {
  if (!C.autoOn) return '';
  const list = autoSlips(d), app = d.app_label || (d.app === 'prizepicks' ? 'PrizePicks' : 'Underdog');
  const ev = autoPart(), sports = (d.sports || []).length, at = ev && ev.as_of ? C.when(ev.as_of) : '';   /* the part's own build time */
  let body;
  if (!A.doc) body = `<p class="ev-state" role="status">${A.error ? 'Ready slips could not be read. Grader tries again in a minute.' : 'Building ready slips from current prices…'}</p>`;
  else if (!list) body = `<p class="ev-state" role="status">Ready slips could not be built just now. Grader tries again in a minute.</p>`;
  else if (!list.length) body = `<p class="ev-state" role="status">No ${esc(app)} slip can be built right now from legs that beat each entry${S.f.sport !== 'all' ? ' in ' + esc(C.SPORTS[S.f.sport] || S.f.sport) : ''}. Checked ${ev.priced_legs || 0} priced legs${ev.books && ev.books.length ? ' at ' + esc(ev.books.join(', ')) : ''}${at ? ' · ' + esc(at) : ''}. New slips appear here as prices change.</p>`;
  else body = `<div class="ev-ready-grid">${list.map(({e, s, id, nth}) => {
    const m = A.msg[sigOf(s)] || '', busy = !!A.busy;
    return `<article class="ev-ready-card" data-auto="${esc(id)}"><div class="ev-ready-top"><h4>${esc(e.entry_label)} ${e.n}-pick${nth ? ' · option ' + (nth + 1) : ''}</h4>`
      + `<small>each pick needs ${pct(e.break_even)} · ${e.positive_legs} legs beat it</small></div><ol>${s.legs.map(l => readyLeg(l, sports)).join('')}</ol>`
      + `<div class="ev-ready-foot"><button data-ev="auto-add" data-id="${esc(id)}"${busy ? ' disabled' : ''}>Add to slip</button><button data-ev="auto-copy" data-id="${esc(id)}"${busy ? ' disabled' : ''}>Copy</button>`
      + (C.bookLinks ? `<button data-ev="auto-open" data-id="${esc(id)}"${busy ? ' disabled' : ''}>Open in ${esc(app)}</button>` : '')
      + `</div><p role="status">${esc(m)}</p></article>`;
  }).join('')}</div>`;
  return `<section class="ev-ready" aria-label="Ready slips"><div class="ev-ready-head"><h3>Ready slips <small>${esc(app)} · best first${at ? ' · built ' + esc(at) : ''}</small></h3>`
    + `<p>Built for you from legs that beat each entry on at least two independent books at the app's exact line. Each slip is rechecked when you add, copy or open it. The chance beside each leg is the books' chance; Grader's own chance stays on the board.</p></div>${body}</section>`;
}
function autoFind(id) {
  const [ei, si] = String(id).split(':').map(Number), ev = autoPart(), e = ev && ev.entries[ei];
  return e && e.slips[si] && !past(e.slips[si].expires_at) ? e.slips[si] : null;
}
async function autoAct(a, id) {
  const s = autoFind(id); if (!s || A.busy) return;
  const k = sigOf(s);
  A.busy = id; A.msg[k] = 'Checking current offers…'; render();
  try {
    const slip = await C.autoSlip(s);
    if (a === 'auto-add') { await C.useGenerated(slip); A.msg[k] = 'Added. Your ' + (slip.platform === 'prizepicks' ? 'PrizePicks' : 'Underdog') + ' slip now holds these picks.'; }
    else if (a === 'auto-open') { await C.openEV(slip); A.msg[k] = 'Current offers checked. Review the app before submitting.'; }
    else {
      await C.checkEV(slip);
      const text = [`${slip.platform === 'prizepicks' ? 'PrizePicks' : 'Underdog'} ${ENTRY[slip.entry_type] || slip.entry_type} · ${slip.legs.length} picks`,
        ...slip.legs.map(l => `${l.player}: ${side(l.side)} ${l.line} ${l.stat_label} · ${l.period_label} · ${l.event_label}`)].join('\n');
      await navigator.clipboard.writeText(text); A.msg[k] = 'Rechecked and copied. No entry has been placed.';
    }
  } catch (e) { A.msg[k] = (e && (e.doc && e.doc.error || e.message)) || 'A line changed. A new slip is built within a minute.'; fetchAuto(); }
  finally { A.busy = ''; render(); }
}
function generatorHtml() {
  return `<section class="ev-bar" aria-label="Generate EV slips"><label>Slips<select id="ev-slip-count">${[1,2,4,8,12].map(n=>`<option value="${n}"${n===slipCount?' selected':''}>${n} slips</option>`).join('')}</select></label><label><input type="checkbox" id="ev-no-repeats"${noRepeats?' checked':''}> No repeat props</label><button data-ev="generate"${generating||S.loading||S.error||!S.doc?' disabled':''}>${generating?'Checking slips…':'Generate EV slips'}</button><p>Uses this app, entry size, sport and book settings. Only positive-EV standard lines that meet the entry rules; different players and games in each slip.</p><p role="status">${esc(generateMessage)}</p></section>`;
}
function paintBox() {
  const d = S.doc;
  if (!d) { box.innerHTML = head() + generatorHtml() + `<p class="ev-state" role="status">${S.error ? esc(S.error) + ' Use Refresh to try again.' : 'Checking current prices…'}</p>`; return; }
  const sports = (d.sports || []).length, rows = d.rows.filter(r => keep(r) && (!S.f.evOnly || (typeof r.ev === 'number' && r.ev > 0)));
  const model = d.model_only.filter(keep), entries = d.options.entries.filter(e => e.app === d.app);
  const nextKey = JSON.stringify([S.f, d.app, d.entry, d.n]);   /* not as_of: the minute re-read keeps the page a member is on */
  if (nextKey !== pageKey) { pageKey = nextKey; marketPage = modelPage = 0; }
  marketPage = Math.min(marketPage, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1));
  modelPage = Math.min(modelPage, Math.max(0, Math.ceil(model.length / PAGE_SIZE) - 1));
  const sportOpts = [['all', 'All sports'], ...d.sports.map(s => [s, C.SPORTS[s] || s])];
  const banner = d.hidden && d.hidden.text ? `<p class="ev-banner" role="status">${esc(d.hidden.text)} Old, one-sided or wide prices never set a chance.</p>` : '';
  box.innerHTML = head()
    + `<div class="ev-apps" role="group" aria-label="App">${(d.apps || [d.app]).map(a => `<button data-ev="app" data-id="${a}" aria-pressed="${d.app === a}">${a === 'prizepicks' ? 'PrizePicks' : 'Underdog'} <span class="ev-chip">${d.plays[a] || 0} beat the entry</span></button>`).join('')}</div>`
    + `<div class="ev-bar"><label>Entry<select id="ev-entry">${entries.map(e => `<option value="${e.entry}-${e.n}"${e.entry === d.entry && e.n === d.n ? ' selected' : ''}>${ENTRY[e.entry] || e.entry} ${e.n}-pick · each leg needs ${pct(e.break_even)} (${odds(Math.round(e.american))})</option>`).join('')}</select></label>`
    + `<label class="ev-search">Search<input id="ev-q" type="search" value="${esc(S.f.q)}" placeholder="Player, team or market"></label>`
    + (sports > 1 ? `<label>Sport<select id="ev-sport">${sportOpts.map(([k, t]) => `<option value="${k}"${S.f.sport === k ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>` : '') + `</div>`
    + readyHtml(d)
    + generatorHtml()
    + (S.error ? `<p class="ev-banner ev-err" role="alert">${esc(S.error)} Showing the prices checked at ${esc(C.when(d.as_of))}. Use Refresh to try again.</p>` : '')   // bug #54
    + (S.notice ? `<p class="ev-banner" role="status">${esc(S.notice)}</p>` : '')
    + (d.payout && d.payout.text ? `<p class="ev-banner">${esc(d.payout.text)}</p>` : '') + filtersHtml(d) + chipsHtml(d) + banner
    + generatedHtml()
    + `<section aria-label="Market priced legs"><div class="ev-quick"><button data-ev="evonly" aria-pressed="${S.f.evOnly}">Only picks that beat the entry</button>${S.f.evOnly ? '<span>Turn off to explore every priced line.</span>' : '<span>All priced lines, including those below break-even.</span>'}</div><h3>Market priced <small>${rows.length} of ${d.rows_total} legs${S.f.evOnly ? ' · only picks that beat the entry' : ''}</small></h3>`
    + (rows.length ? `<ol class="ev-list"><li class="ev-head" aria-hidden="true"><span>Play</span><span>App leg</span><span>To hit · need ${pct(d.break_even)}</span><span>Average return</span><span>Grader</span><span>Market</span><span></span></li>${rows.slice(marketPage * PAGE_SIZE, (marketPage + 1) * PAGE_SIZE).map(r => rowHtml(r, sports)).join('')}</ol>${pager('market', marketPage, rows.length)}`
      : `<p class="ev-state">${d.rows_total ? (S.f.evOnly ? 'No leg beats the entry right now with these filters.' : 'No priced leg matches these filters.') : d.params.min_books > 1 ? `No pick'em leg has ${d.params.min_books} independent books on its exact line right now.` : 'No pick\'em leg has a current book price right now.'}${S.f.evOnly && d.rows_total ? ' <button class="ev-link" data-ev="evonly">Show every priced leg</button>' : ''}</p>`)
    + `</section><section aria-label="Model only legs" class="ev-model"><h3>No book price <small>${model.length} legs · no book price to use on this exact line, ranked by Grader's chance</small></h3>`
    + (model.length ? `<button class="ev-link" data-ev="model" aria-expanded="${S.showModel}">${S.showModel ? 'Hide' : 'Show'} legs with no book price</button>${S.showModel ? `<ol class="ev-list ev-list-model"><li class="ev-head" aria-hidden="true"><span>Play</span><span>App leg</span><span>Grader</span><span>Average return on Grader's chance</span><span></span></li>${model.slice(modelPage * PAGE_SIZE, (modelPage + 1) * PAGE_SIZE).map(r => modelRowHtml(r, sports)).join('')}</ol>${pager('model', modelPage, model.length)}` : ''}` : '<p class="ev-state">None right now.</p>')
    + `</section><details class="ev-how"><summary>How this works</summary><p>To hit is the books' chance for this side at the app's exact line, with each book's cut taken out (the least favourable of three ways, so the chance is never flattered). A book counts only when it prices this exact line and settles a player who sits out the way the app does; nothing is moved over from another line. Exchange prices count only when buy and sell are within ${Math.round(d.options.max_spread * 100)}¢. Prices older than ${Math.round(d.options.freshness_s.sportsbook / 60)} minutes (exchanges ${Math.round(d.options.freshness_s.exchange / 60)}) are left out. Books that share one price feed count once, and by default a leg needs two independent books. Lower Fewest independent books to 1 to see legs that rest on a single book; they are marked 1 book.</p>`
    + `<p>Average return is per pick on this entry: chance to hit ÷ chance needed − 1. Grader is Grader's own chance as the board shows it, beside the books' chance and never mixed into it.</p>`
    + `<p>Payouts: ${esc(d.app_label)} table from ${d.payout_source.source ? `<a href="${esc(d.payout_source.source)}" target="_blank" rel="noopener">its help center</a>` : 'its help center'}, read ${esc(d.payout_source.read_at || '')}. The app's own entry screen is final. Grader links to the app and never places an entry.</p></details>`;
}
function head() {
  const d = S.doc;
  return `<div class="h-section-head ev-headline"><div><span class="h-eyebrow">PICK'EM PRICES</span><h2 tabindex="-1">Best prices</h2><p>Pick'em legs whose chance from the books beats what the entry needs.</p></div>`
    + `<div class="ev-refresh"><button data-ev="refresh"${S.loading ? ' disabled' : ''}>${S.loading ? 'Checking…' : 'Refresh'}</button>${d ? `<small>Checked ${esc(C.when(d.as_of))} · updates every minute</small>` : ''}</div></div>`;
}

const CSS = '#u-hub .ev-ready{margin:0 0 16px}#u-hub .ev-ready-head p{color:var(--u-muted);font-size:13px;margin:0 0 10px}'
  + '#u-hub .ev-ready-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:12px}'
  + '#u-hub .ev-ready-card{border:1px solid var(--u-line);border-radius:10px;padding:14px;min-width:0;background:var(--u-panel)}'
  + '#u-hub .ev-ready-card h4{margin:0;font-size:15px}#u-hub .ev-ready-top small{display:block;color:var(--u-muted);font-size:12px}'
  + '#u-hub .ev-ready-card ol{list-style:none;margin:8px 0 0;padding:0}#u-hub .ev-ready-card li{padding:9px 0;border-top:1px solid var(--u-line);overflow-wrap:anywhere}'
  + '#u-hub .ev-ready-card li>*{display:block}#u-hub .ev-ready-card li small{color:var(--u-muted);font-size:12px}#u-hub .ev-ready-num b.is-pos{color:var(--u-good,#16a34a)}'
  + '#u-hub .ev-ready-foot{display:flex;flex-wrap:wrap;gap:8px;padding-top:10px}#u-hub .ev-ready-foot button{min-height:44px;flex:1 1 auto}'
  + '#u-hub .ev-ready-card p[role=status]{margin:6px 0 0;font-size:12px;color:var(--u-muted)}#u-hub .ev-ready-card p[role=status]:empty{display:none}'
  + '#u-hub .ev-generated-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:16px}#u-hub .ev-generated-card{border:1px solid var(--u-line);border-radius:10px;padding:16px;min-width:0}#u-hub .ev-generated-card li{padding:12px 0;border-bottom:1px solid var(--u-line);overflow-wrap:anywhere}#u-hub .ev-generated-card li>*{display:block}#u-hub .ev-generated-card footer{display:flex;flex-wrap:wrap;gap:8px;padding-top:12px}#u-hub .ev-generated-card button{min-height:44px}'
  + '#u-hub .ev-pages{display:flex;align-items:center;justify-content:center;gap:16px;padding:16px 0}#u-hub .ev-pages button{min-height:40px;padding:8px 16px}#u-hub .ev-pages button:disabled{opacity:.45}'
  + '#u-hub .ev-wrap{max-width:1180px}#u-hub .ev-headline{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}'
  + '#u-hub .ev-refresh{display:flex;flex-direction:column;align-items:flex-end;gap:4px}#u-hub .ev-refresh small{color:var(--u-muted)}'
  + '#u-hub .ev-apps{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 10px}#u-hub .ev-apps button{min-height:40px;display:inline-flex;align-items:center;gap:8px}'
  + '#u-hub .ev-apps button[aria-pressed="true"]{border-color:var(--u-accent,#3b82f6);box-shadow:inset 0 0 0 1px var(--u-accent,#3b82f6)}'
  + '#u-hub .ev-chip{font-size:12px;padding:1px 7px;border-radius:999px;background:var(--u-panel-2,rgba(127,127,127,.15))}'
  + '#u-hub .ev-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:0 0 10px}#u-hub .ev-bar label,#u-hub .ev-grid label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--u-muted)}'
  + '#u-hub .ev-bar select,#u-hub .ev-bar input,#u-hub .ev-grid select,#u-hub .ev-grid input{min-height:40px;max-width:100%}#u-hub .ev-search{flex:1 1 200px}'
  + '#u-hub .ev-drawer{border:1px solid var(--u-line);border-radius:9px;background:var(--u-panel);margin:0 0 12px}#u-hub .ev-drawer summary{min-height:42px;display:flex;align-items:center;padding:0 12px;cursor:pointer;font-weight:600}'
  + '#u-hub .ev-drawer-body{padding:4px 12px 12px}#u-hub .ev-quick{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}#u-hub .ev-quick button{min-height:40px}'
  + '#u-hub .ev-quick button[aria-pressed="true"]{border-color:var(--u-accent,#3b82f6);background:rgba(59,130,246,.18);box-shadow:inset 0 0 0 1px var(--u-accent,#3b82f6);font-weight:600}'
  + '#u-hub .ev-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px}#u-hub .ev-chips>span{font-size:12px;color:var(--u-muted)}'
  + '#u-hub .ev-chip-on{min-height:40px;border-radius:999px;padding:0 12px;border:1px solid var(--u-accent,#3b82f6);background:rgba(59,130,246,.12)}'
  + '#u-hub .ev-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}#u-hub .ev-req,#u-hub .ev-weights{border:0;padding:0;margin:10px 0 0;display:flex;flex-wrap:wrap;gap:6px 14px}'
  + '#u-hub .ev-req legend,#u-hub .ev-weights legend{font-size:12px;color:var(--u-muted);margin-bottom:4px}#u-hub .ev-req label{min-height:40px;display:inline-flex;align-items:center;gap:6px}'
  + '#u-hub .ev-drawer-foot{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap}#u-hub .ev-drawer-foot span{color:var(--u-muted);font-size:12px}'
  + '#u-hub .ev-banner{padding:10px 12px;border-radius:8px;border:1px solid var(--u-line);background:var(--u-panel);margin:0 0 12px}#u-hub .ev-err{border-color:var(--u-warn,#d97706)}'
  + '#u-hub .ev-wrap h3{font-size:16px;margin:14px 0 6px}#u-hub .ev-wrap h3 small{font-weight:400;color:var(--u-muted);font-size:12px;margin-left:6px}'
  + '#u-hub .ev-list{list-style:none;margin:0;padding:0;border:1px solid var(--u-line);border-radius:9px;overflow:hidden}'
  + '#u-hub .ev-row,#u-hub .ev-head{display:grid;grid-template-columns:minmax(180px,2.2fr) 1fr 1fr .8fr .8fr 1fr auto;gap:10px;align-items:center;padding:9px 12px;border-top:1px solid var(--u-line)}'
  + '#u-hub .ev-list-model .ev-row,#u-hub .ev-list-model .ev-head{grid-template-columns:minmax(180px,2.2fr) 1fr 1.2fr 1fr auto}'
  + '#u-hub .ev-head{border-top:0;font-size:12px;color:var(--u-muted);background:var(--u-panel)}#u-hub .ev-cell small{display:block;color:var(--u-muted);font-size:12px}'
  + '#u-hub .ev-play b{display:block}#u-hub .ev-ev.is-pos b{color:var(--u-good,#16a34a)}#u-hub .ev-ev.is-neg b{color:var(--u-muted)}#u-hub .ev-na{color:var(--u-muted);font-size:12px}'
  + '#u-hub .ev-books,#u-hub .ev-add,#u-hub .ev-refresh button,#u-hub .ev-drawer-foot button{min-height:40px}#u-hub .ev-badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:999px;border:1px solid var(--u-line);color:var(--u-muted)}'
  + '#u-hub .ev-src{grid-column:1/-1;overflow-x:auto}#u-hub .ev-src table{width:100%;border-collapse:collapse;font-size:13px}#u-hub .ev-src th,#u-hub .ev-src td{text-align:left;padding:4px 6px;border-top:1px solid var(--u-line)}'
  + '#u-hub .ev-src .ev-num{white-space:nowrap}#u-hub .ev-left{margin:8px 0 2px;font-size:12px;color:var(--u-muted)}#u-hub .ev-left-list{margin:0;padding-left:18px;font-size:12px;color:var(--u-muted);opacity:.75}'
  + '#u-hub .ev-state{color:var(--u-muted)}#u-hub .ev-link{background:none;border:0;padding:0 4px;min-height:40px;color:var(--u-accent,#3b82f6);text-decoration:underline;cursor:pointer}'
  + '#u-hub .ev-how{margin:16px 0}#u-hub .ev-how summary{min-height:40px;display:flex;align-items:center;cursor:pointer;font-weight:600}#u-hub .ev-how p{color:var(--u-muted)}'
  + '@media(max-width:719px){#u-hub .ev-head{display:none}#u-hub .ev-row,#u-hub .ev-list-model .ev-row{grid-template-columns:1fr 1fr;gap:6px 12px}'
  + '#u-hub .ev-play{grid-column:1/-1}#u-hub .ev-cell[data-label]:not(.ev-play)::before{content:attr(data-label);display:block;font-size:11px;color:var(--u-muted)}'
  + '#u-hub .ev-act{grid-column:1/-1}#u-hub .ev-act .ev-add{width:100%}#u-hub .ev-headline{flex-direction:column}#u-hub .ev-refresh{align-items:flex-start}#u-hub .ev-bar>label{flex:1 1 100%}}';

function onClick(e) {
  const b = e.target.closest('[data-ev]'); if (!b || !box.contains(b) || b.disabled) return;
  const id = b.dataset.id, a = b.dataset.ev;
  if (a === 'page') { const step = Number(b.dataset.step); if (b.dataset.kind === 'market') marketPage = Math.max(0, marketPage + step); else modelPage = Math.max(0, modelPage + step); return render(); }
  if (a === 'auto-add' || a === 'auto-copy' || a === 'auto-open') return autoAct(a, id);
  if (a === 'copy-generated' || a === 'open-generated') {
    const slip=generatedBatch?.slips[Number(id)];if(!slip||draftBusy)return;
    draftBusy=true;draftMessage='Checking current offers…';render();
    Promise.resolve().then(async()=>{
      await C.checkEV(slip);
      if(a==='open-generated'){await C.openEV(slip);draftMessage='Current offers checked. Review the app before submitting.';}
      else {
        const text=[`${slip.platform==='prizepicks'?'PrizePicks':'Underdog'} ${slip.entry_type} · ${slip.legs.length} picks`,
          ...slip.legs.map(l=>`${l.player}: ${side(l.side)} ${l.line} ${l.stat_label} · ${l.period_label} · ${l.event_label}`)].join('\n');
        await navigator.clipboard.writeText(text);draftMessage='Rechecked and copied. No entry has been placed.';
      }
    }).catch(e=>{draftMessage=e.doc?.error||e.message||'Could not verify this slip. Generate again.';})
      .finally(()=>{draftBusy=false;render();});return;
  }
  if (a === 'generate') {
    generating=true;generatedBatch=null;draftMessage='';generateMessage='Reading current offers and entry rules…';render();
    const sports=S.f.sport==='all'?S.doc.sports:[S.f.sport];
    Promise.resolve(C.generateEV({platform:S.doc.app,entryType:S.doc.entry,size:S.doc.n,count:slipCount,
      sports,noRepeatProps:noRepeats,evQuery:query()})).then(batch=>{generatedBatch=batch;generateMessage=batch.slips.length+' slips generated below.';})
      .catch(e=>{generateMessage=e.message||'Could not verify current offers. Try again.';})
      .finally(()=>{generating=false;render();});return;
  }
  if (a === 'refresh') return fetchDoc();
  if (a === 'app' && id !== S.p.app) { S.p.app = id; S.p.entry = ''; S.p.n = 0; return fetchDoc();   /* the server opens each app on the entry that needs the lowest chance */ }
  if (a === 'books') { S.open.has(id) ? S.open.delete(id) : S.open.add(id); return render(); }
  if (a === 'model') { S.showModel = !S.showModel; return render(); }
  if (a === 'evonly') { S.f.evOnly = !S.f.evOnly; return render(); }
  if (a === 'quick') {                     /* T4: each is a toggle - a second press turns it off, and the button shows it is on */
    if (id === 'sharp') { const on = S.p.preset !== 'sharp'; S.p.preset = on ? 'sharp' : 'equal'; S.p.min_books = on ? Math.max(2, S.p.min_books) : 2; S.drawer = true; return fetchDoc(); }
    if (id !== 'favorites' && id !== 'longshots') return;
    S.f.quick = S.f.quick === id ? '' : id; S.drawer = true; return render();
  }
  if (a === 'unchip') {
    const [k, v] = String(id).split(/:(.*)/), D = DEF_P(), F = DEF_F();
    S.notice = '';
    if (k === 'req') { S.p.req = S.p.req.filter(x => x !== v); return fetchDoc(); }
    if (k === 'quick' && v === 'sharp') { S.p.preset = 'equal'; S.p.min_books = 2; return fetchDoc(); }
    if (k === 'preset') { S.p.preset = D.preset; S.p.w = {}; return fetchDoc(); }
    if (['min_books', 'min_liq', 'max_width'].includes(k)) { S.p[k] = D[k]; return fetchDoc(); }
    if (k === 'quick') { S.f.quick = ''; return render(); }
    if (['sport', 'q', 'lo', 'hi'].includes(k)) { S.f[k] = F[k]; return render(); }
    return;
  }
  if (a === 'clearall') { S.notice = ''; const keepEv = S.f.evOnly; Object.assign(S.p, DEF_P()); S.f = Object.assign(DEF_F(), {evOnly: keepEv}); return fetchDoc(); }
  if (a === 'save') {
    const m = box.querySelector('#ev-saved');
    if (S.loading || JSON.stringify(S.p) !== JSON.stringify(S.docP)) { if (m) m.textContent = 'Wait for the prices to load, then save.'; return; }
    if (filtersHideAll(S.doc)) { if (m) m.textContent = 'This view shows no legs right now, so it was not saved.'; return; }
    let ok = true; try { localStorage.setItem(KEY, JSON.stringify({v: VIEW_V, p: S.p, f: S.f})); } catch (err) { ok = false; }
    if (m) m.textContent = ok ? 'Saved in this browser.' : 'This browser does not allow saving.'; return;
  }
  if (a === 'reset') {
    try { localStorage.removeItem(KEY); } catch (err) { /* storage blocked */ }
    Object.assign(S.p, DEF_P()); S.f = DEF_F(); S.notice = '';
    return fetchDoc();
  }
  if (a === 'add') return C.addLeg(id, b.dataset.side);
}
function onChange(e) {
  const t = e.target, id = t.id || '';
  if(id==='ev-slip-count'){slipCount=Number(t.value);return;}
  if(id==='ev-no-repeats'){noRepeats=t.checked;return;}
  if (t.dataset.evReq) { const b = t.dataset.evReq; S.p.req = t.checked ? [...new Set([...S.p.req, b])].slice(0, 2) : S.p.req.filter(x => x !== b); S.drawer = true; return fetchDoc(); }
  if (id === 'ev-entry') { const [en, n] = t.value.split('-'); S.p.entry = en; S.p.n = Number(n); return fetchDoc(); }
  if (id === 'ev-sport') { S.f.sport = t.value; return render(); }
  if (id === 'ev-lo' || id === 'ev-hi') { S.f[id.slice(3)] = t.value; S.drawer = true; return render(); }
  if (id.startsWith('ev-w-')) { S.p.w[id.slice(5)] = Number(t.value); S.drawer = true; return fetchDoc(); }
  const k = id.slice(3);
  if (['preset', 'method', 'min_books', 'min_liq', 'max_width'].includes(k)) { S.p[k] = ['min_books', 'min_liq', 'max_width'].includes(k) ? Number(t.value) : t.value; S.drawer = true; return fetchDoc(); }
}
function onInput(e) {
  if (e.target.id !== 'ev-q') return;
  S.f.q = e.target.value; const pos = e.target.selectionStart; render();
  const q = box.querySelector('#ev-q'); if (q) { q.focus({preventScroll: true}); try { q.setSelectionRange(pos, pos); } catch (err) { /* type=search */ } }
}

/* ctx: {api, esc, when, SPORTS, addLeg(key, side)} from the hub. paint() is called each time the #/ev section opens. */
export function paint(el, ctx) {
  C = ctx;
  if (box !== el) {
    box = el; box.classList.add('ev-wrap');
    if (!document.getElementById('hub-ev-style')) document.head.insertAdjacentHTML('beforeend', `<style id="hub-ev-style">${CSS}</style>`);
    box.addEventListener('click', onClick); box.addEventListener('change', onChange); box.addEventListener('input', onInput);
    box.addEventListener('toggle', e => { if (e.target.classList?.contains('ev-drawer')) S.drawer = e.target.open; }, true);
    load();
  }
  if (!S.doc && !S.loading) fetchDoc(); else render();
  if (C.autoOn && (!A.doc || Date.now() - Date.parse(A.doc.as_of) > AUTO_MS)) fetchAuto();
  if (S.doc && !S.loading && Date.now() - Date.parse(S.doc.as_of) > AUTO_MS) fetchDoc();
  if (!autoTimer) {
    autoTimer = setInterval(tick, AUTO_MS);
    document.addEventListener('visibilitychange', () => { if (onScreen() && S.doc && Date.now() - Date.parse(S.doc.as_of) > AUTO_MS) tick(); });
  }
}
