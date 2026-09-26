/* Agent Arena frontend — plain JS, no frameworks, no CDN, works offline.
 *
 * Branding (project name / token ticker) is loaded from GET /api/settings
 * at boot and injected everywhere. Nothing brandable is hardcoded.
 *
 * ------------------------------------------------------------------
 * BETS API CONTRACT (for the Phase 5 betting engine).
 * The UI below calls exactly this contract via the BetsAPI adapter.
 * Until Phase 5 ships these endpoints, every call 404s and the adapter
 * falls back to a clearly-labeled local DEMO mode.
 *
 *   POST /api/battles/{battle_id}/bets
 *     Request body: { fighter_id: string,   // ENGINE id, e.g. "hawk-2" or "hawk-2#1"
 *                     wallet: string,
 *                     amount_sol: number }  // > 0
 *     201 response: { id, battle_id, fighter_id, wallet, amount_sol,
 *                     created_at, payment_status }
 *     400: unknown fighter / invalid amount
 *     403: exhibition battle (betting refused)
 *     404: unknown battle
 *
 *   GET /api/battles/{battle_id}/bets[?wallet=...]
 *     200 response: { bets: [ { id, battle_id, fighter_id, wallet,
 *                              amount_sol, created_at, payment_status }, ... ] }
 *
 *   GET /api/battles/{battle_id}/pool
 *     200 response: { battle_id,
 *                     pools: { "<engine_fighter_id>": total_sol, ... },
 *                     total_sol: number,
 *                     bet_count: number,
 *                     house_cut_pct: number }
 * ------------------------------------------------------------------
 */
"use strict";

/* ============================== helpers ============================== */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

const fmtSol = (n) => String(Math.round(Number(n) * 10000) / 10000);

const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString();
};

class ApiError extends Error {
  constructor(status, body) {
    super("API " + status + ": " + body);
    this.status = status;
    this.body = body;
  }
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign(
    { headers: { "Content-Type": "application/json" } }, opts || {}));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) {
    throw new ApiError(res.status,
      data && typeof data === "object" ? JSON.stringify(data) : String(data));
  }
  return data;
}

/* ============================== state ============================== */

const S = {
  settings: null,
  fighters: [],
  byRegistry: {},   // registry id -> fighter record
};

/* Engine ids get "#n" suffixes for duplicate fighters in one battle;
 * the registry id (for names/colors) is the part before "#". */
const regIdOf = (engineId) => String(engineId).split("#")[0];
const fighterName = (regId) => {
  const f = S.byRegistry[regId];
  return f ? f.name : regId;
};

/* Deterministic per-fighter color (FNV-1a hash -> hue). */
function fighterColor(regId) {
  let h = 2166136261 >>> 0;
  const s = String(regId);
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return "hsl(" + (h % 360) + ", 75%, 55%)";
}

/* ============================== branding ============================== */

async function loadSettings() {
  const s = await api("/api/settings");
  S.settings = s;
  document.title = s.project_name + " — AI Combat Arena";
  $$('[data-brand="project_name"]').forEach((el) => { el.textContent = s.project_name; });
  $$('[data-brand="token_ticker"]').forEach((el) => { el.textContent = "$" + s.token_ticker; });
  const lead = document.querySelector('[data-brand-lead="fighters"]');
  if (lead) {
    lead.textContent = "The house roster of " + s.project_name +
      ". Hire one before a battle, or just watch them tear each other apart.";
  }
}

/* ============================== router ============================== */

function currentRoute() {
  const parts = (location.hash || "#/arena").replace(/^#\/?/, "").split("/");
  return { view: parts[0] || "arena", param: parts[1] || null };
}

function route() {
  const r = currentRoute();
  const valid = ["arena", "fighters", "leaderboard", "battles", "treasury"];
  const v = valid.indexOf(r.view) >= 0 ? r.view : "arena";
  $$(".view").forEach((el) => { el.hidden = true; });
  $("#view-" + v).hidden = false;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === v));
  if (v === "arena") ArenaView.enter(r.param);
  else ArenaView.leave();
  if (v === "fighters") FightersView.render();
  if (v === "leaderboard") LeaderboardView.render();
  if (v === "battles") BattlesView.render(0);
  if (v === "treasury") TreasuryView.render(0);
}

function goToBattle(id) {
  const target = "#/arena/" + id;
  if (location.hash === target) ArenaView.connect(id);
  else location.hash = target;
}

/* ============================== fighters view ============================== */

function drawAvatar(canvas, regId, size) {
  size = size || 84;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + "px";
  canvas.style.height = size + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const g = ctx.createRadialGradient(size / 2, size / 2, 4, size / 2, size / 2, size / 2);
  g.addColorStop(0, "#1a2133");
  g.addColorStop(1, "#0b0e14");
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2); ctx.fill();
  const color = fighterColor(regId);
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.beginPath(); ctx.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(size / 2, size / 2, size * 0.26, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#0b0e14";
  ctx.font = "800 " + Math.round(size * 0.3) + "px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(fighterName(regId).charAt(0).toUpperCase(), size / 2, size / 2 + 1);
}

const winRate = (f) => {
  const total = f.wins + f.losses + f.draws;
  return total > 0 ? (f.wins / total) * 100 : null;
};

