# Sahayak: an offline triage agent on Gemma 4

**Google DeepMind Hyderabad Hackathon · Problem Statement 5: Best Use of Gemma 4 (Local-First Agents)**

Sahayak ("helper") supports a community health worker (ASHA) during a home visit in a village with no signal. The worker types a note in any mix of English, Hindi, or Telugu, adds whatever vitals they could measure, and the agent runs a **sense → decide → act → check** loop entirely on the device, using **Gemma 4 E4B** (with E2B as fallback) through Ollama:

- It **extracts** structured findings from the messy note.
- It **checks** deterministic danger-sign rules, which Gemma cannot overrule.
- It **asks the worker** for missing vitals (for example "count the child's breaths for one minute") and **pauses** until they answer.
- It **recommends care** (GREEN/YELLOW/RED) with advice in the family's language.
- It **hands off to a clinician** when anything is RED, when its confidence is low, or when it keeps getting rejected.
- It **queues records** in a local outbox and syncs them when connectivity returns.

The model never needs the internet, and the visit still completes if the model crashes.

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

## Demo script (2 minutes)

1. **Child · cough, fast breathing?** The agent extracts findings, sees a coughing 2-year-old with no breathing rate, and **pauses to ask the worker to count breaths**. Answer `55`. The rules flag fast breathing (≥40 for ages 1 to 5), the plan becomes YELLOW, and a referral is opened.
2. **Pregnant · headache.** The keyword safety net and Gemma both catch *severe headache / blurred vision*, which is a RED danger sign. The **emergency referral opens instantly**, before any more model reasoning, and the agent asks for the BP.
3. **Hinglish · bachcha behosh.** Gemma reads the Hindi-English note ("behosh" means unconscious, "pee nahi rahi" means not drinking). RED.
4. Click **Kill model** and run **Adult · mild fever**. The trace shows a `RECOVER` step and the agent finishes in **rules-only mode**.
5. Toggle **Offline → Online**. The outbox drains, and each record carries an idempotency key.
6. Stop the server mid-visit (Ctrl-C) and start it again. The visit **resumes from its last saved step**.

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
tests/          loop and safety tests with a scripted fake model
Design.md       architecture and feature documentation
```

Every Gemma call (model, prompt, raw output) and every tool call is logged to `data/agent.log`.
