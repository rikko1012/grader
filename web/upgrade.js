/* Upgraded member pages. Sent only behind Handler._upgrade_on (SCOUT_UPGRADE), and only as one more script on the existing pages:
   /, /lol, /sports and /sports/results keep every route, tool, filter and handler they have. This file adds to them:
     aligned board rows with column labels, a player's other lines under one row, hit-rate and book cells, the research drawer,
     one account slip across the three pages (saved on the account, checked by the server), and stated against observed Results.
   It never grades, never changes a stake and never decides a pass: every number comes from /api/upgrade/*, the workspace API, and a
   page row is only ever matched to its saved record. A failure here leaves the original page as it was. */
(function () {
  "use strict";
  const PAGE = (document.currentScript && document.currentScript.dataset.page) || document.documentElement.dataset.upgrade || "";
  const API = "/api/upgrade/";
  let HUB = null;
  const TOKEN = new URLSearchParams(location.search).get("token");   /* the owner's own link: the API takes it as a header, never in a URL */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (p) => (typeof p !== "number" ? "--" : (p * 100).toFixed(1) + "%");   /* one decimal, from the unrounded chance */
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const tenth = (x) => Math.round(x * 10) / 10;
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const isStr = (v) => typeof v === "string";
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const guard = (fn) => function () { try { return fn.apply(this, arguments); } catch (e) { if (window.console) console.warn("upgrade", e); return undefined; } };
  const idem = () => Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  const debounce = (fn, ms) => { let t = null; return (...a) => { clearTimeout(t); t = setTimeout(() => { t = null; fn(...a); }, ms); }; };
  const X_ICON = '<svg class="u-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';

  const SPORTS = { cs2: "CS2", lol: "LoL", nba: "NBA", nfl: "NFL" };
  const PLATFORMS = {
    prizepicks: { label: "PrizePicks", short: "PP", slip: true, kind: "modifier", types: [["power", "Power"], ["flex", "Flex"]] },
    underdog: { label: "Underdog", short: "UD", slip: true, kind: "multiplier", types: [["standard", "Standard"], ["flex", "Flex"]] }
  };
  const STATUS = { hit: "Hit", miss: "Miss", push: "Push", void: "Void", pending: "Pending" };
  const CHECKS = ["Game not started", "Offer available and unchanged", "Forecast belongs to this event, line and period", "Platform accepts this combination", "No duplicates or conflicts"];
  const MAX_LEGS = 8, BOARD_KEEP = 45000, HITS_RETRY = 10000, CHECK_DEBOUNCE = 350, SAVE_DEBOUNCE = 800, DRAFT_MAX_MS = 86400000;

  /* ---- requests ---- */
  class ApiError extends Error { constructor(status, doc) { super("api " + status); this.status = status; this.doc = doc; } }
  async function apiOnce(path, body, keepalive) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 15000), headers = TOKEN ? { "X-Admin-Token": TOKEN } : {};
    try {
      const opt = { signal: ctl.signal, credentials: "same-origin", headers };
      if (body !== undefined) Object.assign(opt, { method: "POST", body: JSON.stringify(body), keepalive: !!keepalive, headers: Object.assign({ "Content-Type": "application/json" }, headers) });
      const r = await fetch(API + path, opt);
      let doc = null;
      try { doc = await r.json(); } catch (e) { doc = null; }
      if (!r.ok) throw new ApiError(r.status, doc);
      return doc;
    } finally { clearTimeout(t); }
  }
  async function api(path, body, keepalive) {                     /* a read told to wait by a busy server tries again; a press never repeats here */
    for (let i = 0; ; i++) {
      try { return await apiOnce(path, body, keepalive); }
      catch (e) {
        if (body !== undefined || i >= 2 || !(e instanceof ApiError) || e.status !== 503 || !e.doc || e.doc.error !== "busy") throw e;
        await new Promise((r) => setTimeout(r, 2000 + Math.random() * 1500));
      }
    }
  }

  /* ---- time ---- */
  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso), now = new Date(), next = new Date(now.getTime() + 86400000);
    if (dayKey(d) === dayKey(now)) return clock(iso);
    if (dayKey(d) === dayKey(next)) return "Tomorrow " + clock(iso);
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + clock(iso);
  }
  const shortDate = (iso) => new Date(iso.length === 10 ? iso + "T12:00:00" : iso).toLocaleDateString([], { month: "short", day: "numeric" });

  /* ---- saved records: the workspace board of each sport. One copy per sport, read at most once per BOARD_KEEP ---- */
  const R = {
    me: null, hits: {}, hitsAt: 0, hitsComplete: false, lines: {},
    boards: Object.fromEntries(Object.keys(SPORTS).map((k) => [k, { at: 0, busy: null, recs: new Map(), props: new Map(), keys: new Map() }]))
  };
  const offerKey = (book, id) => book + "|" + id;
  const propKey = (r) => [r.event.id, r.player.key, r.market.stat, r.market.period].join("|");
  const sportKey = (ev, pkey, stat, period, line, book) => [ev, pkey, stat, period, Number(line), book].join("|");
  /* a board read sends back the ETag of the copy it holds: an unchanged board answers 304 with no body (audit 2026-11-04, polling).
     Its own path, so a 304 is never an error and the browser cache never adds a second copy. A busy server is retried as api() does. */
  async function boardRead(path, etag) {
    for (let i = 0; ; i++) {
      const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 15000), headers = Object.assign(TOKEN ? { "X-Admin-Token": TOKEN } : {}, etag ? { "If-None-Match": etag } : {});
      try {
        const r = await fetch(API + path, { signal: ctl.signal, credentials: "same-origin", headers, cache: "no-store" });
        if (r.status === 304 && etag) return { same: true };
        let doc = null;
        try { doc = await r.json(); } catch (e) { doc = null; }
        if (!r.ok) throw new ApiError(r.status, doc);
        return { doc, etag: r.headers.get("ETag") };
      } catch (e) {
        if (i >= 2 || !(e instanceof ApiError) || e.status !== 503 || !e.doc || e.doc.error !== "busy") throw e;
        await new Promise((res) => setTimeout(res, 2000 + Math.random() * 1500));
      } finally { clearTimeout(t); }
    }
  }
  async function loadBoard(sport, force) {
    const b = R.boards[sport];
    if (b.busy) return b.busy;
    if (!force && Date.now() - b.at < BOARD_KEEP) return b;
    b.busy = (async () => {
      try {
        const read = await boardRead(sport === "cs2" ? "board" : "board?sport=" + sport, b.at && !b.error ? b.etag : null);
        if (read.same) { Object.assign(b, { at: Date.now(), error: false }); b.busy = null; return b; }
        const doc = read.doc;
        b.etag = read.etag;
        Object.entries(doc.platforms || {}).forEach(([p, v]) => { if (!own(PLATFORMS, p)) PLATFORMS[p] = v; });
        const recs = new Map(), props = new Map(), keys = new Map();
        (doc.records || []).forEach((r) => {
          recs.set(offerKey(r.offer.platform, r.offer.offer_id), r);
          keys.set(sportKey(r.event.id, r.player.key, r.market.stat, r.market.period, r.offer.line, r.offer.platform), r);
          const k = propKey(r);
          if (!props.has(k)) props.set(k, []);
          props.get(k).push(r);
        });
        props.forEach((l) => l.sort((a, b2) => a.offer.line - b2.offer.line));
        Object.assign(b, { at: Date.now(), recs, props, keys, feeds: doc.feeds || {}, error: false });
      } catch (e) { b.error = true; b.at = Date.now() - BOARD_KEEP + 10000; }     /* stale rows stay labelled, never silently current */
      b.busy = null;
      return b;
    })();
    return b.busy;
  }
  async function loadHits() {                                     /* CS2 only: a board row carries L10, the shared pass adds L5 and L20 */
    if (Date.now() - R.hitsAt < (R.hitsComplete ? BOARD_KEEP : HITS_RETRY)) return;
    R.hitsAt = Date.now();
    try { const d = await api("hits"); R.hits = d.rows || {}; R.hitsComplete = d.complete !== false; } catch (e) { R.hitsComplete = true; /* the row keeps its L10 */ }
  }
  /* the shared pass has not reached this CS2 row yet (it fills a slice per request): a quiet placeholder, never a dash */
  const hitsPending = (rec) => {
    if (rec.sport !== "cs2") return false;
    const saved = R.hits[offerKey(rec.offer.platform, rec.offer.offer_id)];
    return (!R.hitsAt || !R.hitsComplete) && !(saved && saved.line === rec.offer.line);
  };
  const siblings = (rec) => (R.boards[rec.sport].props.get(propKey(rec)) || [rec]);
  const tallies = (rec) => {
    const h = rec.history || {}, saved = rec.sport === "cs2" ? R.hits[offerKey(rec.offer.platform, rec.offer.offer_id)] || {} : {}, more = saved.line === rec.offer.line ? saved : {};
    return { l5: h.l5 || more.l5 || null, l10: h.l10 || null, l20: h.l20 || more.l20 || null, h2h: h.h2h || more.h2h || null, season: h.season || more.season || null };   /* h2h / season: SCOUT_BOARD_H2H only */
  };

  /* ---- offer helpers, the workspace's rules ---- */
  const started = (r) => r.event.status === "started" || new Date(r.event.lock_utc || r.event.start_utc) <= Date.now();
  const startWord = (r) => (r.sport === "nba" || r.sport === "nfl" ? "Game started" : "Match started");
  const held = (r) => r.state.kind === "held";
  const slipOffer = (r) => !!(PLATFORMS[r.offer.platform] || {}).slip;
  const chanceOf = (rec, side) => (side === "under" ? rec.scout.p_under : side === "over" ? rec.scout.p_over : null);
  function verdictText(rec, side) {
    const s = rec.scout, sd = side || s.side;
    if (!sd) return "No lean";
    if (s.side && sd !== s.side) return `Scout side is ${cap(s.side)}`;
    if (s.hold) return `Research estimate ${cap(sd)}`;
    if (s.grade) return `${s.grade} ${cap(sd)}${rec.sport === "nba" || rec.sport === "nfl" ? " · Research grade" : ""}`;      /* as classic sports */
    return `Lean ${cap(sd)}`;
  }
  const decimalOf = (o, side) => { const v = o.offer[side], P = PLATFORMS[o.offer.platform] || {}; if (P.price_comparable === false) return null; /* Dabble: own unit, never odds */ return P.kind === "decimal" ? v.payout : v.price; };
  function priceText(o, side) {
    const v = o.offer[side], dec = decimalOf(o, side);
    if (!v.offered) return null;
    if (dec != null) return dec.toFixed(2);
    return v.payout != null && v.payout !== 1 ? v.payout.toFixed(2) + "x" : null;
  }
  const lineLabel = (o) => (o.market.line_type !== "standard" ? cap(o.market.line_type) : "Standard");
  const legId = (r, side) => [r.sport, r.event.id, r.player.id || r.player.name, r.market.stat, r.market.period, r.offer.platform, side, r.offer.line].join("|");
  function makeLeg(r, side, addedAt) {
    return {
      id: legId(r, side), key: r.key, sport: r.sport, book: r.offer.platform, row_id: r.offer.offer_id, side, line: r.offer.line, rev: r.offer.revision, read_rev: r.scout.read_rev || null, shown_p: chanceOf(r, side),
      event_id: r.event.id, event_label: r.event.label, player: r.player.name, player_key: r.player.key, team: r.player.team, stat: r.market.stat, stat_label: r.market.stat_label,
      period: r.market.period, period_label: r.market.period_label, platform_label: r.offer.platform_label, shown_label: verdictText(r, side), added_at: addedAt || new Date().toISOString()
    };
  }
  const wireLeg = (l) => ({ id: l.id, sport: l.sport, book: l.book, row_id: l.row_id, side: l.side, line: l.line, rev: l.rev, read_rev: l.read_rev, shown_p: l.shown_p, team: l.team, player: l.player, event: l.event_id });
  const LEG_TEXT = ["id", "key", "book", "row_id", "event_id", "event_label", "player", "player_key", "team", "stat", "stat_label", "period", "period_label", "platform_label", "rev", "shown_label"];
  const cleanLeg = (l) => isObj(l) && LEG_TEXT.every((k) => isStr(l[k])) && own(SPORTS, l.sport) && own(PLATFORMS, l.book) && (l.side === "over" || l.side === "under") && isNum(l.line)
    && (l.shown_p == null || isNum(l.shown_p)) && (l.read_rev == null || isStr(l.read_rev)) && isStr(l.added_at) && Number.isFinite(Date.parse(l.added_at));

  /* ---- account slip: the workspace's saved slip. Every device merges three ways against the copy it last synced ---- */
  const T = {
    legs: [], types: {}, rev: 0, ready: false, synced: new Set(), pending: false, saving: false, saveFail: false, gen: 0, check: null, checkSig: "", checking: false, checkErr: null,
    tracked: null, trackIdem: {}, trackBusy: {}, copyBusy: false, mine: null, mineState: "idle", tab: "slip", open: new Set(), openPick: null, advice: null,
    slipLines: "no_goblins"   /* the member's goblin rule (hub Slip Studio sets it from saved prefs); the adder suggests only lines it allows */
  };
  const sig = () => T.legs.map((l) => [l.id, l.rev, l.read_rev, l.shown_p].join(":")).join(";") + "|" + JSON.stringify(T.types);
  const legIds = (legs) => new Set(legs.map((l) => l.id));
  const typeOf = (e) => T.types[e.platform] || PLATFORMS[e.platform].types[0][0];
  function freshCheck() {
    const c = T.check;
    if (!c || T.checkSig !== sig()) return null;
    const age = (Date.now() - new Date(c.checked_at)) / 1000;
    return age >= -60 && age < c.ttl_s ? c : null;
  }
  function entries() {
    const m = new Map();
    T.legs.forEach((l) => {
      if (!m.has(l.book)) m.set(l.book, { key: l.book, platform: l.book, label: PLATFORMS[l.book].label, legs: [] });
      m.get(l.book).legs.push(l);
    });
    const c = freshCheck(), bad = (e) => (c && c.entries[e.key] && c.entries[e.key].ok ? 1 : 0);
    return [...m.values()].sort((a, b) => bad(a) - bad(b));
  }
  function mergeLegs(theirs) {
    const t = (theirs || []).filter(cleanLeg), tIds = legIds(t), base = T.synced;
    const keep = T.legs.filter((l) => tIds.has(l.id) || !base.has(l.id));
    const mIds = legIds(keep);
    t.forEach((l) => { if (!mIds.has(l.id) && !base.has(l.id)) keep.push(l); });
    if (keep.length > MAX_LEGS) {
      const over = keep.length - MAX_LEGS;
      keep.sort((a, b) => tIds.has(b.id) - tIds.has(a.id));
      keep.length = MAX_LEGS;
      say(`A slip holds ${MAX_LEGS} picks. ${plural(over, "pick")} from this device did not fit.`);
    }
    T.legs = keep;
    T.synced = tIds;
    if (keep.length !== t.length || keep.some((l) => !tIds.has(l.id))) T.pending = true;
  }
  const draftKey = () => (R.me && R.me.member ? "scout-workspace-draft:" + R.me.member : null);    /* the same draft the workspace keeps */
  function saveDraft() { const k = draftKey(); if (k) try { localStorage.setItem(k, JSON.stringify({ legs: T.legs, types: T.types, base: [...T.synced], at: Date.now() })); } catch (e) { /* the account copy still saves */ } }
  function clearDraft() { const k = draftKey(); if (k) try { localStorage.removeItem(k); } catch (e) { /* nothing to clear */ } }
  function readDraft() {
    const k = draftKey();
    try {
      const d = k ? JSON.parse(localStorage.getItem(k) || "null") : null;
      return isObj(d) && Array.isArray(d.legs) && Array.isArray(d.base) && Date.now() - d.at < DRAFT_MAX_MS ? d : null;
    } catch (e) { return null; }
  }
  function adoptRemote(doc) {
    T.rev = doc.rev || 0;
    if (doc.slip && Array.isArray(doc.slip.legs)) {
      mergeLegs(doc.slip.legs);
      if (isObj(doc.slip.types)) T.types = Object.assign({}, doc.slip.types, T.pending ? T.types : {});
    }
    T.check = T.checkSig === sig() ? T.check : null;
    paintTray();
    if (T.legs.length) { scheduleCheck(); scheduleAdvice(); }
  }
  async function pushState(retried, leaving) {
    if (!T.ready || !T.pending || T.saving) return;
    T.saving = true;
    const gen = T.gen, sent = legIds(T.legs);
    let conflict = false;
    try {
      const doc = await api("state", { rev: T.rev, slip: { legs: T.legs, types: T.types } }, leaving);
      T.rev = doc.rev;
      T.synced = sent;
      if (gen === T.gen) { T.pending = false; clearDraft(); }
      T.saveFail = false;
    } catch (e) {
      if (e.status === 409 && e.doc) { adoptRemote(e.doc); conflict = retried !== true; T.saveFail = retried === true; } else T.saveFail = true;
    }
    T.saving = false;
    if (conflict) return pushState(true);
    if (T.pending && !T.saveFail) saveSoon();
    paintTray();
  }
  const saveSoon = debounce(pushState, SAVE_DEBOUNCE);
  function saveSlip() { T.pending = true; T.gen++; saveDraft(); saveSoon(); }
  async function syncState() {
    if (!T.ready || T.pending || T.saving) return;
    try { const doc = await api("state"); if (!T.pending && !T.saving && doc.rev !== T.rev) adoptRemote(doc); } catch (e) { /* the next look asks again */ }
  }
  async function loadState() {
    try {
      const doc = await api("state"), mine = T.legs;
      T.rev = doc.rev || 0;
      T.ready = true;
      if (doc.slip && Array.isArray(doc.slip.legs)) {
        T.legs = doc.slip.legs.filter(cleanLeg).slice(0, MAX_LEGS);
        T.types = isObj(doc.slip.types) ? doc.slip.types : {};
        T.synced = legIds(T.legs);
      }
      const draft = readDraft(), theirs = T.legs;
      if (mine.length || draft) {
        T.synced = new Set(draft ? draft.base : []);
        T.legs = mine.length ? mine : draft.legs.filter(cleanLeg);
        mergeLegs(theirs);
        if (draft && isObj(draft.types)) T.types = Object.assign({}, T.types, draft.types);
      }
      T.saveFail = false;
      if (T.pending) saveSoon();
      paintTray();
      if (T.legs.length) { scheduleCheck(); scheduleAdvice(); }
    } catch (e) { T.saveFail = true; paintTray(); }
  }
  function slipChanged() {
    T.check = T.checkSig === sig() ? T.check : null;
    T.tracked = null;
    saveSlip();
    paintTray();
    markRows();
    if (T.legs.length) { scheduleCheck(); scheduleAdvice(); }
  }
  function addRefusal(rec, side) {
    if (started(rec)) return `${startWord(rec)} at ${when(rec.event.start_utc)}.`;
    if (!slipOffer(rec)) return `${rec.offer.platform_label} is shown for comparison. Slips cover PrizePicks and Underdog.`;
    if (!side) return "Choose a side first.";
    if (!rec.offer[side].offered) return `${cap(side)} is not offered on ${rec.offer.platform_label}.`;
    if (held(rec)) return "The grade on this line is held.";
    const same = T.legs.find((l) => l.player_key === rec.player.key && l.stat === rec.market.stat && l.period === rec.market.period && l.book === rec.offer.platform && l.event_id === rec.event.id);
    if (same && !(same.side === side && same.line === rec.offer.line)) return `Conflict: ${cap(same.side)} ${same.line} on this prop is already on your ${rec.offer.platform_label} entry.`;
    /* sq-1 adder: one leg per player on an entry. PrizePicks counts a player once; Underdog refuses the same side and publishes nothing on both sides */
    const twin = !same && T.legs.find((l) => l.book === rec.offer.platform && l.sport === rec.sport && l.player_key === rec.player.key);
    if (twin) return `${rec.player.name} is already on your ${rec.offer.platform_label} entry (${cap(twin.side)} ${twin.line} ${twin.stat_label}, ${twin.period_label}). One leg per player: remove that pick first.`;
    if (T.legs.length >= MAX_LEGS && !same) return `A slip holds ${MAX_LEGS} picks.`;
    return null;
  }
  /* Facts about one leg against the rest of its entry, shown at once on the slip (the server's check stays the authority). */
  function legNotes(l) {
    const out = [];
    T.legs.forEach((o) => {
      if (o.id === l.id || o.book !== l.book || o.sport !== l.sport) return;
      if (o.player_key === l.player_key) out.push(`${o.player} is on this entry twice. One leg per player.`);
      else if (o.event_id === l.event_id) out.push(`Same match as ${o.player}: results can move together.${l.book === "prizepicks" && o.team !== l.team ? " PrizePicks keeps its same-match limits in its app; Scout does not offer opposing players here." : ""}`);
    });
    return [...new Set(out)];
  }
  function addLeg(rec, side) {
    const why = addRefusal(rec, side);
    if (why) { say(why); return false; }
    const id = legId(rec, side);
    if (T.legs.some((l) => l.id === id)) return true;
    const leg = makeLeg(rec, side);
    T.legs.push(leg);
    slipChanged();
    const notes = legNotes(leg);
    say(`Added ${rec.player.name} ${cap(side)} ${rec.offer.line} to your slip.${notes.length ? " " + notes[0] : ""}`);
    return true;
  }
  /* sq-1 (slip_quality.py): the server ranks legs by their settled band record and builds each slip for the best all-hit chance,
     one leg per match, never repeating a prop when asked. This page maps each leg to the board record it already holds, rechecks
     every slip, and asks again without a leg that fails (at most GEN_ROUNDS asks, GEN_CHECKS rechecks). Nothing is invented here. */
  const GEN_ROUNDS = 3, GEN_CHECKS = 16;
  async function generateBatch(_records, opts, progress) {
    const {platform, count, size, entryType, mix, games} = opts;
    if (!PLATFORMS[platform] || !Number.isInteger(count) || count < 1 || count > 12 || !Number.isInteger(size) || size < 2 || size > 6)
      throw new Error("Choose 1–12 slips and 2–6 picks per slip.");
    if (platform === "underdog" && entryType === "flex" && size < 3) throw new Error("Underdog Flex needs at least 3 picks.");
    if (platform === "underdog" && mix === "mixed") throw new Error("Underdog does not publish mixed-sport entries. Choose PrizePicks or one sport.");
    if (platform === "prizepicks" && games === "stacks") throw new Error("PrizePicks keeps its same-match limits in its app. Same-match legs are built on Underdog only.");
    if (!PLATFORMS[platform].types.some((t) => t[0] === entryType)) throw new Error("Choose a supported entry type.");
    const exclude = new Set(), passed = new Set();
    let checks = 0, last = null, picked = [], note = "";
    for (let round = 0; round < GEN_ROUNDS; round++) {
      const q = new URLSearchParams({ platform, type: entryType, slips: String(count), picks: String(size), sports: opts.sports.join(","), mix, games,
        repeats: opts.noRepeatProps ? "none" : "allow", lines: opts.lines || "no_goblins", research: opts.research ? "1" : "0" });
      if (exclude.size) q.set("exclude", [...exclude].slice(0, 60).join(","));
      let doc;
      try { doc = await api("generate?" + q.toString()); }
      catch (e) { if (e.doc && e.doc.error && e.status === 400) throw new Error(e.doc.error); throw e; }
      last = doc; picked = []; let failed = false;
      for (const s of doc.slips || []) {
        const recs = s.legs.map((l) => (R.boards[l.sport].recs || new Map()).get(offerKey(l.book, l.row_id)));
        if (recs.some((r) => !r)) {                       /* the page's board copy is older than the server's: reload, then ask again */
          s.legs.forEach((l, i) => { if (!recs[i]) exclude.add([l.book, l.row_id, l.side].join("|")); });
          failed = true; continue;
        }
        const legs = recs.map((r, i) => makeLeg(r, s.legs[i].side)), signature = legs.map((l) => l.id).sort().join("\n");
        if (!passed.has(signature)) {
          if (checks >= GEN_CHECKS) { note = "The server could not finish checking more combinations."; break; }
          checks++;
          progress?.(`Checking slip ${picked.length + 1} of ${count}…`);
          let c;
          try { c = await api("recheck", { idem: idem(), legs: legs.map(wireLeg), entry_types: { [platform]: entryType } }); }
          catch (e) { if (!picked.length) throw e; note = "The server could not finish checking more combinations."; break; }
          const bad = s.legs.filter((l, i) => !c.legs?.[legs[i].id]?.ok);
          if (bad.length || !c.entries?.[platform]?.ok) {
            (bad.length ? bad : s.legs).forEach((l) => exclude.add([l.book, l.row_id, l.side].join("|")));
            note = (bad.length ? c.legs?.[legs[s.legs.indexOf(bad[0])].id]?.problems?.[0]?.text : c.entries?.[platform]?.problems?.[0]?.text) || "Some current offers failed recheck.";
            failed = true; continue;
          }
          passed.add(signature);
        }
        const meta = s.legs.map((l) => ({ rank: l.rank, record: l.record, research: !!l.research, line_type: l.line_type }));
        picked.push({ id: idem(), legs: legs.map((l, i) => Object.assign(l, { gen: meta[i] })), checked_at: new Date().toISOString(), entry_type: entryType, platform,
          why: s.why, all_hit: s.all_hit == null ? null : s.all_hit, payout: s.payout ? { multiple: s.payout.multiple, entry_type: s.payout.entry_type } : null,
          ev: s.ev || null, same_match: !!s.same_match });
      }
      if (!failed || note === "The server could not finish checking more combinations.") break;
      await Promise.allSettled(opts.sports.map((sp) => loadBoard(sp, true)));
    }
    const short = picked.length < count ? (note || (last && last.shortage) || "Fewer valid slips than asked with these settings.") : "";
    return { version: 1, generator: last ? last.version : null, created_at: new Date().toISOString(), options: opts, slips: picked, note: short,
      pool: last ? { legs: last.pool.legs, matches: last.pool.matches } : null };
  }
  async function useGenerated(slip) {
    if(!slip||!Array.isArray(slip.legs)||slip.legs.some(l=>!cleanLeg(l)))throw new Error('This saved lineup is invalid. Generate again.');
    const before=sig(), proposed=[...T.legs.filter(l=>l.book!==slip.platform),...slip.legs];
    if(proposed.length>MAX_LEGS)throw new Error('Your other platform entry leaves too little room. Remove picks there first.');
    const types={...T.types,[slip.platform]:slip.entry_type};
    const doc=await api('recheck',{idem:idem(),legs:proposed.map(wireLeg),entry_types:types});
    if(sig()!==before)throw new Error('Your working draft changed. Review it and try again.');
    const bad=slip.legs.find(l=>!doc.legs?.[l.id]?.ok);
    if(bad||!doc.entries?.[slip.platform]?.ok)throw new Error(bad?doc.legs[bad.id]?.problems?.[0]?.text||'A line changed. Generate again.':doc.entries[slip.platform]?.problems?.[0]?.text||'This entry no longer passes recheck.');
    T.legs=proposed;T.types=types;slipChanged();T.check=doc;T.checkSig=sig();T.checkErr=null;paintTray();
  }
  function removeLeg(id) { T.legs = T.legs.filter((l) => l.id !== id); slipChanged(); }
  /* sq-1 adder: each leg's band record and the best replacement that fits the rest of its entry (slip_quality.advise). Read only:
     a replacement is applied only when the member presses Swap in, at the same position, so nothing is dropped or reordered. */
  async function runAdvice() {
    if (!T.legs.length) { T.advice = null; return; }
    const before = sig(), rule = T.slipLines || "no_goblins";
    try {
      const doc = await api("slip-advice", { legs: T.legs.map((l) => Object.assign(wireLeg(l), { stat: l.stat, period: l.period, player_key: l.player_key,
        line_type: R.boards[l.sport]?.recs?.get(offerKey(l.book, l.row_id))?.market?.line_type || undefined })),
        entry_types: Object.fromEntries(entries().map((e) => [e.platform, typeOf(e)])), lines: T.slipLines || "no_goblins" });
      if (sig() === before && rule === (T.slipLines || "no_goblins")) { T.advice = { sig: before, legs: Object.fromEntries((doc.legs || []).map((r) => [r.id, r])) }; paintTray(); }
    } catch (e) { /* advice is extra: the check stays the authority */ }
  }
  const scheduleAdvice = debounce(runAdvice, 900);
  async function replaceLeg(id) {
    const adv = T.advice && T.advice.sig === sig() ? T.advice.legs[id] : null, rep = adv && adv.replacement, i = T.legs.findIndex((l) => l.id === id);
    if (!rep || i < 0) return;
    await loadBoard(rep.sport);
    const rec = R.boards[rep.sport].recs.get(offerKey(rep.book, rep.row_id));
    if (!rec || started(rec)) { say("That line has left the board."); return; }
    const old = T.legs[i], next = makeLeg(rec, rep.side);
    if (T.legs.some((l, j) => j !== i && (l.id === next.id || (l.book === next.book && l.sport === next.sport && l.player_key === next.player_key)))) { say(`${next.player} is already on your slip.`); return; }
    T.legs = T.legs.map((l, j) => (j === i ? next : l));
    slipChanged();
    say(`Swapped ${old.player} for ${next.player} ${cap(next.side)} ${next.line}.`);
  }
  const onSlip = (rec) => T.legs.some((l) => l.sport === rec.sport && l.key === rec.key && l.book === rec.offer.platform);

  const askCheck = () => api("recheck", { idem: idem(), legs: T.legs.map(wireLeg), entry_types: Object.fromEntries(entries().map((e) => [e.platform, typeOf(e)])) });
  async function runCheck() {
    if (!T.legs.length || T.checking) return;
    const before = sig();
    T.checking = true;
    paintTray();
    try {
      const doc = await askCheck();
      if (sig() === before) { T.check = doc; T.checkSig = before; T.checkErr = null; }
    } catch (e) { T.checkErr = e.status === 429 ? "limit" : "down"; }
    T.checking = false;
    if (sig() !== before) scheduleCheck();
    paintTray();
  }
  const scheduleCheck = debounce(runCheck, CHECK_DEBOUNCE);
  function slipPhase() {
    if (!T.legs.length) return "empty";
    if (T.checking && !freshCheck()) return "running";
    if (T.checkErr) return "down";
    return freshCheck() ? "fresh" : "running";
  }
  async function swapLeg(id) {
    const l = T.legs.find((x) => x.id === id), c = T.check && T.check.legs[id];
    if (l) await loadBoard(l.sport, true);
    const cur = l && c && c.current ? R.boards[l.sport].recs.get(offerKey(l.book, c.current.row_id)) : null;
    if (!cur) { say("That offer has left the board."); return; }
    T.legs = T.legs.map((x) => (x.id === id ? makeLeg(cur, l.side, l.added_at) : x));
    slipChanged();
    say(`Now ${cap(l.side)} ${cur.offer.line} at the current offer.`);
  }
  function safeBookLink(url,book){
    if(typeof url!=='string'||url.length>5000)return false;
    if(book==='prizepicks')return /^https:\/\/app\.prizepicks\.com\/board\?projections=\d{1,15}-[ou]-\d+(?:\.\d+)?(?:,\d{1,15}-[ou]-\d+(?:\.\d+)?){1,5}$/.test(url);
    return url==='https://app.underdogsports.com/pick-em/higher-lower/all/home'||
      /^https:\/\/underdogfantasy\.onelink\.me\/vgwg\?af_force_deeplink=true&af_dp=underdogfantasy%3A%2F%2Fmarket-details%2F[0-9a-f-]{36}(?:&af_web_dp=https%3A%2F%2Fapp\.underdogsports\.com%2Fpick-em%2Fhigher-lower%2Fall%2Ffeatured%3Fmatch_id%3D\d{1,12}%26match_type%3DGame)?$/.test(url);
  }
  function openLine(rec,side){
    if(rec&&rec.offer.platform==='underdog')return openBookLeg(makeLeg(rec,side));
  }
  async function openBookLeg(l){
    if(!l||l.book!=='underdog')return;
    try{
      const d=await api('book-line',{legs:[wireLeg(l)]});
      if(!safeBookLink(d.url,'underdog'))throw Error('invalid link');
      let box=$('u-book-line');if(!box){box=document.createElement('dialog');box.id='u-book-line';box.className='u-book-dialog';box.setAttribute('aria-label','Open book line');document.body.appendChild(box);}
      box.innerHTML=`<form method="dialog"><button class="u-x" aria-label="Close book link">×</button></form><h2>${esc(l.player)}</h2><p>${cap(l.side)} ${esc(l.line)} ${esc(l.stat_label)} · ${esc(l.period_label)}</p><p>Choose the side in Underdog.</p><a class="u-btn u-gold" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Open in Underdog</a>`;box.showModal();
    }catch(e){say(e.message||'The current line could not be opened. Refresh and try again.');}
  }
  async function openGenerated(slip){
    if(!slip||T.openBusy)return;
    T.openBusy=true;
    try{
      const doc=await api('recheck',{idem:idem(),legs:slip.legs.map(wireLeg),entry_types:{[slip.platform]:slip.entry_type}});
      const e=doc.entries?.[slip.platform];
      if(!e?.ok||!e.open||!safeBookLink(e.open.url,slip.platform))return say('These picks could not be opened. Recheck the current lines; no picks were sent.');
      let dialog=$('u-book-open');
      if(!dialog){dialog=document.createElement('dialog');dialog.id='u-book-open';dialog.className='u-book-dialog';dialog.setAttribute('aria-label','Open checked slip');document.body.appendChild(dialog);}
      dialog.innerHTML=`<form method="dialog"><button class="u-x" aria-label="Close book links">×</button></form><h2>${esc(e.open.label)}</h2><p>${slip.platform==='prizepicks'?'Your checked selections open in PrizePicks. Confirm its current lines before submitting.':'Underdog opens each market separately. Select Higher or Lower in the app; this does not prefill or place an entry.'}</p><a class="u-btn u-gold" href="${esc(e.open.url)}" target="_blank" rel="noopener noreferrer">${esc(e.open.label)}</a>${(e.open.legs||[]).map((url,i)=>safeBookLink(url,slip.platform)?`<p><a class="u-btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(slip.legs[i]?.player||'Pick '+(i+1))} · ${esc(slip.legs[i]?.side)} ${esc(slip.legs[i]?.line)}</a></p>`:'').join('')}`;
      dialog.showModal();
    }catch(e){say('The line check failed. No book was opened. Try again.');}
    finally{T.openBusy=false;}
  }
  function entryText(e) {
    const lines = [`${e.label} entry`];
    e.legs.forEach((l, i) => lines.push(`${i + 1}. ${l.player} ${cap(l.side)} ${l.line} ${l.stat_label}, ${l.period_label} (${l.event_label})`));
    lines.push(`Checked ${clock(T.check.checked_at)}`);
    return lines.join("\n");
  }
  async function copyEntry(key) {                                 /* validate first: a refused, failed or timed-out check leaves the clipboard untouched */
    const e = entries().find((x) => x.key === key), before = sig();
    if (!e || T.copyBusy) return;
    T.copyBusy = true;
    paintTray();
    let c = null;
    try {
      const doc = await askCheck();
      if (sig() === before) { T.check = doc; T.checkSig = before; T.checkErr = null; c = doc.entries[key]; }
    } catch (err) { c = null; }
    T.copyBusy = false;
    paintTray();
    if (!c || !c.ok) { say(`The check did not pass. Nothing from the ${e.label} entry is on your clipboard.`); return; }
    try { await navigator.clipboard.writeText(entryText(e)); say(`Copied ${e.label} entry as text`); } catch (err) { say(`Copy failed. Nothing from the ${e.label} entry is on your clipboard.`); }
  }
  async function trackEntry(key) {
    const e = entries().find((x) => x.key === key), c = freshCheck();
    if (T.trackBusy[key]) return;
    if (!e || !c || !c.entries[key] || !c.entries[key].trackable) { say("This entry cannot be tracked yet."); return; }
    const k = key + "|" + sig();
    T.trackIdem[k] = T.trackIdem[k] || idem();                    /* a retry of this press gets the same receipts */
    T.trackBusy[key] = true;
    paintTray();
    const ids = new Set(e.legs.map((l) => l.id)), n = e.legs.length;
    try {
      const doc = await api("track", { idem: T.trackIdem[k], entry_key: key, entry_type: typeOf(e), legs: e.legs.map(wireLeg) });
      T.legs = T.legs.filter((l) => !ids.has(l.id));
      T.trackIdem = {};
      T.check = null;
      saveSlip();
      T.tracked = { n, label: e.label, receipt: doc.receipts[0].id };
      say(`${plural(n, "pick")} tracked.`);
      loadMine();
      if (T.legs.length) { scheduleCheck(); scheduleAdvice(); }
    } catch (err) {
      if (err.status === 409 && err.doc && err.doc.legs) { T.check = err.doc; T.checkSig = sig(); say("The check at Track found a change. Nothing was tracked."); }
      else if (err.status === 503) say("Scout is busy. Your slip is still here: press Track again.");
      else say("Track did not finish. Your slip is still here.");
    }
    T.trackBusy[key] = false;
    paintTray();
    markRows();
  }
  async function loadMine() {
    if (T.mineState === "idle") T.mineState = "loading";
    try { T.mine = await api("mine"); T.mineState = "ok"; } catch (e) { T.mineState = T.mine ? "stale" : "unavailable"; }
    paintTray();
  }

  function removeStarted() {
    if (!R.me?.features?.workspace_v2 || !T.ready || T.saving || T.checking) return;
    // Remove only a positively identified, currently loaded event. A missing row
    // may be a feed outage, so it is left for the authoritative server recheck.
    const gone = T.legs.filter(l => {
      const r = R.boards[l.sport].recs.get(offerKey(l.book, l.row_id));
      return r && r.event.id === l.event_id && started(r);
    });
    if (!gone.length) return;
    const ids = new Set(gone.map(l=>l.id));
    T.legs = T.legs.filter(l=>!ids.has(l.id));
    slipChanged();
    say(`Removed ${gone.length} started ${gone.length===1?'leg':'legs'}: ${gone.map(l=>l.player).join(', ')}.`);
  }

  async function shareSlip() {
    const check=freshCheck(), list=entries();
    if (!R.me?.features?.workspace_v2 || !check || T.pending || T.saving || T.saveFail || list.length!==1 || !check.entries[list[0].key]?.ok)
      return say('Save and recheck one platform entry before making its share card.');
    const legs=list[0].legs;
    const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=330+legs.length*140;
    const g=canvas.getContext('2d');
    g.fillStyle='#10151d';g.fillRect(0,0,canvas.width,canvas.height);
    g.fillStyle='#77adff';g.fillRect(0,0,canvas.width,8);
    const text=(value,y,size,color='#f4f7fc')=>{g.font=`${size}px sans-serif`;g.fillStyle=color;let s=String(value);while(s.length&&g.measureText(s).width>960)s=s.slice(0,-1);g.fillText(s===String(value)?s:s.slice(0,-1)+'…',60,y);};
    text('1TIME SCOUT',74,34);text(`${list[0].label} · ${legs.length} picks · ${typeOf(list[0])}`,126,25,'#bbcee8');
    text('RESEARCH LINEUP',166,18,'#94a7bf');
    legs.forEach((l,i)=>{const y=220+i*140;g.strokeStyle='#344153';g.beginPath();g.moveTo(60,y-22);g.lineTo(1020,y-22);g.stroke();text(`${SPORTS[l.sport]}  /  ${l.player}`,y+14,27);text(`${cap(l.side)} ${l.line} ${l.stat_label} · ${l.period_label}`,y+53,24,'#c5dbfb');text(l.event_label,y+86,18,'#99acc4');});
    text(`Lines checked ${when(check.checked_at)}. Recheck in the app before use.`,canvas.height-65,18,'#aebed1');
    text('No stake, combined win chance or return is implied.',canvas.height-32,17,'#aebed1');
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if(!blob)return say('The share image could not be created. Try again.');
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='1Time-Scout-lineup.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    say('Share card downloaded. Nothing was posted or placed.');
  }

  function keepFocus(box, fn) {                                   /* a repaint never drops the keyboard user */
    const a = document.activeElement, inside = a && a !== box && box.contains(a);
    const u = inside && a.dataset ? a.dataset.u : null, id = inside && a.dataset ? a.dataset.id : null;
    fn();
    if (!inside || a.isConnected) return;
    const sel = u ? `[data-u="${CSS.escape(u)}"]` + (id != null ? `[data-id="${CSS.escape(id)}"]` : "") : a.id === "u-ask-q" ? "#u-ask-q" : ".u-x";
    (box.querySelector(sel) || box.querySelector(".u-x") || box).focus({ preventScroll: true });
  }

  /* ---- account slip: drawing ---- */
  const det = (k) => `data-det="${esc(k)}"${T.open.has(k) ? " open" : ""}`;
  function statusLine(phase) {
    if (phase === "empty") return "";
    if (phase === "running") return "<b>Checking</b>";
    if (phase === "down") return T.checkErr === "limit" ? "<b>Too many checks this minute.</b> Scout will check again shortly." : "<b>The check did not answer.</b> Track and Copy stay off.";
    const vals = Object.values(T.check.entries), ready = vals.filter((e) => e.ok).length;
    return `<b>${ready} of ${vals.length} ${vals.length === 1 ? "entry" : "entries"} ready.</b> Checked ${clock(T.check.checked_at)}.`;
  }
  /* SCOUT_SLIP_HEADER_V2 (server flag, default off): spec 4.3. The server sends every entry type of the platform at once, so the
     Power / Flex toggle repaints from the last answer for the same legs at once, before the recheck returns. */
  const signed = (x) => (typeof x !== "number" ? "--" : (x > 0 ? "+" : x < 0 ? "−" : "") + (Math.abs(x) * 100).toFixed(1) + "%");
  const legsKey = (e) => e.platform + "|" + e.legs.map((l) => [l.id, l.rev, l.read_rev, l.shown_p].join(":")).join(";");
  function head2Of(e, c) {
    T.head2 = T.head2 || {};
    const k = legsKey(e);
    if (c) { if (c.slip_header) T.head2[k] = c.slip_header; else delete T.head2[k]; }   /* a fresh answer without one clears it */
    const h = T.head2[k];
    return h && h.types ? h.types[typeOf(e)] || null : null;
  }
  function head2Html(t) {
    const metric = (value, label, cls) => `<div><b${cls ? ` class="${cls}"` : ""}>${value}</b><span>${label}</span></div>`;
    const need = t.break_even != null ? `<p class="u-need">Each leg needs <b>≥ ${pct(t.break_even)}</b>${t.break_even_american != null ? ` (${t.break_even_american > 0 ? "+" : ""}${esc(t.break_even_american)})` : ""}</p>` : "";
    if (!t.rated) return `<div class="u-head2" data-entry="${esc(t.entry)}">${t.payout != null ? `<div class="u-slip-metrics u-slip-head">${metric(esc(t.payout) + "×", "Payout")}</div>` : ""}${need}<p class="u-cant">${esc(t.reason || "Can't rate this entry.")}</p></div>`;
    const values = metric(esc(t.payout) + "×", "Payout") + metric(pct(t.win), "Chance all hit")
      + metric(signed(t.ev), "Average return", t.ev > 0 ? "u-pos" : t.ev < 0 ? "u-neg" : "");
    return `<div class="u-head2" data-entry="${esc(t.entry)}"><div class="u-slip-metrics u-slip-head">${values}</div>${need}<p class="u-dim">From the chances Scout shows on each pick. Average return is per entry, not a win chance.</p></div>`;
  }
  function legHtml(l, phase, link, position, legEv, leg2) {
    const c = phase === "fresh" ? T.check.legs[l.id] : null;
    const evText = leg2 ? "" : legEv && c && c.ok && legEv.index === position - 1 && Number(legEv.line) === Number(l.line) ? ` · <span class="u-leg-ev">Pick EV ${pct(legEv.ev)}</span>` : "";
    const below = leg2 && leg2.below, need2 = below ? `<p class="u-below">Below the ${pct(leg2.need)} each leg needs</p>` : "";
    const problems = c && !c.ok ? `<div class="u-problems">${c.problems.map((p) => `<p>${esc(p.text)}${p.swap ? ` <button class="u-btn" type="button" data-u="swap" data-id="${esc(l.id)}">Use current offer</button>` : ""}${p.retry ? ` <button class="u-btn" type="button" data-u="check">Check again</button>` : ""}</p>`).join("")}</div>` : "";
    const against = l.shown_label.startsWith("Scout side"), read = against ? esc(l.shown_label) : `${esc(l.shown_label)} ${pct(l.shown_p)}`;
    const notes = legNotes(l), adv = T.advice && T.advice.sig === sig() ? T.advice.legs[l.id] : null, rep = adv && adv.replacement;
    const noteHtml = notes.length ? `<div class="u-leg-notes">${notes.map((t) => `<p>${esc(t)}</p>`).join("")}</div>` : "";
    const advHtml = adv && (adv.band || rep) ? `<p class="u-leg-advice">${adv.band ? esc(adv.band) + "." : ""}${rep ? ` ${adv.in_pool ? "Ranks higher here" : "Best replacement"}: ${esc(rep.player)} ${cap(rep.side)} ${esc(rep.line)} ${esc(rep.stat_label)}, ${esc(rep.period_label)} · ${pct(rep.p)}<button class="u-btn" type="button" data-u="replace" data-id="${esc(l.id)}">Swap in</button>` : ""}</p>` : "";
    return `<div class="u-leg${c && !c.ok ? " bad" : ""}${below ? " u-leg-below" : ""}"><span class="u-leg-index" aria-hidden="true">${String(position).padStart(2,'0')}</span><span class="u-leg-name">${esc(l.player)}<i>${esc(SPORTS[l.sport])}</i></span>
      <span class="u-leg-pick">${cap(l.side)} <b>${esc(l.line)}</b> ${esc(l.stat_label)} · ${esc(l.period_label)}</span>
      <span class="u-leg-meta">${read}${evText}${safeBookLink(link,l.book) ? ` · <button class="u-btn" data-u="open-leg" data-id="${esc(l.id)}">Open this line</button>` : ""}</span>
      <button class="u-x" type="button" aria-label="Remove ${esc(l.player)} ${cap(l.side)} ${esc(l.line)}" data-u="leg-x" data-id="${esc(l.id)}">${X_ICON}</button>${need2}${noteHtml}${advHtml}${problems}</div>`;
  }
  /* an entry that only needs more picks is in progress, not refused: its size and two-team rules read as the next step, in
     neutral text; red stays for real conflicts. null = at least one problem is a real conflict (or a leg did not pass). */
  function progressOf(e, c) {
    if (!c || c.ok || !c.problems.length || !e.legs.every((l) => T.check.legs[l.id] && T.check.legs[l.id].ok)) return null;
    const out = [];
    for (const p of c.problems) {
      const size = /entries take (\d+)-(\d+) picks \(this one has (\d+)\)/.exec(p.text || "");
      if (size && +size[3] < +size[1]) { const n = +size[1] - +size[3]; out.push(`Add ${n} more ${n === 1 ? "pick" : "picks"}`); continue; }
      if (/^All picks are from one team/.test(p.text || "")) { out.push("Add 1 pick from another team"); continue; }
      return null;
    }
    return out;
  }
  function entryHtml(e, phase) {
    const c = phase === "fresh" ? T.check.entries[e.key] : null, ready = !!(c && c.ok), can = !!(c && c.trackable), busy = !!T.trackBusy[e.key];
    const types = PLATFORMS[e.platform].types, cur = typeOf(e), prog = progressOf(e, c);
    const seg = `<div class="u-seg" role="group" aria-label="${esc(e.label)} entry type">${types.map(([v, l]) => `<button type="button" aria-pressed="${cur === v}" data-u="etype" data-id="${esc(e.platform + ":" + v)}">${l}</button>`).join("")}</div>`;
    const entryP = prog ? `<p class="u-progress">${esc(prog.join(" · "))}</p>` : c && c.problems.length ? `<div class="u-problems">${c.problems.map((p) => `<p>${esc(p.text)}</p>`).join("")}</div>` : "";
    const rules = c && c.rules && c.rules.length ? `<details class="u-det" ${det("rules:" + c.platform)}><summary>Platform rules checked</summary><ul>${c.rules.map((r) => `<li>${esc(r.label)}: ${esc(r.text)}.${r.note ? ` ${esc(r.note)}` : ""}${r.source ? ` <a href="${esc(r.source)}" target="_blank" rel="noopener">Source</a>${r.page_date ? `, ${esc(shortDate(r.page_date))}` : ""}` : ""}</li>`).join("")}</ul></details>` : "";
    const open = c && c.open && safeBookLink(c.open.url,e.platform) ? `<button class="u-btn u-gold" data-u="open-entry" data-id="${esc(e.key)}">${esc(c.open.label)}</button>` : "";
    const metric = (value, label) => `<div><b>${value}</b><span>${label}</span></div>`;
    const ev = c && c.ev, head = ev?.available && ev.header ? ev.header : null;   /* SCOUT_SLIP_HEADER: present only from the server */
    const values = head ? metric(esc(head.payout_multiple)+'×','All-hit payout')+metric(pct(head.p_all_win),'All picks win, pushes excluded')+metric(pct(head.expected_return),'Expected return')
      : (ev?.payout ? metric(esc(ev.payout.multiple)+'×',ev.payout.reference_only?'Base all-hit payout':'All-hit payout') : '')
      + (ev?.available ? metric(pct(ev.hit_chance),'Win chance')+metric(pct(ev.ev),'Expected return') : '');
    const tiers = ev?.payout?.reference_only&&ev.payout.tiers ? `<details class="u-det"><summary>Published payout tiers</summary><ul>${Object.entries(ev.payout.tiers).map(([losses,multiple])=>`<li>${e.legs.length-Number(losses)} of ${e.legs.length} correct: ${esc(multiple)}× entry fee</li>`).join('')}</ul><p>Base player-pick rates, checked ${esc(ev.payout.read_at)}. Ties, reboots and DNPs change the tier.</p><a href="https://www.prizepicks.com/help-center/payouts" target="_blank" rel="noopener">PrizePicks payout rules</a></details>` : '';
    const t2 = prog ? null : head2Of(e, c);
    const math = prog ? "" : t2 ? `${head2Html(t2)}${tiers}` : `${values?`<div class="u-slip-metrics${head?' u-slip-head':''}">${values}</div>`:''}<p class="u-dim">${esc(ev ? ev.available ? 'Server-checked probabilities and payout. Expected return is not a win chance.' : ev.reason : 'Recheck to verify the payout and probability inputs.')}</p>${tiers}`;
    return `<section class="u-entry${ready ? " ready" : ""}${c && !c.ok && !prog ? " refused" : ""}${prog ? " building" : ""}" aria-label="${esc(e.label)} entry"><div class="u-entry-h"><span>${esc(e.label)}<small>${esc([...new Set(e.legs.map((l) => SPORTS[l.sport]))].join(" + "))} · ${plural(e.legs.length, "pick")}</small></span>${seg}</div>
      ${e.legs.map((l, i) => legHtml(l, phase, c && c.open && c.open.legs ? c.open.legs[i] : null, i + 1, head && head.legs ? head.legs[i] : null, t2 && t2.legs ? t2.legs[i] : null)).join("")}
      <div class="u-entry-foot">${math}${entryP}${rules}${open}
        <div class="u-acts"><button class="u-btn${!open && can ? " u-gold" : ""}" type="button" ${can && !busy ? "" : "disabled"} data-u="track" data-id="${esc(e.key)}">${busy ? "Tracking" : "Track"}</button><button class="u-btn" type="button" ${ready ? "" : "disabled"} data-u="copy" data-id="${esc(e.key)}">${T.copyBusy ? "Checking" : "Copy"}</button></div></div></section>`;
  }
  function slipTab() {
    const n = T.legs.length, phase = slipPhase();
    const done = T.tracked ? `<div class="u-note good"><b>${plural(T.tracked.n, "pick")} tracked on ${esc(T.tracked.label)}.</b> <button class="u-link" type="button" data-u="tab" data-id="picks">View receipts</button></div>` : "";
    const saved = T.saveFail ? '<p class="u-dim">Your changes will save when the connection returns.</p>' : "";
    if (!n) return `${done}${saved}<p class="u-empty">Add a line from any board with + Slip tray. It follows your account across CS2, LoL and Sports.</p>`;
    const retry = phase === "down" ? `<button class="u-link" type="button" data-u="check">Retry</button>` : phase === "fresh" ? `<button class="u-link" type="button" data-u="check">Check again</button>` : "";
    const checks = phase === "fresh" ? `<details class="u-det" ${det("checks")}><summary>Check details</summary><ul>${CHECKS.map((t, i) => `<li><b class="${T.check.fails[i] ? "no" : "yes"}">${T.check.fails[i] ? "Refused" : "Pass"}</b> ${t}${T.check.fails[i] ? ` (${esc(T.check.fails[i])})` : ""}</li>`).join("")}</ul></details>` : "";
    return `${done}<div class="u-status"><p>${statusLine(phase)} ${retry}</p><button class="u-link" type="button" data-u="clear">Clear</button></div>${saved}${checks}${entries().map((e) => entryHtml(e, phase)).join("")}`;
  }
  const countsText = (c) => Object.entries(c || {}).filter(([, n]) => n).map(([k, n]) => `${n} ${STATUS[k] ? STATUS[k].toLowerCase() : k}`).join(" · ");
  function receiptHtml(r) {
    const st = r.status, open = T.openPick === r.id, scoutSide = cap(r.scout_side || ""), against = r.scout_side && r.side !== r.scout_side;
    const read = `${r.grade || r.hold || (r.research ? "Research estimate" : "Lean")} ${scoutSide}`.trim();
    const kv = [["Chance you saw", r.displayed_p == null ? "None saved for this side" : pct(r.displayed_p)], ["Scout read", read], ...(against ? [["Your side", `${cap(r.side)}, against Scout`]] : []),
      ["Projection", r.projection == null ? "--" : tenth(r.projection)], ["Book", r.platform_label], ["Tracked", when(r.tracked_at)], ["Receipt", r.id]];
    const body = open ? `<div class="u-pick-body"><dl class="u-kv">${kv.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl><ol class="u-time">${(r.timeline || []).map((t) => `<li><time>${esc(when(t.at))}</time>${esc(t.text)}</li>`).join("")}</ol></div>` : "";
    return `<article class="u-pick"><button type="button" aria-expanded="${open}" data-u="pick" data-id="${esc(r.id)}"><span><b>${esc(r.player)} ${cap(r.side)} ${esc(r.line)}</b><small>${esc(SPORTS[r.sport] || "")} · ${esc(r.stat_label)} · ${esc(r.period_label)} · ${esc(r.matchup)}</small></span><span class="u-st ${esc(st)}">${esc(STATUS[st] || st)}${r.final != null ? ` ${esc(r.final)}` : ""}</span></button>${body}</article>`;
  }
  function picksTab() {
    const d = T.mine;
    if (!d) return T.mineState === "unavailable" ? '<p class="u-empty">Your receipts are saved. Scout could not read them just now.</p>' : '<p class="u-empty">Loading receipts</p>';
    if (!d.receipts.length) return '<p class="u-empty">No tracked picks yet.</p>';
    return d.slips.map((t) => {
      const legs = d.receipts.filter((r) => r.slip_id === t.slip_id);
      return legs.length ? `<section class="u-slipcard"><div class="u-slip-h"><b>${esc((PLATFORMS[t.platform] || {}).label || t.platform)}${t.entry_type ? ` · ${esc(cap(t.entry_type))}` : ""}</b><span>${esc(when(t.tracked_at))}</span><span>${esc(countsText(t.counts))}</span></div>${legs.map(receiptHtml).join("")}</section>` : "";
    }).join("");
  }
  function recordTab() {
    const d = T.mine;
    if (!d) return T.mineState === "unavailable" ? '<p class="u-empty">Your record is unavailable right now.</p>' : '<p class="u-empty">Loading your record</p>';
    const r = d.record, rate = r.hit_rate == null ? "--" : pct(r.hit_rate);
    const row = (g) => `<tr><th scope="row">${esc(g.name)}</th><td>${g.hit}-${g.miss}-${g.push}</td><td>${g.hit_rate == null ? "--" : pct(g.hit_rate)}</td></tr>`;
    return `<div class="u-tiles"><div><b>${r.hit}-${r.miss}-${r.push}</b><span>Hit, miss, push</span></div><div><b>${rate}</b><span>Hit rate, ${r.decided} decided</span></div><div><b>${r.void}</b><span>Void</span></div><div><b>${r.pending}</b><span>Pending</span></div></div>
      <p class="u-dim">${esc(r.rule)} ${esc(r.scope)}.</p>${[...(d.by_sport || []), ...(d.by_platform || [])].length ? `<table class="u-tbl"><thead><tr><th scope="col">Group</th><th scope="col">Hit-miss-push</th><th scope="col">Hit rate</th></tr></thead><tbody>${[...(d.by_sport || []), ...(d.by_platform || [])].map(row).join("")}</tbody></table>` : ""}`;
  }
  function paintTray() {
    if (HUB) HUB.refreshSlip();
    const btn = $("u-tray-btn");
    if (!btn) return;
    const n = T.legs.length, c = freshCheck(), vals = c ? Object.values(c.entries) : [], ready = vals.filter((e) => e.ok).length;
    btn.textContent = `Slip tray · ${n}${vals.length ? ` · ${ready} of ${vals.length} ready` : ""}`;
    const box = $("u-tray");
    if (!box.open) return;
    const body = $("u-tray-body"), top = body.scrollTop;
    const tabs = [["slip", `Slip · ${n}`], ["picks", "Receipts"], ["record", "Record"]];
    keepFocus(box, () => {
      $("u-tray-tabs").innerHTML = tabs.map(([k, l]) => `<button type="button" role="tab" aria-selected="${T.tab === k}" data-u="tab" data-id="${k}">${esc(l)}</button>`).join("");
      body.innerHTML = T.tab === "slip" ? slipTab() : T.tab === "picks" ? picksTab() : recordTab();
    });
    body.scrollTop = top;
  }
  function openTray(tab) {
    if (tab) T.tab = tab;
    if (!$("u-tray").open) $("u-tray").showModal();
    paintTray();
    if (T.tab !== "slip" && T.mineState === "idle") loadMine();
    syncState();
    if (T.legs.length && !freshCheck()) scheduleCheck();
  }

  /* ---- research drawer ---- */
  const P = { rec: null, doc: null, state: "idle", tab: "overview", side: "over", histN: 10, seq: 0, only: null };
  const seriesOf = () => (P.doc && P.doc.history ? P.doc.history.series : null);
  function tallyOf(rec, n) {                                      /* past games at this line when they are here, else the board's own count */
    const res = seriesOf(), line = rec.offer.line, saved = tallies(rec)["l" + n];
    if (!res) return saved;
    const list = res.slice(0, n);
    if (list.length < Math.min(n, 5) || (n === 20 && list.length <= 10)) return saved;
    return { over: list.filter((g) => g.value > line).length, under: list.filter((g) => g.value < line).length, push: list.filter((g) => g.value === line).length, n: list.length };
  }
  function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), n = x / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
  function chartSvg(results, line, side, w) {
    /* opponent names in full where they fit, each game's date under its name (audit 2026-11-04); otherwise angled as one
       "name · date" label, so a name is never cut to "benche" */
    const dateOf = (d) => (d.date ? shortDate(String(d.date)) : "");
    const names = results.map((d) => String(d.opponent || "")), dates = results.map(dateOf);
    const flat = Math.max(0, ...names.map((x) => Math.min(x.length, 14)), ...dates.map((x) => x.length));
    const slot0 = (w - 36) / Math.max(1, results.length), angled = flat * 6.2 > slot0 - 4;
    const longest = angled ? Math.max(0, ...results.map((d, i) => Math.min(names[i].length, 14) + (dates[i] ? dates[i].length + 3 : 0))) : flat;
    const data = results.slice().reverse(), h = angled ? 166 + Math.round(longest * 4.4) : 184, m = { l: 30, r: 6, t: 22, b: angled ? Math.round(longest * 4.4) + 20 : 38 }, pw = w - m.l - m.r, ph = h - m.t - m.b;
    const vals = data.map((d) => d.value), closes = data.map((d) => d.closing).filter((c) => typeof c === "number"), top = Math.max(line, ...vals, ...closes, 1) * 1.12, y = (v) => m.t + ph - (v / top) * ph, base = m.t + ph;
    const slot = pw / data.length, bw = Math.min(26, Math.max(6, slot * 0.62)), step = niceStep(top / 3), every = angled ? Math.ceil(13 / slot) : Math.ceil(44 / slot);
    const label = (x) => (x.length > 14 ? x.slice(0, 13) + "…" : x);
    let out = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">`;
    for (let t = 0; t <= top; t += step) out += `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${m.l - 6}" y="${y(t) + 3.5}" text-anchor="end">${Math.round(t * 10) / 10}</text>`;
    data.forEach((d, i) => {
      const x = m.l + slot * i + (slot - bw) / 2, yv = y(d.value), push = d.value === line, over = d.value > line, cls = push ? "b-push" : (side === "over" ? over : !over) ? "b-hit" : "b-miss";
      out += `<rect class="${cls}" x="${x}" y="${yv}" width="${bw}" height="${Math.max(base - yv, 1)}" rx="3"><title>${esc(`${shortDate(d.date)} v ${d.opponent}: ${d.value}`)}</title></rect>`;
      if (i === data.length - 1) out += `<text x="${x + bw / 2}" y="${yv - 5}" text-anchor="middle">${esc(d.value)}</text>`;
      if (typeof d.closing === "number") out += `<line class="close-mark" x1="${x - 3}" x2="${x + bw + 3}" y1="${y(d.closing)}" y2="${y(d.closing)}"><title>${esc(`${shortDate(d.date)} v ${d.opponent}: closed at ${d.closing}, finished ${d.value}`)}</title></line>`;   /* lh-1: the line this game closed at */
      const day = dateOf(d);
      if ((data.length - 1 - i) % every === 0) out += angled ? `<text class="opp" x="${x + bw / 2 + 3}" y="${base + 12}" text-anchor="end" transform="rotate(-40 ${x + bw / 2 + 3} ${base + 12})">${esc(label(String(d.opponent || "")) + (day ? " · " + day : ""))}</text>` : `<text class="opp" x="${x + bw / 2}" y="${h - 21}" text-anchor="middle">${esc(String(d.opponent || ""))}</text>${day ? `<text class="date" x="${x + bw / 2}" y="${h - 7}" text-anchor="middle">${esc(day)}</text>` : ""}`;
    });
    return out + `<line class="line-mark" x1="${m.l}" x2="${w - m.r}" y1="${y(line)}" y2="${y(line)}"/><text class="line-lab" x="${w - m.r}" y="${m.t - 10}" text-anchor="end">Line ${esc(line)}</text></svg>`;
  }
  function tilesHtml(rec, side) {
    const s = rec.scout, ln = P.doc, m = ln && ln.market && ln.market.available ? (side === "under" ? ln.market.p_under : ln.market.p_over) : null;
    const tile = (v, l, cls) => `<div${cls ? ` class="${cls}"` : ""}><b>${v}</b><span>${esc(l)}</span></div>`;
    const windows = [5, 10, 20].map((n) => [n, tallyOf(rec, n)]).filter(([, t]) => t);
    const hits = windows.length ? windows.map(([n, t]) => tile(`${side === "under" ? t.under : t.over}/${t.n}`, `L${n}, ${cap(side)}`)).join("") : tile("--", "No past results at this line");
    return `<div class="u-tiles">${hits}${tile(chanceOf(rec, side) != null ? pct(chanceOf(rec, side)) : "--", `Forecast chance, ${cap(side)}`, "gold")}${m != null ? tile(pct(m), `Market chance, ${cap(side)}`) : ""}${tile(s.projection != null ? esc(tenth(s.projection)) : "--", "Projection")}${rec.history.avg != null ? tile(esc(rec.history.avg), "Recent average") : ""}</div>`;
  }
  function chartHtml(rec, side, full) {
    const res = seriesOf(), t = tallies(rec).l10;
    if (!res) {
      if (P.state === "loading") return '<div class="u-skel tall"></div>';
      return `<p class="u-dim">${t ? esc(`Last ${t.n} at this line: ${t.over} over, ${t.under} under, ${t.push} push.`) : "No past results at this line."}</p>`;
    }
    const n = full ? P.histN : 10, list = res.slice(0, n), line = rec.offer.line;
    const seg = full ? `<div class="u-seg" role="group" aria-label="Games shown">${[5, 10, 20].filter((v) => v <= Math.max(res.length, 5)).map((v) => `<button type="button" aria-pressed="${P.histN === v}" data-u="hist" data-id="${v}">L${v}</button>`).join("")}</div>` : `<button class="u-link" type="button" data-u="ptab" data-id="history">Open history</button>`;
    const table = full ? `<details class="u-det" open><summary>Results as a table</summary><table class="u-tbl"><thead><tr><th scope="col">Played</th><th scope="col">Opponent</th><th scope="col">Final</th><th scope="col">Against ${esc(line)}</th></tr></thead><tbody>${list.map((g) => `<tr><td>${esc(shortDate(g.date))}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td>${g.value === line ? "Push" : g.value > line ? "Over" : "Under"}</td></tr>`).join("")}</tbody></table></details>` : "";
    return `<div class="u-sec-h"><h3>Past results at this line</h3>${seg}</div><div class="u-chart" data-chart data-n="${n}" role="img" aria-label="${list.length} past results against the line ${esc(line)}"></div><div class="u-legend"><span><i class="h"></i>Hit for ${cap(side)}</span><span><i></i>Miss</span><span><i class="p"></i>Push</span><span><i class="l"></i>Line ${esc(line)}</span></div>${table}`;
  }
  function mapsHtml() {
    const res = seriesOf(), by = new Map();
    (res || []).forEach((g) => (g.maps || []).forEach((name, i) => {
      if (!name || !g.values || g.values[i] == null) return;
      const e = by.get(name) || { n: 0, t: 0 };
      e.n++; e.t += g.values[i];
      by.set(name, e);
    }));
    if (!by.size) return "";
    return `<div class="u-sec-h"><h3>By map, last ${res.length} series</h3></div><table class="u-tbl"><thead><tr><th scope="col">Map</th><th scope="col">Played</th><th scope="col">Average per map</th></tr></thead><tbody>${[...by.entries()].sort((a, b) => b[1].n - a[1].n).map(([name, e]) => `<tr><td>${esc(name)}</td><td>${e.n}</td><td>${(e.t / e.n).toFixed(1)}</td></tr>`).join("")}</tbody></table>`;
  }
  function forecastHtml(rec, side) {
    const s = rec.scout, p = chanceOf(rec, side), val = s.validation || {}, off = s.side && side !== s.side && p == null;
    const stake = !s.hold && s.units > 0 ? ` · Stake ${esc(s.units)}U` : '';
    return `<div class="u-forecast"><div class="u-fc-num">${off ? "--" : pct(p)}</div><div><p>${off ? `Scout saved a chance for ${cap(s.side)} only` : `Forecast chance of ${cap(side)} ${esc(rec.offer.line)}`}</p><p><b>${esc(verdictText(rec, side))}</b>${stake}</p></div></div>${s.range80 ? `<p class="u-dim">80% range ${esc(s.range80[0])} to ${esc(s.range80[1])}</p>` : ""}${val.detail || s.stake_reason ? `<details class="u-det"><summary>Grade methodology</summary><p>${esc(val.detail || s.stake_reason)}</p></details>` : ""}`;
  }
  function sidesHtml(rec, side) {
    const b = (sd) => {
      const v = rec.offer[sd];
      return `<button class="u-side" type="button" aria-pressed="${side === sd}" ${v.offered ? "" : "disabled"} data-u="pside" data-id="${sd}"><b>${cap(sd)} ${esc(rec.offer.line)}</b><span>${chanceOf(rec, sd) != null ? pct(chanceOf(rec, sd)) : "No saved chance"}</span><small>${v.offered ? esc(priceText(rec, sd) || lineLabel(rec)) : "Not offered on " + esc(rec.offer.platform_label)}</small></button>`;
    };
    return `<div class="u-sides">${b("over")}${b("under")}</div>`;
  }
  function booksTable(list, cur) {
    const best = (sd) => {
      const o = list.filter((x) => x.offer[sd].offered && decimalOf(x, sd) != null);
      return o.length < 2 ? null : o.reduce((a, b) => (decimalOf(b, sd) > decimalOf(a, sd) ? b : a));
    };
    const cell = (o, sd) => { const t = priceText(o, sd) || (o.offer[sd].offered ? lineLabel(o) : "Not offered"), top = best(sd) === o; return `<td${top ? ' class="best"' : ""}>${esc(t)}</td>`; };
    return `<table class="u-tbl"><thead><tr><th scope="col">Book</th><th scope="col">Line</th><th scope="col">Over</th><th scope="col">Under</th><th scope="col">Scout</th><th scope="col">Seen</th></tr></thead><tbody>${list.map((o) => {
      const here = o === cur, sp = o.scout.p_side;
      return `<tr${here ? ' class="on"' : ""}><td><button class="u-link" type="button" data-u="pline" data-id="${esc(offerKey(o.offer.platform, o.offer.offer_id))}">${esc(o.offer.platform_label)}</button></td><td>${esc(o.offer.line)}${o.market.line_type !== "standard" ? ` <small>${esc(cap(o.market.line_type))}</small>` : ""}</td>${cell(o, "over")}${cell(o, "under")}<td>${esc(verdictText(o))}${sp == null ? "" : ` ${pct(sp)}`}</td><td>${o.offer.seen_at ? esc(when(o.offer.seen_at)) : "--"}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function booksHtml(rec) {
    const ln = P.doc, list = siblings(rec), same = list.filter((o) => o.offer.line === rec.offer.line), other = list.filter((o) => o.offer.line !== rec.offer.line);
    const market = ln && ln.market ? (ln.market.available ? `<p class="u-dim">Market chance from ${esc(ln.market.source)}: Over ${pct(ln.market.p_over)}, Under ${pct(ln.market.p_under)}.</p>` : `<p class="u-dim">${esc(ln.market.reason)}</p>`) : "";
    const mb = (ln && ln.market_books) || null, price = (v) => (v == null ? "--" : esc(v));
    const mbRow = (b, showLine) => `<tr><td>${esc(b.platform_label)}${showLine ? ` <small>${esc(b.line)}</small>` : ""}</td><td>${price(b.over)}</td><td>${price(b.under)}</td><td>${b.chance != null ? pct(b.chance) : "--"}</td></tr>`;
    const mbHead = '<thead><tr><th scope="col">Book</th><th scope="col">Over</th><th scope="col">Under</th><th scope="col">Its chance</th></tr></thead>';
    const extra = mb ? mb.same.filter((b) => !same.some((o) => o.offer.platform === b.platform)) : [];
    return `<div class="u-sec-h"><h3>Line ${esc(rec.offer.line)}</h3></div>${booksTable(same, rec)}${extra.length ? `<div class="u-sec-h"><h3>Market books at ${esc(rec.offer.line)}</h3></div><table class="u-tbl">${mbHead}<tbody>${extra.map((b) => mbRow(b, false)).join("")}</tbody></table>` : ""}${market}
      ${other.length ? `<div class="u-sec-h"><h3>Other lines</h3><span class="u-tag">Different line</span></div><p class="u-dim">Each line is its own bet with its own forecast.</p>${booksTable(other, rec)}` : ""}
      ${mb && mb.other.length ? `<div class="u-sec-h"><h3>Market books, other lines</h3></div><table class="u-tbl">${mbHead}<tbody>${mb.other.map((b) => mbRow(b, true)).join("")}</tbody></table>` : ""}`;
  }
  function contextHtml(rec) {
    if (!P.doc) return P.state === "loading" ? '<div class="u-skel"></div><div class="u-skel"></div>' : '<p class="u-dim">Context is unavailable right now.</p>';
    const list = (P.doc.context && P.doc.context.rows) || [], drivers = (P.doc.context && P.doc.context.drivers) || [], hh = P.doc.h2h, ln = rec.offer.line;
    const h2h = !hh || !hh.opponent ? "" : `<div class="u-sec-h"><h3>Head-to-head vs ${esc(hh.opponent)}</h3></div>${hh.n ? `<dl class="u-kv"><div><dt>Over ${esc(ln)}</dt><dd>${hh.over} of ${hh.n}</dd></div><div><dt>Under ${esc(ln)}</dt><dd>${hh.under} of ${hh.n}</dd></div><div><dt>Average</dt><dd>${hh.avg == null ? "—" : esc(hh.avg)}</dd></div>${hh.series.map((g) => `<div><dt>${esc(g.date)}</dt><dd>${esc(g.value)} · ${g.value === ln ? "Push" : g.value > ln ? "Over" : "Under"}</dd></div>`).join("")}</dl><p class="u-dim">Past meetings at today's line. Not a predicted chance.</p>` : `<p class="u-dim">No past meetings with ${esc(hh.opponent)} in this player's saved results.</p>`}`;
    return `${h2h}<div class="u-sec-h"><h3>${rec.sport === "cs2" ? "Player stats and saved inputs" : "Context"}</h3></div>${list.length ? `<dl class="u-kv">${list.map((r) => `<div><dt>${esc(r.label)}</dt><dd>${esc(r.value)}</dd></div>`).join("")}</dl>` : `<p class="u-dim">${rec.sport === "cs2" ? "This forecast saved no context inputs." : "No context is on file for this line."}</p>`}${drivers.length ? `<div class="u-sec-h"><h3>What moved this forecast</h3></div><ul class="u-list">${drivers.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}`;
  }
  /* SCOUT_GENIE (features.genie, owner switch): one question about this line, answered by the server from this line's payload only,
     every number checked there; a daily cap per member. Off, the tab is the evidence guide alone, unchanged. */
  const G = { key: null, q: "", busy: false, ans: null, err: null, quota: null };
  function askHtml(rec) {
    const k = rec.key + "|" + rec.offer.platform + "|" + P.side;
    if (G.key !== k) Object.assign(G, { key: k, ans: null, err: null });
    const left = G.quota ? `<p class="u-dim">${G.quota.remaining_questions} of ${G.quota.questions_limit} questions left today.</p>` : "";
    const sugg = ["What is the projection?", "How often did this line hit?", "What is the head-to-head?", "Is there a better line elsewhere?"];
    // SCOUT_\u{1F9DE}_PERSONA (features.genie_persona): a light genie voice in the intro and the answer opener; the checked sentences are unchanged
    const persona = R.me?.features?.genie_persona === true;
    const out = G.busy ? '<div class="u-skel"></div>' : G.err ? `<p>${esc(G.err)}</p>` : G.ans ? (persona && G.intro ? `<p class="u-genie-intro">${esc(G.intro)}</p>` : "") + G.ans.map((x) => `<p>${esc(x)}</p>`).join("") : "";
    return `<form class="u-ask" id="u-ask" novalidate>${persona ? `<p class="u-genie-intro">\u{1F9DE} Ask the Genie. I grant answers from this line's saved Scout data only, never a made-up stat.</p>` : ""}<label for="u-ask-q">${persona ? "Make a wish about this line" : "Ask about this line"}</label><div class="u-ask-row"><input id="u-ask-q" name="q" type="text" maxlength="300" autocomplete="off" value="${esc(G.q)}"><button class="u-btn u-gold" type="submit" data-u="genie-ask"${G.busy ? ' aria-disabled="true"' : ""}>Ask</button></div>
      <div class="u-ask-sugg" role="group" aria-label="Suggested questions">${sugg.map((x) => `<button class="u-btn" type="button" data-u="genie-ask" data-id="${esc(x)}">${esc(x)}</button>`).join("")}</div>
      <div class="u-ask-out" id="u-ask-out" role="status" aria-live="polite">${out}</div>${left}</form>`;
  }
  async function genieAsk(text) {
    const rec = P.rec, box = $("u-ask-q"), q = (text || (box ? box.value : "") || "").trim();
    if (!rec || G.busy) return;
    G.q = q;
    if (!q) { G.err = "Type a question about this line first."; paintDrawer(); return; }
    const k = G.key;
    Object.assign(G, { busy: true, err: null, ans: null }); paintDrawer();
    try {
      const d = await api("genie", { sport: rec.sport, book: rec.offer.platform, id: rec.offer.offer_id, revision: rec.offer.revision, side: P.side, question: q });
      if (G.key !== k) return;
      G.ans = d.sentences; G.quota = d.quota; G.intro = d.intro || null;
    } catch (e) {
      if (G.key !== k) return;
      const c = e instanceof ApiError ? e.status : 0;
      if (c === 429 && e.doc && e.doc.quota) G.quota = e.doc.quota;
      G.err = c === 429 ? (e.doc && e.doc.error === "daily_limit_reached" ? (R.me?.features?.genie_persona ? "\u{1F9DE} Your wishes are spent for today. They reset at midnight UTC." : "You have used today's questions. They reset at midnight UTC.") : "Too many questions in a minute. Try again shortly.")
        : c === 409 ? "This line changed. Reopen it to ask about the current offer." : c === 413 ? "Keep the question under 300 characters." : "The Genie is unavailable right now.";
    } finally { if (G.key === k) { G.busy = false; paintDrawer(); } }
  }
  function genieHtml(rec) {
    const doc=P.doc, s=rec.scout, side=P.side, t=tallyOf(rec,10), list=siblings(rec), other=list.filter(r=>r.offer.platform!==rec.offer.platform);
    return `${R.me?.features?.genie ? askHtml(rec) : ""}<div class="u-genie"><span class="u-tag">Scout evidence guide</span><h3>What would you like to check?</h3><p class="u-dim">Answers use this offer's saved forecast and research data. No invented stats, web guesses or new predictions.</p>
      <details class="u-det" open><summary>What supports this pick?</summary><p>${esc(rec.player.name)} has a saved projection of ${s.projection==null?'unavailable':esc(tenth(s.projection))} for ${esc(rec.market.period_label)} ${esc(rec.market.stat_label)}. ${chanceOf(rec,side)==null?'No probability was saved for the selected side.':`The saved chance for ${cap(side)} ${esc(rec.offer.line)} is ${pct(chanceOf(rec,side))}.`} ${s.hold?'This is a research estimate; the validation gate has not released official grades.':''}</p>${doc&&doc.forecast?`<p class="u-dim">Forecast ${esc(doc.forecast.id||'ID unavailable')} · model ${esc(doc.forecast.model_version||s.model_version||'unavailable')}</p>`:''}</details>
      <details class="u-det"><summary>How often did this line hit?</summary><p>${t?`${side==='under'?t.under:t.over} of the last ${t.n} comparable results hit ${cap(side)} at today's line of ${esc(rec.offer.line)}; ${t.push||0} pushed. This is historical frequency, not a forecast.`:'Comparable past-game results are not available for this line.'}</p><button class="u-link" data-u="ptab" data-id="history">Inspect each result</button></details>
      <details class="u-det"><summary>Is there a better line elsewhere?</summary><p>${other.length?other.slice(0,8).map(r=>`${esc(r.offer.platform_label)} lists ${esc(r.offer.line)} (${esc(r.market.line_type)}), seen ${esc(when(r.offer.seen_at))}.`).join(' '):'No other platform offer is available for this exact event, player, stat and period.'}</p><p class="u-dim">Different lines and payout types are not interchangeable. Compare the actual offer before adding it.</p><button class="u-link" data-u="ptab" data-id="books">Compare sources</button></details>
      <details class="u-det"><summary>What should I verify before tracking?</summary><p>Confirm ${esc(rec.market.period_label)}, the ${esc(rec.offer.platform_label)} line and offered side, the start time, and any player context. Scout rechecks the exact revision when you track. ${esc(s.stake_reason||'Saved probabilities can be wrong; the performance record shows their measured accuracy.')}</p><button class="u-link" data-u="ptab" data-id="context">View saved context</button></details></div>`;
  }
  function paintDrawer() {
    const box = $("u-drawer"), rec = P.rec;
    if (!box.open || !rec) return;
    const side = P.side, body = P.tab === "genie" ? genieHtml(rec) : P.tab === "history" ? chartHtml(rec, side, true) + mapsHtml() : P.tab === "books" ? booksHtml(rec) : P.tab === "context" ? contextHtml(rec)
      : tilesHtml(rec, side) + chartHtml(rec, side, false) + forecastHtml(rec, side) + sidesHtml(rec, side);
    const tabs = (P.only || ["overview", "history", "books", "context", "genie"]).map((t) => `<button type="button" role="tab" aria-selected="${P.tab === t}" data-u="ptab" data-id="${t}">${t==='genie'?'Ask the Genie':cap(t)}</button>`).join("");
    const lines = siblings(rec), distinct = [...new Map(lines.map((o) => [o.offer.line, o])).values()];
    const picker = distinct.length > 1 ? `<span class="u-lines" role="group" aria-label="Lines on this prop">${distinct.map((o) => `<button type="button" aria-pressed="${o.offer.line === rec.offer.line}" data-u="pline" data-id="${esc(offerKey(o.offer.platform, o.offer.offer_id))}">${esc(o.offer.line)}</button>`).join("")}</span>` : `<b class="u-line">${esc(rec.offer.line)}</b>`;
    const refusal = addRefusal(rec, side), inSlip = T.legs.find((l) => l.id === legId(rec, side)), note = started(rec) ? `<p class="u-note">${esc(startWord(rec))} at ${esc(when(rec.event.start_utc))}</p>` : "";
    const scroll = $("u-drawer-body") ? $("u-drawer-body").scrollTop : 0;
    keepFocus(box, () => { box.innerHTML = `<div class="u-d-head"><div><h2 id="u-drawer-title">${esc(rec.player.name)}</h2>${picker}<p>${esc(SPORTS[rec.sport])} · ${esc(rec.market.stat_label)} · ${esc(rec.market.period_label)} · ${esc(rec.offer.platform_label)}</p><p>${esc(rec.player.team)}${rec.player.role ? " " + esc(rec.player.role) : ""} · ${esc(rec.event.label)}${rec.event.competition ? " · " + esc(rec.event.competition) : ""} · ${esc(when(rec.event.start_utc))}${rec.event.best_of ? ` · BO${esc(rec.event.best_of)}` : ""}${rec.offer.moved_from != null ? ` · Moved from ${esc(rec.offer.moved_from)}` : ""}</p></div><button class="u-x" type="button" aria-label="Close research" data-u="pclose">${X_ICON}</button></div>
      <div class="u-tabs" role="tablist" aria-label="Research sections">${tabs}</div><div class="u-d-body" id="u-drawer-body" tabindex="0">${note}${body}</div>
      <div class="u-d-foot">${refusal && !inSlip ? `<p class="u-dim" role="status">${esc(refusal)}</p>` : ""}<div class="u-acts">${R.me.features?.book_links&&rec.offer.platform==='underdog'&&!refusal?'<button class="u-btn" type="button" data-u="open-line">Open in Underdog</button>':''}<button class="u-btn" type="button" data-u="tray">Slip tray · ${T.legs.length}</button><button class="u-btn u-gold" type="button" ${refusal && !inSlip ? 'aria-disabled="true"' : ""} data-u="padd">${inSlip ? "Remove from slip tray" : `Add ${cap(side)} ${esc(rec.offer.line)} on ${esc(rec.offer.platform_label)}`}</button></div></div>`; });
    $("u-drawer-body").scrollTop = scroll;
    const res = seriesOf();
    if (res) box.querySelectorAll("[data-chart]").forEach((el) => { el.innerHTML = chartSvg(res.slice(0, +el.dataset.n), rec.offer.line, side, Math.max(240, Math.round(el.clientWidth))); });
  }
  async function researchDoc(rec) {
    const k = [rec.sport, rec.offer.platform, rec.offer.offer_id, rec.offer.revision, rec.scout.read_rev].join('|'), had = R.lines[k];
    if (had && Date.now() - had.at < 15000) return had.doc;
    const doc = await api(`line?${rec.sport === "cs2" ? "" : "sport=" + rec.sport + "&"}book=${encodeURIComponent(rec.offer.platform)}&id=${encodeURIComponent(rec.offer.offer_id)}`);
    // A refresh may return a different revision. Never chart/add it as the selected offer.
    const now = doc.record;
    if (!now || now.sport !== rec.sport || propKey(now) !== propKey(rec) || now.offer.platform !== rec.offer.platform
      || now.offer.offer_id !== rec.offer.offer_id || now.offer.revision !== rec.offer.revision || now.offer.line !== rec.offer.line
      || now.scout.read_rev !== rec.scout.read_rev) throw new Error('offer_changed');
    R.lines[k] = {doc, at: Date.now()};
    const keys = Object.keys(R.lines);
    keys.slice(0, Math.max(0, keys.length - 32)).forEach(x => delete R.lines[x]);
    return doc;
  }
  async function loadLine() {
    const rec = P.rec, seq = ++P.seq;
    P.doc = null; P.state = "loading";
    paintDrawer();
    try {
      const doc = await researchDoc(rec);
      if (seq === P.seq) { P.doc = doc; P.state = "ok"; }
    } catch (e) { if (seq === P.seq) P.state = "unavailable"; }
    if (seq === P.seq) paintDrawer();
  }
  /* only: the tabs to show. The Research hub card already shows the overview, history and context, so its drawer keeps the rest */
  function research(rec, tab, only, side) {
    P.only = Array.isArray(only) && only.length ? only : null;
    P.rec = rec; P.tab = tab || (P.only ? P.only[0] : "overview"); P.side = side || rec.scout.side || "over";
    if (!rec.offer[P.side].offered) P.side = P.side === "over" ? "under" : "over";
    if (!$("u-drawer").open) $("u-drawer").showModal();
    loadLine();
  }

  /* ---- feedback + shell ---- */
  let toastT;
  function say(msg) {
    const box = document.querySelector("dialog[open].u-dialog"), t = (box && box.querySelector(".u-toast")) || $("u-toast");
    document.querySelectorAll(".u-toast").forEach((x) => { x.textContent = ""; x.classList.remove("on"); });
    t.textContent = msg; t.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove("on"), 3200);
  }
  /* desktop: dock the tray button in the page's own top bar (re-docked when a page redraws its header); phone: bottom corner */
  function dockTray() {
    const wide = matchMedia("(min-width:1025px)");
    const place = () => {
      const btn = $("u-tray-btn"), head = document.querySelector("header.sw-top") || document.querySelector("body header");
      if (!btn) return;
      if (wide.matches && head) {
        if (btn.parentNode !== head) head.appendChild(btn);
        btn.classList.add("u-docked");
      } else {
        if (btn.parentNode !== document.body) document.body.appendChild(btn);
        btn.classList.remove("u-docked");
      }
    };
    place();
    wide.addEventListener("change", place);
    const head = document.querySelector("header.sw-top");
    if (head) new MutationObserver(() => { if (wide.matches && !head.contains($("u-tray-btn"))) place(); }).observe(head, { childList: true });
  }
  function mountShell() {
    document.body.insertAdjacentHTML("beforeend", `<button class="u-tray-btn" id="u-tray-btn" type="button" aria-haspopup="dialog">Slip tray · 0</button>
      <dialog class="u-dialog u-right" id="u-drawer" aria-labelledby="u-drawer-title"></dialog>
      <dialog class="u-dialog u-right" id="u-tray" aria-labelledby="u-tray-title"><div class="u-d-head"><div><h2 id="u-tray-title">Slip tray</h2><p>One account slip across CS2, LoL and Sports. Your other slips stay where they are.</p></div><button class="u-x" type="button" aria-label="Close slip tray" data-u="tclose">${X_ICON}</button></div><div class="u-tabs" id="u-tray-tabs" role="tablist" aria-label="Slip tray sections"></div><div class="u-d-body" id="u-tray-body" tabindex="0"></div><div class="u-toast" role="status" aria-live="polite"></div></dialog>
      <div class="u-toast" id="u-toast" role="status" aria-live="polite"></div>`);
    $("u-tray-btn").addEventListener("click", () => openTray());
    dockTray();
    document.addEventListener("click", guard((e) => {
      const t = e.target.closest("[data-u]");
      if (t && !t.disabled && t.getAttribute("aria-disabled") !== "true" && ACT[t.dataset.u]) {
        e.preventDefault();
        e.stopPropagation();
        ACT[t.dataset.u](t.dataset.id, t);
        return;
      }
      if (e.target.classList && e.target.classList.contains("u-dialog")) e.target.close();    /* a click on the backdrop */
    }), true);
    document.addEventListener("submit", (e) => { if (e.target && e.target.id === "u-ask") { e.preventDefault(); guard(() => genieAsk())(); } }, true);
    document.addEventListener("input", (e) => { if (e.target && e.target.id === "u-ask-q") G.q = e.target.value; }, true);
    document.addEventListener("toggle", (e) => {
      const k = e.target.dataset ? e.target.dataset.det : null;
      if (k) { if (e.target.open) T.open.add(k); else T.open.delete(k); }
    }, true);
    window.addEventListener("focus", guard(() => { syncState(); if (T.mine) loadMine(); }));
    window.addEventListener("pagehide", () => { if (T.pending) pushState(false, true); });
    setInterval(guard(() => {
      if (document.hidden) return;
      removeStarted();
      if (T.saveFail) { if (T.ready) pushState(); else loadState(); }
      if (T.legs.length && !T.checking && !freshCheck() && T.checkErr !== "down") { T.checkErr = null; scheduleCheck(); }
    }), 5000);
  }
  const ACT = {
    research(id, el) { const rec = recOfEl(el); if (rec) research(rec); else say("Research for this line is not ready yet."); },
    "add-row"(id, el) {
      const rec = recOfEl(el);
      if (!rec) { say("This line is not ready for the slip tray yet."); return; }
      if (onSlip(rec)) { T.legs = T.legs.filter((l) => !(l.key === rec.key && l.book === rec.offer.platform)); slipChanged(); say(`Removed ${rec.player.name} ${rec.offer.line} from your slip tray`); return; }
      if (!rec.scout.side) { research(rec); return; }
      addLeg(rec, rec.scout.side);
    },
    alts(id, el) {
      const on = !ALTS.has(id);
      if (on) ALTS.add(id); else ALTS.delete(id);
      el.setAttribute("aria-expanded", String(on));
      const box = document.getElementById(el.getAttribute("aria-controls"));
      if (box) box.hidden = !on;
    },
    tray() { $("u-drawer").close(); openTray(); },
    tclose() { $("u-tray").close(); },
    pclose() { $("u-drawer").close(); },
    tab(id) { T.tab = id; if (id !== "slip") loadMine(); paintTray(); },
    check() { T.checkErr = null; T.check = null; runCheck(); },
    clear() { T.legs = []; slipChanged(); say(HUB && !$("u-hub").hidden ? "Slip cleared" : "Slip tray cleared"); },
    swap: swapLeg,
    replace: replaceLeg,
    "leg-x": removeLeg,
    etype(v) { const i = v.indexOf(":"); T.types[v.slice(0, i)] = v.slice(i + 1); slipChanged(); },
    track: trackEntry,
    copy: copyEntry,
    'open-line':()=>openLine(P.rec,P.side),
    "genie-ask": (q) => genieAsk(q),
    'open-leg':id=>openBookLeg(T.legs.find(l=>l.id===id)),
    'open-entry':key=>{const e=entries().find(x=>x.key===key);if(e)openGenerated({platform:e.platform,entry_type:typeOf(e),legs:e.legs});},
    pick(id) { T.openPick = T.openPick === id ? null : id; paintTray(); },
    ptab(id) { if (P.only && !P.only.includes(id)) { $("u-drawer").close(); return; } P.tab = id; paintDrawer(); $("u-drawer-body").scrollTop = 0; },
    pside(sd) { P.side = sd; paintDrawer(); },
    pline(k) { const r = R.boards[P.rec.sport].recs.get(k); if (r) research(r, P.tab, P.only, P.side); },
    hist(n) { P.histN = +n; paintDrawer(); },
    padd() {
      const rec = P.rec, on = T.legs.find((l) => l.id === legId(rec, P.side));
      if (on) { removeLeg(on.id); say(`Removed ${on.player} ${cap(on.side)} ${on.line}`); } else addLeg(rec, P.side);
      paintDrawer();
    },
    "lol-results"() { LOL.results = true; lolAfterRender(); },
    "lol-layout"(id) { LOL.setRows(id === "rows"); }
  };

  /* ---- shared row cells ---- */
  const ALTS = new Set();                                         /* the groups a member opened, kept across redraws */
  let recOfEl = () => null;
  const markRows = guard(() => document.querySelectorAll(".u-add").forEach((b) => {
    const rec = recOfEl(b), on = !!(rec && onSlip(rec));
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    b.textContent = on ? "On tray" : "+ Slip tray";
  }));
  function hitCell(n, t, side) {
    if (!t) return `<span class="u-hc none"><i>L${n}</i><b>--</b></span>`;
    const hits = side === "under" ? t.under : t.over, decided = t.n - t.push, rate = decided ? hits / decided : 0;
    return `<span class="u-hc${rate >= 0.6 ? " up" : ""}" title="${esc(`Last ${t.n} at this line: ${hits} for ${cap(side)}, ${t.push} push`)}"><i>L${n}</i><b>${hits}/${t.n}</b></span>`;
  }
  function hitsHtml(rec, side) {
    const t = rec ? tallies(rec) : {}, sd = side || (rec && rec.scout.side) || "over", windows = [5, 10, 20];
    return `<div class="u-hits"><span class="u-sr">Hit rate at this line for ${cap(sd)}</span>${windows.map((n) => hitCell(n, t["l" + n], sd)).join("")}</div>`;
  }
  function booksCell(rec) {
    if (!rec) return '<div class="u-books"><span class="u-bk none">--</span></div>';
    const list = siblings(rec), line = rec.offer.line, seen = new Set();
    const items = list.filter((o) => { const k = o.offer.platform + "|" + o.offer.line + "|" + o.market.line_type; if (seen.has(k)) return false; seen.add(k); return true; })
      .sort((a, b) => (a.offer.line === line ? 0 : 1) - (b.offer.line === line ? 0 : 1) || (slipOffer(b) ? 1 : 0) - (slipOffer(a) ? 1 : 0));
    const one = (o) => {
      const p = PLATFORMS[o.offer.platform] || { short: o.offer.platform_label }, price = [priceText(o, "over"), priceText(o, "under")].filter(Boolean).join(" / ");
      return `<span class="u-bk${o.offer.line === line ? "" : " diff"}" title="${esc(o.offer.platform_label)}: ${esc(o.offer.line)}${o.offer.line === line ? "" : ", a different line"}"><b>${esc(p.short)}</b>${esc(o.offer.line)}${o.market.line_type !== "standard" ? ` <em>${esc(cap(o.market.line_type))}</em>` : ""}${price ? ` <i>${esc(price)}</i>` : ""}</span>`;
    };
    return `<div class="u-books">${items.slice(0, 4).map(one).join("")}${items.length > 4 ? `<span class="u-bk more">+${items.length - 4}</span>` : ""}</div>`;
  }
  const trayBtn = (rec) => `<button class="u-add${rec && onSlip(rec) ? " on" : ""}" type="button" data-u="add-row" aria-pressed="${!!(rec && onSlip(rec))}" title="Your account slip">${rec && onSlip(rec) ? "On tray" : "+ Slip tray"}</button>`;
  const researchBtn = () => `<button class="u-res-btn" type="button" data-u="research" title="History, forecast, books and context">Research</button>`;

  /* ---- CS2: index.html draws its rows, and this adds to them. Every data attribute its own click handler reads stays on the row ---- */
  const CS2 = {
    rec(r) { return R.boards.cs2.recs.get(offerKey(r.book || "underdog", String(r.id))) || null; },
    decorate() {
      const out = $("boardout");
      if (!out) return;
      out.querySelectorAll(".clist").forEach((list) => {
        const head = list.querySelector(":scope > .crow-head");
        if (head && !head.dataset.u) {
          head.dataset.u = "1";
          head.setAttribute("aria-hidden", "false");
          head.innerHTML = "<span>Grade</span><span>Player</span><span>Market</span><span>Line</span><span>Proj</span><span>Pick · chance</span><span>Hit rate</span><span>Books</span><span>Stake</span><span></span>";
        }
        const groups = new Map();
        [...list.querySelectorAll(".brow2.crow[data-bl]")].forEach((el) => {
          const r = rowOfBl(el.dataset.bl);
          if (!r) return;
          const rec = CS2.rec(r), k = [matchKey(r), r.nick || r.player, r.prop, r.scope || "12"].join("|");
          CS2.cells(el, r, rec);
          if (!groups.has(k)) groups.set(k, []);
          groups.get(k).push(el);
        });
        groups.forEach((els, k) => CS2.group(els, k));
      });
    },
    cells(el, r, rec) {
      el.classList.add("u-decorated");
      const side = r.side === "Less" ? "under" : r.side === "More" ? "over" : null;
      let hit = el.querySelector(":scope > .u-hits-c"), books = el.querySelector(":scope > .u-books-c");
      if (!hit) {
        const stake = el.querySelector(":scope > .c-stake");
        stake.insertAdjacentHTML("beforebegin", '<div class="u-hits-c"></div><div class="u-books-c"></div>');
        hit = el.querySelector(":scope > .u-hits-c"); books = el.querySelector(":scope > .u-books-c");
        const act = el.querySelector(":scope > .c-act");
        if (act) act.insertAdjacentHTML("afterbegin", `<span class="u-row-acts"></span>`);
      }
      const meta = el.querySelector(":scope > .c-meta");
      if (meta && /^graded [^·]*$/.test(meta.textContent.trim())) { el.title = meta.textContent.trim(); meta.hidden = true; }
      hit.innerHTML = rec ? hitsHtml(rec, side) : CS2.savedHits(r, side);
      books.innerHTML = booksCell(rec);
      const acts = el.querySelector(":scope > .c-act .u-row-acts");
      if (acts) acts.innerHTML = r.side ? researchBtn() + trayBtn(rec) : "";
    },
    savedHits(r, side) {
      const n = r.l10_n, o = r.l10_over, push = r.l10_push || 0, t = n && o != null ? { over: o, under: n - o, push, n: n + push } : null;
      return `<div class="u-hits">${hitCell(5, null, side || "over")}${hitCell(10, t, side || "over")}${hitCell(20, null, side || "over")}</div>`;
    },
    group(els, key) {
      if (els.length < 2) return;
      const lead = els[0], id = "u-alt-" + hash(key), open = ALTS.has(key);
      let box = document.getElementById(id);
      if (!box) { box = document.createElement("div"); box.id = id; box.className = "u-alts"; lead.after(box); }
      box.hidden = !open;
      els.slice(1).forEach((x) => { x.classList.add("u-sub"); box.appendChild(x); });
      const who = lead.querySelector(":scope > .c-who");
      if (who && !who.querySelector(".u-alt-btn")) who.insertAdjacentHTML("beforeend", altButton(key, id, els.length - 1, open));
    },
    run: guard(() => {
      CS2.decorate();
      Promise.all([loadBoard("cs2"), loadHits()]).then(guard(() => CS2.decorate()));
    })
  };
  function altButton(key, id, n, open) { return `<button class="u-alt-btn" type="button" data-u="alts" data-id="${esc(key)}" aria-expanded="${open}" aria-controls="${id}">+${n} more ${n === 1 ? "line" : "lines"}</button>`; }
  function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }

  /* ---- LoL: lol.html keeps its Cards and Table layouts. Rows is one more layout, chosen on this device only ---- */
  const LOL = {
    results: false,
    rowsOn() { try { return localStorage.getItem("scout.upgrade.lol.rows") !== "0"; } catch (e) { return true; } },
    setRows(on) { try { localStorage.setItem("scout.upgrade.lol.rows", on ? "1" : "0"); } catch (e) { /* this visit only */ } lolAfterRender(); },
    rec(x) { return R.boards.lol.recs.get(offerKey(x.platform, x.key)) || null; }
  };
  const lolGrade = (x) => (x.pick_offered && x.grade ? x.grade : "");
  function lolRow(x, sub, alts) {
    const rec = LOL.rec(x), side = x.pick_side === "Over" ? "over" : x.pick_side === "Under" ? "under" : null;
    const p = side === "under" ? x.p_under : x.p_over, letter = lolGrade(x), lean = /^Lean/.test(letter) || !letter;
    const pick = !x.pick_offered ? `<span class="u-dim">${esc(x.pick_side || "No pick")}${x.pick_side ? " not offered" : ""}</span>` : `${lean ? "Lean " : ""}${esc(x.pick_side)} <b>${pct(p)}</b>`;
    return `<div class="u-row u-lol${sub ? " u-sub" : ""}" data-k="${esc(x.key)}" tabindex="0" role="button" aria-label="${esc(x.player + " " + x.stat_name + " " + x.scope + " " + x.platform_label + " " + x.line)}">
      <span class="u-g${lean ? " lean" : ""}">${esc(lean ? "L" : letter)}</span>
      <div class="u-who"><b>${esc(x.player)}</b><small>${esc(x.team || "")}${x.role ? " · " + esc(x.role) : ""} vs ${esc(x.opponent || "")}</small><small>${esc(new Date(x.start).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }))}</small>${alts || ""}</div>
      <div class="u-mkt">${esc(x.stat_name)}<small>${esc(x.scope)} · ${esc(x.platform_label)}${x.line_type !== "Standard" ? " · " + esc(x.line_type) : ""}</small></div>
      <div class="u-num"><span class="u-k">Line</span><b>${esc(x.line)}</b></div><div class="u-num"><span class="u-k">Proj</span>${x.projection == null ? "--" : Number(x.projection).toFixed(1)}</div>
      <div class="u-pick-c">${pick}</div>${hitsHtml(rec, side)}${booksCell(rec)}<div class="u-stake">${esc(letter || "--")} · ${esc(x.units)}U</div>
      <div class="u-act">${researchBtn()}${trayBtn(rec)}${typeof ctBtnHtml === "function" ? ctBtnHtml(x) : ""}</div></div>`;
  }
  function lolRows() {
    const shown = rows().slice(0, visibleLimit), groups = new Map();
    shown.forEach((x) => { const k = [matchKey(x), x.player, x.stat, x.scope].join("|"); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(x); });
    const head = '<div class="u-row u-head"><span>Grade</span><span>Player</span><span>Market</span><span>Line</span><span>Proj</span><span>Pick · chance</span><span>Hit rate</span><span>Books</span><span>Stake</span><span></span></div>';
    return `<div class="u-rows">${head}${[...groups.entries()].map(([k, g]) => {
      const id = "u-alt-" + hash(k), open = ALTS.has(k), more = g.length - 1;
      return lolRow(g[0], false, more ? altButton(k, id, more, open) : "") + (more ? `<div class="u-alts" id="${id}"${open ? "" : " hidden"}>${g.slice(1).map((x) => lolRow(x, true)).join("")}</div>` : "");
    }).join("")}</div>`;
  }
  const lolAfterRender = guard(() => {
    const app = $("app"), nav = app && app.querySelector(".workspace-nav #v");
    if (!app || !nav || typeof D === "undefined" || !D) return;
    loadBoard("lol");                                             /* the page's next draw picks it up */
    nav.querySelector("[data-u='lol-results']")?.remove();
    nav.insertAdjacentHTML("beforeend", `<button type="button" data-u="lol-results" aria-pressed="${LOL.results}" class="${LOL.results ? "on" : ""}">Results</button>`);
    if (LOL.results) nav.querySelectorAll("button[data-v]").forEach((b) => { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); });
    app.querySelector(".u-lol-results")?.remove();
    app.querySelector(".u-board")?.remove();
    const views = [...app.children].filter((c) => !c.classList.contains("intro") && !c.classList.contains("workspace-nav") && !c.classList.contains("banner"));
    views.forEach((c) => { c.hidden = LOL.results && !c.classList.contains("workspace-nav"); });
    if (LOL.results) {
      app.querySelector(".workspace-nav").insertAdjacentHTML("afterend", '<section class="u-lol-results" aria-label="Results"></section>');
      resultsInto(app.querySelector(".u-lol-results"), "lol", "Results");
      return;
    }
    const table = app.querySelector(".tablebox.board"), cards = app.querySelector(".cards"), seg = $("layout");
    if (!table || !cards || !seg) return;
    const on = LOL.rowsOn();
    if (!seg.querySelector("[data-u='lol-layout']")) seg.insertAdjacentHTML("afterbegin", `<button type="button" data-u="lol-layout" data-id="rows" aria-pressed="${on}" class="${on ? "on" : ""}">Rows</button>`);
    seg.querySelectorAll("button[data-v]").forEach((b) => { if (on) { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); } });
    table.hidden = cards.hidden = on;
    if (on) {
      table.insertAdjacentHTML("beforebegin", `<div class="u-board">${lolRows()}</div>`);
      app.querySelectorAll(".u-board [data-k]").forEach((el) => {
        el.onclick = () => card(el.dataset.k, el);
        el.onkeydown = (e) => { if (e.target === el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); card(el.dataset.k, el); } };
      });
      ctWire();
    }
  });

  /* ---- Sports: the page's own table draws the row and groups a player's stats; this adds books, research and the slip tray ---- */
  const SP = {
    lg() { return (window.ScoutSportsApp && window.ScoutSportsApp.state && window.ScoutSportsApp.state.lg) || "nba"; },
    norm: (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, ""),
    rec(lg, ev, pid, stat, scope, line, book) { return R.boards[lg].keys.get(sportKey(ev, SP.norm(pid), stat, scope, line, book)) || null; },
    fromKey(key, line, book, lg) {
      const k = String(key || "").split("|");
      return SP.rec(lg, k[0], k[1], k[2], k[3] || "full_game", line, book);
    },
    fromEl(el, lg) {
      const box = el.closest("[data-ukey]"), tr = el.closest("tr[data-rid]");
      return box ? SP.fromKey(box.dataset.ukey, box.dataset.uline, box.dataset.ubook, lg) : tr ? SP.fromKey(tr.dataset.key, tr.dataset.line, tr.dataset.book, lg) : null;
    },
    hookCards() {                                                 /* Cards view and the player card: the page's own after-actions hook */
      const H = window.ScoutSportsApp && window.ScoutSportsApp.hooks;
      if (!H || H.__upgraded) return;
      const prev = H.afterActions;
      H.__upgraded = true;
      H.afterActions = function (inner, c) {
        const out = prev ? prev(inner, c) : inner;
        try {
          const m = c.market, q = c.quote;
          if (!m || !q || q.l == null || !q.p) return out;
          const rec = SP.fromKey(m.k, q.l, q.p, SP.lg());
          return out + `<span class="u-row-acts" data-ukey="${esc(m.k)}" data-uline="${esc(q.l)}" data-ubook="${esc(q.p)}">${researchBtn()}${trayBtn(rec)}</span>`;
        } catch (e) { return out; }
      };
    },
    wrap() {
      const T0 = window.SportsTable;
      if (!T0 || T0.__upgraded) return;
      const orig = T0.html;
      T0.__upgraded = true;
      T0.html = function (opts, st) {
        const out = orig.call(this, opts, st);
        try { return SP.decorate(out, opts); } catch (e) { return out; }
      };
    },
    decorate(html, opts) {
      if (!html) return html;
      const tpl = document.createElement("template"), lg = opts.lg, byRid = new Map();
      tpl.innerHTML = html;
      (T0Rows(opts) || []).forEach((r) => byRid.set(r.rid, r));
      tpl.content.querySelectorAll("tr.sw-tb-row").forEach((tr) => {
        const r = byRid.get(tr.dataset.rid);
        if (!r) return;
        tr.dataset.line = r.line; tr.dataset.book = r.book;
        const rec = SP.rec(lg, r.m.e, r.pid, r.stat, String(r.key).split("|")[3] || "full_game", r.line, r.book);
        const line = tr.querySelector("td.sw-tb-line"), stat = tr.querySelector("td.sw-tb-stat");
        if (line && rec && siblings(rec).length > 1) {
          [...line.querySelectorAll(".sw-tb-note")].filter((n) => /^\d+ books$/.test(n.textContent)).forEach((n) => n.remove());
          line.insertAdjacentHTML("beforeend", booksCell(rec));
        }
        if (stat) stat.insertAdjacentHTML("beforeend", `<div class="u-row-acts">${researchBtn()}${trayBtn(rec)}</div>`);
      });
      if (R.me) loadBoard(lg);                                    /* the page's next draw picks it up */
      return tpl.innerHTML;
    },
    results: guard(() => {
      const main = $("sw-main");
      if (!main || !/^#\/results/.test(location.hash) || main.querySelector(".u-res-box")) return;
      const sub = main.querySelector(".sw-sub");
      if (!sub) return;
      sub.insertAdjacentHTML("afterend", '<section class="u-res-box" aria-label="Stated and observed"></section>');
      resultsInto(main.querySelector(".u-res-box"), SP.lg(), null);
    })
  };
  function T0Rows(opts) { return window.SportsTable.build(opts); }

  /* ---- Results: stated against observed, by letter and market, sample sizes, pushes apart ---- */
  function dumb(st, ob) {
    const X = (v) => Math.max(0, Math.min(100, (v - 0.4) / 0.3 * 100)), left = Math.min(X(ob), X(st)), width = Math.abs(X(ob) - X(st));
    return `<span class="u-dumb" aria-hidden="true"><span class="u-d-tick" style="left:${X(0.5)}%"></span><span class="u-d-bar" style="left:${left}%;width:${width}%"></span><span class="u-d-st" style="left:${X(st)}%"></span><span class="u-d-ob" style="left:${X(ob)}%"></span></span>`;
  }
  function resTable(title, label, list) {
    const rows = (list || []).filter((x) => x.picks || x.pushes);
    if (!rows.length) return "";
    return `<h3>${esc(title)}</h3><div class="u-scroll"><table class="u-tbl u-res-tbl"><thead><tr><th scope="col">${esc(label)}</th><th scope="col">Sample</th><th scope="col">Won-lost</th><th scope="col">Pushes</th><th scope="col">Stated</th><th scope="col">Observed</th><th scope="col">Gap</th><th scope="col" class="w">Stated v observed, 40% to 70%</th></tr></thead><tbody>${rows.map((x) => {
      const gap = x.printed != null && x.observed != null ? (x.observed - x.printed) * 100 : null;
      return `<tr><th scope="row">${esc(x.name)}</th><td>${x.picks}</td><td>${x.wins == null ? "--" : `${x.wins}-${x.losses}`}</td><td>${x.pushes == null ? "--" : x.pushes}</td><td>${x.printed == null ? "--" : pct(x.printed)}</td><td>${x.observed == null ? "--" : pct(x.observed)}</td><td>${gap == null ? "--" : `${gap >= 0 ? "+" : ""}${gap.toFixed(1)} pts`}</td><td class="w">${gap == null ? "" : dumb(x.printed, x.observed)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  async function resultsInto(box, sport, heading) {
    if (!box) return;
    box.classList.add("u-res");
    const h = heading ? `<h2>${esc(heading)}</h2>` : "<h2>Stated and observed</h2>";
    box.innerHTML = h + '<div class="u-skel tall"></div>';
    try {
      if (sport === "cs2") {
        const d = await api("stated"), t = d.totals || {};
        if (!t.picks) { box.innerHTML = `${h}<p class="u-dim">No settled picks meet this rule yet.</p>`; return; }
        box.innerHTML = `${h}<p class="u-dim">${esc(d.rule)}. Pushes are apart and left out of the rate. ${t.picks} picks in ${t.matches} matches, ${t.pushes} ${t.pushes === 1 ? "push" : "pushes"} apart.</p>${resTable("By letter", "Letter", d.by_letter)}${resTable("By market", "Market", d.by_market)}`;
        return;
      }
      const d = await api("results?sport=" + sport), g = d.groups || {}, t = d.totals || {};
      if (!t.picks) { box.innerHTML = `${h}<p class="u-dim">${esc(d.population)}</p><p class="u-dim">No settled forecasts meet this rule yet.</p>`; return; }
      const word = t.match_word || "matches";
      box.innerHTML = `${h}<p class="u-dim">${esc(d.population)}</p><p class="u-dim">${esc(d.rule)} ${t.picks} forecasts${t.matches == null ? "" : ` in ${t.matches} ${esc(word)}`}${t.pushes == null ? "" : `, ${t.pushes} ${t.pushes === 1 ? "push" : "pushes"} apart`}.</p>${resTable("By letter", "Letter", sport === "lol" ? g.chance : d.letters)}${sport === "lol" ? "" : resTable("By chance band", "Chance band", g.chance)}${resTable("By market", "Market", g.stat)}${resTable("By role", "Role", g.role)}`;
    } catch (e) { box.innerHTML = `${h}<p class="u-dim">Stated and observed are unavailable right now.</p>`; }
  }

  /* ---- start: one hook per page. A hook that fails leaves that page as it was ---- */
  function wrap(name, after) {
    const orig = window[name];
    if (typeof orig !== "function") return false;
    window[name] = function () { const out = orig.apply(this, arguments); after(); return out; };
    return true;
  }
  const cs2Results = guard(() => {
    const out = $("resultsout");
    if (!R.me || !out || !out.querySelector(".ot-summary") || out.querySelector(".u-res-box")) return;
    out.insertAdjacentHTML("afterbegin", '<section class="u-res-box" aria-label="Stated and observed"></section>');
    resultsInto(out.querySelector(".u-res-box"), "cs2", null);
  });
  function hookCs2() {
    recOfEl = (el) => { const row = el.closest("[data-bl]"), r = row && rowOfBl(row.dataset.bl); return r ? CS2.rec(r) : null; };
    wrap("drawBoard", () => { if (R.me) CS2.run(); });
    wrap("drawResults", cs2Results);
  }
  function startCs2() {
    CS2.run();
    cs2Results();
  }
  function hookLol() {
    recOfEl = (el) => { const row = el.closest("[data-k]"), k = row && row.dataset.k, x = k && (D.rows || []).find((y) => y.key === k); return x ? LOL.rec(x) : null; };
    wrap("render", () => { if (R.me) lolAfterRender(); });
    document.addEventListener("click", guard((e) => {             /* on the way down: the page's own buttons then redraw with this already set */
      if (e.target.closest("#v button[data-v]")) LOL.results = false;
      if (e.target.closest("#layout button[data-v]")) try { localStorage.setItem("scout.upgrade.lol.rows", "0"); } catch (err) { /* this visit only */ }
    }), true);
  }
  function startLol() {
    loadBoard("lol").then(lolAfterRender);
    lolAfterRender();
  }
  function hookSports() {
    recOfEl = (el) => SP.fromEl(el, SP.lg());
    SP.wrap();
  }
  function startSports() {
    SP.hookCards();                                               /* after DOMContentLoaded: compare.js sets this hook again there */
    const main = $("sw-main");
    if (main) new MutationObserver(SP.results).observe(main, { childList: true });
    window.addEventListener("hashchange", () => setTimeout(SP.results, 0));
    const seen = {};
    const load = guard(() => { const lg = SP.lg(); if (!seen[lg]) { seen[lg] = true; loadBoard(lg).then(guard(() => { if (location.hash.startsWith("#/board") && window.ScoutSportsApp) window.ScoutSportsApp.render(); })); } });
    const lgBox = $("sw-league");
    if (lgBox) lgBox.addEventListener("click", () => setTimeout(load, 0));
    load();
    SP.results();
    if (window.ScoutSportsApp && location.hash.startsWith("#/board")) window.ScoutSportsApp.render();
  }
  function startSportsResults() {
    const main = $("rp-main");
    if (!main) return;
    main.insertAdjacentHTML("afterend", '<section class="u-res-box" aria-label="Stated and observed, NBA"></section><section class="u-res-box" aria-label="Stated and observed, NFL"></section>');
    const boxes = document.querySelectorAll(".u-res-box");
    resultsInto(boxes[0], "nba", "NBA stated and observed");
    resultsInto(boxes[1], "nfl", "NFL stated and observed");
  }
  async function start() {
    document.documentElement.classList.add("scout-upgrade");
    if (PAGE === "sports-results") { startSportsResults(); return; }
    mountShell();
    (PAGE === "cs2" ? hookCs2 : PAGE === "lol" ? hookLol : hookSports)();     /* on before the first await: a draw in the meantime is not missed */
    try { R.me = await api("me"); } catch (e) { R.me = null; }
    if (!R.me) { $("u-tray-btn").hidden = true; window.scoutBootDone?.(); return; }        /* not a member the API knows: the page stays as it is */
    try {
      await window.scoutStylesReady;
      const hub = await import("/upgrade/hub.js");
      HUB = hub.mount({ api, R, T, SPORTS, PLATFORMS, esc, pct, when, loadBoard, loadHits, hitsPending, tallies, siblings, started, priceText, lineLabel, offerKey,
        research, researchDoc, chartSvg, addLeg, generateBatch, refreshAdvice: runAdvice, useGenerated, openGenerated, openLine, cleanLeg, onSlip, slipTab, openTray, loadMine, picksTab, recordTab, receiptHtml, resultsInto, say, chanceOf, verdictText, shareSlip });
    } catch (e) { console.warn("Research hub unavailable; original tools remain available"); }
    window.scoutBootDone?.();
    await loadState();
    paintTray();
    if (PAGE === "cs2") startCs2();
    else if (PAGE === "lol") startLol();
    else if (PAGE === "sports") startSports();
  }
  start().catch(() => { window.scoutBootDone?.(); console.warn("Workspace unavailable; original tools remain available"); });
})();
