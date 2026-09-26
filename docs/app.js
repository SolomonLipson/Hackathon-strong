/*
 * Sahayak front-end. Vanilla JS, no dependencies, no network beyond localhost.
 *
 * Polls the local API to render the agent loop live. Every panel is redrawn
 * only when its data actually changed (keyed by a JSON fingerprint), and a
 * finished visit is not re-rendered, so the screen never flickers. New trace
 * steps are appended with a short fade; old ones are left alone.
 *
 * Also: voice input/answers (voice.js), on-device read-aloud, privacy
 * controls (hide names, wipe all data) and the demo switches (simulate
 * offline network, simulate model crash).
 */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let current = null;
let modelEnabled = true;
let online = false;
const keys = {};                 // panel name -> fingerprint of what is on screen
let renderedSteps = { id: null, n: 0 };
let hideNames = (() => { try { return localStorage.getItem("sahayak.hideNames") === "1"; } catch { return false; } })();

/** Redraw a panel only if its data changed. */
function changed(name, data) {
  const k = JSON.stringify(data);
  if (keys[name] === k) return false;
  keys[name] = k;
  return true;
}

/** Display name respecting privacy mode (initials only). */
const shownName = (name) => (hideNames ? (name || "?").trim().split(/\s+/).map((w) => w[0].toUpperCase() + ".").join("") : name);

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
    return { ...e, steps: e.steps.slice(0, n), status: n < e.steps.length ? "running" : e.status };
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

// ---- Actions ----------------------------------------------------------------

$("#visitForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const vitals = {};
  ["temp_c", "hr", "rr", "spo2", "sbp", "dbp"].forEach((k) => { if (f[k].value !== "") vitals[k] = f[k].value; });
  const btn = $("#visitForm button[type=submit]");
  btn.disabled = true;
  const { id } = await api("/api/encounters", {
    patient: { name: f.name.value, age_years: parseFloat(f.age_years.value), sex: f.sex.value, pregnant: f.pregnant.checked },
    note: f.note.value, vitals, language: f.language.value,
  });
  btn.disabled = false;
  if (id) { e.target.reset(); select(id); }
};

$("#answerForm").onsubmit = async (e) => {
  e.preventDefault();
  const input = e.target.elements.answer;
  const btn = e.target.querySelector("button.primary");
  btn.disabled = true;
  await api(`/api/encounters/${current}/answer`, { answer: input.value });
  input.value = "";
  btn.disabled = false;
  keys.detail = null;
  tick();
};

$("#noteMic").onclick = (e) => Voice.toggle(e.currentTarget, (text) => {
  const note = $("#visitForm").elements.note;
  note.value = (note.value ? note.value + " " : "") + text;
});
$("#answerMic").onclick = (e) => Voice.toggle(e.currentTarget, (text) => { $("#answerForm").elements.answer.value = text; });
window.speakAdvice = (btn) => Voice.speak(btn.dataset.text, btn.dataset.lang, btn);

$("#modelBtn").onclick = async () => { await api("/api/model", { enabled: !modelEnabled }); refreshStatus(); };
$("#netBtn").onclick = async () => { await api("/api/network", { online: !online }); refreshStatus(); };
$("#privacyBtn").onclick = () => {
  hideNames = !hideNames;
  try { localStorage.setItem("sahayak.hideNames", hideNames ? "1" : "0"); } catch { /* storage unavailable */ }
  Object.keys(keys).forEach((k) => delete keys[k]);
  renderedSteps = { id: null, n: 0 };
  tick();
};
$("#wipeBtn").onclick = async () => {
  if (!confirm("Permanently delete ALL patients, visits, traces and queued records on this device?")) return;
  await api("/api/wipe", {});
  current = null;
  history.replaceState(null, "", location.pathname);
  Object.keys(keys).forEach((k) => delete keys[k]);
  $("#detail").hidden = true;
  $("#empty").hidden = false;
  tick();
};

