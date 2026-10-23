/* Scout Sports model lab (admin). Gets an admin sports token with the esports admin token (sent as a header), then
   reads / queues through /api/sports/admin/lab/*. All numbers shown come from the experiment record. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var S = { admin: "", token: null, exp: 0, base: "", ov: null, timer: null };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function n(x, d) { return x == null ? "-" : (typeof x === "number" ? x.toFixed(d == null ? 3 : d) : esc(x)); }
  function status(m, bad) { $("lab-status").textContent = m || ""; $("lab-status").className = "lab-meta" + (bad ? " lab-bad" : ""); }

  function token() {
    if (S.token && Date.now() / 1000 < S.exp - 60) return Promise.resolve();
    return fetch("/api/sports/token", { headers: { "X-Admin-Token": S.admin }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("Admin token refused (" + r.status + ")"); return r.json(); })
      .then(function (t) { S.token = t.token; S.exp = t.exp; S.base = (t.api_base || "").replace(/\/$/, ""); });
  }
  function api(path, opts) {
    opts = opts || {};
    return token().then(function () {
      var h = { "Authorization": "Bearer " + S.token };
      if (opts.body) h["Content-Type"] = "application/json";
      return fetch(S.base + "/api/sports/admin/lab/" + path, { method: opts.method || "GET", headers: h,
        body: opts.body ? JSON.stringify(opts.body) : undefined, cache: "no-store" });
    });
  }
  function apiJson(path, opts) { return api(path, opts).then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); }); }

  function login() {
    S.admin = $("lab-token").value.trim();
    try { sessionStorage.setItem("lab-admin", S.admin); } catch (e) {}
    S.token = null;
    overview().then(function () { $("lab-auth").classList.add("lab-hidden"); $("lab-main").classList.remove("lab-hidden"); })
      .catch(function (e) { status(e.message, true); });
  }
  $("lab-login").addEventListener("click", login);

  function overview() {
    return apiJson("overview").then(function (o) {
      if (o._status !== 200) throw new Error(o.error || ("lab " + o._status));
      S.ov = o; status("");
      var src = Object.keys(o.sources).map(function (k) { return k + ": " + (o.sources[k] ? "available" : "not built yet"); }).join(" | ");
      var reg = o.registry || {};
      $("lab-sources").innerHTML = "Sources - " + esc(src) + "<br>Model registry - " + esc(reg.api || "none") +
        (reg.missing ? " <span class='lab-bad'>(" + esc(reg.missing) + ")</span>" : "") +
        (reg.versions || []).map(function (v) { return "<br><code>" + esc(v.name || v.model_version) + " = " + esc(v.value || v.role) + "</code>"; }).join("") +
        "<br>Feature registry " + esc(o.feature_registry) + " (no lab feature has a validated effect; experiments report their own test results).";
      fillForm(); list(o.experiments);
    });
  }

  function fillForm() {
    var f = $("lab-form"), o = S.ov;
    var lg = f.league.value, src = f.source.value;
    f.stat.innerHTML = (o.targets[lg] || []).map(function (s) { return "<option>" + esc(s) + "</option>"; }).join("");
    if (!f.algorithm.options.length) f.algorithm.innerHTML = Object.keys(o.algorithms).map(function (a) { return "<option value='" + esc(a) + "'>" + esc(o.algorithms[a]) + "</option>"; }).join("");
    $("lab-feats").innerHTML = o.features.filter(function (x) { return x.sports.indexOf(lg) >= 0 && x.sources.indexOf(src) >= 0; })
      .map(function (x) { return "<label><input type='checkbox' name='feat' value='" + esc(x.name) + "'><span><strong>" + esc(x.name) +
        "</strong> - " + esc(x.definition) + "<br><span class='lab-meta'>Missing: " + esc(x.missing) + "</span></span></label>"; }).join("");
    f.algorithm.dispatchEvent(new Event("change"));
  }
  $("lab-form").algorithm.addEventListener("change", function () {
    var a = $("lab-form").algorithm.value;
    $("lab-form").querySelectorAll("input[name=feat]").forEach(function (c) {
      c.disabled = a === "prior_mean" || (a === "shrunk_mean" && c.value !== "prior_n");
      if (c.disabled) c.checked = false;
    });
  });
  $("lab-form").league.addEventListener("change", fillForm);
  $("lab-form").source.addEventListener("change", fillForm);

  $("lab-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var spec = { league: f.league.value, stat: f.stat.value, source: f.source.value, algorithm: f.algorithm.value,
      train_start: f.train_start.value, train_end: f.train_end.value, tune_end: f.tune_end.value, test_end: f.test_end.value,
      seed: parseInt(f.seed.value, 10), min_prior: parseInt(f.min_prior.value, 10),
      features: Array.prototype.map.call(f.querySelectorAll("input[name=feat]:checked"), function (x) { return x.value; }) };
    apiJson("experiments", { method: "POST", body: spec }).then(function (r) {
      $("lab-form-msg").textContent = r._status === 202 ? "Queued " + r.id + " (" + r.state + ")" : (r.detail || r.error || "refused");
      overview();
    });
  });

  function list(xs) {
    var h = "<thead><tr><th>Experiment</th><th>State</th><th>League / stat</th><th>Algorithm</th><th>Seed</th>" +
      "<th class='num'>Test rows</th><th class='num'>Test MAE</th><th class='num'>vs baseline (95% CI)</th><th>Replay</th><th>Queued</th></tr></thead><tbody>";
    h += xs.map(function (x) {
      var t = x.test_vs_baseline;
      var vb = t ? n(t.delta_mae) + " [" + n(t.ci95[0]) + ", " + n(t.ci95[1]) + "]" : "-";
      var cls = t ? (t.ci95[1] < 0 ? "lab-good" : (t.ci95[0] > 0 ? "lab-bad" : "")) : "";
      return "<tr data-id='" + esc(x.id) + "' tabindex='0'><td><code>" + esc(x.id) + "</code></td><td>" + esc(x.state) + "</td><td>" +
        esc(x.league + " / " + x.stat) + "</td><td>" + esc(x.algorithm) + "</td><td>" + esc(x.seed) + "</td><td class='num'>" +
        n(x.test_rows, 0) + "</td><td class='num'>" + n(x.test_mae) + "</td><td class='num " + cls + "'>" + vb + "</td><td>" +
        (x.replay_verified == null ? "-" : (x.replay_verified ? "reproduced" : "<span class='lab-bad'>FAILED</span>")) +
        "</td><td>" + esc(x.created_at) + "</td></tr>";
    }).join("") + "</tbody>";
    $("lab-list").innerHTML = h;
    $("lab-list").querySelectorAll("tr[data-id]").forEach(function (tr) {
      var open = function () { detail(tr.dataset.id); };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", function (e) { if (e.key === "Enter") open(); });
    });
    clearTimeout(S.timer);
    if (xs.some(function (x) { return x.state === "queued" || x.state === "running"; })) S.timer = setTimeout(overview, 5000);
  }

  function reliability(bins) {
    var W = 220, P = 24, sz = W - 2 * P;
    var pts = bins.map(function (b) { return [P + b.mean_p * sz, W - P - b.observed * sz, b.n]; });
    var max = Math.max.apply(null, bins.map(function (b) { return b.n; }).concat([1]));
    return "<svg width='" + W + "' height='" + W + "' role='img' aria-label='Reliability: predicted vs observed over rate'>" +
      "<rect x='" + P + "' y='" + P + "' width='" + sz + "' height='" + sz + "' fill='none' stroke='currentColor' opacity='.2'/>" +
      "<line x1='" + P + "' y1='" + (W - P) + "' x2='" + (W - P) + "' y2='" + P + "' stroke='currentColor' opacity='.35' stroke-dasharray='3 3'/>" +
      pts.map(function (p) { return "<circle cx='" + p[0].toFixed(1) + "' cy='" + p[1].toFixed(1) + "' r='" + (2 + 5 * Math.sqrt(p[2] / max)).toFixed(1) + "' fill='#2456d6' opacity='.75'/>"; }).join("") +
      "<text x='" + P + "' y='" + (W - 6) + "'>predicted 0</text><text x='" + (W - P - 10) + "' y='" + (W - 6) + "'>1</text>" +
      "<text x='2' y='" + (P + 4) + "'>obs 1</text></svg>";
  }

  function detail(id) {
    apiJson("experiment?id=" + encodeURIComponent(id)).then(function (e) {
      var box = $("lab-detail"); box.classList.remove("lab-hidden");
      if (e._status !== 200) { box.textContent = e.error || "not found"; return; }
      var r = e.result || {}, m = r.metrics || {}, sp = e.spec;
      var h = "<h2>" + esc(e.id) + " - " + esc(e.state) + "</h2>";
      if (e.error) h += "<p class='lab-bad'>" + esc(e.error) + "</p>";
      h += "<p class='lab-meta'>" + esc(sp.league + " " + sp.stat) + " | source " + esc(sp.source) + " | " + esc(r.algorithm || sp.algorithm) +
        " | seed " + esc(sp.seed) + " | model_version <code>" + esc(r.model_version || "-") + "</code> | production: " + esc(String(r.production)) +
        " | data cutoff " + esc(sp.cutoff) + "</p>";
      h += "<h3>Dates and counts</h3><div class='lab-scroll'><table class='lab-table'><tr><th>Split</th><th>Window</th><th class='num'>Rows</th><th class='num'>Games</th><th class='num'>Players</th><th class='num'>Offered lines</th></tr>";
      var win = { train: [sp.train_start, sp.train_end], tune: [sp.train_end, sp.tune_end], test: [sp.tune_end, sp.test_end] };
      ["train", "tune", "test"].forEach(function (s) { var c = (r.counts || {})[s] || {};
        h += "<tr><td>" + s + "</td><td>" + esc(win[s][0] + " to " + win[s][1]) + "</td><td class='num'>" + n(c.rows, 0) + "</td><td class='num'>" + n(c.games, 0) +
          "</td><td class='num'>" + n(c.players, 0) + "</td><td class='num'>" + n(c.offered_lines, 0) + "</td></tr>"; });
      h += "</table></div>";
      h += "<h3>Selected features</h3><ul>" + (r.features || []).map(function (f) { return "<li><strong>" + esc(f.name) + "</strong> - " + esc(f.definition) + " <span class='lab-meta'>(" + esc(f.availability) + ")</span></li>"; }).join("") +
        "<li class='lab-meta'>Always computed: " + esc((r.always_computed || []).join(", ")) + "</li></ul>";
      if (m.splits) {
        h += "<h3>Projection error - candidate vs prior_mean baseline (same rows)</h3><div class='lab-scroll'><table class='lab-table'><tr><th>Split</th><th class='num'>Rows</th><th class='num'>MAE</th><th class='num'>Baseline MAE</th><th class='num'>RMSE</th><th class='num'>Bias</th><th class='num'>80% coverage</th></tr>";
        ["train", "tune", "test"].forEach(function (s) { var a = m.splits[s] || {}, b = (m.baseline.splits || {})[s] || {};
          h += "<tr><td>" + s + "</td><td class='num'>" + n(a.rows, 0) + "</td><td class='num'>" + n(a.mae) + "</td><td class='num'>" + n(b.mae) + "</td><td class='num'>" + n(a.rmse) +
            "</td><td class='num'>" + n(a.bias) + "</td><td class='num'>" + n(a.coverage_80) + "</td></tr>"; });
        h += "</table></div>";
        var t = m.test_vs_baseline;
        if (t) h += "<p>Test MAE minus baseline: <strong>" + n(t.delta_mae) + "</strong>, 95% CI [" + n(t.ci95[0]) + ", " + n(t.ci95[1]) + "] (" + esc(t.bootstrap) + ", " + n(t.games, 0) + " games). Negative = better than baseline.</p>";
        ["offered", "reference"].forEach(function (k) { var c = (m.calibration || {})[k];
          if (!c || !c.rows) { h += "<p class='lab-meta'>Calibration at " + k + " lines: no rows.</p>"; return; }
          h += "<h3>Calibration at " + (k === "offered" ? "offered lines" : "reference lines (not offered - floor(prior mean) + 0.5)") + "</h3>" +
            "<p class='lab-meta'>" + n(c.scored, 0) + " scored, " + n(c.pushes, 0) + " pushes left out | Brier " + n(c.brier, 4) + " | log loss " + n(c.log_loss, 4) + " | base over rate " + n(c.base_rate_over) + " | metric on " + esc(c.metric_on) + "</p>" +
            "<div class='lab-row'>" + reliability(c.bins) + "<div class='lab-scroll'><table class='lab-table'><tr><th>Band</th><th class='num'>n</th><th class='num'>Mean p</th><th class='num'>Observed</th></tr>" +
            c.bins.map(function (b) { return "<tr><td>" + esc(b.band) + "</td><td class='num'>" + b.n + "</td><td class='num'>" + n(b.mean_p) + "</td><td class='num'>" + n(b.observed) + "</td></tr>"; }).join("") + "</table></div></div>"; });
        h += "<p class='lab-meta'>" + esc(m.note) + "</p>";
      }
      h += "<h3>Reproducibility</h3><p class='lab-meta'>inputs <code>" + esc(e.inputs_hash) + "</code><br>outputs <code>" + esc(e.outputs_hash) + "</code> | numpy " + esc(r.numpy) + " | " + esc(r.lab_version) + "</p>" +
        "<div class='lab-scroll'><table class='lab-table'><tr><th>Replay</th><th>When</th><th>By</th><th>State</th><th>Match</th></tr>" +
        (e.replays || []).map(function (x) { return "<tr><td>" + x.rid + "</td><td>" + esc(x.at) + "</td><td>" + esc(x.requested_by) + "</td><td>" + esc(x.state) + "</td><td>" + (x.match == null ? "-" : (x.match ? "yes" : "<span class='lab-bad'>no</span>")) + "</td></tr>"; }).join("") + "</table></div>";
      h += "<div class='lab-row' style='margin-top:10px'><button class='lab-btn lab-ghost' data-x='csv'>Export CSV</button><button class='lab-btn lab-ghost' data-x='json'>Export JSON</button><button class='lab-btn' data-x='replay'>Replay from saved inputs</button></div>";
      if (r.model) h += "<details><summary>Fitted parameters</summary><pre>" + esc(JSON.stringify(r.model, null, 1)) + "</pre></details>";
      box.innerHTML = h;
      box.querySelectorAll("[data-x]").forEach(function (b) { b.addEventListener("click", function () {
        if (b.dataset.x === "replay") { apiJson("replay", { method: "POST", body: { id: id } }).then(function () { status("Replay queued."); setTimeout(function () { detail(id); }, 4000); }); return; }
        api("export?id=" + encodeURIComponent(id) + "&format=" + b.dataset.x).then(function (resp) { return resp.blob(); }).then(function (blob) {
          var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = id + "." + b.dataset.x; a.click(); URL.revokeObjectURL(a.href); });
      }); });
      box.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  }

  try { var t = sessionStorage.getItem("lab-admin"); if (t) { $("lab-token").value = t; login(); } } catch (e) {}
})();
