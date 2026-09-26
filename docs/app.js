/*
 * Sahayak front-end. Vanilla JS, no dependencies.
 * Polls the local API every second to render the agent loop live, lets the
 * worker answer the agent's questions, and exposes the demo switches
 * (simulate offline network, simulate model crash).
 */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let current = null;
let modelEnabled = true;
let online = false;

const PRESETS = [
  { label: "Child · cough, fast breathing?", name: "Aarav", age_years: 2, sex: "M", language: "Telugu",
    note: "2 year old boy, cough and fever for 3 days, mother says he is breathing fast and not eating well.", temp_c: 38.6 },
  { label: "Pregnant · headache", name: "Lakshmi", age_years: 24, sex: "F", pregnant: true, language: "Telugu",
    note: "7 months pregnant. Severe headache since morning and says her vision is blurred. Feet swollen." },
  { label: "Hinglish · bachcha behosh", name: "Riya", age_years: 4, sex: "F", language: "Hindi",
    note: "Bachchi ko 2 din se tez bukhar hai, aaj subah se behosh jaisi hai, kuch pee nahi rahi.", temp_c: 39.8 },
  { label: "Adult · mild fever", name: "Ramesh", age_years: 32, sex: "M", language: "Hindi",
    note: "Mild fever and body ache since yesterday, eating and drinking normally, no other complaints.",
    temp_c: 38.1, hr: 92, rr: 18, spo2: 98 },
];

// Replay mode (GitHub Pages): serve recorded real runs instead of the live API.
const REPLAY = window.SAHAYAK_REPLAY;
let replayData = null;
const revealed = {}; // encounter id -> number of trace steps shown so far

async function replayApi(path) {
  if (!replayData) replayData = await (await fetch(REPLAY)).json();
  if (path === "/api/status") {
    return { model: { runtime: true, enabled: true, primary: "gemma4:e4b-it-qat (recorded run)", primary_ready: true },
             sync: { online: false, pending: 0, synced: 0 } };
  }
  if (path === "/api/encounters") return replayData.visits;
  if (path === "/api/handoffs") return replayData.handoffs;
  const m = path.match(/^\/api\/encounters\/(\w+)$/);
  if (m) {
    const e = replayData.encounters[m[1]];
    const n = Math.min((revealed[m[1]] = (revealed[m[1]] ?? 0) + 1), e.steps.length);
    const playing = n < e.steps.length;
    return { ...e, steps: e.steps.slice(0, n), status: playing ? "running" : e.status };
  }
  return {};
}

async function api(path, body) {
  if (REPLAY) {
    if (body !== undefined) { alert("This is a recording of real on-device runs. Clone the repo and run it locally to try the live agent."); return {}; }
    return replayApi(path);
  }
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

function renderPresets() {
  const box = $("#presets");
  PRESETS.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = p.label;
    b.onclick = () => {
      const f = $("#visitForm");
      f.reset();
      for (const [k, v] of Object.entries(p)) {
        const el = f.elements[k];
        if (!el) continue;
        if (el.type === "checkbox") el.checked = !!v; else el.value = v;
      }
    };
    box.appendChild(b);
  });
}

$("#visitForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const vitals = {};
  ["temp_c", "hr", "rr", "spo2", "sbp", "dbp"].forEach((k) => { if (f[k].value !== "") vitals[k] = f[k].value; });
  const { id } = await api("/api/encounters", {
    patient: { id: f.patient_id.value || undefined, name: f.name.value, age_years: parseFloat(f.age_years.value), sex: f.sex.value, pregnant: f.pregnant.checked },
    note: f.note.value, vitals, language: f.language.value,
  });
  current = id;
  refresh();
};

$("#answerForm").onsubmit = async (e) => {
  e.preventDefault();
  const input = e.target.elements.answer;
  await api(`/api/encounters/${current}/answer`, { answer: input.value });
  input.value = "";
  refresh();
};

$("#modelBtn").onclick = async () => { await api("/api/model", { enabled: !modelEnabled }); refreshStatus(); };
$("#netBtn").onclick = async () => { await api("/api/network", { online: !online }); refreshStatus(); };

async function refreshStatus() {
  const s = await api("/api/status");
  modelEnabled = s.model.enabled;
  online = s.sync.online;
  const mp = $("#modelPill");
  const ready = s.model.runtime && s.model.enabled && (s.model.primary_ready || s.model.fallback_ready);
  mp.textContent = ready ? `● ${s.model.primary_ready ? s.model.primary : "gemma4:e2b-it-qat"} local` : (s.model.enabled ? "● model offline: rules-only mode" : "● model killed: rules-only mode");
  mp.className = "pill " + (ready ? "ok" : "bad");
  $("#modelBtn").textContent = s.model.enabled ? "Kill model" : "Restore model";
  $("#netBtn").textContent = online ? "📶 Online" : "✈︎ Offline";
  $("#netBtn").className = "pill toggle " + (online ? "ok" : "bad");
  $("#syncPill").textContent = `outbox: ${s.sync.pending} pending · ${s.sync.synced} synced`;
}

