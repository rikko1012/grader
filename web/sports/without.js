/* "Games without" teammate filter - sheets agent, self-contained module for the Player workspace (uifix owns the page).
   Mount (one line, after the player's history is on screen):
       if (window.SportsWithout) window.SportsWithout.render(container, player, games);
     container  an empty element (the module fills it and owns everything inside it)
     player     the player snapshot (GET /api/sports/player: player_id, team.team_id, markets[{stat, stat_label, quotes}])
     games      that player's history games (GET /api/sports/history: games[{event_id, start_utc, team, opponent,
                participation, values}])
   Data: GET /api/sports/without?league=..&team=.. (sports/without.py gw-1: per final game, the rostered players who did
   NOT play - "dnp" = listed did-not-play / inactive, "absent" = on the team but no stat line), point-in-time per game.
   History only, never a prediction. Pure helpers (teammates, split) exported for node. */
(function (root) {
  "use strict";

  function played(g, stat) {
    return (g.participation === undefined || g.participation === null || g.participation === "played") &&
      g.values && g.values[stat] !== undefined && g.values[stat] !== null;
  }
  function onTeam(g, team) { return !g.team || !g.team.team_id || g.team.team_id === team; }
  // teammates who missed at least one of THIS player's games for this team, most missed first
  function teammates(idx, games, selfId) {
    var cnt = {};
    games.forEach(function (g) {
      var x = idx.games[g.event_id];
      if (!x || !onTeam(g, idx.team_id) || g.participation === "did_not_play") return;
      Object.keys(x.out).forEach(function (pid) { if (pid !== selfId) cnt[pid] = (cnt[pid] || 0) + 1; });
    });
    return Object.keys(cnt).map(function (pid) { return {pid: pid, name: (idx.players || {})[pid] || pid, missed: cnt[pid]}; })
      .sort(function (a, b) { return b.missed - a.missed || (a.name < b.name ? -1 : 1); });
  }
  function summary(vals, line) {
    if (!vals.length) return {n: 0, mean: null, median: null, o: 0, u: 0, p: 0};
    var s = vals.slice().sort(function (a, b) { return a - b; }), m = s.length;
    var o = 0, u = 0, p = 0;
    vals.forEach(function (v) { if (line === null || line === undefined || !isFinite(line)) return; if (v > line + 1e-9) o++; else if (v < line - 1e-9) u++; else p++; });
    return {n: m, mean: vals.reduce(function (a, b) { return a + b; }, 0) / m,
            median: m % 2 ? s[(m - 1) / 2] : (s[m / 2 - 1] + s[m / 2]) / 2, o: o, u: u, p: p};
  }
  // the player's games split by whether the teammate played (only games in the team index, player on that team)
  function split(idx, games, mate, stat, line) {
    var without = [], other = [];
    games.forEach(function (g) {
      var x = idx.games[g.event_id];
      if (!x || !onTeam(g, idx.team_id) || !played(g, stat)) return;
      var why = x.out[mate];
      (why ? without : other).push({g: g, v: g.values[stat], why: why || null});
    });
    return {without: without, other: other, w: summary(without.map(function (r) { return r.v; }), line),
            o: summary(other.map(function (r) { return r.v; }), line)};
  }

  var api = {teammates: teammates, split: split, summary: summary};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsWithout = api;

  var CACHE = {};
  function esc(s) { return root.ScoutShell.esc(s); }
  function n1(x) { return x === null || x === undefined ? "—" : (Math.round(x * 10) / 10).toFixed(1); }
  function lineTxt(x) { return x === null || x === undefined || !isFinite(x) ? "—" : String(Math.round(x * 10) / 10); }
  function day(iso) { try { return new Date(iso).toLocaleDateString([], {month: "short", day: "numeric", year: "2-digit"}); } catch (e) { return iso; } }
  function pctOf(a, n) { return n ? Math.round(a / n * 100) + "%" : "—"; }

  // opts (app.js): {stat, line, league} - the stat tab and line the player page shows; the panel starts there (bug #53)
  api.render = function (container, player, games, opts) {
    if (!container || !player) return;
    var lg = (player.player_id || "").slice(0, 3), team = player.team && player.team.team_id;
    games = games || [];
    if (!team || (lg !== "nba" && lg !== "nfl")) { container.innerHTML = ""; return; }
    container.classList.add("gwx");
    container.innerHTML = '<section class="sw-panel gwx-panel" aria-labelledby="gwx-h"><h2 id="gwx-h">Games without a teammate</h2><p class="sw-muted sw-small" role="status">Loading the team’s game index…</p></section>';
    var key = lg + "|" + team;
    var load = CACHE[key] || (CACHE[key] = root.ScoutSportsApp.api.get("/api/sports/without", {league: lg, team: team})
      .then(function (r) { return r.data; }, function (e) { delete CACHE[key]; throw e; }));
    load.then(function (idx) { draw(container, player, games, idx, opts); }, function (e) {
      container.querySelector(".gwx-panel").innerHTML = '<h2 id="gwx-h">Games without a teammate</h2><p class="sw-muted sw-small">' +
        (e && e.status === 404 ? "No final games of this team are in Scout’s box scores yet." : e && e.status === 503 ? "The team index is still being built - try again in a few minutes." : "The team index could not be loaded.") + "</p>";
    });
  };

  function draw(container, player, games, idx, opts) {
    var mates = teammates(idx, games, player.player_id);
    var stats = [], seen = {};
    (player.markets || []).forEach(function (m) { if (!seen[m.stat]) { seen[m.stat] = 1; stats.push([m.stat, m.stat_label || m.stat, m]); } });
    var panel = container.querySelector(".gwx-panel");
    if (!mates.length || !stats.length) {
      panel.innerHTML = '<h2 id="gwx-h">Games without a teammate</h2><p class="sw-muted sw-small">' +
        (stats.length ? "No teammate missed any of his games in Scout’s box scores and game logs." : "No current lines to compare.") + "</p>";
      return;
    }
    function curLine(m) { var q = (m.quotes || [])[0] || (m.alternates || [])[0]; return q ? q.line : null; }
    // bug #53: start from the page's stat (when this panel lists it) and its line (when finite); stats[0] only as the fallback
    var o0 = opts || {}, hit = stats.filter(function (s) { return s[0] === o0.stat; })[0], s0 = hit || stats[0];
    var l0 = o0.line === null || o0.line === undefined || o0.line === "" ? NaN : Number(o0.line);
    var st = {mate: mates[0].pid, stat: s0[0], line: hit && isFinite(l0) ? l0 : curLine(s0[2])};
    panel.innerHTML = '<h2 id="gwx-h">Games without a teammate</h2>' +
      '<p class="sw-sub">His games when the chosen teammate did not play (box score: did not play / inactive, or on the roster with no stat line). History, not a prediction.</p>' +
      '<div class="gwx-controls"><label class="shx-field"><span>Teammate</span><select id="gwx-mate">' + mates.map(function (m) {
        return '<option value="' + esc(m.pid) + '">' + esc(m.name) + " (missed " + m.missed + ")</option>"; }).join("") + "</select></label>" +
      '<label class="shx-field"><span>Stat</span><select id="gwx-stat">' + stats.map(function (s) { return '<option value="' + esc(s[0]) + '"' + (s[0] === st.stat ? " selected" : "") + ">" + esc(s[1]) + "</option>"; }).join("") + "</select></label>" +
      '<label class="shx-field"><span>Line</span><input id="gwx-line" type="number" step="0.5" min="0" inputmode="decimal" value="' + (st.line === null ? "" : st.line) + '"></label></div>' +
      '<div id="gwx-out" aria-live="polite"></div>';
    function paint() {
      var res = split(idx, games, st.mate, st.stat, st.line), w = res.w, o = res.o;
      var name = (mates.filter(function (m) { return m.pid === st.mate; })[0] || {}).name || "teammate";
      var h = '<div class="gwx-cmp"><div class="gwx-card"><div class="sw-eyebrow">Without ' + esc(name) + "</div><b>" + n1(w.mean) + "</b> avg · median " + n1(w.median) +
        '<div class="sw-small">' + (w.n ? "Over " + w.o + " · Under " + w.u + (w.p ? " · Push " + w.p : "") + " at " + lineTxt(st.line) + " · " + pctOf(w.o, w.n) + " over" : "") + '</div><div class="sw-faint sw-small">n = ' + w.n + (w.n === 1 ? " game" : " games") + "</div></div>" +
        '<div class="gwx-card"><div class="sw-eyebrow">All his other games</div><b>' + n1(o.mean) + "</b> avg · median " + n1(o.median) +
        '<div class="sw-small">' + (o.n ? "Over " + o.o + " · Under " + o.u + (o.p ? " · Push " + o.p : "") + " · " + pctOf(o.o, o.n) + " over" : "") + '</div><div class="sw-faint sw-small">n = ' + o.n + (o.n === 1 ? " game" : " games") + " (teammate played or was not yet on the team)</div></div></div>";
      if (w.n && w.n < 5) h += '<p class="sw-small sw-muted">Small sample: ' + w.n + " game" + (w.n > 1 ? "s" : "") + ".</p>";
      if (w.n) {
        h += '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Games without ' + esc(name) + '</caption><thead><tr><th scope="col">Date</th><th scope="col">Opponent</th><th scope="col" class="num">Value</th><th scope="col">vs ' + lineTxt(st.line) + '</th><th scope="col">Teammate</th></tr></thead><tbody>' +
          res.without.map(function (r) {
            var res_ = st.line === null || !isFinite(st.line) ? "—" : r.v > st.line ? "Over" : r.v < st.line ? "Under" : "Push";
            var opp = r.g.opponent ? (r.g.home === false ? "@ " : "vs ") + (r.g.opponent.abbr || r.g.opponent.name || "") : "";
            return "<tr><td>" + esc(day(r.g.start_utc)) + "</td><td>" + esc(opp) + '</td><td class="num">' + lineTxt(r.v) + "</td><td>" + res_ + '</td><td class="sw-small">' + (r.why === "dnp" ? "Did not play / inactive" : "No stat line (on roster)") + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      } else h += '<p class="sw-small sw-muted">He has no ' + esc(st.stat) + " value in a game this teammate missed.</p>";
      container.querySelector("#gwx-out").innerHTML = h;
    }
    container.querySelector("#gwx-mate").addEventListener("change", function (e) { st.mate = e.target.value; paint(); });
    container.querySelector("#gwx-stat").addEventListener("change", function (e) {
      st.stat = e.target.value;
      var m = stats.filter(function (s) { return s[0] === st.stat; })[0];
      st.line = m ? curLine(m[2]) : null;
      container.querySelector("#gwx-line").value = st.line === null ? "" : st.line;
      paint();
    });
    container.querySelector("#gwx-line").addEventListener("input", function (e) { var v = parseFloat(e.target.value); st.line = isFinite(v) ? v : null; paint(); });
    paint();
  }
})(this);