function select(id) {
  current = id;
  revealed[id] = 0;
  keys.detail = null;
  renderedSteps = { id: null, n: 0 };
  history.replaceState(null, "", `#v=${id}`);
  tick();
}

// ---- Status bar & lists -------------------------------------------------------

async function refreshStatus() {
  const s = await api("/api/status");
  modelEnabled = s.model.enabled;
  online = s.sync.online;
  if (!changed("status", [s, hideNames])) return;
  const ready = s.model.runtime && s.model.enabled && (s.model.primary_ready || s.model.fallback_ready);
  const mp = $("#modelPill");
  mp.textContent = ready ? `● ${s.model.primary_ready ? s.model.primary : "gemma4:e2b-it-qat"} · local` : (s.model.enabled ? "● model not running · rules-only" : "● model killed · rules-only");
  mp.className = "pill " + (ready ? "ok" : "bad");
  $("#modelBtn").textContent = s.model.enabled ? "Kill model" : "Restore model";
  $("#netBtn").textContent = online ? "📶 Online · syncing" : "✈︎ No network";
  $("#netBtn").className = "pill toggle " + (online ? "ok" : "bad");
  $("#syncPill").textContent = `outbox: ${s.sync.pending} pending · ${s.sync.synced} synced`;
  $("#privacyBtn").textContent = hideNames ? "🙈 Names hidden" : "👁 Names shown";
}

async function refreshLists() {
  const visits = await api("/api/encounters");
  if (changed("visits", [visits, current, hideNames])) {
    $("#visits").innerHTML = visits.map((v) => `
      <li data-id="${v.id}" class="t-${v.triage || ""} ${v.id === current ? "active" : ""}">
        <span>${esc(shownName(v.name))} <span class="muted">${v.age_years ?? ""}y · ${new Date(v.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></span>
        <span class="badge ${v.triage || ""}">${esc(v.status === "running" ? "working…" : v.status === "needs_input" ? "question" : v.triage || v.status)}</span>
      </li>`).join("") || `<li class="muted">No visits yet</li>`;
    $("#visits").querySelectorAll("li[data-id]").forEach((li) => (li.onclick = () => select(li.dataset.id)));
  }
  const hs = await api("/api/handoffs");
  if (changed("handoffs", [hs, hideNames])) {
    $("#handoffs").innerHTML = hs.map((h) => `
      <li class="t-${h.urgency}"><span><span class="badge ${h.urgency}">${h.urgency}</span> <b>${esc(shownName(h.name))}</b><div class="reason">${esc(h.reason)}</div></span>
      <button class="pill toggle" data-ack="${h.id}">Ack</button></li>`).join("") || `<li class="muted">No open handoffs</li>`;
    $("#handoffs").querySelectorAll("[data-ack]").forEach((b) => (b.onclick = async () => { await api(`/api/handoffs/${b.dataset.ack}/ack`, {}); refreshLists(); }));
  }
  return visits;
}

// ---- Visit detail ---------------------------------------------------------------

const VITALS = [["temp_c", "Temperature", "°C"], ["hr", "Pulse", "/min"], ["rr", "Breathing", "/min"], ["spo2", "SpO₂", "%"], ["sbp", "BP systolic", ""], ["dbp", "BP diastolic", ""]];
const FLAG_FIELD = { low_spo2: "spo2", infant_fever: "temp_c", hyperpyrexia: "temp_c", high_fever: "temp_c", hypothermia: "temp_c",
  low_temperature: "temp_c", fast_breathing: "rr", tachycardia: "hr", hypotension: "sbp", severe_pre_eclampsia_bp: "sbp",
  pregnancy_hypertension: "sbp", hypertensive_crisis: "sbp" };
const LABEL = (c) => c.replace(/_/g, " ");
const ACTION = { RED: "Refer now: emergency", YELLOW: "See a clinician within 24 h", GREEN: "Home care + follow-up" };
const lastRules = (e) => [...e.steps].reverse().find((s) => s.tool === "check_danger_signs" && s.detail && s.detail.flags)?.detail;

