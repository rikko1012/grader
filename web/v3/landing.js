/* v3 landing.js - owned by page group D (landing).
   Scope: the signed-out landing page (web/landing.html) and /preview/v3/landing.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   The page's own script still paints everything (real board rows, the record count, member reviews, the demos). This file only
   adds what the v3 shape needs and CSS cannot draw:
   - the header wordmark reads "Scout" beside the 1T tile (the link keeps its "1time Scout" name);
   - the example pick card gets the v3 chance meter, filled from the chance the page itself prints (#hc-pct), and the approved
     "Model estimate" label under the number (the card's own line still reads "Scout's chance at exactly <line>"). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var D = document;

  function brand() {
    var a = D.querySelector('header.top .brand'), b = a && a.querySelector('b');
    if (!b || a.classList.contains('v3-named')) return;
    a.classList.add('v3-named');
    b.textContent = 'Scout';
  }

  function heroCard() {
    var pct = D.getElementById('hc-pct');
    if (!pct || pct.getAttribute('data-v3')) return;
    pct.setAttribute('data-v3', '1');
    var box = pct.parentNode, small = box.querySelector('small');
    var meter = D.createElement('span'), fill = D.createElement('i');
    meter.className = 'v3-meter'; meter.setAttribute('aria-hidden', 'true');
    meter.appendChild(fill);
    box.insertBefore(meter, small || null);
    if (small) small.textContent = 'Model estimate';
    function sync() {
      var v = parseFloat(String(pct.textContent || '').replace(',', '.'));
      fill.style.setProperty('--p', isFinite(v) ? String(Math.max(0, Math.min(100, v)) / 100) : '0');
    }
    sync();
    if (window.MutationObserver) new MutationObserver(sync).observe(pct, { childList: true, characterData: true, subtree: true });
  }

  function init() {
    try { brand(); } catch (e) { if (window.console) console.error(e); }
    try { heroCard(); } catch (e) { if (window.console) console.error(e); }
  }
  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', init); else init();
})();
