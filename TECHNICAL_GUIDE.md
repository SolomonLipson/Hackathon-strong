# Sahayak: technical guide

This is a complete explanation of how Sahayak works, for presenting to judges. It covers the stack, how Gemma is configured, every flow, the offline keyword matching, the numbers, the limits, and likely questions.

---

## 1. The one-minute pitch

Sahayak is an **offline triage agent** for community health workers (ASHAs) in villages with poor connectivity. The worker types (or speaks) a note in English, Hindi, Telugu, or Hinglish and adds any vitals they measured. An **agent loop running entirely on the laptop** on **Gemma 4** then:

- **senses:** reads the note and quotes its evidence,
- **decides:** Gemma picks the next action,
- **acts:** runs a tool, such as asking a question or writing a care plan,
- **checks:** deterministic safety rules verify every step.

It ends with GREEN (home care), YELLOW (clinic within 24 h), or RED (refer now). It gives advice in the family's own script, hands off to a clinician when needed, and keeps working if the model crashes or the network is down.

It answers Problem Statement 5 directly:

| The brief asks for | What Sahayak has |
|---|---|
| "Full sense-decide-act-check loop" | `agent.py`: automatic SENSE, then Gemma DECIDE, tool ACT, deterministic CHECK, repeated up to 12 steps |
| "100% offline on Gemma 4" | Gemma 4 E4B + E2B through Ollama on `localhost`; no cloud calls anywhere |
| "Local state management" | One SQLite file: patients, visits, full step trace, handoffs, follow-ups, sync outbox |
| "Offline error recovery" | E4B → E2B → rules-only fallback, crash-safe resume from the trace, store-and-forward sync |
| "Clear boundaries for human handoff" | Asks the worker for missing info, and refers to a clinician on RED/YELLOW, low confidence, repeated unsafe proposals, or budget exhaustion |

---

## 2. Technology stack

| Layer | What we used | Why |
|---|---|---|
| Language (backend) | **Python 3**, standard library only (`http.server`, `sqlite3`, `urllib`, `threading`, `re`, `json`, `subprocess`) | Zero `pip install`, so it runs on any clinic laptop and even installing needs no network |
| Web server | `ThreadingHTTPServer` with a small JSON API (`server.py`) | Tiny, dependency-free, binds to `127.0.0.1` only |
| Database | **SQLite** in WAL mode (`store.py`) | A single file, crash-safe, no server |
| Frontend | **Vanilla HTML + CSS + JavaScript**, no framework, no CDN | Works offline; the page loads nothing from the internet |
| Browser audio | `MediaRecorder`, Web Audio `AnalyserNode` (voice-activity detection), `OfflineAudioContext` (resampling) | Records and prepares speech locally |
| Model runtime | **Ollama 0.34** (llama.cpp engine, Metal GPU on Apple Silicon) on `localhost:11434` | The standard way to run Gemma on-device, with JSON-schema structured outputs and audio input |
| Main model | **Gemma 4 E4B, QAT** (`gemma4:e4b-it-qat`, 4-bit, about 3 GB in memory) | Best reasoning and multilingual quality that fits a 16 GB laptop |
| Small model | **Gemma 4 E2B, QAT** (`gemma4:e2b-it-qat`) | Fast conversational chores (about 1.5 s) and low-memory fallback |
| Speech output | **Piper** neural TTS (ONNX voices: Telugu *padmavathi*, Hindi *priyamvada*), macOS `say` as fallback | Natural-sounding offline Indian-language voices |
| Tests and evaluation | `unittest` (12 tests), `eval/run_eval.py` (19 labelled clinical cases) | Evidence that the loop and safety rules work |
| Hosting of the demo | **GitHub Pages** replay of real recorded runs (`docs/`) | The app is offline by design, so the public demo replays genuine traces |

Code size: about 2,850 lines of application code across 16 files (Python backend plus JavaScript frontend).

---

## 3. Architecture

