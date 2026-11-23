/* Member reviews (/reviews). Member text is only ever placed with textContent, never parsed as HTML. */
(function () {
  "use strict";
  var S = { sort: "new", page: 0, me: null, photos: [], saved: [], busy: false, opener: null, seq: 0 };
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "class") e.className = attrs[k];
      else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }

  function day(iso) {
    try { return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }); } catch (_) { return ""; }
  }

  function stars(n, label) {
    var s = el("span", { class: "stars", role: "img", "aria-label": label || (n + " out of 5 stars") }, [el("i")]);
    s.firstChild.style.width = Math.max(0, Math.min(100, (n / 5) * 100)) + "%";
    return s;
  }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.cache = "no-store";
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
    });
  }

  function post(path, body) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }

  /* ---------------------------------------------------------------- summary + list */
  function showSummary(s) {
    var box = $("summary");
    if (!s || !s.count) { box.hidden = true; return; }
    box.hidden = false;
    $("avg").textContent = s.average.toFixed(1);
    var st = $("avg-stars");
    st.firstChild.style.width = (s.average / 5) * 100 + "%";
    st.setAttribute("aria-label", s.average.toFixed(1) + " out of 5 stars");
    $("count").textContent = s.count === 1 ? "from 1 member review" : "from " + s.count + " member reviews";
    var bars = $("bars");
    bars.textContent = "";
    ["5", "4", "3", "2", "1"].forEach(function (k) {
      var n = s.breakdown[k] || 0, fill = el("span", { class: "fill" });
      fill.style.width = (s.count ? (n / s.count) * 100 : 0) + "%";
      bars.appendChild(el("li", { "aria-label": k + " stars: " + n }, [el("span", { text: k + " ★", "aria-hidden": "true" }),
        el("span", { class: "track", "aria-hidden": "true" }, [fill]), el("span", { text: String(n), "aria-hidden": "true" })]));
    });
  }

  function card(r) {
    var when = el("span", { class: "when", text: day(r.created) + (r.edited ? " · edited " + day(r.edited) : "") });
    var head = el("header", null, [stars(r.stars), el("span", { class: "who", text: r.name }),
      r.verified ? el("span", { class: "badge", text: "Verified member" }) : null, when]);
    var kids = [head];
    if (r.title) kids.push(el("h3", { text: r.title }));
    kids.push(el("p", { class: "body", text: r.body }));
    if (r.photos && r.photos.length) {
      kids.push(el("div", { class: "thumbs" }, r.photos.map(function (p, i) {
        return el("button", { type: "button", "aria-label": "Open photo " + (i + 1) + " of " + r.photos.length + " from " + r.name,
          onclick: function (ev) { openPhoto(p.full, ev.currentTarget); } },
          [el("img", { src: p.thumb, alt: "", loading: "lazy", decoding: "async", width: "96", height: "96" })]);
      })));
    }
    if (r.reply && r.reply.text) kids.push(el("div", { class: "reply" }, [el("b", { text: r.reply.name + " replied" + (r.reply.at ? " · " + day(r.reply.at) : "") }), el("p", { text: r.reply.text })]));
    return el("article", { class: "review", "aria-label": r.stars + " star review by " + r.name }, kids);
  }

  function load(append) {
    var seq = ++S.seq, list = $("list");
    $("reviews").setAttribute("aria-busy", "true");
    return api("/api/reviews?sort=" + S.sort + "&page=" + S.page).then(function (d) {
      if (seq !== S.seq) return;
      $("reviews").removeAttribute("aria-busy");
      if (d._status !== 200) { list.textContent = ""; list.appendChild(el("p", { class: "error", text: "Reviews couldn't load. Refresh to try again." })); return; }
      showSummary(d.summary);
      if (!append) list.textContent = "";
      if (!append && !d.reviews.length) {
        list.appendChild(el("p", { class: "empty", text: "No reviews yet. Members: be the first to write one." }));
      }
      d.reviews.forEach(function (r) { list.appendChild(card(r)); });
      $("more").hidden = !d.more;
    }).catch(function () {
      if (seq !== S.seq) return;
      list.textContent = "";
      list.appendChild(el("p", { class: "error", text: "Reviews couldn't load. Refresh to try again." }));
    });
  }

  /* ---------------------------------------------------------------- lightbox */
  function openPhoto(src, opener) {
    var d = $("lightbox");
    S.opener = opener || null;
    $("lb-img").src = src;
    $("lb-img").alt = "Member photo";
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    $("lb-close").focus();
  }
  function closePhoto() { var d = $("lightbox"); if (d.close) d.close(); else d.removeAttribute("open"); }

  /* ---------------------------------------------------------------- write form */
  function msg(text, bad) { var m = $("form-msg"); if (m) { m.textContent = text || ""; m.className = "msg" + (bad ? " bad" : ""); } }

  function writeBox() {
    var me = S.me, box = $("write"), body = $("write-body");
    body.textContent = "";
    if (!me || !me.write_on) { box.hidden = true; return; }
    box.hidden = false;
    if (!me.signed_in) {
      body.appendChild(el("p", { text: "Scout members: sign in to write a review." }));
      body.appendChild(el("a", { class: "btn primary", href: me.sign_in, text: "Sign in with Whop" }));
      return;
    }
    if (!me.member) {
      body.appendChild(el("p", { text: "Reviews are written by paying Scout members." }));
      body.appendChild(el("a", { class: "btn", href: me.join, text: "Join on Whop" }));
      return;
    }
    body.appendChild(form(me));
  }

  function form(me) {
    var r = me.review, f = el("form", { id: "review-form", novalidate: true });
    $("write-title").textContent = r ? "Your review" : "Write a review";
    if (r && r.hidden) f.appendChild(el("p", { class: "msg bad", text: "Removed from the page" + (r.hidden_reason ? ": " + r.hidden_reason : "") + ". It stays saved here." }));
    var rate = el("div", { class: "rate" });
    [1, 2, 3, 4, 5].forEach(function (n) {
      var inp = el("input", { type: "radio", name: "stars", value: String(n), required: true, checked: r && r.stars === n });
      rate.appendChild(el("label", { "aria-label": n + (n === 1 ? " star" : " stars") }, [inp, el("span", { text: n + " ★", "aria-hidden": "true" })]));
    });
    f.appendChild(el("fieldset", null, [el("legend", { text: "Your rating" }), rate]));
    f.appendChild(el("div", null, [el("label", { class: "lbl", for: "rv-title", text: "Title (optional)" }),
      el("input", { type: "text", id: "rv-title", maxlength: String(me.title_max), value: r ? r.title : "", autocomplete: "off" })]));
    var ta = el("textarea", { id: "rv-body", maxlength: String(me.body_max), required: true, "aria-describedby": "rv-count" });
    ta.value = r ? r.body : "";
    var count = el("div", { class: "count", id: "rv-count", "aria-live": "polite" });
    function counter() { var n = ta.value.trim().length; count.textContent = n + " / " + me.body_max + (n && n < me.body_min ? " · at least " + me.body_min : ""); count.className = "count" + (n && n < me.body_min ? " bad" : ""); }
    ta.addEventListener("input", counter);
    counter();
    f.appendChild(el("div", null, [el("label", { class: "lbl", for: "rv-body", text: "Your review" }), ta, count]));
    var style = r ? r.name_style : "short", nm = me.names || { short: "Scout member", full: "Scout member" };
    var namesBox = el("div", { class: "names" }, [["short", nm.short], ["full", nm.full]].filter(function (x, i, a) { return i === 0 || x[1] !== a[0][1]; }).map(function (x) {
      return el("label", null, [el("input", { type: "radio", name: "name_style", value: x[0], checked: style === x[0] || (x[0] === "short" && style !== "full") }), el("span", { text: x[1] })]);
    }));
    f.appendChild(el("fieldset", null, [el("legend", { text: "Show my name as" }), namesBox]));
    S.photos = r ? r.photos.slice() : [];
    S.saved = S.photos.map(function (p) { return p.id; });
    if (me.photos_on || S.photos.length) {
      var ph = el("div", { class: "photos", id: "rv-photos" });
      var add = el("label", { class: "btn addphoto", id: "rv-add" }, [el("input", { type: "file", id: "rv-file", accept: "image/jpeg,image/png,image/webp,image/*", multiple: true }), el("span", { text: "Add photos" })]);
      if (!me.photos_on) add.hidden = true;
      f.appendChild(el("div", null, [el("span", { class: "lbl", text: "Photos (optional, up to " + me.max_photos + ")" }), ph, el("div", { class: "actions" }, [add]),
        el("p", { class: "hint", text: "Photos are resized and their location data removed before they are saved." })]));
      add.querySelector("input").addEventListener("change", function (ev) { addFiles(ev.target.files); ev.target.value = ""; });
    }
    var postBtn = el("button", { type: "submit", class: "primary", id: "rv-post", text: r ? "Save changes" : "Post review" });
    var acts = el("div", { class: "actions" }, [postBtn]);
    if (r) acts.appendChild(el("button", { type: "button", class: "danger", id: "rv-delete", text: "Delete my review", onclick: del }));
    f.appendChild(acts);
    f.appendChild(el("p", { class: "msg", id: "form-msg", role: "status" }));
    f.addEventListener("submit", submit);
    setTimeout(drawPhotos, 0);
    return f;
  }

  function drawPhotos() {
    var box = $("rv-photos");
    if (!box) return;
    box.textContent = "";
    S.photos.forEach(function (p, i) {
      box.appendChild(el("figure", null, [el("img", { src: p.thumb, alt: "Photo " + (i + 1) }),
        el("button", { type: "button", text: "Remove", "aria-label": "Remove photo " + (i + 1), onclick: function () { removePhoto(p.id); } })]));
    });
    var add = $("rv-add");
    if (add) add.hidden = !S.me.photos_on || S.photos.length >= S.me.max_photos;
  }

  function removePhoto(id) {
    S.photos = S.photos.filter(function (p) { return p.id !== id; });
    if (S.saved.indexOf(id) < 0) post("/api/reviews/photo/delete", { id: id });   /* saved photos go when the review is saved */
    drawPhotos();
  }

  function shrink(file) {
    /* Downscale on a canvas to <= 1600 px and re-encode as JPEG (also drops the file's own metadata). No blob: URLs (CSP). */
    function viaDataUrl() {
      return new Promise(function (ok, bad) {
        var fr = new FileReader();
        fr.onerror = function () { bad(new Error("read")); };
        fr.onload = function () { var img = new Image(); img.onload = function () { ok(img); }; img.onerror = function () { bad(new Error("decode")); }; img.src = fr.result; };
        fr.readAsDataURL(file);
      });
    }
    var src = window.createImageBitmap ? createImageBitmap(file).catch(viaDataUrl) : viaDataUrl();
    return src.then(function (img) {
      var w = img.width, h = img.height, k = Math.min(1, 1600 / Math.max(w, h));
      var c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      if (img.close) img.close();
      return new Promise(function (ok, bad) { c.toBlob(function (b) { b ? ok(b) : bad(new Error("encode")); }, "image/jpeg", 0.85); });
    });
  }

  function addFiles(files) {
    var room = S.me.max_photos - S.photos.length, list = Array.prototype.slice.call(files || [], 0, Math.max(0, room));
    if (!list.length) { msg("Up to " + S.me.max_photos + " photos.", true); return; }
    var chain = Promise.resolve();
    list.forEach(function (file) {
      chain = chain.then(function () {
        msg("Adding photo…");
        return shrink(file).then(function (blob) {
          return api("/api/reviews/photo", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
        }).then(function (d) {
          if (d._status === 201 && d.photo) { S.photos.push(d.photo); drawPhotos(); msg(""); }
          else msg(d.message || "That photo couldn't be added.", true);
        }, function () { msg("That photo type can't be used here - pick a JPG, PNG or WEBP.", true); });
      });
    });
  }

  function submit(ev) {
    ev.preventDefault();
    if (S.busy) return;
    var f = ev.target, st = f.querySelector("input[name=stars]:checked"), body = $("rv-body").value.trim();
    if (!st) { msg("Pick 1 to 5 stars.", true); return; }
    if (body.length < S.me.body_min) { msg("Write at least " + S.me.body_min + " characters.", true); $("rv-body").focus(); return; }
    var ns = f.querySelector("input[name=name_style]:checked");
    S.busy = true; $("rv-post").disabled = true; msg("Posting…");
    post("/api/reviews", { stars: +st.value, title: $("rv-title").value, body: body, name_style: ns ? ns.value : "short",
      photos: S.photos.map(function (p) { return p.id; }) }).then(function (d) {
      S.busy = false;
      if (d._status === 200 || d._status === 201) {
        S.me.review = d.review; writeBox(); msg(d._status === 201 ? "Thanks - your review is posted." : "Saved."); S.page = 0; load();
      } else { $("rv-post").disabled = false; msg(d.error && d.error !== "members_only" ? (d.message || d.error) : (d.message || "Sign in again to post."), true); }
    }, function () { S.busy = false; $("rv-post").disabled = false; msg("Couldn't post - check your connection and try again.", true); });
  }

  function del() {
    if (!window.confirm("Delete your review? This can't be undone.")) return;
    post("/api/reviews/delete", {}).then(function (d) {
      if (d._status === 200) { S.me.review = null; writeBox(); S.page = 0; load(); }
      else msg(d.message || "Couldn't delete - try again.", true);
    });
  }

  /* ---------------------------------------------------------------- start */
  function start() {
    document.querySelectorAll("[data-sort]").forEach(function (b) {
      b.addEventListener("click", function () {
        S.sort = b.getAttribute("data-sort"); S.page = 0;
        document.querySelectorAll("[data-sort]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        load();
      });
    });
    $("more").addEventListener("click", function () { S.page += 1; load(true); });
    $("lb-close").addEventListener("click", closePhoto);
    var d = $("lightbox");
    d.addEventListener("click", function (ev) { if (ev.target === d) closePhoto(); });
    d.addEventListener("close", function () { $("lb-img").removeAttribute("src"); if (S.opener) S.opener.focus(); S.opener = null; });
    load();
    api("/api/reviews/me").then(function (me) { if (me._status === 200) { S.me = me; writeBox(); } }).catch(function () {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