const FightersView = {
  render() {
    const grid = $("#fighter-grid");
    if (!S.fighters.length) {
      grid.innerHTML = '<p class="muted">Could not load fighters.</p>';
      return;
    }
    grid.innerHTML = "";
    S.fighters.forEach((f) => {
      const wr = winRate(f);
      const card = document.createElement("div");
      card.className = "fighter-card";
      card.innerHTML =
        '<div class="avatar-slot"></div>' +
        "<h2>" + esc(f.name) + "</h2>" +
        '<p class="tagline">' + esc(f.tagline) + "</p>" +
        '<p class="desc">' + esc(f.description) + "</p>" +
        '<div class="fighter-record"><span>W <b>' + f.wins + "</b></span>" +
        "<span>L <b>" + f.losses + "</b></span>" +
        "<span>D <b>" + f.draws + "</b></span>" +
        "<span>Win rate <b>" + (wr === null ? "&mdash;" : wr.toFixed(1) + "%") + "</b></span></div>" +
        '<div class="winrate-bar"><div style="width:' + (wr === null ? 0 : wr) + '%"></div></div>';
      const cv = document.createElement("canvas");
      card.querySelector(".avatar-slot").appendChild(cv);
      drawAvatar(cv, f.id);
      grid.appendChild(card);
    });
  },
};

/* ============================== leaderboard view ============================== */

const LeaderboardView = {
  render() {
    const tb = $("#leaderboard-table tbody");
    const rows = S.fighters.slice().sort((a, b) => {
      if (b.wins !== a.wins) return b.wins - a.wins;
      const wa = winRate(a), wb = winRate(b);
      return (wb === null ? -1 : wb) - (wa === null ? -1 : wa);
    });
    tb.innerHTML = rows.map((f, i) => {
      const wr = winRate(f);
      const total = f.wins + f.losses + f.draws;
      return "<tr>" +
        '<td class="' + (i === 0 ? "rank-1" : "") + '">' + (i + 1) + "</td>" +
        "<td><b>" + esc(f.name) + "</b></td>" +
        "<td>" + f.wins + "</td><td>" + f.losses + "</td><td>" + f.draws + "</td>" +
        "<td>" + (wr === null ? "&mdash;" : wr.toFixed(1) + "%") + "</td>" +
        "<td>" + total + "</td></tr>";
    }).join("");
  },
};

/* ============================== battles view ============================== */

const BattlesView = {
  offset: 0,
  limit: 25,
  hasMore: false,

  async render(offset) {
    this.offset = offset == null ? 0 : offset;
    const box = $("#battle-history");
    const btn = $("#btn-more-battles");
    if (this.offset === 0) box.innerHTML = '<p class="muted">Loading battles…</p>';
    let data;
    try {
      data = await api("/api/battles?limit=" + this.limit + "&offset=" + this.offset);
    } catch (e) {
      box.innerHTML = '<p class="muted">Could not load battles.</p>';
      return;
    }
    this.hasMore = data.battles.length === this.limit;
    btn.hidden = !this.hasMore;
    if (this.offset === 0) box.innerHTML = "";
    if (!data.battles.length && this.offset === 0) {
      box.innerHTML = '<p class="muted">No battles yet. Start one from the Arena.</p>';
      return;
    }
    data.battles.forEach((b) => box.appendChild(this.item(b)));
  },

  item(b) {
    const el = document.createElement("div");
    el.className = "history-item";
    const names = b.fighter_ids.map((id) => esc(fighterName(regIdOf(id)))).join(" vs ");
    const winner = b.winner
      ? '<span style="color:var(--good);font-weight:700">' + esc(fighterName(regIdOf(b.winner))) + "</span>"
      : '<span class="muted">draw</span>';
    el.innerHTML =
      '<div class="meta">' +
        '<div class="t1">' + names + "</div>" +
        '<div class="t2">' + fmtTime(b.created_at) + " · " + b.ticks + " ticks · " +
          esc(b.reason || b.status) + "</div>" +
      "</div>" +
      '<span class="badge ' + (b.status === "running" ? "live" : "done") + '">' +
        esc(b.status) + "</span>" +
      (b.exhibition ? '<span class="badge exh">exhibition</span>' : "") +
      "<span>Winner: " + winner + "</span>" +
      '<button class="btn btn-small btn-ghost">Watch</button>';
    el.querySelector("button").addEventListener("click", () => goToBattle(b.id));
    return el;
  },
};

/* ============================== treasury view ============================== */