async function refreshLists() {
  const visits = await api("/api/encounters");
  $("#visits").innerHTML = visits.map((v) => `
    <li data-id="${v.id}" class="${v.id === current ? "active" : ""}">
      <span>${esc(v.name)} <span class="muted">${v.age_years ?? ""}y · ${new Date(v.created_at * 1000).toLocaleTimeString()}</span></span>
      <span class="badge ${v.triage || ""}">${esc(v.status === "running" ? "running…" : v.triage || v.status)}</span>
    </li>`).join("") || `<li class="muted">No visits yet</li>`;
  $("#visits").querySelectorAll("li[data-id]").forEach((li) => (li.onclick = () => { current = li.dataset.id; revealed[current] = 0; refresh(); }));

  const hs = await api("/api/handoffs");
  $("#handoffs").innerHTML = hs.map((h) => `
    <li><span><span class="badge ${h.urgency}">${h.urgency}</span> ${esc(h.name)}<br><span class="muted">${esc(h.reason)}</span></span>
    <button class="pill toggle" data-ack="${h.id}">Ack</button></li>`).join("") || `<li class="muted">No open handoffs</li>`;
  $("#handoffs").querySelectorAll("[data-ack]").forEach((b) => (b.onclick = async () => { await api(`/api/handoffs/${b.dataset.ack}/ack`, {}); refreshLists(); }));
}

function stepBody(st) {
  const d = st.detail || {};
  if (st.phase === "decide") return `<div class="body">${esc(d.thought)}</div>` + (Object.keys(d.args || {}).length ? `<details><summary>args</summary><pre>${esc(JSON.stringify(d.args, null, 2))}</pre></details>` : "");
  if (st.phase === "check") return `<div class="body">${d.ok ? "✓ " : "✗ "}${esc(d.feedback || "approved")}</div>`;
  if (st.phase === "recover") return `<div class="body">${esc(d.message)}</div>`;
  if (st.phase === "sense") return `<div class="body">Worker answered: <b>${esc(d.answer)}</b></div>`;
  let summary = "";
  if (st.tool === "extract_findings") summary = `${(d.findings?.symptoms || []).join(", ")}${d.findings?.danger_signs?.length ? " · danger: " + d.findings.danger_signs.join(", ") : ""}`;
  if (st.tool === "check_danger_signs") summary = `protocol level <b class="badge ${d.level}">${d.level}</b> ${(d.flags || []).map((f) => esc(f.reason)).join("; ")}`;
  if (st.tool === "get_patient_history") summary = `${(d.previous_visits || []).length} previous visit(s)`;
  if (st.tool === "ask_health_worker") summary = `Asked: “${esc(d.asked)}”`;
  if (st.tool === "recommend_care") summary = `Plan: <b class="badge ${d.plan?.triage}">${d.plan?.triage}</b> confidence ${d.plan?.confidence ?? "–"}`;
  if (st.tool === "refer_to_clinician") summary = `Referral opened (${esc(d.referral?.urgency)}): ${esc(d.referral?.reason)}`;
  if (st.tool === "finish") summary = esc(d.summary);
  return `<div class="body">${summary}</div><details><summary>result</summary><pre>${esc(JSON.stringify(d, null, 2))}</pre></details>`;
}

async function refreshDetail() {
  if (!current) return;
  const e = await api(`/api/encounters/${current}`);
  if (e.error) return;
  $("#empty").hidden = true;
  $("#detail").hidden = false;
  $("#dTitle").textContent = `${e.patient.name}, ${e.patient.age_years}y ${e.patient.sex || ""}${e.patient.pregnant ? " · pregnant" : ""}`;
  $("#dSub").textContent = e.note;
  const st = $("#dStatus");
  st.textContent = { running: "agent running", needs_input: "waiting for you", handoff: "handed to clinician", done: "done", failed: "failed" }[e.status] + (e.degraded ? " · rules-only" : "");
  st.className = "badge " + (e.status === "running" ? "spin" : "");

  $("#question").hidden = e.status !== "needs_input";
  $("#qText").textContent = e.question || "";

  const p = e.plan || {};
  const plan = $("#plan");
  if (p.triage && e.status !== "running") {
    plan.hidden = false;
    plan.className = "plan " + p.triage;
    plan.innerHTML = `<div class="big">${p.triage}${e.handoffs.length ? " · referred to clinician" : ""}</div>
      <div>${esc(p.rationale)}</div>
      <ul>${(p.advice_steps || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
      ${p.advice_local_language ? `<div class="local"><b>${esc(e.language)}:</b> ${esc(p.advice_local_language)}</div>` : ""}
      ${p.followup_days ? `<div class="muted">Follow-up in ${p.followup_days} day(s)</div>` : ""}`;
  } else plan.hidden = true;

  $("#trace").innerHTML = e.steps.map((s) => {
    const bad = s.phase === "check" && s.detail && s.detail.ok === false ? " bad" : "";
    return `<li class="${s.phase}${bad}"><span class="ph">${s.phase}</span><span class="tool">${esc(s.tool || "")}</span>
      ${s.model ? `<span class="model">${esc(s.model)}</span>` : ""}${stepBody(s)}</li>`;
  }).join("") + (e.status === "running" ? `<li class="decide"><span class="ph spin">thinking</span></li>` : "");
}

async function refresh() {
  await Promise.all([refreshStatus(), refreshLists(), refreshDetail()]);
}

renderPresets();
if (REPLAY) {
  const b = document.createElement("div");
  b.className = "replay-banner";
  b.innerHTML = "▶ Replay of real runs recorded on a laptop with Wi-Fi off (Gemma 4 E4B via Ollama). Pick a visit on the left to watch the agent loop. <a href='https://github.com/SolomonLipson/Hackathon-strong'>Run it live →</a>";
  document.body.insertBefore(b, document.querySelector("main"));
  api("/api/encounters").then((v) => { if (v.length) { current = v[v.length - 1].id; refresh(); } });
}
refresh();
setInterval(refresh, REPLAY ? 900 : 1200);
