/* ailab UI — vanilla JS single-page app. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");

const state = {
  experiments: [],
  sys: null,
  containerStats: {},   // exp -> stats
  currentExp: null,     // detail view id
  logBuffer: [],        // detail view lines
  tools: [],
};

const fmtBytes = (b) => {
  if (b == null) return "—";
  const gb = b / 1024 ** 3;
  return gb >= 10 ? `${Math.round(gb)} GB` : `${gb.toFixed(1)} GB`;
};
const fmtWhen = (ts) => ts ? new Date(ts).toLocaleString() : "—";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ---------- websocket ---------- */

let ws;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $("#ws-state").textContent = "ws: live"; };
  ws.onclose = () => {
    $("#ws-state").textContent = "ws: reconnecting…";
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    handleWS(m);
  };
  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
}

function handleWS(m) {
  switch (m.type) {
    case "stats_system":
      state.sys = m;
      renderGauges();
      break;
    case "stats_container":
      state.containerStats[m.exp] = m;
      if (state.currentExp === m.exp) renderContainerStats(m);
      break;
    case "status": {
      if (m.exp === null) { toast(m.status); break; }
      const exp = state.experiments.find((e) => e.id === m.exp);
      if (exp) exp.status = m.status; else refreshExperiments();
      if (route().name === "dash") renderExperimentList();
      if (state.currentExp === m.exp) loadDetail(m.exp, { soft: true });
      break;
    }
    case "plan_ready":
      if (route().name === "dash" || state.currentExp === m.exp) {
        openPlanModal(m.exp);
      }
      break;
    case "progress": {
      const exp = state.experiments.find((e) => e.id === m.exp);
      if (exp && m.pct != null) { exp.progress_pct = m.pct; exp.progress_msg = m.message; }
      if (route().name === "dash") renderExperimentList();
      if (state.currentExp === m.exp) renderProgress(m);
      break;
    }
    case "log":
      if (state.currentExp === m.exp) appendLog(m.line);
      break;
    case "trace":
      if (state.currentExp === m.exp) appendTrace(m.event);
      break;
    case "tools":
      state.tools = m.tools || [];
      if (route().name === "tools") renderTools();
      break;
  }
}

/* ---------- router ---------- */

function route() {
  const h = location.hash || "#/";
  if (h.startsWith("#/exp/")) return { name: "exp", id: h.slice(6) };
  if (h === "#/tools") return { name: "tools" };
  if (h === "#/services") return { name: "services" };
  if (h === "#/config") return { name: "config" };
  return { name: "dash" };
}

window.addEventListener("hashchange", render);

function setNav(name) {
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active",
      a.dataset.nav === (name === "exp" ? "dash" : name));
  });
}

async function render() {
  const r = route();
  setNav(r.name);
  state.currentExp = r.name === "exp" ? r.id : null;
  if (r.name === "dash") return renderDash();
  if (r.name === "exp") return renderDetailPage(r.id);
  if (r.name === "tools") return renderToolsPage();
  if (r.name === "services") return renderServicesPage();
  if (r.name === "config") return renderConfigPage();
}

/* ---------- dashboard ---------- */

async function refreshExperiments() {
  state.experiments = await api("/experiments");
}

async function renderDash() {
  view.innerHTML = `
    <div class="row between">
      <div><h1>Experiments</h1><div class="sub" id="dash-sub"></div></div>
      <div class="row">
        <button class="ghost" onclick="pruneImages()">Prune images</button>
        <button class="primary" onclick="openNewModal()">New experiment</button>
      </div>
    </div>
    <div class="gauges" id="gauges"></div>
    <h2>Runs</h2>
    <div class="list" id="exp-list"><div class="empty">Loading…</div></div>`;
  renderGauges();
  await refreshExperiments();
  renderExperimentList();
}

