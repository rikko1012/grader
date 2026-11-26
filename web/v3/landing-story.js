
/* Prop Grader landing (DESIGN_RULES.md section 6). Inlined by landing_v3.py. The page is view-only and server-rendered at its final
   values, so with no JS (or reduced motion) everything is already in its final frame. This adds only:
   - the fixed message for a failed sign-in code (/?signin_error=<code>), never text from the URL;
   - one-time reveals on the Inside panels (+ their text, 80 ms later, and the kills bars) and the record table;
   - count-up on the decided-pick count (the review score never ticks);
   - the Slip Studio timeline (pill "Building…", "Building · 1 of 4" ... "Slip ready"): plays once at 35% on screen (cards fly from the board into the slip, FLIP + WAAPI, transform/opacity
     only; each flown card stays in its board box dimmed to 30% (brief 6b); the slip CTA fades in after "Slip ready"; on phones the board is a 2x2 strip of mini cards
     above the slip and the same flight runs down into the slots), holds "Slip ready", re-arms only after the stage has fully left
     the screen. Off screen the stage always shows its final frame (a full-page capture or a no-data build never shows an empty
     slip); a hidden tab jumps it to the final frame;
   - the FAQ accordion (WAI-ARIA buttons, one open at a time, hidden="until-found" so find-in-page opens a panel);
   - the phone bar: after the hero button has left the screen, hidden over the Slip button, the pricing card, the final call to
     action and any focused element near the bottom; while it shows, body.dock-on hides the header's Try pill (one Try per screen)
     and slides "Sign in" right by the pill's width (--pill-shift, a transform: no hole in the header, no layout shift). Its link is
     in the tab order whenever the bar shows (a hidden bar is visibility:hidden, so never focusable).
   Motion runs only when html.motion is set (prefers-reduced-motion is not "reduce"). */