function stepBody(st) {
  const d = st.detail || {};
  if (st.phase === "decide") return `<div class="body">${esc(d.thought)}</div>` + (Object.keys(d.args || {}).length ? `<details><summary>args</summary><pre>${esc(JSON.stringify(d.args, null, 2))}</pre></details>` : "");
  if (st.phase === "sense" && st.tool === "worker_answer") return `<div class="body">Worker answered: <b>${esc(d.answer)}</b></div>`;
  if (st.phase === "check" && st.tool === "check_danger_signs") return `<div class="body">protocol level <b class="badge ${d.level}">${d.level}</b> ${(d.flags || []).map((f) => esc(f.reason)).join("; ")}</div>`;
  if (st.phase === "check") return `<div class="body">${d.ok ? "✓ " : "✗ "}${esc(d.feedback || "approved")}</div>`;
  if (st.phase === "recover") return `<div class="body">${esc(d.message)}</div>`;
  let summary = "";
  if (st.tool === "extract_findings") summary = `${(d.findings?.symptoms || []).join(", ")}${d.findings?.danger_signs?.length ? " · danger: " + d.findings.danger_signs.map(LABEL).join(", ") : ""}`;
  if (st.tool === "check_danger_signs") summary = `protocol level <b class="badge ${d.level}">${d.level}</b>`;
  if (st.tool === "get_patient_history") summary = `${(d.previous_visits || []).length} previous visit(s)`;
  if (st.tool === "ask_health_worker") summary = `Asked: “${esc(d.asked)}”`;
  if (st.tool === "recommend_care") summary = `Plan: <b class="badge ${d.plan?.triage}">${d.plan?.triage}</b> confidence ${d.plan?.confidence ?? "–"}`;
  if (st.tool === "refer_to_clinician") summary = `Referral opened (${esc(d.referral?.urgency)}): ${esc(d.referral?.reason)}`;
  if (st.tool === "finish") summary = esc(d.summary);
  return `<div class="body">${summary}</div><details><summary>result</summary><pre>${esc(JSON.stringify(d, null, 2))}</pre></details>`;
}

function renderPhases(e) {
  const last = e.steps[e.steps.length - 1];
  const now = e.status === "running" ? (last ? { sense: "decide", decide: "check", check: "decide", act: "check", recover: "decide" }[last.phase] || "sense" : "sense") : null;
  const seen = new Set(e.steps.map((s) => s.phase));
  document.querySelectorAll("#phases li").forEach((li) => {
    li.className = (li.dataset.p === now ? "now " : "") + (seen.has(li.dataset.p) ? "done" : "");
  });
}

function renderPlan(e) {
  const p = e.plan || {};
  const plan = $("#plan");
  const rules = lastRules(e);
  const ref = e.handoffs[0];
  if (e.status === "running" || e.status === "needs_input" || !p.triage) {
    // Progressive result: show what is already known while Gemma keeps working.
    if (!rules) { plan.hidden = true; return; }
    plan.hidden = false;
    plan.className = "plan pending " + rules.level;
    plan.innerHTML = `<div class="top"><span class="level ${rules.level}">${rules.level}</span><span class="what">Protocol minimum so far</span>
        ${ref ? `<span class="badge RED">↗ emergency referral already opened</span>` : ""}</div>
      <div class="why">${(rules.flags || []).map((f) => esc(f.reason)).join(" · ") || "No danger signs found so far."}</div>
      <div class="muted">${e.status === "needs_input" ? "⏸ Waiting for your answer" : "⋯ Gemma is writing the care plan"}</div>`;
    return;
  }
  plan.hidden = false;
  plan.className = "plan " + p.triage;
  plan.innerHTML = `<div class="top"><span class="level ${p.triage}">${p.triage}</span><span class="what">${ACTION[p.triage]}</span>
      ${ref ? `<span class="badge RED">↗ referred to clinician</span>` : ""}</div>
    <div class="why">${esc(p.rationale)}</div>
    <ol>${(p.advice_steps || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
    ${p.advice_local_language ? `<div class="local"><div class="lhead"><span>For the family · ${esc(e.language)}</span>
      <button class="pill toggle speak" data-lang="${esc(e.language)}" data-text="${esc(p.advice_local_language)}" onclick="speakAdvice(this)">🔊 Read aloud</button></div>${esc(p.advice_local_language)}</div>` : ""}
    <div class="meta">${p.followup_days ? `<span>📅 Follow-up in ${p.followup_days} day(s)</span>` : ""}
      ${p.confidence != null ? `<span>🎯 Confidence ${Math.round(p.confidence * 100)}%</span>` : ""}
      ${ref ? `<span>🩺 Handoff: ${esc(ref.reason)}</span>` : ""}</div>`;
}

