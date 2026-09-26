/*
 * "Talk to Sahayak": a hands-free, fully offline voice conversation with the agent,
 * held entirely in the chosen language (Telugu, Hindi or English).
 *
 *   1. Sahayak greets the worker and asks about the patient.
 *   2. The worker talks. Gemma 4 transcribes (audio in, on-device) and
 *      /api/intake turns the dialogue so far into a visit form. The server
 *      decides by code what is still missing (name, age, sex, complaint) and
 *      Sahayak asks for exactly that, one thing at a time. A visit never starts
 *      without age, sex and a complaint; after two failed tries Sahayak asks
 *      the worker to fill that field on screen instead.
 *   3. The normal sense-decide-act-check agent runs. Sahayak announces an
 *      emergency referral or warning, speaks the agent's questions (translated
 *      on-device by Gemma when needed) and sends the spoken answers back.
 *   4. It says the triage and reads the family's advice in their language.
 *
 * Fixed conversational phrases are pre-translated below; everything patient-
 * specific comes from Gemma. Uses Voice (voice.js) and api/select/lastRules
 * (app.js). No network beyond localhost.
 */

const Convo = (() => {
  let on = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const PHRASES = {
    English: {
      greet: "Hello, I am Sahayak. Tell me about the patient: their name, age, and what is wrong.",
      name: "What is the patient's name?", age: "How old is the patient?", sex: "Is the patient female or male?",
      complaint: "What is the problem, and since when?", again: "Sorry, I did not catch that. Please say it again.",
      fill: "Please fill this on the form on the screen.", thanks: "Thank you. I am checking the patient now.",
      red: "Danger sign found. I have already opened an emergency referral.", yellow: "Warning: this patient must see a doctor within 24 hours.",
      type: "Please type the answer on the screen.", handoff: "The doctor has been informed. The visit is saved.", done: "The visit is saved. Take care.",
      RED: "Red. Take the patient to hospital now.", YELLOW: "Yellow. See a doctor within 24 hours.", GREEN: "Green. Care at home, with follow-up.",
    },
    Telugu: {
      greet: "నమస్తే, నేను సహాయక్. రోగి గురించి చెప్పండి: పేరు, వయస్సు, మరియు ఏమి సమస్య.",
      name: "రోగి పేరు ఏమిటి?", age: "రోగి వయస్సు ఎంత?", sex: "రోగి ఆడవారా, మగవారా?",
      complaint: "రోగికి ఏమి సమస్య ఉంది, ఎప్పటి నుండి?", again: "క్షమించండి, నాకు సరిగా వినిపించలేదు. మళ్ళీ చెప్పండి.",
      fill: "దయచేసి ఈ వివరం స్క్రీన్ మీద ఫారమ్‌లో నింపండి.", thanks: "ధన్యవాదాలు. ఇప్పుడు రోగిని పరిశీలిస్తున్నాను.",
      red: "ప్రమాద సంకేతం కనిపించింది. నేను అత్యవసర రిఫరల్ తెరిచాను.", yellow: "జాగ్రత్త: ఈ రోగిని 24 గంటల్లో డాక్టర్ చూడాలి.",
      type: "దయచేసి సమాధానం స్క్రీన్ మీద టైప్ చేయండి.", handoff: "డాక్టర్‌కు సమాచారం పంపాను. సందర్శన సేవ్ అయింది.", done: "సందర్శన సేవ్ అయింది. జాగ్రత్తగా ఉండండి.",
      RED: "ఎరుపు. రోగిని వెంటనే ఆసుపత్రికి తీసుకెళ్లండి.", YELLOW: "పసుపు. 24 గంటల్లో డాక్టర్‌ను చూడాలి.", GREEN: "ఆకుపచ్చ. ఇంట్లోనే సంరక్షణ, తర్వాత మళ్ళీ చూడాలి.",
    },
    Hindi: {
      greet: "नमस्ते, मैं सहायक हूँ। मरीज़ के बारे में बताइए: नाम, उम्र, और क्या तकलीफ़ है।",
      name: "मरीज़ का नाम क्या है?", age: "मरीज़ की उम्र कितनी है?", sex: "मरीज़ महिला है या पुरुष?",
      complaint: "मरीज़ को क्या तकलीफ़ है, और कब से?", again: "माफ़ कीजिए, मैं सुन नहीं पाया। फिर से बताइए।",
      fill: "कृपया यह जानकारी स्क्रीन के फ़ॉर्म में भरें।", thanks: "धन्यवाद। अब मैं मरीज़ की जाँच कर रहा हूँ।",
      red: "ख़तरे का संकेत मिला है। मैंने आपातकालीन रेफ़रल खोल दिया है।", yellow: "सावधान: इस मरीज़ को 24 घंटे में डॉक्टर को दिखाना होगा।",
      type: "कृपया जवाब स्क्रीन पर टाइप करें।", handoff: "डॉक्टर को सूचना भेज दी है। विज़िट सेव हो गई।", done: "विज़िट सेव हो गई। ध्यान रखिए।",
      RED: "लाल। मरीज़ को तुरंत अस्पताल ले जाएँ।", YELLOW: "पीला। 24 घंटे में डॉक्टर को दिखाएँ।", GREEN: "हरा। घर पर देखभाल, फिर जाँच।",
    },
  };

  const lang = () => $("#convoLang").value;
  const T = (key) => (PHRASES[lang()] || PHRASES.English)[key];

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

  /** Sahayak speaks a line in the conversation language. */
  async function agentSays(text) {
    if (!on || !text) return;
    bubble("agent", text);
    setState("speaking");
    await Voice.say(text, lang());
  }

  /** One spoken turn from the worker (cleaned transcript, or "" if silent). */
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
    let heard = await workerSays();
    if (!heard && on) { await agentSays(T("again")); heard = await workerSays(); }
    return heard;
  }

  /** Translate an agent line (e.g. its question) into the conversation language on-device. */
  async function localize(text) {
    if (lang() === "English" || !text) return text;
    setState("thinking");
    const r = await api("/api/translate", { text, language: lang() });
    return r.text || text;
  }

  /** Steps 1-2: collect name, age, sex and complaint by voice. */
  async function intake() {
    const dialogue = [];
    const tries = {};
    let question = T("greet");
    let form = null;
    while (on) {
      const heard = await ask(question);
      if (heard) {
        dialogue.push(`Sahayak: ${question}`, `Worker: ${heard}`);
        setState("thinking");
        form = await api("/api/intake", { transcript: dialogue.join("\n") });
        if (form.error) { await agentSays(form.error); return null; }
        const known = [form.name, form.age_years != null ? `${form.age_years} y` : null, form.sex, form.pregnant ? "pregnant" : null,
          ...Object.entries(form.vitals || {}).map(([k, v]) => `${k} ${v}`)].filter(Boolean).join(" · ");
        bubble("form", `📝 ${known || "–"}${form.clinical_note ? " · " + form.clinical_note : ""}`);
      }
      const missing = (form?.missing || ["name", "age", "sex", "complaint"]).filter((f) => (tries[f] || 0) < 2);
      if (form && !missing.some((f) => f !== "name")) {
        const stillNeeded = (form.missing || []).filter((f) => f !== "name");
        if (!stillNeeded.length) return form;                     // everything essential collected
        await agentSays(T("fill"));                                // gave up on a field by voice
        return null;
      }
      const next = missing[0] || "complaint";
      tries[next] = (tries[next] || 0) + 1;
      question = T(next);
    }
    return null;
  }

  /** Steps 3-4: run the visit, speaking progress, questions and the result. */
  async function follow(id) {
    let lastLevel = null, lastQuestion = null;
    while (on) {
      await sleep(700);
      const e = await api(`/api/encounters/${id}`);
      const rules = lastRules(e);
      if (rules && rules.level !== lastLevel) {
        lastLevel = rules.level;
        if (rules.level === "RED") await agentSays(T("red"));
        else if (rules.level === "YELLOW") await agentSays(T("yellow"));
      }
      if (e.status === "needs_input" && e.question !== lastQuestion) {
        lastQuestion = e.question;
        const answer = await ask(await localize(e.question));
        if (answer) { setState("thinking"); await api(`/api/encounters/${id}/answer`, { answer }); }
        else await agentSays(T("type"));
        continue;
      }
      if (e.status === "done" || e.status === "handoff") {
        const p = e.plan || {};
        await agentSays(T(p.triage) || p.triage);
        await agentSays(lang() === "English" ? (p.advice_steps || []).join(" ") : p.advice_local_language || await localize((p.advice_steps || []).join(" ")));
        await agentSays(T(e.status === "handoff" ? "handoff" : "done"));
        return;
      }
      if (e.status === "failed") { await agentSays(T("handoff")); return; }
      setState("thinking");
    }
  }

  async function start() {
    if (on) return;
    on = true;
    $("#convoLog").innerHTML = "";
    $("#convoLangBadge").textContent = lang();
    $("#convo").hidden = false;
    try {
      const form = await intake();
      if (!form || !on) return;
      await agentSays(T("thanks"));
      const { id } = await api("/api/encounters", {
        patient: { name: form.name || "Patient", age_years: form.age_years, sex: form.sex, pregnant: !!form.pregnant },
        note: form.clinical_note, vitals: form.vitals || {}, language: lang(),
      });
      select(id);
      await follow(id);
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
  $("#convoEnd").onclick = () => { stop(); $("#convo").hidden = true; };
  return { start, stop };
})();