const TreasuryView = {
  offset: 0,
  limit: 25,
  hasMore: false,

  async render(offset) {
    this.offset = offset == null ? 0 : offset;
    const s = S.settings || {};
    const ticker = esc(s.token_ticker || "TKN");
    const name = esc(s.project_name || "");
    $("#treasury-lead").textContent =
      "Fees collected by " + (s.project_name || "the arena") +
      " accumulate here. A share of every round's fees is used to buy back " +
      "and burn $" + (s.token_ticker || "TKN") + " — every burn is listed below.";
    const box = $("#treasury-stats");
    if (this.offset === 0) {
      box.innerHTML = '<p class="muted">Loading treasury…</p>';
      let stats;
      try {
        stats = await api("/api/treasury/stats");
      } catch (e) {
        box.innerHTML = '<p class="muted">Could not load treasury stats.</p>';
        return;
      }
      const card = (value, label) =>
        '<div class="stat-card"><div class="stat-value">' + value +
        '</div><div class="stat-label">' + label + "</div></div>";
      box.innerHTML =
        card(esc(fmtSol(stats.treasury_balance_sol)) + " SOL", "Treasury balance") +
        card(esc(fmtSol(stats.total_fees_sol)) + " SOL", "Total fees collected") +
        card(esc(String(stats.total_burned_tokens)) + " $" + ticker, "Total burned") +
        card(esc(String(stats.burn_count)), "Buyback rounds");
    }
    const tb = $("#treasury-burns-table tbody");
    const btn = $("#btn-more-burns");
    const none = $("#treasury-no-burns");
    if (this.offset === 0) tb.innerHTML = "";
    let data;
    try {
      data = await api("/api/treasury/burns?limit=" + this.limit +
                       "&offset=" + this.offset);
    } catch (e) {
      if (this.offset === 0)
        tb.innerHTML = '<tr><td colspan="5" class="muted">Could not load burn history.</td></tr>';
      return;
    }
    this.hasMore = data.burns.length === this.limit;
    btn.hidden = !this.hasMore;
    if (!data.burns.length && this.offset === 0) {
      none.hidden = false;
      return;
    }
    none.hidden = true;
    tb.innerHTML += data.burns.map((b) =>
      "<tr>" +
      "<td>" + esc(fmtTime(b.created_at)) + "</td>" +
      "<td>" + esc(fmtSol(b.sol_spent)) + " SOL</td>" +
      "<td>" + esc(String(b.tokens_burned)) + " $" + ticker + "</td>" +
      '<td><span class="tx-link">' + esc(String(b.buy_tx || "—")).slice(0, 18) +
        (b.buy_tx && b.buy_tx.length > 18 ? "…" : "") + "</span></td>" +
      '<td><span class="tx-link">' + esc(String(b.burn_tx || "—")).slice(0, 18) +
        (b.burn_tx && b.burn_tx.length > 18 ? "…" : "") + "</span></td>" +
      "</tr>"
    ).join("");
  },
};

/* ============================== arena viewer ============================== */

