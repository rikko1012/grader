/* Prop Grader landing: the feature sections (one member screen per section). Inlined by landing_v3.py when
   SCOUT_LANDING_FEATURES is on. Every number below is a saved example from the member board of 2026-11-25 18:32 UTC
   (CS2: FaZe vs HEROIC and Lynn Vision vs PARIVISION, PGL Major Singapore 2026 Stage 1; NBA CHI at CHA; NFL GB at LAR) and
   from that board's Twistzz card (HLTV map totals, saved factors, line history). Nothing is read from the member API by a
   visitor's browser and nothing here is a current line. Payout multiples are the published reference tables the member
   Slip Studio uses (payout_reference.py: PrizePicks Power 2/3/4 = 3/6/10x, Flex 3 = 3x / 1x, Flex 4 = 6x / 1.5x).
   Motion: each stage plays once when its top passes the lower third of the screen (a cursor drives the real controls), stops for good the moment the
   visitor touches it, and can be replayed. prefers-reduced-motion and no-IntersectionObserver get the final frame. */
(() => {
  'use strict';
  const root = document.querySelector('.fx');
  if (!root) return;
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window);
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const f1 = n => Number(n).toFixed(1);
  const sgn = (n, d = 1) => (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(d);
  const BE = {ud: 53.5, pp: 57.7};
  const APP = {ud: 'Underdog', pp: 'PrizePicks'};

  const ROWS = [
    {name: 'Twistzz', role: 'IGL · Rifler', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 28.8,
      ud: {line: 29.5, from: 30.5, side: 'Under', p: 53.8, g: null, u: 0, hit: 7, edge: 0.3}, pp: {line: 30.5, side: 'Under', p: 58.8, g: 'B', u: 0.5, hit: 7, edge: 1.1}},
    {name: 'JBOEN', role: 'AWPer', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 28.4,
      ud: {line: 29.5, from: 31.5, side: 'Under', p: 55.7, g: 'B-', u: 0.5, hit: 5, edge: 2.2}, pp: {line: 30, side: 'Under', p: 58.6, g: 'B', u: 0.5, hit: 5, edge: 0.9}},
    {name: 'jcobbb', role: 'Entry', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 26.7,
      ud: {line: 28.5, side: 'Under', p: 59.3, g: 'B', u: 0.5, hit: 7, edge: 5.8}, pp: {line: 28.5, side: 'Under', p: 59.3, g: 'B', u: 0.5, hit: 7, edge: 1.6}},
    {name: 'C4LLM3SU3', role: 'Rifler', team: 'Lynn Vision', match: 'Lynn Vision vs PARIVISION', proj: 25.7,
      ud: {line: 23.5, side: 'Over', p: 61.7, g: 'B', u: 0.5, hit: 7, edge: 8.2}, pp: {line: 25, side: 'Over', p: 54.1, g: null, u: 0, hit: 7, edge: -3.6}},
    {name: 'EmiliaQAQ', role: 'Rifler', team: 'Lynn Vision', match: 'Lynn Vision vs PARIVISION', proj: 25.9,
      ud: {line: 23.5, side: 'Over', p: 62.7, g: 'B', u: 0.5, hit: 7, edge: 9.2}, pp: {line: 24.5, side: 'Over', p: 57.6, g: 'B', u: 0.5, hit: 7, edge: -0.1}},
    {name: 'FL1T', role: 'Lurker', team: 'PARIVISION', match: 'Lynn Vision vs PARIVISION', proj: 27.5,
      ud: {line: 29.5, from: 28.5, side: 'Under', p: 60.0, g: 'B', u: 0.5, hit: 6, edge: 6.5}, pp: {line: 30, side: 'Under', p: 63.1, g: 'B+', u: 1, hit: 6, edge: 5.4}}
  ];
  /* Twistzz card, PrizePicks 30.5 (forecast 211105): last 20 Maps 1-2 totals, newest first; saved factors; HLTV 12-month map totals */
  const LAST20 = [28, 26, 20, 23, 27, 41, 47, 12, 34, 23, 23, 34, 32, 25, 38, 43, 39, 35, 39, 37];
  const FACTORS = [['Kills per round, adjusted for the opponents he faced', '0.703', 1], ['Kills per round, all maps', '0.684', 1], ['Kills per round, LAN maps', '0.698', 1],
    ['Tonight’s opponent, kills given up', '−6.3%', 1], ['Tonight’s setting', 'LAN', 1], ['Opponent Elo', '1699', 1], ['Rounds per map, his team', '22.1', 1],
    ['Rating', '1.11', 0], ['KAST', '74.9%', 0], ['Team win chance, betting markets', '46.2%', 0]];
  /* key, name, maps, kills, rounds, deaths */
  const MAPS = [['ancient', 'Ancient', 37, 538, 796, 517], ['mirage', 'Mirage', 37, 560, 813, 521], ['nuke', 'Nuke', 35, 547, 786, 502], ['dust2', 'Dust 2', 36, 555, 791, 536],
    ['anubis', 'Anubis', 17, 245, 358, 242], ['inferno', 'Inferno', 13, 199, 273, 176], ['cache', 'Cache', 12, 196, 258, 166]];
  const ALL = {maps: 198, kills: 3017, rounds: 4323, deaths: 2834};
  const VETO = {ancient: 'Map 1', mirage: 'Map 2', nuke: 'Decider'};
  const LEGS = [
    {id: 'emi', sport: 'CS2', name: 'EmiliaQAQ', sub: 'Lynn Vision vs PARIVISION', side: 'Over', line: 24.5, stat: 'Kills', per: 'Maps 1-2', p: 57.6, g: 'B'},
    {id: 'mil', sport: 'NBA', name: 'Brandon Miller', sub: 'CHI at CHA', side: 'Under', line: 3.5, stat: 'Assists', per: 'Full game', p: 65.4, g: 'A'},
    {id: 'atw', sport: 'NFL', name: 'Tutu Atwell', sub: 'GB at LAR', side: 'Under', line: 23.5, stat: 'Receiving yards', per: 'Full game', p: 70.7, g: 'A'},
    {id: 'twi', sport: 'CS2', name: 'Twistzz', sub: 'FaZe vs HEROIC', side: 'Under', line: 30.5, stat: 'Kills', per: 'Maps 1-2', p: 58.8, g: 'B'},
    {id: 'fl1', sport: 'CS2', name: 'FL1T', sub: 'Lynn Vision vs PARIVISION', side: 'Under', line: 30, stat: 'Kills', per: 'Maps 1-2', p: 63.1, g: 'B+'}
  ];
  const POWER = {2: 3, 3: 6, 4: 10}, FLEX = {3: [3, 1], 4: [6, 1.5]};

  const grade = g => g ? `<span class="fx-g${/^A|B\+/.test(g) ? ' hi' : ''}">${g.replace('-', '−')}</span>` : '';
  const letter = o => o.g ? grade(o.g) : `<span class="fx-lean">Lean ${o.side}</span>`;
  const sideTag = s => `<span class="fx-side ${s === 'Over' ? 'ov' : 'un'}"><i aria-hidden="true"></i>${s}</span>`;
  const av = n => `<span class="fx-av" aria-hidden="true">${esc(n[0].toUpperCase())}</span>`;
  const hitCls = r => r >= 0.6 ? 'good' : r < 0.4 ? 'poor' : 'mid';
  const ln = v => String(v);
  const appbar = on => `<div class="fx-bar" aria-hidden="true"><span class="fx-brand"><i>1T</i>Prop Grader</span><nav>${['Board', 'Slips', 'My picks', 'Record', 'Tools'].map(n => `<span${n === on ? ' class="on"' : ''}>${n}</span>`).join('')}</nav><span class="fx-ex">Saved example</span><span class="fx-slipbtn">Slip <b class="fx-cnt">0</b></span></div>`;
  const PLUS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path class="p" d="M8 3v10M3 8h10"/><path class="c" d="M3.5 8.5l3 3 6-7"/></svg>';
  function toaster(el) {
    let t = null;
    return msg => { const b = $('.fx-toast', el); b.innerHTML = `<i aria-hidden="true"></i><span>${esc(msg)}</span><em>Undo</em>`; b.classList.add('on'); clearTimeout(t); t = setTimeout(() => b.classList.remove('on'), 2000); };
  }

  /* ---- motion kit: abortable sequences, a cursor that drives real controls, number tweens ---- */
  class Stop extends Error {}
  function kit(stage) {
    const tok = {dead: false, timers: new Set()};
    const cur = document.createElement('i'); cur.className = 'fx-cur'; cur.setAttribute('aria-hidden', 'true'); stage.appendChild(cur);
    const wait = ms => new Promise((res, rej) => { if (tok.dead) return rej(new Stop()); const t = setTimeout(() => { tok.timers.delete(t); tok.dead ? rej(new Stop()) : res(); }, ms); tok.timers.add(t); });
    async function to(el, ms = 620) {
      if (tok.dead) throw new Stop();
      const a = stage.getBoundingClientRect(), b = el.getBoundingClientRect();
      cur.style.transitionDuration = ms + 'ms';
      cur.style.transform = `translate(${b.left - a.left + b.width / 2}px,${b.top - a.top + b.height / 2}px)`;
      cur.classList.add('on');
      await wait(ms + 90);
    }
    async function click(el) {
      await to(el);
      cur.classList.add('tap'); el.classList.add('fx-press');
      await wait(140);
      cur.classList.remove('tap'); el.classList.remove('fx-press');
      el.dispatchEvent(new CustomEvent('fx-act', {bubbles: true}));
      await wait(120);
    }
    const hide = () => cur.classList.remove('on');
    const kill = () => { tok.dead = true; tok.timers.forEach(clearTimeout); tok.timers.clear(); hide(); };
    return {tok, wait, to, click, hide, kill};
  }
  function tween(el, to, dec = 1, suf = '', ms = 700) {
    const from = parseFloat(el.dataset.v ?? to); el.dataset.v = to;
    const done = () => { el.textContent = Number(to).toFixed(dec) + suf; };
    if (RM || from === to || !isFinite(from)) return done();
    const t0 = performance.now();
    (function step(t) {
      if (String(el.dataset.v) !== String(to)) return;
      const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      el.textContent = (from + (to - from) * e).toFixed(dec) + suf;
      k < 1 ? requestAnimationFrame(step) : done();
    })(t0);
  }
  /* A stage: render(), then autoplay once in view. Any real pointer/keyboard use ends the autoplay for good. */
  function stage(id, build) {
    const el = $('#' + id); if (!el) return;
    let k = null, played = false, user = false;
    const api = build(el, () => k);
    const stop = () => { if (k) { k.kill(); k = null; } el.classList.remove('fx-auto'); };
    async function play() {
      stop(); api.reset(); el.classList.add('fx-in');
      if (RM) return api.final();
      k = kit(el); el.classList.add('fx-auto'); const mine = k;
      try { await api.auto(mine); } catch (e) { if (!(e instanceof Stop)) throw e; }
      if (k === mine) { mine.hide(); el.classList.remove('fx-auto'); }
    }
    ['pointerdown', 'keydown'].forEach(ev => el.addEventListener(ev, e => { if (e.isTrusted && !e.target.closest('.fx-replay')) { user = true; stop(); } }));
    const rp = $('.fx-replay', el.closest('.fx-sec')); if (rp) rp.addEventListener('click', () => { user = false; play(); });
    if (RM) { api.reset(); api.final(); el.classList.add('fx-in'); return; }
    new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting && !played) { played = true; play(); }
      else if (!e.isIntersecting && k) { stop(); api.final(); }
    }), {rootMargin: '0px 0px -28% 0px'}).observe(el);
    document.addEventListener('visibilitychange', () => { if (document.hidden && k) { stop(); api.final(); } });
  }
  const act = (el, sel, fn) => { const h = e => { const t = e.target.closest(sel); if (t && el.contains(t)) fn(t, e); }; el.addEventListener('click', h); el.addEventListener('fx-act', h); };

  /* ---- the member board (web/v3/board.js layout): shared by 1 (grades) and 4 (the Views: Lines differ, Line moved) ---- */
  function board(el, views) {
    let app = 'all', view = 'all', picked = [];
    const toast = toaster(el);
    const cur = r => r[app === 'pp' ? 'pp' : 'ud'], oth = r => r[app === 'pp' ? 'ud' : 'pp'];
    const VIEW = {all: ['All props', ''], differ: ['Lines differ', 'Another book lists a different line for the same player, stat and maps. For an Over the lower line is the easier one, for an Under the higher one; payouts can differ.'],
      moved: ['Line moved', 'Lines that moved from where they opened; each row shows the line it moved from.']};
    const show = r => view === 'differ' ? r.ud.line !== r.pp.line : view === 'moved' ? !!cur(r).from : true;
    function paint(first) {
      const a = app === 'pp' ? 'pp' : 'ud';
      $$('.fx-chip', el).forEach(c => c.setAttribute('aria-pressed', String(c.dataset.app === app)));
      let n = 0;
      $$('.fx-row', el).forEach((tr, i) => {
        const r = ROWS[i], o = cur(r), x = oth(r), on = show(r), diff = o.line !== x.line;
        tr.hidden = !on; if (on) tr.style.setProperty('--i', n++);
        $('.fx-neq', tr).innerHTML = diff ? `≠ ${APP[a === 'pp' ? 'ud' : 'pp']} ${ln(x.line)}` : ''; $('.fx-neq', tr).hidden = !diff;
        $('.fx-ln b', tr).innerHTML = ln(o.line) + (o.from ? ` <s>${ln(o.from)}</s>` : '');
        $('.fx-ln small', tr).textContent = APP[a];
        $('.fx-sd', tr).innerHTML = sideTag(o.side);
        tween($('.fx-pc', tr), o.p, 1, '%', first ? 900 : 520);
        $('.fx-gr', tr).innerHTML = letter(o) + `<small>Stake <b>${o.u}U</b></small>`;
        const h = $('.fx-hit', tr); h.innerHTML = `<b>${o.hit}</b>/10`; h.className = 'fx-hit ' + hitCls(o.hit / 10);
        const ed = $('.fx-ed', tr); ed.innerHTML = `<b>${sgn(o.edge)}</b> pts`; ed.className = 'fx-ed ' + (o.edge > 0 ? 'pos' : 'neg');
        $('.fx-cmp', tr).innerHTML = diff ? `<b>2 lines</b><small>${ln(Math.min(o.line, x.line))}–${ln(Math.max(o.line, x.line))}</small>` : `<b>2 books</b><small>at ${ln(o.line)}</small>`;
        $('.fx-cmp', tr).classList.toggle('diff', diff);
        const b = $('.fx-add', tr), sel = picked.includes(i); b.setAttribute('aria-pressed', String(sel)); b.setAttribute('aria-label', `${sel ? 'Remove' : 'Add'} ${r.name}`); tr.classList.toggle('picked', sel);
      });
      $('.fx-cnt', el).textContent = picked.length;
      $('.fx-count', el).textContent = view === 'all' ? '6 players · 12 lines' : `${n} player${n === 1 ? '' : 's'} · ${VIEW[view][0].toLowerCase()}`;
      $('.fx-legend', el).textContent = `Edge: the model estimate minus the per-pick break-even of ${a === 'ud' ? 'an Underdog standard' : 'a PrizePicks Power'} 2-pick entry (${BE[a]}%), in points.`;
      if (views) {
        $('.fx-vbtn span', el).textContent = view === 'all' ? 'Views' : VIEW[view][0];
        $$('.fx-vmenu button', el).forEach(b => b.setAttribute('aria-checked', String(b.dataset.view === view)));
        const d = $('.fx-vdesc', el); d.textContent = VIEW[view][1]; d.hidden = view === 'all';
      }
    }
    const menu = open => { const m = $('.fx-vmenu', el); if (m) { m.hidden = !open; $('.fx-vbtn', el).setAttribute('aria-expanded', String(open)); } };
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-hd"><div><h3>CS2 player props</h3><p class="fx-count"></p></div></div>
      <div class="fx-tools"><div class="fx-chips" role="group" aria-label="App"><button type="button" class="fx-chip" data-app="all" aria-pressed="true">All</button><button type="button" class="fx-chip" data-app="pp" aria-pressed="false">PP <i>6</i></button><button type="button" class="fx-chip" data-app="ud" aria-pressed="false">UD <i>6</i></button></div>
      ${views ? `<div class="fx-vwrap"><button type="button" class="fx-vbtn" aria-haspopup="true" aria-expanded="false"><span>Views</span><svg viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></button><div class="fx-vmenu" role="menu" hidden>${Object.entries(VIEW).map(([k, v]) => `<button type="button" role="menuitemradio" data-view="${k}" aria-checked="${k === 'all'}">${v[0]}</button>`).join('')}</div></div>` : ''}
      <span class="fx-sort">Sort <b>Start time</b></span></div>${views ? '<p class="fx-vdesc" hidden></p>' : ''}
      <div class="fx-tbl" role="table" aria-label="Saved example of the CS2 board"><div class="fx-th" role="row"><span role="columnheader">Player</span><span role="columnheader">Market</span><span role="columnheader">Line</span><span role="columnheader">Proj.</span><span role="columnheader">Grader · est.</span><span role="columnheader">L10</span><span role="columnheader">Edge<small>vs break-even</small></span><span role="columnheader">Compare</span><span role="columnheader"></span></div>
      ${ROWS.map((r, i) => `<div class="fx-row" role="row" style="--i:${i}"><span class="fx-pl" role="cell">${av(r.name)}<span><b>${esc(r.name)}</b><small>${esc(r.match)}</small></span></span>
        <span class="fx-mk" role="cell"><b>Kills</b><small>Maps 1-2</small><em class="fx-neq" hidden></em></span><span class="fx-ln" role="cell"><b></b><small></small></span><span class="fx-pj" role="cell">${f1(r.proj)}</span>
        <span class="fx-md" role="cell"><span class="fx-sd"></span><b class="fx-pc" data-v="50">50.0%</b><span class="fx-gr"></span></span>
        <span class="fx-hit" role="cell"></span><span class="fx-ed" role="cell"></span><span class="fx-cmp" role="cell"></span><span class="fx-ac" role="cell"><button type="button" class="fx-add" data-i="${i}" aria-pressed="false">${PLUS}</button></span></div>`).join('')}</div>
      <p class="fx-legend"></p><div class="fx-toast" role="status"></div></div>`;
    act(el, '.fx-chip', c => { app = c.dataset.app; menu(false); paint(false); });
    act(el, '.fx-add', b => { const i = +b.dataset.i, k = picked.indexOf(i), r = ROWS[i], o = cur(r); if (k >= 0) { picked.splice(k, 1); toast(`Removed ${r.name} from your slip.`); } else { picked.push(i); toast(`Added ${r.name} ${o.side} ${ln(o.line)} to your slip.`); } paint(false); });
    act(el, '.fx-vbtn', () => menu($('.fx-vmenu', el).hidden));
    act(el, '.fx-vmenu button', b => { view = b.dataset.view; menu(false); paint(false); });
    return {
      set(a, v) { app = a; view = v; picked = []; menu(false); },
      paint,
      reset() { app = 'all'; view = 'all'; picked = []; menu(false); $$('.fx-pc', el).forEach(p => { p.dataset.v = 50; }); paint(true); }
    };
  }
  stage('fx-board', el => {
    const b = board(el, false);
    return {
      reset: b.reset,
      final() { b.set('all', 'all'); b.paint(false); },
      async auto(k) { await k.wait(1900); await k.click($('[data-app="pp"]', el)); await k.wait(2200); await k.click($$('.fx-add', el)[5]); await k.wait(1900); await k.click($('[data-app="all"]', el)); await k.wait(600); }
    };
  });

  /* ---- 2. The player card (web/v3/player.js layout): the decision panel + Overview / Lines / Stats ---- */
  stage('fx-card', el => {
    let book = 'pp', tab = 'overview', added = false;
    const T = ROWS[0], toast = toaster(el);
    const tally = (line, n) => LAST20.slice(0, n).filter(v => v < line).length;
    function panel() {
      const o = T[book], gap = Math.abs(o.line - T.proj);
      $('.fx-pline', el).textContent = ln(o.line);
      $('.fx-psel', el).innerHTML = `${APP[book]} ${ln(o.line)}${o.from ? ` <s>${ln(o.from)}</s>` : ''}`;
      $('.fx-pseen', el).textContent = `${APP[book]} · Standard line${o.from ? ' · opened ' + ln(o.from) : ''}`;
      $$('.fx-sides span', el).forEach((sp, i) => { sp.innerHTML = `${i ? 'Under' : 'Over'} ${ln(o.line)}${i ? ' <i>Grader</i>' : ''}`; });
      tween($('.fx-big', el), o.p, 1, '%', 800);
      $('.fx-pg', el).innerHTML = letter(o);
      $('.fx-pu', el).textContent = o.u + 'U';
      $('.fx-pgap', el).innerHTML = `<b>−${gap.toFixed(1)}</b><small>below the line</small>`;
      $('.fx-pmk', el).innerHTML = book === 'ud' ? '<small>Market chance</small><b>50.0%</b><small>From Underdog prices</small>' : '<small>Market chance</small><b>—</b><small>No prices on PrizePicks</small>';
      $('.fx-padd', el).textContent = added ? `Added Under ${ln(o.line)}` : `Add Under ${ln(o.line)}`; $('.fx-padd', el).classList.toggle('done', added);
      $('.fx-cnt', el).textContent = added ? 1 : 0;
      $('.fx-ptal', el).innerHTML = [5, 10, 20].map(n => `<div><b>${tally(o.line, n)}<i>/${n}</i></b><small>L${n}, Under ${ln(o.line)}</small></div>`).join('');
    }
    function right() {
      const o = T[book], gap = Math.abs(o.line - T.proj).toFixed(1), u10 = tally(o.line, 10);
      $$('.fx-tabs button', el).forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
      $('.fx-tp', el).innerHTML = tab === 'overview' ? `<div class="fx-ins"><small>Key insights</small><h4>Why Grader leans Under ${ln(o.line)}</h4><p class="fx-dot on">Grader projects <b>${f1(T.proj)} Kills</b>, ${gap} below the ${ln(o.line)} line · <b>${f1(o.p)}%</b> chance Under</p>
          <div class="fx-tiles"><div><small>Under ${ln(o.line)}</small><b>${u10}<i>/10</i></b><small>Last 10 results</small></div><div><small>Over ${ln(o.line)}</small><b>${10 - u10}<i>/10</i></b><small>0 pushes kept separate</small></div><div><small>Average / median</small><b>27.7 / 25</b><small>Maps 1-2</small></div><div><small>Recent range</small><b>12–47</b><small>Observed results, not a forecast</small></div></div>
          <small class="fx-sub">Other numbers that point to Under</small><p class="fx-dot">Went Under in <b>${u10}</b> of the last <b>10</b> at ${ln(o.line)} <i>(L5 ${tally(o.line, 5)}/5 · L20 ${tally(o.line, 20)}/20)</i></p><p class="fx-dot">Against HEROIC’s current core: <b>28, 23, 25</b> in 3 meetings, all Under</p></div>`
        : tab === 'lines' ? `<div class="fx-ins"><div class="fx-th2"><h4>Books and lines</h4><small>Same event · stat · period</small></div><div class="fx-bl" role="table"><div class="fx-blh" role="row"><span>Book / line</span><span></span><span>Over</span><span>Under</span><span>Grader</span></div>
          ${['ud', 'pp'].map(k => { const x = T[k]; return `<button type="button" class="fx-blr" data-book="${k}" aria-pressed="${k === book}"><span><b>${APP[k]}</b> <i>Standard</i></span><span class="fx-bll">${ln(x.line)}${x.from ? ` <s>${ln(x.from)}</s>` : ''}</span><span>${k === 'ud' ? '1.90' : 'Standard'}</span><span>${k === 'ud' ? '1.90' : 'Standard'}</span><span>${x.g ? `<b>${x.g}</b> ` : 'Lean '}Under <b>${f1(x.p)}%</b></span></button>`; }).join('')}</div>
          <p class="fx-note">Choose a line to recount its past results. Each line is its own pick with its own forecast; payouts and availability differ.</p></div>`
        : `<div class="fx-ins"><div class="fx-th2"><h4>Every factor</h4><small>projection ${f1(T.proj)}</small></div><div class="fx-ft" role="table"><div class="fx-blh" role="row"><span>Factor</span><span>Value</span><span>In this grade</span></div>${FACTORS.map(f => `<div role="row"><span>${esc(f[0])}</span><span>${f[1]}</span><span class="${f[2] ? 'used' : ''}">${f[2] ? 'Used' : 'Weight 0'}</span></div>`).join('')}</div>
          <p class="fx-note">Used = it moves this projection. Weight 0 = shown for context: a factor moves grades only after a chronological test.</p></div>`;
    }
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-id">${av('Twistzz')}<div><h3>Twistzz</h3><p>FaZe · IGL · Rifler</p></div></div>
      <div class="fx-match"><div><b>FaZe</b><small>VRS #27</small></div><div><small>PGL Major Singapore 2026</small><b>Best of 3</b></div><div><b>HEROIC</b><small>VRS #21</small></div></div>
      <div class="fx-cols"><div class="fx-dp"><div class="fx-dph"><div><small>Kills, Maps 1-2</small><b class="fx-pline"></b></div><span class="fx-psel"></span></div><small class="fx-pseen"></small>
        <div class="fx-sides"><span></span><span class="on"></span></div>
        <div class="fx-dpg"><div><small>Chance Under</small><b class="fx-big" data-v="50">50.0%</b><small>Model estimate</small></div><div><small>Grade</small><span class="fx-pg"></span></div><div><small>Stake</small><b class="fx-pu"></b></div></div>
        <div class="fx-dpg sm"><div><small>Projection</small><b>${f1(T.proj)}</b><small>Maps 1-2</small></div><div><small>Gap to line</small><span class="fx-pgap"></span></div><div class="fx-pmk"></div></div>
        <button type="button" class="fx-padd"></button><div class="fx-ptal"></div></div>
      <div class="fx-rt"><div class="fx-tabs" role="tablist">${[['overview', 'Overview'], ['lines', 'Lines'], ['stats', 'Stats']].map(t => `<button type="button" role="tab" data-tab="${t[0]}" aria-selected="${t[0] === tab}">${t[1]}</button>`).join('')}</div><div class="fx-tp" role="tabpanel"></div></div></div>
      <div class="fx-toast" role="status"></div></div>`;
    act(el, '.fx-tabs button', b => { tab = b.dataset.tab; right(); });
    act(el, '.fx-blr', b => { book = b.dataset.book; added = false; panel(); right(); });
    act(el, '.fx-padd', () => { added = !added; panel(); toast(added ? `Added Twistzz Under ${ln(T[book].line)} to your slip.` : 'Removed Twistzz from your slip.'); });
    return {
      reset() { book = 'pp'; tab = 'overview'; added = false; $('.fx-big', el).dataset.v = 50; panel(); right(); },
      final() { book = 'pp'; tab = 'overview'; added = false; panel(); right(); },
      async auto(k) { await k.wait(2400); await k.click($('[data-tab="lines"]', el)); await k.wait(1300); await k.click($('[data-book="ud"]', el)); await k.wait(2300); await k.click($('[data-book="pp"]', el)); await k.wait(1700); await k.click($('[data-tab="stats"]', el)); await k.wait(2400); await k.click($('[data-tab="overview"]', el)); await k.wait(500); }
    };
  });

  /* ---- 3. The map picker (the card's Maps block: HLTV totals summed, then divided) ---- */
  stage('fx-maps', el => {
    let sel = [];
    const PACE = 15.25, BASE = ALL.kills / ALL.maps;
    const dl = (d, dec) => `<i class="${d > 0 ? 'up' : d < 0 ? 'dn' : ''}">${sgn(d, dec)}</i>`;
    function paint() {
      $$('.fx-map', el).forEach(b => { const i = sel.indexOf(b.dataset.k); b.setAttribute('aria-pressed', String(i >= 0)); $('.fx-n', b).textContent = i >= 0 ? i + 1 : ''; });
      $('.fx-mh span', el).textContent = sel.length === 2 ? '2 maps combined · a third pick replaces the first' : sel.length === 1 ? '1 of 2 picked' : 'Pick 2 to combine';
      const out = $('.fx-comb', el), m = sel.map(k => MAPS.find(x => x[0] === k));
      if (m.length < 2) { out.className = 'fx-comb empty'; out.innerHTML = `<p>${m.length ? esc(m[0][1]) + ' picked. Pick one more map.' : 'Pick 2 maps to combine his numbers.'}</p>`; return; }
      const n = m[0][2] + m[1][2], kills = m[0][3] + m[1][3], v = kills / n, d = v - PACE, pct = d / PACE * 100;
      out.className = 'fx-comb';
      out.innerHTML = `<div class="fx-ct"><b class="fx-cv" data-v="${PACE}">${PACE.toFixed(2)}</b><p><b class="${d < 0 ? 'dn' : 'up'}">${sgn(d, 2)}</b> vs line pace <b>${PACE}</b><br><b>${sgn(pct)}%</b> ${d < 0 ? 'below' : 'above'} pace</p></div>
        <div class="fx-pace"><i style="transform:scaleX(${Math.min(1, v / (PACE * 1.25)).toFixed(3)})"></i><span></span><em>pace ${PACE}</em></div>
        <div class="fx-cs"><div><small>Kills/map</small><b>${v.toFixed(1)}</b></div><div><small>KPR</small><b>${(kills / (m[0][4] + m[1][4])).toFixed(2)}</b></div><div><small>K/D</small><b>${(kills / (m[0][5] + m[1][5])).toFixed(2)}</b></div></div>
        <p class="fx-note">${esc(m[0][1])} + ${esc(m[1][1])} · ${n} maps, HLTV last 12 months · kills a map</p><button type="button" class="fx-clear">Clear maps</button>`;
      tween($('.fx-cv', out), Number(v.toFixed(2)), 2, '', 650);
    }
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-id sm">${av('Twistzz')}<div><h3>Twistzz · Under 30.5 Kills</h3><p>Maps 1-2 · FaZe vs HEROIC · line pace 15.25 kills a map</p></div></div>
      <div class="fx-st2"><div><small>Maps</small><b>${ALL.maps}</b></div><div><small>Kills/map</small><b>${BASE.toFixed(1)}</b></div><div><small>KPR</small><b>${(ALL.kills / ALL.rounds).toFixed(2)}</b></div><div><small>K/D</small><b>${(ALL.kills / ALL.deaths).toFixed(2)}</b></div><div><small>Likely veto</small><b class="t">Ancient · Mirage · Nuke</b></div></div>
      <div class="fx-mh"><h4>Maps</h4><span></span></div>
      <div class="fx-mcols"><div class="fx-mlist">${MAPS.map((m, i) => `<button type="button" class="fx-map m-${m[0]}" data-k="${m[0]}" aria-pressed="false" style="--i:${i}"><span class="fx-n" aria-hidden="true"></span><span class="fx-mn"><b>${m[1]}</b><small>${m[2]} maps${VETO[m[0]] ? ' · likely ' + VETO[m[0]] : ''}</small></span><span class="fx-mv2"><small>Kills/map</small><b>${(m[3] / m[2]).toFixed(1)}</b>${dl(m[3] / m[2] - BASE, 1)}</span><span class="fx-mv2"><small>KPR</small><b>${(m[3] / m[4]).toFixed(2)}</b></span><span class="fx-mv2"><small>K/D</small><b>${(m[3] / m[5]).toFixed(2)}</b></span></button>`).join('')}</div>
      <div class="fx-comb empty" aria-live="polite"></div></div>
      <p class="fx-legend">HLTV, last 12 months, every map he played; change vs his all-map kills a map (${BASE.toFixed(1)}).</p></div>`;
    act(el, '.fx-map', b => { const k = b.dataset.k, i = sel.indexOf(k); if (i >= 0) sel.splice(i, 1); else { sel.push(k); if (sel.length > 2) sel.shift(); } paint(); });
    act(el, '.fx-clear', () => { sel = []; paint(); });
    return {
      reset() { sel = []; paint(); },
      final() { sel = ['ancient', 'mirage']; paint(); },
      async auto(k) { await k.wait(1300); await k.click($('[data-k="nuke"]', el)); await k.wait(500); await k.click($('[data-k="dust2"]', el)); await k.wait(2500); await k.click($('[data-k="ancient"]', el)); await k.wait(2300); await k.click($('[data-k="mirage"]', el)); await k.wait(700); }
    };
  });

  /* ---- 4. Views: Lines differ and Line moved (the board's own filters) ---- */
  stage('fx-lines', el => {
    const b = board(el, true);
    return {
      reset: b.reset,
      final() { b.set('all', 'differ'); b.paint(false); },
      async auto(k) {
        await k.wait(1800); await k.click($('.fx-vbtn', el)); await k.wait(500); await k.click($('[data-view="differ"]', el)); await k.wait(2400);
        await k.click($('[data-app="pp"]', el)); await k.wait(2400); await k.click($('[data-app="all"]', el)); await k.wait(700);
        await k.click($('.fx-vbtn', el)); await k.wait(500); await k.click($('[data-view="moved"]', el)); await k.wait(2600);
        await k.click($('.fx-vbtn', el)); await k.wait(500); await k.click($('[data-view="differ"]', el)); await k.wait(500);
      }
    };
  });

  /* ---- 5. Slip Studio: + on a board row, the toast, the leg lands in the slip, Power / Flex, the payout ---- */
  stage('fx-slip', el => {
    let legs = [], entry = 'power';
    const toast = toaster(el);
    const leg = id => LEGS.find(l => l.id === id);
    function panel() {
      const n = legs.length, ok = n >= 2, flexOk = !!FLEX[n];
      if (!flexOk) entry = 'power';
      $('.fx-cnt', el).textContent = n; $('.fx-cnt2', el).textContent = n;
      const sp = [...new Set(legs.map(id => leg(id).sport))]; $('.fx-app', el).innerHTML = `PrizePicks <small>${n ? sp.join(' · ') + ' · ' : ''}${n} pick${n === 1 ? '' : 's'}</small>`;
      $$('.fx-add', el).forEach(b => { const on = legs.includes(b.dataset.id); b.setAttribute('aria-pressed', String(on)); b.setAttribute('aria-label', `${on ? 'Remove' : 'Add'} ${leg(b.dataset.id).name}`); b.closest('.fx-srow').classList.toggle('picked', on); });
      $('.fx-legs', el).innerHTML = n ? legs.map(id => { const l = leg(id); return `<li data-id="${id}">${av(l.name)}<div><b>${esc(l.name)} <i>${l.sport}</i></b><span>${l.side} <b>${l.line}</b> ${esc(l.stat)} · ${l.per}</span><small><b>${f1(l.p)}%</b> Model estimate</small></div>${grade(l.g)}<button type="button" class="fx-rm" data-id="${id}" aria-label="Remove ${esc(l.name)}">×</button></li>`; }).join('') : '<li class="fx-none">Add props from the board. Your slip builds here.</li>';
      $$('.fx-ent button', el).forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.e === entry)); b.disabled = b.dataset.e === 'flex' && !flexOk; });
      const pay = $('.fx-pay', el), st = $('.fx-st', el);
      if (!ok) { pay.innerHTML = `<b>—</b><small>Add ${2 - n} more to price the entry</small>`; st.className = 'fx-st'; st.innerHTML = n ? '<i></i>Add one more pick' : '<i></i>No picks yet'; return; }
      pay.innerHTML = entry === 'power' ? `<b>${POWER[n]}×</b><small>All-hit payout · Power ${n}-pick</small>` : `<b>${FLEX[n][0]}×</b><small>All-hit payout · <b>${FLEX[n][1]}×</b> with one miss · Flex ${n}-pick</small>`;
      st.className = 'fx-st ok'; st.innerHTML = '<i></i>1 of 1 entry ready';
    }
    function fly(btn, id) {
      if (RM) return;
      const row = btn.closest('.fx-srow'), tgt = $(`.fx-legs li[data-id="${id}"]`, el); if (!tgt) return;
      const a = el.getBoundingClientRect(), r = row.getBoundingClientRect(), t = tgt.getBoundingClientRect();
      const g = document.createElement('div'); g.className = 'fx-ghost'; g.innerHTML = `${av(leg(id).name)}<b>${esc(leg(id).name)}</b>`;
      g.style.cssText = `left:${r.left - a.left}px;top:${r.top - a.top}px;width:${r.width}px;height:${r.height}px`;
      el.appendChild(g); tgt.classList.add('landing');
      g.animate([{transform: 'none', opacity: 1}, {transform: `translate(${(t.left - r.left) * 0.55}px,${(t.top - r.top) * 0.5 - 26}px) scale(${Math.max(0.5, t.width / r.width * 1.05)}) rotate(-2deg)`, opacity: 1, offset: 0.55},
        {transform: `translate(${t.left - r.left}px,${t.top - r.top}px) scale(${t.width / r.width},${t.height / r.height})`, opacity: 0.2}], {duration: 620, easing: 'cubic-bezier(.6,0,.2,1)'}).onfinish = () => { g.remove(); tgt.classList.remove('landing'); tgt.classList.add('landed'); };
    }
    el.innerHTML = appbar('Slips') + `<div class="fx-split"><div class="fx-pad fx-sboard"><div class="fx-hd"><div><h3>Player props</h3><p>All sports · PrizePicks lines</p></div></div>
      ${LEGS.map((l, i) => `<div class="fx-srow" style="--i:${i}">${av(l.name)}<span class="fx-pl2"><b>${esc(l.name)} <i>${l.sport}</i></b><small data-pick="${l.side} ${l.line} ${esc(l.stat)}">${esc(l.sub)}</small></span><span class="fx-mk"><b>${l.side} ${l.line}</b><small>${esc(l.stat)}</small></span><span class="fx-md2"><b>${f1(l.p)}%</b>${grade(l.g)}</span><button type="button" class="fx-add" data-id="${l.id}" aria-pressed="false">${PLUS}</button></div>`).join('')}
      <div class="fx-toast" role="status"></div></div>
      <aside class="fx-panel2" aria-label="Slip"><div class="fx-sh"><h4>Slip <b class="fx-cnt2">0</b></h4><span class="fx-st"><i></i>No picks yet</span></div><div class="fx-app"></div>
      <div class="fx-ent" role="group" aria-label="Entry type"><button type="button" data-e="power" aria-pressed="true">Power</button><button type="button" data-e="flex" aria-pressed="false">Flex</button></div>
      <ul class="fx-legs"></ul><div class="fx-pay"></div><p class="fx-note">One entry per app. The member slip rechecks every leg against current offers before you track it.</p><a class="fx-go" href="${esc($('.fx').dataset.trial || '#pricing')}">Build yours free for 1 day</a></aside></div>`;
    act(el, '.fx-add', b => { const id = b.dataset.id, i = legs.indexOf(id); if (i >= 0) { legs.splice(i, 1); panel(); toast(`Removed ${leg(id).name} from your slip.`); } else { legs.push(id); panel(); fly(b, id); const l = leg(id); toast(`Added ${l.name} ${l.side} ${l.line} to your slip.`); } });
    act(el, '.fx-rm', b => { legs = legs.filter(x => x !== b.dataset.id); panel(); });
    act(el, '.fx-ent button', b => { if (!b.disabled) { entry = b.dataset.e; panel(); } });
    return {
      reset() { legs = []; entry = 'power'; panel(); },
      final() { legs = ['emi', 'mil', 'atw']; entry = 'power'; panel(); $$('.fx-legs li', el).forEach(li => li.classList.add('landed')); },
      async auto(k) {
        await k.wait(1100);
        for (const id of ['emi', 'mil', 'atw']) { await k.click($(`.fx-add[data-id="${id}"]`, el)); await k.wait(1150); }
        await k.click($('[data-e="flex"]', el)); await k.wait(1900); await k.click($('[data-e="power"]', el)); await k.wait(700);
        await k.to($('.fx-go', el), 700); await k.wait(400);
      }
    };
  });

  /* ---- section reveals + the progress rail (which feature you are on) ---- */
  const secs = $$('.fx-sec');
  if (RM) secs.forEach(s => s.classList.add('seen'));
  else {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('seen'); io.unobserve(e.target); } }), {threshold: 0.12});
    secs.forEach(s => io.observe(s));
  }
  const rail = $$('.fx-rail a');
  if (rail.length && 'IntersectionObserver' in window) {
    const ro = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) rail.forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + e.target.id)); }), {rootMargin: '-45% 0px -50% 0px'});
    secs.forEach(s => ro.observe(s));
  }
  /* a faint light follows the pointer across each frame (fine pointers only) */
  if (!RM && matchMedia('(hover:hover) and (pointer:fine)').matches) {
    $$('.fx-frame').forEach(f => {
      f.addEventListener('pointermove', e => { const r = f.getBoundingClientRect(); f.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%'); f.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%'); });
    });
  }
})();