function renderFindings(e) {
  const f = e.findings || {};
  if (!f.summary && !(f.symptoms || []).length) { $("#findings").innerHTML = `<span class="muted">⋯ Gemma is reading the note</span>`; return; }
  const ev = Object.fromEntries((f.evidence || []).map((x) => [x.code, x.quote]));
  const grp = (lbl, html) => html ? `<div class="grp"><div class="lbl">${lbl}</div>${html}</div>` : "";
  $("#findings").innerHTML =
    grp("Danger signs", (f.danger_signs || []).map((c) => `<span class="chip danger">⚠ ${esc(LABEL(c))}</span>${ev[c] ? `<div class="evidence">“${esc(ev[c])}”</div>` : ""}`).join("")) +
    grp("Unclear: worth asking", (f.unclear_signs || []).map((c) => `<span class="chip unclear">? ${esc(LABEL(c))}</span>`).join("")) +
    grp("Symptoms", (f.symptoms || []).map((s) => `<span class="chip">${esc(s)}</span>`).join("")) +
    grp("Ruled out", (f.negated || []).map((s) => `<span class="chip neg">${esc(LABEL(s))}</span>`).join("")) +
    grp("Duration", f.duration_days != null ? `<span class="chip">${f.duration_days < 1 ? "since today" : f.duration_days + " day(s)"}</span>` : "") +
    (f.summary ? `<div class="summary">${esc(f.summary)}</div>` : "") +
    `<div class="hint">read by ${esc(f.source || "")}</div>`;
}

function renderVitals(e) {
  const rules = lastRules(e);
  const flagged = {};
  (rules?.flags || []).forEach((fl) => {
    const k = FLAG_FIELD[fl.code] || (fl.code.startsWith("unreliable_") ? fl.code.slice(11) : null);
    if (k && (!flagged[k] || fl.level === "RED")) flagged[k] = fl.level;
  });
  const asked = new Set((e.answers || []).map((a) => a.field));
  const rows = VITALS.filter(([k]) => e.vitals[k] != null).map(([k, name, unit]) =>
    `<tr class="f-${flagged[k] || ""}"><td>${name}${asked.has(k) ? '<span class="src">from answer</span>' : ""}</td><td>${e.vitals[k]}${unit}</td></tr>`).join("");
  $("#vitals").innerHTML = (rows ? `<table class="vt">${rows}</table>` : `<div class="muted">No vitals measured</div>`) +
    (rules ? `<div class="flags"><b>Protocol minimum: <span class="badge ${rules.level}">${rules.level}</span></b>
      ${(rules.flags || []).map((fl) => `<div><span class="badge ${fl.level}">${fl.level}</span> ${esc(fl.reason)}</div>`).join("") || '<div class="muted">No danger flags</div>'}</div>` : "");
}

function renderStats(e) {
  const decides = e.steps.filter((s) => s.phase === "decide").length;
  const gemma = e.steps.filter((s) => s.model && s.model.startsWith("gemma")).length;
  const rejected = e.steps.filter((s) => s.phase === "check" && s.detail && s.detail.ok === false).length;
  const ts = e.steps.map((s) => s.ts).filter(Boolean);
  const secs = ts.length ? Math.round(Math.max(...ts) - e.created_at) : 0;
  const stat = (v, k) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#stats").innerHTML = stat(gemma, "Gemma calls (on-device)") + stat(decides, "agent decisions") +
    stat(rejected, "unsafe proposals blocked") + stat(`${secs}s`, e.degraded ? "agent time · rules-only" : "agent time");
}

