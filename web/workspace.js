/* Research workspace. Every sport comes only from /preview/workspace/api/*: a failure shows that sport's last copy with its time,
   or says its board is unavailable, and one sport going down never touches another. Which sports a member sees is the server's
   answer (me.sports). The slip, tracked picks, preferences, saved views and the watchlist live on the member's account. */
(function () {
  "use strict";
  const D = window.WORKSPACE_DATA;
  const API = "/preview/workspace/api/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const act = (name, id) => `data-act="${name}"${id != null ? ` data-id="${esc(id)}"` : ""} data-fk="${esc(name + ":" + (id == null ? "" : id))}"`;
  const pct = (p) => (typeof p !== "number" ? "--" : (p * 100).toFixed(1) + "%");     /* one decimal, from the unrounded chance */
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const tenth = (x) => Math.round(x * 10) / 10;                /* a projection reads to one place; the saved number is untouched */
  const ICON = {
    plus: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    check: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7"/></svg>',
    x: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    down: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    up: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>'
  };
  ICON.star = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 4 2.5 5.2 5.7.8-4.1 4 1 5.6L12 17.3 6.9 19.6l1-5.6-4.1-4 5.7-.8z"/></svg>';
  const TABS = ["overview", "history", "books", "context"];
  const VIEWS = ["research", "matches", "slips", "picks", "results"];
  const SPORT_KEYS = ["cs2", "lol", "nba", "nfl"];
  const DEFAULT_F = { day: "today", book: "all", market: "all", period: "all", side: "all", tier: "all", gapsOnly: false, hideOff: false, watchOnly: false, sort: "start" };
  const SORTS = ["start", "chance", "player"];
  const BOARD_POLL = 30000, MINE_POLL = 60000, CHECK_DEBOUNCE = 350, SAVE_DEBOUNCE = 800, PREFS_DEBOUNCE = 2500, LINE_KEEP = 15000, MAX_LEGS = 8, DRAFT_MAX_MS = 86400000;
  const SCOPES = "#sheet-body,#rail-body,#view-slips,#view-picks,#view-results,#view-matches,#panel,#filters-body,#chips,#feed-strips,#board-list,#board-head";
  const STATUS = { hit: "Hit", miss: "Miss", push: "Push", void: "Void", pending: "Pending" };
  const CHECKS = ["Game not started", "Offer available and unchanged", "Forecast belongs to this event, line and period", "Platform accepts this combination", "No duplicates or conflicts"];

  /* ---- requests ---- */
  class ApiError extends Error { constructor(status, doc) { super("api " + status); this.status = status; this.doc = doc; } }
  async function api(path, body, keepalive) {                     /* a read told to wait by a busy server tries again; a press never repeats here */
    for (let i = 0; ; i++) {
      try { return await apiOnce(path, body, keepalive); }
      catch (e) {
        if (body !== undefined || i >= 2 || !(e instanceof ApiError) || e.status !== 503 || !e.doc || e.doc.error !== "busy") throw e;
        await new Promise((r) => setTimeout(r, 2000 + Math.random() * 1500));
      }
    }
  }
  async function apiOnce(path, body, keepalive) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 15000);
    try {
      const r = await fetch(API + path, body === undefined ? { signal: ctl.signal } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal, keepalive: !!keepalive });
      let doc = null;
      try { doc = await r.json(); } catch (e) { doc = null; }
      if (!r.ok) {
        if ([401, 403, 404].includes(r.status) && !/^(line|board\?|results\?)/.test(path)) sessionEnded();    /* every other route exists for a signed-in member */
        throw new ApiError(r.status, doc);
      }
      return doc;
    } finally { clearTimeout(t); }
  }
  const idem = () => Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  function debounce(fn, ms) {
    let t = null;
    return (...a) => { clearTimeout(t); t = setTimeout(() => { t = null; fn(...a); }, ms); };
  }

  /* ---- saved data is another writer's output: only the shape the page renders gets through ---- */
  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const isStr = (v) => typeof v === "string";
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const LEG_TEXT = ["id", "key", "book", "row_id", "event_id", "event_label", "player", "player_key", "team", "stat", "stat_label", "period", "period_label", "platform_label", "rev", "shown_label"];
  function cleanLeg(l) {
    return isObj(l) && LEG_TEXT.every((k) => isStr(l[k])) && own(D.sports, l.sport) && own(D.platforms, l.book) && (l.side === "over" || l.side === "under") && isNum(l.line)
      && (l.shown_p == null || isNum(l.shown_p)) && (l.read_rev == null || isStr(l.read_rev)) && isStr(l.added_at) && Number.isFinite(Date.parse(l.added_at));
  }
  function cleanView(v) {
    return isObj(v) && isStr(v.id) && isStr(v.name) && isObj(v.state) && isObj(v.state.f) && (v.state.q == null || isStr(v.state.q))
      && (v.state.chip === "all" || own(D.sports, v.state.chip)) && SORTS.includes(v.state.f.sort == null ? "start" : v.state.f.sort);
  }
  const cleanWatch = (w) => isObj(w) && isStr(w.id) && own(D.sports, w.sport) && isStr(w.key) && isStr(w.name) && (w.team == null || isStr(w.team));

  /* ---- time: one local clock everywhere, UTC only in a receipt's Details ---- */
  const fmtUTC = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  const stamp = (iso) => fmtUTC.format(new Date(iso)) + " UTC";
  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  function dayOf(iso) {
    const d = new Date(iso), now = new Date(), next = new Date(now.getTime() + 86400000);
    return dayKey(d) === dayKey(now) ? "today" : dayKey(d) === dayKey(next) ? "tomorrow" : "later";
  }
  function when(iso) {
    if (!iso) return "";
    const k = dayOf(iso);
    if (k === "today") return clock(iso);
    if (k === "tomorrow") return "Tomorrow " + clock(iso);
    return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + clock(iso);
  }
  const shortDate = (iso) => new Date(iso.length === 10 ? iso + "T12:00:00" : iso).toLocaleDateString([], { month: "short", day: "numeric" });   /* a date-only string is a day, not UTC midnight */
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
    return when(iso);
  }

  /* ---- state ---- */
  const T = { board: "", slip: "", panel: "" };                 /* what each timed region last painted */
  const S = {
    view: "research", chip: "all", q: "", f: Object.assign({}, DEFAULT_F), activeView: "p-today", me: null, event: null,
    boards: Object.fromEntries(SPORT_KEYS.map((k) => [k, { status: "loading", records: [], at: null, feeds: null, version: null, fails: 0 }])),
    panel: null, opener: null, openerSel: ".row-open", wanted: null, histN: 10, lines: {},
    slip: { legs: [], types: {} }, check: null, checkSig: "", checking: false, checkErr: null, tracked: null, trackIdem: {}, trackBusy: {}, sheetMark: null, searchJump: false,
    rev: 0, stateReady: false, saveFail: false, views: [], watch: [], syncedLegs: new Set(), syncedViews: new Set(), syncedWatch: new Set(), savedDay: false, signedOut: false, copyBusy: false,
    mine: { status: "loading", doc: null, older: [], next: null }, openPick: null, picksFilter: "all", picksSport: "all",
    results: Object.fromEntries(SPORT_KEYS.map((k) => [k, { status: "idle", doc: null }])), resSport: "cs2", group: "chance"
  };
  const sportsOn = () => SPORT_KEYS.filter((k) => k === "cs2" || !!(S.me && S.me.sports && S.me.sports[k]));
  const anyLoading = () => sportsOn().some((k) => S.boards[k].status === "loading");
  const boardList = () => (S.chip === "all" ? sportsOn() : [S.chip]).map((k) => [k, S.boards[k]]);
  const watchId = (sport, key) => sport + ":" + key;
  const watched = (r) => S.watch.some((w) => w.id === watchId(r.sport, r.player.key));

  /* ---- rows: one board row per exact prop and line. Offers at another line stay a separate row ---- */
  const rows = new Map();
  let all = [];
  const propKey = (r) => [r.sport, r.event.id, r.player.key, r.market.stat, r.market.period].join(".");
  function rebuild() {
    rows.clear();
    sportsOn().flatMap((k) => S.boards[k].records).forEach((r) => {
      if (!rows.has(r.key)) rows.set(r.key, { id: r.key, offers: [], siblings: [] });
      rows.get(r.key).offers.push(r);
    });
    all = [...rows.values()];
    all.forEach((row) => { row.lead = row.offers[0]; row.prop = propKey(row.lead); });
    const byProp = new Map();
    all.forEach((row) => { if (!byProp.has(row.prop)) byProp.set(row.prop, []); byProp.get(row.prop).push(row); });
    all.forEach((row) => { row.siblings = byProp.get(row.prop).filter((o) => o !== row).sort((a, b) => a.lead.offer.line - b.lead.offer.line); });
  }
  const findOffer = (book, id) => { for (const row of all) for (const o of row.offers) if (o.offer.platform === book && o.offer.offer_id === id) return o; return null; };

  /* ---- offer helpers ---- */
  const started = (r) => r.event.status === "started" || new Date(r.event.lock_utc || r.event.start_utc) <= Date.now();
  const startWord = (r) => (r.sport === "nba" || r.sport === "nfl" ? "Game started" : "Match started");
  const held = (r) => r.state.kind === "held";
  const slipOffer = (r) => D.platforms[r.offer.platform].slip;
  const liveOffer = (r) => !started(r);
  function lead(row) {
    const book = S.f.book;
    const pool = book === "all" ? row.offers : row.offers.filter((o) => o.offer.platform === book);
    return pool.find((o) => liveOffer(o) && slipOffer(o)) || pool.find(liveOffer) || pool[0] || row.lead;
  }
  const offerOf = (row, platform) => row.offers.find((o) => o.offer.platform === platform) || lead(row);
  const lostBook = (row, P) => !row.offers.some((o) => o.offer.platform === P.platform);
  const rowFlag = (row) => (started(row.lead) ? "started" : "");
  const chanceOf = (rec, side) => (side === "under" ? rec.scout.p_under : side === "over" ? rec.scout.p_over : null);
  function verdict(rec, side) {
    const s = rec.scout, sd = side || s.side;
    if (!sd) return { html: '<span class="verdict">No lean</span>', text: "No lean" };
    if (s.side && sd !== s.side) return { html: `<span class="verdict">Scout side is ${cap(s.side)}</span>`, text: `Scout side is ${cap(s.side)}` };
    if (s.hold) return { html: `<span class="verdict">${cap(sd)}</span>`, text: `Research estimate ${cap(sd)}` };
    if (s.grade) return { html: `<span class="verdict"><span class="g-letter">${esc(s.grade)}</span> ${cap(sd)}</span>`, text: `${s.grade} ${cap(sd)}` };
    return { html: `<span class="verdict">Lean ${cap(sd)}</span>`, text: `Lean ${cap(sd)}` };
  }
  const decimalOf = (o, side) => {                              /* a decimal price, the one number two books can compare on */
    const v = o.offer[side], P = D.platforms[o.offer.platform];
    if (P.price_comparable === false) return null;              /* Dabble: its own unit, not what an entry pays: never compared as odds */
    return P.kind === "decimal" ? v.payout : v.price;
  };
  function priceText(o, side) {
    const v = o.offer[side], dec = decimalOf(o, side);
    if (!v.offered) return null;
    if (dec != null) return dec.toFixed(2);
    return v.payout != null && v.payout !== 1 ? v.payout.toFixed(2) + "x" : null;   /* a 1.00x multiplier is no price: it says nothing */
  }
  const lineLabel = (o) => (o.market.line_type !== "standard" ? cap(o.market.line_type) : "Standard");

  /* ---- leg identity: sport, event, player, market, period, platform, side, line ---- */
  const legId = (r, side) => [r.sport, r.event.id, r.player.id || r.player.name, r.market.stat, r.market.period, r.offer.platform, side, r.offer.line].join("|");
  function makeLeg(r, side, addedAt) {
    return {
      id: legId(r, side), key: r.key, sport: r.sport, book: r.offer.platform, row_id: r.offer.offer_id, side, line: r.offer.line, rev: r.offer.revision, read_rev: r.scout.read_rev || null, shown_p: chanceOf(r, side),
      event_id: r.event.id, event_label: r.event.label, player: r.player.name, player_key: r.player.key, team: r.player.team, stat: r.market.stat, stat_label: r.market.stat_label,
      period: r.market.period, period_label: r.market.period_label, platform_label: r.offer.platform_label, shown_label: verdict(r, side).text, added_at: addedAt || new Date().toISOString()
    };
  }
  const wireLeg = (l) => ({ id: l.id, sport: l.sport, book: l.book, row_id: l.row_id, side: l.side, line: l.line, rev: l.rev, read_rev: l.read_rev, shown_p: l.shown_p, team: l.team, player: l.player, event: l.event_id });
  const rowLegs = (rec) => S.slip.legs.filter((l) => l.key === rec.key);                      /* this exact line, any platform */
  const legsFor = (rec) => rowLegs(rec).filter((l) => l.book === rec.offer.platform);
  const sig = () => S.slip.legs.map((l) => [l.id, l.rev, l.read_rev, l.shown_p].join(":")).join(";") + "|" + JSON.stringify(S.slip.types);
  function slipChanged() {
    S.check = S.checkSig === sig() ? S.check : null;
    S.tracked = null;
    saveState({ slip: true });
    renderSlips(); renderBoard(); renderPanel();
    if (S.slip.legs.length) scheduleCheck();
  }
  function addRefusal(rec, side) {
    if (started(rec)) return `${startWord(rec)} at ${when(rec.event.start_utc)}.`;
    if (!slipOffer(rec)) return `${rec.offer.platform_label} is shown for comparison. Slips cover PrizePicks and Underdog.`;
    if (!rec.offer[side].offered) return `${cap(side)} is not offered on ${rec.offer.platform_label}.`;
    if (held(rec)) return "The grade on this line is held.";
    const same = S.slip.legs.find((l) => l.player_key === rec.player.key && l.stat === rec.market.stat && l.period === rec.market.period && l.book === rec.offer.platform && l.event_id === rec.event.id);
    if (same && !(same.side === side && same.line === rec.offer.line)) return `Conflict: ${cap(same.side)} ${same.line} on this prop is already on your ${rec.offer.platform_label} entry.`;
    if (S.slip.legs.length >= MAX_LEGS && !same) return `A slip holds ${MAX_LEGS} picks.`;
    return null;
  }
  function addLeg(rec, side) {
    const why = addRefusal(rec, side);
    if (why) { say(why); return false; }
    S.slip.legs.push(makeLeg(rec, side));
    slipChanged();
    say(`Added ${rec.player.name} ${cap(side)} ${rec.offer.line}`);
    return true;
  }
  function removeLeg(id) { S.slip.legs = S.slip.legs.filter((l) => l.id !== id); slipChanged(); }

  /* ---- entries: one tray, one entry per destination platform; sport is a tag on the leg ---- */
  function entries() {
    const m = new Map();
    S.slip.legs.forEach((l) => {
      if (!m.has(l.book)) m.set(l.book, { key: l.book, platform: l.book, label: D.platforms[l.book].label, legs: [] });
      m.get(l.book).legs.push(l);
    });
    const c = freshCheck(), bad = (e) => (c && c.entries[e.key] && c.entries[e.key].ok ? 1 : 0);
    return [...m.values()].sort((a, b) => bad(a) - bad(b));                                   /* entries that need attention first */
  }
  const typeOf = (e) => S.slip.types[e.platform] || D.platforms[e.platform].types[0][0];
  function freshCheck() {
    const c = S.check;
    if (!c || S.checkSig !== sig()) return null;
    const age = (Date.now() - new Date(c.checked_at)) / 1000;
    return age >= -60 && age < c.ttl_s ? c : null;
  }

  /* ---- the server check: on open, on every change, again when it ages. Nothing here decides a pass ---- */
  const askCheck = () => api("recheck", { idem: idem(), legs: S.slip.legs.map(wireLeg), entry_types: Object.fromEntries(entries().map((e) => [e.platform, typeOf(e)])) });
  async function runCheck() {
    if (!S.slip.legs.length || S.checking || S.signedOut) return;
    const before = sig();
    S.checking = true;
    renderSlips();
    try {
      const doc = await askCheck();
      if (sig() === before) { S.check = doc; S.checkSig = before; S.checkErr = null; }
    } catch (e) {
      S.checkErr = e.status === 429 ? "limit" : "down";
    }
    S.checking = false;
    if (sig() !== before) scheduleCheck();
    renderSlips(); renderBoard(); renderPanel();
  }
  const scheduleCheck = debounce(runCheck, CHECK_DEBOUNCE);
  function slipPhase() {
    if (!S.slip.legs.length) return "empty";
    if (S.signedOut) return "signedout";
    if (S.checking && !freshCheck()) return "running";
    if (S.checkErr) return "down";
    return freshCheck() ? "fresh" : "running";
  }
  async function swapLeg(id) {
    await loadBoard();                                         /* the offer to swap to is the server's current one */
    const l = S.slip.legs.find((x) => x.id === id), c = S.check && S.check.legs[id];
    const cur = l && c && c.current ? findOffer(l.book, c.current.row_id) : null;
    if (!cur) { say("That offer has left the board."); return; }
    S.slip.legs = S.slip.legs.map((x) => (x.id === id ? makeLeg(cur, l.side, l.added_at) : x));
    slipChanged();
    say(`Now ${cap(l.side)} ${cur.offer.line} at the current offer.`);
  }

  /* ---- copy and track ---- */
  function entryText(e) {
    const c = S.check, lines = [`${e.label} entry`];
    e.legs.forEach((l, i) => lines.push(`${i + 1}. ${l.player} ${cap(l.side)} ${l.line} ${l.stat_label}, ${l.period_label} (${l.event_label})`));
    lines.push(`Checked ${clock(c.checked_at)}`);
    return lines.join("\n");
  }
  async function copyEntry(key) {                               /* validate first: a refused, failed or timed-out check leaves the clipboard untouched */
    const e = entries().find((x) => x.key === key), before = sig(), mark = focusMark();
    if (!e || S.copyBusy) return;
    S.copyBusy = true;
    renderSlips();
    let c = null;
    try {
      const doc = await askCheck();
      if (sig() === before) { S.check = doc; S.checkSig = before; S.checkErr = null; c = doc.entries[key]; }
    } catch (err) { c = null; }
    S.copyBusy = false;
    renderSlips(); renderBoard(); renderPanel();
    if (!c || !c.ok) { focusBack(mark); say(`The check did not pass. Nothing from the ${e.label} entry is on your clipboard.`); return; }
    const text = entryText(e);
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (err) {
      const t = document.createElement("textarea");
      t.value = text; t.style.cssText = "position:fixed;opacity:0";
      (document.querySelector("dialog[open]") || document.body).appendChild(t); t.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      t.remove();
    }
    focusBack(mark);
    say(ok ? `Copied ${e.label} entry as text` : `Copy failed. Nothing from the ${e.label} entry is on your clipboard.`);
  }
  async function trackEntry(key) {
    const e = entries().find((x) => x.key === key), c = freshCheck();
    if (S.trackBusy[key]) return;
    if (!e || !c || !c.entries[key] || !c.entries[key].trackable) { say("This entry cannot be tracked yet."); return; }
    const k = key + "|" + sig();
    S.trackIdem[k] = S.trackIdem[k] || idem();                  /* a retry of this press gets the same receipts */
    S.trackBusy[key] = true;
    renderSlips();
    const ids = new Set(e.legs.map((l) => l.id)), n = e.legs.length;
    try {
      const doc = await api("track", { idem: S.trackIdem[k], entry_key: key, entry_type: typeOf(e), legs: e.legs.map(wireLeg) });
      S.slip.legs = S.slip.legs.filter((l) => !ids.has(l.id));
      S.trackIdem = {};
      S.check = null;
      saveState({ slip: true });
      S.tracked = { slip_id: doc.slip_id, n, label: e.label, receipt: doc.receipts[0].id };
      say(`${plural(n, "pick")} tracked.`);
      loadMine();
      if (S.slip.legs.length) scheduleCheck();
    } catch (err) {
      if (err.status === 409 && err.doc && err.doc.legs) { S.check = err.doc; S.checkSig = sig(); say("The check at Track found a change. Nothing was tracked."); }
      else if (err.status === 503) say("Scout is busy. Your slip is still here: press Track again.");
      else if (!S.signedOut) say("Track did not finish. Your slip is still here.");
    }
    S.trackBusy[key] = false;
    renderSlips(); renderBoard(); renderPanel(); renderNav();
  }

  /* ---- account: slip, preferences and views save to the member's account ----
     Every device merges three ways against the copy it last synced, so an add or a removal on either device stands. A save that
     fails stays pending and tick sends it again; until the account has it, a local draft keeps the slip for this member. */
  function prefs() { return { chip: S.chip, f: S.f, group: S.group, histN: S.histN, resSport: S.resSport }; }
  const pendingSave = { slip: false, prefs: false, views: false, watch: false };
  let saveGen = 0, saving = false;
  const unsaved = () => pendingSave.slip || pendingSave.prefs || pendingSave.views || pendingSave.watch;
  function statePatch() {
    const p = { rev: S.rev };
    if (pendingSave.slip) p.slip = { legs: S.slip.legs, types: S.slip.types };
    if (pendingSave.prefs) p.prefs = prefs();
    if (pendingSave.views) p.views = S.views;
    if (pendingSave.watch) p.watch = S.watch;
    return p;
  }
  const legIds = (legs) => new Set(legs.map((l) => l.id));
  function mergeLegs(theirs) {
    const t = (theirs || []).filter(cleanLeg), tIds = legIds(t), base = S.syncedLegs;
    const keep = S.slip.legs.filter((l) => tIds.has(l.id) || !base.has(l.id));
    const mIds = legIds(keep);
    t.forEach((l) => { if (!mIds.has(l.id) && !base.has(l.id)) keep.push(l); });
    if (keep.length > MAX_LEGS) {                               /* the account's legs stay; what this device added stops at the limit */
      const over = keep.length - MAX_LEGS;
      keep.sort((a, b) => tIds.has(b.id) - tIds.has(a.id));
      keep.length = MAX_LEGS;
      say(`A slip holds ${MAX_LEGS} picks. ${plural(over, "pick")} from this device did not fit.`);
    }
    S.slip.legs = keep;
    S.syncedLegs = tIds;
    if (keep.length !== t.length || keep.some((l) => !tIds.has(l.id))) pendingSave.slip = true;
  }
  const viewIds = (list) => new Set(list.map((v) => v.id));
  function mergeViews(theirs) {
    const t = (theirs || []).filter(cleanView), tIds = viewIds(t), base = S.syncedViews;
    const keep = S.views.filter((v) => tIds.has(v.id) || !base.has(v.id));
    const mIds = viewIds(keep);
    t.forEach((v) => { if (!mIds.has(v.id) && !base.has(v.id)) keep.push(v); });
    S.views = keep;
    S.syncedViews = tIds;
    if (keep.length !== t.length || keep.some((v) => !tIds.has(v.id))) pendingSave.views = true;
  }
  function mergeWatch(theirs) {
    const t = (theirs || []).filter(cleanWatch), tIds = viewIds(t), base = S.syncedWatch;
    const keep = S.watch.filter((w) => tIds.has(w.id) || !base.has(w.id));
    const mIds = viewIds(keep);
    t.forEach((w) => { if (!mIds.has(w.id) && !base.has(w.id)) keep.push(w); });
    S.watch = keep;
    S.syncedWatch = tIds;
    if (keep.length !== t.length || keep.some((w) => !tIds.has(w.id))) pendingSave.watch = true;
  }
  function adoptRemote(doc) {                                   /* another device moved the account: this device takes its changes, always */
    S.rev = doc.rev || 0;
    if (doc.slip && Array.isArray(doc.slip.legs)) {
      mergeLegs(doc.slip.legs);
      if (isObj(doc.slip.types)) S.slip.types = Object.assign({}, doc.slip.types, pendingSave.slip ? S.slip.types : {});
    }
    if (Array.isArray(doc.views)) mergeViews(doc.views);
    if (Array.isArray(doc.watch)) mergeWatch(doc.watch);
    S.check = S.checkSig === sig() ? S.check : null;
    renderSlips(); renderBoard(); renderPanel();
    if ($("filters-dialog").open) renderFilters();
    if (S.slip.legs.length) scheduleCheck();
  }
  async function pushState(retried, leaving) {
    if (!S.stateReady || !unsaved() || saving || S.signedOut) return;
    saving = true;
    const had = Object.assign({}, pendingSave), gen = saveGen, sentLegs = legIds(S.slip.legs), sentViews = viewIds(S.views), sentWatch = viewIds(S.watch);
    let conflict = false;
    try {
      const doc = await api("state", statePatch(), leaving);
      S.rev = doc.rev;
      if (had.slip) S.syncedLegs = sentLegs;
      if (had.views) S.syncedViews = sentViews;
      if (had.watch) S.syncedWatch = sentWatch;
      if (gen === saveGen) {                                    /* a change made during this request stays pending */
        Object.keys(had).forEach((k) => { if (had[k]) pendingSave[k] = false; });
        if (had.slip) clearDraft();
      }
      S.saveFail = false;
    } catch (e) {
      if (e.status === 409 && e.doc) {
        adoptRemote(e.doc);
        conflict = retried !== true;
        S.saveFail = retried === true;
      } else S.saveFail = true;                                 /* tick sends it again */
    }
    saving = false;
    if (conflict) return pushState(true);
    if (unsaved() && !S.saveFail) saveSoon();
    renderSlips();
  }
  const saveSoon = debounce(pushState, SAVE_DEBOUNCE), prefsSoon = debounce(pushState, PREFS_DEBOUNCE);
  function saveState(what) {
    Object.assign(pendingSave, what);
    saveGen++;
    if (what.slip) saveDraft();
    if (what.slip || what.views || what.watch) saveSoon(); else prefsSoon();
  }
  async function syncState() {                                  /* picks up what another device saved */
    if (!S.stateReady || unsaved() || saving || S.signedOut) return;
    try {
      const doc = await api("state");
      if (!unsaved() && !saving && doc.rev !== S.rev) adoptRemote(doc);
      if (unsaved()) saveSoon();
    } catch (e) { /* the next poll asks again */ }
  }
  const draftKey = () => (S.me && S.me.member ? "scout-workspace-draft:" + S.me.member : null);
  function saveDraft() {
    const k = draftKey();
    if (!k) return;
    try { localStorage.setItem(k, JSON.stringify({ legs: S.slip.legs, types: S.slip.types, base: [...S.syncedLegs], at: Date.now() })); } catch (e) { /* the account copy still saves */ }
  }
  function clearDraft() {
    const k = draftKey();
    try { if (k) localStorage.removeItem(k); } catch (e) { /* nothing to clear */ }
  }
  function readDraft() {
    const k = draftKey();
    try {
      const d = k ? JSON.parse(localStorage.getItem(k) || "null") : null;
      return isObj(d) && Array.isArray(d.legs) && Array.isArray(d.base) && Date.now() - d.at < DRAFT_MAX_MS ? d : null;
    } catch (e) { return null; }
  }
  function applyState(doc) {
    S.rev = doc.rev || 0;
    S.stateReady = true;
    if (doc.slip && Array.isArray(doc.slip.legs)) {
      S.slip = { legs: doc.slip.legs.filter(cleanLeg).slice(0, MAX_LEGS), types: isObj(doc.slip.types) ? doc.slip.types : {} };
      S.syncedLegs = legIds(S.slip.legs);
    }
    if (isObj(doc.prefs)) {
      const p = doc.prefs;
      if (isObj(p.f)) { S.f = Object.assign({}, DEFAULT_F, p.f); if (!SORTS.includes(S.f.sort)) S.f.sort = "start"; S.activeView = null; S.savedDay = true; }
      if (p.chip === "all" || own(D.sports, p.chip)) S.chip = p.chip;
      if (["chance", "stat", "side", "version", "role"].includes(p.group)) S.group = p.group;
      if ([5, 10, 20].includes(p.histN)) S.histN = p.histN;
      if (own(D.sports, p.resSport)) S.resSport = p.resSport;
    }
    if (Array.isArray(doc.views)) { S.views = doc.views.filter(cleanView); S.syncedViews = viewIds(S.views); }
    if (Array.isArray(doc.watch)) { S.watch = doc.watch.filter(cleanWatch); S.syncedWatch = viewIds(S.watch); }
    if (S.chip !== "all" && !sportsOn().includes(S.chip)) S.chip = "all";
    if (!sportsOn().includes(S.resSport)) S.resSport = "cs2";
    rebuild();
  }

  async function loadState() {                                  /* a failed first read must not leave this visit unsaved */
    if (S.stateReady) return;
    try {
      const doc = await api("state"), mine = S.slip.legs;
      applyState(doc);
      const draft = readDraft(), theirs = S.slip.legs;
      if (mine.length || draft) {                               /* added before this read, or saved here and never reached the account */
        S.syncedLegs = new Set(draft ? draft.base : []);
        S.slip.legs = mine.length ? mine : draft.legs.filter(cleanLeg);
        mergeLegs(theirs);
        if (draft && isObj(draft.types)) S.slip.types = Object.assign({}, S.slip.types, draft.types);
      }
      S.saveFail = false;
      if (unsaved()) saveSoon();
      renderSlips(); renderBoard();
      if (S.slip.legs.length) scheduleCheck();
    } catch (e) { S.saveFail = true; renderSlips(); }
  }

  /* ---- loads ---- */
  async function loadSport(k) {
    const b = S.boards[k];
    try {
      const doc = await api(k === "cs2" ? "board" : "board?sport=" + k);
      Object.entries(doc.platforms || {}).forEach(([p, v]) => { if (!own(D.platforms, p)) D.platforms[p] = v; });
      S.boards[k] = { status: "ok", records: doc.records, at: doc.board_at, feeds: doc.feeds, version: doc.version, fails: 0 };
    } catch (e) {
      S.boards[k] = Object.assign({}, b, { status: b.records.length ? "stale" : "unavailable", fails: b.fails + 1 });
    }
  }
  async function loadBoard() {
    await Promise.all(sportsOn().map(loadSport));
    rebuild();
    renderChips(); renderBoard(); renderMatches();
    if (S.panel) { renderPanel(); loadLine(); }
    if (S.wanted) route(false);
    if (S.slip.legs.length && !S.checking) scheduleCheck();
  }
  async function loadMine() {
    try {
      const doc = await api("mine"), same = S.mine.doc && S.mine.doc.rev === doc.rev;     /* older pages stay until the member's picks move */
      S.mine = { status: "ok", doc, older: same ? S.mine.older : [], next: same ? S.mine.next : doc.next_before };
    } catch (e) { S.mine = Object.assign({}, S.mine, { status: S.mine.doc ? "stale" : "unavailable" }); }
    renderPicks(); renderResults(); renderSlipsPage(); renderNav();
  }
  async function loadOlder() {
    if (!S.mine.next) return;
    try {
      const doc = await api(`mine?before=${encodeURIComponent(S.mine.next)}`);
      S.mine.older.push(doc);
      S.mine.next = doc.next_before;
    } catch (e) { say("Older picks did not load. Press again in a moment."); }
    renderPicks(); renderSlipsPage();
  }
  const mineReceipts = () => [S.mine.doc, ...S.mine.older].flatMap((d) => (d ? d.receipts : []));
  const mineSlips = () => [S.mine.doc, ...S.mine.older].flatMap((d) => (d ? d.slips : []));
  async function loadResults(k) {
    const sport = k || S.resSport, cur = S.results[sport];
    if (cur.status === "idle") S.results[sport] = { status: "loading", doc: null };
    try { S.results[sport] = { status: "ok", doc: await api(sport === "cs2" ? "results" : "results?sport=" + sport) }; } catch (e) { S.results[sport] = { status: cur.doc ? "stale" : "unavailable", doc: cur.doc }; }
    renderResults();
  }
  async function loadLine(force) {
    const P = S.panel;
    if (!P) return;
    const row = rows.get(P.id);
    if (!row) return;
    const rec = offerOf(row, P.platform);
    const k = rec.offer.platform + "|" + rec.offer.offer_id, had = S.lines[k];
    if (!force && had && had.doc && Date.now() - had.at < LINE_KEEP) return;
    S.lines[k] = { status: "loading", doc: had && had.doc, at: had ? had.at : 0 };
    try {
      const doc = await api(`line?${rec.sport === "cs2" ? "" : "sport=" + rec.sport + "&"}book=${encodeURIComponent(rec.offer.platform)}&id=${encodeURIComponent(rec.offer.offer_id)}`);
      S.lines[k] = { status: "ok", doc, at: Date.now() };
    } catch (e) {
      S.lines[k] = { status: "unavailable", doc: had && had.doc, at: had ? had.at : 0 };
    }
    renderPanel();
  }
  const lineOf = (rec) => S.lines[rec.offer.platform + "|" + rec.offer.offer_id] || { status: "loading", doc: null };

  /* ---- filters ---- */
  function matches(row, ignoreChip) {
    const r = row.lead, f = S.f, q = S.q.trim().toLowerCase();
    if (!ignoreChip && S.chip !== "all" && r.sport !== S.chip) return false;
    if (S.event && r.event.id !== S.event) return false;
    if (f.watchOnly && !watched(r)) return false;
    if (q && ![r.player.name, r.player.team, r.player.opponent, r.event.label, r.event.competition, D.sports[r.sport]].join(" ").toLowerCase().includes(q)) return false;
    if (f.day !== "all" && dayOf(r.event.start_utc) !== f.day) return false;
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
  const rankChance = (r) => r.scout.p_side || 0;
  function visibleRows() {
    const list = all.filter((r) => matches(r)), off = (r) => (rowFlag(r) ? 1 : 0);
    const by = {
      start: (a, b) => new Date(a.lead.event.start_utc) - new Date(b.lead.event.start_utc) || a.lead.event.id.localeCompare(b.lead.event.id)
        || a.prop.localeCompare(b.prop) || a.lead.offer.line - b.lead.offer.line,
      chance: (a, b) => rankChance(lead(b)) - rankChance(lead(a)),
      player: (a, b) => a.lead.player.name.localeCompare(b.lead.player.name)
    }[S.f.sort];
    return list.sort((a, b) => off(a) - off(b) || by(a, b));
  }
  function setF(k, v) { S.f[k] = v; S.activeView = null; saveState({ prefs: true }); renderBoardAll(); }
  function resetFilters() { S.f = Object.assign({}, DEFAULT_F, { day: "all" }); S.q = ""; S.chip = "all"; S.event = null; $("search").value = ""; S.activeView = null; saveState({ prefs: true }); renderBoardAll(); }

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
  function renderChips() { keepFocus(paintChips); }
  function paintChips() {
    const counts = { all: 0 };
    all.forEach((r) => { if (matches(r, true)) { counts.all++; counts[r.lead.sport] = (counts[r.lead.sport] || 0) + 1; } });
    const on = sportsOn(), list = on.length > 1 ? [["all", "All"], ...on.map((k) => [k, D.sports[k]])] : [["cs2", "CS2"]];    /* a member with one sport has no switch to make */
    $("chips").innerHTML = list.map(([k, v]) => {
      const n = counts[k] || 0, down = k !== "all" && !["ok", "loading"].includes(S.boards[k].status);
      return `<button class="chip" type="button" aria-pressed="${on.length === 1 || S.chip === k}" aria-label="${esc(v)}, ${plural(n, "prop")}" ${act("chip", k)}>${esc(v)}${down ? '<span class="dot"></span>' : ""}<span class="n">${n}</span></button>`;
    }).join("");
    $("chips").classList.toggle("scrolls", $("chips").scrollWidth > $("chips").clientWidth + 1);
    if (S.f.market !== "all" && !marketOpts().some(([v]) => v === S.f.market)) S.f.market = "all";
    options($("f-day"), dayOpts, S.f.day); options($("f-market"), marketOpts(), S.f.market);
    const phone = window.matchMedia("(max-width:760px)").matches;
    const keys = phone ? ["day", "book", "market", "period", "side", "tier", "gapsOnly", "hideOff", "watchOnly", "sort"] : ["book", "period", "side", "tier", "gapsOnly", "hideOff", "watchOnly", "sort"];
    const n = keys.filter((k) => S.f[k] !== DEFAULT_F[k]).length;
    $("more-label").textContent = "Filters" + (n ? ` · ${n}` : "");
  }
  function renderFeedStrips() {
    const notes = [];
    if (S.event) {
      const r = all.find((x) => x.lead.event.id === S.event);
      notes.push(`<div class="strip"><b>${esc(r ? r.lead.event.label : "This match")}</b><button class="btn" type="button" ${act("event-clear")}>All props</button></div>`);
    }
    boardList().forEach(([k, c]) => {
      if (c.status === "stale" && !S.signedOut) notes.push(`<div class="strip"><b>${esc(D.sports[k])} lines as of ${esc(when(c.at))}</b><button class="btn" type="button" ${act("retry-board")}>Retry</button></div>`);
      if (c.status === "ok" && c.feeds) {
        Object.entries(c.feeds).forEach(([p, f]) => {
          if (!["ok", "empty", "loading"].includes(f.state)) notes.push(`<div class="strip"><b>${esc(D.sports[k])} ${esc((D.platforms[p] || {}).label || p)} lines as of ${f.at ? esc(when(f.at)) : "the last read"}</b></div>`);
        });
      }
    });
    $("feed-strips").innerHTML = notes.join("");
  }

  /* ---- render: board ---- */
  function renderHead() {
    const sortBtn = (k, label) => `<button type="button" class="hd-sort" aria-pressed="${S.f.sort === k}" ${act("sort", k)}>${label}${S.f.sort === k ? ICON.down : ""}</button>`;
    keepFocus(() => { $("board-head").innerHTML = `<span class="h-player">${sortBtn("start", "Start")}${sortBtn("player", "Player")}</span><span>Line</span><span>${sortBtn("chance", "Scout")}</span><span>Hit rate</span><span class="h-pp">PrizePicks</span><span class="h-ud">Underdog</span><span class="h-other">Other books</span><span></span>`; });
  }
  function hitCell(n, t, side) {
    const hits = side === "under" ? t.under : t.over, decided = t.n - t.push, rate = decided ? hits / decided : 0;
    return `<span class="hc${rate >= 0.6 ? " hit-up" : ""}" title="${esc(`Last ${t.n} at this line: ${hits} for ${cap(side)}, ${t.push} push`)}"><i>L${n}</i><b>${hits}/${t.n}</b></span>`;
  }
  function histCell(rec) {
    const h = rec.history, t = h.l10, side = rec.scout.side || "over", more = [[5, h.l5], [10, h.l10], [20, h.l20]].filter(([, x]) => x);
    if (more.length > 1) return `<div class="hist cells"><span class="sr">Hit rate at this line for ${cap(side)}</span>${more.map(([n, x]) => hitCell(n, x, side)).join("")}</div>`;
    if (!t) return '<div class="hist"><span class="hist-n none">No L10</span></div>';
    const hits = side === "under" ? t.under : t.over, cells = [];
    for (let i = 0; i < t.n; i++) cells.push(`<i class="${i < hits ? "m-hit" : i < hits + t.push ? "m-push" : ""}"></i>`);
    return `<div class="hist"><span class="meter" aria-hidden="true">${cells.join("")}</span><span class="hist-n"><span class="sr">Last ${t.n}: ${hits} for ${cap(side)}, ${t.push} push</span><span aria-hidden="true"><span class="l10-lab">L10 </span>${hits}/${t.n}</span></span></div>`;
  }
  function best(row, side) {                                    /* the best price of a side, only among prices that compare */
    const offers = row.offers.filter((o) => o.offer[side].offered && decimalOf(o, side) != null);
    if (offers.length < 2) return null;
    return offers.reduce((a, b) => (decimalOf(b, side) > decimalOf(a, side) ? b : a));
  }
  function priceBit(row, o, side) {
    const t = priceText(o, side), offered = o.offer[side].offered, top = best(row, side) === o;
    if (!offered) return `<span class="bp no"><i>${side[0].toUpperCase()}</i>—</span>`;
    return `<span class="bp${top ? " best" : ""}"><i>${side[0].toUpperCase()}</i>${t ? esc(t) : "Listed"}${top ? '<span class="sr"> best price</span>' : ""}</span>`;
  }
  function bookCell(row, pf) {
    let o = row.offers.find((x) => x.offer.platform === pf), other = false;
    if (!o) {
      o = row.siblings.map((s) => s.offers.find((x) => x.offer.platform === pf)).find(Boolean);
      other = !!o;
    }
    if (!o) return '<span class="bc none"><span class="sr">Not listed</span><span aria-hidden="true">—</span></span>';
    const line = other ? `<span class="bl" title="A different line is not the same bet">${esc(o.offer.line)}</span>` : "";
    const src = other ? row.siblings.find((s) => s.offers.includes(o)) : row;
    if (D.platforms[pf].kind === "modifier" && !priceText(o, "over") && !priceText(o, "under")) {   /* PrizePicks sends no price for a side */
      const only = !o.offer.under.offered ? " · Over only" : !o.offer.over.offered ? " · Under only" : "";
      return `<span class="bc${other ? " other" : ""}">${line}<span class="bt">${esc(lineLabel(o))}${only}</span></span>`;
    }
    return `<span class="bc${other ? " other" : ""}">${line}${priceBit(src, o, "over")}${priceBit(src, o, "under")}</span>`;
  }
  function otherCell(row) {
    const o = row.offers.filter((x) => !["prizepicks", "underdog"].includes(x.offer.platform));
    if (!o.length) return '<span class="bc none"><span class="sr">None</span><span aria-hidden="true">—</span></span>';
    return `<span class="bc stack">${o.slice(0, 3).map((x) => `<span class="bo"><b>${esc(D.platforms[x.offer.platform].short)}</b>${priceBit(row, x, "over")}${priceBit(row, x, "under")}</span>`).join("")}${o.length > 3 ? `<span class="bo"><b>+${o.length - 3}</b></span>` : ""}</span>`;
  }
  function renderRow(row, sub) {
    const rec = lead(row), flag = rowFlag(row), s = rec.scout, side = s.side, v = verdict(rec);
    const on = rowLegs(rec), inSlip = on.length > 0;
    const pick = on[0] ? `${on[0].player} ${cap(on[0].side)} ${on[0].line} on ${on[0].platform_label}` : `${rec.player.name} ${cap(side || "over")} ${rec.offer.line} on ${rec.offer.platform_label}`;
    const addBtn = flag ? `<button class="add" type="button" disabled aria-label="${esc(rec.player.name)}: Match started">${ICON.plus}</button>`
      : !side && !inSlip ? `<button class="add" type="button" ${act("row-choose", row.id)} aria-label="Choose a side for ${esc(rec.player.name)}">${ICON.plus}</button>`
        : `<button class="add" type="button" aria-pressed="${inSlip}" ${act("row-add", row.id)} aria-label="${inSlip ? "Remove" : "Add"} ${esc(pick)} ${inSlip ? "from" : "to"} slip">${inSlip ? ICON.check : ICON.plus}</button>`;
    const read = flag ? startWord(rec) : side ? `Scout ${v.text} ${pct(s.p_side)}` : s.p_over != null ? `Over ${pct(s.p_over)}, Under ${pct(s.p_under)}` : "No lean";
    const marketLabel = esc(rec.market.stat_label) + (rec.market.period === "full_game" ? "" : ` · ${esc(rec.market.period_label)}`);
    const est = s.hold ? `<span class="g-est">Research estimate</span>` : "";
    const sub2 = [est, s.projection != null ? `Proj ${esc(tenth(s.projection))}` : "", s.units ? `${esc(s.units)}U` : "", rec.sport === "lol" && s.grade ? "0U" : ""].filter(Boolean).join('<span class="sc-rest"> · </span>');
    const scout = flag ? `<span class="off-note">${startWord(rec)}</span>`
      : !side && s.p_over != null ? `<div class="sc-top sc-both"><span><span class="sr">Over </span><i aria-hidden="true">O</i> <b>${pct(s.p_over)}</b></span><span><span class="sr">Under </span><i aria-hidden="true">U</i> <b>${pct(s.p_under)}</b></span></div><span class="sc-sub">${sub2}</span>`
        : `<div class="sc-top"><b class="pct">${side ? pct(s.p_side) : "--"}</b>${v.html}</div><span class="sc-sub">${sub2}</span>`;
    const sportTag = sportsOn().length > 1 ? `<span class="s-tag">${esc(D.sports[rec.sport])}</span>` : "";
    const star = watched(rec) ? `<span class="w-mark" title="On your watchlist">${ICON.star}<span class="sr">On your watchlist</span></span>` : "";
    const name = sub ? `<span class="pl-name alt">Other line</span>` : `<span class="pl-name">${star}${esc(rec.player.name)}</span><span class="pl-sub">${sportTag}${marketLabel} · ${esc(rec.player.team)}${rec.player.role ? " " + esc(rec.player.role) : ""}</span>`;
    return `<li class="row${flag ? " is-off" : ""}${sub ? " is-sub" : ""}${inSlip ? " on-slip" : ""}" data-row="${esc(row.id)}" data-open="${S.panel && S.panel.id === row.id}">
      <button class="row-open" type="button" ${act("open", row.id)} aria-label="Research ${esc(rec.player.name)}, ${esc(rec.market.stat_label)} ${esc(rec.market.period_label)}, line ${esc(rec.offer.line)}, ${esc(read)}"></button>
      <div class="c-player">${name}</div>
      <div class="c-line"><b>${esc(rec.offer.line)}</b></div>
      <div class="c-scout">${scout}</div>
      <div class="c-hist">${histCell(rec)}</div>
      <div class="c-book c-pp">${bookCell(row, "prizepicks")}</div>
      <div class="c-book c-ud">${bookCell(row, "underdog")}</div>
      <div class="c-book c-other">${otherCell(row)}</div>
      <div class="c-add">${addBtn}</div></li>`;
  }
  function groupHead(r) {
    return `<li class="match-head"><b>${esc(r.event.label)}</b><span>${esc(r.event.competition)}${r.event.best_of ? ` · BO${esc(r.event.best_of)}` : ""}</span><span class="m-time">${esc(until(r.event.start_utc))}</span></li>`;
  }
  function renderList(list) {
    if (S.f.sort !== "start") return list.map((r) => renderRow(r, false)).join("");
    const out = [];
    let ev = null, prop = null;
    list.forEach((r) => {
      if (r.lead.event.id !== ev) { ev = r.lead.event.id; prop = null; out.push(groupHead(r.lead)); }
      out.push(renderRow(r, r.prop === prop));
      prop = r.prop;
    });
    return out.join("");
  }
  const skeletonRows = (n) => `<div aria-busy="true"><p class="sr">Board loading</p>${Array.from({ length: n }, () => '<div class="skel-row"><i></i><i></i><i></i><i></i><i></i></div>').join("")}</div>`;
  function renderBoard() { keepFocus(paintBoard); T.board = boardSig(); }
  function paintBoard() {
    const box = $("board-list"), want = boardList();
    renderFeedStrips();
    if (want.every(([, c]) => c.status === "loading") && !all.length) { box.innerHTML = skeletonRows(10); return; }
    const list = visibleRows();
    $("view-research").toggleAttribute("data-no-other", !list.some((r) => r.offers.some((o) => !["prizepicks", "underdog"].includes(o.offer.platform))));
    const off = want.filter(([, c]) => c.status === "unavailable");
    const down = off.length ? `<div class="state"><h3>${esc(off.map(([k]) => D.sports[k]).join(", "))} board unavailable</h3><p>Scout could not read ${off.length > 1 ? "these boards" : "the board"}. Your slip and tracked picks are safe.</p><button class="btn" type="button" ${act("retry-board")}>Retry</button></div>` : "";
    const loading = want.some(([, c]) => c.status === "loading") ? skeletonRows(4) : "";
    const at = want.map(([, c]) => c.at).filter(Boolean).sort()[0];
    $("board-meta").textContent = `${plural(list.length, "prop")}${at ? ` · Updated ${clock(at)}` : ""}`;
    box.innerHTML = down + loading + (list.length ? `<ol aria-label="Props">${renderList(list)}</ol>`
      : down || loading ? "" : `<div class="empty"><p>No props match these filters.</p><button class="btn" type="button" ${act("reset-filters")}>Show all props</button></div>`);
  }
  function renderBoardAll() { renderChips(); renderHead(); renderBoard(); if ($("filters-dialog").open) renderFilters(); }

  /* ---- render: filters ---- */
  function viewItem(v, mine) {
    return `<li><button class="view-link" type="button" aria-pressed="${S.activeView === v.id}" ${act("view", v.id)}>${esc(v.name)}</button>${mine ? `<button class="view-del" type="button" aria-label="Delete view ${esc(v.name)}" ${act("view-del", v.id)}>${ICON.x}</button>` : ""}</li>`;
  }
  const presets = () => D.presets.filter((v) => v.state.chip === "all" || sportsOn().includes(v.state.chip));
  function applyView(id) {
    const v = [...presets(), ...S.views].find((x) => x.id === id);
    if (!v) return;
    S.f = Object.assign({}, DEFAULT_F, v.state.f); S.chip = v.state.chip === "all" || sportsOn().includes(v.state.chip) ? v.state.chip : "all"; S.q = v.state.q || ""; $("search").value = S.q; S.activeView = id; S.event = null;
    if (S.view !== "research") location.hash = "#/research";
    saveState({ prefs: true });
    renderBoardAll();
    say(`View: ${v.name}`);
  }
  function renderFilters() {
    const seg = (key, label, list) => `<div class="field"><span>${label}</span><div class="seg wide" role="group" aria-label="${label}">${list.map(([v, l]) => `<button type="button" aria-pressed="${S.f[key] === v}" ${act("f-set", key + ":" + v)}>${esc(l)}</button>`).join("")}</div></div>`;
    const sel = (key, label, list) => `<label class="field"><span>${label}</span><select data-f="${key}">${list.map(([v, l]) => `<option value="${esc(v)}"${S.f[key] === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const tog = (key, label) => `<label class="check"><input type="checkbox" data-f="${key}"${S.f[key] ? " checked" : ""}><span>${label}</span></label>`;
    keepFocus(() => {
      $("filters-body").innerHTML = `<h3 class="eyebrow">Saved views</h3><ul class="views-list">${presets().map((v) => viewItem(v, false)).join("")}${S.views.map((v) => viewItem(v, true)).join("")}</ul>
        <form class="save-row" id="save-view"><label class="field"><span class="sr">View name</span><input type="text" id="view-name" maxlength="28" placeholder="Name this view"></label><button class="btn" type="submit">Save</button></form>
        ${seg("sort", "Sort", [["start", "Start"], ["chance", "Chance"], ["player", "Player"]])}${seg("day", "Day", dayOpts)}
        ${seg("side", "Scout side", [["all", "Both"], ["over", "Over"], ["under", "Under"]])}
        ${seg("tier", "Scout read", [["all", "All"], ["graded", "Graded"], ["lean", "Lean"], ["estimate", "Estimate"]])}
        ${seg("period", "Period", [["all", "All"], ["map1", "Map 1"], ["maps12", "Maps 1-2"], ["maps13", "Maps 1-3"], ["full_game", "Game"]])}
        ${sel("book", "Book", bookOpts())}${sel("market", "Market", marketOpts())}${tog("gapsOnly", "Books differ on the line")}${tog("hideOff", "Hide started")}${tog("watchOnly", `Watchlist only (${S.watch.length})`)}
        <button class="btn btn-quiet" type="button" ${act("reset-filters")}>Reset filters</button>`;
    });
    $("filters-done").textContent = `Show ${plural(visibleRows().length, "prop")}`;
  }

  /* ---- render: slip ---- */
  function statusLine(phase) {
    const c = S.check;
    if (phase === "empty") return "";
    if (phase === "running") return "<b>Checking</b>";
    if (phase === "signedout") return "<b>Your session ended.</b> Sign in to check this slip.";
    if (phase === "down") return S.checkErr === "limit" ? "<b>Too many checks this minute.</b> Scout will check again shortly." : "<b>The check did not answer.</b> Track and Copy stay off.";
    const vals = Object.values(c.entries), ready = vals.filter((e) => e.ok).length;
    return `<b>${ready} of ${plural(vals.length, "entry").replace("entrys", "entries")} ready.</b> Checked ${clock(c.checked_at)}.`;
  }
  const openDetails = new Set();                                /* a timed repaint never closes what the member opened */
  const det = (k) => `data-det="${esc(k)}"${openDetails.has(k) ? " open" : ""}`;
  function renderLeg(l, phase, link) {
    const c = phase === "fresh" ? S.check.legs[l.id] : null;
    const problems = c && !c.ok ? `<div class="problems">${c.problems.map((p) => `<p class="problem">${esc(p.text)}${p.swap ? `<br><button class="btn" type="button" ${act("swap-leg", l.id)}>Use current offer</button>` : ""}${p.retry ? `<br><button class="btn" type="button" ${act("check-now")}>Check again</button>` : ""}</p>`).join("")}</div>` : "";
    const against = l.shown_label.startsWith("Scout side");
    const read = against ? esc(l.shown_label) : `${esc(l.shown_label)} ${pct(l.shown_p)}`;
    return `<div class="leg${c && !c.ok ? " bad" : ""}">
      <span class="leg-name">${esc(l.player)}<span class="s-tag">${esc(D.sports[l.sport])}</span></span>
      <span class="leg-pick">${cap(l.side)} <b>${esc(l.line)}</b> ${esc(l.stat_label)} · ${esc(l.period_label)}</span>
      <span class="leg-meta">${read}${/^https:\/\//.test(link || "") ? ` · <a href="${esc(link)}" target="_blank" rel="noopener">Open this line</a>` : ""}</span>
      <button class="leg-x" type="button" aria-label="Remove ${esc(l.player)} ${cap(l.side)} ${esc(l.line)}" ${act("leg-remove", l.id)}>${ICON.x}</button>${problems}</div>`;
  }
  function typeSeg(e) {
    const types = D.platforms[e.platform].types, cur = typeOf(e);
    return `<div class="seg small" role="group" aria-label="${esc(e.label)} entry type">${types.map(([v, l]) => `<button type="button" aria-pressed="${cur === v}" ${act("entry-type", e.platform + ":" + v)}>${l}</button>`).join("")}</div>`;
  }
  function moneyLines(c) {
    if (!c) return "";
    const ev = c.ev || {}, out = [];
    if (ev.payout) out.push(`<span class="pay">Pays ${esc(ev.payout.multiple)}x if every pick hits, on Underdog's ${esc(ev.payout.entry_type)} table</span>`);
    if (ev.available) out.push(`<span class="pay">Hit chance ${pct(ev.hit_chance)} · EV ${ev.ev >= 0 ? "+" : ""}${(ev.ev * 100).toFixed(1)}%</span>`);
    else if (ev.reason) out.push(`<span class="ev-why">EV: ${esc(ev.reason)}</span>`);
    return out.join("");
  }
  function ruleList(c) {
    if (!c || !c.rules || !c.rules.length) return "";
    return `<details class="checks" ${det("rules:" + c.platform)}><summary>Platform rules checked</summary><ul>${c.rules.map((r) => `<li><span>${esc(r.label)}: ${esc(r.text)}.${r.note ? ` ${esc(r.note)}` : ""}${r.source ? ` <a href="${esc(r.source)}" target="_blank" rel="noopener">Source</a>${r.page_date ? `, ${esc(shortDate(r.page_date))}` : ""}` : ""}</span></li>`).join("")}</ul></details>`;
  }
  let goldUsed = false;
  function primaryClass() { if (goldUsed) return "btn"; goldUsed = true; return "btn btn-gold"; }
  function renderEntry(e, phase) {
    const c = phase === "fresh" ? S.check.entries[e.key] : null, ready = !!(c && c.ok), can = !!(c && c.trackable), busy = !!S.trackBusy[e.key];
    const entryP = c && c.problems.length ? `<div class="problems" style="margin:0">${c.problems.map((p) => `<p class="problem">${esc(p.text)}</p>`).join("")}</div>` : "";
    const open = c && c.open ? `<a class="${primaryClass()} btn-block" href="${esc(c.open.url)}" target="_blank" rel="noopener">${esc(c.open.label)}</a>` : "";
    const trackClass = !open && can ? primaryClass() : "btn";
    const sports = [...new Set(e.legs.map((l) => D.sports[l.sport]))].join(" + ");
    return `<section class="entry${ready ? " ready" : ""}${c && !c.ok ? " refused" : ""}" aria-label="${esc(e.label)} entry">
      <header class="entry-head"><span>${esc(e.label)}<small>${esc(sports)} · ${plural(e.legs.length, "pick")}</small></span>${typeSeg(e)}</header>
      ${e.legs.map((l, i) => renderLeg(l, phase, c && c.open && c.open.legs ? c.open.legs[i] : null)).join("")}
      <div class="entry-foot">${entryP}${moneyLines(c)}${ruleList(c)}${open}
        <div class="entry-acts"><button class="${trackClass}" type="button" ${can && !busy ? "" : "disabled"} ${act("track", e.key)}>${busy ? "Tracking" : "Track"}</button><button class="btn" type="button" ${ready ? "" : "disabled"} ${act("copy", e.key)}>${S.copyBusy ? "Checking" : "Copy"}</button></div>
      </div></section>`;
  }
  function renderChecks(phase) {
    if (phase !== "fresh") return "";
    const c = S.check;
    return `<details class="checks" ${det("checks")}><summary>Check details</summary><ul>${CHECKS.map((t, i) => `<li><span class="ck${c.fails[i] ? " no" : ""}">${c.fails[i] ? "Refused" : "Pass"}</span><span>${t}${c.fails[i] ? ` (${c.fails[i]})` : ""}</span></li>`).join("")}</ul></details>`;
  }
  function slipHtml(mode) {
    const n = S.slip.legs.length, phase = slipPhase();
    const titleTag = mode === "rail" ? `<h2 id="rail-h">Your slip</h2>` : mode === "page" ? `<h2>Your slip</h2>` : "";
    const head = `<div class="slip-head">${titleTag}<span class="slip-count" tabindex="-1" data-slip-head data-fk="slip-head">${plural(n, "pick")}</span>${n ? `<button class="link-btn" type="button" ${act("slip-clear")}>Clear</button>` : ""}</div>`;
    const done = S.tracked ? `<div class="tracked"><b>${plural(S.tracked.n, "pick")} tracked on ${esc(S.tracked.label)}.</b> <a href="#/picks/${esc(S.tracked.receipt)}">View receipts</a></div>` : "";
    const saved = S.saveFail ? '<p class="slip-status">Your changes will save when the connection returns.</p>' : "";
    if (!n) return `<div class="slip">${head}${done}${saved}<div class="empty"><p>Add a prop from the board to start a slip.</p></div></div>`;
    const retry = phase === "down" ? `<button class="link-btn" type="button" ${act("check-now")}>Retry</button>` : phase === "fresh" ? `<button class="link-btn" type="button" ${act("check-now")}>Check again</button>` : "";
    goldUsed = false;
    const body = phase === "running" && !S.check ? '<div class="skel-line"></div><div class="skel-line"></div>' : "";
    return `<div class="slip">${head}${done}<p class="slip-status">${statusLine(phase)} ${retry}</p>${saved}
      <div class="slip-body">${body}${renderChecks(phase)}${entries().map((e) => renderEntry(e, phase)).join("")}</div></div>`;
  }

  /* ---- focus: a re-render never drops the keyboard user. Same control in the same region, else that region's next stop ---- */
  function focusMark(el) {
    const a = el || document.activeElement;
    if (!a || a === document.body || !a.dataset) return null;
    if (!a.dataset.fk && !a.id) return null;
    const box = a.closest(SCOPES);
    return { k: a.dataset.fk || null, id: a.id || null, scope: box ? box.id : null };
  }
  function regionStop(id) {
    const box = id && $(id);
    if (!box) return null;
    if (id === "feed-strips" || id === "board-list" || id === "board-head") return $("board-list");
    if (id === "panel") return $("panel-name");
    if (id === "chips") return box.querySelector(".chip");
    if (id === "filters-body") return box.querySelector(".view-link");
    if (id === "view-picks" || id === "view-results") return box.querySelector("h1");
    return box.querySelector('[data-act="track"]:not(:disabled)') || (id === "view-slips" ? $("slips-h") : box.querySelector("[data-slip-head]"));
  }
  function focusBack(mark) {
    if (!mark) return;
    const a = document.activeElement;
    if (a && a !== document.body) return;                     /* focus landed somewhere on purpose */
    const box = mark.scope ? $(mark.scope) : document;
    let n = mark.k ? box.querySelector(`[data-fk="${CSS.escape(mark.k)}"]`) : mark.id ? $(mark.id) : null;
    if (!n || n.disabled) n = regionStop(mark.scope);
    if (n) n.focus({ preventScroll: true });
  }
  function keepFocus(fn) { const m = focusMark(); fn(); focusBack(m); }
  function renderSlips() {
    keepFocus(() => {
      const scroller = document.querySelector("#rail-body .slip-body"), top = scroller ? scroller.scrollTop : 0;
      $("rail-body").innerHTML = slipHtml("rail");
      const again = document.querySelector("#rail-body .slip-body");
      if (again) again.scrollTop = top;
      if ($("slip-dialog").open) $("sheet-body").innerHTML = slipHtml("sheet");
      paintSlipsPage();
      const n = S.slip.legs.length, c = freshCheck(), vals = c ? Object.values(c.entries) : [];
      const ready = vals.filter((e) => e.ok).length;
      $("slip-fab").textContent = `Slip · ${plural(n, "pick")}${vals.length ? ` · ${ready} of ${vals.length} ready` : ""}`;
      $("slip-fab").hidden = !n || S.view === "slips";
      document.querySelectorAll("[data-mini-slip]").forEach((b) => { b.textContent = `Slip · ${n}`; });
      renderNav();
    });
    T.slip = slipSig();
  }
  function renderSlipsPage() { keepFocus(paintSlipsPage); }
  const countsText = (c) => Object.entries(c || {}).filter(([, n]) => n).map(([k, n]) => `${n} ${k === "void" ? "void" : k === "pending" ? "pending" : STATUS[k].toLowerCase()}`).join(" · ");
  function paintSlipsPage() {
    const doc = S.mine.doc, receipts = mineReceipts(), past = doc ? mineSlips().map((t) => {
      const legs = receipts.filter((r) => r.slip_id === t.slip_id), first = legs[0];
      return `<div class="pick-card"><a class="pick-head slip-link" href="#/picks/${esc(first ? first.id : "")}"><span><b>${esc((D.platforms[t.platform] || {}).label || t.platform)} · ${plural(t.legs, "pick")}</b><small>${esc(legs.map((l) => `${l.player} ${cap(l.side)} ${l.line}`).join(", "))}</small></span>
        <span class="slip-res"><span>${esc(countsText(t.counts))}</span><small>${esc(when(t.tracked_at))}</small></span></a></div>`;
    }).join("") : "";
    const none = S.mine.status === "unavailable" ? '<p class="ln-note">Tracked slips are unavailable right now.</p>' : S.mine.status === "loading" ? '<div class="skel-line"></div>' : '<p class="ln-note">No tracked slips yet.</p>';
    $("view-slips").innerHTML = `<div class="page-h"><div><h1 id="slips-h" tabindex="-1">Slips</h1><p>One tray across sports, split into one entry per platform.</p></div></div>
      <div class="slips-grid"><div class="slip-page">${slipHtml("page")}</div>
      <div class="res-sec first"><h2>Tracked slips</h2>${past || none}${olderBtn()}</div></div>`;
  }
  const olderBtn = () => (S.mine.next ? `<button class="btn btn-quiet" type="button" ${act("mine-older")}>Show older picks</button>` : "");
  function renderNav() {
    const pending = S.mine.doc ? S.mine.doc.record.pending : 0;
    const counts = { slips: S.slip.legs.length, picks: pending };
    document.querySelectorAll(".nav-list [data-nav]").forEach((a) => {
      const k = a.dataset.nav;
      a.querySelector(".nav-count")?.remove();
      if (counts[k]) a.insertAdjacentHTML("beforeend", `<span class="nav-count"><span class="sr">${counts[k]} ${k === "slips" ? "on slip" : "pending"}</span><span aria-hidden="true">${counts[k]}</span></span>`);
    });
    document.querySelectorAll("[data-nav]").forEach((a) => {
      if (a.dataset.nav === S.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  /* ---- render: research panel ---- */
  function seriesOf(rec) {
    const d = lineOf(rec).doc;
    return d && d.history ? d.history.series : null;
  }
  function tallyOf(rec, n) {                                    /* past games at this line when they are here, else the board's own count */
    const res = seriesOf(rec), line = rec.offer.line;
    if (!res) return rec.history["l" + n] || null;
    const list = res.slice(0, n);
    if (list.length < Math.min(n, 5) || (n === 20 && list.length <= 10)) return rec.history["l" + n] || null;
    return { over: list.filter((g) => g.value > line).length, under: list.filter((g) => g.value < line).length, push: list.filter((g) => g.value === line).length, n: list.length };
  }
  function secStrip(rec, side) {
    const s = rec.scout;
    const ln = lineOf(rec).doc, m = ln && ln.market && ln.market.available ? (side === "under" ? ln.market.p_under : ln.market.p_over) : null;
    const tile = (val, label, extra) => `<div class="hit-tile${extra || ""}"><b>${val}</b><span>${label}</span></div>`;
    const windows = [5, 10, 20].map((n) => [n, tallyOf(rec, n)]).filter(([, t]) => t);
    const hitTiles = windows.length ? windows.map(([, t]) => tile(`${side === "under" ? t.under : t.over}/${t.n}`, `Hit rate, last ${t.n}, ${cap(side)}`)).join("") : tile("--", "No past results at this line");
    return `<section class="sec strip-sec" aria-label="Key numbers"><div class="hit-strip">
      ${hitTiles}
      ${tile(chanceOf(rec, side) != null ? pct(chanceOf(rec, side)) : "--", `Forecast chance, ${cap(side)}`, " gold")}
      ${m != null ? tile(pct(m), `Market chance, ${cap(side)}`) : ""}
      ${tile(s.projection != null ? esc(tenth(s.projection)) : "--", "Projection")}
      ${rec.history.avg != null ? tile(esc(rec.history.avg), "Recent average") : ""}</div></section>`;
  }
  function secChart(rec, side, full) {
    const res = seriesOf(rec), t = rec.history.l10;
    if (!res) {
      if (lineOf(rec).status === "loading") return '<section class="sec" aria-label="Past results"><div class="skel-chart"></div></section>';
      const text = t ? `Last ${t.n} at this line: ${t.over} over, ${t.under} under, ${t.push} push.` : "No past results at this line.";
      return `<section class="sec" aria-label="Past results"><div class="sec-h"><h3 class="eyebrow">Past results</h3></div><p class="ln-note">${esc(text)}</p></section>`;
    }
    const n = full ? S.histN : 10, list = res.slice(0, n), line = rec.offer.line;
    const hits = list.filter((g) => g.value !== line && (side === "over" ? g.value > line : g.value < line)).length, pushes = list.filter((g) => g.value === line).length;
    const seg = `<div class="seg small" role="group" aria-label="Games shown">${[5, 10, 20].filter((v) => v <= Math.max(res.length, 5)).map((v) => `<button type="button" aria-pressed="${S.histN === v}" ${act("hist-n", v)}>L${v}</button>`).join("")}</div>`;
    const table = full ? `<details class="tbl"><summary>Show results as a table</summary><table class="dt"><thead><tr><th>Played</th><th>Opponent</th><th>Final</th><th>Against ${esc(line)}</th></tr></thead><tbody>
      ${list.map((g) => `<tr><td>${esc(shortDate(g.date))}</td><td>${esc(g.opponent)}</td><td class="num">${esc(g.value)}</td><td>${g.value === line ? "Push" : g.value > line ? "Over" : "Under"}</td></tr>`).join("")}</tbody></table></details>` : "";
    return `<section class="sec" aria-label="Past results"><div class="sec-h"><h3 class="eyebrow">Past results at this line</h3>${full ? seg : `<button class="link-btn" type="button" ${act("p-tab", "history")}>Open history</button>`}</div>
      <div class="chart" data-chart data-n="${n}" role="img" aria-label="${list.length} past results against the line ${esc(line)}: ${hits} hits for ${cap(side)}, ${pushes} ${pushes === 1 ? "push" : "pushes"}."></div>
      <div class="legend"><span><i class="h"></i>Hit for ${cap(side)}</span><span><i></i>Miss</span><span><i class="p"></i>Push</span><span><i class="l"></i>Line ${esc(line)}</span></div>${table}</section>`;
  }
  function secMaps(rec) {                                       /* CS2: each map of the last 20 series, from the same rows as the chart */
    const res = seriesOf(rec), by = new Map();
    (res || []).forEach((g) => (g.maps || []).forEach((name, i) => {
      if (!name || !g.values || g.values[i] == null) return;
      const e = by.get(name) || { n: 0, t: 0 };
      e.n++; e.t += g.values[i];
      by.set(name, e);
    }));
    if (!by.size) return "";
    const list = [...by.entries()].sort((a, b) => b[1].n - a[1].n);
    return `<section class="sec" aria-label="By map"><div class="sec-h"><h3 class="eyebrow">By map, last ${res.length} series</h3></div>
      <table class="dt"><thead><tr><th>Map</th><th>Played</th><th>Average per map</th></tr></thead><tbody>${list.map(([name, e]) => `<tr><td>${esc(name)}</td><td class="num">${e.n}</td><td class="num">${(e.t / e.n).toFixed(1)}</td></tr>`).join("")}</tbody></table></section>`;
  }
  function secSides(row, rec, side) {
    const sideBtn = (sd) => {
      const v = rec.offer[sd], price = priceText(rec, sd);
      return `<button class="side-btn" type="button" aria-pressed="${side === sd}" ${v.offered ? "" : "disabled"} ${act("p-side", sd)}><b>${cap(sd)} ${esc(rec.offer.line)}</b><span class="side-p">${chanceOf(rec, sd) != null ? pct(chanceOf(rec, sd)) : "No saved chance"}</span><small>${v.offered ? esc(price || lineLabel(rec)) : "Not offered on " + esc(rec.offer.platform_label)}</small></button>`;
    };
    const books = row.offers.length > 1 ? `<div class="book-pick" role="group" aria-label="Book at this line">${row.offers.map((o) => `<button class="book-opt" type="button" aria-pressed="${o === rec}" ${act("p-book", o.offer.platform)}>${esc(o.offer.platform_label)}</button>`).join("")}</div>` : "";
    return `<section class="sec" aria-label="Sides"><div class="sides">${sideBtn("over")}${sideBtn("under")}</div>${books}</section>`;
  }
  function secForecast(rec, side) {
    const s = rec.scout, v = verdict(rec, side), p = chanceOf(rec, side), val = s.validation || {};
    const off = s.side && side !== s.side && p == null;
    const stake = s.hold ? "None" : `${s.units == null ? 0 : s.units}U`;
    const reason = s.stake_reason ? `<p class="consensus">${esc(s.stake_reason)}</p>` : "";
    const detail = val.detail ? `<p class="consensus">${esc(val.detail)}</p>` : "";
    const label = val.label ? `<span class="tag">${esc(val.label)}</span>` : "";
    return `<section class="sec" aria-label="Scout forecast"><div class="sec-h"><h3 class="eyebrow">Scout forecast</h3>${label}</div>
      <div class="forecast"><div class="fc-top"><div class="fc-num">${off ? "--" : pct(p)}</div><div class="fc-lab">${off ? `Scout saved a chance for ${cap(s.side)} only` : `Forecast chance of ${cap(side)} ${esc(rec.offer.line)}`}<br>${v.html} · Stake ${esc(stake)}</div></div>${s.range80 ? `<p class="consensus">80% range ${esc(s.range80[0])} to ${esc(s.range80[1])}</p>` : ""}${reason}${detail}</div></section>`;
  }
  function secDetails(rec) {
    const ln = lineOf(rec).doc, f = ln && ln.forecast;
    const kv = [["Model", rec.scout.model_version || "--"], ["Forecast saved", f && f.logged_at ? when(f.logged_at) : rec.scout.forecast_at ? when(rec.scout.forecast_at) : "--"], ...(f && f.p_saved != null ? [["Saved chance", pct(f.p_saved)]] : [])];
    return `<details class="tbl details"><summary>Details</summary><dl class="kv">${kv.map(([k, val]) => `<div><dt>${k}</dt><dd>${esc(val)}</dd></div>`).join("")}</dl></details>`;
  }
  function priceCell(o, side) { return priceText(o, side) || (o.offer[side].offered ? esc(lineLabel(o)) : "Not offered"); }
  function bookTable(list, row) {
    return `<table class="dt"><thead><tr><th style="width:30%">Book</th><th>Over</th><th>Under</th><th>Updated</th></tr></thead><tbody>${list.map((o) => {
      const top = (sd) => row && best(row, sd) === o;
      return `<tr><td>${esc(o.offer.platform_label)}</td><td class="num${top("over") ? " best" : ""}">${priceCell(o, "over")}</td><td class="num${top("under") ? " best" : ""}">${priceCell(o, "under")}</td><td>${o.offer.seen_at ? esc(when(o.offer.seen_at)) : "--"}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function secBooks(row, rec) {
    const others = row.siblings.map((s) => {
      const v = verdict(s.lead), sp = s.lead.scout.p_side, line = esc(s.lead.offer.line);
      return `<div class="ln-group other"><div class="ln-title"><span><b>${line}</b> <span class="tag">Different line</span></span><button class="link-btn" type="button" ${act("p-line", s.id)}>Open ${line}</button></div>
        <p class="ln-note">Not the same bet as ${esc(rec.offer.line)}. Its own forecast: ${esc(v.text)} ${sp == null ? "" : pct(sp)}.</p>${bookTable(s.offers, s)}</div>`;
    }).join("");
    const ln = lineOf(rec).doc;
    const market = ln && ln.market ? (ln.market.available ? `<p class="consensus">Market chance from ${esc(ln.market.source)}: Over ${pct(ln.market.p_over)}, Under ${pct(ln.market.p_under)}.</p>` : `<p class="consensus">${esc(ln.market.reason)}</p>`) : "";
    const mb = (ln && ln.market_books) || null, price = (v) => (v == null ? "--" : esc(v));
    const mbRow = (b, showLine) => `<tr><td>${esc(b.platform_label)}${showLine ? ` <span class="tag">${esc(b.line)}</span>` : ""}</td><td class="num">${price(b.over)}</td><td class="num">${price(b.under)}</td><td>${b.chance != null ? pct(b.chance) : "--"}</td></tr>`;
    const mbHead = '<thead><tr><th style="width:38%">Book</th><th>Over</th><th>Under</th><th>Its chance</th></tr></thead>';
    const extra = mb ? mb.same.filter((b) => !row.offers.some((o) => o.offer.platform === b.platform)) : [];       /* a book already in the table above is not listed twice */
    const same = extra.length ? `<h4 class="eyebrow sub-h">Market books at ${esc(rec.offer.line)}</h4><table class="dt">${mbHead}<tbody>${extra.map((b) => mbRow(b, false)).join("")}</tbody></table>` : "";
    const diff = mb && mb.other.length ? `<div class="ln-group other"><div class="ln-title"><span><b>Other lines</b> <span class="tag">Different line</span></span></div><p class="ln-note">Not the same bet as ${esc(rec.offer.line)}.</p><table class="dt">${mbHead}<tbody>${mb.other.map((b) => mbRow(b, true)).join("")}</tbody></table></div>` : "";
    return `<section class="sec" aria-label="Book comparison"><div class="sec-h"><h3 class="eyebrow">Books</h3></div>
      <div class="ln-group current"><div class="ln-title"><span><b>${esc(rec.offer.line)}</b></span></div>${bookTable(row.offers, row)}${same}${market}</div>${others}${diff}</section>`;
  }
  function secContext(rec) {
    const ln = lineOf(rec);
    if (!ln.doc) return `<section class="sec" aria-label="Context">${ln.status === "loading" ? '<div class="skel-line"></div><div class="skel-line"></div>' : '<p class="ln-note">Context is unavailable right now.</p>'}</section>`;
    const list = (ln.doc.context && ln.doc.context.rows) || [], drivers = (ln.doc.context && ln.doc.context.drivers) || [];
    const title = rec.sport === "cs2" ? "Inputs saved with this forecast" : "Context";
    const empty = rec.sport === "cs2" ? "This forecast saved no context inputs." : "No context is on file for this line.";
    return `<section class="sec" aria-label="Context"><div class="sec-h"><h3 class="eyebrow">${title}</h3></div>${list.length ? `<dl class="ctx-list">${list.map((r) => `<div><dt>${esc(r.label)}</dt><dd>${esc(r.value)}</dd></div>`).join("")}</dl>` : `<p class="ln-note">${empty}</p>`}${drivers.length ? `<h4 class="eyebrow sub-h">What moved this forecast</h4><ul class="drivers">${drivers.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}</section>`;
  }
  function panelNotice(row, rec) {
    const P = S.panel;
    if (lostBook(row, P)) {                                     /* a move on one book never swaps the book under the member */
      const moved = row.siblings.find((r) => r.offers.some((o) => o.offer.platform === P.platform));
      return `<div class="strip inset"><b>${esc(D.platforms[P.platform].label)} no longer lists ${esc(rec.offer.line)}. Showing ${esc(rec.offer.platform_label)}.</b>${moved ? `<button class="btn" type="button" ${act("p-line", moved.id)}>Open ${esc(moved.lead.offer.line)}</button>` : ""}</div>`;
    }
    if (started(rec)) return `<div class="strip inset"><b>${startWord(rec)} at ${esc(when(rec.event.start_utc))}</b></div>`;
    if (held(rec)) return `<div class="strip inset"><b>The grade on this line is held</b></div>`;
    return "";
  }
  function panelBody(row, rec, side) {
    const P = S.panel, top = panelNotice(row, rec);
    if (P.tab === "history") return top + secChart(rec, side, true) + secMaps(rec);
    if (P.tab === "books") return top + secBooks(row, rec);
    if (P.tab === "context") return top + secContext(rec);
    return top + secStrip(rec, side) + secChart(rec, side, false) + secForecast(rec, side) + secSides(row, rec, side) + secDetails(rec);
  }
  function neighbours() {
    const list = visibleRows(), i = S.panel ? list.findIndex((r) => r.id === S.panel.id) : -1;
    return { list, i, prev: i > 0 ? list[i - 1] : null, next: i >= 0 && i < list.length - 1 ? list[i + 1] : null };
  }
  function renderPanel() {
    const el = $("panel"), P = S.panel;
    if (!P) return;
    const row = rows.get(P.id);
    if (!row) {
      const now = all.filter((r) => r.prop === P.prop).sort((a, b) => a.lead.offer.line - b.lead.offer.line);
      const body = now.length ? `<h3>This line moved</h3><p>${esc(P.line)} is off the board. This prop now lists:</p><div class="book-pick">${now.map((r) => `<button class="book-opt" type="button" ${act("p-line", r.id)}>${esc(r.lead.offer.line)} on ${esc(r.offers.map((o) => o.offer.platform_label).join(" and "))}</button>`).join("")}</div>`
        : "<h3>This line left the board</h3><p>The match started or the platform pulled the line.</p>";
      keepFocus(() => { el.innerHTML = `<header class="p-head"><h2 class="p-name" id="panel-name" tabindex="-1">${now.length ? "Line moved" : "Line gone"}</h2><button class="icon-btn p-close" type="button" aria-label="Close research" ${act("p-close")}>${ICON.x}</button></header><div class="p-body"><div class="state bare">${body}</div></div>`; });
      return;
    }
    const rec = offerOf(row, P.platform), side = P.side;
    const body = el.querySelector(".p-body"), scrollTop = body ? body.scrollTop : 0, tableOpen = !!el.querySelector("details.tbl:not(.details)[open]"), detOpen = !!el.querySelector("details.details[open]");
    const refusal = addRefusal(rec, side), inSlip = legsFor(rec).some((l) => l.side === side);
    const tab = TABS.map((t) => `<button class="tab" type="button" role="tab" id="tab-${t}" aria-controls="p-body" aria-selected="${P.tab === t}" tabindex="${P.tab === t ? 0 : -1}" ${act("p-tab", t)}>${cap(t)}</button>`).join("");
    const picker = row.siblings.length ? `<span class="line-pick" role="group" aria-label="Lines on this prop">${[row, ...row.siblings].sort((a, b) => a.lead.offer.line - b.lead.offer.line).map((r) => `<button type="button" aria-pressed="${r === row}" ${act("p-line", r.id)}>${esc(r.lead.offer.line)}</button>`).join("")}</span>` : `<b class="p-line">${esc(rec.offer.line)}</b>`;
    const nb = neighbours();
    const stepper = `<div class="p-step"><button class="icon-btn" type="button" ${nb.prev ? "" : "disabled"} aria-label="Previous prop" ${act("p-prev")}>${ICON.up}</button><button class="icon-btn" type="button" ${nb.next ? "" : "disabled"} aria-label="Next prop" ${act("p-next")}>${ICON.down}</button></div>`;
    keepFocus(() => {
      el.innerHTML = `<header class="p-head">
        <div class="p-title"><h2 class="p-name" id="panel-name" tabindex="-1">${esc(rec.player.name)}</h2>${picker}<span class="p-stat">${esc(D.sports[rec.sport])} · ${esc(rec.market.stat_label)} · ${esc(rec.market.period_label)} · ${esc(rec.offer.platform_label)}</span></div>
        <div class="p-ctrl"><button class="icon-btn w-btn" type="button" aria-pressed="${watched(rec)}" aria-label="${watched(rec) ? "Remove" : "Add"} ${esc(rec.player.name)} ${watched(rec) ? "from" : "to"} your watchlist" ${act("watch", row.id)}>${ICON.star}</button>${stepper}<button class="icon-btn p-close" type="button" aria-label="Close research" ${act("p-close")}>${ICON.x}</button></div>
        <p class="p-sub">${esc(rec.player.team)}${rec.player.role ? " " + esc(rec.player.role) : ""} · ${esc(rec.event.label)}${rec.event.competition ? " · " + esc(rec.event.competition) : ""} · ${started(rec) ? "Started" : "Starts"} ${esc(when(rec.event.start_utc))}${rec.event.best_of ? ` · BO${esc(rec.event.best_of)}` : ""}${rec.offer.moved_from != null ? ` · Moved from ${esc(rec.offer.moved_from)}` : ""}</p>
      </header>
      <div class="tabs" role="tablist" aria-label="Research sections">${tab}</div>
      <div class="p-body" id="p-body" role="tabpanel" aria-labelledby="tab-${P.tab}" tabindex="0">${panelBody(row, rec, side)}</div>
      <footer class="p-foot">${refusal && !inSlip ? `<p class="add-why" role="status">${esc(refusal)}</p>` : ""}
        <div class="p-foot-row"><button class="btn mini-slip" type="button" data-mini-slip ${act("open-sheet")}>Slip · ${S.slip.legs.length}</button>
        <button class="btn btn-gold" type="button" ${refusal && !inSlip ? 'aria-disabled="true"' : ""} ${act("p-add")}>${inSlip ? "Remove from slip" : `Add ${cap(side)} ${esc(rec.offer.line)} on ${esc(rec.offer.platform_label)}`}</button></div></footer>`;
    });
    const nbody = el.querySelector(".p-body");
    if (nbody) nbody.scrollTop = scrollTop;
    const tbl = tableOpen && el.querySelector("details.tbl:not(.details)");
    if (tbl) tbl.open = true;
    const det = detOpen && el.querySelector("details.details");
    if (det) det.open = true;
    mountCharts(rec, side);
    T.panel = panelSig();
  }

  /* ---- chart: bars for past results, the offer line drawn across them ---- */
  function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), n = x / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
  function chartSvg(results, line, side, w) {
    const data = results.slice().reverse(), h = 176, m = { l: 30, r: 6, t: 22, b: 24 }, pw = w - m.l - m.r, ph = h - m.t - m.b;
    const vals = data.map((d) => d.value), top = Math.max(line, ...vals) * 1.12, y = (v) => m.t + ph - (v / top) * ph, base = m.t + ph;
    const slot = pw / data.length, bw = Math.min(26, Math.max(6, slot * 0.62)), step = niceStep(top / 3);
    let out = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">`;
    for (let t = 0; t <= top; t += step) {
      out += `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${m.l - 6}" y="${y(t) + 3.5}" text-anchor="end">${Math.round(t * 10) / 10}</text>`;
    }
    const labelEvery = Math.ceil(44 / slot), hiV = Math.max(...vals);
    let hiDone = false;
    data.forEach((d, i) => {
      const x = m.l + slot * i + (slot - bw) / 2, yv = y(d.value), push = d.value === line, over = d.value > line;
      const cls = push ? "b-push" : (side === "over" ? over : !over) ? "b-hit" : "b-miss", r = Math.min(4, bw / 2, base - yv);
      if (push) out += `<rect class="b-push" x="${x + .75}" y="${yv}" width="${bw - 1.5}" height="${Math.max(base - yv, 1)}" rx="3"/>`;
      else out += `<path class="${cls}" d="M${x},${base}V${yv + r}Q${x},${yv} ${x + r},${yv}H${x + bw - r}Q${x + bw},${yv} ${x + bw},${yv + r}V${base}Z"/>`;
      const last = i === data.length - 1, hi = d.value === hiV && !hiDone && !last;
      if (hi) hiDone = true;
      if (last || hi) out += `<text x="${x + bw / 2}" y="${yv - 5}" text-anchor="middle">${esc(d.value)}</text>`;
      if ((data.length - 1 - i) % labelEvery === 0) out += `<text x="${x + bw / 2}" y="${h - 8}" text-anchor="middle">${esc(String(d.opponent || "").slice(0, 6))}</text>`;
      const verdictText = push ? "Push" : (side === "over" ? over : !over) ? "Hit" : "Miss";
      out += `<rect class="bar-hit" x="${m.l + slot * i}" y="${m.t - 10}" width="${slot}" height="${ph + 10}" data-tip="${esc(`${shortDate(d.date)} v ${d.opponent}: ${d.value}. ${verdictText}`)}"/>`;
    });
    out += `<line class="line-mark" x1="${m.l}" x2="${w - m.r}" y1="${y(line)}" y2="${y(line)}"/>`;
    out += `<text class="line-lab" x="${w - m.r}" y="${m.t - 10}" text-anchor="end">Line ${esc(line)}</text>`;
    return out + "</svg>";
  }
  function mountCharts(rec, side) {
    const res = seriesOf(rec);
    if (!res) return;
    document.querySelectorAll("#panel [data-chart]").forEach((el) => {
      const n = +el.dataset.n, w = Math.max(240, Math.round(el.clientWidth));
      el.innerHTML = chartSvg(res.slice(0, n), rec.offer.line, side, w);
    });
  }

  /* ---- render: my picks ---- */
  function leadLine(r) {
    if (r.final != null) return `Final ${r.final}, line ${r.line}`;
    if (r.status === "void") return `Void, line ${r.line}`;
    return `Line ${r.line}${r.start_utc ? `, ${new Date(r.start_utc) <= Date.now() ? "started" : "starts"} ${when(r.start_utc)}` : ""}`;
  }
  function timelineHtml(r) {
    return `<ol class="timeline">${r.timeline.map((t) => `<li class="k-${esc(t.kind)}${t.result ? " r-" + esc(t.result) : ""}"><time datetime="${esc(t.at)}">${esc(when(t.at))}</time>${esc(t.text)}</li>`).join("")}</ol>`;
  }
  function pickRow(r) {
    const st = r.status, open = S.openPick === r.id;
    const scoutSide = cap(r.scout_side || ""), against = r.scout_side && r.side !== r.scout_side;      /* Scout's grade and stake belong to Scout's side, never to the member's */
    const read = r.research ? `${r.hold || "Research estimate"} ${scoutSide}`.trim() : `${r.grade || "Lean"} ${scoutSide}`.trim();
    const kv = [["Sport", D.sports[r.sport] || r.sport], ["Chance you saw", r.displayed_p == null ? "None saved for this side" : pct(r.displayed_p)], ["Scout read", read],
      ...(r.research ? [] : [["Scout stake", `${r.units == null ? 0 : r.units}U${scoutSide ? ` on ${scoutSide}` : ""}`]]), ...(against ? [["Your side", `${cap(r.side)}, against Scout`]] : []),
      ["Projection", r.projection == null ? "--" : tenth(r.projection)], ["Book", r.platform_label]];
    const saved = r.forecast_p == null || r.research ? [] : [["Saved forecast", `${r.forecast_grade === "LEAN" || !r.forecast_grade ? "Lean" : r.forecast_grade} ${scoutSide} ${pct(r.forecast_p)}, ${r.forecast_units == null ? 0 : r.forecast_units}U`]];
    const ids = [...saved, ["Offer", r.offer_revision], ["Forecast", r.forecast_id], ["Model", r.model_version], ["Tracked", stamp(r.tracked_at)], ["Receipt", r.id]];
    const body = open ? `<div class="pick-body" id="pick-${esc(r.id)}"><dl class="kv">${kv.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      <h3 class="eyebrow" style="margin-top:14px">Timeline</h3>${timelineHtml(r)}
      <details class="tbl details"><summary>Details</summary><dl class="kv">${ids.map(([k, v]) => `<div><dt>${k}</dt><dd class="mono">${esc(v)}</dd></div>`).join("")}</dl></details></div>` : "";
    return `<article class="pick-row"><button class="pick-head" type="button" aria-expanded="${open}" aria-controls="pick-${esc(r.id)}" ${act("pick", r.id)}>
      <span><b>${esc(r.player)} ${cap(r.side)} ${esc(r.line)}</b><small>${esc(D.sports[r.sport] || "")} · ${esc(r.stat_label)} · ${esc(r.period_label)} · ${esc(r.matchup)}</small></span>
      <span class="pick-final">${esc(leadLine(r))}</span><span class="status ${st}">${STATUS[st]}${r.corrected ? '<small>Corrected</small>' : ""}</span>${ICON.down}</button>${body}</article>`;
  }
  const picksShow = (r) => (S.picksSport === "all" || r.sport === S.picksSport) && (S.picksFilter === "all" || (S.picksFilter === "pending" ? r.status === "pending" : r.status !== "pending"));
  function summary(rec) {
    const rate = rec.hit_rate == null ? "--" : pct(rec.hit_rate);
    return `<div class="rec-strip"><div><b>${rec.hit}-${rec.miss}-${rec.push}</b><span>Hit, miss, push</span></div><div><b>${rate}</b><span>Hit rate, ${rec.decided} decided</span></div><div><b>${rec.void}</b><span>Void</span></div><div><b>${rec.pending}</b><span>Pending</span></div><div><b>${rec.matches}</b><span>Matches</span></div></div>`;
  }
  function renderPicks() {
    const box = $("view-picks"), doc = S.mine.doc;
    const seg = `<div class="seg" role="group" aria-label="Show picks">${[["all", "All"], ["pending", "Pending"], ["settled", "Settled"]].map(([k, l]) => `<button type="button" aria-pressed="${S.picksFilter === k}" ${act("picks-filter", k)}>${l}</button>`).join("")}</div>`;
    const sportSeg = sportsOn().length > 1 ? `<div class="seg" role="group" aria-label="Show sport">${[["all", "All"], ...sportsOn().map((k) => [k, D.sports[k]])].map(([k, l]) => `<button type="button" aria-pressed="${S.picksSport === k}" ${act("picks-sport", k)}>${l}</button>`).join("")}</div>` : "";
    const head = `<div class="page-h"><div><h1 id="picks-h" tabindex="-1">My picks</h1><p>Each receipt keeps what you saw when you tracked it. Corrections are added below, never written over.</p></div><div class="page-acts">${sportSeg}${seg}</div></div>`;
    let inner;
    if (!doc) inner = S.mine.status === "unavailable" ? `<div class="state"><h3>My picks unavailable</h3><p>Your receipts are saved. Scout could not read them just now.</p><button class="btn" type="button" ${act("retry-mine")}>Retry</button></div>` : Array.from({ length: 4 }, () => '<div class="skel-line tall"></div>').join("");
    else {
      const stale = S.mine.status === "stale" ? `<div class="strip inset"><b>Saved copy from ${esc(when(doc.as_of))}</b><button class="btn" type="button" ${act("retry-mine")}>Retry</button></div>` : "";
      const groups = [], receipts = mineReceipts();
      mineSlips().forEach((t) => {
        const legs = receipts.filter((r) => r.slip_id === t.slip_id && picksShow(r));
        if (legs.length) groups.push(`<section class="pick-card"><header class="slip-h"><b>${esc((D.platforms[t.platform] || {}).label || t.platform)}${t.entry_type ? ` · ${esc(cap(t.entry_type))}` : ""}</b><span>${esc(when(t.tracked_at))}</span><span>${esc(countsText(t.counts))}</span></header>${legs.map(pickRow).join("")}</section>`);
      });
      inner = stale + summary(doc.record) + (groups.length ? groups.join("") : '<div class="empty"><p>No picks in this view.</p></div>') + olderBtn();
    }
    keepFocus(() => { box.innerHTML = head + inner; });
  }

  /* ---- render: results. Separate populations, never totalled together ---- */
  const GROUPS = { chance: ["by_chance", "band", "Chance band"], stat: ["by_stat", null, "Stat"], side: ["by_side", "side", "Side"], version: ["by_version", "version", "Version"] };
  function dumb(st, ob) {
    const X = (v) => Math.max(0, Math.min(100, (v - 0.4) / 0.3 * 100)), left = Math.min(X(ob), X(st)), width = Math.abs(X(ob) - X(st));
    return `<span class="dumb" aria-hidden="true"><span class="tick" style="left:${X(0.5)}%"></span><span class="bar" style="left:${left}%;width:${width}%"></span><span class="st" style="left:${X(st)}%"></span><span class="ob" style="left:${X(ob)}%"></span></span>`;
  }
  function resRow(name, x) {
    const gap = (x.observed - x.printed) * 100;
    return `<tr><td>${esc(name)}</td><td data-l="Picks">${x.picks}</td><td data-l="Won-lost">${x.wins}-${x.losses}</td><td data-l="Forecast chance">${pct(x.printed)}</td><td data-l="Hit rate">${pct(x.observed)}</td><td data-l="Gap">${gap >= 0 ? "+" : ""}${gap.toFixed(1)} pts</td><td class="w" data-l="Forecast v hit rate, 40% to 70%">${dumb(x.printed, x.observed)}</td></tr>`;
  }
  function boardRecord() {
    const seg = `<div class="seg" role="group" aria-label="Group results by">${Object.entries(GROUPS).map(([k, g]) => `<button type="button" aria-pressed="${S.group === k}" ${act("group", k)}>${g[2]}</button>`).join("")}</div>`;
    const R = S.results.cs2.doc;
    if (!R) return S.results.cs2.status === "unavailable" ? `<div class="state"><h3>Scout board record unavailable</h3><p>Scout could not read the record just now.</p><button class="btn" type="button" ${act("retry-results")}>Retry</button></div>` : '<div class="skel-line tall"></div><div class="skel-line tall"></div>';
    if (!R.picks || !R.picks.picks) return `<p class="ln-note">No settled picks meet this rule yet.</p><p class="ln-note">${esc(R.rule)}.</p><p class="ln-note">${esc(R.definition)}</p>`;
    const p = R.picks, g = GROUPS[S.group], rowsOut = (R[g[0]] || []).filter((x) => x.picks).map((x) => resRow(g[1] ? x[g[1]] : `${x.stat} ${x.scope}`, x)).join("");
    const ci = p.observed_ci ? `95% interval ${pct(p.observed_ci[0])} to ${pct(p.observed_ci[1])}, whole matches resampled.` : "";
    const stale = S.results.cs2.status === "stale" ? '<p class="ln-note">Saved copy.</p>' : "";
    const asOf = R.as_of ? `As of ${esc(when(R.as_of))}.` : "";
    return `${stale}<div class="res-top"><div class="rec-strip"><div><b>${p.wins}-${p.losses}</b><span>Won-lost, ${p.pushes === 1 ? "1 push" : `${p.pushes} pushes`} apart</span></div><div><b>${pct(p.observed)}</b><span>Hit rate, settled picks</span></div><div><b class="gold-t">${pct(p.printed)}</b><span>Forecast chance, average</span></div><div><b>${p.matches}</b><span>Matches</span></div></div></div>
      <p class="ln-note">${esc(R.rule)}. ${ci} ${asOf}</p><p class="ln-note">${esc(R.definition)}</p>
      <div class="res-bar">${seg}<div class="res-legend"><span><i class="st"></i>Forecast chance</span><span><i class="ob"></i>Hit rate</span></div></div>
      <table class="res-tbl"><thead><tr><th>${g[2]}</th><th>Picks</th><th>Won-lost</th><th>Forecast chance</th><th>Hit rate</th><th>Gap</th><th style="width:24%">Forecast v hit rate, 40% to 70%</th></tr></thead><tbody>${rowsOut}</tbody></table>
      <p class="ln-note" style="margin-top:8px">${esc(R.note)} Market chance is a third number: Research shows it for a line when both Underdog prices are present.</p>`;
  }
  function yourRecord() {
    const doc = S.mine.doc;
    if (!doc) return S.mine.status === "unavailable" ? `<p class="ln-note">Your record is unavailable right now.</p>` : '<div class="skel-line tall"></div>';
    const r = doc.record;
    const groups = doc.by_platform.map((g) => `<li>${esc(g.name)}: ${g.hit}-${g.miss}-${g.push}${g.hit_rate == null ? "" : `, ${pct(g.hit_rate)}`}</li>`).join("");
    const sports = (doc.by_sport || []).length > 1 ? `<table class="res-tbl mine-tbl"><thead><tr><th>Sport</th><th>Hit-miss-push</th><th>Hit rate</th><th>Pending</th><th>Matches</th></tr></thead><tbody>${doc.by_sport.map((g) => `<tr><td>${esc(g.name)}</td><td data-l="Hit-miss-push">${g.hit}-${g.miss}-${g.push}</td><td data-l="Hit rate">${g.hit_rate == null ? "--" : pct(g.hit_rate)}</td><td data-l="Pending">${g.pending}</td><td data-l="Matches">${g.matches}</td></tr>`).join("")}</tbody></table>` : "";
    return `${summary(r)}<p class="ln-note">${esc(r.rule)} ${esc(r.scope)}.${r.dupes ? ` ${plural(r.dupes, "pick")} repeat a player, stat and match on another platform.` : ""}</p>${sports}${groups ? `<ul class="plat-list">${groups}</ul>` : ""}${r.picks ? "" : '<p class="ln-note">No tracked picks yet.</p>'}`;
  }
  const num = (v) => (v == null ? "--" : esc(v));
  function otherRow(x) {
    const gap = x.printed != null && x.observed != null ? (x.observed - x.printed) * 100 : null, record = x.wins == null ? "--" : `${x.wins}-${x.losses}${x.pushes != null ? `-${x.pushes}` : ""}`;
    return `<tr><td>${esc(x.name)}</td><td data-l="Picks">${x.picks}</td><td data-l="Won-lost-push">${record}</td><td data-l="Forecast chance">${x.printed == null ? "--" : pct(x.printed)}</td><td data-l="Hit rate">${x.observed == null ? "--" : pct(x.observed)}</td><td data-l="Gap">${gap == null ? "--" : `${gap >= 0 ? "+" : ""}${gap.toFixed(1)} pts`}</td><td class="w" data-l="Forecast v hit rate, 40% to 70%">${gap == null ? "" : dumb(x.printed, x.observed)}</td></tr>`;
  }
  function otherRecord(k) {
    const box = S.results[k], R = box.doc, name = D.sports[k];
    if (!R) return box.status === "unavailable" ? `<div class="state"><h3>${esc(name)} record unavailable</h3><p>Scout could not read the record just now.</p><button class="btn" type="button" ${act("retry-results")}>Retry</button></div>` : '<div class="skel-line tall"></div><div class="skel-line tall"></div>';
    const t = R.totals, word = t.match_word || "matches";
    const stale = box.status === "stale" ? '<p class="ln-note">Saved copy.</p>' : "";
    if (!t.picks) return `${stale}<p class="ln-note">No settled forecasts meet this rule yet.</p><p class="ln-note">${esc(R.population)}</p><p class="ln-note">${esc(R.rule)}</p>`;
    const names = { chance: k === "lol" ? "Grade" : "Chance band", stat: "Stat", role: "Role" }, keys = Object.keys(R.groups).filter((g) => R.groups[g] && R.groups[g].length);
    const group = keys.includes(S.group) ? S.group : keys[0];
    const seg = keys.length > 1 ? `<div class="seg" role="group" aria-label="Group results by">${keys.map((g) => `<button type="button" aria-pressed="${group === g}" ${act("group", g)}>${names[g]}</button>`).join("")}</div>` : "<span></span>";
    const ci = t.ci ? `95% interval ${pct(t.ci[0])} to ${pct(t.ci[1])}, whole ${word} resampled.` : "";
    const mk = R.market ? `<p class="ln-note">Same props, scored against the market estimate ${esc(R.market.market || "")}: Scout Brier ${num(R.market.scout_brier)}, market Brier ${num(R.market.market_brier)} on ${num(R.market.pairs)} props across ${num(R.market.games)} games. Lower is better.</p>` : "";
    return `${stale}<div class="res-top"><div class="rec-strip"><div><b>${t.wins == null ? t.picks : `${t.wins}-${t.losses}`}</b><span>${t.wins == null ? "Settled forecasts" : "Won-lost"}, ${t.pushes == null ? "pushes apart" : t.pushes === 1 ? "1 push apart" : `${t.pushes} pushes apart`}</span></div><div><b>${t.observed == null ? "--" : pct(t.observed)}</b><span>Hit rate, settled forecasts</span></div><div><b class="gold-t">${t.printed == null ? "--" : pct(t.printed)}</b><span>Forecast chance, average</span></div><div><b>${t.matches == null ? "--" : t.matches}</b><span>${cap(word)}</span></div></div></div>
      <p class="ln-note">${esc(R.population)}</p><p class="ln-note">${esc(R.rule)} ${ci} ${R.as_of ? `As of ${esc(when(R.as_of))}.` : ""}</p>${R.gate ? `<p class="ln-note">${esc(R.gate)}</p>` : ""}
      <div class="res-bar">${seg}<div class="res-legend"><span><i class="st"></i>Forecast chance</span><span><i class="ob"></i>Hit rate</span></div></div>
      <table class="res-tbl"><thead><tr><th>${names[group]}</th><th>Picks</th><th>Won-lost-push</th><th>Forecast chance</th><th>Hit rate</th><th>Gap</th><th style="width:24%">Forecast v hit rate, 40% to 70%</th></tr></thead><tbody>${(R.groups[group] || []).map(otherRow).join("")}</tbody></table>${mk}`;
  }
  function renderResults() {
    const box = $("view-results"), on = sportsOn(), k = on.includes(S.resSport) ? S.resSport : "cs2";
    const tabs = on.length > 1 ? `<div class="seg res-tabs" role="group" aria-label="Sport">${on.map((x) => `<button type="button" aria-pressed="${k === x}" ${act("res-sport", x)}>${esc(D.sports[x])}</button>`).join("")}</div>` : "";
    const head = `<div class="page-h"><div><h1 id="results-h" tabindex="-1">Results</h1><p>Separate records. They are never added together. A push is its own result: it is never a win.</p></div>${tabs}</div>`;
    const title = k === "cs2" ? "Scout board record" : S.results[k].doc ? S.results[k].doc.label : `${D.sports[k]} record`;
    const lede = k === "cs2" ? '<p class="ln-note">Saved Scout forecasts that meet the rule and definition below, whoever tracked them.</p>' : "";
    const inner = `<section class="res-sec first" aria-labelledby="res-board"><h2 id="res-board">${esc(title)}</h2>${lede}${k === "cs2" ? boardRecord() : otherRecord(k)}</section>
      <section class="res-sec" aria-labelledby="res-mine"><h2 id="res-mine">Your tracked picks</h2>${yourRecord()}</section>`;
    keepFocus(() => { box.innerHTML = head + inner; });
  }

  /* ---- render: matches. Every event with a line on it, per sport, in start order ---- */
  function renderMatches() {
    const box = $("view-matches"), m = new Map(), on = sportsOn(), shown = S.chip === "all" ? on : [S.chip];
    all.filter((r) => shown.includes(r.lead.sport) && !started(r.lead)).forEach((r) => {
      const ev = r.lead.event, e = m.get(ev.id) || { ev, sport: r.lead.sport, props: 0, players: new Set(), books: new Set() };
      e.props++; e.players.add(r.lead.player.key); r.offers.forEach((o) => { if (D.platforms[o.offer.platform].slip) e.books.add(o.offer.platform_label); });
      m.set(ev.id, e);
    });
    const list = [...m.values()].sort((a, b) => new Date(a.ev.start_utc) - new Date(b.ev.start_utc));
    const cards = list.map((e) => `<button class="match-card" type="button" ${act("event", e.ev.id)}><span class="mc-top"><span class="s-tag">${esc(D.sports[e.sport])}</span><span class="mc-time">${esc(until(e.ev.start_utc))}</span></span><b>${esc(e.ev.label)}</b><small>${esc(e.ev.competition)}${e.ev.best_of ? ` · BO${esc(e.ev.best_of)}` : ""}</small><small>${plural(e.props, "prop")} · ${plural(e.players.size, "player")}${e.books.size ? ` · ${esc([...e.books].join(", "))}` : ""}</small></button>`).join("");
    const off = shown.filter((k) => S.boards[k].status === "unavailable");
    const none = anyLoading() ? '<div class="skel-line tall"></div><div class="skel-line tall"></div>' : off.length ? "" : '<div class="empty"><p>No matches with lines right now.</p></div>';
    const seg = on.length > 1 ? `<div class="seg" role="group" aria-label="Sport">${[["all", "All"], ...on.map((k) => [k, D.sports[k]])].map(([k, l]) => `<button type="button" aria-pressed="${S.chip === k}" ${act("chip", k)}>${esc(l)}</button>`).join("")}</div>` : "";
    keepFocus(() => { box.innerHTML = `<div class="page-h"><div><h1 id="matches-h" tabindex="-1">Matches</h1><p>Every match with lines on it. Choose one to see its props.</p></div>${seg}</div>${off.length ? `<p class="ln-note">${esc(off.map((k) => D.sports[k]).join(", "))} ${off.length > 1 ? "boards are" : "board is"} unavailable right now.</p>` : ""}<div class="match-grid">${cards || none}</div>`; });
  }

  /* ---- routing ---- */
  function decoded(s) { try { return decodeURIComponent(s); } catch (e) { return ""; } }      /* a mangled link just shows the board */
  function parseHash() {
    const p = location.hash.replace(/^#\/?/, "").split("/");
    const view = VIEWS.includes(p[0]) ? p[0] : "research";       /* #/board reads the same as the board's own route */
    return { view, panel: view === "research" && p[1] === "p" ? decoded(p[2] || "") : null, tab: TABS.includes(p[3]) ? p[3] : "overview", pick: view === "picks" ? p[1] || null : null };
  }
  const wide = () => window.matchMedia("(min-width:1440px)").matches;
  function setInert(on) {
    ["skip", "top", "nav", "main", "bottom", "slip-fab"].forEach((id) => { $(id).inert = on; });
  }
  function syncPanelRole() {
    if (!S.panel) { $("app").removeAttribute("data-panel"); return; }
    const beside = wide();
    $("panel").setAttribute("aria-modal", String(window.matchMedia("(max-width:1179px)").matches));
    $("app").dataset.panel = beside ? "side" : "over";
    $("panel-scrim").hidden = beside;
    setInert(!beside);
  }
  function closePanelUI() {
    const el = $("panel");
    if (el.hidden) return;
    el.hidden = true; el.innerHTML = ""; $("panel-scrim").hidden = true; S.panel = null;
    setInert(false);
    syncPanelRole();
    const row = S.opener && document.querySelector(`.row[data-row="${CSS.escape(S.opener)}"]`);
    const b = row && (row.querySelector(`${S.openerSel}:not(:disabled)`) || row.querySelector(".row-open"));
    renderBoard();
    const again = row && document.querySelector(`.row[data-row="${CSS.escape(S.opener)}"]`);
    const c = again && (again.querySelector(`${S.openerSel}:not(:disabled)`) || again.querySelector(".row-open"));
    if (c) c.focus(); else if (b) b.focus(); else $("board-list").focus({ preventScroll: true });
  }
  function openPanelUI(id, tab) {
    const row = rows.get(id);
    if (!row) {
      if (anyLoading()) { S.wanted = true; return; }
      S.wanted = null;
      closePanelUI();
      return;
    }
    S.wanted = null;
    const first = !S.panel, same = S.panel && S.panel.id === id;
    const rec = lead(row), keep = S.panel && row.offers.some((o) => o.offer.platform === S.panel.platform);   /* another line of a prop stays on its book */
    S.panel = { id, tab, side: same ? S.panel.side : rec.scout.side || "over", platform: same || keep ? S.panel.platform : rec.offer.platform, prop: row.prop, line: rec.offer.line };
    if (!rec.offer[S.panel.side].offered) S.panel.side = S.panel.side === "over" ? "under" : "over";
    $("panel").hidden = false; $("panel-scrim").hidden = false;
    syncPanelRole();
    renderPanel();
    if (!same) renderBoard();
    if (first || !same) { const nm = $("panel-name"); if (nm) nm.focus(); }
    loadLine();
  }
  function route(focusPage) {
    const r = parseHash(), changed = r.view !== S.view;
    S.view = r.view;
    VIEWS.forEach((v) => { $("view-" + v).hidden = v !== r.view; });
    $("app").dataset.view = r.view;
    if (r.pick) {
      S.openPick = r.pick;
      S.picksFilter = "all";                                  /* a Receipt link always lands on its receipt */
    }
    if (r.view === "picks") renderPicks();
    if (r.view === "matches") renderMatches();
    if (r.view === "results" && S.results[S.resSport].status === "idle") loadResults();
    if (r.panel) openPanelUI(r.panel, r.tab); else closePanelUI();
    renderNav();
    $("slip-fab").hidden = r.view === "slips" || !S.slip.legs.length;
    const typing = S.searchJump;
    S.searchJump = false;
    if (changed && focusPage && !typing) { const h = document.querySelector(`#view-${r.view} h1`); if (h && r.view !== "research") h.focus(); else if (r.view === "research") $("board-list").focus({ preventScroll: true }); }
    if (r.pick) { const c = document.querySelector(`[data-fk="pick:${CSS.escape(r.pick)}"]`); if (c) c.focus(); }
  }
  function setPanelHash(id, tab) {
    history.replaceState(null, "", `#/research/p/${encodeURIComponent(id)}${tab && tab !== "overview" ? "/" + tab : ""}`);
  }
  function step(row) {
    if (!row) return;
    S.opener = row.id; S.openerSel = ".row-open";
    setPanelHash(row.id, S.panel ? S.panel.tab : "overview");
    openPanelUI(row.id, S.panel ? S.panel.tab : "overview");
    const el = document.querySelector(`.row[data-row="${CSS.escape(row.id)}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  /* ---- feedback ---- */
  let toastT;
  function say(msg) {
    const box = document.querySelector("dialog[open]");                  /* a modal dialog hides everything outside it */
    document.querySelectorAll("[data-toast]").forEach((x) => { x.textContent = ""; x.classList.remove("on"); });
    const t = box ? box.querySelector("[data-toast]") : $("toast");
    t.textContent = msg; t.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove("on"), 3200);
  }

  /* ---- actions ---- */
  const actions = {
    chip(id) { S.chip = id; S.activeView = null; S.event = null; saveState({ prefs: true }); renderBoardAll(); renderMatches(); $("board-scroll").scrollTop = 0; },
    event(id) { S.event = id; S.f = Object.assign({}, S.f, { day: "all" }); renderBoardAll(); location.hash = "#/research"; $("board-scroll").scrollTop = 0; },
    "event-clear"() { S.event = null; renderBoardAll(); },
    watch(id) {
      const r = rows.get(id).lead, k = watchId(r.sport, r.player.key), on = S.watch.some((w) => w.id === k);
      S.watch = on ? S.watch.filter((w) => w.id !== k) : [...S.watch, { id: k, sport: r.sport, key: r.player.key, name: r.player.name, team: r.player.team }];
      saveState({ watch: true });
      renderBoard(); renderPanel();
      say(on ? `${r.player.name} left your watchlist` : `${r.player.name} is on your watchlist`);
    },
    open(id) { S.opener = id; S.openerSel = ".row-open"; location.hash = `#/research/p/${encodeURIComponent(id)}`; },
    "row-choose"(id) { S.opener = id; S.openerSel = ".add"; location.hash = `#/research/p/${encodeURIComponent(id)}`; },
    "row-add"(id) {
      const rec = lead(rows.get(id)), legs = rowLegs(rec);
      if (legs.length) { const one = legs[0]; legs.forEach((l) => { S.slip.legs = S.slip.legs.filter((x) => x.id !== l.id); }); slipChanged(); say(`Removed ${one.player} ${cap(one.side)} ${one.line}`); } else addLeg(rec, rec.scout.side);
    },
    sort(k) { setF("sort", k); },
    "f-set"(v) { const i = v.indexOf(":"); setF(v.slice(0, i), v.slice(i + 1)); },
    "retry-board"() { loadBoard(); },
    "retry-mine"() { loadMine(); },
    "mine-older"() { loadOlder(); },
    "retry-results"() { loadResults(); },
    "res-sport"(k) { S.resSport = k; saveState({ prefs: true }); renderResults(); if (S.results[k].status === "idle" || S.results[k].status === "unavailable") loadResults(k); },
    "picks-sport"(k) { S.picksSport = k; renderPicks(); },
    "reset-filters"() { resetFilters(); },
    view: applyView,
    "view-del"(id) { S.views = S.views.filter((v) => v.id !== id); saveState({ views: true }); if (S.activeView === id) S.activeView = null; renderFilters(); },
    "check-now"() { S.checkErr = null; S.check = null; runCheck(); },
    "leg-remove": removeLeg,
    "swap-leg": swapLeg,
    "entry-type"(v) { const i = v.indexOf(":"); S.slip.types[v.slice(0, i)] = v.slice(i + 1); slipChanged(); },
    "slip-clear"() { S.slip = { legs: [], types: S.slip.types }; slipChanged(); say("Slip cleared"); },
    copy: copyEntry,
    track: trackEntry,
    "open-sheet"() { S.sheetMark = focusMark(); $("sheet-body").innerHTML = slipHtml("sheet"); $("slip-dialog").showModal(); if (S.slip.legs.length && !freshCheck()) scheduleCheck(); },
    "p-close"() { location.hash = "#/research"; },
    "p-tab"(t) {
      S.panel.tab = t; setPanelHash(S.panel.id, t); renderPanel();
      const b = $("p-body"); if (b) b.scrollTop = 0;
      const tb = $("tab-" + t); if (tb) tb.focus();
    },
    "p-side"(sd) { S.panel.side = sd; renderPanel(); },
    "p-book"(pf) { S.panel.platform = pf; const rec = offerOf(rows.get(S.panel.id), pf); if (!rec.offer[S.panel.side].offered) S.panel.side = S.panel.side === "over" ? "under" : "over"; renderPanel(); loadLine(); },
    "p-line"(id) { step(rows.get(id)); },
    "p-prev"() { step(neighbours().prev); },
    "p-next"() { step(neighbours().next); },
    "p-add"() {
      const P = S.panel, rec = offerOf(rows.get(P.id), P.platform), on = legsFor(rec).find((l) => l.side === P.side);
      if (on) { removeLeg(on.id); say(`Removed ${on.player} ${cap(on.side)} ${on.line}`); } else addLeg(rec, P.side);
    },
    "hist-n"(n) { S.histN = +n; saveState({ prefs: true }); renderPanel(); },
    pick(id) { S.openPick = S.openPick === id ? null : id; renderPicks(); },
    "picks-filter"(k) { S.picksFilter = k; renderPicks(); },
    group(k) { S.group = k; saveState({ prefs: true }); renderResults(); }
  };
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-act]");
    if (t && !t.disabled && t.getAttribute("aria-disabled") !== "true" && actions[t.dataset.act]) {
      e.preventDefault();
      const mark = focusMark(t);
      actions[t.dataset.act](t.dataset.id);
      focusBack(mark);
    }
    if (e.target.id === "panel-scrim") location.hash = "#/research";
    if (e.target.tagName === "DIALOG") e.target.close();           /* a click on the backdrop */
  });
  document.addEventListener("change", (e) => {
    const f = e.target.dataset ? e.target.dataset.f : null;
    if (f) { setF(f, e.target.type === "checkbox" ? e.target.checked : e.target.value); return; }
    if (e.target.id === "f-day") setF("day", e.target.value);
    if (e.target.id === "f-market") setF("market", e.target.value);
  });
  document.addEventListener("toggle", (e) => {                     /* toggle does not bubble: this listens on the way down */
    const k = e.target.dataset ? e.target.dataset.det : null;
    if (k) { if (e.target.open) openDetails.add(k); else openDetails.delete(k); }
  }, true);
  document.addEventListener("submit", (e) => {
    if (e.target.id !== "save-view") return;
    e.preventDefault();
    const name = $("view-name").value.trim();
    if (!name) { $("view-name").focus(); return; }
    const v = { id: "u-" + Date.now().toString(36), name, state: { chip: S.chip, q: S.q, f: Object.assign({}, S.f) } };
    S.views.push(v); S.activeView = v.id; saveState({ views: true });
    renderFilters(); say(`Saved view: ${name}`);
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
    if (e.key === "/" && !typing && !$("filters-dialog").open && !$("slip-dialog").open) {
      e.preventDefault();
      searchOpen(true); $("search").focus(); return;
    }
    if (e.key === "Enter" && e.target.id === "search") {
      e.preventDefault();                                     /* the key must not also press the row it lands on */
      if (S.view !== "research") location.hash = "#/research";
      const first = document.querySelector(".row-open");
      if (first) first.focus();
      return;
    }
    if (e.key === "Escape") {
      if (e.target.id === "search" && S.q) { e.target.value = ""; S.q = ""; S.activeView = null; renderBoardAll(); return; }
      if ($("filters-dialog").open || $("slip-dialog").open) return;
      if (S.panel) location.hash = "#/research";
      if ($("top").classList.contains("search-open")) {
        searchOpen(false);
        if (e.target.id === "search") $("search-toggle").focus();
      }
    }
    if (!typing && S.panel && !$("filters-dialog").open && !$("slip-dialog").open) {
      if (e.key === "j" || e.key === "J") { e.preventDefault(); step(neighbours().next); return; }
      if (e.key === "k" || e.key === "K") { e.preventDefault(); step(neighbours().prev); return; }
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
  function searchOpen(on) {
    $("top").classList.toggle("search-open", on);
    $("search-toggle").setAttribute("aria-expanded", String(on));
  }
  $("search").addEventListener("input", (e) => {
    S.q = e.target.value; S.activeView = null; renderBoardAll();
    if (S.q.trim() && S.view !== "research") { S.searchJump = true; location.hash = "#/research"; }   /* the board is what search filters */
  });
  $("search-toggle").addEventListener("click", () => {
    const on = !$("top").classList.contains("search-open");
    searchOpen(on);
    if (on) $("search").focus();
  });
  $("skip").addEventListener("click", (e) => {
    e.preventDefault();                                       /* the hash is the route here, so it stays put */
    const t = S.view === "research" ? $("board-list") : document.querySelector(`#view-${S.view} h1`);
    if (t) t.focus();
  });
  $("open-filters").addEventListener("click", () => { renderFilters(); $("filters-dialog").showModal(); });
  $("filters-done").addEventListener("click", () => $("filters-dialog").close());
  $("filters-dialog").addEventListener("close", renderChips);
  $("slip-dialog").addEventListener("close", () => focusBack(S.sheetMark));
  $("slip-fab").addEventListener("click", () => actions["open-sheet"]());
  function tipAt(t, x, y) {
    const tip = $("chart-tip");
    tip.textContent = t; tip.hidden = false;
    tip.style.left = Math.max(8, Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8)) + "px";
    tip.style.top = Math.max(y - 34, 8) + "px";
  }
  document.addEventListener("pointermove", (e) => {
    const t = e.target.dataset ? e.target.dataset.tip : null;
    if (e.pointerType === "touch") return;
    if (!t) { $("chart-tip").hidden = true; return; }
    tipAt(t, e.clientX, e.clientY);
  });
  document.addEventListener("pointerdown", (e) => {                /* a tapped bar shows its value */
    const t = e.target.dataset ? e.target.dataset.tip : null;
    if (t) tipAt(t, e.clientX, e.clientY); else $("chart-tip").hidden = true;
  });
  let lastTop = 0;
  $("board-scroll").addEventListener("scroll", (e) => {            /* phone: the toolbar steps aside while reading the list */
    const y = e.target.scrollTop;
    $("toolbar").classList.toggle("tucked", y > lastTop && y > 48);
    lastTop = y;
  }, { passive: true });
  let resizeT;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      renderChips();
      syncPanelRole();
      if (S.panel && rows.get(S.panel.id)) { mountCharts(offerOf(rows.get(S.panel.id), S.panel.platform), S.panel.side); }
    }, 120);
  });
  window.addEventListener("hashchange", () => route(true));

  /* ---- the clock: nothing on screen waits for a click to go out of date. Only what changed repaints ---- */
  function boardSig() { return all.map((r) => rowFlag(r) + until(lead(r).event.start_utc)).join("|") + sportsOn().map((k) => S.boards[k].status).join(); }
  function slipSig() { const ph = slipPhase(); return ph + statusLine(ph); }
  function panelSig() {
    if (!S.panel || !rows.get(S.panel.id)) return "";
    const rec = offerOf(rows.get(S.panel.id), S.panel.platform);
    return [started(rec), addRefusal(rec, S.panel.side)].join("|");
  }
  function tick() {
    if (S.signedOut) return;
    if (S.saveFail) { if (S.stateReady) pushState(); else loadState(); }
    if (boardSig() !== T.board) { renderBoard(); if (S.f.hideOff) renderChips(); }
    if (S.slip.legs.length && !S.checking && !freshCheck() && !S.checkErr) scheduleCheck();
    if (S.slip.legs.length && !S.checking && S.checkErr === "limit") { S.checkErr = null; scheduleCheck(); }
    if (slipSig() !== T.slip) renderSlips();
    if (panelSig() !== T.panel) renderPanel();
  }
  setInterval(tick, 5000);
  setInterval(() => { if (!document.hidden && !S.signedOut) { loadBoard(); syncState(); } }, BOARD_POLL);
  setInterval(() => { if (!document.hidden && !S.signedOut && S.mine.doc && S.mine.doc.record.pending) loadMine(); }, MINE_POLL);
  function comeBack() {
    if (S.signedOut) return;
    tick();
    if (S.stateReady) syncState(); else loadState();
    loadBoard();
    if (S.mine.doc) loadMine();
  }
  function leaving() { if (unsaved()) pushState(false, true); }      /* keepalive: the request outlives the page, and the draft covers a refused one */
  document.addEventListener("visibilitychange", () => { if (document.hidden) leaving(); else comeBack(); });
  window.addEventListener("pagehide", leaving);
  window.addEventListener("focus", comeBack);

  function sessionEnded() {
    if (S.signedOut) return;
    S.signedOut = true;
    const box = $("session-strip");
    box.innerHTML = '<b>Your session ended.</b><span class="t">Your slip stays on this device.</span><a class="btn" href="/auth/login">Sign in</a>';
    box.hidden = false;
    renderSlips(); renderFeedStrips();
  }

  /* ---- start ---- */
  async function boot() {
    rebuild();
    renderHead(); renderChips(); renderBoard(); renderSlips(); renderPicks(); renderResults(); renderMatches(); route(false);
    try {                                                       /* who this is comes first: it decides the sports shown and the draft */
      S.me = await api("me");
      $("account-name").textContent = S.me.name || "Member";
      $("avatar").textContent = (S.me.name || "M").slice(0, 2).toUpperCase();
    } catch (e) { S.me = null; }
    await loadState();
    await loadBoard();
    if (!S.savedDay && !visibleRows().length && all.length) { S.f.day = "all"; S.activeView = null; }    /* no props today: the first look is the next ones */
    renderHead(); renderBoardAll(); renderSlips(); renderPicks(); renderResults(); renderMatches();
    if (S.slip.legs.length) scheduleCheck();
    await Promise.allSettled([loadMine(), loadResults("cs2"), ...(S.resSport !== "cs2" ? [loadResults()] : [])]);
    $("app").dataset.boot = "done";
  }
  boot();
})();