```
┌──────────────────────────── Browser (offline page) ────────────────────────────┐
│  app.js (visit form, live trace, results)   voice.js (mic, read-aloud)           │
│  conversation.js ("Talk to Sahayak" spoken dialogue)                            │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │ JSON over http://127.0.0.1:8765  (never leaves the laptop)
┌───────────────▼──────────────── server.py (Python) ────────────────────────────┐
│  /api/encounters  /api/transcribe  /api/intake  /api/translate  /api/speak ...  │
└──┬──────────────┬──────────────┬──────────────┬──────────────┬─────────────────┘
   │              │              │              │              │
┌──▼───────┐ ┌────▼─────┐ ┌──────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
│ agent.py │ │ llm.py   │ │ protocols.py│ │ store.py  │ │ tts.py      │
│ the loop │ │ Gemma    │ │ rules +     │ │ SQLite    │ │ Piper/say   │
│          │ │ client   │ │ keyword net │ │           │ │             │
└──┬───────┘ └────┬─────┘ └─────────────┘ └─────┬─────┘ └─────────────┘
   │ tools.py     │ HTTP localhost:11434        │ outbox
   │ checker.py   ▼                              ▼
   │        ┌─────────────┐               ┌───────────┐
   │        │ Ollama      │               │ sync.py   │ → district server when online
   │        │ Gemma 4 E4B │               └───────────┘
   │        │ Gemma 4 E2B │
   │        └─────────────┘
```

**Files, one per feature:**

| File | Responsibility |
|---|---|
| `config.py` | Every setting in one place: model names, context size, limits, voices, paths |
| `llm.py` | Talks to Gemma. Structured JSON calls, the fallback chain, warm-up, transcription, translation |
| `agent.py` | The sense-decide-act-check loop, the rules-only fallback planner, crash-safe resume |
| `tools.py` | The agent's actions: extract findings, check danger signs, history, ask, recommend, refer, finish |
| `checker.py` | The safety verifier that approves or rejects every proposed action |
| `protocols.py` | Deterministic clinical rules, danger-sign definitions, the offline keyword net |
| `store.py` | The SQLite schema and data access |
| `sync.py` | Store-and-forward outbox with back-off and idempotency keys |
| `intake.py` | Voice mode: turns a spoken description into a visit form |
| `tts.py` | Offline read-aloud |
| `patients.py` | Returning patients and follow-ups (post-submission branch, see §13) |
| `server.py` | HTTP API and static files |
| `static/*` | The UI |

---

## 4. How Gemma is set up and called

### 4.1 Installation (one time, then offline)
```bash
brew install ollama && ollama serve
ollama pull gemma4:e4b-it-qat     # 6.2 GB download
ollama pull gemma4:e2b-it-qat     # 4.3 GB download
```
We chose the **QAT (quantization-aware trained)** builds. The model was trained to be run at 4-bit precision, so it keeps quality while being small enough for a laptop. They are also smaller to download than the standard builds (6.2 GB against 9.6 GB).

### 4.2 Every call goes to Ollama's `/api/chat` on localhost with these settings (`llm.py`, `config.py`)

| Setting | Value | Why |
|---|---|---|
| `format` | a **JSON schema** | **Structured outputs**: Ollama constrains decoding with a grammar, so Gemma can only emit JSON matching our schema. This makes decisions machine-checkable. |
| `think` | `false` | No hidden "thinking" tokens, which is faster |
| `temperature` | 0.1 (0 for transcription) | Near-deterministic, reproducible behaviour |
| `num_ctx` | 4096, the **same for every call** | A different context size forces Ollama to reload the model (about 4 s). Keeping it fixed removed those reloads. |
| `num_predict` | 700 | A cap on output length |
| `keep_alive` | 30 min | Gemma stays in GPU memory between calls |
| warm-up | at server start, both models are loaded | The first visit isn't slow |
| retries | 1 retry per model, then the next model | Handles a timeout or invalid JSON |

**Fallback chain:** Gemma 4 E4B → Gemma 4 E2B → `LLMUnavailable` → the agent switches to its **rules-only planner**. A visit is never lost because the model died.

### 4.3 The jobs Gemma does

