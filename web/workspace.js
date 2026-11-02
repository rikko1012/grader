/* Research workspace preview. Plain JS over window.WORKSPACE_DATA (sample dr-1 records); no server calls.
   Recheck, Track and the "loading" delays are local stand-ins for the routed checks listed in the research map.
   Slip, tracked picks and saved views live in localStorage for this prototype only. */
(function () {
  "use strict";
  const D = window.WORKSPACE_DATA;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const act = (name, id) => `data-act="${name}"${id != null ? ` data-id="${esc(id)}"` : ""} data-fk="${esc(name + ":" + (id == null ? "" : id))}"`;
  const pct = (p) => (p == null ? "--" : (p * 100).toFixed(1) + "%");     /* one decimal, from the unrounded chance */
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const ICON = {
    plus: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    check: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7"/></svg>',
    x: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    down: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
  };
  const TABS = ["overview", "history", "books", "context"];
  const VIEWS = ["research", "slips", "picks", "results"];
  const DEFAULT_F = { day: "today", book: "all", market: "all", period: "all", side: "all", tier: "all", gapsOnly: false, hideOff: false, sort: "start" };

  /* ---- storage: every read and write survives a blocked or empty localStorage ---- */
  const KEY = { slip: "ws:slip:v1", receipts: "ws:receipts:v1", views: "ws:views:v1" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* page still works for this visit */ } },
    drop(k) { try { localStorage.removeItem(k); } catch (e) { /* nothing kept */ } }
  };

  /* ---- time ---- */
  const fmtUTC = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  const stamp = (iso) => fmtUTC.format(new Date(iso)) + " UTC";
  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  function ago(iso) {
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    if (m < 1440) return Math.round(m / 60) + " h ago";
    return plural(Math.round(m / 1440), "day") + " ago";
  }
  function until(iso) {
    const m = Math.round((new Date(iso) - Date.now()) / 60000);
    if (m <= 0) return "started";
    if (m < 60) return `in ${m}m`;
    if (m < 1440) return clock(iso);
    return "tomorrow " + clock(iso);
  }

  /* ---- rows: one board row per exact prop and line. Offers at another line stay a separate row. ---- */
  const rows = new Map();
  D.records.forEach((r) => {
    if (!rows.has(r.key)) rows.set(r.key, { id: r.key, offers: [], siblings: [] });
    rows.get(r.key).offers.push(r);
  });
  const propKey = (r) => [r.sport, r.event.id, r.player.key, r.market.stat, r.market.period].join(".");
  const all = [...rows.values()];
  all.forEach((row) => { row.lead = row.offers[0]; });
  all.forEach((row) => { row.siblings = all.filter((o) => o !== row && propKey(o.lead) === propKey(row.lead)); });
  const offerById = new Map(D.records.map((r) => [r.offer.offer_id, r]));

  /* ---- state ---- */
  const S = {
    view: "research", chip: "all", q: "", f: Object.assign({}, DEFAULT_F), activeView: "p-today", booting: true,
    forced: "ok", cleared: {}, loading: {}, feeds: JSON.parse(JSON.stringify(D.feeds)), panel: null, opener: null, histN: 10,
    picksFilter: "all", openPick: null, group: "sport", resSport: "all",
    slip: store.get(KEY.slip, null), local: store.get(KEY.receipts, []), views: store.get(KEY.views, []), rechecking: false
  };

  /* ---- panel states ---- */
  function pState(panel) {
    if (S.loading[panel]) return "loading";
    if (S.forced !== "ok" && !S.cleared[panel]) return S.forced;
    return "ok";
  }
  const PANEL_AT = { board: D.board_at, research: D.board_at, slip: D.board_at, picks: D.picks_at, results: D.results_at };
  const PANEL_NAME = { board: "Board", research: "Research", slip: "Recheck", picks: "My picks", results: "Results" };
  const retryBtn = (panel) => `<button class="btn" type="button" ${act("retry", panel)}>Retry</button>`;
  function stateBlock(panel) {
    const k = pState(panel), name = PANEL_NAME[panel], t = PANEL_AT[panel];
    if (k === "loading") return `<div aria-busy="true"><p class="sr">${name} loading</p>${Array.from({ length: 7 }, () => '<div class="skel"><i></i><i></i></div>').join("")}</div>`;
    if (k === "unavailable") return `<div class="state"><h3>${name} unavailable</h3><p>The feed did not answer. Nothing below is shown until it does.</p><p class="t">Last good copy ${stamp(t)}</p>${retryBtn(panel)}</div>`;
    return "";
  }
  function stateStrip(panel, extra) {
    const k = pState(panel), name = PANEL_NAME[panel], t = PANEL_AT[panel], ins = extra ? " inset" : "";
    if (k === "stale") return `<div class="strip${ins}"><b>${name} last updated ${ago(t)}</b><span class="t">${stamp(t)}</span>${retryBtn(panel)}</div>`;
    if (k === "withdrawn") return `<div class="strip${ins}"><b>Offers here were withdrawn</b><span class="t">${stamp(t)}</span>${retryBtn(panel)}</div>`;
    return "";
  }
  function blocked(panel) { const k = pState(panel); return k === "loading" || k === "unavailable"; }
  function retry(panel) {
    S.loading[panel] = true; S.cleared[panel] = true; renderPanelFor(panel);
    setTimeout(() => { S.loading[panel] = false; renderPanelFor(panel); say(`${PANEL_NAME[panel]} updated`); }, 700);
  }
  function renderPanelFor(p) {
    if (p === "board") renderBoard();
    if (p === "research") renderPanel();
    if (p === "slip") renderSlips();
    if (p === "picks") renderPicks();
    if (p === "results") renderResults();
  }

  /* ---- offer and row helpers ---- */
  const started = (r) => r.event.status === "started" || new Date(r.event.lock_utc) <= Date.now();
  const withdrawn = (r) => r.state.kind === "withdrawn";
  const feedStale = (r) => S.feeds[r.sport].kind === "stale";
  const liveOffer = (r) => !withdrawn(r) && !started(r);
  const slipOffer = (r) => D.platforms[r.offer.platform].slip;
  function lead(row) {
    const book = S.f.book;
    const pool = book === "all" ? row.offers : row.offers.filter((o) => o.offer.platform === book);
    return pool.find((o) => liveOffer(o) && slipOffer(o)) || pool.find(liveOffer) || pool[0] || row.lead;
  }
  const offerOf = (row, platform) => row.offers.find((o) => o.offer.platform === platform) || lead(row);
  function rowFlag(row) {
    if (row.offers.every(withdrawn)) return "withdrawn";
    if (started(row.lead)) return "started";
    return "";
  }
  function verdict(rec, side) {
    const s = rec.scout, sd = side || s.side;
    if (!sd) return { html: '<span class="verdict">No lean</span>', text: "No lean", side: "" };
    if (s.side && sd !== s.side) return { html: `<span class="verdict">Scout side is ${cap(s.side)}</span>`, text: `Scout side is ${cap(s.side)}`, side: "" };
    if (s.hold) return { html: '<span class="verdict g-est">Research estimate</span>', text: `Research estimate ${cap(sd)}`, side: cap(sd) };
    if (s.grade && sd === s.side) return { html: `<span class="verdict"><span class="g-letter">${esc(s.grade)}</span> ${cap(sd)}</span>`, text: `${s.grade} ${cap(sd)}`, side: "" };
    return { html: `<span class="verdict">Lean ${cap(sd)}</span>`, text: `Lean ${cap(sd)}`, side: "" };
  }
  const chanceOf = (rec, side) => (side === "under" ? rec.scout.p_under : side === "over" ? rec.scout.p_over : null);
  const payoutOf = (rec, side) => rec.offer[side].payout;
  const payoutText = (v) => (v == null ? "Payout unknown" : `Payout ${v.toFixed(2)}x`);

  /* ---- leg identity: sport, event, player, market, period, platform, side, line, offer revision ---- */
  const legId = (r, side) => [r.sport, r.event.id, r.player.id || r.player.name, r.market.stat, r.market.period, r.offer.platform, side, r.offer.line, r.offer.revision].join("|");
  function makeLeg(r, side, addedAt, oldLine, oldP) {
    const line = oldLine == null ? r.offer.line : oldLine;      /* oldLine, oldP: what the leg held before its line moved */
    return {
      id: legId(r, side) + (oldLine == null ? "" : "|old"), key: r.key, sport: r.sport, event_id: r.event.id, event_label: r.event.label, player: r.player.name, player_key: r.player.key, team: r.player.team,
      stat: r.market.stat, stat_label: r.market.stat_label, period: r.market.period, period_label: r.market.period_label, line_type: r.market.line_type,
      platform: r.offer.platform, platform_label: r.offer.platform_label, offer_id: r.offer.offer_id,
      revision: oldLine == null ? r.offer.revision : r.offer.revision.replace(/:r\d+:/, ":r2:"), revision_kind: r.offer.revision_kind,
      line, side, payout: payoutOf(r, side), shown_p: oldP == null ? chanceOf(r, side) : oldP, shown_label: verdict(r, side).text, model_version: r.scout.model_version,
      forecast_id: r.scout.forecast_id, forecast_at: r.scout.forecast_at, added_at: addedAt || new Date().toISOString()
    };
  }
  function seedSlip() {
    const legs = D.seed_slip.map((s) => {
      const rec = rows.get(s.key).offers.find((o) => o.offer.platform === s.platform);
      return makeLeg(rec, s.side, new Date(D.now + s.added * 60000).toISOString(), s.old_line, s.old_p);
    });
    return { legs, check: null };
  }
  if (!S.slip) S.slip = seedSlip();
  function saveSlip() { store.set(KEY.slip, S.slip); }
  const legsFor = (rec) => S.slip.legs.filter((l) => l.key === rec.key && l.platform === rec.offer.platform && l.line === rec.offer.line);

  function addRefusal(rec, side) {
    const label = rec.offer.platform_label;
    if (pState("research") === "withdrawn" || withdrawn(rec)) return `Withdrawn by ${label} at ${stamp(rec.state.board_at)}.`;
    if (started(rec)) return `Game started at ${stamp(rec.event.start_utc)}.`;
    if (!slipOffer(rec)) return `${label} is shown for comparison. Slips cover PrizePicks and Underdog.`;
    if (!rec.offer[side].offered) return `${cap(side)} is not offered on ${label}.`;
    const same = S.slip.legs.find((l) => l.player_key === rec.player.key && l.stat === rec.market.stat && l.period === rec.market.period && l.platform === rec.offer.platform && l.event_id === rec.event.id);
    if (same) {
      if (same.side === side && same.line === rec.offer.line) return `${cap(side)} ${rec.offer.line} is already on your slip.`;
      return `Conflict: ${cap(same.side)} ${same.line} on this prop is already on your ${label} entry.`;
    }
    return null;
  }
  function addLeg(rec, side) {
    const why = addRefusal(rec, side);
    if (why) { say(why); return false; }
    S.slip.legs.push(makeLeg(rec, side));
    S.slip.check = null;
    saveSlip(); renderSlips(); renderBoard();
    say(`Added ${rec.player.name} ${cap(side)} ${rec.offer.line}`);
    return true;
  }
  function removeLeg(id) {
    S.slip.legs = S.slip.legs.filter((l) => l.id !== id);
    S.slip.check = null;
    saveSlip(); renderSlips(); renderBoard();
  }

  /* ---- entries: one tray, one entry per platform and sport ---- */
  function entries() {
    const m = new Map();
    S.slip.legs.forEach((l) => {
      const k = l.platform + "|" + l.sport;
      if (!m.has(k)) m.set(k, { key: k, platform: l.platform, label: D.platforms[l.platform].label, sport: l.sport, legs: [] });
      m.get(k).legs.push(l);
    });
    return [...m.values()];
  }
  const sig = () => S.slip.legs.map((l) => l.id).join(";");

  /* ---- recheck (local stand-in for the per-sport server checks) ---- */
  const CHECKS = ["Game not started", "Offer available and unchanged", "Forecast matches event, league and period", "Platform accepts combination and leg count", "No duplicates or conflicts"];
  function legProblems(l) {
    const out = [], cur = offerById.get(l.offer_id);
    if (!cur) { out.push({ c: 1, text: "This offer is no longer on the board." }); return out; }
    if (started(cur)) out.push({ c: 0, text: `Game started at ${stamp(cur.event.start_utc)}.` });
    if (withdrawn(cur)) out.push({ c: 1, text: `Withdrawn by ${l.platform_label} at ${stamp(cur.state.board_at)}.` });
    else if (cur.offer.revision !== l.revision || cur.offer.line !== l.line) {
      out.push({ c: 1, text: `Line moved from ${l.line} to ${cur.offer.line}${cur.offer.moved_at ? ` at ${stamp(cur.offer.moved_at)}` : ""}.`, swap: true });
    }
    if (!cur.offer[l.side].offered) out.push({ c: 1, text: `${cap(l.side)} is no longer offered on ${l.platform_label}.` });
    if (feedStale(cur)) out.push({ c: 1, text: `${D.sports[l.sport]} lines last updated ${ago(S.feeds[l.sport].at)}.`, feed: l.sport });
    const sc = D.forecast_scope[l.offer_id];
    if (!sc || sc.event_id !== l.event_id || sc.sport !== l.sport || sc.period !== l.period) {
      out.push({ c: 2, text: `This forecast was made for ${sc ? esc(periodName(sc.period)) : "another event"}, and this offer is ${l.period_label}.` });
    }
    S.slip.legs.forEach((o) => {
      if (o === l || o.platform !== l.platform || o.player_key !== l.player_key || o.stat !== l.stat || o.period !== l.period) return;
      out.push({ c: 4, text: o.id === l.id ? "Duplicate of another leg." : `Conflicts with ${cap(o.side)} ${o.line} on the same prop.` });
    });
    return out;
  }
  function periodName(p) { const r = D.records.find((x) => x.market.period === p); return r ? r.market.period_label : p; }
  function entryProblems(e) {
    const rule = D.platforms[e.platform], max = e.sport === "cs2" ? Math.min(rule.max, 6) : rule.max, out = [], n = e.legs.length;
    if (n < rule.min) out.push({ c: 3, text: `${e.label} entries need at least ${rule.min} picks. This one has ${n}.` });
    if (n > max) out.push({ c: 3, text: `${e.label} ${D.sports[e.sport]} entries take ${max} picks at most. This one has ${n}.` });
    if (n >= rule.min && new Set(e.legs.map((l) => l.team)).size < 2) out.push({ c: 3, text: "Entries need players from at least 2 different teams." });
    return out;
  }
  function runRecheck() {
    const res = { at: new Date().toISOString(), sig: sig(), legs: {}, entries: {}, fails: CHECKS.map(() => 0), unknown: 0 };
    S.slip.legs.forEach((l) => {
      const p = legProblems(l);
      res.legs[l.id] = { ok: !p.length, problems: p };
      p.forEach((x) => res.fails[x.c]++);
      if (l.payout == null) res.unknown++;
    });
    entries().forEach((e) => {
      const p = entryProblems(e);
      p.forEach((x) => res.fails[x.c]++);
      res.entries[e.key] = { ok: !p.length && e.legs.every((l) => res.legs[l.id].ok), problems: p };
    });
    return res;
  }
  function slipPhase() {
    const c = S.slip.check;
    if (!S.slip.legs.length) return "empty";
    if (S.rechecking || pState("slip") === "loading") return "running";
    if (pState("slip") === "unavailable") return "down";
    if (pState("slip") === "stale") return "aged";
    if (!c || c.sig !== sig()) return "draft";
    return (Date.now() - new Date(c.at)) / 60000 > D.recheck_ttl_min ? "aged" : "fresh";
  }
  function recheck() {
    if (S.rechecking) return;
    S.rechecking = true; renderSlips();
    setTimeout(() => {
      S.rechecking = false;
      S.slip.check = runRecheck();
      saveSlip(); renderSlips();
      const c = S.slip.check, ready = Object.values(c.entries).filter((e) => e.ok).length, n = Object.keys(c.entries).length;
      say(`Recheck done: ${ready} of ${n} ${n === 1 ? "entry" : "entries"} ready`);
    }, 650);
  }
  function swapLeg(id) {
    const l = S.slip.legs.find((x) => x.id === id), cur = l && offerById.get(l.offer_id);
    if (!cur) return;
    S.slip.legs = S.slip.legs.map((x) => (x.id === id ? makeLeg(cur, l.side) : x));
    S.slip.check = null;
    saveSlip(); renderSlips(); renderBoard();
    say(`Now ${cap(l.side)} ${cur.offer.line} at the current revision. Recheck to continue.`);
  }

  /* ---- copy and track ---- */
  function entryText(e) {
    const c = S.slip.check, lines = [`${e.label} entry, ${D.sports[e.sport]}`];
    e.legs.forEach((l, i) => lines.push(`${i + 1}. ${l.player} ${cap(l.side)} ${l.line} ${l.stat_label}, ${l.period_label} (${l.event_label})`));
    lines.push(`Rechecked ${stamp(c.at)}`);
    return lines.join("\n");
  }
  function prefillNote(e) {
    if (D.platforms[e.platform].prefill === "verified") return "PrizePicks prefill is verified on live offer ids. Sample ids have none, so Copy gives text.";
    return "Underdog opens per line, with no slip prefill. Copy gives text.";
  }
  async function copyEntry(key) {
    const e = entries().find((x) => x.key === key);
    if (!e) return;
    const text = entryText(e);
    try { await navigator.clipboard.writeText(text); } catch (err) {
      const t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch (e2) { /* the toast below reports it */ }
      t.remove();
    }
    say(`Copied ${e.label} entry as text`);
  }
  function trackEntry(key) {
    const e = entries().find((x) => x.key === key);
    if (!e) return;
    const now = new Date().toISOString(), slipId = "sl-" + Date.now().toString(36);
    e.legs.forEach((l, i) => {
      S.local.push({
        id: `rc-${Date.now().toString(36)}-${i}`, slip_id: slipId, sport: l.sport, player: l.player, matchup: l.event_label, stat_label: l.stat_label, period_label: l.period_label,
        platform: l.platform, platform_label: l.platform_label, side: l.side, line: l.line, line_type: l.line_type, offer_revision: l.revision, revision_kind: l.revision_kind,
        model_version: l.model_version, forecast_id: l.forecast_id, forecast_at: l.forecast_at, displayed_p: l.shown_p, tracked_at: now, final: null,
        timeline: [{ at: now, kind: "tracked", text: `Tracked ${cap(l.side)} ${l.line} at ${pct(l.shown_p)}` }]
      });
    });
    store.set(KEY.receipts, S.local);
    const n = e.legs.length;
    S.slip.legs = S.slip.legs.filter((l) => !e.legs.includes(l));
    if (S.slip.check) { delete S.slip.check.entries[key]; e.legs.forEach((l) => delete S.slip.check.legs[l.id]); S.slip.check.sig = sig(); }
    saveSlip(); renderSlips(); renderBoard(); renderPicks(); renderResults(); renderNav();
    say(`${plural(n, "pick")} tracked. Receipts are in My picks.`);
  }
  const allReceipts = () => [...S.local, ...D.receipts].sort((a, b) => new Date(b.tracked_at) - new Date(a.tracked_at));
  function pickStatus(r) {
    for (let i = r.timeline.length - 1; i >= 0; i--) if (r.timeline[i].result) return r.timeline[i].result;
    return "pending";
  }
  const STATUS = { hit: "Hit", miss: "Miss", push: "Push", void: "Withdrawn", pending: "Pending" };

  /* ---- filters ---- */
  function matches(row, ignoreChip) {
    const r = row.lead, f = S.f, q = S.q.trim().toLowerCase();
    if (!ignoreChip && S.chip !== "all" && r.sport !== S.chip) return false;
    if (q && ![r.player.name, r.player.team, r.player.opponent, r.event.label, r.event.competition, D.sports[r.sport]].join(" ").toLowerCase().includes(q)) return false;
    if (f.day !== "all" && D.events[r.event.id].day !== f.day) return false;
    if (f.book !== "all" && !row.offers.some((o) => o.offer.platform === f.book)) return false;
    if (f.market !== "all" && r.market.stat_label !== f.market) return false;
    if (f.period !== "all" && r.market.period !== f.period) return false;
    if (f.side !== "all" && r.scout.side !== f.side) return false;
    if (f.tier === "graded" && !r.scout.grade) return false;
    if (f.tier === "lean" && (r.scout.grade || r.scout.hold)) return false;
    if (f.tier === "estimate" && !r.scout.hold) return false;
    if (f.gapsOnly && !row.siblings.length) return false;
    if (f.hideOff && rowFlag(row)) return false;
    return true;
  }
  function visibleRows() {
    const list = all.filter((r) => matches(r));
    const off = (r) => (rowFlag(r) ? 1 : 0);
    const by = {
      start: (a, b) => new Date(a.lead.event.start_utc) - new Date(b.lead.event.start_utc),
      chance: (a, b) => (lead(b).scout.p_side || 0) - (lead(a).scout.p_side || 0),
      player: (a, b) => a.lead.player.name.localeCompare(b.lead.player.name)
    }[S.f.sort];
    return list.sort((a, b) => off(a) - off(b) || by(a, b));
  }
  function setF(k, v) { S.f[k] = v; S.activeView = null; renderBoardAll(); }
  function resetFilters() { S.f = Object.assign({}, DEFAULT_F, { day: "all" }); S.q = ""; S.chip = "all"; $("search").value = ""; S.activeView = null; renderBoardAll(); }

  /* ---- render: toolbar ---- */
  function options(el, list, cur) {
    el.innerHTML = list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    el.value = cur;
  }
  const dayOpts = [["today", "Today"], ["tomorrow", "Tomorrow"], ["all", "All days"]];
  const bookOpts = () => [["all", "All books"], ...Object.entries(D.platforms).map(([k, v]) => [k, v.label])];
  const marketOpts = () => {
    const set = new Set(all.filter((r) => S.chip === "all" || r.lead.sport === S.chip).map((r) => r.lead.market.stat_label));
    return [["all", "All markets"], ...[...set].sort().map((m) => [m, m])];
  };
  function renderChips() {
    const counts = { all: 0 };
    all.forEach((r) => { if (matches(r, true)) { counts.all++; counts[r.lead.sport] = (counts[r.lead.sport] || 0) + 1; } });
    $("chips").innerHTML = [["all", "All"], ...Object.entries(D.sports)].map(([k, v]) => {
      const dot = k !== "all" && S.feeds[k].kind !== "ok" ? '<span class="dot" title="Feed is behind"></span>' : "";
      return `<button class="chip" type="button" aria-pressed="${S.chip === k}" ${act("chip", k)}>${v}${dot}<span class="n">${counts[k] || 0}</span></button>`;
    }).join("");
    if (S.f.market !== "all" && !marketOpts().some(([v]) => v === S.f.market)) S.f.market = "all";
    options($("f-day"), dayOpts, S.f.day); options($("f-book"), bookOpts(), S.f.book); options($("f-market"), marketOpts(), S.f.market);
    const phone = window.matchMedia("(max-width:760px)").matches;
    const keys = phone ? ["day", "book", "market", "period", "side", "tier", "gapsOnly", "hideOff", "sort"] : ["period", "side", "tier", "gapsOnly", "hideOff", "sort"];
    const n = keys.filter((k) => S.f[k] !== DEFAULT_F[k]).length;
    $("more-label").textContent = (phone ? "Filters" : "More filters") + (n ? ` · ${n}` : "");
  }
  function renderFeedStrips() {
    const out = Object.keys(D.sports).filter((k) => (S.chip === "all" || S.chip === k)).map((k) => {
      const f = S.feeds[k];
      if (f.kind === "stale") return `<div class="strip"><b>${D.sports[k]} lines last updated ${ago(f.at)}</b><span class="t">${stamp(f.at)}</span><button class="btn" type="button" ${act("retry-feed", k)}>Retry</button></div>`;
      if (f.kind === "loading") return `<div class="strip" aria-busy="true"><b>${D.sports[k]} lines updating</b></div>`;
      return "";
    }).join("");
    $("feed-strips").innerHTML = (blocked("board") ? "" : stateStrip("board")) + (blocked("board") ? "" : out);
  }

  /* ---- render: board ---- */
  function histCell(rec) {
    const h = rec.history, side = rec.scout.side || "over";
    if (!h.l10) return '<div class="hist"><span class="hist-n" aria-label="No last 10 in this feed">No L10</span></div>';
    const games = h.recent.results.slice(0, 10).reverse();
    const pips = games.map((g) => {
      const push = g.value === rec.offer.line, over = g.value > rec.offer.line;
      return `<i class="pip${push ? " push" : (side === "over" ? over : !over) ? " hit" : ""}"></i>`;
    }).join("");
    return `<div class="hist"><span class="pips" aria-hidden="true">${pips}</span><span class="hist-n" aria-label="Last 10: ${h.l10.over} over, ${h.l10.under} under, ${h.l10.push} push">${h.l10.over}-${h.l10.under}-${h.l10.push}</span></div>`;
  }
  function bookChips(row) {
    const chips = row.offers.map((o) => `<span class="bk${withdrawn(o) ? " off" : ""}">${D.platforms[o.offer.platform].short}</span>`);
    row.siblings.forEach((s) => chips.push(`<span class="bk other" title="Different line"><span class="who">${s.offers.map((o) => D.platforms[o.offer.platform].short).join(" ")} </span>${s.lead.offer.line}</span>`));
    return `<div class="books">${chips.join("")}</div>`;
  }
  function renderRow(row) {
    const rec = lead(row), flag = rowFlag(row), forced = pState("board") === "withdrawn", s = rec.scout, side = s.side;
    const v = verdict(rec), off = flag || forced;
    const stale = !flag && (feedStale(rec) || pState("board") === "stale");
    const ev = D.events[rec.event.id], matchup = `${ev.teams[0]} ${rec.sport === "nba" || rec.sport === "nfl" ? "at" : "vs"} ${ev.teams[1]}`;
    const when = flag === "started" ? "Started" : until(rec.event.start_utc);
    const chanceHtml = `<span class="sc-num">${v.side ? `<span class="sc-m">${v.side} </span>` : ""}<b class="pct">${side ? pct(s.p_side) : "--"}</b></span>`;
    const marketLabel = esc(rec.market.stat_label) + (rec.market.period === "full_game" ? "" : ` · ${esc(rec.market.period_label)}`);
    const flagNote = flag === "withdrawn" || forced ? `Withdrawn ${clock(forced ? PANEL_AT.board : rec.state.board_at)}` : "Game started";
    const scout = off ? `<span class="off-note">${esc(flagNote)}</span>`
      : `<div class="sc-top">${chanceHtml}${v.html}</div><span class="sc-sub">${v.side ? `<span class="sc-side">${v.side}</span><span class="sc-rest"> · </span>` : ""}<span class="sc-rest">Proj ${s.projection}${s.units ? ` · ${s.units}U` : ""}</span></span>`;
    const inSlip = side && legsFor(rec).length > 0;
    const addSide = side || "over", label = `${rec.player.name} ${cap(addSide)} ${rec.offer.line} on ${rec.offer.platform_label}`;
    const addBtn = off ? `<button class="add" type="button" disabled aria-label="${esc(flagNote)}">${ICON.plus}</button>`
      : !side ? `<button class="add" type="button" ${act("open", row.id)} aria-label="Choose a side for ${esc(rec.player.name)}">${ICON.plus}</button>`
        : `<button class="add" type="button" aria-pressed="${inSlip}" ${act("row-add", row.id)} aria-label="${inSlip ? "Remove" : "Add"} ${esc(label)} ${inSlip ? "from" : "to"} slip">${inSlip ? ICON.check : ICON.plus}</button>`;
    return `<li class="row${off ? " is-off" : ""}${flag === "withdrawn" || forced ? " is-withdrawn" : ""}${stale ? " is-stale" : ""}" data-row="${esc(row.id)}">
      <button class="row-open" type="button" ${act("open", row.id)} aria-label="Research ${esc(rec.player.name)}, ${esc(rec.market.stat_label)} ${esc(rec.market.period_label)}, line ${rec.offer.line}, Scout ${esc(off ? flagNote : v.text + " " + pct(s.p_side))}"></button>
      <div class="c-player"><span class="pl-name">${esc(rec.player.name)}</span><span class="pl-sub"><span class="s-tag">${D.sports[rec.sport]}</span>${matchup} · ${when}</span></div>
      <div class="c-line"><b>${rec.offer.line}</b><span>${marketLabel}</span></div>
      <div class="c-scout">${scout}</div>
      <div class="c-hist">${histCell(rec)}</div>
      <div class="c-books">${bookChips(row)}</div>
      <div class="c-add">${addBtn}</div></li>`;
  }
  function renderBoard() {
    const box = $("board-list");
    renderFeedStrips();
    if (S.booting) { box.innerHTML = `<div aria-busy="true"><p class="sr">Board loading</p>${Array.from({ length: 10 }, () => '<div class="skel"><i></i><i></i></div>').join("")}</div>`; return; }
    if (blocked("board")) { box.innerHTML = stateBlock("board"); $("board-meta").textContent = ""; return; }
    const list = visibleRows();
    $("board-meta").textContent = `${plural(list.length, "prop")} · Updated ${clock(D.board_at)}`;
    box.innerHTML = list.length ? `<ol aria-label="Props">${list.map(renderRow).join("")}</ol>`
      : `<div class="empty"><p>No props match these filters.</p><button class="btn" type="button" ${act("reset-filters")}>Show all props</button></div>`;
  }
  function renderBoardAll() { renderChips(); renderBoard(); renderViews(); }

  /* ---- render: saved views ---- */
  function viewItem(v, own) {
    return `<li><button class="view-link" type="button" aria-pressed="${S.activeView === v.id}" ${act("view", v.id)}>${esc(v.name)}</button>${own ? `<button class="view-del" type="button" aria-label="Delete view ${esc(v.name)}" ${act("view-del", v.id)}>${ICON.x}</button>` : ""}</li>`;
  }
  function renderViews() {
    $("views-side").innerHTML = D.presets.map((v) => viewItem(v, false)).join("") + S.views.map((v) => viewItem(v, true)).join("");
    const box = $("views-drawer");
    if (box) box.innerHTML = $("views-side").innerHTML;
  }
  function applyView(id) {
    const v = [...D.presets, ...S.views].find((x) => x.id === id);
    if (!v) return;
    S.f = Object.assign({}, DEFAULT_F, v.state.f); S.chip = v.state.chip; S.q = v.state.q || ""; $("search").value = S.q; S.activeView = id;
    if (S.view !== "research") location.hash = "#/research";
    renderBoardAll();
    if ($("filters-dialog").open) renderFilters();
    say(`View: ${v.name}`);
  }
  function renderFilters() {
    const sel = (key, label, list) => `<label class="field"><span>${label}</span><select data-f="${key}">${list.map(([v, l]) => `<option value="${esc(v)}"${S.f[key] === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const tog = (key, label) => `<label class="check"><input type="checkbox" data-f="${key}"${S.f[key] ? " checked" : ""}><span>${label}</span></label>`;
    $("filters-body").innerHTML = sel("day", "Day", dayOpts) + sel("book", "Book", bookOpts()) + sel("market", "Market", marketOpts()) +
      sel("period", "Period", [["all", "All periods"], ["map1", "Map 1"], ["maps12", "Maps 1-2"], ["full_game", "Full game"]]) +
      sel("side", "Scout side", [["all", "Over and Under"], ["over", "Over"], ["under", "Under"]]) +
      sel("tier", "Scout read", [["all", "All"], ["graded", "Graded"], ["lean", "Lean Over/Under"], ["estimate", "Research estimate"]]) +
      sel("sort", "Sort", [["start", "Start time"], ["chance", "Scout chance"], ["player", "Player"]]) +
      tog("gapsOnly", "Books differ on the line") + tog("hideOff", "Hide started and withdrawn") +
      `<button class="btn btn-quiet" type="button" ${act("reset-filters")}>Reset filters</button>
       <h3 class="eyebrow">Saved views</h3><ul class="views-list" id="views-drawer"></ul>
       <form class="save-row" id="save-view"><label class="field"><span class="sr">View name</span><input type="text" id="view-name" maxlength="28" placeholder="Name this view"></label><button class="btn" type="submit">Save</button></form>`;
    renderViews();
  }

  /* ---- render: slip ---- */
  function statusLine(phase) {
    const c = S.slip.check, oldest = S.slip.legs.reduce((m, l) => (l.added_at < m ? l.added_at : m), new Date().toISOString());
    if (phase === "empty") return "";
    if (phase === "running") return "<b>Rechecking</b>";
    if (phase === "down") return `<b>Recheck unavailable.</b> Last answer ${c ? stamp(c.at) : "none"}.`;
    if (phase === "fresh") {
      const ready = Object.values(c.entries).filter((e) => e.ok).length, n = Object.keys(c.entries).length;
      return `<b>Rechecked ${clock(c.at)}.</b> ${ready} of ${n} ready.`;
    }
    if (phase === "aged") return `<b>Last rechecked ${c ? ago(c.at) : "earlier"}.</b> Recheck before Track or Copy.`;
    return `<b>Not rechecked.</b> Oldest leg added ${ago(oldest)}.`;
  }
  function renderLeg(l, phase) {
    const c = phase === "fresh" ? S.slip.check.legs[l.id] : null;
    const problems = c && !c.ok ? `<div class="problems">${c.problems.map((p) => `<p class="problem">${p.text}${p.swap ? `<br><button class="btn" type="button" ${act("swap-leg", l.id)}>Use current line</button>` : ""}${p.feed ? `<br><button class="btn" type="button" ${act("retry-feed", p.feed)}>Update ${D.sports[p.feed]} lines</button>` : ""}</p>`).join("")}</div>` : "";
    return `<div class="leg">
      <span class="leg-name">${esc(l.player)}</span>
      <span class="leg-pick">${cap(l.side)} <span class="mono">${l.line}</span> ${esc(l.stat_label)} · ${esc(l.period_label)}</span>
      <span class="leg-meta">${esc(l.shown_label)} ${pct(l.shown_p)} at ${clock(l.added_at)}</span>
      <button class="leg-x" type="button" aria-label="Remove ${esc(l.player)} ${cap(l.side)} ${l.line}" ${act("leg-remove", l.id)}>${ICON.x}</button>
      ${c && c.ok ? '<span class="leg-ok">Passed recheck</span>' : ""}${problems}</div>`;
  }
  function renderEntry(e, phase) {
    const c = phase === "fresh" ? S.slip.check.entries[e.key] : null;
    const down = pState("slip") === "withdrawn";
    const rule = D.platforms[e.platform], max = e.sport === "cs2" ? Math.min(rule.max, 6) : rule.max;
    const unknown = e.legs.filter((l) => l.payout == null).length;
    const pay = unknown ? `Payout unknown on ${plural(unknown, "leg")}. EV stays hidden.` : "Payouts known. EV stays hidden in this preview.";
    const entryP = c && c.problems.length ? `<div class="problems" style="margin:0">${c.problems.map((p) => `<p class="problem">${p.text}</p>`).join("")}</div>` : "";
    const ready = c && c.ok && !down;
    return `<section class="entry${ready ? " ready" : ""}" aria-label="${esc(e.label)} ${D.sports[e.sport]} entry">
      <header class="entry-head"><span>${esc(e.label)} · ${D.sports[e.sport]}</span><span>${e.legs.length} of ${max} legs</span></header>
      ${e.legs.map((l) => renderLeg(l, phase)).join("")}
      <div class="entry-foot">${entryP}<span>${pay}</span>${ready ? `<span>${esc(prefillNote(e))}</span>` : ""}
        <div class="entry-acts"><button class="btn" type="button" ${ready ? "" : "disabled"} ${act("track", e.key)}>Track</button><button class="btn" type="button" ${ready ? "" : "disabled"} ${act("copy", e.key)}>Copy</button></div>
      </div></section>`;
  }
  function renderChecks(phase) {
    if (phase !== "fresh") return "";
    const c = S.slip.check, unknown = c.unknown;
    const li = CHECKS.map((t, i) => `<li><span class="ck${c.fails[i] ? " no" : ""}">${c.fails[i] ? "Refused" : "Pass"}</span><span>${t}${c.fails[i] ? ` (${c.fails[i]})` : ""}</span></li>`).join("");
    return `<details class="checks"><summary>Recheck details</summary><ul>${li}<li><span class="ck">${unknown ? "Hold" : "Pass"}</span><span>Payout known before EV: ${unknown ? `unknown on ${plural(unknown, "leg")}, EV hidden` : "known, EV hidden in this preview"}</span></li></ul></details>`;
  }
  function slipHtml(mode) {
    const n = S.slip.legs.length, phase = slipPhase();
    const titleTag = mode === "rail" ? `<h2 id="rail-h">Your slip</h2>` : mode === "page" ? `<h2>Your slip</h2>` : "";
    const head = `<div class="slip-head">${titleTag}<span class="slip-count">${plural(n, "pick")}</span>${n ? `<button class="link-btn" type="button" ${act("slip-clear")}>Clear</button>` : ""}</div>`;
    if (!n) return `<div class="slip">${head}<div class="empty"><p>Add a prop from the board to start a slip.</p></div></div>`;
    const down = phase === "down" ? `<div class="strip inset"><b>Recheck did not answer</b><span class="t">${stamp(PANEL_AT.slip)}</span>${retryBtn("slip")}</div>` : stateStrip("slip", true);
    const body = phase === "running" ? '<div class="skel-line"></div><div class="skel-line"></div><div class="skel-line"></div>' : "";
    return `<div class="slip">${head}<p class="slip-status">${statusLine(phase)}</p>
      <div class="slip-body">${down}${body}${renderChecks(phase)}${entries().map((e) => renderEntry(e, phase)).join("")}</div>
      <div class="slip-foot"><button class="btn btn-gold btn-block" type="button" ${phase === "running" ? "disabled" : ""} ${act("recheck")}>${phase === "fresh" ? "Recheck again" : "Recheck"}</button>
      <p class="slip-note">Legs on one platform and sport share an entry. Saved in this browser.</p></div></div>`;
  }
  function keepFocus(fn) {
    const a = document.activeElement, k = a && a.dataset ? a.dataset.fk : null;
    fn();
    if (k) { const n = document.querySelector(`[data-fk="${CSS.escape(k)}"]`); if (n) n.focus(); }
  }
  function renderSlips() {
    keepFocus(() => {
      const scroller = document.querySelector("#rail-body .slip-body"), top = scroller ? scroller.scrollTop : 0;
      $("rail-body").innerHTML = slipHtml("rail");
      const again = document.querySelector("#rail-body .slip-body");
      if (again) again.scrollTop = top;
      if ($("slip-dialog").open) $("sheet-body").innerHTML = slipHtml("sheet");
      renderSlipsPage();
      const n = S.slip.legs.length;
      $("slip-fab").textContent = `Slip · ${plural(n, "pick")}`;
      document.querySelectorAll("[data-mini-slip]").forEach((b) => { b.textContent = `Slip · ${n}`; });
      renderNav();
    });
  }
  function renderSlipsPage() {
    const groups = new Map();
    allReceipts().forEach((r) => { if (!groups.has(r.slip_id)) groups.set(r.slip_id, []); groups.get(r.slip_id).push(r); });
    const past = [...groups.entries()].map(([id, legs]) => {
      const t = legs[0];
      return `<div class="pick-card"><a class="pick-head" href="#/picks/${esc(t.id)}" style="text-decoration:none;grid-template-columns:minmax(0,1fr) auto">
        <span><b>${esc(t.platform_label)} · ${D.sports[t.sport]}</b><small>${esc(legs.map((l) => `${l.player} ${cap(l.side)} ${l.line}`).join(", "))}</small></span>
        <span class="pick-chance">${stamp(t.tracked_at)}</span></a></div>`;
    }).join("");
    $("view-slips").innerHTML = `<div class="page-h"><div><h1 id="slips-h" tabindex="-1">Slips</h1><p>One tray across sports, split into the entries a platform can take.</p></div></div>
      <div class="slip-page">${slipHtml("page")}</div>
      <div class="res-sec"><h2>Tracked slips</h2>${past || '<p class="ln-note">No tracked slips yet.</p>'}</div>`;
  }
  function renderNav() {
    const pending = allReceipts().filter((r) => pickStatus(r) === "pending").length;
    const counts = { slips: S.slip.legs.length, picks: pending };
    document.querySelectorAll(".nav-list [data-nav]").forEach((a) => {
      const k = a.dataset.nav;
      a.querySelector(".nav-count")?.remove();
      if (counts[k]) a.insertAdjacentHTML("beforeend", `<span class="nav-count" aria-label="${counts[k]} ${k === "slips" ? "on slip" : "pending"}">${counts[k]}</span>`);
    });
    document.querySelectorAll("[data-nav]").forEach((a) => {
      if (a.dataset.nav === S.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  /* ---- render: research panel ---- */
  function secMarket(row, rec, side) {
    const sideBtn = (sd) => {
      const offered = rec.offer[sd].offered;
      return `<button class="side-btn" type="button" aria-pressed="${side === sd}" ${offered ? "" : "disabled"} ${act("p-side", sd)}><b>${cap(sd)} ${rec.offer.line}</b><span class="mono">${pct(chanceOf(rec, sd))}</span><small>${offered ? esc(payoutText(payoutOf(rec, sd))) : "Not offered on " + esc(rec.offer.platform_label)}</small></button>`;
    };
    const books = row.offers.length > 1 ? `<div class="book-pick" role="group" aria-label="Book at this line">${row.offers.map((o) => `<button class="book-opt" type="button" aria-pressed="${o === rec}" ${act("p-book", o.offer.platform)}>${esc(o.offer.platform_label)}</button>`).join("")}</div>` : "";
    return `<section class="sec" aria-label="Market and line"><div class="sides">${sideBtn("over")}${sideBtn("under")}</div>${books}</section>`;
  }
  function secForecast(rec, side) {
    const s = rec.scout, v = verdict(rec, side), p = chanceOf(rec, side), stale = feedStale(rec) || pState("research") === "stale";
    const o = s.p_over * 100, u = s.p_under * 100, pp = s.p_push * 100;
    const validation = s.validation;
    const kv = [["Projection", s.projection], ["80% range", s.range80 ? s.range80.join(" to ") : "--"], ["Stake", s.hold ? "None" : `${s.units}U`],
      ["Model", s.model_version], ["Forecast logged", stamp(s.forecast_at)], ["Forecast id", s.forecast_id || "Not sent by this feed"]];
    return `<section class="sec" aria-label="Scout forecast"><div class="sec-h"><h3 class="eyebrow">Scout forecast</h3><span class="tag g">${esc(validation.label)}</span></div>
      <div class="forecast"><div class="fc-top"><div><div class="fc-num">${pct(p)}</div><div class="fc-lab">Predicted chance of ${cap(side)} ${rec.offer.line}${stale ? `, as of ${stamp(s.forecast_at)}` : ""}</div></div><div>${v.html}</div></div>
      <div class="pbar" aria-hidden="true"><i class="${side === "over" ? "on" : ""}" style="width:${o}%"></i>${pp ? `<i class="pp" style="width:${pp}%"></i>` : ""}<i class="${side === "under" ? "on" : ""}" style="width:${u}%"></i></div>
      <div class="pbar-key"><span>Over ${pct(s.p_over)}</span>${pp ? `<span>Push ${pct(s.p_push)}</span>` : ""}<span>Under ${pct(s.p_under)}</span></div>
      <dl class="kv">${kv.map(([k, val]) => `<div><dt>${k}</dt><dd class="mono">${esc(val)}</dd></div>`).join("")}</dl>
      ${s.stake_reason ? `<p class="consensus">${esc(s.stake_reason)}</p>` : ""}<p class="consensus">${esc(validation.detail)}</p></div></section>`;
  }
  function tile(label, t) {
    return `<div class="hist-tile"><b>${t ? `${t.over}-${t.under}-${t.push}` : "--"}</b><span>${label}</span></div>`;
  }
  function secHistory(rec, side, compact) {
    const h = rec.history, more = compact ? `<button class="link-btn" type="button" ${act("p-tab", "history")}>Open history</button>` : "";
    if (!h.recent.results) {
      return `<section class="sec" aria-label="Past results"><div class="sec-h"><h3 class="eyebrow">Past results</h3></div>
        <div class="state bare"><h3>No game-by-game results</h3><p>The ${D.sports[rec.sport]} feed sends a recent average only: ${h.recent.per_game} per game over ${h.recent.games} games.</p><p class="t">Feed time ${stamp(S.feeds[rec.sport].at)}</p></div></section>`;
    }
    const n = compact ? 10 : S.histN, res = h.recent.results.slice(0, n);
    const hits = res.filter((g) => g.value !== rec.offer.line && (side === "over" ? g.value > rec.offer.line : g.value < rec.offer.line)).length;
    const pushes = res.filter((g) => g.value === rec.offer.line).length, avg = (res.reduce((a, g) => a + g.value, 0) / res.length).toFixed(1);
    const seg = compact ? "" : `<div class="seg" role="group" aria-label="Games shown">${[5, 10, 20].map((v) => `<button type="button" aria-pressed="${S.histN === v}" ${act("hist-n", v)}>L${v}</button>`).join("")}</div>`;
    const table = compact ? "" : `<details class="tbl"><summary>Show results as a table</summary><table class="dt"><thead><tr><th>Played</th><th>Opponent</th><th>Final</th><th>Against ${rec.offer.line}</th></tr></thead><tbody>
      ${res.map((g) => `<tr><td>${g.ago_days} days ago</td><td class="mono">${esc(g.opponent)}</td><td class="mono">${g.value}</td><td>${g.value === rec.offer.line ? "Push" : g.value > rec.offer.line ? "Over" : "Under"}</td></tr>`).join("")}</tbody></table></details>`;
    return `<section class="sec" aria-label="Past results"><div class="sec-h"><h3 class="eyebrow">Past results at this line</h3>${more}${seg}</div>
      <div class="hist-block"><div class="hist-tiles">${tile("L5 over-under-push", h.l5)}${tile("L10 over-under-push", h.l10)}<div class="hist-tile"><b>${avg}</b><span>Average, ${res.length} games</span></div></div>
      <div class="chart" data-chart data-n="${n}" role="img" aria-label="${res.length} past results against the line ${rec.offer.line}: ${hits} hits for ${cap(side)}, ${pushes} ${pushes === 1 ? "push" : "pushes"}."></div>
      <div class="legend"><span><i class="h"></i>Hit for ${cap(side)}</span><span><i></i>Miss</span><span><i class="p"></i>Push</span><span><i class="l"></i>Line ${rec.offer.line}</span></div>${table}</div></section>`;
  }
  function bookTable(r) {
    return `<table class="dt"><thead><tr><th style="width:34%">Book</th><th>Over</th><th>Under</th><th>Seen</th></tr></thead><tbody>${r.offers.map((o) => {
      const cell = (sd) => (!o.offer[sd].offered ? "Not offered" : o.offer[sd].payout == null ? "Unknown" : o.offer[sd].payout.toFixed(2) + "x");
      const st = withdrawn(o) ? `<small>Withdrawn ${stamp(o.state.board_at)}</small>` : "";
      return `<tr><td>${esc(o.offer.platform_label)}${o.market.line_type !== "standard" ? ` <span class="tag">${esc(o.market.line_type)}</span>` : ""}<small>${esc(o.offer.revision_kind === "exact" ? "r" + o.offer.revision.split(":r")[1].split(":")[0] : "Derived id")}</small>${st}</td>
        <td class="mono">${cell("over")}</td><td class="mono">${cell("under")}</td><td class="mono">${clock(o.offer.seen_at)}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function secBooks(row, rec, compact) {
    const c = row.lead.consensus;
    const cons = c ? `<p class="consensus">Market at ${rec.offer.line}: Over ${pct(c.p_over)}, Under ${pct(c.p_under)} from ${plural(c.n, "book")}. Scout is ${c.edge_pts > 0 ? "+" : ""}${c.edge_pts.toFixed(1)} pts on Over.</p>` : "";
    const others = row.siblings.map((s) => {
      const v = verdict(s.lead), sp = s.lead.scout.p_side;
      return `<div class="ln-group other"><div class="ln-title"><span><b>${s.lead.offer.line}</b> <span class="tag">Different line</span></span><button class="link-btn" type="button" ${act("p-line", s.id)}>Open ${s.lead.offer.line}</button></div>
        <p class="ln-note">Not the same bet as ${rec.offer.line}. Its own forecast: ${esc(v.text)} ${sp == null ? "" : pct(sp)}.</p>${compact ? "" : bookTable(s)}</div>`;
    }).join("");
    return `<section class="sec" aria-label="Book comparison"><div class="sec-h"><h3 class="eyebrow">Books</h3>${compact ? `<button class="link-btn" type="button" ${act("p-tab", "books")}>Compare books</button>` : ""}</div>
      <div class="ln-group current"><div class="ln-title"><span><b>${rec.offer.line}</b> <span class="tag g">Exact match</span></span></div>${bookTable(row)}${cons}</div>${others}</section>`;
  }
  function secContext(rec, compact) {
    const c = D.context[rec.player.key] || D.context_default[rec.sport];
    const dl = (list) => `<dl class="ctx-list">${list.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;
    const usage = (rec.sport === "nba" || rec.sport === "nfl") ? "Minutes and role" : "Role";
    const body = compact
      ? dl(c.usage.slice(0, 2)) + c.limits.map((l) => `<p class="ln-note" style="margin-top:6px">${esc(l)}</p>`).join("")
      : `<h4 class="eyebrow">${usage}</h4>${dl(c.usage)}<h4 class="eyebrow" style="margin-top:14px">Opponent ${esc(rec.player.opponent)}</h4>${dl(c.opponent)}
         ${c.injuries.length ? `<h4 class="eyebrow" style="margin-top:14px">Injury report</h4>${dl(c.injuries)}` : ""}
         <h4 class="eyebrow" style="margin-top:14px">Sample</h4>${c.limits.map((l) => `<p class="ln-note">${esc(l)}</p>`).join("")}`;
    return `<section class="sec" aria-label="Context"><div class="sec-h"><h3 class="eyebrow">Context</h3>${compact ? `<button class="link-btn" type="button" ${act("p-tab", "context")}>Full context</button>` : ""}</div>${body}</section>`;
  }
  function panelNotice(rec) {
    if (withdrawn(rec)) return `<div class="strip inset"><b>Withdrawn by ${esc(rec.offer.platform_label)}</b><span class="t">${stamp(rec.state.board_at)}</span></div>`;
    if (started(rec)) return `<div class="strip inset"><b>Game started</b><span class="t">${stamp(rec.event.start_utc)}</span></div>`;
    if (feedStale(rec)) return `<div class="strip inset"><b>${D.sports[rec.sport]} lines last updated ${ago(S.feeds[rec.sport].at)}</b><span class="t">${stamp(S.feeds[rec.sport].at)}</span><button class="btn" type="button" ${act("retry-feed", rec.sport)}>Retry</button></div>`;
    return "";
  }
  function panelBody(row, rec, side) {
    if (blocked("research")) return stateBlock("research");
    const P = S.panel, top = stateStrip("research", true) + panelNotice(rec);
    if (P.tab === "history") return top + secHistory(rec, side, false);
    if (P.tab === "books") return top + secBooks(row, rec, false);
    if (P.tab === "context") return top + secContext(rec, false);
    return top + secMarket(row, rec, side) + secForecast(rec, side) + secHistory(rec, side, true) + secBooks(row, rec, true) + secContext(rec, true);
  }
  function renderPanel() {
    const el = $("panel"), P = S.panel;
    if (!P) return;
    const row = rows.get(P.id), rec = offerOf(row, P.platform), side = P.side;
    const body = el.querySelector(".p-body"), scrollTop = body ? body.scrollTop : 0;
    const ev = D.events[rec.event.id];
    const refusal = blocked("research") ? "This panel has not loaded." : addRefusal(rec, side);
    const inSlip = legsFor(rec).some((l) => l.side === side);
    const tab = TABS.map((t) => `<button class="tab" type="button" role="tab" id="tab-${t}" aria-controls="p-body" aria-selected="${P.tab === t}" tabindex="${P.tab === t ? 0 : -1}" ${act("p-tab", t)}>${cap(t)}</button>`).join("");
    keepFocus(() => {
      el.innerHTML = `<header class="p-head">
        <h2 class="p-name" id="panel-name" tabindex="-1">${esc(rec.player.name)}</h2>
        <button class="icon-btn p-close" type="button" aria-label="Close research" ${act("p-close")}>${ICON.x}</button>
        <p class="p-sub">${esc(rec.player.team)} ${esc(rec.player.role)} · ${esc(rec.event.label)} · ${esc(rec.event.competition)}<br>${started(rec) ? "Started" : "Starts"} ${stamp(rec.event.start_utc)}${ev.best_of ? ` · BO${ev.best_of}` : ""}</p>
        <p class="p-market"><span>${esc(rec.market.stat_label)} · ${esc(rec.market.period_label)}</span><b>${rec.offer.line}</b><span class="tag">${esc(rec.offer.platform_label)}</span>${row.siblings.length ? `<span class="tag">Other lines exist</span>` : ""}</p>
      </header>
      <div class="tabs" role="tablist" aria-label="Research sections">${tab}</div>
      <div class="p-body" id="p-body" role="tabpanel" aria-labelledby="tab-${P.tab}" tabindex="0">${panelBody(row, rec, side)}</div>
      <footer class="p-foot">${refusal ? `<p class="add-why" role="status">${refusal}</p>` : ""}
        <div class="p-foot-row"><button class="btn mini-slip" type="button" data-mini-slip ${act("open-sheet")}>Slip · ${S.slip.legs.length}</button>
        <button class="btn btn-gold" type="button" ${refusal ? "disabled" : ""} ${act("p-add")}>${inSlip ? "On your slip" : `Add ${cap(side)} ${rec.offer.line} to slip`}</button></div></footer>`;
    });
    const nb = el.querySelector(".p-body");
    if (nb) nb.scrollTop = scrollTop;
    mountCharts(rec, side);
  }

  /* ---- chart: bars for past results, the offer line drawn across them ---- */
  function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), n = x / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
  function chartSvg(results, line, side, w) {
    const data = results.slice().reverse(), h = 196, m = { l: 30, r: 6, t: 22, b: 24 }, pw = w - m.l - m.r, ph = h - m.t - m.b;
    const vals = data.map((d) => d.value), top = Math.max(line, ...vals) * 1.12, y = (v) => m.t + ph - (v / top) * ph, base = m.t + ph;
    const slot = pw / data.length, bw = Math.min(26, Math.max(6, slot * 0.62)), step = niceStep(top / 3);
    let out = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">`;
    for (let t = 0; t <= top; t += step) {
      out += `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${m.l - 6}" y="${y(t) + 3.5}" text-anchor="end">${Math.round(t * 10) / 10}</text>`;
    }
    const labelEvery = Math.ceil(32 / slot), hiV = Math.max(...vals);
    let hiDone = false;
    data.forEach((d, i) => {
      const x = m.l + slot * i + (slot - bw) / 2, yv = y(d.value), push = d.value === line, over = d.value > line;
      const cls = push ? "b-push" : (side === "over" ? over : !over) ? "b-hit" : "b-miss", r = Math.min(4, bw / 2, base - yv);
      if (push) out += `<rect class="b-push" x="${x + .75}" y="${yv}" width="${bw - 1.5}" height="${Math.max(base - yv, 1)}" rx="3"/>`;
      else out += `<path class="${cls}" d="M${x},${base}V${yv + r}Q${x},${yv} ${x + r},${yv}H${x + bw - r}Q${x + bw},${yv} ${x + bw},${yv + r}V${base}Z"/>`;
      const last = i === data.length - 1, hi = d.value === hiV && !hiDone && !last;
      if (hi) hiDone = true;
      if (last || hi) out += `<text x="${x + bw / 2}" y="${yv - 5}" text-anchor="middle">${d.value}</text>`;
      if ((data.length - 1 - i) % labelEvery === 0) out += `<text x="${x + bw / 2}" y="${h - 8}" text-anchor="middle">${esc(d.opponent)}</text>`;
      const verdictText = push ? "Push" : (side === "over" ? over : !over) ? "Hit" : "Miss";
      out += `<rect class="bar-hit" x="${m.l + slot * i}" y="${m.t - 10}" width="${slot}" height="${ph + 10}" data-tip="${esc(`${d.ago_days} days ago v ${d.opponent}: ${d.value}. ${verdictText}`)}"/>`;
    });
    out += `<line class="line-mark" x1="${m.l}" x2="${w - m.r}" y1="${y(line)}" y2="${y(line)}"/>`;
    out += `<text class="line-lab" x="${w - m.r}" y="${m.t - 10}" text-anchor="end">Line ${line}</text>`;
    return out + "</svg>";
  }
  function mountCharts(rec, side) {
    document.querySelectorAll("#panel [data-chart]").forEach((el) => {
      const n = +el.dataset.n, w = Math.max(240, Math.round(el.clientWidth));
      el.innerHTML = chartSvg(rec.history.recent.results.slice(0, n), rec.offer.line, side, w);
    });
  }

  /* ---- render: my picks ---- */
  function pickCard(r) {
    const st = pickStatus(r), open = S.openPick === r.id;
    const kv = [["Offer revision", r.offer_revision], ["Revision type", r.revision_kind === "exact" ? "Exact" : "Derived from offer fields"], ["Forecast version", r.model_version],
      ["Forecast id", r.forecast_id || "Not sent by this feed"], ["Displayed probability", pct(r.displayed_p)], ["Tracked at", stamp(r.tracked_at)], ["Forecast logged", stamp(r.forecast_at)], ["Book", r.platform_label]];
    const body = open ? `<div class="pick-body" id="pick-${esc(r.id)}"><h3 class="eyebrow">Receipt</h3><dl class="kv">${kv.map(([k, v]) => `<div><dt>${k}</dt><dd class="mono">${esc(v)}</dd></div>`).join("")}</dl>
      <h3 class="eyebrow" style="margin-top:14px">Timeline</h3><ol class="timeline">${r.timeline.map((t) => `<li class="k-${t.kind}${t.result ? " r-" + t.result : ""}"><time datetime="${t.at}">${stamp(t.at)}</time>${esc(t.text)}</li>`).join("")}</ol></div>` : "";
    return `<article class="pick-card"><button class="pick-head" type="button" aria-expanded="${open}" aria-controls="pick-${esc(r.id)}" ${act("pick", r.id)}>
      <span><b>${esc(r.player)} ${cap(r.side)} ${r.line}</b><small>${D.sports[r.sport]} · ${esc(r.stat_label)} · ${esc(r.period_label)} · ${esc(r.matchup)}</small></span>
      <span class="pick-chance">${pct(r.displayed_p)}</span><span class="status ${st}">${STATUS[st]}</span>${ICON.down}</button>${body}</article>`;
  }
  function renderPicks() {
    const box = $("view-picks"), list = allReceipts().filter((r) => {
      const st = pickStatus(r);
      return S.picksFilter === "all" || (S.picksFilter === "pending" ? st === "pending" : st !== "pending");
    });
    const seg = `<div class="seg" role="group" aria-label="Show picks">${[["all", "All"], ["pending", "Pending"], ["settled", "Settled"]].map(([k, l]) => `<button type="button" aria-pressed="${S.picksFilter === k}" ${act("picks-filter", k)}>${l}</button>`).join("")}</div>`;
    const head = `<div class="page-h"><div><h1 id="picks-h" tabindex="-1">My picks</h1><p>Each receipt keeps what you saw when you tracked it. Corrections are added below, never written over.</p></div>${seg}</div>`;
    const inner = blocked("picks") ? stateBlock("picks")
      : stateStrip("picks", true) + (list.length ? list.map(pickCard).join("") : '<div class="empty"><p>No picks in this view.</p></div>');
    keepFocus(() => { box.innerHTML = head + inner; });
  }

  /* ---- render: results ---- */
  function wilson(h, n) {
    if (!n) return [0, 0];
    const z = 1.96, p = h / n, d = 1 + z * z / n, c = (p + z * z / (2 * n)) / d, r = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
    return [c - r, c + r];
  }
  function groupResults() {
    const name = { sport: (r) => D.sports[r[0]], market: (r) => `${D.sports[r[0]]} ${r[1]}`, side: (r) => cap(r[2]), version: (r) => r[3] }[S.group];
    const m = new Map();
    D.results.filter((r) => S.resSport === "all" || r[0] === S.resSport).forEach((r) => {
      const k = name(r), g = m.get(k) || { name: k, hits: 0, misses: 0, pushes: 0, stated: 0 }, n = r[4] + r[5];
      g.hits += r[4]; g.misses += r[5]; g.pushes += r[6]; g.stated += r[7] * n;
      m.set(k, g);
    });
    return [...m.values()];
  }
  function resRow(g) {
    const n = g.hits + g.misses, obs = n ? g.hits / n : 0, st = n ? g.stated / n : 0, gap = (obs - st) * 100, ci = wilson(g.hits, n);
    const X = (v) => Math.max(0, Math.min(100, (v - 0.4) / 0.3 * 100));
    const left = Math.min(X(obs), X(st)), width = Math.abs(X(obs) - X(st));
    return `<tr><td>${esc(g.name)}</td>
      <td data-l="Settled">${n}</td><td data-l="W-L-P">${g.hits}-${g.misses}-${g.pushes}</td><td data-l="Stated">${pct(st)}</td><td data-l="Observed">${pct(obs)}</td>
      <td data-l="Gap">${gap >= 0 ? "+" : ""}${gap.toFixed(1)} pts</td><td data-l="95% range">${pct(ci[0])} to ${pct(ci[1])}</td>
      <td class="w"><span class="dumb" aria-hidden="true"><span class="bar" style="left:${left}%;width:${width}%"></span><span class="st" style="left:${X(st)}%"></span><span class="ob" style="left:${X(obs)}%"></span></span></td></tr>`;
  }
  function renderResults() {
    const box = $("view-results");
    const seg = `<div class="seg" role="group" aria-label="Group results by">${[["sport", "Sport"], ["market", "Market"], ["side", "Side"], ["version", "Version"]].map(([k, l]) => `<button type="button" aria-pressed="${S.group === k}" ${act("group", k)}>${l}</button>`).join("")}</div>`;
    const sportSel = `<label class="pick" style="display:block"><span class="sr">Sport</span><select id="res-sport">${[["all", "All sports"], ...Object.entries(D.sports)].map(([k, v]) => `<option value="${k}"${S.resSport === k ? " selected" : ""}>${v}</option>`).join("")}</select></label>`;
    const head = `<div class="page-h"><div><h1 id="results-h" tabindex="-1">Results</h1><p>Settled picks only, as of ${stamp(D.results_at)}. A push is its own result: it is not a win and is left out of Observed.</p></div><div style="display:flex;gap:8px;flex-wrap:wrap">${seg}${sportSel}</div></div>`;
    let inner;
    if (blocked("results")) inner = stateBlock("results");
    else {
      const groups = groupResults(), total = groups.reduce((t, g) => ({ name: "All", hits: t.hits + g.hits, misses: t.misses + g.misses, pushes: t.pushes + g.pushes, stated: t.stated + g.stated }), { name: "All", hits: 0, misses: 0, pushes: 0, stated: 0 });
      const rcpts = allReceipts();
      const counted = (s) => (s === "pending" ? "Not settled yet" : s === "void" ? "Not counted" : s === "push" ? "Counted as a push, not a win" : "Counted");
      inner = stateStrip("results", true) + `<div class="res-legend"><span><i class="st"></i>Stated chance (forecast)</span><span><i class="ob"></i>Observed hit rate (verified results)</span></div>
        <table class="res-tbl"><thead><tr><th>${cap(S.group)}</th><th>Settled</th><th>W-L-P</th><th>Stated</th><th>Observed</th><th>Gap</th><th>95% range</th><th style="width:22%">Stated v observed, 40% to 70%</th></tr></thead>
        <tbody>${groups.map(resRow).join("")}</tbody><tfoot>${resRow(total)}</tfoot></table>
        <div class="res-sec"><h2>Your tracked picks</h2><p class="ln-note">Every pick you track appears here with the reason it counts or does not.</p>
        ${rcpts.map((r) => {
          const st = pickStatus(r), lastT = r.timeline[r.timeline.length - 1];
          return `<div class="res-track"><span><b>${esc(r.player)} ${cap(r.side)} ${r.line}</b><small>${D.sports[r.sport]} · ${esc(r.stat_label)} · ${esc(r.model_version)}</small></span>
            <span class="status ${st}" style="text-align:left">${STATUS[st]}</span>
            <span class="res-why">${esc(counted(st))}. ${esc(lastT.text)}. <a href="#/picks/${esc(r.id)}">Receipt</a></span></div>`;
        }).join("")}</div>`;
    }
    keepFocus(() => { box.innerHTML = head + inner; });
  }

  /* ---- routing ---- */
  function parseHash() {
    const p = location.hash.replace(/^#\/?/, "").split("/");
    const view = VIEWS.includes(p[0]) ? p[0] : "research";
    return { view, panel: view === "research" && p[1] === "p" ? decodeURIComponent(p[2] || "") : null, tab: TABS.includes(p[3]) ? p[3] : "overview", pick: view === "picks" ? p[1] || null : null };
  }
  function setInert(on) {
    ["top", "nav", "main", "bottom", "slip-fab"].forEach((id) => { $(id).inert = on; });
  }
  function closePanelUI() {
    const el = $("panel");
    if (el.hidden) return;
    el.hidden = true; el.innerHTML = ""; $("panel-scrim").hidden = true; S.panel = null;
    setInert(false);
    const b = S.opener && document.querySelector(`.row[data-row="${CSS.escape(S.opener)}"] .row-open`);
    if (b) b.focus(); else $("board-list").focus({ preventScroll: true });
  }
  function openPanelUI(id, tab) {
    const row = rows.get(id);
    if (!row) { closePanelUI(); return; }
    const first = !S.panel, same = S.panel && S.panel.id === id;
    const rec = lead(row);
    S.panel = { id, tab, side: same ? S.panel.side : rec.scout.side || "over", platform: same ? S.panel.platform : rec.offer.platform };
    if (!rec.offer[S.panel.side].offered) S.panel.side = S.panel.side === "over" ? "under" : "over";
    $("panel").hidden = false; $("panel-scrim").hidden = false;
    setInert(true);
    renderPanel();
    if (first || !same) { const nm = $("panel-name"); if (nm) nm.focus(); }
  }
  function route(focusPage) {
    const r = parseHash(), changed = r.view !== S.view;
    S.view = r.view;
    VIEWS.forEach((v) => { $("view-" + v).hidden = v !== r.view; });
    if (r.pick) S.openPick = r.pick;
    if (r.view === "picks") renderPicks();
    if (r.panel) openPanelUI(r.panel, r.tab); else closePanelUI();
    renderNav();
    $("slip-fab").hidden = r.view === "slips";
    if (changed && focusPage) { const h = document.querySelector(`#view-${r.view} h1`); if (h && r.view !== "research") h.focus(); else if (r.view === "research") $("board-list").focus({ preventScroll: true }); }
    if (r.pick) { const c = document.querySelector(`[data-fk="pick:${CSS.escape(r.pick)}"]`); if (c) c.focus(); }
  }
  function setPanelHash(id, tab) {
    const h = `#/research/p/${encodeURIComponent(id)}${tab && tab !== "overview" ? "/" + tab : ""}`;
    history.replaceState(null, "", h);
  }

  /* ---- feedback ---- */
  let toastT;
  function say(msg) {
    const t = $("toast");
    t.textContent = msg; t.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove("on"), 3200);
  }

  /* ---- actions ---- */
  const actions = {
    chip(id) { S.chip = id; S.activeView = null; renderBoardAll(); $("board-scroll").scrollTop = 0; },
    open(id) { S.opener = id; location.hash = `#/research/p/${encodeURIComponent(id)}`; },
    "row-add"(id) {
      const row = rows.get(id), rec = lead(row), legs = legsFor(rec);
      if (legs.length) legs.forEach((l) => removeLeg(l.id)); else addLeg(rec, rec.scout.side);
      const again = document.querySelector(`[data-fk="${CSS.escape("row-add:" + id)}"]`);
      if (again) again.focus();
    },
    retry,
    "retry-feed"(id) {
      S.feeds[id].kind = "loading"; renderBoardAll(); if (S.panel) renderPanel();
      setTimeout(() => {
        S.feeds[id] = { kind: "ok", at: new Date().toISOString() };
        renderBoardAll(); if (S.panel) renderPanel(); renderSlips(); say(`${D.sports[id]} lines updated`);
      }, 700);
    },
    "reset-filters"() { resetFilters(); if ($("filters-dialog").open) renderFilters(); },
    view: applyView,
    "view-del"(id) { S.views = S.views.filter((v) => v.id !== id); store.set(KEY.views, S.views); if (S.activeView === id) S.activeView = null; renderViews(); },
    recheck,
    "leg-remove": removeLeg,
    "swap-leg": swapLeg,
    "slip-clear"() { S.slip = { legs: [], check: null }; saveSlip(); renderSlips(); renderBoard(); say("Slip cleared"); },
    copy: copyEntry,
    track: trackEntry,
    "open-sheet"() { $("sheet-body").innerHTML = slipHtml("sheet"); $("slip-dialog").showModal(); },
    "p-close"() { location.hash = "#/research"; },
    "p-tab"(t) {
      S.panel.tab = t; setPanelHash(S.panel.id, t); renderPanel();
      const b = $("p-body"); if (b) b.scrollTop = 0;
      const tb = $("tab-" + t); if (tb) tb.focus();
    },
    "p-side"(sd) { S.panel.side = sd; renderPanel(); },
    "p-book"(pf) { S.panel.platform = pf; const rec = offerOf(rows.get(S.panel.id), pf); if (!rec.offer[S.panel.side].offered) S.panel.side = S.panel.side === "over" ? "under" : "over"; renderPanel(); },
    "p-line"(id) { location.replace(`#/research/p/${encodeURIComponent(id)}/books`); },
    "p-add"() {
      const P = S.panel, rec = offerOf(rows.get(P.id), P.platform);
      if (addLeg(rec, P.side)) renderPanel();
    },
    "hist-n"(n) { S.histN = +n; renderPanel(); },
    pick(id) { S.openPick = S.openPick === id ? null : id; renderPicks(); },
    "picks-filter"(k) { S.picksFilter = k; renderPicks(); },
    group(k) { S.group = k; renderResults(); }
  };
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-act]");
    if (t && !t.disabled && actions[t.dataset.act]) { e.preventDefault(); actions[t.dataset.act](t.dataset.id); }
    if (!e.target.closest("#account")) $("account").open = false;
    if (e.target.id === "panel-scrim") location.hash = "#/research";
    if (e.target.tagName === "DIALOG") e.target.close();           /* a click on the backdrop */
  });
  document.addEventListener("change", (e) => {
    const f = e.target.dataset ? e.target.dataset.f : null;
    if (f) { setF(f, e.target.type === "checkbox" ? e.target.checked : e.target.value); return; }
    if (e.target.id === "f-day") setF("day", e.target.value);
    if (e.target.id === "f-book") setF("book", e.target.value);
    if (e.target.id === "f-market") setF("market", e.target.value);
    if (e.target.id === "res-sport") { S.resSport = e.target.value; renderResults(); $("res-sport").focus(); }
    if (e.target.id === "force-state") {
      S.forced = e.target.value; S.cleared = {};
      ["board", "research", "slip", "picks", "results"].forEach(renderPanelFor);
    }
  });
  document.addEventListener("submit", (e) => {
    if (e.target.id !== "save-view") return;
    e.preventDefault();
    const name = $("view-name").value.trim();
    if (!name) { $("view-name").focus(); return; }
    const v = { id: "u-" + Date.now().toString(36), name, state: { chip: S.chip, q: S.q, f: Object.assign({}, S.f) } };
    S.views.push(v); S.activeView = v.id; store.set(KEY.views, S.views); $("view-name").value = "";
    renderViews(); say(`Saved view: ${name}`);
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
    if (e.key === "/" && !typing && !$("filters-dialog").open && !$("slip-dialog").open) {
      e.preventDefault();
      $("top").classList.add("search-open"); $("search").focus(); return;
    }
    if (e.key === "Escape") {
      if (e.target.id === "search" && S.q) { e.target.value = ""; S.q = ""; S.activeView = null; renderBoardAll(); return; }
      if ($("filters-dialog").open || $("slip-dialog").open) return;
      if (S.panel) location.hash = "#/research";
      $("top").classList.remove("search-open");
    }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.classList.contains("row-open")) {
      const list = [...document.querySelectorAll(".row-open")], i = list.indexOf(e.target) + (e.key === "ArrowDown" ? 1 : -1);
      if (list[i]) { e.preventDefault(); list[i].focus(); }
    }
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && e.target.getAttribute("role") === "tab") {
      const i = TABS.indexOf(S.panel.tab) + (e.key === "ArrowRight" ? 1 : -1);
      actions["p-tab"](TABS[(i + TABS.length) % TABS.length]);
    }
  });
  $("search").addEventListener("input", (e) => { S.q = e.target.value; S.activeView = null; renderBoardAll(); });
  $("search-toggle").addEventListener("click", () => {
    const on = $("top").classList.toggle("search-open");
    $("search-toggle").setAttribute("aria-expanded", on);
    if (on) $("search").focus();
  });
  $("open-filters").addEventListener("click", () => { renderFilters(); $("filters-dialog").showModal(); });
  $("filters-dialog").addEventListener("close", renderChips);
  $("slip-fab").addEventListener("click", () => actions["open-sheet"]());
  $("reset-sample").addEventListener("click", () => {
    Object.values(KEY).forEach((k) => store.drop(k));
    S.slip = seedSlip(); saveSlip(); S.local = []; S.views = []; S.activeView = "p-today"; $("account").open = false;
    renderSlips(); renderBoardAll(); renderPicks(); renderResults(); say("Sample slip and picks reset");
  });
  document.addEventListener("pointermove", (e) => {
    const tip = $("chart-tip"), t = e.target.dataset ? e.target.dataset.tip : null;
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t; tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.max(e.clientY - 34, 8) + "px";
  });
  let resizeT;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      renderChips();
      if (S.panel) { const row = rows.get(S.panel.id); mountCharts(offerOf(row, S.panel.platform), S.panel.side); }
    }, 120);
  });
  window.addEventListener("hashchange", () => route(true));
  window.addEventListener("storage", (e) => {
    if (e.key === KEY.slip) { S.slip = store.get(KEY.slip, { legs: [], check: null }); renderSlips(); renderBoard(); }
  });

  /* ---- start ---- */
  saveSlip();
  renderChips(); renderViews(); renderBoard(); renderSlips(); renderPicks(); renderResults(); route(false);
  setTimeout(() => { S.booting = false; renderBoard(); }, 260);    /* stands in for the first board fetch */
})();
