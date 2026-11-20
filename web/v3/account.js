/* v3 account.js - owned by page group D (account, help, sign-in states, nav extras).
   Scope: member-side account / help / paywall / signed-in states inside the member pages.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   Tools is the account and preferences screen in v3 (D mock "Account and preferences"). The hub's sidebar - which carries its
   Sign out link - is hidden under the v3 header, so Tools gets one Account card at the top: "Signed in with Whop", the name the
   hub itself shows (its own Sign out link's "Signed in as ..." title or its account menu), and the same /auth/logout link.
   The hub rewrites #hub-tools-body each time Tools opens; an observer puts the card back. Nothing else is added. */
(function () {
  'use strict';
  var V = window.ScoutV3;
  if (!V) return;

  function who(hub) {
    var a = hub.querySelector('.h-signout[title]'), t = a && a.getAttribute('title') || '';
    if (/^Signed in as /.test(t)) return t.slice(13).trim();
    var s = hub.querySelector('.h-account summary span'), n = s && s.textContent.trim();
    return n && n !== 'Account' ? n : '';
  }

  function card(hub) {
    var body = hub.querySelector('#hub-tools-body');
    if (!body || body.querySelector('.v3-acct')) return;
    var name = who(hub), sec = document.createElement('section');
    sec.className = 'v3-acct';
    sec.setAttribute('aria-labelledby', 'v3-acct-t');
    sec.innerHTML = '<div><h2 id="v3-acct-t">Account</h2><b>Signed in with Whop' + (name ? ' · ' + V.esc(name) : '') + '</b>' +
      '<p>Your membership, billing and cancelling live in your Whop account.</p></div><a href="/auth/logout">Sign out</a>';
    body.insertBefore(sec, body.firstChild);
  }

  V.onHub(function (hub) {
    var run = function () { try { card(hub); } catch (e) { if (window.console) console.error(e); } };
    run();
    var body = hub.querySelector('#hub-tools-body');          // only the Tools body: the board's own re-renders never reach this
    if (window.MutationObserver) new MutationObserver(run).observe(body || hub, { childList: true, subtree: !body });
  });
})();