| # | Job | Model | Input → output |
|---|---|---|---|
| 1 | **Read the note (SENSE)** | E4B | note + patient → a checklist verdict for all 15 danger signs, evidence quotes, symptoms, denied symptoms, duration, vitals in the text, summary |
| 2 | **Decide the next step (DECIDE)** | E4B | a compact JSON "state view" → `{thought, tool, args}` |
| 3 | **Write the care plan** | E4B | through the `recommend_care` args: triage, 2–5 advice steps, advice **in the family's script**, follow-up days, confidence, rationale |
| 4 | **Transcribe speech** | E4B | a 16 kHz WAV clip → a clean verbatim transcript (audio passed through Ollama's multimodal input) |
| 5 | **Voice intake** | E2B (fast) | the spoken dialogue so far → a visit form (name, age, sex, pregnancy, vitals, complaint) |
| 6 | **Translate a spoken line** | E2B (fast) | the agent's English question → Telugu or Hindi script |

### 4.4 How Gemma "understands" what we say
- Gemma models are **pretrained on heavily multilingual data** (Google cites 140+ languages for the Gemma family), which includes Hindi, Telugu, and romanized Hinglish. The E2B/E4B edge models also accept **audio input natively**; Ollama lists the `audio` capability. We don't train or fine-tune anything. We use the instruction-tuned model and control it through the prompt and schema.
- **Understanding is steered, not trusted blindly:**
  1. **Checklist prompting.** Instead of "list the danger signs", Gemma must give a verdict for *each* of 15 signs, and each has a plain-language definition with Hindi and Telugu examples (`protocols.DANGER_SIGN_DEFS`). Small models miss everyday phrasing ("has not taken breast milk, hard to wake up") when given only code names. Forcing a verdict per sign fixed that.
  2. **Evidence quotes plus a grounding check.** For every "yes", Gemma must quote the words from the note. If fewer than half the quote's words actually occur in the note, the "yes" is downgraded to "unclear". This catches hallucinated evidence.
  3. **Sharp negative definitions.** For example, "Eating less / poor appetite is NOT *unable to feed*", and "an ordinary headache is only a symptom".
  4. **JSON schema.** The output shape is guaranteed, so the code can verify it.

---

## 5. The agent loop, step by step (`agent.py`)

```
Visit created
   │
   ▼
┌─ SENSE (automatic whenever information is stale) ─────────────────────────────┐
│ 1. extract_findings   Gemma checklist + evidence  ∪  offline keyword net      │
│ 2. get_patient_history  previous visits from SQLite                           │
│ 3. check_danger_signs   deterministic rules → minimum level + flags           │
│    └─ if RED: open an EMERGENCY REFERRAL immediately (before any Gemma step)  │
└───────────────────────────────────────────────────────────────────────────────┘
   │
   ▼  loop, at most 12 steps
┌─ DECIDE ─ Gemma sees the state view → picks ONE tool + args (JSON schema) ────┐
│   the state view includes: findings, unclear signs, protocol flags, history,  │
│   answers, plan, referral, a "still_open" checklist, last checker feedback    │
└───────────────────────────────────────────────────────────────────────────────┘
   │
   ▼
┌─ CHECK (before acting) ─ checker.precheck ────────────────────────────────────┐
│  rejected? → the reason goes back to Gemma as feedback → DECIDE again         │
│  3 rejections in a row → stop trusting the model → rules-only planner         │
└───────────────────────────────────────────────────────────────────────────────┘
   │ approved
   ▼
┌─ ACT ─ run the tool (ask_health_worker / recommend_care / refer / finish) ────┐
└───────────────────────────────────────────────────────────────────────────────┘
   │
   ▼
┌─ CHECK (after acting) ─ checker.postcheck ────────────────────────────────────┐
│  confidence < 0.55 → forced clinician handoff                                 │
│  nothing left open → the visit closes automatically (saves a Gemma call)      │
└───────────────────────────────────────────────────────────────────────────────┘
   │
   ▼
PERSIST: state + a trace step written to SQLite after every step
   │
   ├── ask_health_worker → status "needs_input" → pause until the worker answers
   │      a numeric answer fills the vital (e.g. breathing rate); free text → re-SENSE
   └── finish → status "done", or "handoff" if referred → follow-up scheduled → outbox
```

**The checker's rules** (`checker.py`), which the model cannot override:

| Rule | Effect |
|---|---|
| **Triage can only go up** | A plan below the protocol's minimum level is rejected ("protocol requires RED: SpO₂ 88% < 90%") |
| `finish` prerequisites | Refused until findings exist, the rules have been checked, critical missing vitals have been asked for, a plan exists, and YELLOW/RED visits have a referral |
| No-progress guard | Re-running a tool whose inputs haven't changed is rejected, with the list of what is still open. This fixed Gemma repeating a step 12 times. |
| Native script | For Telugu or Hindi families, advice written in English letters is rejected, and Gemma rewrites it in Telugu or Devanagari script |
| Question budget | At most 3 questions per visit |
| Low confidence | Below 0.55 → forced clinician handoff |
| RED protocol | → emergency referral opened immediately |

**The model versus the rules:** Gemma is the **planner and the language brain**: it reads, decides what to ask, writes the advice, and may *escalate* above the rules. The rules are the **safety floor**: auditable and deterministic, and they can only raise urgency. This is our trust boundary for health.

---

## 6. The offline "keyword net": how it works without internet (`protocols.py`)

The keyword net is **plain code in the repository**: 20 regular-expression patterns for danger signs and 12 for symptoms. Python's `re` module runs them locally. Nothing is fetched. It exists for two reasons:

1. **A safety net under Gemma.** Its danger signs are always *added* to Gemma's, so a model miss can never lower urgency.
2. **Degraded mode.** If the model is down, it *is* the reader.

It covers four ways of writing:

| Sign | English | Hinglish | Hindi (Devanagari) | Telugu |
|---|---|---|---|---|
| unconscious / lethargic | unconscious, hard to wake, very sleepy | behosh, uth nahi | बेहोश | స్పృహ లేదు |
| convulsions | seizure, fits | jhatke, daura | दौरा, झटके | మూర్ఛ |
| unable to drink or feed | not able to drink, not taken breast milk | pee nahi, doodh nahi | पी नहीं | పాలు తాగడం లేదు |
| difficulty breathing | breathless, short of breath | saans | सांस लेने में | ఆయాసం |

**Negation handling.** A mention doesn't count if it is negated:
- **English pre-negation:** "no / not / denies / without" within about 25 characters *before* the word ("no chest pain", "denies fever").
- **Hindi and Telugu post-negation:** "nahi / नहीं / లేదు" within about 12 characters *after* the word ("saans nahi phool rahi").
- Some patterns contain the negation themselves ("pee nahi", "not drinking") and are exempt.

Example: *"No chest pain, no vomiting, mild cough"* gives symptoms `[cough]` and no danger signs.

**The deterministic clinical rules** (`protocols.evaluate`), inspired by WHO IMCI; demo thresholds, not validated:

| Vital or sign | YELLOW | RED |
|---|---|---|
| SpO₂ | < 94% | < 90% |
| Temperature | ≥ 39.5 °C, or 35.0–35.9 °C | ≥ 41 °C, < 35.0 °C, or ≥ 37.5 °C in an infant under 2 months |
| Breathing rate | ≥ 50 (under 1 y), ≥ 40 (1–5 y), ≥ 22 (adult) | ≥ 30 (adult) |
| Heart rate (adult) | > 110 | > 130 |
| BP | ≥ 140/90 in pregnancy | ≥ 160/110 in pregnancy, ≥ 180/120, systolic < 90 |
| Danger signs | chest pain (< 35 y), breathing difficulty (adult), dehydration, blood in stool, severe headache (not pregnant) | unconscious, convulsions, unable to feed, vomits everything, chest indrawing, bleeding, pregnancy bleeding, severe headache in pregnancy, stiff neck, bites, suicidal thoughts |
| Fever ≥ 7 days | YELLOW | |

**Plausibility checks:** a reading outside physiological ranges (temperature outside 32–43 °C, SpO₂ < 50, etc.) is **not trusted**. It raises "unreliable reading", and the agent must ask for a re-measure rather than trigger a false emergency.

**Missing information** (`missing_critical`): a coughing child under 5 needs a breathing rate, a pregnant patient needs BP, and a fever needs a temperature. A vague note (at most one symptom, no danger sign, no duration) needs a clarifying question.

---

## 7. Human handoff boundaries

| Situation | What the agent does |
|---|---|
| Critical vital missing | Pauses and **asks the worker** (for example "count breaths for one minute") |
| Impossible reading | Asks for a **re-measure** |
| Vague note | Asks a **clarifying question** |
| RED danger sign | **Emergency referral** at once |
| YELLOW plan | **Referral required** before the visit may close |
| Confidence < 0.55 | **Forced referral** |
| 3 rejected proposals in a row | Switches to the rules planner |
| 12-step budget used up | **Forced referral**; never a guess |
| Agent crash | Visit marked failed and handed to a clinician |

Each handoff carries the **protocol reason plus Gemma's rationale**, and appears in the clinician queue.

---

## 8. Offline error recovery and local state

- **Crash-safe resume.** Every step is written to the `steps` table. On restart, `resume_interrupted()` finds visits still marked "running" and `load_state()` **replays the trace** to rebuild working memory. For example, it knows whether the danger-sign check ran *after* the latest vitals change; if not, it re-checks. Tested with `kill -9` mid-visit.
- **Model crash.** The **Kill model** button simulates it. The trace shows a `RECOVER` step, and the visit completes in rules-only mode.
- **Spotty network.** Finished visits and referrals go into an **outbox** table with an idempotency key (so re-sending never duplicates) and exponential back-off from 5 s to 300 s. They sync when the network returns.
- **Database schema** (`store.py`):

| Table | Holds |
|---|---|
| `patients` | name, age, sex, pregnancy, village |
| `encounters` | a visit: note, vitals, status, triage, findings, plan, answers, degraded flag |
| `steps` | append-only trace of every sense/decide/act/check/recover step |
| `handoffs` | clinician queue |
| `followups` | scheduled revisits |
| `outbox` | records waiting to sync |

---

## 9. Voice (experimental)

**Speech in** (`voice.js` → `/api/transcribe` → `llm.transcribe`):
1. The browser records the mic (`MediaRecorder`), with **voice-activity detection**: it stops about 1.3 s after the speaker goes quiet.
2. It decodes and resamples to **16 kHz mono**, trims silence, and normalises loudness.
3. It encodes a WAV file and sends it as base64 to the local server.
4. Gemma 4 E4B transcribes it using a **duration-aware prompt** ("this 2.4-second clip…") plus a **language hint** (write Telugu words in Telugu script).
5. **Anti-hallucination guards:** replies about the audio ("I cannot transcribe…") are discarded, and a transcript longer than physically possible for the clip length is truncated.

**Talk to Sahayak** (`conversation.js`), a turn-taking spoken dialogue:
1. A greeting in the chosen language.
2. Gemma E2B turns what the worker said into a visit form. **Code decides what is missing**; the model doesn't. Only the complaint and age are essential.
3. Sahayak asks for missing items out loud, and each answer is sent with its question, so "Solomon" is understood as the name.
4. The agent runs. Sahayak announces RED/YELLOW findings, speaks the agent's questions (translated on-device by E2B), and sends spoken answers back.
5. It says the triage and reads the family's advice.

**Speech out** (`tts.py` → `/api/speak`): **Piper** neural voices for Telugu and Hindi (under 1 s per sentence), the macOS voice for English, and browser voices as a last resort.

**Honest status:** voice works with clear speech in a quiet room. It is unreliable with noisy laptop mics and with spoken Telugu numbers ("ముప్పై" was once heard as 37), so the typed workflow is the primary interface.

---

## 10. Privacy
- All data sits in one local SQLite file, and the server listens only on `127.0.0.1`.
- **De-identified sync:** outbox records carry only the patient id, age, sex, and pregnancy. **Names and villages never leave the device.**
- **Hide names** shows initials on screen. **Wipe data** deletes every table and runs `VACUUM`, so deleted rows don't linger on disk.
- Every Gemma call (model, prompt, output) is logged locally for audit, with raw audio stripped from the logs.

---

## 11. Performance (M5 MacBook, 16 GB, Wi-Fi off)

| Measure | Value |
|---|---|
| Gemma 4 E4B generation speed | about 36 tokens/s |
| A typical visit | **2 Gemma calls, 13–20 s** (was about 50 s with 7 calls before our optimisations) |
| Emergency referral visible | about **10 s** (it happens during SENSE, before any Gemma decision) |
| Voice intake step (E2B) | 1.2–1.9 s |
| Transcription of a short clip | 0.5–1.2 s |
| Read-aloud synthesis | under 1 s |

What made it fast: a fixed `num_ctx` (no reloads), `keep_alive`, startup warm-up, the automatic SENSE phase (no model decision spent on steps that always happen), auto-close when nothing is open, and E2B for small chores. The UI only redraws what changed and polls fast only while the agent is working.

---

## 12. Evaluation (`eval/run_eval.py`, `eval/results.md`)

There are 19 labelled vignettes: infants, pregnancy, chest pain, snake bite, self-harm, TB-like cough, dehydration, an implausible reading, and English, Hinglish, and Telugu notes. Each runs through the **full agent** with scripted worker answers.

| Configuration | Exact triage | **Under-triage (unsafe)** | Over-triage |
|---|---|---|---|
| Rules-only | 89% (17/19) | 2 | 0 |
| **Gemma 4 agent** | **95% (18/19)** | **0** | 1 (a deliberate escalation of cough with blood) |

Be honest if asked: these cases were also used to find and fix bugs, so the accurate framing is "no known unsafe failures on 19 cases", not a clinical accuracy claim.

---

## 13. Added after submission (branch `post-hackathon`, not in the judged `main`)

**Returning patients** (`patients.py`, `/api/patients?q=`):
- Typing a name suggests existing patients, with their visit count, last visit, and last triage. Picking one attaches the new visit to that patient.
- The SENSE phase then loads their earlier visits as **history** (with "days ago"). Gemma is told to be more cautious when a patient isn't improving.
- Tested: Kavya's first visit was GREEN. On the return visit ("fever 3 days, less active, drinking less") the protocol said GREEN, but **Gemma escalated to YELLOW, citing the persistent fever despite the earlier visit**. The history is shown under "Previous visits".

**Follow-ups** (`/api/followups`, `/api/followups/<id>/done`):
- A panel groups follow-ups into **Overdue / Due today / Next 7 days**, with "Start visit" (pre-fills the returning patient) and "Done".
- **Seeing the patient again automatically closes their open follow-ups.**
- A database migration adds `done_ts` and `done_by_encounter` to existing databases.

Tests: 4 new unit tests (search, history, follow-up buckets, done and auto-close on revisit). An HTTP smoke test with real Gemma passed all 7 checks.

---

## 14. How to run it (for a judge)
```bash
git clone https://github.com/SolomonLipson/Hackathon-strong && cd Hackathon-strong
brew install ollama && ollama serve &
ollama pull gemma4:e4b-it-qat && ollama pull gemma4:e2b-it-qat
./scripts/setup_voices.sh          # optional: natural Telugu/Hindi voices
python3 -m sahayak.server          # open http://127.0.0.1:8765, then turn Wi-Fi off
python3 -m unittest discover -s tests   # tests, no model needed
python3 eval/run_eval.py           # accuracy evaluation
```

---

## 15. Likely judge questions, with answers

**Q: Is it really offline?**
Yes. The model runs in Ollama on `localhost`, the page loads nothing from the internet, speech recognition is Gemma's own audio input, and speech output uses Piper or OS voices. We demo with Wi-Fi off. The only network use is the optional outbox sync, which is de-identified.

**Q: Why Gemma 4 E4B and not a bigger model?**
It has to run on a clinic laptop, and eventually a phone. E4B QAT uses about 3 GB of memory at 36 tokens/s and still reads Hinglish and writes Telugu. E2B covers low-memory devices and fast chores.

**Q: What stops the model from giving dangerous advice?**
The deterministic checker. Triage can never go below the protocol level, a RED sign opens a referral before the model reasons, low confidence forces a handoff, and every model "yes" needs a quote that really appears in the note.

**Q: What if Gemma misses a danger sign?**
The offline keyword net runs in parallel and its signs are always added to Gemma's. In the evaluation, Gemma plus the checker had zero under-triage.

**Q: How is this an agent and not a chatbot?**
It chooses its own next action from tools, verifies it, can be rejected and correct itself, pauses to ask a human, keeps state across steps and crashes, and hands off. The trace in the UI shows every step.

**Q: How do you handle a crash or a power cut?**
Every step is persisted, and on restart the visit resumes from its trace. If the model dies, a rules-only planner finishes the visit.

**Q: Did you fine-tune Gemma?**
No. We use the instruction-tuned QAT models and control them with JSON schemas, checklist prompts with definitions, evidence and grounding checks, and a no-progress guard.

**Q: What are the limitations?**
The protocol isn't clinically validated. Voice is experimental in noisy rooms. It runs on a laptop, not yet a phone. The evaluation set is small and was also our development set.

**Q: What's next?**
An Android app with Gemma 4 E2B (LiteRT), clinician-reviewed IMCI rules, a larger held-out evaluation, encryption at rest, and a clinician dashboard for synced referrals.
