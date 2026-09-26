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
- One tool per iteration, chosen by Gemma from a JSON-schema-constrained menu. Gemma sees a compact state view: note, vitals, findings, protocol flags, history, answers, plan, referral, step budget, and the checker's last feedback.
- The loop ends on `finish` (approved by the checker), on `ask_health_worker` (status `needs_input`), or when the budget runs out (`MAX_STEPS`). Running out of budget produces a forced clinician handoff, never a guess.
- **Degraded mode**: if Gemma is unreachable, returns invalid JSON after retries, or proposes rejected actions `MAX_CHECK_REJECTIONS` times, the loop switches to `rules_planner`, a deterministic policy that follows the same tools and checker.
- **Crash-safe resume**: every step is persisted. `load_state()` replays the trace to rebuild working memory (for example, whether rules were checked after the latest findings or vitals change). `resume_interrupted()` restarts visits that were `running` at startup.

### Sense ([tools.py](sahayak/tools.py) `extract_findings`)
- Gemma extracts symptoms, allowed danger-sign codes, duration, and any vitals mentioned in the note (converting °F to °C), using a strict JSON schema.
- **Keyword safety net**: English and Hinglish regexes run too, and any danger sign they find is unioned in, so a missed "behosh" cannot lower triage.

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
- Voice input with on-device ASR, and packaging for Android via LiteRT / MediaPipe LLM Inference.
- Encrypting the SQLite file at rest, and authenticated sync.
