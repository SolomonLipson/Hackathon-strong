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
  { label: "👶 Child · cough", sub: "asks to count breaths", name: "Aarav", age_years: 2, sex: "M", language: "Telugu",
    note: "2 year old boy, cough and fever for 3 days, not eating well.", temp_c: 38.6 },
  { label: "🤰 Pregnant · headache", sub: "danger sign → instant referral", name: "Lakshmi", age_years: 24, sex: "F", pregnant: true, language: "Telugu",
    note: "7 months pregnant. Severe headache since morning and says her vision is blurred. Feet swollen." },
  { label: "🗣 Hinglish note", sub: "behosh, pee nahi rahi", name: "Riya", age_years: 4, sex: "F", language: "Hindi",
    note: "Bachchi ko 2 din se tez bukhar hai, aaj subah se behosh jaisi hai, kuch pee nahi rahi.", temp_c: 39.8 },
  { label: "🍼 Infant · not feeding", sub: "everyday words, RED signs", name: "Baby Anu", age_years: 0.7, sex: "F", language: "Telugu",
    note: "Baby has not taken breast milk since morning and is very sleepy, hard to wake up.", temp_c: 38.9 },
  { label: "🤒 Adult · mild fever", sub: "home care, GREEN", name: "Ramesh", age_years: 32, sex: "M", language: "Hindi",
    note: "Mild fever and body ache since yesterday, eating and drinking normally, no vomiting, no breathing problem.",
    temp_c: 38.1, hr: 92, rr: 18, spo2: 98 },
  { label: "❓ Vague + bad reading", sub: "re-measure, clarify", name: "Solomon", age_years: 22, sex: "M", language: "English",
    note: "Not sure, but low energy.", temp_c: 30, hr: 90, rr: 18, spo2: 98 },
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
    b.innerHTML = `${esc(p.label)}<small>${esc(p.sub)}</small>`;
    b.onclick = () => {
      const f = $("#visitForm");
      f.reset();
      for (const [k, v] of Object.entries(p)) {
        if (k === "label" || k === "sub") continue;
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

$("#noteMic").onclick = (e) => Voice.toggle(e.currentTarget, (text) => {
  const note = $("#visitForm").elements.note;
  note.value = (note.value ? note.value + " " : "") + text;
});
$("#answerMic").onclick = (e) => Voice.toggle(e.currentTarget, (text) => { $("#answerForm").elements.answer.value = text; });
window.speakAdvice = (btn) => Voice.speak(btn.dataset.text, btn.dataset.lang);

$("#modelBtn").onclick = async () => { await api("/api/model", { enabled: !modelEnabled }); refreshStatus(); };
$("#netBtn").onclick = async () => { await api("/api/network", { online: !online }); refreshStatus(); };

async function refreshStatus() {
  const s = await api("/api/status");
  modelEnabled = s.model.enabled;
  online = s.sync.online;
  const mp = $("#modelPill");
  const ready = s.model.runtime && s.model.enabled && (s.model.primary_ready || s.model.fallback_ready);
  mp.textContent = ready ? `● ${s.model.primary_ready ? s.model.primary : "gemma4:e2b-it-qat"} · local` : (s.model.enabled ? "● model not running · rules-only" : "● model killed · rules-only");
  mp.className = "pill " + (ready ? "ok" : "bad");
  $("#modelBtn").textContent = s.model.enabled ? "Kill model" : "Restore model";
  $("#netBtn").textContent = online ? "📶 Online · syncing" : "✈︎ No network";
  $("#netBtn").className = "pill toggle " + (online ? "ok" : "bad");
  $("#syncPill").textContent = `outbox: ${s.sync.pending} pending · ${s.sync.synced} synced`;
}

async function refreshLists() {
  const visits = await api("/api/encounters");
  $("#visits").innerHTML = visits.map((v) => `
    <li data-id="${v.id}" class="t-${v.triage || ""} ${v.id === current ? "active" : ""}">
      <span>${esc(v.name)} <span class="muted">${v.age_years ?? ""}y · ${new Date(v.created_at * 1000).toLocaleTimeString()}</span></span>
      <span class="badge ${v.triage || ""}">${esc(v.status === "running" ? "running…" : v.triage || v.status)}</span>
    </li>`).join("") || `<li class="muted">No visits yet</li>`;
  $("#visits").querySelectorAll("li[data-id]").forEach((li) => (li.onclick = () => { current = li.dataset.id; revealed[current] = 0; refresh(); }));

  const hs = await api("/api/handoffs");
  $("#handoffs").innerHTML = hs.map((h) => `
    <li class="t-${h.urgency}"><span><span class="badge ${h.urgency}">${h.urgency}</span> <b>${esc(h.name)}</b><div class="reason">${esc(h.reason)}</div></span>
    <button class="pill toggle" data-ack="${h.id}">Ack</button></li>`).join("") || `<li class="muted">No open handoffs</li>`;
  $("#handoffs").querySelectorAll("[data-ack]").forEach((b) => (b.onclick = async () => { await api(`/api/handoffs/${b.dataset.ack}/ack`, {}); refreshLists(); }));
}

function stepBody(st) {
  const d = st.detail || {};
  if (st.phase === "decide") return `<div class="body">${esc(d.thought)}</div>` + (Object.keys(d.args || {}).length ? `<details><summary>args</summary><pre>${esc(JSON.stringify(d.args, null, 2))}</pre></details>` : "");
  if (st.phase === "check" && st.tool === "check_danger_signs") return `<div class="body">protocol level <b class="badge ${d.level}">${d.level}</b> ${(d.flags || []).map((f) => esc(f.reason)).join("; ")}</div>`;
  if (st.phase === "check") return `<div class="body">${d.ok ? "✓ " : "✗ "}${esc(d.feedback || "approved")}</div>`;
  if (st.phase === "recover") return `<div class="body">${esc(d.message)}</div>`;
  if (st.phase === "sense" && st.tool === "worker_answer") return `<div class="body">Worker answered: <b>${esc(d.answer)}</b></div>`;
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

const VITALS = [["temp_c", "Temperature", "°C"], ["hr", "Pulse", "/min"], ["rr", "Breathing", "/min"], ["spo2", "SpO₂", "%"], ["sbp", "BP systolic", ""], ["dbp", "BP diastolic", ""]];
const FLAG_FIELD = { low_spo2: "spo2", infant_fever: "temp_c", hyperpyrexia: "temp_c", high_fever: "temp_c", hypothermia: "temp_c",
  low_temperature: "temp_c", fast_breathing: "rr", tachycardia: "hr", hypotension: "sbp", severe_pre_eclampsia_bp: "sbp",
  pregnancy_hypertension: "sbp", hypertensive_crisis: "sbp" };
const LABEL = (c) => c.replace(/_/g, " ");
const ACTION = { RED: "Refer now: emergency", YELLOW: "See a clinician within 24 h", GREEN: "Home care + follow-up" };

function renderPhases(e) {
  const last = e.steps[e.steps.length - 1];
  const now = e.status === "running" ? (last ? { sense: "decide", decide: "check", check: "act", act: "check", recover: "decide" }[last.phase] || "sense" : "sense") : null;
  const seen = new Set(e.steps.map((s) => s.phase));
  document.querySelectorAll("#phases li").forEach((li) => {
    li.className = (li.dataset.p === now ? "now " : "") + (seen.has(li.dataset.p) ? "done" : "");
  });
}

function renderFindings(e) {
  const f = e.findings || {};
  if (!f.summary && !(f.symptoms || []).length) { $("#findings").innerHTML = `<span class="muted spin">Gemma is reading the note</span>`; return; }
  const ev = Object.fromEntries((f.evidence || []).map((x) => [x.code, x.quote]));
  const grp = (lbl, html) => html ? `<div class="grp"><div class="lbl">${lbl}</div>${html}</div>` : "";
  $("#findings").innerHTML =
    grp("Danger signs", (f.danger_signs || []).map((c) => `<span class="chip danger">⚠ ${esc(LABEL(c))}</span>${ev[c] ? `<div class="evidence">“${esc(ev[c])}”</div>` : ""}`).join("")) +
    grp("Unclear: worth asking", (f.unclear_signs || []).map((c) => `<span class="chip unclear">? ${esc(LABEL(c))}</span>`).join("")) +
    grp("Symptoms", (f.symptoms || []).map((s) => `<span class="chip">${esc(s)}</span>`).join("")) +
    grp("Ruled out", (f.negated || []).map((s) => `<span class="chip neg">${esc(s)}</span>`).join("")) +
    grp("Duration", f.duration_days != null ? `<span class="chip">${f.duration_days < 1 ? "since today" : f.duration_days + " day(s)"}</span>` : "") +
    (f.summary ? `<div class="summary">${esc(f.summary)}</div>` : "") +
    `<div class="hint">read by ${esc(f.source || "")}</div>`;
}

function renderVitals(e) {
  const rules = [...e.steps].reverse().find((s) => s.tool === "check_danger_signs" && s.detail && s.detail.flags)?.detail;
  const flagged = {};
  (rules?.flags || []).forEach((fl) => {
    const k = FLAG_FIELD[fl.code] || (fl.code.startsWith("unreliable_") ? fl.code.slice(11) : null);
    if (k && (!flagged[k] || fl.level === "RED")) flagged[k] = fl.level;
  });
  const asked = new Set((e.answers || []).map((a) => a.field));
  const rows = VITALS.filter(([k]) => e.vitals[k] != null).map(([k, name, unit]) =>
    `<tr class="f-${flagged[k] || ""}"><td>${name}${asked.has(k) ? '<span class="src">from answer</span>' : ""}</td><td>${e.vitals[k]}${unit}</td></tr>`).join("");
  $("#vitals").innerHTML = (rows ? `<table class="vt">${rows}</table>` : `<div class="muted">No vitals measured yet</div>`) +
    (rules ? `<div class="flags"><b>Protocol minimum: <span class="badge ${rules.level}">${rules.level}</span></b>
      ${(rules.flags || []).map((fl) => `<div><span class="badge ${fl.level}">${fl.level}</span> ${esc(fl.reason)}</div>`).join("") || '<div class="muted">No danger flags</div>'}</div>` : "");
}

function renderStats(e) {
  const decides = e.steps.filter((s) => s.phase === "decide");
  const gemma = e.steps.filter((s) => s.model && s.model.startsWith("gemma")).length;
  const rejected = e.steps.filter((s) => s.phase === "check" && s.detail && s.detail.ok === false).length;
  const ts = e.steps.map((s) => s.ts).filter(Boolean);
  const secs = ts.length ? Math.round((e.status === "running" ? Date.now() / 1000 : Math.max(...ts)) - e.created_at) : 0;
  const stat = (v, k) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#stats").innerHTML = stat(gemma, "Gemma calls (on-device)") + stat(decides.length, "agent decisions") +
    stat(rejected, "unsafe proposals blocked") + stat(`${secs}s`, e.degraded ? "elapsed · rules-only mode" : "elapsed");
}

function renderPlan(e) {
  const p = e.plan || {};
  const plan = $("#plan");
  const key = JSON.stringify([p, e.handoffs.length, e.status]);
  if (!p.triage || e.status === "running") { plan.hidden = true; plan.dataset.key = ""; return; }
  if (plan.dataset.key === key) return; // unchanged: keep DOM (and any read-aloud) intact
  plan.dataset.key = key;
  plan.hidden = false;
  plan.className = "plan " + p.triage;
  const ref = e.handoffs[0];
  plan.innerHTML = `<div class="top"><span class="level ${p.triage}">${p.triage}</span><span class="what">${ACTION[p.triage]}</span>
      ${ref ? `<span class="badge RED">↗ referred to clinician</span>` : ""}</div>
    <div class="why">${esc(p.rationale)}</div>
    <ol>${(p.advice_steps || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
    ${p.advice_local_language ? `<div class="local"><div class="lhead"><span>For the family · ${esc(e.language)}</span>
      <button class="pill toggle speak" data-lang="${esc(e.language)}" data-text="${esc(p.advice_local_language)}" onclick="speakAdvice(this)">🔊 Read aloud</button></div>${esc(p.advice_local_language)}</div>` : ""}
    <div class="meta">${p.followup_days ? `<span>📅 Follow-up in ${p.followup_days} day(s)</span>` : ""}
      ${p.confidence != null ? `<span>🎯 Confidence ${Math.round(p.confidence * 100)}%</span>` : ""}
      ${ref ? `<span>🩺 Handoff reason: ${esc(ref.reason)}</span>` : ""}</div>`;
}

async function refreshDetail() {
  if (!current) return;
  const e = await api(`/api/encounters/${current}`);
  if (!e || e.error) return;
  $("#empty").hidden = true;
  $("#detail").hidden = false;
  $("#dAvatar").textContent = (e.patient.name || "?").trim()[0].toUpperCase();
  $("#dTitle").textContent = `${e.patient.name} · ${e.patient.age_years < 1 ? Math.round(e.patient.age_years * 12) + " months" : e.patient.age_years + " y"} ${e.patient.sex || ""}${e.patient.pregnant ? " · pregnant" : ""}`;
  $("#dSub").textContent = `“${e.note}”`;
  const st = $("#dStatus");
  st.textContent = { running: "agent working…", needs_input: "waiting for you", handoff: "handed to clinician", done: "completed", failed: "failed" }[e.status] + (e.degraded ? " · rules-only" : "");
  st.className = "status " + e.status;

  $("#question").hidden = e.status !== "needs_input";
  if ($("#qText").textContent !== (e.question || "")) $("#qText").textContent = e.question || "";

  renderPhases(e);
  renderPlan(e);
  renderFindings(e);
  renderVitals(e);
  renderStats(e);

  $("#traceCount").textContent = `(${e.steps.length} steps)`;
  $("#trace").innerHTML = e.steps.map((s) => {
    const bad = s.phase === "check" && s.detail && s.detail.ok === false ? " bad" : "";
    return `<li class="${s.phase}${bad}"><span class="ph">${s.phase}</span><span class="tool">${esc(s.tool || "")}</span>
      ${s.model ? `<span class="model">${esc(s.model)}</span>` : ""}${stepBody(s)}</li>`;
  }).join("") + (e.status === "running" ? `<li class="decide"><span class="ph spin">Gemma is thinking</span></li>` : "");
}

async function refresh() {
  await Promise.all([refreshStatus(), refreshLists(), refreshDetail()]);
}

renderPresets();
const hashId = location.hash.match(/^#v=(\w+)/);
if (hashId) current = hashId[1];
if (REPLAY) {
  const b = document.createElement("div");
  b.className = "replay-banner";
  b.innerHTML = "▶ Replay of real runs recorded on a laptop with Wi-Fi off (Gemma 4 E4B via Ollama). Pick a visit on the left to watch the agent loop. <a href='https://github.com/SolomonLipson/Hackathon-strong'>Run it live →</a>";
  document.body.insertBefore(b, document.querySelector("main"));
  if (!hashId) api("/api/encounters").then((v) => { if (v.length) { current = v[v.length - 1].id; refresh(); } });
}
refresh();
setInterval(refresh, REPLAY ? 900 : 1200);
