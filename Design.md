# Sahayak design

## Problem

An ASHA worker in rural Telangana sees a sick child at home. There is often no signal, the notes are messy and multilingual, and patient data is sensitive. The worker needs to decide quickly whether this child can stay home, needs the PHC within 24 hours, or must go to hospital now. A cloud chatbot fails on all three counts: connectivity, privacy, and being a single straight arrow from input to answer.

## Architecture

```
          ┌──────────── browser UI (static/, offline) ────────────┐
          │ note + vitals        answers          handoff ack    │
          └──────┬───────────────────┬──────────────────┬────────┘
                 ▼                   ▼                  ▼
          server.py (stdlib HTTP, localhost)
                 │
                 ▼
   ┌─────────── agent.py loop (per encounter, background thread) ───────────┐
   │  DECIDE  Gemma 4 E4B via Ollama → {thought, tool, args} (JSON schema)   │
   │     │    ↳ on failure: E2B → rules_planner (degraded mode)              │
   │  CHECK   checker.precheck → reject + feedback → next DECIDE             │
   │  ACT     tools.py (extract / rules / history / ask / plan / refer / fin)│
   │  CHECK   checker.postcheck → forced handoff (RED, low confidence)       │
   │  PERSIST store.py (SQLite): state + trace step                          │
   └───────────────┬───────────────────────────────┬─────────────────────────┘
                   ▼                               ▼
          handoffs table (human)         outbox table → sync.py → district server
```

## Features

### Agent loop ([agent.py](sahayak/agent.py))
- After the automatic SENSE phase, one tool per iteration is chosen by Gemma from a JSON-schema-constrained menu. The checker rejects repeated tools that make no progress, and the state includes an explicit `still_open` checklist, because small models otherwise repeat finished steps. Gemma sees a compact state view: note, vitals, findings, protocol flags, history, answers, plan, referral, step budget, and the checker's last feedback.
- The loop ends on `finish` (approved by the checker), on `ask_health_worker` (status `needs_input`), or when the budget runs out (`MAX_STEPS`). Running out of budget produces a forced clinician handoff, never a guess.
- **Degraded mode**: if Gemma is unreachable, returns invalid JSON after retries, or proposes rejected actions `MAX_CHECK_REJECTIONS` times, the loop switches to `rules_planner`, a deterministic policy that follows the same tools and checker.
- **Crash-safe resume**: every step is persisted. `load_state()` replays the trace to rebuild working memory (for example, whether rules were checked after the latest findings or vitals change). `resume_interrupted()` restarts visits that were `running` at startup.

### Sense ([agent.py](sahayak/agent.py) `_sense`, [tools.py](sahayak/tools.py) `extract_findings`)
- The SENSE phase runs automatically at the start and whenever new information arrives (a free-text answer clears findings, and a new vital makes the rules stale). It reads the note, loads history, and applies the rules without spending a model decision. A RED sign therefore opens the emergency referral within seconds.
- **Checklist extraction**: Gemma must give a yes/no/unclear verdict for each of the 15 danger signs, using plain-language definitions with local-language examples (`protocols.DANGER_SIGN_DEFS`), and must quote the evidence. It also lists present and ruled-out symptoms, duration, and vitals mentioned in the note. Unclear signs are shown to the planner as "worth asking about".
- **Grounding check**: a "yes" whose quoted evidence is not actually in the note is downgraded to unclear.
- **Negation-aware keyword net** (English, Hinglish, Devanagari, Telugu): it handles pre-negation ("no chest pain", "denies fever") and post-negation ("saans nahi phool rahi"). Its danger signs are always unioned in, so a model miss can't lower urgency.

### Voice ([static/voice.js](sahayak/static/voice.js), `llm.transcribe`)
- The browser records the mic and stops automatically about 1.5 s after the speaker goes quiet (energy-based voice-activity detection). It then resamples and encodes the clip as a 16 kHz mono PCM WAV. Gemma is asked for a clean, **strictly verbatim** transcript. It removes fillers and repetitions, is told the clip length and a word budget, and never adds anything that wasn't said. A transcript longer than physically possible for the clip is truncated. `/api/transcribe` sends it to Gemma 4 E4B's native audio input through Ollama and gets a verbatim transcript in the spoken language and script. The inline audio is stripped from the logs.
- It is used for both the visit note and answers to the agent's questions. The keyword safety net also understands Devanagari and Telugu script, because voice transcripts can arrive in native script.

### Measurement doubt and clarification ([protocols.py](sahayak/protocols.py))
- Readings outside physiological ranges (for example temperature outside 32–43 °C or SpO₂ below 50) are **not** used for triage. They raise a YELLOW "unreliable reading" flag, and the agent must ask for a re-measure before finishing.
- A vague note (at most one symptom, no danger sign, no duration) requires one clarifying question. The free-text answer triggers re-extraction.
- Temperature bands: RED below 35.0 °C (hypothermia) or at 41 °C and above (hyperpyrexia), YELLOW for 35.0–35.9 °C or 39.5 °C and above.

