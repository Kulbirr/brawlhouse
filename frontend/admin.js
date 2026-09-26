/* Agent Arena admin panel (Phase 7) - IN-HOUSE ONLY.

Plain JS, no dependencies. The admin token lives in sessionStorage only and
is sent as the X-Admin-Token header on every request. A 401 clears the
token and re-shows the lock screen. The token is never logged, never put in
a URL, and never written into code.

Note on number encoding: the backend settings API type-checks values
strictly (float fields reject JSON integers), and JSON.stringify(1.0)
serializes as "1". encodeSettings() rewrites whole-number float fields to
"1.0" form so saves never 400 on a whole number.
*/

"use strict";

var TOKEN_KEY = "aa_admin_token";
var SCARY_PHRASE = "GO LIVE";

/* Settings field -> DOM id. Labels stay generic; no project/token names. */
var FIELD_IDS = {
  project_name: "f-project-name",
  token_ticker: "f-token-ticker",
  token_mint: "f-token-mint",
  hire_fee_sol: "f-hire-fee",
  betting_house_cut_pct: "f-house-cut",
  buyback_pct: "f-buyback-pct",
  team_pct: "f-team-pct",
  buyback_interval_minutes: "f-buyback-interval",
  buyback_enabled: "f-buyback-enabled",
  buyback_live: "f-buyback-live",
  buyback_mock_rate: "f-mock-rate",
  buyback_hot_sol_cap: "f-hot-cap",
  team_sweep_enabled: "f-team-sweep",
  betting_live: "f-betting-live"
};

var FLOAT_FIELDS = ["hire_fee_sol", "betting_house_cut_pct", "buyback_pct",
  "team_pct", "buyback_mock_rate", "buyback_hot_sol_cap"];
var INT_FIELDS = ["buyback_interval_minutes"];
var BOOL_FIELDS = ["betting_live", "buyback_enabled", "buyback_live", "team_sweep_enabled"];

var SCARY = {
  betting_live: {
    title: "Enable LIVE betting escrow?",
    text: "REAL MONEY WARNING: with live betting, bettors must send SOL to the " +
      "escrow wallet before each bet, and winnings are paid out as REAL on-chain " +
      "transfers from that wallet. Only enable after the owner checklist is done: " +
      "escrow wallet created and funded with a small hot balance (bulk in cold " +
      "storage), ESCROW_PRIVATE_KEY in the server .env, Helius key set, and the " +
      "betting/escrow flow legally reviewed."
  },
  buyback_live: {
    title: "Enable LIVE buybacks?",
    text: "REAL MONEY WARNING: with live buybacks, the bot will swap SOL for the " +
      "token via the Jupiter API and burn the bought tokens on-chain, signing with " +
      "the treasury hot wallet key. Only enable after the owner checklist is done: " +
      "token launched and token_mint set, TREASURY_PRIVATE_KEY in the server .env, " +
      "hot wallet funded under the hot SOL cap, Helius key set, and the buyback " +
      "flow legally reviewed."
  }
};

/* currentSettings holds the last-loaded server values, used to detect an
   off->on flip of a live flag so the scary confirmation only fires then. */
var currentSettings = {};

/* ---------------------------------------------------------------- helpers */
function $(id) { return document.getElementById(id); }

function token() { return sessionStorage.getItem(TOKEN_KEY) || ""; }

