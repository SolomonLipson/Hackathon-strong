/*
 * "Talk to Sahayak": a hands-free, fully offline voice conversation with the agent.
 *
 * The health worker keeps their hands on the patient and just talks:
 *   1. Sahayak greets them out loud and asks about the patient.
 *   2. The worker describes the patient in free speech. Gemma 4 transcribes it
 *      (audio in, on-device) and /api/intake turns everything said so far into
 *      a visit form (name, age, sex, pregnancy, vitals, clinical note). If
 *      something essential is missing, Sahayak asks for it out loud.
 *   3. The visit starts; the normal sense-decide-act-check agent runs. Sahayak
 *      announces what the protocol found (e.g. an emergency referral), speaks
 *      the agent's questions, and sends the worker's spoken answers back.
 *   4. At the end it says the triage and plan, then reads the family's advice
 *      in Telugu / Hindi with the device's offline voice.
 *
 * Turn-taking: the mic opens right after Sahayak finishes speaking and closes
 * when the worker pauses. Uses Voice (voice.js) and the page's api/select/
 * lastRules helpers (app.js). No network beyond localhost.
 */

const Convo = (() => {
  let on = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const panel = () => $("#convo");

  function bubble(who, text, cls = "") {
    const div = document.createElement("div");
    div.className = `msg ${who} ${cls}`;
    div.textContent = text;
    $("#convoLog").appendChild(div);
    $("#convoLog").scrollTop = $("#convoLog").scrollHeight;
  }

  function setState(state) {
    const labels = { speaking: "🔊 Sahayak is speaking", listening: "🎙 Listening: speak now, pause when done",
      transcribing: "⋯ Gemma is understanding you", thinking: "⋯ Gemma is thinking", idle: "Conversation ended" };
    $("#convoState").textContent = labels[state] || state;
    $("#convoOrb").className = "orb " + state;
  }

  /** Sahayak says something (English to the worker unless a language is given). */
  async function agentSays(text, language = "English") {
    if (!on || !text) return;
    bubble("agent", text);
    setState("speaking");
    await Voice.say(text, language);
  }

  /** Listen for one spoken turn from the worker; returns cleaned text or "". */
  async function workerSays() {
    if (!on) return "";
    try {
      const text = await Voice.listen((s) => on && setState(s));
      if (text) bubble("you", text);
      return text;
    } catch (err) {
      bubble("agent", "Microphone problem: " + err.message, "err");
      stop();
      return "";
    }
  }

  async function ask(question) {
    await agentSays(question);
    return workerSays();
  }

  /** Steps 1-2: collect the patient's details by voice. */
  async function intake() {
    const said = [];
    let heard = await ask("Hello, I am Sahayak. Tell me about the patient: their name, age, and what is wrong.");
    for (let turn = 0; turn < 5 && on; turn++) {
      if (!heard) { heard = await ask("I did not catch that. Please describe the patient."); continue; }
      said.push(heard);
      setState("thinking");
      const form = await api("/api/intake", { transcript: said.join("\n") });
      if (form.error) { await agentSays(form.error); return null; }
      const known = [form.name, form.age_years != null ? `${form.age_years} years` : null, form.sex, form.pregnant ? "pregnant" : null,
        ...Object.entries(form.vitals || {}).map(([k, v]) => `${k} ${v}`)].filter(Boolean).join(" · ");
      bubble("form", `📝 ${known || "…"}${form.clinical_note ? " · " + form.clinical_note : ""}`);
      const missing = form.missing || [];
      if (!missing.length || (turn >= 3 && form.age_years != null)) return form;
      heard = await ask(form.next_question || (missing.includes("age") ? "How old is the patient?" : "What is the problem?"));
    }
    return null;
  }

  /** Steps 3-4: run the visit, speaking progress, questions and the result. */
  async function follow(id, language) {
    let lastLevel = null, lastQuestion = null;
    while (on) {
      await sleep(800);
      const e = await api(`/api/encounters/${id}`);
      const rules = lastRules(e);
      if (rules && rules.level !== lastLevel) {
        lastLevel = rules.level;
        const reasons = (rules.flags || []).filter((f) => f.level === rules.level).map((f) => f.reason).join(", ");
        if (rules.level === "RED") await agentSays(`Danger sign: ${reasons}. I have already opened an emergency referral.`);
        else if (rules.level === "YELLOW") await agentSays(`Warning: ${reasons}.`);
      }
      if (e.status === "needs_input" && e.question !== lastQuestion) {
        lastQuestion = e.question;
        const answer = await ask(e.question);
        if (answer) { setState("thinking"); await api(`/api/encounters/${id}/answer`, { answer }); }
        else await agentSays("Please type the answer on the screen when you have it.");
        continue;
      }
      if (e.status === "done" || e.status === "handoff") {
        const p = e.plan || {};
        await agentSays(`Triage ${p.triage}. ${ACTION[p.triage] || ""}. ${(p.advice_steps || []).join(" ")}`);
        if (p.advice_local_language && language !== "English") {
          await agentSays(`Now the advice for the family in ${language}.`);
          await agentSays(p.advice_local_language, language);
        }
        await agentSays(e.status === "handoff" ? "The clinician has been notified. Visit saved." : "Visit saved. Take care.");
        return;
      }
      if (e.status === "failed") { await agentSays("Something went wrong. The visit has been handed to a clinician."); return; }
      if (e.status === "running") setState("thinking");
    }
  }

  async function start() {
    if (on) return;
    on = true;
    $("#convoLog").innerHTML = "";
    panel().hidden = false;
    const language = $("#convoLang").value;
    try {
      const form = await intake();
      if (!form || !on) return;
      await agentSays("Thank you. Let me check this patient.");
      const { id } = await api("/api/encounters", {
        patient: { name: form.name || "Patient", age_years: form.age_years, sex: form.sex || "F", pregnant: !!form.pregnant },
        note: form.clinical_note, vitals: form.vitals || {}, language,
      });
      select(id);
      await follow(id, language);
    } finally {
      on = false;
      setState("idle");
    }
  }

  function stop() {
    on = false;
    Voice.stopListening();
    Voice.stopSpeaking();
    setState("idle");
  }

  $("#convoBtn").onclick = start;
  $("#convoEnd").onclick = () => { stop(); panel().hidden = true; };
  return { start, stop };
})();
