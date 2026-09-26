# Sahayak: an offline triage agent on Gemma 4

**Google DeepMind Hyderabad Hackathon · Problem Statement 5: Best Use of Gemma 4 (Local-First Agents)**

Sahayak ("helper") supports a community health worker (ASHA) during a home visit in a village with no signal. The worker types a note in any mix of English, Hindi, or Telugu, adds whatever vitals they could measure, and the agent runs a **sense → decide → act → check** loop entirely on the device, using **Gemma 4 E4B** (with E2B as fallback) through Ollama:

- It **extracts** structured findings from the messy note.
- It **checks** deterministic danger-sign rules, which Gemma cannot overrule.
- It **asks the worker** for missing vitals (for example "count the child's breaths for one minute") and **pauses** until they answer.
- It **recommends care** (GREEN/YELLOW/RED) with advice in the family's language.
- It **hands off to a clinician** when anything is RED, when its confidence is low, or when it keeps getting rejected.
- It **queues records** in a local outbox and syncs them when connectivity returns.

- It **listens**: the worker can speak the note or an answer. **Gemma 4 transcribes the audio on-device** with its native audio encoder and returns a cleaned transcript (no "umm/aah", no false starts, every clinical fact kept). There is no cloud speech API and no separate ASR model. Advice is **read aloud** in Hindi or Telugu by the operating system's offline voices.
- It **protects privacy**: names are masked on screen with one click, synced records are de-identified, and **Wipe data** erases the device.
- It **doubts bad readings**: a physiologically implausible vital, such as 30 °C in a talking patient, is not acted on. The agent asks for a re-measure. A vague note ("not sure, low energy") gets a clarifying question.

The model never needs the internet, and the visit still completes if the model crashes.

**Live demo (replay of real on-device runs):** https://solomonlipson.github.io/Hackathon-strong/

> ⚠️ Demo protocol for a hackathon. This is not validated medical advice.

## Quick start (macOS/Linux, about 5 minutes plus model download)

```bash
# 1. Local model runtime + Gemma 4
brew install ollama            # or see https://ollama.com/download
ollama serve &                 # starts on 127.0.0.1:11434
ollama pull gemma4:e4b-it-qat  # primary model (6.2 GB, quantization-aware trained)
ollama pull gemma4:e2b-it-qat  # fallback (4.3 GB, optional, recommended)

# 2. Run Sahayak (Python 3.10+, standard library only, no pip install)
python3 -m sahayak.server      # open http://127.0.0.1:8765
```

Then turn Wi-Fi off. Everything keeps working.

Run the tests (no model needed):

```bash
python3 -m unittest discover -s tests
```

Run the accuracy evaluation (19 labelled vignettes, Gemma vs rules-only, results in [eval/results.md](eval/results.md)):

```bash
python3 eval/run_eval.py
```

**For natural voices (recommended, one-time, then offline):** macOS System Settings → Accessibility → Spoken Content → System voice → Manage Voices, then download **Geeta (Enhanced)** for Telugu, **Lekha (Enhanced)** for Hindi, and **Rishi (Enhanced)** for Indian English. Sahayak automatically uses the best installed variant (Premium > Enhanced > compact).

Voice input needs a browser microphone. `http://127.0.0.1` counts as a secure origin, so Chrome allows it offline.

## Demo script (3 minutes)

0. Click **🎙 Talk to Sahayak** for a hands-free spoken conversation. Sahayak greets you, you describe the patient, it asks for anything missing, speaks its findings and questions, takes your spoken answers, and finally reads the family's advice in Telugu or Hindi. Everything is on-device.

1. Turn Wi-Fi off. Click **🎤 Speak the note** and just talk, for example *"Bachchi ko do din se tez bukhar hai, behosh jaisi hai, kuch pee nahi rahi"*. Recording stops when you pause, and Gemma returns a cleaned transcript without fillers.
2. Start the agent. Within about 10 s the SENSE phase shows the RED danger signs with Gemma's quoted evidence, and the **emergency referral is already open**. Gemma then writes the care plan with Telugu or Hindi advice. Press **🔊 Read aloud** (on-device voice).
3. A 2-year-old with cough and no breathing rate: the agent **pauses and asks the worker to count breaths**. Answer by voice or typing.
4. Temperature `30` with the note *"not sure, low energy"*: the agent refuses the impossible reading, asks for a re-measure, then asks a clarifying question.
5. Click **Kill model** and run any visit. The trace shows `RECOVER` and the visit completes in rules-only mode.
6. Toggle **No network → Online**. The outbox drains, de-identified, so names never leave the device.
7. **👁 Names shown → 🙈 Names hidden** masks names on screen. **🗑 Wipe data** permanently deletes everything on the device.
8. `kill -9` the server mid-visit and restart it. The visit resumes from its last saved step.

## Configuration

All settings are in [sahayak/config.py](sahayak/config.py), and each can be overridden with an environment variable:

| Variable | Default | Meaning |
|---|---|---|
| `SAHAYAK_MODEL` | `gemma4:e4b-it-qat` | primary Gemma model |
| `SAHAYAK_FALLBACK_MODEL` | `gemma4:e2b-it-qat` | fallback model |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | local runtime |
| `SAHAYAK_HOST` / `SAHAYAK_PORT` | `127.0.0.1` / `8765` | web server |
| `SAHAYAK_SYNC_URL` | unset (simulated) | district server endpoint for the outbox |
| `SAHAYAK_DATA_DIR` | `./data` | SQLite database and `agent.log` |

## Layout

```
sahayak/
  config.py     all tunables (models, limits, paths)
  llm.py        local Gemma client: JSON-schema outputs, E4B -> E2B -> LLMUnavailable
  protocols.py  deterministic danger-sign rules + keyword extractor (offline safety net)
  tools.py      the agent's tools (ACT) and AgentState
  checker.py    the CHECK step: rejects unsafe actions, forces human handoff
  agent.py      the loop, rules-only fallback planner, crash-safe resume
  store.py      SQLite local state (patients, encounters, trace, handoffs, outbox)
  sync.py       store-and-forward outbox with back-off + idempotency keys
  server.py     stdlib HTTP server + JSON API
  static/       offline single-page UI (no CDN)
  tts.py        offline read-aloud (best installed macOS voice / espeak-ng)
  intake.py     voice mode: Gemma turns a spoken description into the visit form
  static/conversation.js  "Talk to Sahayak" hands-free spoken dialogue
  static/voice.js  mic capture + auto-stop on silence -> 16 kHz WAV -> Gemma cleaned transcript; read-aloud playback
tests/          loop and safety tests with a scripted fake model
eval/           labelled vignettes + accuracy report (Gemma vs rules-only)
scripts/        export real runs to the GitHub Pages replay demo
Design.md       architecture and feature documentation
```

Every Gemma call (model, prompt, raw output) and every tool call is logged to `data/agent.log`.
