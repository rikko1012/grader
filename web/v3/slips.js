/* v3 slips.js - owned by page group B (slips).
   Scope: Slip Studio (#hub-studio-section), the slip column (.h-slip), book dialog, share card.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   What it does (display only; every node the hub renders stays, with its id and listeners):
   - Slips band: the title, the hub's own sync line and its My slip actions (+ Add player props, Download lineup image) move into
     one lit band above the tabs.
   - My slip: an aside with "This slip" (read from the entries on screen: platform, entry type, picks, payout, checked time) and the
     hub's "Before you track". Each leg gets an initials avatar (when it has no face) and its read (chance, grade or Lean) set at the
     right as the board row reads; the original read text stays in the leg for screen readers.
   - Auto-build: the hub's own controls are grouped into two panels; each <select> gets a segmented control that sets that select
     and fires its change event (the select stays the source of truth); the Generate block becomes the batch rail, mirroring the
     hub's live summary line. */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, esc = V.esc;
  var DL = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3.5v9M6 9l4 4 4-4M4 16.5h12"/></svg>';

  function initials(name) {
    var parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '';
    return (parts.length > 1 ? parts[0].charAt(0) + parts[parts.length - 1].charAt(0) : parts[0].charAt(0)).toUpperCase();
  }
  function textOf(el) {                                    /* own text only (no child elements such as the sport tag) */
    var s = '';
    if (!el) return s;
    el.childNodes.forEach(function (n) { if (n.nodeType === 3) s += n.nodeValue; });
    return s.trim();
  }

  /* ---------------------------------------------------------------- leg decoration (studio + column) */
  var READ = /^(.*?)\s*((?:\d+(?:\.\d+)?%)|--)\s*$/;
  function readChip(label) {
    var g = /^([A-F][+-]?) (Over|Under)$/.exec(label);
    if (g) return '<span class="sc-grade sc-grade--sm"' + (g[1].charAt(0) === 'C' ? ' data-tier="c"' : '') + '>' + esc(g[1]) + '</span>';
    if (/^Lean (Over|Under)$/.test(label)) return '<span class="sc-grade sc-grade--sm sc-grade--lean">' + esc(label) + '</span>';
    return '';
  }
  function decorateLeg(leg) {
    if (leg.classList.contains('sb-dec')) return;
    leg.classList.add('sb-dec');
    var name = leg.querySelector('.u-leg-name');
    if (name) {
      var face = name.querySelector('.u-face');
      if (face) {
        var wrap = document.createElement('span'); wrap.className = 'sb-face';
        wrap.appendChild(face); leg.insertBefore(wrap, leg.firstChild);   /* the hub's own face node (its image load / fallback handlers are document-wide) */
      } else {
        var av = document.createElement('span'); av.className = 'sb-av'; av.setAttribute('aria-hidden', 'true');
        av.textContent = initials(textOf(name)); leg.insertBefore(av, leg.firstChild);
      }
    }
    /* the period stays one token ("Maps 1-2", "Full game"): never broken after its hyphen (the Nov 6 wrap rule); same text */
    var pick = leg.querySelector('.u-leg-pick');
    if (pick) [].slice.call(pick.childNodes).forEach(function (n) {
      if (n.nodeType !== 3) return;
      var m2 = /(·\s*)([^·]+?)\s*$/.exec(n.nodeValue);
      if (!m2 || !m2[2].trim()) return;
      var at = n.nodeValue.length - m2[0].length + m2[1].length, tail = n.splitText(at), rest = tail.nodeValue.replace(/\s+$/, '');
      var sp = document.createElement('span'); sp.className = 'sb-per'; sp.textContent = rest;
      tail.nodeValue = tail.nodeValue.slice(rest.length);
      tail.before(sp);
    });
    var meta = leg.querySelector('.u-leg-meta'), first = meta && meta.firstChild;
    if (!first || first.nodeType !== 3) return;
    var raw = first.nodeValue, rest = '', cut = raw.indexOf(' · ');
    if (cut >= 0) { rest = raw.slice(cut); raw = raw.slice(0, cut); }
    var m = READ.exec(raw.trim());
    if (!m || !m[1]) return;
    var label = m[1].trim(), chance = m[2] === '--' ? '—' : m[2], chip = readChip(label);
    var src = document.createElement('span'); src.className = 'sb-src'; src.textContent = raw;
    meta.replaceChild(src, first);
    if (rest) {
      var sep = document.createElement('span'); sep.className = 'sb-sep'; sep.textContent = ' · ';
      src.after(sep);
      var tail = rest.slice(3);
      if (tail) sep.after(document.createTextNode(tail));
    }
    if (!meta.textContent.replace(src.textContent, '').trim()) meta.classList.add('sb-only-src');
    var read = document.createElement('span'); read.className = 'sb-read'; read.setAttribute('aria-hidden', 'true');
    read.innerHTML = '<b class="sc-num">' + esc(chance) + '</b><small>' + (chip ? 'Model estimate' : esc(label)) + '</small>' + chip;
    leg.appendChild(read);
  }
  /* each leg's stake, from the board record it was added from (the hub's own leg id: sport|event|player|stat|period|platform|side|line)
     and only on Scout's side: the stake the board row and the card state for that line. No record on the board now = no stake shown. */
  var legIx = {n: -1, map: {}};
  function legIndex() {
    var recs = V.boardRecs ? V.boardRecs() : {}, keys = Object.keys(recs);
    if (keys.length === legIx.n) return legIx.map;
    var map = {};
    keys.forEach(function (k) {
      var r = recs[k];
      if (!r || !r.scout || !r.scout.side) return;
      map[[r.sport, r.event.id, r.player.id || r.player.name, r.market.stat, r.market.period, r.offer.platform, r.scout.side, r.offer.line].join('|')] = r;
    });
    legIx = {n: keys.length, map: map};
    return map;
  }
  function legStake(leg) {
    var x = leg.querySelector('[data-u="leg-x"]'), read = leg.querySelector('.sb-read');
    if (!x || !read || !V.stakeOf) return;
    var r = legIndex()[x.getAttribute('data-id')], st = r ? V.stakeOf(r, null) : null, have = read.querySelector('.sb-stake');
    if (st == null) { if (have) have.remove(); return; }
    var html = 'Stake <b>' + esc(st) + 'U</b>';
    if (!have) { have = document.createElement('small'); have.className = 'sb-stake'; read.appendChild(have); }
    if (have.innerHTML !== html) have.innerHTML = html;
    if (V.leanOnly && V.leanOnly(r)) {
      var g = read.querySelector('.sc-grade'), lab = 'Lean ' + (r.scout.side === 'over' ? 'Over' : 'Under');
      if (!g) read.insertAdjacentHTML('beforeend', '<span class="sc-grade sc-grade--sm sc-grade--lean">' + esc(lab) + '</span>');
      var sm = read.querySelector(':scope > small:not(.sb-stake)');
      if (sm && sm.textContent === 'No grade yet') sm.textContent = 'Model estimate';
    }
  }
  function decorateCard(li) {                               /* Auto-build slip cards: initials when the leg has no face */
    if (li.classList.contains('sb-dec')) return;
    li.classList.add('sb-dec');
    if (li.querySelector('.u-face')) return;
    var who = li.querySelector('div > strong');
    var av = document.createElement('span'); av.className = 'sb-av'; av.setAttribute('aria-hidden', 'true');
    av.textContent = initials(who && who.textContent); li.insertBefore(av, li.firstChild);
  }

  /* ---------------------------------------------------------------- "This slip" (read from the entries on screen) */
  function paintSummary(sec) {
    var card = sec.querySelector('.sb-sum--slip');
    if (!card) return;
    var entries = sec.querySelectorAll('#hub-studio-body .u-entry');
    if (!entries.length) { card.hidden = true; return; }
    var rows = [];
    entries.forEach(function (e) {
      var h = e.querySelector('.u-entry-h > span'), type = e.querySelector('.u-entry-h .u-seg [aria-pressed="true"]');
      var picks = e.querySelectorAll('.u-leg').length, pay = null;
      e.querySelectorAll('.u-slip-metrics b').forEach(function (b) { if (!pay && /×$/.test(b.textContent.trim())) pay = b.textContent.trim(); });
      rows.push([textOf(h) + (type ? ' · ' + type.textContent.trim() : ''), picks + (picks === 1 ? ' pick' : ' picks') + (pay ? ' · ' + pay : '')]);
    });
    var st = sec.querySelector('#hub-studio-body .u-status p span');
    var at = st && /Checked (.+?)\.?$/.exec(st.textContent.trim());
    if (at) rows.push(['Checked', at[1]]);
    var html = '<h3>This slip</h3><dl>' + rows.map(function (r) { return '<div><dt>' + esc(r[0]) + '</dt><dd class="sc-num">' + esc(r[1]) + '</dd></div>'; }).join('') + '</dl>';
    if (card.innerHTML !== html) card.innerHTML = html;
    card.hidden = false;
  }

  /* ---------------------------------------------------------------- Auto-build: segmented controls over the hub's own selects */
  var NUMBERED = /^(\d+) (?:slips?|picks?)$/;
  function syncSeg(sel) {
    var seg = sel.parentNode.querySelector('.sb-seg[data-for="' + sel.id + '"]');
    if (!seg) return;
    var opts = [].slice.call(sel.options), num = opts.every(function (o) { return NUMBERED.test(o.textContent.trim()); });
    var key = opts.map(function (o) { return o.value + '|' + o.textContent + '|' + o.disabled; }).join(';');
    if (seg.dataset.key !== key) {
      seg.dataset.key = key;
      seg.classList.toggle('is-num', num);
      seg.classList.toggle('is-wrap', opts.length > 3 && !num);
      seg.innerHTML = opts.map(function (o) {
        var t = o.textContent.trim(), short = num ? NUMBERED.exec(t)[1] : t;
        return '<button type="button" data-v="' + esc(o.value) + '"' + (o.disabled ? ' disabled' : '') + (short !== t ? ' aria-label="' + esc(t) + '"' : '') + '>' + esc(short) + '</button>';
      }).join('');
    }
    seg.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-v') === sel.value)); });
  }
  function syncAll(sec) { sec.querySelectorAll('.sb-panel select').forEach(syncSeg); }

  var SUMMARY_TAIL = {};
  function mirrorSummary(sec) {
    var p = document.getElementById('hub-generation-summary'), big = sec.querySelector('.sb-big'), dl = sec.querySelector('.sb-rail .sb-sum');
    if (!p || !big || !dl) return;
    var parts = p.textContent.split(' · '), type = document.getElementById('hub-generation-type');
    var typeText = type && type.selectedOptions[0] ? type.selectedOptions[0].textContent.trim() : '';
    if (parts.length < 3) {                                  /* "Select at least one sport ..." */
      big.innerHTML = esc(p.textContent) + '<small></small>'; dl.innerHTML = ''; return;
    }
    var rules = [];
    [['hub-generation-games'], ['hub-generation-repeats', 'none'], ['hub-generation-lines'], ['hub-generation-awphs', 'out']].forEach(function (r) {
      var s = document.getElementById(r[0]);
      if (!s || !s.selectedOptions[0] || (r[1] && s.value !== r[1])) return;
      if (r[0] === 'hub-generation-awphs') { if (s.closest('[hidden]') || !sec.querySelector('#hub-generation-sports input[value=cs2]:checked')) return; rules.push('No AWPer headshots'); return; }
      if (r[0] === 'hub-generation-lines' && s.value === 'all') return;
      rules.push(s.selectedOptions[0].textContent.trim());
    });
    var row = function (k, v) { return v ? '<div><dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd></div>' : ''; };
    big.innerHTML = esc(parts[0]) + '<small>' + esc(parts.slice(1).join(' · ')) + '</small>';
    dl.innerHTML = row('Platform', parts[1] + (typeText ? ' · ' + typeText : '')) + row('Sports', parts[2]) + row('Rules', rules.join(' · ')) +
      (parts.length > 3 ? row('Filter', parts.slice(3).join(' · ')) : '');
  }

  function buildGenerator(sec) {
    var gen = sec.querySelector('.h-generator'), controls = gen && gen.querySelector('.h-generator-controls');
    if (!gen || !controls || gen.querySelector('.sb-bcols')) return;
    var cols = document.createElement('div'); cols.className = 'sb-bcols';
    var build = document.createElement('div'); build.className = 'sb-build';
    var rail = document.createElement('div'); rail.className = 'sb-rail';
    cols.appendChild(build); cols.appendChild(rail);
    var panels = [], cur = null;
    var sports = document.getElementById('hub-generation-sports');
    [].slice.call(controls.children).forEach(function (el) {
      if (el.classList.contains('h-generator-step')) {
        cur = document.createElement('section'); cur.className = 'sb-panel'; cur.setAttribute('aria-label', (el.textContent || '').replace(/^\s*\d+\s*/, '').trim());
        panels.push(cur); cur.appendChild(el);
        if (panels.length === 1 && sports) cur.appendChild(sports);
        return;
      }
      if (el.classList.contains('h-generation-submit')) { rail.appendChild(el); return; }
      if (cur) cur.appendChild(el);
    });
    panels.forEach(function (pn) { build.appendChild(pn); });
    var status = document.getElementById('hub-generation-status');
    if (status) rail.appendChild(status);
    var how = [].slice.call(gen.children).filter(function (c) { return c.tagName === 'DETAILS'; })[0];
    if (how) build.appendChild(how);
    controls.hidden = true;                                  /* now empty: its children moved into the panels and the rail */
    gen.appendChild(cols);
    /* each labelled select: a settings row with a segmented control (the select stays, hidden, as the source of truth) */
    cols.querySelectorAll('.sb-panel label').forEach(function (lab) {
      var sel = lab.querySelector(':scope > select');
      if (!sel || !sel.id) return;
      lab.classList.add('sb-set');
      lab.childNodes.forEach(function (n) {
        if (n.nodeType === 3 && n.nodeValue.trim()) { var s = document.createElement('span'); s.className = 'sb-set__l'; s.textContent = n.nodeValue.trim(); lab.replaceChild(s, n); }
      });
      var l = lab.querySelector('.sb-set__l');
      var seg = document.createElement('div'); seg.className = 'sb-seg'; seg.setAttribute('role', 'group'); seg.setAttribute('data-for', sel.id);
      if (l) seg.setAttribute('aria-label', l.textContent);
      sel.after(seg);
      syncSeg(sel);
      new MutationObserver(function () { syncSeg(sel); }).observe(sel, {childList: true, subtree: true, attributes: true});
    });
    cols.addEventListener('click', function (e) {
      var b = e.target.closest('.sb-seg button');
      if (!b || b.disabled) return;
      e.preventDefault();                                    /* a click inside a <label> would otherwise focus / open its select */
      var sel = document.getElementById(b.parentNode.getAttribute('data-for'));
      if (!sel || sel.value === b.getAttribute('data-v')) return;
      sel.value = b.getAttribute('data-v');
      sel.dispatchEvent(new Event('input', {bubbles: true}));
      sel.dispatchEvent(new Event('change', {bubbles: true}));
    });
    var sub = rail.querySelector('.h-generation-submit'), p = document.getElementById('hub-generation-summary');
    if (sub && p) {
      sub.insertAdjacentHTML('afterbegin', '<span class="sb-cap" aria-hidden="true">This batch</span><p class="sb-big sc-num" aria-hidden="true"></p><dl class="sb-sum" aria-hidden="true"></dl>');
      p.classList.add('sb-mirrored');
      new MutationObserver(function () { mirrorSummary(sec); }).observe(p, {childList: true, characterData: true, subtree: true});
      mirrorSummary(sec);
    }
    sec.addEventListener('change', function () { setTimeout(function () { syncAll(sec); mirrorSummary(sec); }, 0); });
  }

  /* ---------------------------------------------------------------- studio layout */
  function buildStudio(sec) {
    if (sec.querySelector('.sb-band')) return;
    var tabs = sec.querySelector('.h-studio-tabs'), intro = sec.querySelector('.h-studio-intro');
    var band = document.createElement('div');
    band.className = 'sb-band sc-lit';
    band.innerHTML = '<h2 class="sc-lit__title">Slips</h2>';
    if (intro) {
      band.appendChild(intro);
      intro.querySelectorAll(':scope > button').forEach(iconize);
      new MutationObserver(function (list) {                  /* the hub may add its lineup-image button later */
        list.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.tagName === 'BUTTON') iconize(n); }); });
      }).observe(intro, {childList: true});
    }
    sec.insertBefore(band, tabs || sec.firstChild);
    var panel = sec.querySelector('[data-studio-panel="slip"]'), explain = panel && panel.querySelector('.h-studio-explain');
    if (panel) {
      var aside = document.createElement('div'); aside.className = 'sb-aside';
      aside.innerHTML = '<div class="sb-sum sb-sum--slip" hidden></div>';
      if (explain) aside.appendChild(explain);
      panel.appendChild(aside);
    }
    buildGenerator(sec);
    syncAll(sec);
  }
  function iconize(b) {
    if (b.dataset.sbIcon) return;
    b.dataset.sbIcon = '1';
    var t = b.firstChild;
    if (t && t.nodeType === 3 && /^\+\s*/.test(t.nodeValue)) { t.nodeValue = t.nodeValue.replace(/^\+\s*/, ''); b.insertAdjacentHTML('afterbegin', V.ICON.plus); }
    else if (b.getAttribute('data-hub') === 'share-card') b.insertAdjacentHTML('afterbegin', DL);
  }

  /* ---------------------------------------------------------------- repaint watcher (one rAF per burst of hub repaints) */
  function watch(hub, sec) {
    var queued = false;
    function run() {
      queued = false;
      hub.querySelectorAll('#hub-studio-body .u-leg:not(.sb-dec), #hub-slip-content .u-leg:not(.sb-dec)').forEach(decorateLeg);
      hub.querySelectorAll('#hub-studio-body .u-leg, #hub-slip-content .u-leg').forEach(function (l) { try { legStake(l); } catch (e) { /* the leg keeps its read */ } });
      hub.querySelectorAll('#hub-generated .h-generated-card li:not(.sb-dec)').forEach(decorateCard);
      var sync = document.getElementById('hub-studio-sync');
      if (sync) sync.classList.toggle('sb-off', /^Not yet synced/.test(sync.textContent));
      paintSummary(sec);
      if (!sec.querySelector('[data-studio-panel="build"]').hidden) syncAll(sec);
    }
    function q() { if (!queued) { queued = true; (window.requestAnimationFrame || setTimeout)(run); } }
    ['hub-studio-body', 'hub-slip-content', 'hub-generated', 'hub-studio-sync'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) new MutationObserver(q).observe(el, {childList: true, subtree: true, characterData: true});
    });
    sec.querySelectorAll('[data-studio-panel]').forEach(function (p) { new MutationObserver(q).observe(p, {attributes: true, attributeFilter: ['hidden']}); });
    document.addEventListener('scout-v3-recs', function () { legIx.n = -1; q(); });   /* a new board answer: stakes re-read */
    run();
  }

  /* an empty My slip leads with the studio (approved mock 08): the first time Slips opens in this page with nothing on the slip, the
     hub's own Auto-build tab is pressed once. My slip (its empty state) is one tap away and stays the tab whenever it holds picks. */
  function leadWithBuild(sec) {
    var done = false, auto = false;
    var tab = function (id) { return sec.querySelector('[role=tab][data-hub=studio-tab][data-id=' + id + ']'); };
    var count = function () { var n = document.getElementById('hub-studio-tab-n'); return n ? parseInt(n.textContent, 10) || 0 : 0; };
    function check() {
      if (done || sec.hidden) return;
      done = true;
      var slip = tab('slip'), build = tab('build');
      if (!count() && slip && slip.getAttribute('aria-selected') === 'true' && build) { auto = true; build.click(); }
    }
    /* the switch is ours until the member picks a tab: once the slip holds picks (a saved slip that loads late, or a pick added from
       the board) My slip comes back, so a member with picks never lands on Auto-build because of it */
    sec.addEventListener('click', function (e) { if (e.isTrusted && e.target.closest && e.target.closest('[data-hub=studio-tab]')) auto = false; }, true);
    function back() {
      if (!auto || !count() || sec.hidden) return;       /* hidden: done the next time Slips opens (the hidden observer) */
      auto = false;
      var slip = tab('slip');
      if (slip && slip.getAttribute('aria-selected') !== 'true') slip.click();
    }
    new MutationObserver(function () { check(); back(); }).observe(sec, {attributes: true, attributeFilter: ['hidden']});
    var n = document.getElementById('hub-studio-tab-n');
    if (n) new MutationObserver(back).observe(n, {childList: true, characterData: true, subtree: true});
    check();
  }
  V.onHub(function (hub) {
    var sec = document.getElementById('hub-studio-section');
    if (!sec) return;
    buildStudio(sec);
    watch(hub, sec);
    leadWithBuild(sec);
  });
})();
