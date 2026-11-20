/* 1time Scout v3 shell (SHARED). First file of every v3 script bundle (web/UI_V3.md); page-group files follow it in the same bundle.
   Runs only on a page the server marked <html data-ui-v3> (SCOUT_UI_V3=1, or the owner's /preview/v3 cookie). Display only: it reads
   no API, changes no grade, and every control it adds presses the page's own existing control (the research hub's buttons), so
   behaviour stays the page's. Without a hub (classic view, public pages) the nav is plain links to the hub's own routes.

   window.ScoutV3 (for page groups):
     ICON{name}                     the one icon set (20px grid, 1.5 stroke, currentColor; from the approved system/shell.py)
     esc(s)                         HTML-escape
     state(el, kind, opts)          kind 'loading' | 'empty' | 'error' | 'boot' -> the approved states into el (opts below)
     art(name) -> Promise<svg text> one original scene from /v3/art/<name>.svg (fixed list ScoutV3.ART), cached; rejects on an unknown name
     mountArt(el, name)             inline that scene into el (CSS needs it inline: .sc-scene__art>svg / .sc-lit__art>svg)
     mapArt(label) -> name | ''     'Dust2' / 'de_dust2' / 'Dust 2' -> 'dust2'; '' when there is no scene for it (never a guess)
     HUE{name}                      per-map hue for .sc-scene's --h
     toast(text, ms)                one dark toast above the bottom bar
     theme() / setTheme('light'|'dark'|null)
     setSport(key)                  html[data-sc-sport] (cs2 | lol | nba | nfl | soccer | tennis): the one accent per surface
     onHub(fn)                      fn(hubElement) once the research hub is in the page (now, or when upgrade-hub.js adds it) */