function renderTrace(e) {
  const box = $("#trace");
  const fresh = renderedSteps.id !== e.id || renderedSteps.n > e.steps.length;
  if (fresh) { box.innerHTML = ""; renderedSteps = { id: e.id, n: 0 }; }
  box.querySelector(".thinking")?.remove();
  e.steps.slice(renderedSteps.n).forEach((s) => {
    const li = document.createElement("li");
    const bad = s.phase === "check" && s.detail && s.detail.ok === false ? " bad" : "";
    li.className = `${s.phase}${bad}${fresh ? "" : " new"}`;
    li.innerHTML = `<span class="ph">${s.phase}</span><span class="tool">${esc(s.tool || "")}</span>
      ${s.model ? `<span class="model">${esc(s.model)}</span>` : ""}${stepBody(s)}`;
    box.appendChild(li);
  });
  renderedSteps.n = e.steps.length;
  if (e.status === "running") box.insertAdjacentHTML("beforeend", `<li class="decide thinking"><span class="ph">⋯ Gemma is thinking</span></li>`);
  $("#traceCount").textContent = `(${e.steps.length} steps)`;
}

async function refreshDetail() {
  if (!current) return null;
  const e = await api(`/api/encounters/${current}`);
  if (!e || e.error) return null;
  if (!changed("detail", [e.status, e.steps.length, e.question, e.plan, e.findings, e.vitals, e.handoffs, hideNames])) return e;
  $("#empty").hidden = true;
  $("#detail").hidden = false;
  $("#dAvatar").textContent = (shownName(e.patient.name) || "?").trim()[0].toUpperCase();
  $("#dTitle").textContent = `${shownName(e.patient.name)} · ${e.patient.age_years < 1 ? Math.round(e.patient.age_years * 12) + " months" : e.patient.age_years + " y"} ${e.patient.sex || ""}${e.patient.pregnant ? " · pregnant" : ""}`;
  $("#dSub").textContent = `“${e.note}”`;
  const st = $("#dStatus");
  st.textContent = { running: "agent working…", needs_input: "waiting for you", handoff: "handed to clinician", done: "completed", failed: "failed" }[e.status] + (e.degraded ? " · rules-only" : "");
  st.className = "status " + e.status;
  $("#question").hidden = e.status !== "needs_input";
  $("#qText").textContent = e.question || "";
  if (e.status === "needs_input") $("#answerForm").elements.answer.focus();
  renderPhases(e);
  renderPlan(e);
  renderFindings(e);
  renderVitals(e);
  renderStats(e);
  renderTrace(e);
  return e;
}

// ---- Polling: fast while the agent works, slow when idle ------------------------

let timer = null;
async function tick() {
  clearTimeout(timer);
  let busy = false;
  try {
    const [, visits, e] = await Promise.all([refreshStatus(), refreshLists(), refreshDetail()]);
    busy = (e && e.status === "running") || (visits || []).some((v) => v.status === "running");
  } catch (err) { console.error(err); }
  timer = setTimeout(tick, busy || REPLAY ? 700 : 3000);
}

const hashId = location.hash.match(/^#v=(\w+)/);
if (hashId) current = hashId[1];
if (REPLAY) {
  const b = document.createElement("div");
  b.className = "replay-banner";
  b.innerHTML = "▶ Replay of real runs recorded on a laptop with Wi-Fi off (Gemma 4 E4B via Ollama). Pick a visit on the left to watch the agent loop. <a href='https://github.com/SolomonLipson/Hackathon-strong'>Run it live →</a>";
  document.body.insertBefore(b, document.querySelector("main"));
  if (!hashId) api("/api/encounters").then((v) => { if (v.length) select(v[v.length - 1].id); });
}
tick();
