/* SCOUT_PICKEM_EV (default off): the pick'em +EV board, route #/ev under Tools (competitor spec 4.1).
 * Every number on this page comes from the server (workspace_ev.py, math in gradecore/ev_board.py, fair.py, payouts.py,
 * line_shift.py). The page only filters and sorts what it is given; filters that change the fair chance are sent back as
 * parameters. Saved views live in this browser only (a per-viewer convenience). */
const KEY = 'scout-ev-view';
const S = {doc: null, loading: false, error: null, seq: 0, open: new Set(), showModel: false,
  p: {app: 'prizepicks', entry: 'power', n: 2, preset: 'equal', method: 'worst', min_books: 1, req: [], w: {}, min_liq: 0, max_width: 0.1},
  f: {q: '', sport: 'all', lo: '', hi: '', quick: '', evOnly: true}};
let C = null, box = null;

function load() {
  try { const v = JSON.parse(localStorage.getItem(KEY) || 'null'); if (v && v.p && v.f) { Object.assign(S.p, v.p); Object.assign(S.f, v.f); return true; } } catch (e) { /* storage blocked */ }
  return false;
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
  try { const d = await C.api('pickem-ev?' + query()); if (seq !== S.seq) return; S.doc = d; S.p.app = d.app; S.p.entry = d.entry; S.p.n = d.n; }
  catch (e) { if (seq === S.seq) S.error = 'Prices could not be loaded.'; }
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
const REASON = {stale: 'older than its limit', 'one-sided': 'one side priced only', wide: 'book cut too large', 'different line too far': 'line too far from the app\'s line',
  'different line and no line-shift shape': 'different line, no shape for this stat', 'no valid quote time': 'no time on the price'};
const reason = r => REASON[r] || (r.startsWith('exchange spread') ? 'buy-sell gap too wide' : r.includes('liquidity') ? 'less money on it than your minimum' : r);

function quickOk(r) {
  const p = r.basis === 'market' ? r.p_market : r.p_model;
  if (S.f.quick === 'favorites') return typeof p === 'number' && p >= 0.6;
  if (S.f.quick === 'longshots') return typeof p === 'number' && p < 0.5;
  return true;
}
function keep(r) {
  const f = S.f, p = r.basis === 'market' ? r.p_market : r.p_model;
  if (f.sport !== 'all' && r.sport !== f.sport) return false;
  if (f.q && ![r.player, r.team, r.event, r.stat_label].join(' ').toLowerCase().includes(f.q.toLowerCase())) return false;
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
function rowHtml(r, sports) {
  const id = r.key + '|' + r.side, open = S.open.has(id);
  return `<li class="ev-row"><div class="ev-cell ev-play" data-label="Play">${play(r, sports)}</div>`
    + `<div class="ev-cell" data-label="App leg">${legOdds(r)}</div>`
    + `<div class="ev-cell ev-hit" data-label="To hit"><b>${pct(r.p_market)}</b></div>`
    + `<div class="ev-cell ev-ev${evClass(r.ev)}" data-label="Average return"><b>${evText(r.ev)}</b></div>`
    + `<div class="ev-cell" data-label="Scout"><span>${pct(r.p_model)}</span></div>`
    + `<div class="ev-cell" data-label="Market"><button class="ev-books" data-ev="books" data-id="${esc(id)}" aria-expanded="${open}">${r.books} ${r.books === 1 ? 'book' : 'books'} ${open ? '▴' : '▾'}</button></div>`
    + `<div class="ev-cell ev-act">${addBtn(r)}</div>${open ? sourcesHtml(r) : ''}</li>`;
}
function modelRowHtml(r, sports) {
  return `<li class="ev-row ev-model-row"><div class="ev-cell ev-play" data-label="Play">${play(r, sports)}</div>`
    + `<div class="ev-cell" data-label="App leg">${legOdds(r)}</div>`
    + `<div class="ev-cell ev-hit" data-label="Scout"><b>${pct(r.p_model)}</b> <span class="ev-badge">No book price</span></div>`
    + `<div class="ev-cell ev-ev${evClass(r.ev_model)}" data-label="Average return on Scout's chance"><b>${evText(r.ev_model)}</b></div>`
    + `<div class="ev-cell ev-act">${addBtn(r)}</div></li>`;
}
function filtersHtml(d) {
  const p = S.p, f = S.f, o = d.options, books = o.books || [];
  const sel = (id, label, opts, v) => `<label>${label}<select id="ev-${id}">${opts.map(([k, t]) => `<option value="${esc(k)}"${String(k) === String(v) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
  const w = p.preset === 'custom' ? `<fieldset class="ev-weights"><legend>How much each book counts</legend>${books.map(b => sel('w-' + b.id, b.label, o.weight_choices.map(x => [x, x === 0 ? 'Off' : x + 'x']), p.w[b.id] ?? 1)).join('')}</fieldset>` : '';
  return `<details class="ev-drawer"${S.drawer ? ' open' : ''}><summary>Filters</summary><div class="ev-drawer-body">`
    + `<div class="ev-quick" role="group" aria-label="Quick filters">${[['sharp', 'Top books only'], ['favorites', 'Favorites'], ['longshots', 'Longshots']].map(([k, t]) => `<button data-ev="quick" data-id="${k}" aria-pressed="${f.quick === k || (k === 'sharp' && p.preset === 'sharp')}">${t}</button>`).join('')}`
    + `<button data-ev="evonly" aria-pressed="${f.evOnly}">Only picks that beat the entry</button></div>`
    + `<div class="ev-grid">${sel('preset', 'How books count', o.presets.map(x => [x, x === 'equal' ? 'All the same' : x === 'sharp' ? 'Top books count more' : 'Choose for each book']), p.preset)}`
    + sel('min_books', 'Fewest books', Array.from({length: o.min_books_max}, (_, i) => [i + 1, String(i + 1)]), p.min_books)
    + sel('min_liq', 'Least money on an exchange price', o.min_liq_choices.map(x => [x, x ? '$' + x.toLocaleString() : 'Any']), p.min_liq)
    + sel('max_width', 'Largest book cut', o.max_width_choices.map(x => [x, (x * 100).toFixed(0) + '%']), p.max_width)
    + `<label>To hit from<input id="ev-lo" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(f.lo)}" placeholder="0"></label>`
    + `<label>To hit up to<input id="ev-hi" type="number" min="0" max="100" step="1" inputmode="numeric" value="${esc(f.hi)}" placeholder="100"></label></div>`
    + `<fieldset class="ev-req"><legend>Required books (up to 2)</legend>${books.map(b => `<label><input type="checkbox" data-ev-req="${esc(b.id)}"${p.req.includes(b.id) ? ' checked' : ''}${!p.req.includes(b.id) && p.req.length >= 2 ? ' disabled' : ''}> ${esc(b.label)}</label>`).join('') || '<span>No books right now</span>'}</fieldset>${w}`
    + `<div class="ev-drawer-foot"><button data-ev="save">Save as my view</button><button data-ev="reset">Reset</button><span id="ev-saved" role="status"></span></div></div></details>`;
}
function render() {
  if (!box) return;
  const d = S.doc;
  if (!d) { box.innerHTML = head() + `<p class="ev-state" role="status">${S.error ? esc(S.error) + ' Use Refresh to try again.' : 'Checking current prices…'}</p>`; return; }
  const sports = (d.sports || []).length, rows = d.rows.filter(r => keep(r) && (!S.f.evOnly || (typeof r.ev === 'number' && r.ev > 0)));
  const model = d.model_only.filter(keep), entries = d.options.entries.filter(e => e.app === d.app);
  const sportOpts = [['all', 'All sports'], ...d.sports.map(s => [s, C.SPORTS[s] || s])];
  const banner = d.hidden && d.hidden.text ? `<p class="ev-banner" role="status">${esc(d.hidden.text)} Old, one-sided or wide prices never set a chance.</p>` : '';
  box.innerHTML = head()
    + `<div class="ev-apps" role="group" aria-label="App">${(d.apps || [d.app]).map(a => `<button data-ev="app" data-id="${a}" aria-pressed="${d.app === a}">${a === 'prizepicks' ? 'PrizePicks' : 'Underdog'} <span class="ev-chip">${d.plays[a] || 0} beat the entry</span></button>`).join('')}</div>`
    + `<div class="ev-bar"><label>Entry<select id="ev-entry">${entries.map(e => `<option value="${e.entry}-${e.n}"${e.entry === d.entry && e.n === d.n ? ' selected' : ''}>${ENTRY[e.entry] || e.entry} ${e.n}-pick · each leg needs ${pct(e.break_even)} (${odds(Math.round(e.american))})</option>`).join('')}</select></label>`
    + `<label class="ev-search">Search<input id="ev-q" type="search" value="${esc(S.f.q)}" placeholder="Player, team or market"></label>`
    + (sports > 1 ? `<label>Sport<select id="ev-sport">${sportOpts.map(([k, t]) => `<option value="${k}"${S.f.sport === k ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>` : '') + `</div>`
    + filtersHtml(d) + banner
    + `<section aria-label="Market priced legs"><h3>Market priced <small>${rows.length} of ${d.rows_total} legs${S.f.evOnly ? ' · only picks that beat the entry' : ''}</small></h3>`
    + (rows.length ? `<ol class="ev-list"><li class="ev-head" aria-hidden="true"><span>Play</span><span>App leg</span><span>To hit · need ${pct(d.break_even)}</span><span>Average return</span><span>Scout</span><span>Market</span><span></span></li>${rows.map(r => rowHtml(r, sports)).join('')}</ol>`
      : `<p class="ev-state">${d.rows_total ? 'No leg beats the entry right now with these filters.' : 'No pick\'em leg has a current book price right now.'}${S.f.evOnly && d.rows_total ? ' <button class="ev-link" data-ev="evonly">Show every priced leg</button>' : ''}</p>`)
    + `</section><section aria-label="Model only legs" class="ev-model"><h3>No book price <small>${model.length} legs · no book prices this line, ranked by Scout's chance</small></h3>`
    + (model.length ? `<button class="ev-link" data-ev="model" aria-expanded="${S.showModel}">${S.showModel ? 'Hide' : 'Show'} legs with no book price</button>${S.showModel ? `<ol class="ev-list ev-list-model"><li class="ev-head" aria-hidden="true"><span>Play</span><span>App leg</span><span>Scout</span><span>Average return on Scout's chance</span><span></span></li>${model.slice(0, 100).map(r => modelRowHtml(r, sports)).join('')}</ol>` : ''}` : '<p class="ev-state">None right now.</p>')
    + `</section><details class="ev-how"><summary>How this works</summary><p>To hit is the books' chance for this side at the app's exact line, with each book's cut taken out (the least favourable of three ways, so the chance is never flattered). A book on a nearby line is read at the app's line from how the stat usually spreads; a book line too far away is left out. Exchange prices count only when buy and sell are within ${Math.round(d.options.max_spread * 100)}¢. Prices older than ${Math.round(d.options.freshness_s.sportsbook / 60)} minutes (exchanges ${Math.round(d.options.freshness_s.exchange / 60)}) are left out. A book copied by two feeds counts once.</p>`
    + `<p>Average return is per pick on this entry: chance to hit ÷ chance needed − 1. Scout is Scout's own chance as the board shows it, beside the books' chance and never mixed into it.</p>`
    + `<p>Payouts: ${esc(d.app_label)} table from ${d.payout_source.source ? `<a href="${esc(d.payout_source.source)}" target="_blank" rel="noopener">its help center</a>` : 'its help center'}, read ${esc(d.payout_source.read_at || '')}. The app's own entry screen is final. Scout links to the app and never places an entry.</p></details>`;
}
function head() {
  const d = S.doc;
  return `<div class="h-section-head ev-headline"><div><span class="h-eyebrow">PICK'EM PRICES</span><h2 tabindex="-1">Best prices</h2><p>Pick'em legs whose chance from the books beats what the entry needs.</p></div>`
    + `<div class="ev-refresh"><button data-ev="refresh"${S.loading ? ' disabled' : ''}>${S.loading ? 'Checking…' : 'Refresh'}</button>${d ? `<small>Checked ${esc(C.when(d.as_of))}</small>` : ''}</div></div>`;
}

const CSS = '#u-hub .ev-wrap{max-width:1180px}#u-hub .ev-headline{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}'
  + '#u-hub .ev-refresh{display:flex;flex-direction:column;align-items:flex-end;gap:4px}#u-hub .ev-refresh small{color:var(--u-muted)}'
  + '#u-hub .ev-apps{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 10px}#u-hub .ev-apps button{min-height:40px;display:inline-flex;align-items:center;gap:8px}'
  + '#u-hub .ev-apps button[aria-pressed="true"]{border-color:var(--u-accent,#3b82f6);box-shadow:inset 0 0 0 1px var(--u-accent,#3b82f6)}'
  + '#u-hub .ev-chip{font-size:12px;padding:1px 7px;border-radius:999px;background:var(--u-panel-2,rgba(127,127,127,.15))}'
  + '#u-hub .ev-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:0 0 10px}#u-hub .ev-bar label,#u-hub .ev-grid label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--u-muted)}'
  + '#u-hub .ev-bar select,#u-hub .ev-bar input,#u-hub .ev-grid select,#u-hub .ev-grid input{min-height:40px;max-width:100%}#u-hub .ev-search{flex:1 1 200px}'
  + '#u-hub .ev-drawer{border:1px solid var(--u-line);border-radius:9px;background:var(--u-panel);margin:0 0 12px}#u-hub .ev-drawer summary{min-height:42px;display:flex;align-items:center;padding:0 12px;cursor:pointer;font-weight:600}'
  + '#u-hub .ev-drawer-body{padding:4px 12px 12px}#u-hub .ev-quick{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}#u-hub .ev-quick button{min-height:40px}'
  + '#u-hub .ev-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}#u-hub .ev-req,#u-hub .ev-weights{border:0;padding:0;margin:10px 0 0;display:flex;flex-wrap:wrap;gap:6px 14px}'
  + '#u-hub .ev-req legend,#u-hub .ev-weights legend{font-size:12px;color:var(--u-muted);margin-bottom:4px}#u-hub .ev-req label{min-height:40px;display:inline-flex;align-items:center;gap:6px}'
  + '#u-hub .ev-drawer-foot{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap}#u-hub .ev-drawer-foot span{color:var(--u-muted);font-size:12px}'
  + '#u-hub .ev-banner{padding:10px 12px;border-radius:8px;border:1px solid var(--u-line);background:var(--u-panel);margin:0 0 12px}'
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
  if (a === 'refresh') return fetchDoc();
  if (a === 'app' && id !== S.p.app) { S.p.app = id; S.p.entry = id === 'prizepicks' ? 'power' : 'standard'; S.p.n = 2; return fetchDoc(); }
  if (a === 'books') { S.open.has(id) ? S.open.delete(id) : S.open.add(id); return render(); }
  if (a === 'model') { S.showModel = !S.showModel; return render(); }
  if (a === 'evonly') { S.f.evOnly = !S.f.evOnly; return render(); }
  if (a === 'quick') {
    if (id === 'sharp') { const on = S.p.preset !== 'sharp'; S.p.preset = on ? 'sharp' : 'equal'; S.p.min_books = on ? Math.max(2, S.p.min_books) : 1; S.drawer = true; return fetchDoc(); }
    S.f.quick = S.f.quick === id ? '' : id; S.drawer = true; return render();
  }
  if (a === 'save') {
    let ok = true; try { localStorage.setItem(KEY, JSON.stringify({p: S.p, f: S.f})); } catch (err) { ok = false; }
    const m = box.querySelector('#ev-saved'); if (m) m.textContent = ok ? 'Saved in this browser.' : 'This browser does not allow saving.'; return;
  }
  if (a === 'reset') {
    try { localStorage.removeItem(KEY); } catch (err) { /* storage blocked */ }
    Object.assign(S.p, {preset: 'equal', method: 'worst', min_books: 1, req: [], w: {}, min_liq: 0, max_width: 0.1}); Object.assign(S.f, {q: '', sport: 'all', lo: '', hi: '', quick: '', evOnly: true});
    return fetchDoc();
  }
  if (a === 'add') return C.addLeg(id, b.dataset.side);
}
function onChange(e) {
  const t = e.target, id = t.id || '';
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
}