(function () {
  'use strict';
  var root = document.documentElement;
  if (!root.dataset.uiV3 || window.ScoutV3) return;
  var PAGE = root.dataset.v3Page || '';
  var MEMBER = {cs2: 1, lol: 1, sports: 1, 'sports-results': 1};

  var ICON = {
    search: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="5.6"/><path d="M13.2 13.2L17 17"/></svg>',
    sun: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.4"/><path d="M10 2v1.8M10 16.2V18M2 10h1.8M16.2 10H18M4.3 4.3l1.3 1.3M14.4 14.4l1.3 1.3M4.3 15.7l1.3-1.3M14.4 5.6l1.3-1.3"/></svg>',
    moon: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 12.6A6.3 6.3 0 017.4 4.5a6.3 6.3 0 108.1 8.1z"/></svg>',
    board: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h14"/></svg>',
    slip: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 2.8h10v14.4l-1.7-1.1-1.6 1.1-1.7-1.1-1.7 1.1-1.6-1.1L5 17.2z"/><path d="M7.6 7h4.8M7.6 10h4.8M7.6 13h2.6"/></svg>',
    picks: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10.5l3.5 3.5L16 5.5"/></svg>',
    record: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 16V9M10 16V4M16 16v-5"/></svg>',
    more: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/></svg>',
    plus: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11"/></svg>',
    check: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>',
    open: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 4H4.5v11.5H16V12"/><path d="M11 3.5h5.5V9M16.5 3.5L9 11"/></svg>',
    chev: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5"/></svg>',
    ev: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 15.5l4.5-5 3 3L17 6"/><path d="M12.5 6H17v4.5"/></svg>',
    arb: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 7h12l-3-3M16.5 13h-12l3 3"/></svg>',
    books: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2"/><path d="M3 8h14M7 12h2.5"/></svg>',
    moneyline: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16.5h14"/><path d="M5.5 13.5V9M10 13.5V5.5M14.5 13.5v-6"/></svg>',
    grader: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3.5" y="3.5" width="13" height="13" rx="2.5"/><path d="M7 12.5l2-5.5 2 5.5M7.7 10.8h2.6M13 8v4M11 10h4"/></svg>',
    classic: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="3.5" width="14" height="13" rx="2"/><path d="M3 8h14M8 8v8.5"/></svg>',
    alerts: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 13.5V9a4.5 4.5 0 019 0v4.5l1.5 1.5H4z"/><path d="M8.5 17h3"/></svg>',
    account: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7.5" r="3.2"/><path d="M4 16.5c1.2-2.9 3.4-4.2 6-4.2s4.8 1.3 6 4.2"/></svg>',
    help: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="M8 8a2 2 0 113 1.7c-.7.4-1 .9-1 1.6M10 13.8v.1"/></svg>',
    tools: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M4 10h12M4 14h12"/><circle cx="7" cy="6" r="1.4"/><circle cx="13" cy="10" r="1.4"/><circle cx="9" cy="14" r="1.4"/></svg>',
    ext: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5h7v7M15 5l-9 9"/></svg>',
    games: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="5" rx="1.5"/><rect x="3" y="11" width="14" height="5" rx="1.5"/></svg>',
    alert: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="M10 6.5v4.2M10 13.4v.1"/></svg>',
    empty: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3.5" y="4" width="13" height="12" rx="2"/><path d="M3.5 11h4l1 1.5h3l1-1.5h4"/></svg>'
  };

  /* Desktop nav (the hub's own NAV order and labels; EV / Arbitrage / Sportsbooks only when the hub has them, i.e. their flag is on).
     key -> [label, hub section id, route]. Routes are the hub's own V2 hashes (upgrade-hub.js route()). */
  var NAV = [['board', 'Board', 'board', '#/board'], ['slips', 'Slips', 'studio', '#/slips'], ['ev', 'EV', 'ev', '#/ev'], ['arb', 'Arbitrage', 'arb', '#/arb'],
    ['books', 'Sportsbooks', 'books', '#/books'], ['picks', 'My picks', 'picks', '#/picks'], ['record', 'Record', 'record', '#/record'], ['tools', 'Tools', 'tools', '#/tools']];
  var OPTIONAL = {ev: 1, arb: 1, books: 1};
  var TABS = [['board', 'Board', 'board'], ['slips', 'Slip', 'slip'], ['picks', 'My picks', 'picks'], ['record', 'Record', 'record'], ['more', 'More', 'more']];
  /* the More sheet without a hub: the same destinations as the hub's own More sheet (#hub-more), as links */
  var MORE_LINKS = [['Prices', [['moneyline', 'Moneylines', '/?view=classic#moneylines', 1]]],
    ['Tools', [['games', 'By game', '#/games', 0], ['tools', 'Tools', '#/tools', 0], ['alerts', 'Alerts', '#/alerts', 0], ['grader', 'Single prop grader', '/?view=classic#one', 1],
      ['board', 'Match research', '/?view=classic#matches', 1], ['classic', 'Classic board', '/?view=classic', 1]]]];
  var ART = ['dust2', 'mirage', 'inferno', 'nuke', 'ancient', 'anubis', 'train', 'overpass', 'cache', 'nba', 'nba-home', 'nfl', 'soccer',
    'tennis-hard', 'tennis-clay', 'tennis-grass', 'tennis-indoor', 'rift-blue', 'rift-red'];
  var HUE = {dust2: 30, mirage: 18, inferno: 10, nuke: 212, ancient: 150, anubis: 182, train: 214, overpass: 176, cache: 80};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; });
  }
  function base() { return PAGE === 'lol' ? '/lol' : (PAGE === 'sports' || PAGE === 'sports-results') ? '/sports' : '/'; }
  function hub() { var h = document.getElementById('u-hub'); return h && !h.hidden ? h : null; }
  function hubBtn(id) { var h = document.getElementById('u-hub'); return h ? h.querySelector('.h-sidebar [data-hub="section"][data-id="' + id + '"]') : null; }

  /* ---------------------------------------------------------------- theme (same key as the hub's own toggle) */
  function theme() { var t = root.dataset.theme; return t === 'light' || t === 'dark' ? t : (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); }
  function setTheme(t) {
    if (t === 'light' || t === 'dark') root.dataset.theme = t; else delete root.dataset.theme;
    try { if (t) localStorage.setItem('scout-theme', t); else localStorage.removeItem('scout-theme'); } catch (e) { /* storage blocked: this page only */ }
    var b = document.querySelector('.sc-v3-bar [data-v3="theme"]');
    if (b) { b.innerHTML = theme() === 'light' ? ICON.moon : ICON.sun; b.setAttribute('aria-label', theme() === 'light' ? 'Switch to dark theme' : 'Switch to light theme'); }
  }
  function setSport(k) { if (/^(cs2|lol|nba|nfl|soccer|tennis)$/.test(k || '')) root.dataset.scSport = k; }

  /* ---------------------------------------------------------------- states */
  function state(el, kind, o) {
    if (!el) return;
    o = o || {};
    var h = '';
    if (kind === 'loading') {
      var n = Math.max(1, Math.min(12, o.rows || 4)), rows = '';
      for (var i = 0; i < n; i++) rows += '<div class="sc-skrow"><span class="sc-sk c"></span><span><span class="sc-sk" style="width:55%"></span><span class="sc-sk w2" style="margin-top:8px;height:10px"></span></span><span class="sc-sk"></span><span class="sc-sk"></span></div>';
      h = '<div class="sc-skel" role="status" aria-busy="true"><span class="sc-sr">' + esc(o.label || 'Loading') + '</span>' + rows + '</div>';
    } else if (kind === 'empty') {
      h = '<div class="sc-surface sc-empty" role="status"><div class="sc-empty__ic">' + (ICON[o.icon] || ICON.empty) + '</div><b>' + esc(o.title || '') + '</b>' +
        (o.text ? '<p>' + esc(o.text) + '</p>' : '') + (o.action ? '<button type="button" class="sc-btn" data-v3-act>' + esc(o.action) + '</button>' : '') + '</div>';
    } else if (kind === 'error') {
      h = '<div class="sc-errbox" role="alert">' + ICON.alert + '<p>' + esc(o.text || 'This did not load.') + '</p>' +
        (o.retry ? '<button type="button" class="sc-btn" data-v3-act>' + esc(o.action || 'Try again') + '</button>' : '') + '</div>';
    } else if (kind === 'boot') {
      h = '<div class="sc-boot" role="status"><div><span class="sc-brand__mark" aria-hidden="true">1T</span><p>' + esc(o.text || 'Loading') + '</p><i></i></div></div>';
    }
    el.innerHTML = h;
    var act = el.querySelector('[data-v3-act]'), fn = o.retry || o.onAction;
    if (act && typeof fn === 'function') act.addEventListener('click', fn);
  }

  /* ---------------------------------------------------------------- art (inline SVG from a fixed list; one fetch per scene) */
  var artCache = {};
  function art(name) {
    if (ART.indexOf(name) < 0) return Promise.reject(new Error('no scene ' + name));
    if (!artCache[name]) artCache[name] = fetch('/v3/art/' + name + '.svg', {credentials: 'same-origin'}).then(function (r) {
      if (!r.ok) throw new Error('art ' + r.status);
      return r.text();
    }).catch(function (e) { delete artCache[name]; throw e; });
    return artCache[name];
  }
  function mountArt(el, name) {
    return art(name).then(function (svg) { if (el && /^<svg[\s>]/.test(svg)) el.innerHTML = svg; return el; });
  }
  function mapArt(label) {
    var k = String(label || '').toLowerCase().replace(/^de_/, '').replace(/[^a-z0-9]/g, '');
    return HUE.hasOwnProperty(k) ? k : '';
  }
  var toastTimer = null;
  function toast(text, ms) {
    var t = document.querySelector('.sc-toast.sc-v3-toast');
    if (!t) { t = document.createElement('div'); t.className = 'sc-toast sc-v3-toast'; t.setAttribute('role', 'status'); t.innerHTML = '<span></span>'; document.body.appendChild(t); if (window.ScoutV3 && ScoutV3._keepLive) ScoutV3._keepLive(t); }
    t.firstChild.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, ms || 3200);
  }

  var hubFns = [];
  function onHub(fn) { var h = document.getElementById('u-hub'); if (h) { try { fn(h); } catch (e) { /* page group bug: keep the shell */ } } else hubFns.push(fn); }

  window.ScoutV3 = {ICON: ICON, NAV: NAV, ART: ART, HUE: HUE, esc: esc, state: state, art: art, mountArt: mountArt, mapArt: mapArt, toast: toast,
    theme: theme, setTheme: setTheme, setSport: setSport, onHub: onHub, page: PAGE};

  /* ---------------------------------------------------------------- the one header + bottom bar (member pages only) */
  if (!MEMBER[PAGE]) return;
  var B = base();
  document.body.classList.add('sc-v3', 'sc-v3-shell');
  var nav = NAV.map(function (n) {
    return '<a class="sc-nav__a" href="' + B + n[3] + '" data-v3-nav="' + n[0] + '"' + (OPTIONAL[n[0]] ? ' hidden' : '') + '>' + n[1] +
      (n[0] === 'tools' ? '<span class="sc-nav__n" data-v3-count="alerts"></span>' : '') + '</a>';
  }).join('');
  var bar = document.createElement('div');                  /* not <header>/<nav>: the pages style those tags globally */
  bar.className = 'sc-appbar sc-v3-bar sc-app';
  bar.setAttribute('role', 'banner');
  bar.innerHTML = '<a class="sc-brand" href="' + B + '#/board" data-v3-nav="board" aria-label="1time Scout home"><span class="sc-brand__mark" aria-hidden="true">1T</span>Scout</a>' +
    '<div class="sc-nav" role="navigation" aria-label="Research hub">' + nav + '</div>' +
    '<div class="sc-appbar__end"><button class="sc-iconbtn" type="button" data-v3="search" aria-label="Search players, teams and games">' + ICON.search + '</button>' +
    '<a class="sc-slip" href="' + B + '#/slips" data-v3-nav="slips">' + ICON.slip + 'Slip <b class="sc-num" data-v3-count="slip">0</b></a>' +
    '<button class="sc-iconbtn" type="button" data-v3="theme" aria-label="Switch theme">' + ICON.sun + '</button></div>';
  var tabs = document.createElement('div');
  tabs.className = 'sc-tabbar sc-v3-tabs sc-app';
  tabs.setAttribute('role', 'navigation');
  tabs.setAttribute('aria-label', 'Sections');
  tabs.innerHTML = TABS.map(function (t) {
    var href = t[0] === 'more' ? '#' : B + NAV.filter(function (n) { return n[0] === t[0]; })[0][3];
    return '<a href="' + href + '" ' + (t[0] === 'more' ? 'data-v3="more" role="button" aria-expanded="false"' : 'data-v3-nav="' + t[0] + '"') + '>' + ICON[t[2]] + '<span>' + t[1] +
      (t[0] === 'slips' ? ' <b class="sc-num" data-v3-count="slip">0</b>' : '') + '</span>' + (t[0] === 'more' ? '<span class="sc-nav__n" data-v3-count="alerts-m"></span>' : '') + '</a>';
  }).join('');
  document.body.insertBefore(bar, document.body.firstChild);
  document.body.appendChild(tabs);
  /* The open hub makes every other child of <body> inert (upgrade-hub.js route()); the shell is the hub's own navigation, so it
     stays reachable. Only the inert flag on the shell's own nodes is undone; the classic page under the hub stays inert. */
  var live = new MutationObserver(function (list) { list.forEach(function (m) { if (m.target.inert) m.target.inert = false; }); });
  function keepLive(node) { live.observe(node, {attributes: true, attributeFilter: ['inert']}); if (node.inert) node.inert = false; }
  keepLive(bar); keepLive(tabs);
  window.ScoutV3._keepLive = keepLive;
  setTheme(root.dataset.theme || null);

  /* More: the hub's own sheet when the hub is open (every item it has, as it is); else the v3 sheet of the same links */
  var sheet = null, scrim = null;
  function closeMore() { if (sheet) { sheet.hidden = true; scrim.hidden = true; } tabs.querySelector('[data-v3="more"]').setAttribute('aria-expanded', 'false'); }
  function openMore(btn) {
    var t = document.getElementById('hub-more-toggle');
    if (hub() && t) { t.click(); btn.setAttribute('aria-expanded', t.getAttribute('aria-expanded') || 'false'); return; }
    if (!sheet) {
      scrim = document.createElement('div'); scrim.className = 'sc-scrim sc-v3-scrim'; scrim.hidden = true;
      sheet = document.createElement('div'); sheet.className = 'sc-sheet sc-more sc-v3-more sc-app'; sheet.hidden = true;
      sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-label', 'More');
      sheet.innerHTML = '<div class="sc-more__grab" aria-hidden="true"></div>' + MORE_LINKS.map(function (g) {
        return '<section class="sc-more__g" aria-label="' + g[0] + '"><h3 class="sc-more__h">' + g[0] + '</h3><div class="sc-more__list">' + g[1].map(function (i) {
          return '<a class="sc-more__a" href="' + (i[2].charAt(0) === '#' ? B + i[2] : i[2]) + '">' + ICON[i[0]] + '<span>' + i[1] + '</span>' + (i[3] ? ICON.ext : '') + '</a>';
        }).join('') + '</div></section>';
      }).join('');
      document.body.appendChild(scrim); document.body.appendChild(sheet); keepLive(scrim); keepLive(sheet);
      scrim.addEventListener('click', closeMore);
      sheet.addEventListener('click', function (e) { if (e.target.closest('a')) closeMore(); });
    }
    var open = sheet.hidden;
    sheet.hidden = !open; scrim.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { var a = sheet.querySelector('a'); if (a) a.focus(); }
  }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && sheet && !sheet.hidden) closeMore(); });

  function go(key, e) {
    var n = NAV.filter(function (x) { return x[0] === key; })[0];
    if (!n) return;
    var b = hub() && hubBtn(n[2]);
    if (b) { if (e) e.preventDefault(); b.click(); paintCurrent(); }
    /* no open hub: the link's own route (the hub's V2 hash) opens it, as any member link does */
  }
  function onClick(e) {
    var a = e.target.closest('[data-v3-nav],[data-v3]');
    if (!a) return;
    var act = a.getAttribute('data-v3');
    if (act === 'theme') { setTheme(theme() === 'light' ? 'dark' : 'light'); return; }
    if (act === 'more') { e.preventDefault(); openMore(a); return; }
    if (act === 'search') {
      var q = document.getElementById('hub-q');
      if (hub() && q) { go('board', null); setTimeout(function () { q.focus(); }, 0); } else location.assign(B + '#/board');
      return;
    }
    go(a.getAttribute('data-v3-nav'), e);
  }
  bar.addEventListener('click', onClick);
  tabs.addEventListener('click', onClick);

  /* current section + counts mirror the hub's own (its sidebar's aria-current, slip count, alert counts) */
  function currentKey() {
    var h = document.getElementById('u-hub');
    if (h && !h.hidden) {
      var cur = h.querySelector('.h-sidebar [data-hub="section"][aria-current="page"]');
      if (cur) { var id = cur.getAttribute('data-id'); for (var i = 0; i < NAV.length; i++) if (NAV[i][2] === id) return NAV[i][0]; }
    }
    var m = location.hash.match(/^#\/(\w+)/), k = m ? m[1] : 'board';
    return {slips: 'slips', results: 'record', record: 'record', picks: 'picks', tools: 'tools', alerts: 'tools', ev: 'ev', arb: 'arb', books: 'books'}[k] || 'board';
  }
  function paintCurrent() {
    var k = currentKey();
    bar.querySelectorAll('.sc-nav [data-v3-nav]').forEach(function (a) { if (a.getAttribute('data-v3-nav') === k) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    var inTabs = false;
    tabs.querySelectorAll('[data-v3-nav]').forEach(function (a) { var on = a.getAttribute('data-v3-nav') === k; inTabs = inTabs || on; if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    var more = tabs.querySelector('[data-v3="more"]');
    if (inTabs) more.removeAttribute('aria-current'); else more.setAttribute('aria-current', 'page');
  }
  function text(id) { var el = document.getElementById(id); return el ? (el.textContent || '').trim() : ''; }
  function paintCounts() {
    var n = text('hub-tab-slip-n') || text('hub-slip-count') || '0';
    document.querySelectorAll('[data-v3-count="slip"]').forEach(function (b) { b.textContent = n; });
    var pill = bar.querySelector('.sc-slip');
    pill.classList.toggle('has-items', n !== '0' && n !== '');
    var al = text('hub-alert-count'), alm = text('hub-alert-count-m') || al;
    bar.querySelectorAll('[data-v3-count="alerts"]').forEach(function (b) { b.textContent = al; });
    tabs.querySelectorAll('[data-v3-count="alerts-m"]').forEach(function (b) { b.textContent = alm; });
  }
  function paintNav() {
    var h = document.getElementById('u-hub');
    bar.querySelectorAll('.sc-nav [data-v3-nav]').forEach(function (a) {
      var k = a.getAttribute('data-v3-nav');
      if (OPTIONAL[k]) a.hidden = !(h && hubBtn(NAV.filter(function (n) { return n[0] === k; })[0][2]));
    });
  }
  var queued = false;
  function sync() {
    if (queued) return; queued = true;
    (window.requestAnimationFrame || setTimeout)(function () { queued = false; paintNav(); paintCurrent(); paintCounts(); });
  }
  var watching = null;
  function watchHub(h) {
    if (watching === h) return;
    watching = h;
    new MutationObserver(sync).observe(h, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-current', 'hidden', 'aria-expanded']});
    var fns = hubFns; hubFns = [];
    fns.forEach(function (fn) { try { fn(h); } catch (e) { /* page group bug: keep the shell */ } });
    sync();
  }
  if (document.getElementById('u-hub')) watchHub(document.getElementById('u-hub'));
  else {
    var mo = new MutationObserver(function () { var h = document.getElementById('u-hub'); if (h) { mo.disconnect(); watchHub(h); } });
    mo.observe(document.body, {childList: true});
  }
  window.addEventListener('hashchange', sync);
  window.addEventListener('popstate', sync);
  sync();
})();
