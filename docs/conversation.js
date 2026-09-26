/*
 * "Talk to Sahayak": a hands-free, fully offline voice conversation with the agent,
 * held entirely in the chosen language (Telugu, Hindi or English).
 *
 *   1. Sahayak greets the worker and asks about the patient.
 *   2. The worker talks. Gemma 4 transcribes (audio in, on-device) and
 *      /api/intake turns the dialogue so far into a visit form. The server
 *      decides by code what is still missing and Sahayak asks for exactly
 *      that, one thing at a time: the complaint and age (essential, up to two
 *      tries), name and sex (asked once; any gender answer is accepted). It
 *      never dead-ends: after the tries it continues with what it has.
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
      greet: "Namaste! I am Sahayak, your health assistant. I work fully on this device, so everything you tell me stays private. Don't worry, we will go through this together. Please tell me about the patient: their name, their age, and what is troubling them.",
      ack: "I'm sorry to hear that. Thank you for telling me. Let me check this carefully.",
      noage: "That's okay, I will continue without the age.",
      got: (f) => `Got it${f.name ? ", " + f.name : ""}${f.age_years != null ? ", " + f.age_years + " years" : ""}.`,
      name: "What is the patient's name?", age: "How old is the patient?", sex: "Is the patient female, male, or other?",
      complaint: "What is the problem, and since when?", again: "Sorry, I did not catch that. Please say it again.",
      fill: "Please fill this on the form on the screen.", thanks: "Thank you. I am checking the patient now.",
      red: "Danger sign found. I have already opened an emergency referral.", yellow: "Warning: this patient must see a doctor within 24 hours.",
      type: "Please type the answer on the screen.", handoff: "The doctor has been informed. The visit is saved.", done: "The visit is saved. Take care.",
      RED: "Red. Take the patient to hospital now.", YELLOW: "Yellow. See a doctor within 24 hours.", GREEN: "Green. Care at home, with follow-up.",
    },
    Telugu: {
      greet: "నమస్తే! నేను సహాయక్, మీ ఆరోగ్య సహాయకుడిని. నేను పూర్తిగా ఈ పరికరంలోనే పనిచేస్తాను, కాబట్టి మీరు చెప్పేది అంతా గోప్యంగా ఉంటుంది. కంగారు పడకండి, మనం కలిసి చూద్దాం. రోగి గురించి చెప్పండి: పేరు, వయస్సు, మరియు ఏమి ఇబ్బంది ఉంది.",
      ack: "అయ్యో, అది విని బాధగా ఉంది. చెప్పినందుకు ధన్యవాదాలు. నేను జాగ్రత్తగా పరిశీలిస్తాను.",
      noage: "పర్వాలేదు, వయస్సు లేకుండానే కొనసాగిస్తాను.",
      got: (f) => `సరే${f.name ? ", " + f.name : ""}${f.age_years != null ? ", " + f.age_years + " ఏళ్లు" : ""}.`,
      name: "రోగి పేరు ఏమిటి?", age: "రోగి వయస్సు ఎంత?", sex: "రోగి ఆడవారా, మగవారా, లేక ఇతరులా?",
      complaint: "రోగికి ఏమి సమస్య ఉంది, ఎప్పటి నుండి?", again: "క్షమించండి, నాకు సరిగా వినిపించలేదు. మళ్ళీ చెప్పండి.",
      fill: "దయచేసి ఈ వివరం స్క్రీన్ మీద ఫారమ్‌లో నింపండి.", thanks: "ధన్యవాదాలు. ఇప్పుడు రోగిని పరిశీలిస్తున్నాను.",
      red: "ప్రమాద సంకేతం కనిపించింది. నేను అత్యవసర రిఫరల్ తెరిచాను.", yellow: "జాగ్రత్త: ఈ రోగిని 24 గంటల్లో డాక్టర్ చూడాలి.",
      type: "దయచేసి సమాధానం స్క్రీన్ మీద టైప్ చేయండి.", handoff: "డాక్టర్‌కు సమాచారం పంపాను. సందర్శన సేవ్ అయింది.", done: "సందర్శన సేవ్ అయింది. జాగ్రత్తగా ఉండండి.",
      RED: "ఎరుపు. రోగిని వెంటనే ఆసుపత్రికి తీసుకెళ్లండి.", YELLOW: "పసుపు. 24 గంటల్లో డాక్టర్‌ను చూడాలి.", GREEN: "ఆకుపచ్చ. ఇంట్లోనే సంరక్షణ, తర్వాత మళ్ళీ చూడాలి.",
    },
    Hindi: {
      greet: "नमस्ते! मैं सहायक हूँ, आपका स्वास्थ्य सहायक। मैं पूरी तरह इसी डिवाइस पर काम करता हूँ, इसलिए आपकी हर बात गोपनीय रहेगी। घबराइए मत, हम साथ मिलकर देखेंगे। मरीज़ के बारे में बताइए: नाम, उम्र, और क्या तकलीफ़ है।",
      ack: "यह सुनकर दुख हुआ। बताने के लिए धन्यवाद। मैं ध्यान से जाँच करता हूँ।",
      noage: "कोई बात नहीं, मैं उम्र के बिना आगे बढ़ता हूँ।",
      got: (f) => `ठीक है${f.name ? ", " + f.name : ""}${f.age_years != null ? ", " + f.age_years + " साल" : ""}.`,
      name: "मरीज़ का नाम क्या है?", age: "मरीज़ की उम्र कितनी है?", sex: "मरीज़ महिला है, पुरुष है, या अन्य?",
      complaint: "मरीज़ को क्या तकलीफ़ है, और कब से?", again: "माफ़ कीजिए, मैं सुन नहीं पाया। फिर से बताइए।",
      fill: "कृपया यह जानकारी स्क्रीन के फ़ॉर्म में भरें।", thanks: "धन्यवाद। अब मैं मरीज़ की जाँच कर रहा हूँ।",
      red: "ख़तरे का संकेत मिला है। मैंने आपातकालीन रेफ़रल खोल दिया है।", yellow: "सावधान: इस मरीज़ को 24 घंटे में डॉक्टर को दिखाना होगा।",
      type: "कृपया जवाब स्क्रीन पर टाइप करें।", handoff: "डॉक्टर को सूचना भेज दी है। विज़िट सेव हो गई।", done: "विज़िट सेव हो गई। ध्यान रखिए।",
      RED: "लाल। मरीज़ को तुरंत अस्पताल ले जाएँ।", YELLOW: "पीला। 24 घंटे में डॉक्टर को दिखाएँ।", GREEN: "हरा। घर पर देखभाल, फिर जाँच।",
    },
  };

  const lang = () => $("#convoLang").value;
  const T = (key, arg) => { const v = (PHRASES[lang()] || PHRASES.English)[key]; return typeof v === "function" ? v(arg) : v; };

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

  /**
   * Steps 1-2: collect the patient's details by voice. Only the age and the
   * complaint are essential; name and sex are asked once. Never dead-ends:
   * after two tries for a field it continues with what it has.
   */
  async function intake() {
    const dialogue = [];
    const tries = {};
    let question = T("greet");
    let form = null;
    let acknowledged = false;
    const complaintSaid = [];
    const grounded = (note, said) => {
      const words = note.toLowerCase().match(/[\p{L}\p{M}\p{N}]{3,}/gu) || [];
      const hay = said.toLowerCase();
      return words.length > 0 && words.filter((w) => hay.includes(w)).length / words.length >= 0.4;
    };
    while (on) {
      const heard = await ask(question);
      if (heard && (question === T("greet") || question === T("complaint"))) complaintSaid.push(heard);
      if (heard) {
        dialogue.push(`Sahayak: ${question}`, `Worker: ${heard}`);
        setState("thinking");
        form = await api("/api/intake", { transcript: dialogue.join("\n") });
        if (form.error) { await agentSays(form.error); return null; }
        // The complaint must come from what was said about the illness, not from a name/age/sex answer.
        if (form.clinical_note && !grounded(form.clinical_note, complaintSaid.join(" "))) form.clinical_note = "";
        form.missing = form.clinical_note ? (form.missing || []).filter((f) => f !== "complaint") : [...new Set([...(form.missing || []), "complaint"])];
        const known = [form.name, form.age_years != null ? `${form.age_years} y` : null, { F: "female", M: "male", O: "other" }[form.sex],
          form.pregnant ? "pregnant" : null, ...Object.entries(form.vitals || {}).map(([k, v]) => `${k} ${v}`)].filter(Boolean).join(" · ");
        bubble("form", `📝 ${known || "–"}${form.clinical_note ? " · " + form.clinical_note : ""}`);
        if (!acknowledged && form.clinical_note) { acknowledged = true; await agentSays(T("ack")); }
      }
      const optional = { name: 1, sex: 1 };  // asked at most once
      const open = (form?.missing || ["age", "complaint"]).filter((f) => (tries[f] || 0) < (optional[f] ? 1 : 2));
      const next = ["complaint", "age", "name", "sex"].find((f) => open.includes(f));
      if (!next) {
        if (!form) return null;
        if (form.age_years == null) await agentSays(T("noage"));
        if (!form.clinical_note) form.clinical_note = dialogue.filter((l) => l.startsWith("Worker:")).map((l) => l.slice(8)).join(". ");
        if (form.name || form.age_years != null) await agentSays(T("got", form));
        return form;
      }
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
        patient: { name: form.name || "Patient", age_years: form.age_years, sex: form.sex || null, pregnant: !!form.pregnant },
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