const ArenaView = {
  LOGICAL: 760,
  battleId: null,
  meta: null,
  ws: null,
  status: null,          // "live" | "replay"
  prev: null, cur: null, // snapshot dicts
  prevT: 0, curT: 0,
  particles: [],
  dashPrev: {},
  arenaSize: 1000,
  hpMax: 100,
  done: false,
  result: null,
  canvas: null, ctx: null, dpr: 1,
  raf: 0, lastT: 0,

  initCanvas() {
    if (this.ctx) return;
    const c = $("#arena-canvas");
    this.canvas = c;
    this.ctx = c.getContext("2d");
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = this.LOGICAL * this.dpr;
    c.height = this.LOGICAL * this.dpr;
    const step = (now) => {
      this.raf = requestAnimationFrame(step);
      this.draw(now);
    };
    this.raf = requestAnimationFrame(step);
  },

  enter(battleId) {
    this.initCanvas();
    this.refreshPicker();
    if (battleId && battleId !== this.battleId) this.connect(battleId);
    else if (!battleId && !this.battleId) this.showEmpty(true);
    else this.syncSidePanels();
  },

  leave() { this.disconnect(); },

  disconnect() {
    if (this.ws) {
      try { this.ws.close(); } catch (e) { /* already closed */ }
      this.ws = null;
    }
  },

  resetStream() {
    this.prev = null; this.cur = null;
    this.prevT = 0; this.curT = 0;
    this.particles = [];
    this.dashPrev = {};
    this.arenaSize = 1000;
    this.hpMax = 100;
    this.done = false;
    this.result = null;
    this.status = null;
    $("#arena-result").hidden = true;
    $("#arena-tick").textContent = "";
  },

  showEmpty(on) { $("#arena-empty").hidden = !on; },

  setNote(html) { $("#arena-notes").innerHTML = html || ""; },

  async connect(battleId) {
    this.disconnect();
    this.battleId = battleId;
    const seq = (this._seq = (this._seq || 0) + 1); // stale-connect guard
    this.resetStream();
    this.showEmpty(false);
    this.setNote("Connecting…");
    let meta;
    try {
      meta = await api("/api/battles/" + encodeURIComponent(battleId) + "?snapshots=false");
    } catch (e) {
      if (seq !== this._seq) return;
      this.setNote("Battle not found.");
      return;
    }
    if (seq !== this._seq) return; // user moved on while we were fetching
    this.meta = meta;
    $("#arena-battle-label").textContent =
      meta.fighter_ids.map((id) => fighterName(regIdOf(id))).join(" vs ") +
      (meta.exhibition ? " (exhibition)" : "");
    this.syncSidePanels();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(proto + "://" + location.host +
      "/ws/battles/" + encodeURIComponent(battleId));
    this.ws = ws;
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return; // stale socket from a previous battle
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      this.onMessage(msg);
    };
    ws.onclose = () => {
      if (this.ws === ws && !this.done) {
        this.setNote('Connection lost. <button class="btn btn-small btn-ghost" id="btn-retry-ws">Retry</button>');
        const b = $("#btn-retry-ws");
        if (b) b.addEventListener("click", () => this.connect(battleId));
      }
      if (this.ws === ws) this.ws = null;
    };
    ws.onerror = () => { /* onclose handles the UI */ };
  },

  onMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "info") this.onInfo(msg);
    else if (msg.type === "snapshot") this.onSnapshot(msg);
    else if (msg.type === "done") this.onDone(msg);
  },

  onInfo(msg) {
    this.status = msg.status; // "live" | "replay"
    const live = msg.status === "live";
    $("#arena-live-badge").hidden = !live;
    $("#arena-mode-badge").textContent = live ? "STREAMING" : "REPLAY";
    this.setNote(live
      ? "Streaming live — ticks arrive as the engine runs them."
      : "Replay of a finished battle, streamed from stored snapshots.");
  },

  onSnapshot(snap) {
    if (!snap || !Array.isArray(snap.fighters)) return;
    const now = performance.now();
    if (this.cur) {
      const prevById = {};
      this.cur.fighters.forEach((f) => { prevById[f.id] = f; });
      snap.fighters.forEach((f) => {
        const p = prevById[f.id];
        if (p && p.alive && !f.alive) this.explode(f);
        const pd = this.dashPrev[f.id] || 0;
        if (f.dash_cooldown > pd) this.dashStreak(f);
        this.dashPrev[f.id] = f.dash_cooldown;
      });
    } else {
      snap.fighters.forEach((f) => { this.dashPrev[f.id] = f.dash_cooldown; });
    }
    this.prev = this.cur;
    this.prevT = this.curT;
    this.cur = snap;
    this.curT = now;
    snap.fighters.forEach((f) => {
      if (typeof f.x === "number") this.arenaSize = Math.max(this.arenaSize, Math.abs(f.x));
      if (typeof f.y === "number") this.arenaSize = Math.max(this.arenaSize, Math.abs(f.y));
      if (typeof f.hp === "number") this.hpMax = Math.max(this.hpMax, f.hp);
    });
    if (Array.isArray(snap.projectiles)) {
      snap.projectiles.forEach((p) => {
        if (typeof p.x === "number") this.arenaSize = Math.max(this.arenaSize, Math.abs(p.x));
        if (typeof p.y === "number") this.arenaSize = Math.max(this.arenaSize, Math.abs(p.y));
      });
    }
    $("#arena-tick").textContent = "tick " + snap.tick;
  },

  onDone(msg) {
    this.done = true;
    this.result = msg.result || {};
    this.setNote("");
    this.refreshPicker();
    this.syncSidePanels();
    this.showResult();
  },

  explode(f) {
    const color = fighterColor(regIdOf(f.id));
    for (let i = 0; i < 26; i++) {
      const ang = Math.random() * Math.PI * 2;
      const sp = 60 + Math.random() * 220;
      this.particles.push({
        x: f.x, y: f.y,
        vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
        born: performance.now(), life: 450 + Math.random() * 450,
        color: color, size: 2 + Math.random() * 3.5,
      });
    }
  },

  dashStreak(f) {
    for (let i = 0; i < 8; i++) {
      this.particles.push({
        x: f.x + (Math.random() - 0.5) * 16,
        y: f.y + (Math.random() - 0.5) * 16,
        vx: (Math.random() - 0.5) * 40, vy: (Math.random() - 0.5) * 40,
        born: performance.now(), life: 220 + Math.random() * 120,
        color: "#9be7ff", size: 1.5 + Math.random() * 2,
      });
    }
  },

  /* ------------------------------ drawing ------------------------------ */

  draw(now) {
    const ctx = this.ctx;
    if (!ctx) return;
    const L = this.LOGICAL;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = "#05070b";
    ctx.fillRect(0, 0, L, L);

    const k = L / this.arenaSize;
    const X = (x) => x * k;
    const Y = (y) => y * k;
    this.drawGrid(ctx, X, Y, k);

    if (!this.cur) return;

    let a = 1;
    if (this.prev && this.curT > this.prevT) {
      a = Math.min(1, Math.max(0, (now - this.curT) / Math.max(1, this.curT - this.prevT)));
    }
    const lerp = (p, c) => p + (c - p) * a;

    const prevP = {};
    if (this.prev && Array.isArray(this.prev.projectiles)) {
      this.prev.projectiles.forEach((p) => { prevP[p.id] = p; });
    }
    (this.cur.projectiles || []).forEach((p) => {
      const q = prevP[p.id];
      this.drawProjectile(ctx, X, Y, q ? lerp(q.x, p.x) : p.x, q ? lerp(q.y, p.y) : p.y, p);
    });

    const prevF = {};
    if (this.prev) this.prev.fighters.forEach((f) => { prevF[f.id] = f; });
    this.cur.fighters.forEach((f) => {
      const q = prevF[f.id];
      this.drawFighter(ctx, X, Y, k, q ? lerp(q.x, f.x) : f.x, q ? lerp(q.y, f.y) : f.y, f);
    });

    this.updateParticles(ctx, X, Y, now);
  },

  drawGrid(ctx, X, Y, k) {
    const L = this.LOGICAL;
    ctx.strokeStyle = "rgba(76, 201, 240, 0.35)";
    ctx.lineWidth = 2;
    ctx.strokeRect(X(0), Y(0), this.arenaSize * k, this.arenaSize * k);
    ctx.strokeStyle = "rgba(120, 140, 180, 0.08)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let g = 100; g < this.arenaSize; g += 100) {
      ctx.moveTo(X(g), Y(0)); ctx.lineTo(X(g), Y(this.arenaSize));
      ctx.moveTo(X(0), Y(g)); ctx.lineTo(X(this.arenaSize), Y(g));
    }
    ctx.stroke();
  },

  drawProjectile(ctx, X, Y, x, y, p) {
    if (typeof x !== "number" || typeof y !== "number") return;
    const color = fighterColor(regIdOf(p.owner));
    const tx = x - (p.vx || 0) * 0.03;
    const ty = y - (p.vy || 0) * 0.03;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineCap = "round";
    ctx.lineWidth = 3;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(X(tx), Y(ty));
    ctx.lineTo(X(x), Y(y));
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(X(x), Y(y), 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  },

  drawFighter(ctx, X, Y, k, x, y, f) {
    if (!f.alive) return; // eliminated: the explosion already told the story
    const reg = regIdOf(f.id);
    const color = fighterColor(reg);
    const r = Math.max(6, 12 * k);
    const cx = X(x), cy = Y(y);

    ctx.save();
    if (f.benched) ctx.globalAlpha = 0.45;
    ctx.shadowColor = color;
    ctx.shadowBlur = 14;
    ctx.fillStyle = f.benched ? "#6b7280" : color;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // heading nose
    ctx.strokeStyle = "rgba(5,7,11,0.85)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(f.heading || 0) * (r + 4), cy + Math.sin(f.heading || 0) * (r + 4));
    ctx.stroke();

    // shield ring + energy arc
    if (f.shield_active) {
      ctx.strokeStyle = "#4cc9f0";
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 6, 0, Math.PI * 2);
      ctx.stroke();
      const frac = Math.max(0, Math.min(1, (f.shield_energy || 0) / 100));
      ctx.strokeStyle = "#9be7ff";
      ctx.lineWidth = 3.5;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 10, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();

    // name label
    ctx.fillStyle = f.benched ? "#9aa3b5" : "#e8ecf4";
    ctx.font = "600 12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(fighterName(reg) + (f.benched ? " (benched)" : ""), cx, cy - r - 16);

    // HP bar
    const w = 46, bx = cx - w / 2, by = cy - r - 13;
    const frac = Math.max(0, Math.min(1, f.hp / this.hpMax));
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(bx - 1, by - 1, w + 2, 7);
    ctx.fillStyle = frac > 0.5 ? "#3ddc84" : frac > 0.25 ? "#ffb020" : "#ff5470";
    ctx.fillRect(bx, by, w * frac, 5);

    // kills badge
    if (f.kills > 0) {
      ctx.fillStyle = "#8b94a9";
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.textBaseline = "top";
      ctx.fillText("K" + f.kills, cx, cy + r + 4);
    }
  },

  updateParticles(ctx, X, Y, now) {
    const dt = Math.min(0.05, (now - (this.lastT || now)) / 1000);
    this.lastT = now;
    this.particles = this.particles.filter((p) => now - p.born < p.life);
    this.particles.forEach((p) => {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vx *= 0.96; p.vy *= 0.96;
      const t = 1 - (now - p.born) / p.life;
      ctx.save();
      ctx.globalAlpha = Math.max(0, t);
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(X(p.x), Y(p.y), p.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    });
  },

  showResult() {
    const r = this.result;
    const ov = $("#arena-result");
    const elim = (r.elimination_order || []).map(
      (id) => "<li>" + esc(fighterName(regIdOf(id))) + "</li>").join("");
    const headline = r.draw
      ? '<div class="result-draw">DRAW</div>'
      : r.winner
        ? '<div class="result-winner">' + esc(fighterName(regIdOf(r.winner))) + "</div>"
        : '<div class="result-draw">NO WINNER RECORDED</div>';
    ov.innerHTML =
      "<h2>Battle over</h2>" + headline +
      '<div class="muted">' + esc(r.reason || "") + " · " + r.ticks + " ticks</div>" +
      (elim ? "<ol>" + elim + "</ol>" : "") +
      '<div class="btn-row">' +
        '<button class="btn btn-small btn-ghost" id="btn-rewatch">Watch replay</button>' +
        '<button class="btn btn-small" id="btn-new-from-result">New battle</button>' +
      "</div>";
    ov.hidden = false;
    $("#btn-rewatch").addEventListener("click", () => this.connect(this.battleId));
    $("#btn-new-from-result").addEventListener("click", () => openNewBattleModal());
  },

  /* ------------------------------ side panels ------------------------------ */

  async refreshPicker() {
    let data;
    try { data = await api("/api/battles?limit=30"); }
    catch (e) { return; }
    const live = data.battles.filter((b) => b.status === "running");
    const recent = data.battles.filter((b) => b.status === "finished").slice(0, 10);
    const mk = (b) => {
      const el = document.createElement("button");
      el.className = "picker-item" + (b.id === this.battleId ? " selected" : "");
      const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(" vs ");
      el.innerHTML =
        '<div class="row1"><span>' + esc(names) + "</span>" +
        '<span class="badge ' + (b.status === "running" ? "live" : "done") + '">' +
          esc(b.status) + "</span></div>" +
        '<div class="row2">' + (b.status === "running" ? "in progress" : b.ticks + " ticks") +
          (b.winner ? " · winner " + esc(fighterName(regIdOf(b.winner))) : "") +
          (b.exhibition ? " · exhibition" : "") + "</div>";
      el.addEventListener("click", () => goToBattle(b.id));
      return el;
    };
    const liveList = $("#picker-live-list");
    const recentList = $("#picker-recent-list");
    liveList.innerHTML = "";
    recentList.innerHTML = "";
    live.forEach((b) => liveList.appendChild(mk(b)));
    recent.forEach((b) => recentList.appendChild(mk(b)));
    if (!live.length) liveList.innerHTML = '<p class="muted small">Nothing live right now.</p>';
    if (!recent.length) recentList.innerHTML = '<p class="muted small">No finished battles yet.</p>';
  },

  syncSidePanels() {
    this.refreshHire();
    renderBets();
  },

  refreshHire() {
    const sel = $("#hire-fighter");
    const btn = $("#btn-hire");
    const feeLine = $("#hire-fee-line");
    feeLine.innerHTML = "Hire fee: <b>" + esc(fmtSol(S.settings.hire_fee_sol)) +
      " SOL</b> per fighter, per battle.";
    sel.innerHTML = "";
    const meta = this.meta;
    if (!meta) {
      btn.disabled = true;
      return;
    }
    meta.fighter_ids.forEach((eid) => {
      const opt = document.createElement("option");
      opt.value = eid;
      opt.textContent = fighterName(regIdOf(eid)) + (eid.indexOf("#") >= 0 ? " (" + eid + ")" : "");
      sel.appendChild(opt);
    });
    const running = meta.status === "running" && !this.done;
    btn.disabled = !running;
    btn.title = running ? "" : "Hiring is only open while a battle is live";
  },
};

/* ============================== hire flow ============================== */

async function submitHire() {
  const meta = ArenaView.meta;
  const out = $("#hire-result");
  const wallet = $("#hire-wallet").value.trim();
  if (!meta) return;
  if (!wallet) {
    out.hidden = false;
    out.innerHTML = "Enter a wallet address first.";
    return;
  }
  const btn = $("#btn-hire");
  btn.disabled = true;
  try {
    const res = await api("/api/battles/" + encodeURIComponent(meta.id) + "/hire", {
      method: "POST",
      body: JSON.stringify({ fighter_id: $("#hire-fighter").value, wallet: wallet }),
    });
    out.hidden = false;
    out.innerHTML =
      "Hired <b>" + esc(fighterName(regIdOf(res.fighter_id))) + "</b> for <b>" +
      esc(fmtSol(res.fee_sol)) + " SOL</b>.<br>" +
      '<span class="muted small">Simulation only — no real payment. ' +
      'Backend recorded <code>payment_status="' + esc(res.payment_status) + '"</code>.</span>';
  } catch (e) {
    out.hidden = false;
    out.innerHTML = e.status === 409
      ? "This wallet already hired a fighter in this battle."
      : "Hire failed: " + esc(e.body || e.message);
  } finally {
    btn.disabled = !(meta.status === "running" && !ArenaView.done);
  }
}

/* ============================== betting (BetsAPI adapter) ============================== */

const BetsAPI = {
  mode: null, // "real" | "demo"

  async ensure(battleId) {
    if (this.mode) return this.mode;
    try {
      await api("/api/battles/" + encodeURIComponent(battleId) + "/pool");
      this.mode = "real";
    } catch (e) {
      this.mode = "demo"; // Phase 5 endpoints not deployed yet
    }
    return this.mode;
  },

  async getPool(battleId, fighterIds) {
    if (this.mode === "real") {
      return api("/api/battles/" + encodeURIComponent(battleId) + "/pool");
    }
    return DemoPool.get(battleId, fighterIds);
  },

  async placeBet(battleId, bet) {
    if (this.mode === "real") {
      return api("/api/battles/" + encodeURIComponent(battleId) + "/bets", {
        method: "POST",
        body: JSON.stringify(bet),
      });
    }
    return DemoPool.place(battleId, bet);
  },

  async myBets(battleId, wallet) {
    if (this.mode === "real") {
      const q = wallet ? "?wallet=" + encodeURIComponent(wallet) : "";
      const d = await api("/api/battles/" + encodeURIComponent(battleId) + "/bets" + q);
      return d.bets || [];
    }
    return DemoPool.myBets(battleId, wallet);
  },
};

/* Local simulated pool used until the Phase 5 betting engine exists. */
const DemoPool = {
  cache: {},

  hash(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  },

  rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  },

  key(battleId) { return "arena_demo_bets_" + battleId; },

  loadMine(battleId) {
    try { return JSON.parse(localStorage.getItem(this.key(battleId)) || "[]"); }
    catch (e) { return []; }
  },

  saveMine(battleId, bets) {
    try { localStorage.setItem(this.key(battleId), JSON.stringify(bets)); }
    catch (e) { /* storage unavailable: demo bets stay in-memory */ }
  },

  get(battleId, fighterIds) {
    if (!this.cache[battleId]) this.seed(battleId, fighterIds || []);
    return this.cache[battleId];
  },

  seed(battleId, fighterIds) {
    const rnd = this.rng(this.hash(String(battleId)));
    const pools = {};
    fighterIds.forEach((f) => { pools[f] = 0; });
    const bets = [];
    const n = 8 + Math.floor(rnd() * 9);
    for (let i = 0; i < n; i++) {
      const f = fighterIds[Math.floor(rnd() * fighterIds.length)];
      if (!f) continue;
      const amt = Math.round((0.05 + rnd() * 1.45) * 100) / 100;
      pools[f] = Math.round((pools[f] + amt) * 100) / 100;
      bets.push({
        id: "demo-seed-" + i, battle_id: battleId, fighter_id: f,
        wallet: "Demo" + (1000 + Math.floor(rnd() * 9000)),
        amount_sol: amt, created_at: new Date().toISOString(),
        payment_status: "demo",
      });
    }
    this.loadMine(battleId).forEach((b) => {
      pools[b.fighter_id] = Math.round(((pools[b.fighter_id] || 0) + b.amount_sol) * 100) / 100;
      bets.push(b);
    });
    this.cache[battleId] = this.summarize(battleId, pools, bets);
  },

  summarize(battleId, pools, bets) {
    let total = 0;
    Object.keys(pools).forEach((f) => { total += pools[f]; });
    return {
      battle_id: battleId,
      pools: pools,
      total_sol: Math.round(total * 100) / 100,
      bet_count: bets.length,
      house_cut_pct: S.settings ? S.settings.betting_house_cut_pct : 5,
      _bets: bets,
    };
  },

  place(battleId, bet) {
    const st = this.get(battleId, [bet.fighter_id]);
    const rec = {
      id: "demo-user-" + Date.now(),
      battle_id: battleId,
      fighter_id: bet.fighter_id,
      wallet: bet.wallet,
      amount_sol: bet.amount_sol,
      created_at: new Date().toISOString(),
      payment_status: "demo",
    };
    st.pools[bet.fighter_id] = Math.round(((st.pools[bet.fighter_id] || 0) + bet.amount_sol) * 100) / 100;
    st._bets.push(rec);
    const mine = this.loadMine(battleId);
    mine.push(rec);
    this.saveMine(battleId, mine);
    const fresh = this.summarize(battleId, st.pools, st._bets);
    this.cache[battleId] = fresh;
    return rec;
  },

  myBets(battleId, wallet) {
    const st = this.get(battleId);
    return st._bets.filter((b) => !wallet || b.wallet === wallet);
  },
};