function fmtTs(ts) {
  if (!ts) return "-";
  var d = new Date(String(ts).replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) d = new Date(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

function fmtNum(n, digits) {
  if (n === null || n === undefined) return "-";
  var x = Number(n);
  if (!isFinite(x)) return "-";
  return x.toLocaleString("en-US", { maximumFractionDigits: digits === undefined ? 9 : digits });
}

var statusTimer = null;
function status(msg, isError) {
  var el = $("status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  el.hidden = false;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(function () { el.hidden = true; }, 4000);
}

/* Authenticated fetch. 401 -> token cleared, lock screen re-shown with an
   "invalid token" message. 503 -> admin not configured on the server. */
async function api(path, opts) {
  opts = opts || {};
  var headers = { "X-Admin-Token": token() };
  if (opts.headers) {
    Object.keys(opts.headers).forEach(function (k) { headers[k] = opts.headers[k]; });
  }
  var res = await fetch(path, {
    method: opts.method || "GET",
    headers: headers,
    body: opts.body
  });
  if (res.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    showLock("Invalid token. Enter the admin token again.");
    throw new Error("unauthorized");
  }
  if (res.status === 503) {
    throw new Error("Admin not configured on this server: set ADMIN_TOKEN in the server .env");
  }
  var body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON body */ }
  if (!res.ok) {
    var msg = (body && (body.detail || body.message)) || ("HTTP " + res.status);
    throw new Error(String(msg));
  }
  return body;
}

/* Serialize a settings payload so whole-number floats keep their decimal
   point (backend rejects JSON ints for float fields). */
function encodeSettings(obj) {
  var s = JSON.stringify(obj);
  FLOAT_FIELDS.forEach(function (k) {
    s = s.replace(new RegExp('"' + k + '":(-?\\d+)([,}])'), '"$1":$2.0$3');
  });
  return s;
}

function readField(key) {
  var el = $(FIELD_IDS[key]);
  if (BOOL_FIELDS.indexOf(key) !== -1) return el.checked;
  var raw = el.value.trim();
  if (INT_FIELDS.indexOf(key) !== -1) {
    var iv = parseInt(raw, 10);
    if (!isFinite(iv) || iv < 0) throw new Error("Invalid value for " + key + ": " + raw);
    return iv;
  }
  if (FLOAT_FIELDS.indexOf(key) !== -1) {
    var fv = parseFloat(raw);
    if (!isFinite(fv) || fv < 0) throw new Error("Invalid value for " + key + ": " + raw);
    return fv;
  }
  return el.value; /* text fields: project_name, token_ticker, token_mint */
}

/* ------------------------------------------------------------------ lock */
function showLock(message) {
  $("admin-root").hidden = true;
  $("lock-screen").hidden = false;
  var err = $("lock-error");
  if (message) { err.textContent = message; err.hidden = false; }
  else { err.hidden = true; }
  $("token-input").value = "";
  setTimeout(function () { $("token-input").focus(); }, 50);
}

function hideLock() {
  $("lock-screen").hidden = true;
  $("admin-root").hidden = false;
}

/* --------------------------------------------------------------- settings */
async function loadSettings() {
  /* PUT with an empty body merges nothing and returns the FULL store
     (private fields included) plus the env-overridden list. */
  var body = await api("/api/admin/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: "{}"
  });
  var s = body.settings || {};
  currentSettings = s;
  Object.keys(FIELD_IDS).forEach(function (key) {
    var el = $(FIELD_IDS[key]);
    if (el.type === "checkbox") el.checked = !!s[key];
    else el.value = (s[key] === null || s[key] === undefined) ? "" : s[key];
  });
  /* Flag fields pinned by environment variables: file writes won't show. */
  document.querySelectorAll("[data-field]").forEach(function (wrap) {
    var old = wrap.querySelector(".env-badge");
    if (old) old.remove();
  });
  (body.env_overridden || []).forEach(function (key) {
    var wrap = document.querySelector('[data-field="' + key + '"]');
    if (!wrap) return;
    var badge = document.createElement("span");
    badge.className = "env-badge";
    badge.textContent = "ENV-LOCKED";
    badge.title = "Overridden by an environment variable: file writes to this field will not take effect until the env var is unset.";
    var label = wrap.querySelector("label > span, .checkbox-row > span");
    (label || wrap).appendChild(badge);
  });
  updateSanity();
  updateBuybackToggle();
}

async function saveSettings(keys) {
  var payload = {};
  keys.forEach(function (k) { payload[k] = readField(k); });
  await api("/api/admin/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: encodeSettings(payload)
  });
  await loadSettings();
  status("Saved.");
}

function updateSanity() {
  var bb = parseFloat($("f-buyback-pct").value);
  var tm = parseFloat($("f-team-pct").value);
  var el = $("sanity-line");
  if (!isFinite(bb) || !isFinite(tm) || bb < 0 || tm < 0) {
    el.innerHTML = '<span class="sanity-warn">Enter valid buyback/team percentages to see the split.</span>';
    return;
  }
  var bbSol = bb / 100, tmSol = tm / 100, rest = 1 - bbSol - tmSol;
  var html = "Of every <b>1 SOL</b> in fees: <b>" + bbSol.toFixed(3) +
    " SOL</b> to buybacks, <b>" + tmSol.toFixed(3) + " SOL</b> to team";
  if (rest > 0.0000005) {
    html += ", <span class='muted'>" + rest.toFixed(3) + " SOL unsplit remainder stays in the treasury</span>";
  } else if (rest < -0.0000005) {
    html += " <span class='sanity-warn'>&mdash; splits exceed 100%; review the numbers</span>";
  }
  html += ".";
  el.innerHTML = html;
}

function updateBuybackToggle() {
  var on = $("f-buyback-enabled").checked;
  var label = $("buyback-state-label");
  label.textContent = on ? "BUYBACKS ACTIVE" : "BUYBACKS PAUSED";
  label.className = "state " + (on ? "on" : "off");
}

