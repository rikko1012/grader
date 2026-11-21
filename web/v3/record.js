/* v3 record.js - owned by page group C (public record).
   Scope: the public /record page (web/record.html + record.js).
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   Display only. web/record.js keeps loading, switching and painting the record; this file adds, after each paint:
   - the brand mark beside the page's own "1TIME SCOUT" link, and the title's second line set in the muted ink (a span);
   - under "Actual hit rate" the gap to the "Average stated chance" shown beside it (both on screen, so it always reconciles;
     no delta when either is "\u2014");
   - each calendar day marked against that average stated chance (at or above / within 3 pts / further below), as on the
     member Record. Nothing is fetched here. */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, esc = V.esc, MINUS = '\u2212';
  function pctOf(text) { var m = /^\s*(-?\d+(?:\.\d+)?)%/.exec(text || ''); return m ? parseFloat(m[1]) / 100 : null; }

  function chrome() {
    var link = document.querySelector('main > header > a[href="/"]');
    if (link && !link.querySelector('.sc-brand__mark')) {
      var mark = document.createElement('span');
      mark.className = 'sc-brand__mark';
      mark.setAttribute('aria-hidden', 'true');
      mark.textContent = '1T';
      link.insertBefore(mark, link.firstChild);
      link.classList.add('v3-brand');
      /* the same 1T Scout header as every member page: "Scout" beside the mark (the page's own wordmark stays for readers) */
      var word = document.createElement('span');
      word.className = 'v3-brand-word';
      word.setAttribute('aria-hidden', 'true');
      word.textContent = 'Scout';
      mark.after(word);
    }
    var h1 = document.querySelector('main > h1');
    if (h1 && !h1.querySelector('.v3-h1-sub')) {
      var br = h1.querySelector('br');
      if (br && br.nextSibling && br.nextSibling.nodeType === 3) {
        var s = document.createElement('span');
        s.className = 'v3-h1-sub';
        s.textContent = br.nextSibling.textContent;
        h1.replaceChild(s, br.nextSibling);
      }
    }
  }

  /* wording the same on every record page: one en-dash W–L, singular for one ("1 push"), and the snapshot as a date and UTC time */
  var ONE = {wins: 'win', losses: 'loss', pushes: 'push', voids: 'void'};
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function utcText(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return null;
    var p2 = function (n) { return (n < 10 ? '0' : '') + n; };
    return MON[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + d.getUTCFullYear() + ', ' + p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + ' UTC';
  }
  function words(root) {
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), n, fix = [];
    while ((n = w.nextNode())) {
      var t = n.nodeValue, u = t.replace(/(^|[^\d.])1 (wins|losses|pushes|voids)\b/g, function (m, a, k) { return a + '1 ' + ONE[k]; })
        .replace(/Snapshot: (\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:?\d\d)?)/, function (m, iso) { var x = utcText(iso); return x ? 'Snapshot: ' + x : m; });
      if (u !== t) fix.push([n, u]);
    }
    fix.forEach(function (f) { f[0].nodeValue = f[1]; });
  }
  function decorate() {
    var root = document.getElementById('record');
    if (!root) return;
    words(root);
    var scores = root.querySelector('.scores');
    if (!scores) return;
    var cells = scores.children, hasSlips = !!root.querySelector('nav.view [data-view="slips"][aria-pressed="true"]');
    if (hasSlips || cells.length < 3) return;                      /* the slip view has no stated chance: no delta there */
    var obs = pctOf(cells[1].querySelector('b') && cells[1].querySelector('b').textContent),
      pr = pctOf(cells[2].querySelector('b') && cells[2].querySelector('b').textContent);
    if (obs != null && pr != null && !cells[1].querySelector('.v3-delta')) {
      var d = obs - pr, v = Math.round(d * 1000) / 10, el = document.createElement('span');
      el.className = 'v3-delta ' + (v === 0 ? 'is-even' : d >= 0 ? 'is-good' : d > -0.03 ? 'is-near' : 'is-poor');   /* 0.0 pts: no arrow, no colour */
      el.innerHTML = (v === 0 ? '' : '<i class="v3-tri ' + (d >= 0 ? 'up' : 'down') + '" aria-hidden="true"></i>') +
        esc((v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(v).toFixed(1) + ' pts vs stated');
      cells[1].appendChild(el);
    }
    if (pr != null) [].forEach.call(root.querySelectorAll('.calendar details'), function (dd) {
      if (dd.dataset.v3) return;
      var x = pctOf(dd.querySelector('summary b') && dd.querySelector('summary b').textContent);
      dd.dataset.v3 = x == null ? 'none' : x >= pr ? 'good' : x > pr - 0.03 ? 'near' : 'poor';
    });
    var cal = root.querySelector('.calendar');
    if (pr != null && cal && cal.querySelector('details[data-v3]:not([data-v3="none"])') && !root.querySelector('.v3-cal-key')) {
      cal.insertAdjacentHTML('afterend', '<p class="v3-cal-key">Bar under each day vs the average stated chance, ' + (pr * 100).toFixed(1) +
        '%: <span><i class="good"></i>at or above</span><span><i class="near"></i>within 3 pts below</span><span><i class="poor"></i>further below</span></p>');
    }
  }

  function start() {
    chrome();
    var root = document.getElementById('record');
    if (!root) return;
    var busy = false;
    new MutationObserver(function () {
      if (busy) return;
      busy = true;
      requestAnimationFrame(function () { busy = false; try { decorate(); } catch (e) { /* display only */ } });
    }).observe(root, {childList: true, subtree: true});
    try { decorate(); } catch (e) { /* display only */ }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