const BetUI = {
  selected: null,

  async render() {
    const body = $("#bets-body");
    const badge = $("#bets-mode-badge");
    const meta = ArenaView.meta;
    if (!meta) {
      badge.hidden = true;
      body.innerHTML = '<p class="muted small">Select a battle to see betting.</p>';
      return;
    }
    if (meta.exhibition) {
      badge.hidden = true;
      body.innerHTML = '<div class="exhibition-note">Exhibition &mdash; no betting on this battle.</div>';
      return;
    }
    const mode = await BetsAPI.ensure(meta.id);
    badge.hidden = mode !== "demo";
    let pool;
    try {
      pool = await BetsAPI.getPool(meta.id, meta.fighter_ids);
    } catch (e) {
      body.innerHTML = '<p class="muted small">Could not load the pool.</p>';
      return;
    }
    const total = pool.total_sol || 0;
    const n = meta.fighter_ids.length;
    this.selected = this.selected && meta.fighter_ids.indexOf(this.selected) >= 0
      ? this.selected : meta.fighter_ids[0];

    let html =
      (mode === "demo"
        ? '<p class="muted small"><b>DEMO MODE</b> — simulated local pool, no real SOL moves. ' +
          "The real betting engine arrives in Phase 5.</p>"
        : "") +
      '<div class="muted small" style="margin-bottom:8px">Pool: <b>' + esc(fmtSol(total)) +
      ' SOL</b> · ' + pool.bet_count + " bets · house cut " + esc(String(pool.house_cut_pct)) + "%</div>" +
      '<div id="bet-options">';
    meta.fighter_ids.forEach((fid) => {
      const p = pool.pools[fid] || 0;
      const share = total > 0 ? p / total : 1 / n;
      const mult = p > 0 && total > 0 ? total / p : 0;
      html +=
        '<button class="bet-option' + (fid === this.selected ? " selected" : "") +
        '" data-fid="' + esc(fid) + '">' +
        '<span class="bet-dot" style="background:' + esc(fighterColor(regIdOf(fid))) + '"></span>' +
        "<span>" + esc(fighterName(regIdOf(fid))) + "</span>" +
        '<span class="odds">' + (mult > 0 ? "×" + mult.toFixed(2) : "—") +
        ' <span class="muted">(' + (share * 100).toFixed(1) + "%)</span></span></button>";
    });
    html += "</div>" +
      '<label class="field"><span>Amount (SOL)</span>' +
      '<input id="bet-amount" type="number" min="0.01" step="0.01" value="0.10"></label>' +
      '<label class="field"><span>Your wallet address</span>' +
      '<input id="bet-wallet" type="text" placeholder="Solana wallet address" autocomplete="off"></label>' +
      '<button id="btn-bet" class="btn"' +
        (meta.status === "running" && !ArenaView.done ? "" : " disabled") + ">" +
        "Place bet (simulation)</button>" +
      '<div class="my-bets"><h3>My bets</h3><div id="my-bets-list" class="muted small">Enter your wallet to see your bets.</div></div>';
    body.innerHTML = html;

    body.querySelectorAll(".bet-option").forEach((b) => {
      b.addEventListener("click", () => {
        this.selected = b.dataset.fid;
        body.querySelectorAll(".bet-option").forEach((x) => x.classList.remove("selected"));
        b.classList.add("selected");
      });
    });
    const walletInput = body.querySelector("#bet-wallet");
    const refreshMine = async () => {
      const w = walletInput.value.trim();
      const list = $("#my-bets-list");
      if (!w) { list.innerHTML = "Enter your wallet to see your bets."; return; }
      const bets = await BetsAPI.myBets(meta.id, w);
      list.innerHTML = bets.length
        ? bets.map((b) => '<div class="bet-row"><span>' + esc(fighterName(regIdOf(b.fighter_id))) +
            "</span><span><b>" + esc(fmtSol(b.amount_sol)) + " SOL</b></span></div>").join("")
        : "No bets from this wallet yet.";
    };
    walletInput.addEventListener("change", refreshMine);
    body.querySelector("#btn-bet").addEventListener("click", async () => {
      const amount = Number(body.querySelector("#bet-amount").value);
      const wallet = walletInput.value.trim();
      if (!wallet) { alert("Enter a wallet address first."); return; }
      if (!(amount > 0)) { alert("Enter an amount greater than 0."); return; }
      try {
        await BetsAPI.placeBet(meta.id, {
          fighter_id: this.selected, wallet: wallet, amount_sol: amount,
        });
        await this.render();
        const wi = $("#bet-wallet");
        if (wi) { wi.value = wallet; wi.dispatchEvent(new Event("change")); }
      } catch (e) {
        alert("Bet failed: " + (e.body || e.message));
      }
    });
  },
};

