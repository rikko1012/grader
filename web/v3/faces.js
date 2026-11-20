/* v3 faces.js - player headshots and team logos on the v3 cards (owner 2026-11-20: "Add player and team pictures to cards also").
   Runs after the page groups in the member and landing bundles, only on a v3 page (ScoutV3 is defined). One IIFE: a syntax error
   here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file).

   Display only:
   - Pictures come from Scout's own origin (/img/..., img_cache.py), never from a third-party host. Which board player has a READY
     picture is read from /img/index/<sport>.json (exact ids from the board snapshot; a name maps only when one board player has it).
   - Each card keeps its monogram: the picture is laid over it and removed again if it cannot load; nothing else in the card moves.
     The hub's own nodes are never replaced; where a card has no avatar node yet (esports board rows, My picks rows) one is added
     once that sport's board has pictures (the monogram where a player has none, so every name stays aligned).
   - Where the hub draws an ESPN hotlink (NBA / NFL faces), it is swapped for the same-origin copy when that copy is ready.
   - The hub repaints by innerHTML: one MutationObserver re-applies, idempotently; an image already in the browser cache shows at
     once (no fade on a repaint). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var D = document, root = D.documentElement;
  var PAGE = root.getAttribute('data-v3-page') || '';
  var SPORT_OF = {CS2: 'cs2', NBA: 'nba', NFL: 'nfl', Soccer: 'soccer', Tennis: 'tennis', LoL: 'lol', League: 'lol'};
  var WITH = {cs2: 1, nba: 1, nfl: 1, soccer: 1, tennis: 1};
  var IDX = {}, LOADING = {}, SEEN = {}, LAST = {}, REFRESH_MS = 90000;
  var CARDS = [];                                          /* the hub's teed line docs: name -> exact player id + sport */

  /* ---------------------------------------------------------------- the ready-picture lists */
  function want(sport) {
    if (!WITH[sport] || PAGE === 'landing' || PAGE === 'record') return;
    SEEN[sport] = 1;
    if (LOADING[sport] || (LAST[sport] && Date.now() - LAST[sport] < REFRESH_MS)) return;
    LOADING[sport] = 1;
    fetch('/img/index/' + sport + '.json', {credentials: 'same-origin'}).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (d) {
      LOADING[sport] = 0; LAST[sport] = Date.now();
      if (d && d.p && d.n) { IDX[sport] = d; schedule(); }
    }, function () { LOADING[sport] = 0; LAST[sport] = Date.now(); });
  }
  function bySport(sport, name, pid) {
    var d = IDX[sport];
    if (!d) return null;
    var id = pid && d.p[pid] ? pid : (name ? d.n[name] : null);
    var f = id && d.p[id];
    return f ? {i: f[0] || '', t: f[1] || '', team: f[2] || ''} : null;
  }
  function face(sport, name, pid) {
    name = String(name || '').trim();
    if (sport) { want(sport); return bySport(sport, name, pid); }
    var hit = null, n = 0;                                 /* no sport on screen: only a name that one sport's board has */
    Object.keys(IDX).forEach(function (s) { var f = bySport(s, name, null); if (f) { hit = f; n++; } });
    return n === 1 ? hit : null;
  }
  /* a sport whose board has pictures gives every row an avatar (the monogram where there is no picture), so names stay aligned */
  function anyPics(sport) { var d = IDX[sport]; if (!d) return false; if (d.__any == null) d.__any = Object.keys(d.p).length > 0; return d.__any; }
  function teamLogo(sport, team) {
    var d = IDX[sport];
    if (sport) want(sport);
    return d && d.tn && team ? d.tn[String(team).trim()] || '' : '';
  }

  /* ---------------------------------------------------------------- the hub's line docs (teed, never re-fetched) */
  var nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function (input, init) {
      var p = nativeFetch.apply(this, arguments);
      try {
        var u = typeof input === 'string' ? input : (input && input.url) || '';
        if (/\/api\/upgrade\/line\?/.test(u)) {
          p.then(function (r) {
            if (r && r.ok) r.clone().json().then(function (d) {
              var rec = d && d.record;
              if (!rec || !rec.player) return;
              CARDS.unshift({name: String(rec.player.name || '').trim(), id: String(rec.player.id || ''), sport: rec.sport,
                             team: rec.player.team || '', opp: rec.player.opponent || ''});
              CARDS.length = Math.min(CARDS.length, 24);
              want(rec.sport); schedule();
            }, function () { /* not JSON */ });
          }, function () { /* the hub sees its own failure */ });
        }
      } catch (e) { /* never in the hub's way */ }
      return p;
    };
  }
  function cardOf(name) { for (var i = 0; i < CARDS.length; i++) if (CARDS[i].name === name) return CARDS[i]; return null; }

  /* ---------------------------------------------------------------- painting */
  function ownText(el) {
    var s = '';
    if (el) el.childNodes.forEach(function (n) { if (n.nodeType === 3) s += n.nodeValue; });
    return s.trim();
  }
  function initials(n) {
    var w = String(n || '').trim().split(/\s+/).filter(Boolean);
    return w.length ? (w.length > 1 ? w[0].charAt(0) + w[w.length - 1].charAt(0) : w[0].slice(0, 2)).toUpperCase() : '';
  }
  function img(cls, src, box) {
    var im = D.createElement('img');
    im.className = cls; im.alt = ''; im.setAttribute('aria-hidden', 'true'); im.decoding = 'async';
    im.width = im.height = cls === 'v3f-img' ? 160 : 128;
    im.addEventListener('load', function () { im.classList.add('is-in'); if (cls === 'v3f-img') box.classList.add('v3f-on'); });
    im.addEventListener('error', function () {
      im.remove();
      if (cls === 'v3f-img') box.classList.remove('v3f-on');      /* the monogram under it stays */
    });
    im.src = src;
    if (im.complete && im.naturalWidth) { im.classList.add('is-in'); if (cls === 'v3f-img') box.classList.add('v3f-on'); }
    return im;
  }
  /* NBA / NFL: the hub's own face carries the exact ESPN id in its hotlink (kept on the node once the hotlink is swapped) */
  function espnPid(box) {
    if (!box) return '';
    var have = box.getAttribute('data-v3f-pid');
    if (have) return have;
    var im = box.querySelector(':scope > img.u-face-shot'), m = im && /\/headshots\/(nba|nfl)\/players\/full\/(\d+)\.png/.exec(im.getAttribute('src') || '');
    var pid = m ? m[1] + ':player:espn:' + m[2] : '';
    if (pid) box.setAttribute('data-v3f-pid', pid);
    return pid;
  }
  /* box: the card's avatar node (hub .u-face, slips .sb-av / .sb-face, player .v3p-avatar, or one added here) */
  function paint(box, f) {
    if (!box || !f) return;
    var sig = f.i + '|' + f.t;
    if (box.getAttribute('data-v3f') === sig && (!f.i || box.querySelector(':scope > .v3f-img'))) return;
    box.setAttribute('data-v3f', sig);
    box.classList.add('v3f');
    box.querySelectorAll(':scope > .v3f-img, :scope > .v3f-badge').forEach(function (n) { n.remove(); });
    if (f.i) box.querySelectorAll(':scope > img.u-face-shot').forEach(function (n) { n.remove(); });   /* the ESPN hotlink */
    if (f.t) box.querySelectorAll(':scope > img.u-face-logo').forEach(function (n) { n.remove(); });
    if (!f.i) box.classList.remove('v3f-on');
    if (f.i) box.appendChild(img('v3f-img', f.i, box));
    if (f.t) box.appendChild(img('v3f-badge', f.t, box));
  }
  function newBox(name, size, cls) {
    var s = D.createElement('span');
    s.className = 'u-face v3f v3f-new' + (cls ? ' ' + cls : '');
    s.style.setProperty('--face', size + 'px');
    s.setAttribute('aria-hidden', 'true');
    var i = D.createElement('i'); i.textContent = initials(name); s.appendChild(i);
    return s;
  }

  /* board rows: name + sport tag in the row's button */
  function boardRows(scope) {
    scope.querySelectorAll('#u-hub td.h-c-player').forEach(function (td) {
      var btn = td.querySelector(':scope > .h-player'); if (!btn) return;
      var b = btn.querySelector('b'), tag = btn.querySelector('small > i:last-child');
      var sport = tag ? SPORT_OF[tag.textContent.trim()] : null, name = b ? b.textContent.trim() : '';
      if (!sport || !name) return;
      var box = td.querySelector(':scope > .u-face'), f = face(sport, name, espnPid(box));
      if (!box) {
        if (!anyPics(sport)) return;
        box = newBox(name, 32, 'h-face'); td.insertBefore(box, btn);
      }
      if (f) paint(box, f);
    });
    scope.querySelectorAll('#u-hub .h-player-head').forEach(function (h) {
      var b = h.querySelector(':scope > b'), sp = h.querySelector(':scope > span');
      var name = b ? b.textContent.trim() : '', bits = sp ? sp.textContent.split(' · ') : [];
      var sport = SPORT_OF[(bits[bits.length - 1] || '').trim()];
      if (!sport || !name) return;
      var box = h.querySelector(':scope > .u-face'), f = face(sport, name, espnPid(box));
      if (!box) { if (!anyPics(sport)) return; box = newBox(name, 24, 'h-face'); h.insertBefore(box, h.firstChild); }
      if (f) paint(box, f);
    });
  }
  /* the player card hero + its match strip */
  function playerCard(scope) {
    var head = scope.querySelector('#u-hub #hub-detail .h-card-head');
    if (!head) return;
    var title = head.querySelector('#hub-card-title'), name = ownText(title);
    var c = cardOf(name), sport = c ? c.sport : (root.getAttribute('data-sc-sport') || '');
    var box = head.querySelector(':scope > .u-face, :scope > .v3p-avatar');
    var f = name ? face(WITH[sport] ? sport : null, name, c && c.id) : null;
    if (box && f) paint(box, f);
    if (!WITH[sport]) return;
    scope.querySelectorAll('#u-hub #hub-detail .sc-matchup__team').forEach(function (t) {
      var b = t.querySelector(':scope > b'), team = b ? b.textContent.trim() : '', logo = teamLogo(sport, team);
      var have = t.querySelector(':scope > .v3f-team');
      if (!logo) { if (have) have.remove(); return; }
      if (have && have.getAttribute('data-src') === logo) return;
      if (have) have.remove();
      var im = D.createElement('img');
      im.className = 'v3f-team'; im.alt = ''; im.setAttribute('aria-hidden', 'true'); im.width = im.height = 128; im.decoding = 'async';
      im.setAttribute('data-src', logo);
      im.addEventListener('load', function () { im.classList.add('is-in'); });
      im.addEventListener('error', function () { im.remove(); });
      im.src = logo;
      if (im.complete && im.naturalWidth) im.classList.add('is-in');
      t.insertBefore(im, b);
    });
  }
  /* slip legs (studio, slip column, tray) and Auto-build cards */
  function legs(scope) {
    scope.querySelectorAll('.u-leg').forEach(function (leg) {
      var nm = leg.querySelector('.u-leg-name'); if (!nm) return;
      var tag = nm.querySelector(':scope > i'), sport = tag ? SPORT_OF[tag.textContent.trim()] : null, name = ownText(nm);
      var box = leg.querySelector('.sb-face > .u-face, :scope > .sb-av, .u-leg-name > .u-face');
      if (!box || !sport || !name) return;
      paint(box, face(sport, name, espnPid(box)));                /* a leg is a current board offer */
    });
    scope.querySelectorAll('#u-hub .h-generated-card li').forEach(function (li) {
      var box = li.querySelector(':scope > .sb-av, :scope > .u-face'), who = li.querySelector('div > strong');
      var name = who ? who.textContent.trim() : '';
      if (!box || !name) return;
      paint(box, face(null, name));
    });
  }
  /* My picks rows (hub section and tray) */
  function picks(scope) {
    scope.querySelectorAll('.u-pick > button').forEach(function (btn) {
      var b = btn.querySelector(':scope > span > b'), sm = btn.querySelector(':scope > span > small');
      if (!b || !sm) return;
      var name = b.textContent.replace(/\s+(Over|Under)\s+\S+\s*$/, '').trim();
      var sport = SPORT_OF[(sm.textContent.split(' · ')[0] || '').trim()];
      if (!sport || !name) return;
      var box = btn.querySelector(':scope > .u-face'), pid = espnPid(box), f = face(sport, pid ? '' : name, pid);
      if (f && !pid) {                                       /* a receipt has no player id: only today's board player with that name
                                                                who plays for one of the receipt's two teams */
        var match = (sm.textContent.split(' · ').pop() || '').split(' vs ').map(function (x) { return x.trim(); });
        if (!f.team || match.indexOf(f.team) < 0) f = null;
      }
      if (!box) {
        if (!anyPics(sport)) return;
        box = newBox(name, 32); btn.insertBefore(box, btn.firstChild);
        var art = btn.closest('.u-pick'); if (art) art.classList.add('u-pick-face');   /* the row's face column (picks.css) */
      }
      if (f) paint(box, f);
    });
  }

  function pass() {
    timer = 0;
    try { boardRows(D); } catch (e) { /* pictures are optional */ }
    try { playerCard(D); } catch (e) { /* " */ }
    try { legs(D); } catch (e) { /* " */ }
    try { picks(D); } catch (e) { /* " */ }
  }
  var timer = 0;
  function schedule() { if (!timer) timer = (window.requestAnimationFrame || setTimeout)(pass); }

  if (PAGE === 'landing' || PAGE === 'record') return;      /* signed-out pages: no member board to read */
  if (window.MutationObserver) {
    new MutationObserver(function (list) {
      for (var i = 0; i < list.length; i++) {
        var t = list[i].target;
        if (list[i].addedNodes.length && !(t.classList && (t.classList.contains('v3f') || t.classList.contains('sc-matchup__team')))) { schedule(); return; }
      }
    }).observe(D.documentElement, {childList: true, subtree: true});
  }
  setInterval(function () {
    if (D.hidden) return;
    Object.keys(SEEN).forEach(want);
  }, REFRESH_MS + 1000);
  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', schedule); else schedule();
})();
