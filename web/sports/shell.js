/* Scout member shell - the explicit interface Sports uses to sit inside the existing 1time / Scout member shell.
   The esports pages (web/index.html, web/lol.html) are NOT changed or imported: this module re-creates the shared
   header (1time mark, product switch, member line) from one declared list, so the Sports page cannot alter them.

   ScoutShell.PRODUCTS  [{key, label, href}] - the destinations of the member shell (CS2, LoL, Sports)
   ScoutShell.mount(el, {active})            - renders the header into el (a <header>)
   ScoutShell.member()                       - GET /auth/me (the esports server's member check) -> {required, member, name, upgrade}
   ScoutShell.gate(el, info)                 - the sign-in / upgrade card for non-members (no data is ever fetched)
*/
(function (root) {
  "use strict";
  var PRODUCTS = [
    {key: "cs2", label: "CS2", href: "/"},
    {key: "lol", label: "LoL", href: "/lol"},
    {key: "sports", label: "Sports", href: "/sports"}
  ];
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  var MARK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#170b00" stroke-width="2.4" ' +
             'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="7"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/></svg>';

  function mount(el, opts) {
    var active = (opts && opts.active) || "sports";
    el.innerHTML =
      '<a class="ss-brand" href="/sports" aria-label="1time Scout Sports home"><span class="ss-mark">' + MARK + '</span>' +
      '<span class="ss-word">1time <em>Scout</em></span></a>' +
      '<nav class="ss-products" aria-label="Product">' + PRODUCTS.map(function (p) {
        var on = p.key === active;
        return '<a href="' + p.href + '"' + (on ? ' class="on" aria-current="page"' : "") + ">" + esc(p.label) + "</a>";
      }).join("") + "</nav>" +
      '<span class="ss-grow"></span><span class="ss-who" id="ss-who"></span>';
  }

  function member() {
    return fetch("/auth/me", {credentials: "same-origin"}).then(function (r) { return r.json(); })
      .catch(function () { return {}; });
  }

  function showMember(info) {
    var w = document.getElementById("ss-who");
    if (!w) return;
    if (info && info.name) w.innerHTML = esc(info.name) + ' · <a href="/auth/logout">Sign out</a>';
    else if (info && info.signed_in) w.innerHTML = '<a href="/auth/logout">Sign out</a>';
  }

  function gate(el, info) {
    var up = info && info.upgrade;
    el.innerHTML = '<section class="ss-gate" aria-labelledby="ss-gate-h"><h1 id="ss-gate-h">Scout Sports is for members</h1>' +
      "<p>Sign in with your 1time membership to open the NBA and NFL workspace.</p>" +
      '<p><a class="ss-btn ss-btn-primary" href="/auth/login">Sign in</a>' +
      (up ? ' <a class="ss-btn" href="' + esc(up) + '">Get membership</a>' : "") + "</p></section>";
  }

  var api = {PRODUCTS: PRODUCTS, mount: mount, member: member, showMember: showMember, gate: gate, esc: esc};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ScoutShell = api;
})(this);