/* The big pause toggle saves immediately: it is the in-house kill switch. */
async function onBuybackToggle() {
  try {
    await saveSettings(["buyback_enabled"]);
    status(currentSettings.buyback_enabled ? "Buybacks enabled." : "Buybacks paused. Fees keep accumulating untouched.");
  } catch (e) {
    if (e.message !== "unauthorized") { status("Error: " + e.message, true); loadSettings(); }
  }
}

/* Scary confirmation: only fires on an off->on flip of a live flag. */
var scaryProceed = null;
function maybeScary(key, proceed) {
  var el = $(FIELD_IDS[key]);
  if (el.checked && !currentSettings[key]) {
    $("scary-title").textContent = SCARY[key].title;
    $("scary-text").textContent = SCARY[key].text;
    $("scary-input").value = "";
    $("scary-confirm").disabled = true;
    scaryProceed = proceed;
    $("scary-modal").hidden = false;
    setTimeout(function () { $("scary-input").focus(); }, 50);
  } else {
    proceed();
  }
}

/* --------------------------------------------------------------- fighters */
async function loadFighters() {
  var res = await fetch("/api/fighters");
  if (!res.ok) throw new Error("Could not load fighters (HTTP " + res.status + ")");
  var data = await res.json();
  var host = $("fighter-editors");
  host.innerHTML = "";
  (data.fighters || []).forEach(function (f) {
    var wrap = document.createElement("div");
    wrap.className = "panel";
    wrap.style.marginBottom = "10px";
    var grid = document.createElement("div");
    grid.className = "fighter-edit-grid";
    grid.innerHTML =
      '<div class="fid"></div>' +
      '<div>' +
      '<label class="field"><span>Name</span><input type="text" data-k="name" maxlength="64"></label>' +
      '<label class="field"><span>Tagline</span><input type="text" data-k="tagline" maxlength="160"></label>' +
      '<label class="field"><span>Description</span><textarea data-k="description" maxlength="2000"></textarea></label>' +
      '<div style="display:flex;gap:10px;align-items:center">' +
      '<button class="btn btn-small" data-act="save">Save</button>' +
      '<span class="fighter-record-line" data-k="record"></span>' +
      '</div></div>';
    grid.querySelector(".fid").textContent = f.id;
    grid.querySelector('[data-k="name"]').value = f.name || "";
    grid.querySelector('[data-k="tagline"]').value = f.tagline || "";
    grid.querySelector('[data-k="description"]').value = f.description || "";
    grid.querySelector('[data-k="record"]').textContent =
      "W " + f.wins + " / L " + f.losses + " / D " + f.draws;
    grid.querySelector('[data-act="save"]').addEventListener("click", function () {
      saveFighter(f.id, grid);
    });
    wrap.appendChild(grid);
    host.appendChild(wrap);
  });
}

