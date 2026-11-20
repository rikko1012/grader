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

  function decorate() {
    var root = document.getElementById('record');
    if (!root) return;
    var scores = root.querySelector('.scores');
    if (!scores) return;
    var cells = scores.children, hasSlips = !!root.querySelector('nav.view [data-view="slips"][aria-pressed="true"]');
    if (hasSlips || cells.length < 3) return;                      /* the slip view has no stated chance: no delta there */
    var obs = pctOf(cells[1].querySelector('b') && cells[1].querySelector('b').textContent),
      pr = pctOf(cells[2].querySelector('b') && cells[2].querySelector('b').textContent);
    if (obs != null && pr != null && !cells[1].querySelector('.v3-delta')) {
      var d = obs - pr, v = Math.round(d * 1000) / 10, el = document.createElement('span');
      el.className = 'v3-delta ' + (d >= 0 ? 'is-good' : d > -0.03 ? 'is-near' : 'is-poor');
      el.innerHTML = '<i class="v3-tri ' + (d >= 0 ? 'up' : 'down') + '" aria-hidden="true"></i>' +
        esc((v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(v).toFixed(1) + ' pts vs stated');
      cells[1].appendChild(el);
    }
    if (pr != null) [].forEach.call(root.querySelectorAll('.calendar details'), function (dd) {
      if (dd.dataset.v3) return;
      var x = pctOf(dd.querySelector('summary b') && dd.querySelector('summary b').textContent);
      dd.dataset.v3 = x == null ? 'none' : x >= pr ? 'good' : x > pr - 0.03 ? 'near' : 'poor';
    });
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
