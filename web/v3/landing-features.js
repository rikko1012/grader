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
  const SERIES = [['Oct 21', 'HEROIC', 23], ['Oct 22', 'BC.Game', 34], ['Oct 27', 'Alliance', 12], ['Oct 27', 'K27', 47], ['Oct 29', 'GamerLegion', 41],
    ['Oct 29', 'B8', 27], ['Nov 2', 'FURIA', 23], ['Nov 3', 'Falcons', 20], ['Nov 15', 'fnatic', 26], ['Nov 15', 'HEROIC', 28]];
  const H2H = [['Feb 14', 'HEROIC', 25], ['Oct 21', 'HEROIC', 23], ['Nov 15', 'HEROIC', 28]];
  const FACTORS = [['Kills per round, adjusted for opponents faced', '0.703', 17.5], ['Kills per round, all maps', '0.684', -7.9],
    ['Tonight’s opponent, kills given up', '−6.3%', -7.0], ['Kills per round, LAN maps', '0.698', 3.8], ['Rounds in his sample', '1,488', 1.7]];
  const MAPS = [['ancient', 'Ancient', 37, 538], ['mirage', 'Mirage', 37, 560], ['nuke', 'Nuke', 35, 547], ['dust2', 'Dust 2', 36, 555],
    ['anubis', 'Anubis', 17, 245], ['inferno', 'Inferno', 13, 199], ['cache', 'Cache', 12, 196]];
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
  const sideTag = s => `<span class="fx-side ${s === 'Over' ? 'ov' : 'un'}"><i aria-hidden="true"></i>${s}</span>`;
  const av = n => `<span class="fx-av" aria-hidden="true">${esc(n[0].toUpperCase())}</span>`;
  const hitCls = r => r >= 0.6 ? 'good' : r < 0.4 ? 'poor' : 'mid';
  const appbar = on => `<div class="fx-bar" aria-hidden="true"><span class="fx-brand"><i>1T</i>Scout</span><nav>${['Board', 'Slips', 'My picks', 'Record', 'Tools'].map(n => `<span${n === on ? ' class="on"' : ''}>${n}</span>`).join('')}</nav><span class="fx-ex">Saved example</span></div>`;

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

  /* ---- 1. The board ---- */
  stage('fx-board', el => {
    let app = 'ud';
    const cell = (r, a) => { const o = r[a]; return {o, rate: o.hit / 10}; };
    function paint(first) {
      $$('.fx-chip', el).forEach(c => c.setAttribute('aria-pressed', String(c.dataset.app === app)));
      $$('.fx-row', el).forEach((tr, i) => {
        const r = ROWS[i], {o, rate} = cell(r, app);
        $('.fx-ln b', tr).textContent = o.line % 1 ? o.line : o.line + '';
        $('.fx-ln small', tr).innerHTML = `${APP[app]}${o.from ? ` <s>${o.from}</s>` : ''}`;
        $('.fx-sd', tr).innerHTML = sideTag(o.side);
        tween($('.fx-pc', tr), o.p, 1, '%', first ? 900 : 520);
        $('.fx-mt i', tr).style.transform = `scaleX(${Math.max(0, Math.min(1, (o.p - 50) / 15))})`;
        $('.fx-gr', tr).innerHTML = o.g ? grade(o.g) + `<small>Stake <b>${o.u}U</b></small>` : `<span class="fx-lean">Lean ${o.side}</span><small>Stake <b>0U</b></small>`;
        const h = $('.fx-hit', tr); h.textContent = Math.round(rate * 100) + '%'; h.className = 'fx-hit ' + hitCls(rate);
        const ed = $('.fx-ed', tr); ed.innerHTML = `<b>${sgn(o.edge)}</b> pts`; ed.className = 'fx-ed ' + (o.edge > 0 ? 'pos' : 'neg');
        tr.classList.toggle('moved', !!o.from);
      });
      $('.fx-legend', el).textContent = `Edge: the model estimate minus the per-pick break-even of a ${app === 'ud' ? 'Underdog standard' : 'PrizePicks Power'} 2-pick entry (${BE[app]}%), in points.`;
    }
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-hd"><div><h3>CS2 player props</h3><p>6 players · 12 lines</p></div>
      <div class="fx-chips" role="group" aria-label="App"><button type="button" class="fx-chip" data-app="ud" aria-pressed="true">Underdog</button><button type="button" class="fx-chip" data-app="pp" aria-pressed="false">PrizePicks</button></div></div>
      <div class="fx-tbl" role="table" aria-label="Saved example of the CS2 board"><div class="fx-th" role="row"><span role="columnheader">Player</span><span role="columnheader">Market</span><span role="columnheader">Line</span><span role="columnheader">Proj.</span><span role="columnheader">Model</span><span role="columnheader">L10</span><span role="columnheader">Edge</span></div>
      ${ROWS.map((r, i) => `<div class="fx-row" role="row" style="--i:${i}"><span class="fx-pl" role="cell">${av(r.name)}<span><b>${esc(r.name)}</b><small>${esc(r.match)}</small></span></span>
        <span class="fx-mk" role="cell"><b>Kills</b><small>Maps 1-2</small></span><span class="fx-ln" role="cell"><b></b><small></small></span><span class="fx-pj" role="cell">${f1(r.proj)}</span>
        <span class="fx-md" role="cell"><span class="fx-sd"></span><b class="fx-pc" data-v="50">50.0%</b><span class="fx-mt"><i></i></span><span class="fx-gr"></span></span>
        <span class="fx-hit" role="cell"></span><span class="fx-ed" role="cell"></span></div>`).join('')}</div>
      <p class="fx-legend"></p></div>`;
    act(el, '.fx-chip', c => { app = c.dataset.app; paint(false); });
    return {
      reset() { app = 'ud'; $$('.fx-pc', el).forEach(p => { p.dataset.v = 50; }); paint(true); },
      final() { paint(false); },
      async auto(k) { await k.wait(1900); await k.click($('[data-app="pp"]', el)); await k.wait(2100); await k.to($$('.fx-row', el)[5], 700); await k.wait(900); await k.click($('[data-app="ud"]', el)); await k.wait(600); }
    };
  });

  /* ---- 2. The player card ---- */
  stage('fx-card', el => {
    let win = 'l10';
    const LINE = 30.5, MAX = 50;
    function chart() {
      const s = win === 'l10' ? SERIES : H2H, under = s.filter(x => x[2] < LINE).length;
      $('.fx-bars', el).innerHTML = s.map((x, i) => `<button type="button" class="fx-bw ${x[2] < LINE ? 'hit' : 'miss'}" style="--h:${x[2] / MAX};--i:${i}" aria-label="${x[0]} vs ${esc(x[1])}: ${x[2]} kills, ${x[2] < LINE ? 'Under' : 'Over'}"><span class="fx-tip"><b>${x[2]}</b> kills<br>${x[0]} · ${esc(x[1])}</span><i></i><em>${x[2]}</em></button>`).join('') + `<span class="fx-lline" style="--h:${LINE / MAX}"><b>${LINE}</b></span>`;
      $('.fx-tally', el).innerHTML = `<b>${under} of ${s.length}</b> Under ${LINE}${win === 'h2h' ? ' against HEROIC’s current core' : ''}`;
      $$('.fx-seg button', el).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.win === win)));
    }
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-id">${av('Twistzz')}<div><h3>Twistzz</h3><p>FaZe · IGL · Rifler</p></div><div class="fx-mt2"><b>FaZe</b><small>VRS #27</small><span>Best of 3</span><b>HEROIC</b><small>VRS #21</small></div></div>
      <div class="fx-stats">${[['Maps', '150'], ['Kills/map', '15.3'], ['HS/map', '9.5'], ['KPR', '0.716'], ['Rating', '1.11'], ['KAST', '74.9%']].map(s => `<div><small>${s[0]}</small><b>${s[1]}</b></div>`).join('')}</div>
      <div class="fx-dec"><div><small>PrizePicks · Kills · Maps 1-2</small><strong>Under 30.5</strong></div><div class="fx-est"><b class="fx-big" data-v="50">50.0%</b><span class="fx-mt"><i></i></span><small>Model estimate</small></div>${grade('B')}<small class="fx-stk">Stake <b>0.5U</b></small></div>
      <div class="fx-two"><div class="fx-panel"><div class="fx-ph"><h4>Results at this line</h4><div class="fx-seg" role="group" aria-label="Sample"><button type="button" data-win="l10" aria-pressed="true">Last 10</button><button type="button" data-win="h2h" aria-pressed="false">vs HEROIC</button></div></div><div class="fx-bars"></div><p class="fx-tally"></p></div>
      <div class="fx-panel"><div class="fx-ph"><h4>Behind the grade</h4><small>projection 28.8</small></div><ul class="fx-fac">${FACTORS.map((f, i) => `<li style="--i:${i}"><span>${esc(f[0])}<small>${f[1]}</small></span><span class="fx-fb ${f[2] > 0 ? 'up' : 'dn'}"><i style="--w:${Math.abs(f[2]) / 17.5}"></i></span><b>${sgn(f[2])}%</b></li>`).join('')}</ul></div></div></div>`;
    act(el, '.fx-seg button', b => { win = b.dataset.win; chart(); });
    const est = () => { tween($('.fx-big', el), 58.8, 1, '%', 1100); $('.fx-est .fx-mt i', el).style.transform = `scaleX(${(58.8 - 50) / 15})`; };
    return {
      reset() { win = 'l10'; $('.fx-big', el).dataset.v = 50; chart(); est(); },
      final() { chart(); est(); },
      async auto(k) { await k.wait(2300); await k.to($$('.fx-bw', el)[3], 700); await k.wait(1100); await k.click($('[data-win="h2h"]', el)); await k.wait(2000); await k.click($('[data-win="l10"]', el)); await k.wait(500); }
    };
  });

  /* ---- 3. The map picker (HLTV totals summed, then divided: the member card's own maths) ---- */
  stage('fx-maps', el => {
    let sel = [];
    const PACE = 15.25;
    function paint() {
      $$('.fx-map', el).forEach(b => { const i = sel.indexOf(b.dataset.k); b.setAttribute('aria-pressed', String(i >= 0)); $('.fx-n', b).textContent = i >= 0 ? i + 1 : ''; });
      const out = $('.fx-comb', el), m = sel.map(k => MAPS.find(x => x[0] === k));
      $('.fx-mh span', el).textContent = sel.length === 2 ? '2 maps combined · a third pick replaces the first' : sel.length === 1 ? '1 of 2 picked' : 'Pick 2 to combine';
      if (m.length < 2) { out.className = 'fx-comb empty'; out.innerHTML = `<p>${m.length ? esc(m[0][1]) + ' picked. Pick one more map.' : 'Pick two maps to see his combined kills a map.'}</p>`; return; }
      const v = (m[0][3] + m[1][3]) / (m[0][2] + m[1][2]), d = v - PACE;
      out.className = 'fx-comb ' + (d < 0 ? 'under' : 'over');
      out.innerHTML = `<div><small>${esc(m[0][1])} + ${esc(m[1][1])}</small><strong><b class="fx-cv" data-v="${PACE}">${PACE.toFixed(2)}</b> kills a map</strong></div><div class="fx-dl"><b>${sgn(d, 2)}</b><small>vs line pace ${PACE}</small></div><div class="fx-pace"><i style="--x:${Math.max(0.04, Math.min(0.96, 0.5 + d / 2))}"></i><span></span></div><p>${m[0][2] + m[1][2]} maps in the last 12 months (HLTV). ${d < 0 ? 'Below' : 'Above'} the pace Under 30.5 needs.</p>`;
      tween($('.fx-cv', out), Number(v.toFixed(2)), 2, '', 650);
    }
    el.innerHTML = appbar('Board') + `<div class="fx-pad"><div class="fx-id sm">${av('Twistzz')}<div><h3>Twistzz · Under 30.5 Kills</h3><p>Maps 1-2 · line pace 15.25 kills a map</p></div></div>
      <div class="fx-veto"><small>Most likely veto</small>${Object.entries(VETO).map(([k, s]) => `<span><i>${s}</i>${MAPS.find(m => m[0] === k)[1]}</span>`).join('')}<em>HEROIC picked Ancient in 4 of its last 8 vetoes · FaZe picked Mirage in 4 of 8</em></div>
      <div class="fx-mh"><h4>Maps</h4><span></span></div>
      <div class="fx-mgrid">${MAPS.map((m, i) => `<button type="button" class="fx-map m-${m[0]}" data-k="${m[0]}" aria-pressed="false" style="--i:${i}"><span class="fx-n" aria-hidden="true"></span><b>${m[1]}</b><span class="fx-kpm">${(m[3] / m[2]).toFixed(2)}<small>kills/map</small></span><small>${m[2]} maps${VETO[m[0]] ? ' · ' + VETO[m[0]] : ''}</small></button>`).join('')}</div>
      <div class="fx-comb empty" aria-live="polite"></div></div>`;
    act(el, '.fx-map', b => { const k = b.dataset.k, i = sel.indexOf(k); if (i >= 0) sel.splice(i, 1); else { sel.push(k); if (sel.length > 2) sel.shift(); } paint(); });
    return {
      reset() { sel = []; paint(); },
      final() { sel = ['ancient', 'mirage']; paint(); },
      async auto(k) { await k.wait(1200); await k.click($('[data-k="ancient"]', el)); await k.wait(500); await k.click($('[data-k="mirage"]', el)); await k.wait(2600); await k.click($('[data-k="nuke"]', el)); await k.wait(900); }
    };
  });

  /* ---- 4. Line comparison and line moves ---- */
  stage('fx-lines', el => {
    let app = 'ud';
    const T = ROWS[0], LO = 27.5, HI = 31.5, x = v => ((v - LO) / (HI - LO)) * 100;
    function paint() {
      const o = T[app];
      $$('.fx-book', el).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.app === app)));
      $('.fx-sel', el).style.left = x(o.line) + '%';
      tween($('.fx-lp', el), o.p, 1, '%', 600);
      $('.fx-lgr', el).innerHTML = o.g ? grade(o.g) : '<span class="fx-lean">Lean Under</span>';
      $('.fx-lw', el).textContent = `Under ${o.line} on ${APP[app]}`;
      $('.fx-facts', el).innerHTML = [['Gap to projection', f1(o.line - 28.75) + ' kills'], ['Break-even per pick', BE[app] + '%'], ['Entry', app === 'ud' ? 'Standard 2-pick' : 'Power 2-pick'], ['Edge', sgn(o.edge) + ' pts']].map(f => `<div><small>${f[0]}</small><b>${f[1]}</b></div>`).join('');
    }
    el.innerHTML = appbar('Tools') + `<div class="fx-pad"><div class="fx-id sm">${av('Twistzz')}<div><h3>Twistzz · Kills · Maps 1-2</h3><p>FaZe vs HEROIC · one projection, two lines</p></div><span class="fx-flag">Lines differ · 1.0</span></div>
      <div class="fx-books"><button type="button" class="fx-book" data-app="ud" aria-pressed="true"><small>Underdog</small><strong>29.5</strong><span class="fx-mv"><svg viewBox="0 0 120 34" aria-hidden="true"><path class="fx-step" d="M2 8H52V26H118"/><circle cx="2" cy="8" r="2.5"/><circle class="fx-now" cx="118" cy="26" r="3"/></svg><em>Opened 30.5 · moved to 29.5 after 15 min</em></span></button>
      <button type="button" class="fx-book" data-app="pp" aria-pressed="false"><small>PrizePicks</small><strong>30.5</strong><span class="fx-mv"><svg viewBox="0 0 120 34" aria-hidden="true"><path class="fx-step" d="M2 8H118"/><circle cx="2" cy="8" r="2.5"/><circle class="fx-now" cx="118" cy="8" r="3"/></svg><em>Opened 30.5 · has not moved</em></span></button></div>
      <div class="fx-track" aria-hidden="true"><span class="fx-proj" style="left:${x(28.75)}%"><b>28.8</b><small>projection</small></span><span class="fx-tk" style="left:${x(29.5)}%"><i></i>29.5</span><span class="fx-tk" style="left:${x(30.5)}%"><i></i>30.5</span><span class="fx-sel"></span></div>
      <div class="fx-res"><div><small class="fx-lw"></small><b class="fx-lp" data-v="53.8">53.8%</b><small>Model estimate</small></div><span class="fx-lgr"></span></div>
      <div class="fx-facts"></div></div>`;
    act(el, '.fx-book', b => { app = b.dataset.app; paint(); });
    return {
      reset() { app = 'ud'; paint(); },
      final() { app = 'pp'; paint(); },
      async auto(k) { await k.wait(2200); await k.click($('[data-app="pp"]', el)); await k.wait(2600); await k.click($('[data-app="ud"]', el)); await k.wait(2200); await k.click($('[data-app="pp"]', el)); await k.wait(500); }
    };
  });

  /* ---- 5. Slip Studio: + on a board row, the toast, the leg lands in the slip, Power / Flex, the payout ---- */
  stage('fx-slip', el => {
    let legs = [], entry = 'power', toastT = null;
    const leg = id => LEGS.find(l => l.id === id);
    function panel() {
      const n = legs.length, ok = n >= 2, flexOk = !!FLEX[n];
      if (!flexOk) entry = 'power';
      $('.fx-cnt', el).textContent = n; $('.fx-cnt2', el).textContent = n;
      $$('.fx-add', el).forEach(b => { const on = legs.includes(b.dataset.id); b.setAttribute('aria-pressed', String(on)); b.setAttribute('aria-label', `${on ? 'Remove' : 'Add'} ${leg(b.dataset.id).name}`); b.closest('.fx-srow').classList.toggle('picked', on); });
      $('.fx-legs', el).innerHTML = n ? legs.map(id => { const l = leg(id); return `<li data-id="${id}">${av(l.name)}<div><b>${esc(l.name)} <i>${l.sport}</i></b><span>${l.side} <b>${l.line}</b> ${esc(l.stat)} · ${l.per}</span><small><b>${f1(l.p)}%</b> Model estimate</small></div>${grade(l.g)}<button type="button" class="fx-rm" data-id="${id}" aria-label="Remove ${esc(l.name)}">×</button></li>`; }).join('') : '<li class="fx-none">Add props from the board. Your slip builds here.</li>';
      $$('.fx-ent button', el).forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.e === entry)); b.disabled = b.dataset.e === 'flex' && !flexOk; });
      const pay = $('.fx-pay', el), st = $('.fx-st', el);
      if (!ok) { pay.innerHTML = `<b>—</b><small>Add ${2 - n} more to price the entry</small>`; st.className = 'fx-st'; st.innerHTML = '<i></i>Building…'; return; }
      pay.innerHTML = entry === 'power' ? `<b>${POWER[n]}×</b><small>All ${n} hit · PrizePicks Power ${n}-pick</small>` : `<b>${FLEX[n][0]}×</b><small>All ${n} hit · <b>${FLEX[n][1]}×</b> with one miss · Flex ${n}-pick</small>`;
      st.className = 'fx-st ok'; st.innerHTML = '<i></i>1 of 1 entry ready · every leg rechecked';
    }
    function toast(msg) { const t = $('.fx-toast', el); t.innerHTML = `<i aria-hidden="true"></i>${esc(msg)}`; t.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), 1900); }
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
    el.innerHTML = appbar('Slips') + `<div class="fx-split"><div class="fx-pad fx-sboard"><div class="fx-hd"><div><h3>All sports · PrizePicks</h3><p>Graded lines you can add</p></div><span class="fx-slipbtn">Slip <b class="fx-cnt">0</b></span></div>
      ${LEGS.map((l, i) => `<div class="fx-srow" style="--i:${i}">${av(l.name)}<span class="fx-pl2"><b>${esc(l.name)} <i>${l.sport}</i></b><small data-pick="${l.side} ${l.line} ${esc(l.stat)}">${esc(l.sub)}</small></span><span class="fx-mk"><b>${l.side} ${l.line}</b><small>${esc(l.stat)}</small></span><span class="fx-md2"><b>${f1(l.p)}%</b>${grade(l.g)}</span><button type="button" class="fx-add" data-id="${l.id}" aria-pressed="false"><svg viewBox="0 0 16 16" aria-hidden="true"><path class="p" d="M8 3v10M3 8h10"/><path class="c" d="M3.5 8.5l3 3 6-7"/></svg></button></div>`).join('')}
      <div class="fx-toast" role="status"></div></div>
      <aside class="fx-panel2" aria-label="Slip"><div class="fx-sh"><h4>Slip <b class="fx-cnt2">0</b></h4><span class="fx-st"><i></i>Building…</span></div><div class="fx-app">PrizePicks <small>one entry per app</small></div>
      <div class="fx-ent" role="group" aria-label="Entry type"><button type="button" data-e="power" aria-pressed="true">Power</button><button type="button" data-e="flex" aria-pressed="false">Flex</button></div>
      <ul class="fx-legs"></ul><div class="fx-pay"></div><a class="fx-go" href="${esc($('.fx').dataset.trial || '#pricing')}">Build yours free for 1 day</a></aside></div>`;
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