async function saveFighter(fid, grid) {
  var payload = {
    name: grid.querySelector('[data-k="name"]').value.trim(),
    tagline: grid.querySelector('[data-k="tagline"]').value.trim(),
    description: grid.querySelector('[data-k="description"]').value.trim()
  };
  try {
    await api("/api/admin/fighters/" + encodeURIComponent(fid), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    status("Fighter " + fid + " updated.");
  } catch (e) {
    if (e.message !== "unauthorized") status("Error: " + e.message, true);
  }
}

/* --------------------------------------------------------------- treasury */
async function loadTreasury() {
  var fees = await api("/api/treasury/fees?limit=50");
  var totals = fees.totals || {};
  var cards = $("fee-cards");
  cards.innerHTML = "";
  [["Total fees", totals.total_sol], ["Betting cuts", totals.betting_sol],
   ["Hire fees", totals.hire_sol]].forEach(function (pair) {
    var card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = '<div class="stat-value">' + fmtNum(pair[1], 4) + ' SOL</div>' +
      '<div class="stat-label">' + pair[0] + "</div>";
    cards.appendChild(card);
  });

  var ftb = $("fee-events-table").querySelector("tbody");
  ftb.innerHTML = "";
  (fees.events || []).forEach(function (ev) {
    var tr = document.createElement("tr");
    tr.innerHTML = "<td>" + ev.id + "</td><td>" + fmtTs(ev.created_at) + "</td>" +
      "<td class='tx-link'>" + (ev.battle_id ? String(ev.battle_id).slice(0, 12) + "&hellip;" : "-") + "</td>" +
      "<td>" + ev.source + "</td><td>" + fmtNum(ev.amount_sol, 4) + "</td>";
    ftb.appendChild(tr);
  });
  if (!(fees.events || []).length) {
    ftb.innerHTML = '<tr><td colspan="5" class="muted">No fee events yet.</td></tr>';
  }

  /* Burn history is public; read-only display here. */
  var bres = await fetch("/api/treasury/burns?limit=25");
  var burns = bres.ok ? await bres.json() : { burns: [], total: 0 };
  var btb = $("burn-events-table").querySelector("tbody");
  btb.innerHTML = "";
  (burns.burns || []).forEach(function (b) {
    var tr = document.createElement("tr");
    tr.innerHTML = "<td>" + fmtTs(b.created_at) + "</td><td>" + fmtNum(b.sol_spent, 4) + "</td>" +
      "<td>" + fmtNum(b.tokens_bought, 2) + "</td><td>" + fmtNum(b.tokens_burned, 2) + "</td>" +
      "<td>" + (b.dry_run ? "mock" : "live") + "</td>";
    btb.appendChild(tr);
  });
  if (!(burns.burns || []).length) {
    btb.innerHTML = '<tr><td colspan="5" class="muted">No buyback rounds recorded yet.</td></tr>';
  }
  $("treasury-note").textContent =
    "Fee events are written by bet settlement (source=betting) and the hire endpoint (source=hire). " +
    "The buyback bot consumes unallocated events; the public dashboard shows ledger-based balances.";
}

/* ------------------------------------------------------------------- boot */
async function init() {
  await loadSettings();
  await loadFighters();
  await loadTreasury();

  $("btn-save-branding").addEventListener("click", function () {
    saveSettings(["project_name", "token_ticker", "token_mint"])
      .catch(function (e) { if (e.message !== "unauthorized") status("Error: " + e.message, true); });
  });
  $("btn-save-fees").addEventListener("click", function () {
    saveSettings(["hire_fee_sol", "betting_house_cut_pct", "buyback_pct", "team_pct"])
      .catch(function (e) { if (e.message !== "unauthorized") status("Error: " + e.message, true); });
  });
  $("btn-save-buyback").addEventListener("click", function () {
    maybeScary("buyback_live", function () {
      saveSettings(["buyback_interval_minutes", "buyback_hot_sol_cap", "buyback_mock_rate",
        "buyback_live", "team_sweep_enabled"])
        .catch(function (e) { if (e.message !== "unauthorized") status("Error: " + e.message, true); });
    });
  });
  $("btn-save-betting").addEventListener("click", function () {
    maybeScary("betting_live", function () {
      saveSettings(["betting_live"])
        .catch(function (e) { if (e.message !== "unauthorized") status("Error: " + e.message, true); });
    });
  });
  $("btn-refresh-treasury").addEventListener("click", function () {
    loadTreasury()
      .then(function () { status("Treasury refreshed."); })
      .catch(function (e) { if (e.message !== "unauthorized") status("Error: " + e.message, true); });
  });

  ["f-buyback-pct", "f-team-pct"].forEach(function (id) {
    $(id).addEventListener("input", updateSanity);
  });
  $("f-buyback-enabled").addEventListener("change", onBuybackToggle);

  $("btn-lock").addEventListener("click", function () {
    sessionStorage.removeItem(TOKEN_KEY);
    showLock("");
  });

  /* scary modal wiring */
  $("scary-input").addEventListener("input", function () {
    $("scary-confirm").disabled = $("scary-input").value.trim() !== SCARY_PHRASE;
  });
  $("scary-cancel").addEventListener("click", function () {
    $("scary-modal").hidden = true;
    scaryProceed = null;
    loadSettings(); /* revert the checkbox to the saved value */
  });
  $("scary-confirm").addEventListener("click", function () {
    if ($("scary-input").value.trim() !== SCARY_PHRASE) return;
    $("scary-modal").hidden = true;
    var fn = scaryProceed;
    scaryProceed = null;
    if (fn) fn();
  });
}

document.addEventListener("DOMContentLoaded", function () {
  $("lock-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = $("token-input").value;
    if (!t) return;
    sessionStorage.setItem(TOKEN_KEY, t);
    hideLock();
    init().catch(function (e) {
      /* 401 inside init already re-shows the lock screen; anything else
         surfaces as a status error on the (now hidden) panel, so show lock. */
      if (e.message !== "unauthorized") {
        sessionStorage.removeItem(TOKEN_KEY);
        showLock("Could not load admin data: " + e.message);
      }
    });
  });
  if (token()) {
    hideLock();
    init().catch(function (e) {
      if (e.message !== "unauthorized") {
        sessionStorage.removeItem(TOKEN_KEY);
        showLock("Could not load admin data: " + e.message);
      }
    });
  }
});
