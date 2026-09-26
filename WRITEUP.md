# Sahayak: an offline, voice-first triage agent for village health workers on Gemma 4

**Subtitle:** A sense-decide-act-check agent that runs 100% on-device on Gemma 4 E4B/E2B. It listens to the health worker, understands English, Hindi, Telugu, and Hinglish, quotes its evidence, asks when unsure, can only escalate triage, hands off to clinicians, and keeps working when the model or the network dies.

**Track:** Problem Statement 5: Best Use of Gemma 4 (Local-First Agents)

## The problem

India's ~1 million ASHA community health workers are the first point of contact for rural families. At a doorstep they must decide in minutes whether a sick child can stay home, must see the PHC within 24 hours, or needs an ambulance now. A cloud chatbot fails them three ways: villages have **patchy connectivity**, pregnancy status and symptoms are **private**, and a single model call from note to answer is a **"straight arrow"** that fails silently. Triage needs verification, questions, and a clear line where a human takes over.

## What we built

Sahayak is a local web app: Python standard library, SQLite, and Gemma 4 through Ollama on `localhost`. There is no pip install and no CDN, and it works with Wi-Fi off. The worker **speaks or types** a note in any language (*"Bachchi ko 2 din se tez bukhar hai, behosh jaisi hai, kuch pee nahi rahi"*), adds any vitals they measured, and watches the agent work.

**Gemma 4 does four jobs, all on-device:**

1. **Listens.** Gemma 4 E4B's native audio input turns the worker's speech into a *clean* transcript in about 2–3 s ("umm, the child has, uh, fever for, aah, three days, three days" becomes "The child has fever for 3 days"), with no cloud speech API and no separate ASR model. Recording stops by itself when the speaker pauses.
2. **Understands.** It turns messy multilingual notes into structured findings with quoted evidence.
3. **Plans.** It chooses the agent's next action.
4. **Counsels.** It writes advice in Telugu or Hindi, which the operating system's offline voices read aloud.

E2B is the low-memory fallback.

**Talk to Sahayak** makes the whole visit a spoken conversation. The agent greets the worker, Gemma turns their description into the visit form, and it asks aloud for anything missing. It then speaks findings, emergency referrals, and questions, takes spoken answers, and reads the family's advice in their language (enforced native script, not romanized).

### The agent loop

