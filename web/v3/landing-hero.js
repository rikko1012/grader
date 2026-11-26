/* Prop Grader landing hero (owner 2026-11-26: "the hero must be the true interactive visualization of the product").
   One member window: the board, the selected player's card beside it, the slip tray under it. Every number is a saved
   example from the member board of 2026-11-25 18:32 UTC (CS2, PGL Major Singapore 2026 Stage 1, Kills Maps 1-2) - the
   same rows the feature sections use. Nothing is read from the member API and nothing here is a current line.
   Payout multiples: the published PrizePicks Power table the member Slip Studio uses (payout_reference.py).
   Motion: a cursor drives the real controls once on load; any real click, tap or key stops it for good; Replay
   plays it again. prefers-reduced-motion gets the final frame. */
(() => {
  'use strict';
  const root = document.getElementById('hx');
  if (!root) return;
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (s, r) => (r || root).querySelector(s);
  const $$ = (s, r) => [...(r || root).querySelectorAll(s)];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const APP = {pp: 'PrizePicks', ud: 'Underdog'};
  const POWER = {2: 3, 3: 6, 4: 10};
  const ROWS = [
    {id: 'twi', name: 'Twistzz', role: 'IGL · Rifler', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 28.8,
      ud: {line: 29.5, side: 'Under', p: 53.8, g: null, hit: 7}, pp: {line: 30.5, side: 'Under', p: 58.8, g: 'B', hit: 7}},
    {id: 'jbo', name: 'JBOEN', role: 'AWPer', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 28.4,
      ud: {line: 29.5, side: 'Under', p: 55.7, g: 'B-', hit: 5}, pp: {line: 30, side: 'Under', p: 58.6, g: 'B', hit: 5}},
    {id: 'jco', name: 'jcobbb', role: 'Entry', team: 'FaZe', match: 'FaZe vs HEROIC', proj: 26.7,
      ud: {line: 28.5, side: 'Under', p: 59.3, g: 'B', hit: 7}, pp: {line: 28.5, side: 'Under', p: 59.3, g: 'B', hit: 7}},
    {id: 'c4l', name: 'C4LLM3SU3', role: 'Rifler', team: 'Lynn Vision', match: 'Lynn Vision vs PARIVISION', proj: 25.7,
      ud: {line: 23.5, side: 'Over', p: 61.7, g: 'B', hit: 7}, pp: {line: 25, side: 'Over', p: 54.1, g: null, hit: 7}},
    {id: 'emi', name: 'EmiliaQAQ', role: 'Rifler', team: 'Lynn Vision', match: 'Lynn Vision vs PARIVISION', proj: 25.9,
      ud: {line: 23.5, side: 'Over', p: 62.7, g: 'B', hit: 7}, pp: {line: 24.5, side: 'Over', p: 57.6, g: 'B', hit: 7}},
    {id: 'fl1', name: 'FL1T', role: 'Lurker', team: 'PARIVISION', match: 'Lynn Vision vs PARIVISION', proj: 27.5,
      ud: {line: 29.5, side: 'Under', p: 60.0, g: 'B', hit: 6}, pp: {line: 30, side: 'Under', p: 63.1, g: 'B+', hit: 6}}
  ];
  const byId = Object.fromEntries(ROWS.map(r => [r.id, r]));
  const S = {app: 'pp', sel: 'fl1', slip: []};          // slip: [{id, app}]

  const grade = o => o.g ? `<span class="hx-g${/^A|B\+/.test(o.g) ? ' hi' : ''}">${o.g.replace('-', '−')}</span>`
    : `<span class="hx-lean">Lean</span>`;
  const side = s => `<span class="hx-side ${s === 'Over' ? 'ov' : 'un'}">${s}</span>`;
  const inSlip = id => S.slip.some(l => l.id === id);
  const PLUS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path class="p" d="M8 3v10M3 8h10"/><path class="c" d="M3.5 8.5l3 3 6-7"/></svg>';

  root.innerHTML = `
<div class="hx-win">
  <div class="hx-bar"><span class="hx-brand"><i>1T</i>Prop Grader</span>
    <nav aria-hidden="true"><span class="on">Board</span><span>Slips</span><span>My picks</span><span>Record</span></nav>
    <span class="hx-tag">Saved example</span>
    <button class="hx-slipbtn" type="button" data-act="slipbtn">Slip <b class="hx-cnt">0</b></button></div>
  <div class="hx-body">
    <section class="hx-board" aria-label="Sample board">
      <div class="hx-head"><div><h3>CS2 player props</h3><p>PGL Major Singapore · Kills, Maps 1–2</p></div>
        <div class="hx-apps" role="group" aria-label="App">${['pp', 'ud'].map(a => `<button type="button" data-app="${a}" aria-pressed="false">${APP[a]}</button>`).join('')}</div></div>
      <div class="hx-cols" aria-hidden="true"><span>Player</span><span>Line</span><span>Grader side</span><span>Grade</span><span></span></div>
      <ul class="hx-rows">${ROWS.map(r => `<li class="hx-row" data-id="${r.id}">
        <button type="button" class="hx-pick" data-act="pick" aria-label="Open ${esc(r.name)}"><span class="hx-av" aria-hidden="true">${esc(r.name[0].toUpperCase())}</span><span class="hx-who"><b>${esc(r.name)}</b><small>${esc(r.team)} · ${esc(r.role)}</small></span></button>
        <span class="hx-ln"></span><span class="hx-sd"></span><span class="hx-gr"></span>
        <button type="button" class="hx-add" data-act="add" aria-label="Add ${esc(r.name)} to slip">${PLUS}</button></li>`).join('')}</ul>
    </section>
    <aside class="hx-card" aria-label="Player card" aria-live="polite"></aside>
  </div>
  <div class="hx-tray" aria-live="polite"></div>
</div>
<div class="hx-under"><button type="button" class="hx-replay" data-act="replay"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v2.8h2.8"/></svg>Replay</button>
<p>Saved examples from the member board, Nov 25 · not current picks. Click any player, switch apps, build the slip.</p></div>`;
  const trust = document.querySelector('.hx-hero .hero-trust');
  if (trust) root.appendChild(trust);      // the trust row moves under the window: the product sits higher on the first screen

  function paint() {
    $$('.hx-apps button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.app === S.app)));
    for (const r of ROWS) {
      const o = r[S.app], li = $(`.hx-row[data-id="${r.id}"]`);
      li.classList.toggle('on', r.id === S.sel);
      $('.hx-pick', li).setAttribute('aria-pressed', String(r.id === S.sel));
      $('.hx-ln', li).innerHTML = `<b>${o.line}</b>`;
      $('.hx-sd', li).innerHTML = `${side(o.side)}<b>${o.p.toFixed(1)}%</b>`;
      $('.hx-gr', li).innerHTML = grade(o);
      const add = $('.hx-add', li), on = inSlip(r.id);
      add.classList.toggle('on', on);
      add.setAttribute('aria-pressed', String(on));
      add.setAttribute('aria-label', `${on ? 'Remove' : 'Add'} ${r.name} ${on ? 'from' : 'to'} slip`);
    }
    card();
    tray();
  }

  function card() {
    const r = byId[S.sel], o = r[S.app], other = r[S.app === 'pp' ? 'ud' : 'pp'];
    const gap = r.proj - o.line, C = 2 * Math.PI * 34;
    const lo = Math.min(r.proj, o.line) - 4, hi = Math.max(r.proj, o.line) + 4, at = v => ((v - lo) / (hi - lo) * 100).toFixed(1);
    const on = inSlip(r.id);
    $('.hx-card').innerHTML = `
<div class="hx-who2"><span class="hx-av lg" aria-hidden="true">${esc(r.name[0].toUpperCase())}</span><div><h4>${esc(r.name)}</h4><p>${esc(r.role)} · ${esc(r.team)}</p></div></div>
<p class="hx-match">${esc(r.match)} <span>·</span> Kills, Maps 1–2 <span>·</span> ${APP[S.app]} ${o.line}</p>
<div class="hx-dec">
  <div class="hx-ring"><svg viewBox="0 0 80 80" aria-hidden="true"><circle cx="40" cy="40" r="34"/><circle class="f ${o.side === 'Over' ? 'ov' : 'un'}" cx="40" cy="40" r="34" style="stroke-dasharray:${C.toFixed(1)};stroke-dashoffset:${(C * (1 - o.p / 100)).toFixed(1)}"/></svg><b>${o.p.toFixed(1)}%</b></div>
  <div class="hx-call"><small>Grader side</small><strong>${o.side} ${o.line}</strong><small>${o.p.toFixed(1)}% model estimate</small></div>
  <div class="hx-letter">${grade(o)}</div>
</div>
<div class="hx-scale"><div class="hx-scale-l"><span>Projection <b>${r.proj.toFixed(1)}</b></span><span>Line <b>${o.line}</b></span></div>
  <div class="hx-track" aria-hidden="true"><i class="ln" style="left:${at(o.line)}%"></i><i class="pj ${gap >= 0 ? 'ov' : 'un'}" style="left:${at(r.proj)}%"></i></div>
  <p>Projection <b class="${gap >= 0 ? 'pos' : 'neg'}">${Math.abs(gap).toFixed(1)} ${gap >= 0 ? 'above' : 'below'}</b> the line</p></div>
<div class="hx-facts">
  <div><small>L10 at this line</small><b>${o.hit}<span>/10</span></b><i class="hx-meter" aria-hidden="true"><i style="width:${o.hit * 10}%"></i></i></div>
  <div><small>${APP[S.app === 'pp' ? 'ud' : 'pp']} line</small><b>${other.line}</b><span class="hx-oth">${other.side} ${other.p.toFixed(1)}% ${other.g ? '· ' + other.g.replace('-', '−') : '· Lean'}</span></div>
</div>
<button type="button" class="hx-cta${on ? ' on' : ''}" data-act="add-card" aria-pressed="${on}">${on ? 'In your slip ✓' : `Add ${o.side} ${o.line} to slip`}</button>`;
  }

  function tray() {
    const n = S.slip.length;
    $('.hx-cnt').textContent = n;
    $('.hx-slipbtn').classList.toggle('has', n > 0);
    const t = $('.hx-tray');
    if (!n) { t.innerHTML = `<span class="hx-empty">Your slip is empty · tap <b>+</b> on any row</span>`; t.classList.remove('has'); return; }
    t.classList.add('has');
    const apps = new Set(S.slip.map(l => l.app));
    const pay = apps.size === 1 && [...apps][0] === 'pp' && POWER[n] ? `<span class="hx-pay"><small>PrizePicks Power · ${n} picks</small><b>${POWER[n]}×</b></span>`
      : apps.size > 1 ? `<span class="hx-pay"><small>Two apps</small><b>1 slip per app</b></span>`
      : n === 1 ? `<span class="hx-pay"><small>Add one more</small><b>2+ picks</b></span>`
      : `<span class="hx-pay"><small>${APP[[...apps][0]]} · ${n} picks</small><b>Priced in Slip Studio</b></span>`;
    t.innerHTML = `<ul class="hx-legs">${S.slip.map(l => { const r = byId[l.id], o = r[l.app]; return `<li class="hx-leg"><b>${esc(r.name)}</b><span>${o.side} ${o.line}</span><button type="button" data-act="drop" data-id="${l.id}" aria-label="Remove ${esc(r.name)}">×</button></li>`; }).join('')}</ul>${pay}`;
  }

  function toggle(id) {
    const i = S.slip.findIndex(l => l.id === id);
    if (i >= 0) S.slip.splice(i, 1);
    else if (S.slip.length < 4) S.slip.push({id, app: S.app});
    paint();
    const li = $(`.hx-row[data-id="${id}"]`);
    if (!RM && li) { li.classList.remove('flash'); void li.offsetWidth; li.classList.add('flash'); }
  }
  function act(t) {
    const a = t.dataset.act;
    if (t.dataset.app) { S.app = t.dataset.app; paint(); return; }
    if (a === 'pick') { S.sel = t.closest('.hx-row').dataset.id; paint(); return; }
    if (a === 'add') { toggle(t.closest('.hx-row').dataset.id); return; }
    if (a === 'add-card') { toggle(S.sel); return; }
    if (a === 'drop') { toggle(t.dataset.id); return; }
    if (a === 'slipbtn') { $('.hx-tray').scrollIntoView({block: 'nearest', behavior: RM ? 'auto' : 'smooth'}); return; }
    if (a === 'replay') play();
  }
  const handler = e => { const t = e.target.closest('[data-act],[data-app]'); if (t && root.contains(t)) act(t); };
  root.addEventListener('click', handler);
  root.addEventListener('hx-act', handler);

  /* ---- the tour: a cursor clicks the real controls ---- */
  class Stop extends Error {}
  let tok = null;
  const cur = document.createElement('i'); cur.className = 'hx-cur'; cur.setAttribute('aria-hidden', 'true');
  $('.hx-win').appendChild(cur);
  function stop() { if (tok) { tok.dead = true; tok.timers.forEach(clearTimeout); tok = null; } cur.classList.remove('on'); root.classList.remove('hx-auto'); }
  const wait = (k, ms) => new Promise((res, rej) => { if (k.dead) return rej(new Stop()); const t = setTimeout(() => { k.timers.delete(t); k.dead ? rej(new Stop()) : res(); }, ms); k.timers.add(t); });
  async function click(k, el) {
    if (!el || k.dead) throw new Stop();
    const w = $('.hx-win').getBoundingClientRect(), b = el.getBoundingClientRect();
    cur.style.transform = `translate(${(b.left - w.left + b.width / 2).toFixed(0)}px,${(b.top - w.top + b.height / 2).toFixed(0)}px)`;
    cur.classList.add('on');
    await wait(k, 720);
    cur.classList.add('tap'); el.classList.add('hx-press');
    await wait(k, 150);
    cur.classList.remove('tap'); el.classList.remove('hx-press');
    el.dispatchEvent(new CustomEvent('hx-act', {bubbles: true}));
    await wait(k, 140);
  }
  const row = id => $(`.hx-row[data-id="${id}"]`);
  async function tour(k) {
    await wait(k, 1100);
    await click(k, $('.hx-pick', row('twi'))); await wait(k, 1500);
    await click(k, $('[data-app="ud"]')); await wait(k, 1500);
    await click(k, $('[data-app="pp"]')); await wait(k, 900);
    await click(k, $('[data-act="add-card"]')); await wait(k, 1100);
    await click(k, $('.hx-pick', row('fl1'))); await wait(k, 1300);
    await click(k, $('.hx-add', row('fl1'))); await wait(k, 1000);
    await click(k, $('.hx-add', row('emi'))); await wait(k, 1200);
  }
  function final() { S.app = 'pp'; S.sel = 'fl1'; S.slip = [{id: 'twi', app: 'pp'}, {id: 'fl1', app: 'pp'}, {id: 'emi', app: 'pp'}]; paint(); }
  async function play() {
    stop();
    S.app = 'pp'; S.sel = 'fl1'; S.slip = []; paint();
    if (RM) return final();
    const k = tok = {dead: false, timers: new Set()};
    root.classList.add('hx-auto');
    try { await tour(k); } catch (e) { if (!(e instanceof Stop)) throw e; }
    if (tok === k) { cur.classList.remove('on'); root.classList.remove('hx-auto'); tok = null; }
  }
  let user = false;                      // a visitor who touched the demo is never overridden by the tour
  ['pointerdown', 'keydown'].forEach(ev => root.addEventListener(ev, e => { if (e.isTrusted && !e.target.closest('.hx-replay')) { user = true; stop(); } }));
  document.addEventListener('visibilitychange', () => { if (document.hidden && tok) { stop(); final(); } });

  paint();
  if (RM) final();
  else if ('IntersectionObserver' in window) {
    let started = false;
    new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting && !started) { started = true; if (!user) play(); }
      else if (!e.isIntersecting && tok) { stop(); final(); }
    }), {threshold: 0.35}).observe(root);
  } else final();
})();
