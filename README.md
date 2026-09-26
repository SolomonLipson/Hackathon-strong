# Sahayak: an offline triage agent on Gemma 4

**Google DeepMind Hyderabad Hackathon · Problem Statement 5: Best Use of Gemma 4 (Local-First Agents)**

Sahayak ("helper") supports a community health worker (ASHA) during a home visit in a village with no signal. The worker types a note in any mix of English, Hindi, or Telugu, adds whatever vitals they could measure, and the agent runs a **sense → decide → act → check** loop entirely on the device, using **Gemma 4 E4B** (with E2B as fallback) through Ollama:

- It **extracts** structured findings from the messy note.
- It **checks** deterministic danger-sign rules, which Gemma cannot overrule.
- It **asks the worker** for missing vitals (for example "count the child's breaths for one minute") and **pauses** until they answer.
- It **recommends care** (GREEN/YELLOW/RED) with advice in the family's language.
- It **hands off to a clinician** when anything is RED, when its confidence is low, or when it keeps getting rejected.
- It **queues records** in a local outbox and syncs them when connectivity returns.

- It **listens**: the worker can speak the note or an answer. **Gemma 4 transcribes the audio on-device** with its native audio encoder, so there is no cloud speech API and no separate ASR model. The advice can be **read aloud** in Hindi or Telugu using the device's offline voices.
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

Run the accuracy evaluation (18 labelled vignettes, Gemma vs rules-only, results in [eval/results.md](eval/results.md)):

```bash
python3 eval/run_eval.py
```

Voice input needs a browser microphone. `http://127.0.0.1` counts as a secure origin, so Chrome allows it offline.

## Demo script (2 minutes)

1. **Child · cough, fast breathing?** Gemma reads "breathing fast" as difficulty breathing in a child under five, which is RED, so the **emergency referral opens instantly**. The agent then notices there is no breathing rate and **pauses to ask the worker to count breaths**. Answer `55`. The rules re-check, the plan is RED, and the advice comes in Telugu.
2. **Pregnant · headache.** The keyword safety net and Gemma both catch *severe headache / blurred vision*, which is a RED danger sign. The **emergency referral opens instantly**, before any more model reasoning, and the agent asks for the BP.
3. **Hinglish · bachcha behosh.** Gemma reads the Hindi-English note ("behosh" means unconscious, "pee nahi rahi" means not drinking). RED.
4. Click **Kill model** and run **Adult · mild fever**. The trace shows a `RECOVER` step and the agent finishes in **rules-only mode**.
5. Toggle **Offline → Online**. The outbox drains, and each record carries an idempotency key.
6. Click **🎤 Speak the note** and say, for example, *"Bachchi ko do din se bukhar hai, kuch pee nahi rahi"*. Gemma transcribes it locally. Answer the agent's questions by voice too, and press **🔊 Read aloud** on the advice.
7. Enter temperature `30` with the note *"not sure, low energy"*. The agent refuses to trust the reading, asks for a re-measure, then asks a clarifying question.
8. Stop the server mid-visit (Ctrl-C) and start it again. The visit **resumes from its last saved step**.

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
  static/voice.js  mic capture -> 16 kHz WAV -> Gemma transcription; offline read-aloud
tests/          loop and safety tests with a scripted fake model
eval/           labelled vignettes + accuracy report (Gemma vs rules-only)
scripts/        export real runs to the GitHub Pages replay demo
Design.md       architecture and feature documentation
```

Every Gemma call (model, prompt, raw output) and every tool call is logged to `data/agent.log`.