- **SENSE (automatic whenever the picture is stale).** Gemma reads the note, plus any answers, through a **per-sign checklist**: for each of 15 danger signs it must answer yes, no, or unclear, using plain-language definitions with local-language examples, and must quote the words that support each "yes". A **negation-aware keyword net** in English, Hinglish, Devanagari, and Telugu runs alongside and is unioned in, so a model miss can never lower urgency while "no chest pain" or "saans nahi phool rahi" never raises it. Local history is loaded and the deterministic danger-sign rules run. A RED result opens an emergency referral *within seconds, before any model reasoning*.
- **DECIDE.** Gemma sees a compact JSON state (findings, unclear signs, protocol flags, answers, plan, referral, a `still_open` checklist, and the checker's last feedback) and returns `{thought, tool, args}` under a JSON schema: `ask_health_worker`, `recommend_care`, `refer_to_clinician`, `finish`, or a re-run of a sensing tool.
- **CHECK (pre-action).** A deterministic verifier rejects unsafe or no-progress proposals and feeds the reason back. **Triage can only go up**: a plan below the protocol level is rejected. `finish` is refused until critical vitals have been asked for, a plan exists, and YELLOW or RED visits have a referral.
- **ACT**, then **CHECK (post-action)**: confidence below 0.55 forces a clinician handoff.
- **PERSIST**: state and a trace step go to SQLite after every step.

### Human handoff boundaries

The agent never guesses:

- **Missing vitals.** For a coughing child under five with no breathing rate, it asks the worker to count breaths for a minute and pauses (`needs_input`).
- **Implausible readings.** If a vital can't be real (30 °C in a talking patient), it asks for a re-measure instead of triggering an emergency.
- **Vague notes.** "Not sure, low energy" gets a clarifying question, and the answer is re-read by Gemma.

It **hands off to a clinician** for RED, YELLOW, low confidence, repeated rejections, or an exhausted 12-step budget, with a specific reason. Every question and answer works by voice.

### Offline error recovery and local state

- **Fallback chain.** If E4B fails after a retry, the agent tries E2B. If that fails, it switches to a **rules-only planner** that uses the same tools and checker. The trace shows a `RECOVER` step and the visit still completes. A **Kill model** button demonstrates this live.
- **Crash-safe resume.** After `kill -9`, visits are rebuilt by replaying the step trace. That includes knowing whether the rules ran *after* the latest vitals change, and it resumes mid-visit.
- **Store-and-forward sync.** Visits and referrals queue in a local outbox with idempotency keys and exponential back-off, and drain when signal returns.
- **Privacy.** Synced records are de-identified (names never leave the device). Names can be masked on screen, and one click wipes all data. Every Gemma call is logged locally for audit.
- **Speed.** A fixed context size and keep-alive (no model reloads), a startup warm-up, automatic sensing, and auto-close when nothing is left open. A visit takes 2 Gemma calls and 16–20 s, and an emergency referral is visible after about 10 s.

## Evaluation

We built 19 labelled vignettes covering infants, pregnancy, adults, self-harm, TB-like cough, English, Hinglish, and Telugu (`eval/run_eval.py`). Each one runs through the full agent with scripted worker answers, comparing **Gemma 4 E4B** against **rules-only** (keyword net plus deterministic planner). The safety-critical metric is **under-triage**.

| Configuration | Exact triage | Under-triage (unsafe) | Over-triage | Questions asked | Median time/visit |
|---|---|---|---|---|---|
| Rules-only (keyword net + deterministic planner) | 89% (17/19) | **2** | 0 | 5 | <1 s |
| **Gemma 4 E4B agent** | **95% (18/19)** | **0** | 1 | 4 | 31 s |

The one Gemma "miss" is a deliberate escalation. For a 3-week cough with blood in the sputum and weight loss, the rules said GREEN, our label said YELLOW, and Gemma chose RED ("haemoptysis ... requires immediate clinical evaluation despite the protocol suggesting GREEN"). That is the planner using its right to escalate above the rules.

These vignettes were also our development set. The first Gemma version missed the infant case, then over-triaged an ordinary Telugu headache and a child who was "not eating well", and each failure led to a fix described below. So read this as "no known unsafe failures on 19 cases", not as a clinical accuracy claim. The per-case table is in `eval/results.md`.

The rules-only misses are exactly the cases that need clinical language understanding: the "lower chest pulling in" description of chest indrawing, and a 3-week cough with blood and weight loss, which has no keyword rule but should never be sent home. Gemma catches both, and may escalate above the rules.

## Why these technical choices

- **Gemma 4 E4B/E2B QAT.** These edge models fit on a clinic laptop yet handle Hinglish notes, native-script speech, and Telugu counselling.
- **Checklist extraction with evidence.** Forcing a verdict per sign, with definitions and quotes, fixed the misses of "list the danger signs" and makes each finding auditable.
- **Model as planner, rules as the floor.** Gemma contributes language, judgment, and counselling (it may escalate above the rules), while auditable rules guarantee the minimum. This is the right trust boundary for health.

## Challenges we overcame

- **The model reading everyday language.** Our first extraction missed "has not taken breast milk, very sleepy, hard to wake up" (two RED signs). Checklist extraction plus the negation-aware net fixed it. Later runs over-triaged an ordinary Telugu headache (తలనొప్పి) as "severe headache" and "not eating well" as "unable to feed". Sharper definitions (for example "eating less is NOT this sign") and a grounding check (quoted evidence must really be in the note) fixed both, and each got its own eval case.
- **Small models repeating themselves.** E4B once called `extract_findings` 12 times in a row. We now sense automatically, reject repeated tools that make no progress, and give an explicit `still_open` list. Visits dropped from about 7 Gemma decisions to 2, and from about 50 s to 16–20 s.
- **Trusting bad inputs.** A user test entered 30 °C and got an emergency referral. Plausibility ranges now trigger a re-measure. We also corrected temperature bands (hypothermia below 35.0 °C, hyperpyrexia at 41 °C and above).

## Limitations and next steps

The protocol is a hackathon demo and is not clinically validated. The next steps are clinician-reviewed IMCI/maternal rule sets, Android packaging with LiteRT (E2B on a phone), streaming voice, encryption at rest, and authenticated sync to the state HMIS.

**Code:** https://github.com/SolomonLipson/Hackathon-strong
**Live demo (replay of real on-device runs, no login):** https://solomonlipson.github.io/Hackathon-strong/