function renderGauges() {
  const el = $("#gauges");
  if (!el) return;
  const s = state.sys;
  if (!s) { el.innerHTML = ""; return; }
  const g = [];
  g.push(gauge("CPU", `${Math.round(s.cpu_pct)}<small>%</small>`, s.cpu_pct));
  g.push(gauge("RAM", `${fmtBytes(s.ram_used_bytes)} <small>/ ${fmtBytes(s.ram_total_bytes)}</small>`,
    (s.ram_used_bytes / s.ram_total_bytes) * 100));
  if (s.gpu) {
    g.push(gauge("GPU VRAM", `${fmtBytes(s.gpu.vram_used_bytes)} <small>/ ${fmtBytes(s.gpu.vram_total_bytes)}</small>`,
      (s.gpu.vram_used_bytes / s.gpu.vram_total_bytes) * 100, true));
  }
  g.push(gauge("Disk free", `${fmtBytes(s.disk_free_bytes)}`,
    100 - (s.disk_free_bytes / s.disk_total_bytes) * 100));
  el.innerHTML = g.join("");
}

function gauge(label, valueHTML, pct, live = false) {
  return `<div class="gauge"><div class="label">${label}</div>
    <div class="value">${valueHTML}</div>
    <div class="bar ${live ? "live" : ""}"><div style="width:${Math.min(100, pct || 0)}%"></div></div></div>`;
}

function renderExperimentList() {
  const el = $("#exp-list");
  if (!el) return;
  const running = state.experiments.filter((e) => e.status === "running").length;
  const queued = state.experiments.filter((e) => e.status === "queued").length;
  const sub = $("#dash-sub");
  if (sub) sub.textContent = `${state.experiments.length} total · ${running} running · ${queued} queued`;
  if (!state.experiments.length) {
    el.innerHTML = `<div class="empty">No experiments yet. Create one to put the lab to work.</div>`;
    return;
  }
  el.innerHTML = state.experiments.map((e) => `
    <div class="item" onclick="location.hash='#/exp/${e.id}'">
      <span class="lamp ${e.status}"></span>
      <div style="flex:1;min-width:0">
        <div class="title">${esc(e.title || e.prompt.slice(0, 90))}</div>
        <div class="meta">${e.id} · rev ${e.revision} · ${fmtWhen(e.created_at)}</div>
      </div>
      ${e.status === "running" && e.progress_pct != null ? `
        <div class="mini-progress">
          <div class="pct">${e.progress_pct}% ${esc(e.progress_msg || "")}</div>
          <div class="bar live"><div style="width:${e.progress_pct}%"></div></div>
        </div>` : ""}
      <span class="badge ${e.status}">${e.status.replace(/_/g, " ")}</span>
    </div>`).join("");
}

async function pruneImages() {
  const r = await api("/system/prune-images", { method: "POST" });
  toast(`Pruned ${r.removed.length} experiment image(s)`);
}

/* ---------- new experiment modal ---------- */

function openNewModal() {
  modal(`
    <h3>New experiment</h3>
    <div class="field">
      <textarea id="ne-prompt" rows="4" placeholder="Compare three anomaly detection methods on the KDD dataset and report precision and recall"></textarea>
    </div>
    <div class="grid3">
      <div class="field"><div class="flabel">Model</div>
        <select id="ne-model"><option value="">Per-phase routing (default)</option></select></div>
      <div class="field"><div class="flabel">Time limit (min)</div>
        <input id="ne-timeout" type="number" value="240"></div>
      <div class="field"><div class="flabel">Check-in every (min)</div>
        <input id="ne-checkin" type="number" value="5"></div>
    </div>
    <div class="grid3">
      <div class="field"><div class="flabel">CPUs</div><input id="ne-cpus" type="number" value="4"></div>
      <div class="field"><div class="flabel">RAM (GB)</div><input id="ne-mem" type="number" value="16"></div>
      <div></div>
    </div>
    <div class="row" style="gap:18px;flex-wrap:wrap">
      <label class="check"><input type="checkbox" id="ne-review" checked> Review plan before run</label>
      <label class="check"><input type="checkbox" id="ne-net" checked> Network access</label>
      <label class="check"><input type="checkbox" id="ne-keep"> Keep services after run</label>
    </div>
    <div class="actions">
      <button onclick="closeModal()">Cancel</button>
      <button class="primary" onclick="createExperiment()">Create experiment</button>
    </div>`);
  api("/system/models").then((r) => {
    const sel = $("#ne-model");
    if (sel) r.models.forEach((m) => {
      const o = document.createElement("option"); o.value = m; o.textContent = m;
      sel.appendChild(o);
    });
  }).catch(() => {});
}