function renderBets() { BetUI.render(); }

/* ============================== new battle modal ============================== */

function openNewBattleModal() {
  const root = $("#modal-root");
  root.innerHTML =
    '<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true">' +
    "<h2>New battle</h2>" +
    '<div class="fighter-checks">' +
      S.fighters.map((f) =>
        '<label class="checkbox-row"><input type="checkbox" value="' + esc(f.id) +
        '" checked> ' + esc(f.name) + "</label>").join("") +
    "</div>" +
    '<label class="field"><span>Seed (optional, blank = random)</span>' +
    '<input id="nb-seed" type="number" placeholder="random"></label>' +
    '<label class="checkbox-row"><input id="nb-exhibition" type="checkbox"> ' +
    "Exhibition (demo fight, no betting)</label>" +
    '<div class="btn-row">' +
      '<button class="btn btn-small btn-ghost" id="nb-cancel">Cancel</button>' +
      '<button class="btn btn-small" id="nb-go">Start battle</button>' +
    "</div></div></div>";

  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-backdrop").addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-backdrop")) close();
  });
  $("#nb-cancel").addEventListener("click", close);
  $("#nb-go").addEventListener("click", async () => {
    const ids = $$("#modal-root .fighter-checks input:checked").map((c) => c.value);
    if (ids.length < 2) { alert("Pick at least 2 fighters."); return; }
    if (ids.length > 8) { alert("Pick at most 8 fighters."); return; }
    const seedRaw = $("#nb-seed").value.trim();
    const body = {
      fighter_ids: ids,
      exhibition: $("#nb-exhibition").checked,
    };
    if (seedRaw !== "") body.seed = Number(seedRaw);
    $("#nb-go").disabled = true;
    try {
      const b = await api("/api/battles", { method: "POST", body: JSON.stringify(body) });
      close();
      goToBattle(b.id);
    } catch (e) {
      alert("Could not start battle: " + (e.body || e.message));
      $("#nb-go").disabled = false;
    }
  });
}

/* ============================== boot ============================== */

async function boot() {
  try {
    await loadSettings();
  } catch (e) {
    const bar = $("#brand-error");
    bar.hidden = false;
    bar.textContent = "Could not reach the API at " + location.origin +
      " — is the backend running? (" + (e.message || e) + ")";
    return;
  }
  try {
    const d = await api("/api/fighters");
    S.fighters = d.fighters || [];
    S.fighters.forEach((f) => { S.byRegistry[f.id] = f; });
  } catch (e) {
    $("#fighter-grid").innerHTML = '<p class="muted">Could not load fighters.</p>';
  }
  ArenaView.initCanvas();
  $("#btn-hire").addEventListener("click", submitHire);
  $("#btn-new-battle").addEventListener("click", openNewBattleModal);
  $("#btn-more-battles").addEventListener("click", () =>
    BattlesView.render(BattlesView.offset + BattlesView.limit));
  $("#btn-more-burns").addEventListener("click", () =>
    TreasuryView.render(TreasuryView.offset + TreasuryView.limit));
  window.addEventListener("hashchange", route);
  route();
}

document.addEventListener("DOMContentLoaded", boot);