(function () {
  'use strict';
  var D = document, W = window, R = D.documentElement;
  var IO = 'IntersectionObserver' in W;
  var MOTION = R.classList.contains('motion') && IO;
  function $(s, r) { return (r || D).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || D).querySelectorAll(s)); }
  function safe(f) { try { f(); } catch (e) { if (W.console) console.warn('landing', e); } }
  function io(els, opts, fn) {
    var o = new IntersectionObserver(function (es) { es.forEach(function (e) { fn(e, o); }); }, opts);
    els.forEach(function (el) { o.observe(el); });
    return o;
  }

  safe(function () {
    var code = new URLSearchParams(location.search).get('signin_error');
    if (!code) return;
    var msg = { expired: 'Sign-in expired. Please try again.', whop: 'Whop sign-in did not complete. Please try again.',
      no_account: 'Whop did not return your account. Please try again.' };
    var el = D.getElementById('signin-error');
    el.textContent = Object.prototype.hasOwnProperty.call(msg, code) ? msg[code] : 'Sign-in did not complete. Please try again.';
    el.hidden = false;
  });

  /* reveals */
  safe(function () {
    var els = $$('.rv');
    if (!MOTION) { els.forEach(function (el) { el.classList.add('in'); }); return; }
    io(els, { threshold: 0.1 }, function (e, o) {
      if (!e.isIntersecting) return;
      o.unobserve(e.target);
      e.target.classList.add('in');
      var row = e.target.closest('.icol');
      if (row) row.classList.add('in');
    });
  });

  /* proof tickers */
  safe(function () {
    if (!MOTION) return;
    var nf = new Intl.NumberFormat('en-US');
    io($$('.tk[data-to]'), { threshold: 0.6 }, function (e, o) {
      if (!e.isIntersecting) return;
      o.unobserve(e.target);
      var el = e.target, to = parseFloat(el.getAttribute('data-to')), fin = el.textContent;
      var pre = el.getAttribute('data-pre') || '', suf = el.getAttribute('data-suf') || '', t0 = null;
      if (!isFinite(to) || to <= 0) return;
      function step(t) {
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / 1000);
        el.textContent = k < 1 ? pre + nf.format(Math.round(to * (1 - Math.pow(1 - k, 3)))) + suf : fin;
        if (k < 1) requestAnimationFrame(step);
      }
      el.textContent = pre + '0' + suf;
      requestAnimationFrame(step);
    });
  });

  /* Slip Studio timeline */
  safe(function () {
    var st = D.getElementById('ss');
    if (!st || !MOTION || !st.animate) return;
    var boxes = $$('.ss-board .sbx', st), slots = $$('.slot', st), pill = D.getElementById('ss-st');
    var LEG = [300, 1300, 2300, 3300], READY = 4400, FLY = 650, DROP = 650;
    var timers = [], ghosts = [], state = 'idle', seen = false;           /* idle (final frame, armed) -> playing -> done -> idle off screen */
    function later(ms, f) { timers.push(setTimeout(f, ms)); }
    function clear() {
      timers.forEach(clearTimeout); timers = [];
      ghosts.forEach(function (g) { if (g.parentNode) g.parentNode.removeChild(g); }); ghosts = [];
    }
    function start() {
      clear();
      st.classList.add('run'); st.classList.remove('ready');
      boxes.forEach(function (b) { b.classList.remove('gone'); });
      slots.forEach(function (s) { s.classList.remove('on'); });
      pill.textContent = 'Building\u2026';
      state = 'idle';
    }
    function land(i) {
      var s = slots[i];
      s.classList.add('on');
      pill.textContent = 'Building \u00b7 ' + (i + 1) + ' of 4';
      var sc = $('.sc', s);
      if (sc) sc.animate([{ transform: 'scale(.95)' }, { transform: 'scale(1)' }], { duration: 240, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    function drop(i) {
      var s = slots[i];
      if (!s) return;
      s.classList.add('on');
      pill.textContent = 'Building \u00b7 ' + (i + 1) + ' of 4';
      var sc = $('.sc', s);
      if (sc) sc.animate([{ transform: 'translateY(-16px)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: DROP, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    function fly(i) {
      var bx = boxes[i], s = slots[i], c = bx && $('.sc', bx);
      if (!c || !s) return;
      if (!c.offsetParent) { drop(i); return; }
      var sr = st.getBoundingClientRect(), a = c.getBoundingClientRect(), b = s.getBoundingClientRect();
      var mini = a.width < b.width * 0.8;                    /* phones: a mini card flies down and hands over to the full slot card */
      var g = c.cloneNode(true);
      g.classList.add('ghost'); if (mini) g.classList.add('mini');
      g.style.width = a.width + 'px'; g.style.height = a.height + 'px';
      st.appendChild(g); ghosts.push(g);
      bx.classList.add('gone');
      var x0 = a.left - sr.left, y0 = a.top - sr.top, x1 = b.left - sr.left, y1 = b.top - sr.top + (mini ? (b.height - a.height) / 2 : 0);
      var an = g.animate([
        { transform: 'translate(' + x0 + 'px,' + y0 + 'px) rotate(0deg)', opacity: 1 },
        { transform: 'translate(' + (x0 + x1) / 2 + 'px,' + ((y0 + y1) / 2 - 12) + 'px) rotate(-2deg)', opacity: 1, offset: 0.5 },
        { transform: 'translate(' + x1 + 'px,' + y1 + 'px) rotate(0deg)', opacity: mini ? 0 : 1 }
      ], { duration: FLY, easing: 'cubic-bezier(.77,0,.175,1)', fill: 'forwards' });
      if (mini) { var tl = setTimeout(function () { land(i); }, FLY * 0.7); timers.push(tl); }
      an.onfinish = function () { if (!mini) land(i); if (g.parentNode) g.parentNode.removeChild(g); };
    }
    function finish() {                                      /* the final frame: what a still page shows */
      clear();
      st.classList.remove('run', 'ready');
      boxes.forEach(function (b) { b.classList.remove('gone'); });
      slots.forEach(function (s) { s.classList.remove('on'); });
      pill.textContent = 'Slip ready';
      state = 'done';
    }
    function rearm() { finish(); state = 'idle'; }             /* the final frame on screen, ready to play on the next entry */
    function play() {
      start(); state = 'playing';
      LEG.forEach(function (t, i) { later(t, function () { fly(i); }); });
      later(READY, function () {
        pill.textContent = 'Slip ready'; st.classList.add('ready'); state = 'done';
        if (!seen) rearm();                                 /* ended off screen: replay on the next entry */
      });
    }
    rearm();
    io([st], { threshold: [0, 0.35] }, function (e) {
      seen = e.isIntersecting;
      if (e.intersectionRatio >= 0.35 && state === 'idle' && !D.hidden) play();
      else if (!e.isIntersecting && state !== 'idle') rearm();  /* fully off screen: final frame, replays on the next entry */
    });
    D.addEventListener('visibilitychange', function () { if (D.hidden && state === 'playing') finish(); });
  });

  /* FAQ accordion */
  safe(function () {
    var acc = D.getElementById('acc');
    if (!acc) return;
    var items = $$('.ai', acc);
    function btn(it) { return $('button', it); }
    function panel(it) { return $('.ap', it); }
    function set(it, open, animate) {
      var b = btn(it), p = panel(it);
      if ((b.getAttribute('aria-expanded') === 'true') === open) return;
      b.setAttribute('aria-expanded', open ? 'true' : 'false');
      it.classList.toggle('open', open);
      if (!animate || !MOTION) {
        p.style.height = '';
        if (open) p.removeAttribute('hidden'); else p.setAttribute('hidden', 'until-found');
        return;
      }
      p.classList.remove('anim');
      if (open) {
        p.removeAttribute('hidden');
        p.style.height = '0px';
      } else {
        p.style.height = p.scrollHeight + 'px';
      }
      void p.offsetHeight;
      p.classList.add('anim');
      p.style.height = open ? p.scrollHeight + 'px' : '0px';
      var done = function (ev) {
        if (ev && ev.target !== p) return;
        p.removeEventListener('transitionend', done);
        p.classList.remove('anim');
        if (b.getAttribute('aria-expanded') === 'true') p.style.height = '';
        else { p.setAttribute('hidden', 'until-found'); p.style.height = ''; }
      };
      p.addEventListener('transitionend', done);
      setTimeout(done, 400);
    }
    items.forEach(function (it) {
      if (btn(it).getAttribute('aria-expanded') !== 'true') panel(it).setAttribute('hidden', 'until-found');
      btn(it).addEventListener('click', function () {
        var open = btn(it).getAttribute('aria-expanded') !== 'true';
        items.forEach(function (o) { if (o !== it) set(o, false, true); });
        set(it, open, true);
      });
      panel(it).addEventListener('beforematch', function () {
        items.forEach(function (o) { if (o !== it) set(o, false, false); });
        set(it, true, false);
      });
    });
    acc.classList.add('ready');
  });

  /* phone bar */
  safe(function () {
    var dock = D.getElementById('dock'), hero = D.getElementById('hero-cta');
    if (!dock || !hero || !IO) return;
    var past = false, over = {}, low = false;
    var pill = $('.btn-nav'), si = $('.nav-cta .tl');
    function measure() {                                     /* how far "Sign in" slides to sit flush right while the pill is hidden */
      if (pill && si && !D.body.classList.contains('dock-on')) R.style.setProperty('--pill-shift', (pill.getBoundingClientRect().right - si.getBoundingClientRect().right) + 'px');
    }
    measure();
    W.addEventListener('resize', measure, { passive: true });
    if (D.fonts && D.fonts.ready) D.fonts.ready.then(measure);
    function set() {
      var show = past && !low && !Object.keys(over).some(function (k) { return over[k]; });
      if (dock.classList.contains('show') === show) return;
      dock.classList.toggle('show', show);
      D.body.classList.toggle('dock-on', show);
    }
    io([hero], { threshold: 0 }, function (e) { past = !e.isIntersecting && e.boundingClientRect.top < 0; set(); });
    var cover = $$('.ss-cta, .ss-go, #plan, #final');
    cover.forEach(function (el, i) { el.setAttribute('data-dock', i); });
    io(cover, { threshold: 0 }, function (e) { over[e.target.getAttribute('data-dock')] = e.isIntersecting; set(); });
    function focusCheck() {
      var a = D.activeElement;
      low = !!(a && a !== D.body && !dock.contains(a) && a.getBoundingClientRect().bottom > innerHeight - 96);
      set();
    }
    D.addEventListener('focusin', focusCheck);
    D.addEventListener('focusout', function () { setTimeout(focusCheck, 0); });
    W.addEventListener('scroll', function () { if (low) focusCheck(); }, { passive: true });
  });
})();


(() => {
 'use strict';
 const $=s=>document.querySelector(s), reduced=matchMedia('(prefers-reduced-motion: reduce)');
 if(!$('#demo-board')) return;        // the new hero (landing-hero.js) replaces this block
 const players=[
  {name:'Vexite',role:'Star rifler',team:'FlyQuest',opponent:'Eternal Fire',stat:'Kills',period:'Map 1',line:14.5,projection:16.8,chance:65.2,grade:'B+',side:'Over',book:'PrizePicks',record:[3,5,11]},
  {name:'1NVISIBLEE',role:'IGL',team:'Lunacy',opponent:'Sökerorg',stat:'Kills',period:'Maps 1–2',line:29.5,projection:32.7,chance:64.6,grade:'B+',side:'Over',book:'Underdog',record:[3,8,12]},
  {name:'nettik',role:'Rifler',team:'FlyQuest',opponent:'Eternal Fire',stat:'Kills',period:'Map 1',line:13,projection:14.9,chance:64.1,grade:'B+',side:'Over',book:'PrizePicks',record:[4,7,12]},
  {name:'xm1nd',role:'AWPer',team:'Lunacy',opponent:'Sökerorg',stat:'Kills',period:'Maps 1–2',line:30.5,projection:33.5,chance:63.6,grade:'B+',side:'Over',book:'Underdog',record:[5,9,14]},
  {name:'fr3nd',role:'AWPer',team:'Wildcard',opponent:'FaZe',stat:'Kills',period:'Map 1',line:12.5,projection:14.1,chance:61.6,grade:'B',side:'Over',book:'Underdog',record:[3,6,13]},
  {name:'malbsMd',role:'Entry',team:'Liquid',opponent:'BC.Game',stat:'Kills',period:'Map 1',line:15.5,projection:14,chance:60.7,grade:'B',side:'Under',book:'Underdog',record:[2,4,10]}
 ];
 let selected=0,phoneTab=1,heroTimer=null,heroVisible=false,heroPaused=reduced.matches;
 const heroNotes=[['THE BOARD','Select a player. Open the reasoning.','Search the sample board or choose a player. The phone card follows your selection.'],['KEY INSIGHTS','See what supports the side.','The card connects the saved projection to the exact line, with recent history kept separate.'],['BEHIND THE GRADE','Read the numbers behind the letter.','Inspect the saved model estimate, side, line and grade together.']];
 function board(){
  $('#demo-board').innerHTML=`<div class="demo-app"><div class="demo-nav"><strong><i>1T</i>Prop Grader</strong><span class="current">Board</span><span>Slips</span><span>EV</span><span>Arbitrage</span><span>Sportsbooks</span><span>My picks</span><span>Record</span></div><div class="demo-top"><h3>CS2 player props</h3><p>CS2 sample board · select any player to explore</p><div class="demo-sports"><b>CS2</b><span>LoL</span><span>NBA</span><span>NFL</span><span>Soccer</span><span>Tennis</span></div><div class="demo-board-tools"><input id="demo-search" type="search" aria-label="Search demo players" placeholder="Search a player or team…" autocomplete="off"><span class="demo-chip">All apps</span><span class="demo-chip muted">Saved examples</span></div></div><div class="demo-table-wrap"><table class="demo-table" aria-label="Interactive sample prop board"><thead><tr><th>Player / matchup</th><th>Market</th><th>App line</th><th>Projection</th><th>Grader side</th><th>L5 / L10 / L20</th></tr></thead><tbody id="demo-rows"></tbody></table><p id="demo-no-results" class="demo-empty" hidden>No matching sample players. Try Vexite or FlyQuest.</p></div><div class="demo-footer"><span id="demo-count">6 sample players</span><span>Click a player to research</span></div></div>`;
  $('#demo-search').addEventListener('input',rows);$('#demo-rows').addEventListener('click',e=>{const b=e.target.closest('[data-player]');if(!b)return;selected=Number(b.dataset.player);heroPaused=true;phoneTab=0;rows();phone();heroPlayback();});rows();
 }
 function rows(){const q=$('#demo-search').value.trim().toLowerCase();let count=0;
  $('#demo-rows').innerHTML=players.map((p,i)=>{if(!`${p.name} ${p.team} ${p.opponent}`.toLowerCase().includes(q))return '';count++;return `<tr class="${i===selected?'chosen':''}"><td><button class="demo-player" data-player="${i}" aria-label="Explore ${p.name}" aria-pressed="${i===selected}"><b>${p.name[0]}</b><span>${p.name}<small>${p.role} · ${p.team} vs ${p.opponent}</small></span></button></td><td>${p.stat}<small>${p.period}</small></td><td><b>${p.line}</b><small>${p.book}</small></td><td>${p.projection.toFixed(1)}</td><td><span class="demo-side"><small>${p.side}</small><b>${p.chance}%</b></span><small><span class="demo-grade">${p.grade}</span></small></td><td class="demo-rate">${p.record.map((v,k)=>`<span><b class="${v/[5,10,20][k]>=.6?'rate-good':v/[5,10,20][k]<.4?'rate-poor':''}">${v}</b>/${[5,10,20][k]}</span>`).join(' · ')}</td></tr>`;}).join('');
  $('#demo-no-results').hidden=count>0;$('#demo-count').textContent=`${count} sample player${count===1?'':'s'}`;
 }
 function phone(){const p=players[selected],gap=Math.abs(p.projection-p.line).toFixed(1);
  const ring=2*Math.PI*39;
  $('#hero-grade-body').innerHTML=`<div class="summary-person"><span class="player-avatar">${p.name[0]}</span><div><h3>${p.name}</h3><p>${p.team} · ${p.role}</p></div></div><div class="summary-tags"><span>${p.stat} · ${p.period}</span><span>${p.book}</span></div><div class="summary-decision"><div class="chance-ring"><svg viewBox="0 0 92 92" aria-hidden="true"><circle cx="46" cy="46" r="39"/><circle class="chance-fill" cx="46" cy="46" r="39" style="stroke-dasharray:${ring};stroke-dashoffset:${ring*(1-p.chance/100)}"/></svg><span><b>${p.chance}%</b><small>model estimate</small></span></div><div class="summary-side"><b>${p.side} ${p.line}</b><span>Grader’s side at<br>this exact line</span></div><span class="demo-grade">${p.grade}</span></div><div class="projection-scale"><div><span>Projection <b>${p.projection.toFixed(1)}</b></span><span>Line <b>${p.line}</b></span></div><div class="projection-track" aria-hidden="true"><i class="scale-line" style="left:${p.line/(Math.max(p.line,p.projection)*1.35)*100}%"></i><i class="scale-projection" style="left:${p.projection/(Math.max(p.line,p.projection)*1.35)*100}%"></i></div></div>`;

  const sections=[`<h4>Start with the whole picture.</h4><p>${p.stat} · ${p.period}. Compare the saved model estimate with the player’s recent record at this line.</p><div class="phone-history">${p.record.map((v,i)=>`<span><b>${v}/${[5,10,20][i]}</b><small>L${[5,10,20][i]} ${p.side}</small></span>`).join('')}</div><div class="phone-context"><small>PLAYER ROLE</small><p>${p.role} · ${p.team}</p></div>`,
  `<h4>Why Grader leans ${p.side} ${p.line}</h4><p class="positive-insight">Grader projects <b>${p.projection.toFixed(1)} ${p.stat.toLowerCase()}</b>, ${gap} ${p.projection>p.line?'above':'below'} the ${p.line} line.</p><div class="phone-context"><small>RECENT HISTORY · SEPARATE FROM THE MODEL</small><p>${p.record[1]} of the last 10 results hit ${p.side} at this line. That history is context—not the ${p.chance}% forecast.</p></div>`,
  `<h4>Behind the grade</h4><table class="phone-breakdown"><tbody><tr><td>Projection</td><td>${p.projection.toFixed(1)}</td></tr><tr><td>App line</td><td>${p.line}</td></tr><tr><td>Side</td><td>${p.side}</td></tr><tr><td>Model estimate</td><td>${p.chance}%</td></tr><tr><td>Letter</td><td>${p.grade}</td></tr><tr><td>Period</td><td>${p.period}</td></tr></tbody></table>`];
  $('#demo-phone').innerHTML=`<div class="demo-app phone-app"><div class="phone-brand"><strong>Prop Grader</strong><span>DEMO</span></div><div class="player-identity"><div class="player-avatar">${p.name[0]}</div><div><h3>${p.name}</h3><p>${p.role} · ${p.team}</p></div></div><div class="phone-match">${p.team} vs ${p.opponent}<br>${p.stat} · ${p.period}</div><div class="phone-projection"><div><small>GRADER SIDE</small><strong>${p.side} ${p.line}</strong><small>${p.chance}% model estimate</small></div><span class="demo-grade">${p.grade}</span></div><div class="phone-metrics"><div><span>Projection</span><b>${p.projection.toFixed(1)}</b></div><div><span>Gap to line</span><b class="projection-delta">${p.projection>=p.line?'+':'−'}${gap}</b></div></div><div class="phone-tabs" role="group" aria-label="Demo player card">${['Overview','Insights','Grade'].map((n,i)=>`<button data-phone-tab="${i}" aria-pressed="${phoneTab===i}">${n}</button>`).join('')}</div><div class="phone-content">${sections[phoneTab]}</div><p class="phone-demo-note">Saved example · not a current recommendation</p></div>`;
  $('[data-capture="0"]').setAttribute('aria-pressed',String(phoneTab===0));$('[data-capture="1"]').setAttribute('aria-pressed',String(phoneTab===1));$('[data-capture="2"]').setAttribute('aria-pressed',String(phoneTab===2));
  $('.callout-index').textContent=heroNotes[phoneTab][0];$('#capture-title').textContent=heroNotes[phoneTab][1];$('#capture-copy').textContent=heroNotes[phoneTab][2];
 }
 function heroPlayback(){const mobile=$('#mobile-product-play');if(mobile){mobile.textContent=heroPaused?'Play':'Pause';mobile.setAttribute('aria-label',`${heroPaused?'Play':'Pause'} mobile product animation`);}clearInterval(heroTimer);heroTimer=null;$('#pause-product').textContent=heroPaused?'Play':'Pause';$('#pause-product').setAttribute('aria-label',`${heroPaused?'Play':'Pause'} product animation`);if(!heroPaused&&heroVisible&&!document.hidden)heroTimer=setInterval(()=>{phoneTab=(phoneTab+1)%3;phone();},7000);}
 $('#demo-phone').addEventListener('click',e=>{const b=e.target.closest('[data-phone-tab]');if(!b)return;phoneTab=Number(b.dataset.phoneTab);heroPaused=true;phone();$(`[data-phone-tab="${phoneTab}"]`).focus({preventScroll:true});heroPlayback();});
 document.querySelectorAll('[data-capture]').forEach(b=>b.addEventListener('click',()=>{phoneTab=Number(b.dataset.capture);heroPaused=true;phone();heroPlayback();}));
 $('#pause-product').addEventListener('click',()=>{heroPaused=!heroPaused;heroPlayback();});
 $('#replay-product').addEventListener('click',()=>{phoneTab=0;heroPaused=false;phone();heroPlayback();$('.device-stage').scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'start'});});
 new IntersectionObserver(es=>{heroVisible=es[0].isIntersecting;heroPlayback();}).observe($('.device-stage'));
 document.addEventListener('visibilitychange',heroPlayback);reduced.addEventListener('change',()=>{if(reduced.matches){heroPaused=true;heroPlayback();}});
 board();phone();heroPlayback();

 let historyWindow=10,book='pp',currentFeature=0,currentStep=0;
 const studio={legs:2,count:2,noRepeat:true,side:'Either',results:null};
 function research(){const wins={5:3,10:5,20:11}[historyWindow];return `<div class="workspace-head"><h4>Vexite · Map 1 kills</h4><span class="demo-label">SAVED EXAMPLE</span></div><p class="workspace-sub">History against Over 14.5 · CS2 · FlyQuest</p><div class="demo-segment" role="group" aria-label="Demo history window">${[5,10,20].map(n=>`<button data-window="${n}" aria-pressed="${historyWindow===n}">Last ${n}</button>`).join('')}</div><div class="research-summary"><div class="demo-panel"><span class="metric-label">Over 14.5 · last ${historyWindow}</span><strong class="big-metric">${wins}<small> / ${historyWindow}</small></strong><div class="demo-outcomes" aria-label="${wins} of ${historyWindow} results over the line">${Array.from({length:historyWindow},(_,i)=>`<i aria-hidden="true" class="${i<wins?'hit':''}" style="animation-delay:${i*25}ms"></i>`).join('')}</div><p>Aggregate count, not game order.</p></div><div class="demo-panel"><span class="metric-label">Grader’s saved estimate</span><strong class="big-metric">65.2<small>%</small></strong><span class="demo-grade">B+</span><p>Over 14.5 · projection 16.8</p></div></div><div class="demo-stats"><div><small>L10 average</small><strong>17.5</strong></div><div><small>L10 median</small><strong>14.5</strong></div><div><small>L10 range</small><strong>9–35</strong></div></div><p class="demo-explain">The recent record and the model estimate answer different questions. A past hit rate is not a prediction.</p>`;}
 function comparison(){const pp=book==='pp';return `<div class="workspace-head"><h4>One player. Two lines.</h4><span class="demo-label">TRY BOTH APPS</span></div><p class="workspace-sub">Vexite · kills · Map 1 · FlyQuest vs Eternal Fire</p><div class="demo-compare-options"><button class="book-choice" data-book="pp" aria-pressed="${pp}"><span>PrizePicks</span><strong>14.5</strong><small>Standard line · saved example</small></button><button class="book-choice" data-book="ud" aria-pressed="${!pp}"><span>Underdog</span><strong>15.5</strong><small>1.90 decimal · saved example</small></button></div><div class="comparison-forecast demo-swap"><div><p>Grader’s side at this line</p><strong>Over ${pp?'14.5':'15.5'}</strong></div><div><p>Model estimate</p><strong>${pp?'65.2':'58.7'}%</strong></div><span class="demo-grade">${pp?'B+':'B'}</span></div><div class="comparison-facts"><div><span>Projection</span><b>16.8 kills</b></div><div><span>Gap to selected line</span><b>+${pp?'2.3':'1.3'} kills</b></div><div><span>Entry terms shown</span><b>${pp?'Power 2-pick':'Standard 2-pick'}</b></div><div><span>Saved break-even / leg</span><b>${pp?'57.7':'53.5'}%</b></div></div><p class="demo-explain">The projection stays the same. The line changes the probability and letter. Check current payouts and availability in your app.</p>`;}
 function slip(){return `<div class="workspace-head"><h4>Build a demo batch</h4><span class="demo-label">TRY THE CONTROLS</span></div><p class="workspace-sub">Six saved CS2 props · demonstration only</p><div class="studio-grid"><div class="studio-control"><label for="demo-legs">Picks per slip</label><select id="demo-legs">${[2,3,4,5,6].map(n=>`<option value="${n}" ${studio.legs===n?'selected':''}>${n} picks</option>`).join('')}</select></div><div class="studio-control"><label for="demo-batch">Number of slips</label><select id="demo-batch">${[1,2,4,8].map(n=>`<option value="${n}" ${studio.count===n?'selected':''}>${n} ${n===1?'slip':'slips'}</option>`).join('')}</select></div><div class="studio-control"><label for="demo-side">Pick side</label><select id="demo-side">${['Either','Over','Under'].map(n=>`<option ${studio.side===n?'selected':''} value="${n}">${n==='Either'?'Either side':n+'s only'}</option>`).join('')}</select></div><div><label class="studio-check"><input id="demo-no-repeat" type="checkbox" ${studio.noRepeat?'checked':''}> No repeat props</label></div></div><div class="studio-build"><p><b id="demo-batch-label">${studio.count} slips × ${studio.legs} picks</b>Uses saved example lines</p><button id="demo-generate">Build demo slips</button></div><div class="studio-result" id="demo-slip-results" aria-live="polite">${studio.results||''}</div><p class="legal-demo">This demonstrates selection controls, not the live ranking engine. No live recheck, payout calculation, bet placement or account tracking occurs.</p>`;}
 function renderFeature(n,step,tour=false){currentFeature=n;currentStep=step;if(tour&&n===1)book=step===2?'ud':'pp';const content=n===0?research():n===1?comparison():slip();$('#demo-feature').innerHTML=`<div class="demo-workspace" data-feature-index="${n}" data-tour-step="${step}">${content}</div>`;}
 const demoFeature=$('#demo-feature');
 if(demoFeature){
 $('#demo-feature').addEventListener('click',e=>{const w=e.target.closest('[data-window]'),b=e.target.closest('[data-book]');if(w){historyWindow=Number(w.dataset.window);renderFeature(0,currentStep);$(`[data-window="${historyWindow}"]`).focus({preventScroll:true});}if(b){book=b.dataset.book;renderFeature(1,currentStep);$(`[data-book="${book}"]`).focus({preventScroll:true});}if(e.target.closest('#demo-generate'))generate();});
 $('#demo-feature').addEventListener('change',e=>{const id=e.target.id;if(id==='demo-legs')studio.legs=Number(e.target.value);if(id==='demo-batch')studio.count=Number(e.target.value);if(id==='demo-side')studio.side=e.target.value;if(id==='demo-no-repeat')studio.noRepeat=e.target.checked;studio.results=null;$('#demo-slip-results').textContent='Settings changed. Build again to update your demo slips.';$('#demo-batch-label').textContent=`${studio.count} slips × ${studio.legs} picks`;});
 }
 function generate(){const pool=players.filter(p=>studio.side==='Either'||p.side===studio.side);const combinations=[];
  function visit(start,chosen){if(combinations.length>=100)return;if(chosen.length===studio.legs){combinations.push(chosen);return;}for(let i=start;i<pool.length;i++)visit(i+1,[...chosen,pool[i]]);}
  visit(0,[]);const result=[],used=new Set();for(const combo of combinations){if(studio.noRepeat&&combo.some(p=>used.has(p.name)))continue;result.push(combo);combo.forEach(p=>used.add(p.name));if(result.length===studio.count)break;}
  const message=result.length===studio.count?`${result.length} demo slips built.`:`${result.length} of ${studio.count} demo slips built. The six-prop sample cannot fill more with these settings. Try fewer picks, either side or allow reuse.`;
  studio.results=`<p>${message}</p>${result.map((ps,i)=>`<article class="sample-slip"><strong>Demo slip ${i+1} · ${ps.length} picks</strong><ul>${ps.map(p=>`<li><b>${p.name}</b><span>${p.side} ${p.line} ${p.stat} · ${p.period}</span></li>`).join('')}</ul></article>`).join('')}`;$('#demo-slip-results').innerHTML=studio.results;
 }
 ['hero-prev','hero-next'].forEach(id=>$('#'+id).addEventListener('click',()=>{selected=(selected+(id==='hero-next'?1:players.length-1))%players.length;heroPaused=true;phoneTab=1;rows();phone();heroPlayback();}));
 $('#mobile-product-play').addEventListener('click',()=>{heroPaused=!heroPaused;heroPlayback();});
 window.ScoutDemo={renderFeature};
})();

(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const windowEl = $('.review-window'), track = $('.review-track');
  const originals = [...track.children];
  originals.forEach(el => { const copy = el.cloneNode(true); copy.setAttribute('aria-hidden','true'); copy.inert = true; track.append(copy); });
  let reviewPaused = reduced.matches, reviewVisible = false, hover = false, focused = false, last = 0, fractionalTravel = 0;
  const reviewButton = $('#review-play');
  function reviewState() {
    reviewButton.textContent = reviewPaused ? 'Play' : 'Pause';
    reviewButton.setAttribute('aria-label', `${reviewPaused ? 'Play' : 'Pause'} review carousel`);
  }
  function loopWidth() { return originals.length ? track.children[originals.length].offsetLeft - track.children[0].offsetLeft : 0; }
  function animateReviews(t) {
    const elapsed = last ? Math.min(t-last, 60) : 0; last = t;
    if (originals.length && !reviewPaused && reviewVisible && !hover && !focused && !document.hidden) {
      fractionalTravel += elapsed * .026; const pixels = Math.floor(fractionalTravel); fractionalTravel -= pixels; windowEl.scrollLeft += pixels;
      const width = loopWidth(); if (width && windowEl.scrollLeft >= width) windowEl.scrollLeft -= width;
    }
    requestAnimationFrame(animateReviews);
  }
  reviewButton.addEventListener('click', () => { reviewPaused = !reviewPaused; reviewState(); });
  function moveReview(direction) {
    if (!originals.length) return;
    reviewPaused = true; reviewState();
    const width = loopWidth(), step = track.children[1].offsetLeft - track.children[0].offsetLeft;
    if (direction < 0 && windowEl.scrollLeft < step/2) windowEl.scrollLeft += width;
    if (direction > 0 && windowEl.scrollLeft >= width) windowEl.scrollLeft -= width;
    windowEl.scrollBy({left:direction*step, behavior:reduced.matches?'instant':'smooth'});
  }
  $('#review-prev').addEventListener('click',()=>moveReview(-1));
  $('#review-next').addEventListener('click',()=>moveReview(1));
  if(!originals.length) [reviewButton,$('#review-prev'),$('#review-next')].forEach(b=>b.disabled=true);
  windowEl.addEventListener('mouseenter',()=>hover=true);
  windowEl.addEventListener('mouseleave',()=>hover=false);
  windowEl.addEventListener('focusin',()=>focused=true);
  windowEl.addEventListener('focusout',()=>focused=false);
  windowEl.addEventListener('pointerdown',()=>{reviewPaused=true;reviewState();});
  windowEl.addEventListener('keydown',e=>{if(['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();moveReview(e.key==='ArrowRight'?1:-1);}});
  new IntersectionObserver(es=>reviewVisible=es[0].isIntersecting).observe(windowEl);
  reviewState(); requestAnimationFrame(animateReviews);

  if (!$('#feature-panel')) { reduced.addEventListener('change',()=>{if(reduced.matches){reviewPaused=true;reviewState();}}); return; }
  const features = [
    {label:'PLAYER RESEARCH', title:'The story behind the stat.', description:'A recent hit rate is only the start. Open the player’s results, role, matchup and model estimate together—at the exact stat and period you’re researching.', image:'actual-history.jpg', alt:'Actual Vexite research screen showing Over 14.5, a 16.8 projection and last ten results.', steps:[
      {name:'Recent results',detail:'Compare the last 5, 10 and 20 results against this line. Pushes stay separate, and each window shows its sample size.',box:[50.3,42,46.5,10]},
      {name:'History in context',detail:'Explore the recent record behind the average. Vexite went Over 14.5 in 5 of 10 saved games. This demo shows aggregate counts, not an ordered game log.',box:[50.3,55,46.5,34]},
      {name:'The model’s view',detail:'Put the history beside the projection, side and letter. Grader’s 16.8 projection is a different measure from the player’s recent hit rate.',box:[2,28,46.5,29]}
    ]},
    {label:'LINE COMPARISON', title:'Same player. A different decision.',description:'A one-point difference can change the pick. Compare the available lines for the same player, event, stat and period, with prices and checked times alongside them.',image:'actual-compare.jpg',alt:'Actual Lines tab comparing Vexite Map 1 kills: PrizePicks 14.5 and Underdog 15.5.',steps:[
      {name:'Compare the apps',detail:'This saved example shows PrizePicks at 14.5 and Underdog at 15.5. The lower line is easier for the Over; the higher line is easier for the Under.',box:[50.3,14,46.5,16]},
      {name:'Check the terms',detail:'Lines are only part of the comparison. Check the price, entry type and break-even requirement. Payouts and settlement rules can differ by app.',box:[50.3,38,46.5,17]},
      {name:'Read each forecast',detail:'A different line gets its own forecast. In this example, Over 14.5 is B+ at 65.2%; Over 15.5 is B at 58.7%. Neither is a guaranteed result.',box:[50.3,66,46.5,20]}
    ]},
    {label:'SLIP STUDIO',title:'Your rules. Your batch.',description:'Choose the eligible sports, platform, entry type and 2–6 picks per slip. Build several lineups, control repeated props and review the results before you play.',image:'actual-slip.jpg',alt:'Actual Slip Studio setup with sport, platform, entry type, batch size and picks per slip controls.',steps:[
      {name:'Choose your entry',detail:'Pick PrizePicks or Underdog, choose the available entry type, then set your batch size and picks per slip. Flex and Power use different payout rules.',box:[2,36,62,49]},
      {name:'Set your rules',detail:'Choose the sport mix, one per match and no repeat props. The extra filters include Overs only or Unders only—using Grader’s side, not flipping picks to fill a batch.',image:'actual-slip-rules.jpg',alt:'Actual Slip Studio selection rules including no repeat props and Overs only or Unders only.',box:[2,7,62,61]},
      {name:'Review before playing',detail:'Try building a demo batch below. The live product rechecks current lines; this demo uses a limited saved pool and never places or tracks a bet.',box:[65.8,36,31,36]}
    ]}
  ];
  const tabs = [...document.querySelectorAll('[data-feature]')];
  let feature = 0, step = 0, featurePaused = reduced.matches, featureVisible = false;
  let featureTimer;
  function paintStep() {
    const f=features[feature], s=f.steps[step];
    $('#feature-detail').textContent=s.detail;
    window.ScoutDemo.renderFeature(feature,step,true);
    [...$('.feature-steps').children].forEach((b,i)=>b.setAttribute('aria-pressed',String(i===step)));
  }
  function playback() {
    clearInterval(featureTimer);
    $('#feature-play').textContent=featurePaused?'Play tour':'Pause tour';
    $('#feature-play').setAttribute('aria-label',`${featurePaused?'Play':'Pause'} feature walkthrough`);
    if(!featurePaused&&featureVisible&&!document.hidden) featureTimer=setInterval(()=>{step=(step+1)%3;paintStep();},7000);
  }
  function selectFeature(index) {
    feature=index; step=0; const f=features[index];
    tabs.forEach((b,i)=>{b.setAttribute('aria-selected',String(i===index));b.tabIndex=i===index?0:-1;});
    $('#feature-panel').setAttribute('aria-labelledby','feature-tab-'+index);
    $('#feature-label').textContent=f.label; $('#feature-title').textContent=f.title; $('#feature-description').textContent=f.description;
    $('.feature-steps').replaceChildren(...f.steps.map((s,i)=>{const b=document.createElement('button');b.type='button';b.textContent=s.name;b.setAttribute('aria-pressed',String(i===0));b.addEventListener('click',()=>{step=i;featurePaused=true;paintStep();playback();});return b;}));
    paintStep(); playback();
  }
  tabs.forEach((b,i)=>{
    b.addEventListener('click',()=>{featurePaused=reduced.matches;selectFeature(i);});
    b.addEventListener('keydown',e=>{let n;if(e.key==='ArrowRight')n=(i+1)%3;else if(e.key==='ArrowLeft')n=(i+2)%3;else if(e.key==='Home')n=0;else if(e.key==='End')n=2;else return;e.preventDefault();tabs[n].focus();selectFeature(n);});
  });
  $('#feature-play').addEventListener('click',()=>{featurePaused=!featurePaused;playback();});
  new IntersectionObserver(es=>{featureVisible=es[0].isIntersecting;playback();},{threshold:.15}).observe($('#feature-panel'));
  document.addEventListener('visibilitychange',playback);
  reduced.addEventListener('change',()=>{if(reduced.matches){reviewPaused=true;featurePaused=true;reviewState();playback();}});
  $('#demo-feature').addEventListener('pointerdown',()=>{featurePaused=true;playback();});
  $('#demo-feature').addEventListener('focusin',()=>{featurePaused=true;playback();});
  selectFeature(0);
})();