async function createExperiment() {
  const prompt = $("#ne-prompt").value.trim();
  if (!prompt) { toast("Enter an experiment prompt"); return; }
  const body = {
    prompt,
    review_before_run: $("#ne-review").checked,
    network_access: $("#ne-net").checked,
    keep_services: $("#ne-keep").checked,
    timeout_minutes: +$("#ne-timeout").value || undefined,
    checkin_interval_minutes: +$("#ne-checkin").value || undefined,
    cpus: +$("#ne-cpus").value || undefined,
    mem_gb: +$("#ne-mem").value || undefined,
    model: $("#ne-model").value || undefined,
  };
  const exp = await api("/experiments", { method: "POST", body });
  closeModal();
  location.hash = `#/exp/${exp.id}`;
}

/* ---------- plan approval modal ---------- */

async function openPlanModal(id) {
  const exp = await api(`/experiments/${id}`);
  if (exp.status !== "awaiting_approval") return;
  const p = exp.plan || {};
  modal(`
    <h3>Plan review — ${esc(p.title || id)}</h3>
    <div class="sub" style="margin-bottom:10px">${esc(p.objective || "")}</div>
    <div class="sub" style="margin-bottom:10px">Success: ${esc(p.success_criteria || "")}</div>
    <div class="sub" style="margin-bottom:10px">
      env: ${esc(p.environment?.type)} · pip: ${esc((p.environment?.requirements || []).join(", ") || "none")}
      · services: ${esc((p.services || []).map(s => s.kind).join(", ") || "none")}
      · ${p.resources?.cpus} cpu / ${p.resources?.mem_gb} GB / ${p.resources?.timeout_minutes} min
    </div>
    <pre class="plan">${esc((p.files || []).map(f => `# ── ${f.path}\n${f.content}`).join("\n\n"))}</pre>
    <div class="actions">
      <button class="danger" onclick="planDecision('${id}', false)">Reject</button>
      <button class="primary" onclick="planDecision('${id}', true)">Approve and launch</button>
    </div>`);
}

async function planDecision(id, ok) {
  await api(`/experiments/${id}/${ok ? "approve" : "reject"}`, { method: "POST" });
  closeModal();
}

/* ---------- detail ---------- */

async function renderDetailPage(id) {
  view.innerHTML = `<div class="empty">Loading…</div>`;
  await loadDetail(id);
}

async function loadDetail(id, { soft = false } = {}) {
  let exp;
  try { exp = await api(`/experiments/${id}`); }
  catch { view.innerHTML = `<div class="empty">Experiment not found.</div>`; return; }
  const p = exp.plan || {};
  const running = exp.status === "running";
  const paused = exp.status === "paused";

  if (soft && $("#det-status")) {
    $("#det-status").textContent = exp.status.replace(/_/g, " ");
    $("#det-status").className = `badge ${exp.status}`;
    renderControls(exp);
    renderTraceAll(exp.events);
    renderArtifacts(exp.artifacts);
    if (exp.conclusion) renderConclusion(exp.conclusion);
    return;
  }

  view.innerHTML = `
    <div class="row between" style="align-items:flex-start">
      <div style="min-width:0">
        <div class="row" style="flex-wrap:wrap">
          <h1 style="margin:0">${esc(exp.title || exp.prompt.slice(0, 80))}</h1>
          <span class="badge ${exp.status}" id="det-status">${exp.status.replace(/_/g, " ")}</span>
        </div>
        <div class="sub">${exp.id} · rev ${exp.revision} · image ${esc(exp.image || "—")} · started ${fmtWhen(exp.started_at)}</div>
      </div>
      <div class="row" id="det-controls"></div>
    </div>
    <div class="progress-line" id="det-progress" style="display:${running ? "" : "none"}">
      <div class="bar live"><div id="det-bar" style="width:${exp.progress_pct || 0}%"></div></div>
      <span class="pct" id="det-pct">${exp.progress_pct != null ? exp.progress_pct + "%" : ""} ${esc(exp.progress_msg || "")}</span>
    </div>
    <div class="statgrid" id="det-stats"></div>
    ${exp.status === "awaiting_approval" ? `<h2>Plan</h2>
      <div class="row"><button class="primary" onclick="openPlanModal('${id}')">Review plan</button></div>` : ""}
    <h2>Live logs</h2>
    <div class="logpane" id="det-logs"></div>
    <h2>Agent trace</h2>
    <div class="trace" id="det-trace"></div>
    <h2>Artifacts</h2>
    <div class="chips" id="det-artifacts"></div>
    <div id="det-conclusion"></div>
    <h2>Ask about this experiment</h2>
    <div class="chatlog" id="det-chat"></div>
    <div class="row">
      <input id="det-chat-input" placeholder="Why did revision 1 fail?"
             onkeydown="if(event.key==='Enter')sendChat('${id}')">
      <button onclick="sendChat('${id}')">Send</button>
    </div>`;

  renderControls(exp);
  renderTraceAll(exp.events);
  renderArtifacts(exp.artifacts);
  if (exp.conclusion) renderConclusion(exp.conclusion);
  const cs = state.containerStats[id];
  if (cs) renderContainerStats(cs);

  const logs = await api(`/experiments/${id}/logs?tail=400`);
  state.logBuffer = logs.lines;
  const pane = $("#det-logs");
  pane.innerHTML = logs.lines.map(logLineHTML).join("\n") ||
    `<span style="color:var(--text-faint)">no output yet</span>`;
  pane.scrollTop = pane.scrollHeight;
  if (paused) renderControls(exp);
}

function renderControls(exp) {
  const el = $("#det-controls");
  if (!el) return;
  const b = [];
  const id = exp.id;
  if (exp.status === "running")
    b.push(`<button onclick="expOp('${id}','pause')">Pause</button>`);
  if (exp.status === "paused")
    b.push(`<button class="primary" onclick="expOp('${id}','resume')">Resume</button>`);
  if (!["completed", "failed", "stopped"].includes(exp.status))
    b.push(`<button class="danger" onclick="expOp('${id}','stop')">Stop</button>`);
  else
    b.push(`<button onclick="expOp('${id}','rerun')">Re-run</button>`,
           `<button class="danger" onclick="deleteExp('${id}')">Delete</button>`);
  el.innerHTML = b.join("");
}

async function expOp(id, op) {
  const r = await api(`/experiments/${id}/${op}`, { method: "POST" });
  if (op === "rerun" && r.id) location.hash = `#/exp/${r.id}`;
}

async function deleteExp(id) {
  if (!confirm("Delete this experiment, its workspace, containers, and images?")) return;
  await api(`/experiments/${id}`, { method: "DELETE" });
  location.hash = "#/";
}

function renderContainerStats(s) {
  const el = $("#det-stats");
  if (!el) return;
  el.innerHTML = `
    <div class="stat"><div class="label">Container CPU</div><div class="value">${s.cpu_pct}%</div></div>
    <div class="stat"><div class="label">Container RAM</div>
      <div class="value">${fmtBytes(s.mem_bytes)} / ${fmtBytes(s.mem_limit_bytes)}</div></div>`;
}

function renderProgress(m) {
  const line = $("#det-progress");
  if (!line) return;
  line.style.display = "";
  if (m.pct != null) {
    $("#det-bar").style.width = `${m.pct}%`;
    $("#det-pct").textContent = `${m.pct}% ${m.message || ""}`;
  } else if (m.message) {
    $("#det-pct").textContent = m.message;
  }
}

function logLineHTML(line) {
  return esc(line).replace(/##PROGRESS[^#]*##/g, (s) => `<span class="marker">${s}</span>`);
}

function appendLog(line) {
  const pane = $("#det-logs");
  if (!pane) return;
  const stick = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 30;
  state.logBuffer.push(line);
  if (state.logBuffer.length > 2000) state.logBuffer.shift();
  pane.insertAdjacentHTML("beforeend", "\n" + logLineHTML(line));
  if (stick) pane.scrollTop = pane.scrollHeight;
}

const TRACE_KINDS = {
  status: "status", created: "created", plan: "plan approved", run: "run started",
  toolcall: "tool call", toolresult: "tool result", checkin: "check-in",
  revision: "revision", service: "service", artifact: "artifact",
  warning: "warning", error: "error", conclusion: "conclusion",
};

function traceEntryHTML(ev) {
  const kind = TRACE_KINDS[ev.type] || ev.type;
  const p = ev.payload || {};
  let body = "";
  if (ev.type === "toolcall") body = `${p.name}(${JSON.stringify(p.arguments || {})})`;
  else if (ev.type === "toolresult") body = (p.result || "").slice(0, 300);
  else if (ev.type === "checkin") body = `→ ${p.action}${p.notes ? " · " + p.notes : ""}\n${(p.reasoning || "").slice(0, 400)}`;
  else if (ev.type === "revision") body = `revision ${p.revision}: ${(p.files || []).join(", ")}`;
  else if (ev.type === "status") body = p.status;
  else if (ev.type === "conclusion") body = (p.conclusion || "").slice(0, 500);
  else body = p.message || p.title || p.name || p.path || p.kind || "";
  return `<div class="entry">
    <div class="head"><span class="kind ${ev.type}">${esc(kind)}</span>
      <span class="when">${fmtWhen(ev.ts)}</span></div>
    ${body ? `<div class="body">${esc(body)}</div>` : ""}
  </div>`;
}

function renderTraceAll(events) {
  const el = $("#det-trace");
  if (!el) return;
  const skip = new Set(["log"]);
  el.innerHTML = (events || []).filter((e) => !skip.has(e.type))
    .map(traceEntryHTML).join("") || `<div class="empty">Nothing yet.</div>`;
}

function appendTrace(ev) {
  const el = $("#det-trace");
  if (!el) return;
  el.insertAdjacentHTML("beforeend", traceEntryHTML(ev));
}

function renderArtifacts(artifacts) {
  const el = $("#det-artifacts");
  if (!el) return;
  el.innerHTML = (artifacts || []).map((a) =>
    `<a class="chip" href="/api/experiments/${state.currentExp}/artifacts/${encodeURIComponent(a.path)}"
        download>${esc(a.path)} · ${fmtBytes(a.size_bytes)}</a>`).join("")
    || `<span class="sub">Files written to output/ appear here.</span>`;
}

function renderConclusion(text) {
  let el = $("#det-conclusion");
  if (!el) return;
  el.innerHTML = `<h2>Conclusion</h2>
    <div class="card"><div class="desc" style="white-space:pre-wrap">${esc(text)}</div></div>`;
}

async function sendChat(id) {
  const input = $("#det-chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  const log = $("#det-chat");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div>`);
  log.insertAdjacentHTML("beforeend", `<div class="msg assistant" id="chat-pending">…</div>`);
  try {
    const r = await api(`/experiments/${id}/chat`, { method: "POST", body: { message: q } });
    $("#chat-pending").outerHTML = `<div class="msg assistant">${esc(r.answer)}</div>`;
  } catch (e) {
    $("#chat-pending").outerHTML = `<div class="msg assistant">error: ${esc(e.message)}</div>`;
  }
}

/* ---------- tools ---------- */

async function renderToolsPage() {
  view.innerHTML = `
    <div class="row between">
      <div><h1>Tools</h1><div class="sub">watching ~/ailab-data/tools</div></div>
      <button onclick="reloadTools()">Reload</button>
    </div>
    <div class="cards" id="tool-cards" style="margin-top:18px"><div class="empty">Loading…</div></div>`;
  state.tools = await api("/tools");
  renderTools();
}

function renderTools() {
  const el = $("#tool-cards");
  if (!el) return;
  if (!state.tools.length) {
    el.innerHTML = `<div class="empty">Drop .py files exposing SCHEMA and execute() into the tools directory.</div>`;
    return;
  }
  el.innerHTML = state.tools.map((t) => {
    const fn = t.schema?.function || {};
    const params = Object.keys(fn.parameters?.properties || {}).join(", ");
    return `<div class="card ${t.status === "error" ? "error" : ""}">
      <div class="row between"><span class="name">${esc(t.name)}</span>
        <span class="badge ${t.status === "error" ? "failed" : "completed"}">${t.status}</span></div>
      ${t.status === "error"
        ? `<div class="err">${esc(t.error)}</div>`
        : `<div class="desc">${esc(fn.description || "")}</div>
           <div class="sub">params: ${esc(params || "none")}</div>`}
      <div class="foot">
        ${t.status === "loaded" ? `<button onclick="openToolTest('${esc(t.name)}')">Test run</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

async function reloadTools() {
  state.tools = await api("/tools/reload", { method: "POST" });
  renderTools();
  toast("Tools reloaded");
}

function openToolTest(name) {
  const t = state.tools.find((x) => x.name === name);
  const props = t?.schema?.function?.parameters?.properties || {};
  const fields = Object.entries(props).map(([k, v]) => `
    <div class="field"><div class="flabel">${esc(k)} <span class="sub">${esc(v.type || "")}</span></div>
      <input data-arg="${esc(k)}" placeholder="${esc(v.description || "")}"></div>`).join("");
  modal(`
    <h3>Test ${esc(name)}</h3>
    ${fields || `<div class="sub">No parameters.</div>`}
    <pre class="plan" id="tool-result" style="display:none"></pre>
    <div class="actions">
      <button onclick="closeModal()">Close</button>
      <button class="primary" onclick="runToolTest('${esc(name)}')">Run</button>
    </div>`);
}

async function runToolTest(name) {
  const args = {};
  document.querySelectorAll("[data-arg]").forEach((i) => {
    if (i.value !== "") args[i.dataset.arg] = i.value;
  });
  const out = $("#tool-result");
  out.style.display = "";
  out.textContent = "running…";
  try {
    const r = await api(`/tools/${name}/test`, { method: "POST", body: { args } });
    try { out.textContent = JSON.stringify(JSON.parse(r.result), null, 2); }
    catch { out.textContent = r.result; }
  } catch (e) { out.textContent = `error: ${e.message}`; }
}

/* ---------- services ---------- */

async function renderServicesPage() {
  view.innerHTML = `<h1>Services</h1>
    <div class="sub">Databases and caches kept after their experiments finished.</div>
    <div class="cards" id="svc-cards" style="margin-top:18px"><div class="empty">Loading…</div></div>`;
  const services = await api("/services");
  const el = $("#svc-cards");
  if (!services.length) {
    el.innerHTML = `<div class="empty">No services. Plans that declare services with persist=true land here.</div>`;
    return;
  }
  el.innerHTML = services.map((s) => `
    <div class="card">
      <div class="row between"><span class="name">${esc(s.kind)}</span>
        <span class="badge ${s.state === "running" ? "running" : ""}">${esc(s.state)}</span></div>
      <div class="sub">${esc(s.container_name)}</div>
      <div class="sub">experiment ${esc(s.experiment_id)} · ${s.persist ? "persistent" : "ephemeral"}</div>
      <div class="foot">
        <button onclick="svcOp(${s.id},'start')">Start</button>
        <button onclick="svcOp(${s.id},'stop')">Stop</button>
        <button class="danger" onclick="svcOp(${s.id},'delete')">Delete</button>
      </div>
    </div>`).join("");
}

async function svcOp(id, op) {
  if (op === "delete" && !confirm("Delete this service container?")) return;
  await api(`/services/${id}/${op}`, { method: "POST" });
  renderServicesPage();
}

/* ---------- config ---------- */

async function renderConfigPage() {
  const cfg = await api("/config");
  view.innerHTML = `
    <h1>Config</h1>
    <div class="sub">Stored overrides merge over defaults; changes apply without restart.</div>
    <div class="field" style="margin-top:16px">
      <textarea id="cfg-json" rows="24">${esc(JSON.stringify(cfg, null, 2))}</textarea>
    </div>
    <div class="row">
      <button class="primary" onclick="saveConfig()">Save</button>
      <span class="sub">Saved values become the override file; edit freely.</span>
    </div>`;
}

async function saveConfig() {
  let patch;
  try { patch = JSON.parse($("#cfg-json").value); }
  catch { toast("Invalid JSON"); return; }
  await api("/config", { method: "PATCH", body: { patch } });
  toast("Config saved");
}

/* ---------- modal helpers ---------- */

function modal(html) {
  $("#modal-root").innerHTML =
    `<div class="overlay" onclick="if(event.target===this)closeModal()"><div class="modal">${html}</div></div>`;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

/* ---------- boot ---------- */

connectWS();
render();
setInterval(() => { if (route().name === "dash") refreshExperiments().then(renderExperimentList); }, 15000);