### Talk to Sahayak ([conversation.js](sahayak/static/conversation.js), [intake.py](sahayak/intake.py))
This is a hands-free, turn-taking voice dialogue held entirely in the chosen language (Telugu, Hindi, or English). Fixed conversational phrases are pre-translated, and patient-specific lines such as the agent's questions are translated on-device by Gemma. Sahayak speaks, then the mic opens and closes when the worker pauses, and Gemma transcribes. `/api/intake` has Gemma turn everything said so far into the visit form (name, age, sex, pregnancy, vitals, clinical note) The server then decides **by code** what is still missing (name, age, sex, complaint), so a visit never starts without the essentials. Each answer is sent together with the question it answers, so a bare "Solomon" is understood as the name. Sahayak asks for each missing item out loud (twice at most, then asks the worker to use the form), then runs the normal agent. It announces protocol findings and emergency referrals, speaks the agent's questions, and feeds spoken answers back. At the end it speaks the triage and reads the family's advice in their language. A small number-parsing backup catches plainly spoken ages that the model skips.

### Advice in the family's script
The checker rejects advice written in romanized "Tenglish" or "Hinglish". At least half the letters must be in the Telugu (U+0C00–0C7F) or Devanagari (U+0900–097F) block, so Gemma rewrites it. This is skipped in rules-only mode.

### Read-aloud ([tts.py](sahayak/tts.py))
Chrome often exposes no Indian-language voices, so speech is synthesized by the operating system (macOS `say`: Lekha for Hindi, Geeta for Telugu; espeak-ng on Linux) and streamed to the page as WAV at the voice's natural rate. The most natural installed variant is picked automatically (Premium > Enhanced > compact; Enhanced voices are a one-time download and then work offline). The browser's voices are only a fallback.

### Privacy
- Everything stays in one local SQLite file. Nothing is sent anywhere except the outbox.
- Outbox payloads are **de-identified**: patient id, age, sex, and pregnancy only. The name and village never leave the device.
- **Hide names** shows initials only, which is useful when a family member is looking at the screen. **Wipe data** deletes every table and `VACUUM`s the file so deleted rows don't linger on disk.

### Speed
- One fixed `num_ctx` and `keep_alive` for every call. Previously, switching between voice and triage calls made Ollama reload the model (about 4 s).
- The model is warmed up at startup. SENSE costs no decision calls, and a visit closes automatically once the checker has nothing left open.
- The result: a typical visit takes 2 Gemma calls and 16–20 s on an M5 laptop (about 36 tokens/s), and an emergency referral is visible after about 10 s.
- The UI redraws only panels whose data changed, and polls fast only while the agent is working.

### Evaluation ([eval/](eval))
19 labelled vignettes in English, Hinglish, and Telugu, run through the full agent with scripted worker answers, comparing Gemma against rules-only. The key metric is under-triage.

### Check ([checker.py](sahayak/checker.py), [protocols.py](sahayak/protocols.py))
- `protocols.evaluate`: IMCI-inspired thresholds for SpO₂, temperature (including infants under 2 months), age-specific breathing rate, heart rate, BP (pregnancy-specific), plus danger signs. It returns flags and a minimum level.
- `precheck`: unknown tools, empty questions, a question budget of 3, a triage below the protocol floor, and `finish` before prerequisites are met are all rejected with feedback the model sees next turn.
- `postcheck`: a RED protocol result opens an emergency referral immediately. Confidence below `HANDOFF_CONFIDENCE` forces a referral.

### Human handoff
Handoff happens when the rules say RED, the triage is YELLOW, confidence is low, the step budget runs out, or the agent crashes. Handoffs appear in the clinician queue in the UI and are queued for sync.

### Local state ([store.py](sahayak/store.py))
One SQLite file (WAL mode) holds patients, encounters, the step trace, handoffs, follow-ups, and the outbox.

### Sync ([sync.py](sahayak/sync.py))
Store-and-forward outbox with an idempotency key per record, exponential back-off (5 s to 300 s), and simulated connectivity for demos. `SAHAYAK_SYNC_URL` points it at a real server.

### UI ([static/](sahayak/static))
Vanilla JS that polls the API every 1.2 s. It shows a colour-coded live trace (decide, act, check, sense, recover), the agent's question box, the final plan with local-language advice, the handoff queue, and demo switches for the network and the model.

## Why these choices
- **Gemma 4 E4B plus E2B fallback**: E4B gives good multilingual extraction and tool choice on a laptop, while E2B keeps working on low-RAM devices.
- **JSON-schema outputs**: every model decision is machine-checkable, which is what makes a deterministic checker possible.
- **Rules as the floor, the model as the planner**: the model adds language understanding and judgment, and the rules guarantee it can only escalate. This is the right trust boundary for health.
- **Standard library only**: zero install friction on clinic hardware, and no network needed even to install.

## Limitations and next steps
- The protocol is a demo and not clinically validated. A real deployment needs clinician-reviewed IMCI/MCP rule sets.
- Packaging for Android via LiteRT / MediaPipe LLM Inference (Gemma 4 E2B on phone), and streaming voice.
- Encrypting the SQLite file at rest, and authenticated sync.
