# Sahayak: an offline triage agent for village health workers, on Gemma 4

**Subtitle:** A sense-decide-act-check agent that runs 100% on-device with Gemma 4 E4B/E2B. It pauses to ask the health worker for missing vitals, can only ever escalate triage, hands off to clinicians, and keeps working when the model or the network dies.

**Track:** Problem Statement 5: Best Use of Gemma 4 (Local-First Agents)

## The problem

India's ~1 million ASHA community health workers are the first point of contact for rural families. During a home visit they must decide in minutes whether a sick child can be managed at home, needs the primary health centre within 24 hours, or needs an ambulance now. Three constraints rule out a cloud chatbot:

1. **Connectivity**: many villages have patchy or no mobile data.
2. **Privacy**: pregnancy status, symptoms, and vitals should not leave the device by default.
3. **Safety**: a single model call that maps a messy note to an answer is exactly the "straight arrow" that fails silently. Health triage needs verification, a way to ask for missing information, and a clear line where a human takes over.

## What we built

Sahayak is a local web app. Python standard library, SQLite, and Gemma 4 through Ollama on `localhost`. There is nothing to pip install and no CDN, and it keeps working with Wi-Fi off. The worker types a note in any mix of English, Hindi, or Telugu (for example *"Bachchi ko 2 din se tez bukhar hai, aaj subah se behosh jaisi hai"*), adds whatever vitals they could measure, and watches the agent work live.

### The agent loop

Each iteration is **DECIDE → CHECK → ACT → CHECK → PERSIST**:

- **DECIDE**: Gemma 4 E4B gets a compact JSON view of the state: note, vitals, extracted findings, protocol flags, patient history, the worker's answers, the current plan, any referral, the step budget, and *the checker's last feedback*. It returns `{thought, tool, args}` constrained by a JSON schema (Ollama structured outputs), choosing one of seven tools:
  `extract_findings`, `check_danger_signs`, `get_patient_history`, `ask_health_worker`, `recommend_care`, `refer_to_clinician`, `finish`.
- **SENSE** (`extract_findings`): Gemma turns the multilingual note into symptoms, danger-sign codes from a fixed vocabulary, duration, and vitals mentioned in the text (converting °F to °C). A keyword extractor for English and Hinglish runs alongside it, and any danger sign it finds is **unioned in**, so a model miss can never lower urgency.
- **CHECK** (pre-action): a deterministic verifier rejects invalid or unsafe proposals and feeds the reason back to Gemma on its next turn. Most importantly, **triage can only go up**. If the IMCI-inspired rules say RED (for example SpO₂ < 90%, fever in an infant under 2 months, severe BP in pregnancy, "unconscious"), a GREEN or YELLOW plan is rejected. `finish` is refused until findings are extracted, rules are checked, critical missing vitals have been asked for, a plan exists, and YELLOW or RED visits have a referral.
- **ACT**: the tool runs against local state.
- **CHECK** (post-action): a RED protocol result opens an emergency referral *immediately, before any further model reasoning*. Model confidence below 0.55 forces a clinician handoff.
- **PERSIST**: the state and a trace step are written to SQLite after every step.

### Human handoff boundaries

The agent never has to guess. It **asks the worker** when a critical vital is missing: for a coughing child under five with no breathing rate it says *"Count the child's breaths for one full minute"* and pauses with status `needs_input` until answered. It **hands off to a clinician** when the rules say RED, the plan is YELLOW, confidence is low, the checker rejects too many proposals, or the 12-step budget runs out. Handoffs appear in a clinician queue.

### Offline error recovery and local state

- **Model fallback chain**: E4B (QAT) is retried once, then E2B (QAT), then the **rules-only planner**, a deterministic policy that uses the same tools and the same checker. The trace shows a `RECOVER` step and the visit is marked *degraded* but still completes safely. A **Kill model** switch in the UI demonstrates this live.
- **Crash-safe resume**: on restart, visits that were `running` are rebuilt by replaying the step trace (for example, whether the rules were checked *after* the latest vitals change) and continue from where they stopped.
- **Store-and-forward sync**: finished visits and referrals go to a local outbox with idempotency keys and exponential back-off, and drain when connectivity returns. An **Offline/Online** switch simulates this.
- **Everything local**: patients, encounters, the full decision trace, handoffs, follow-ups, and the outbox live in one SQLite file. Every Gemma call (model, prompt, raw output) is logged for audit.

## Why these technical choices

- **Gemma 4 E4B/E2B QAT**: quantization-aware-trained edge models that fit on a laptop or a clinic mini-PC, while still reading Hinglish notes and writing advice in Telugu or Hindi for the family. E2B is the low-memory fallback.
- **JSON-schema-constrained outputs**: every model decision is machine-checkable, and that is what lets a deterministic checker sit in the loop instead of parsing prose.
- **Model as planner, rules as the floor**: Gemma contributes language understanding, sequencing (what to ask, when to check history), and local-language counselling. Auditable rules guarantee the safety floor. This is the right trust boundary for health.
- **Standard library only**: zero install friction on low-end hardware, and the whole system is under 1,500 lines that a judge can read.

## Challenges we overcame

- **Keeping rule state consistent across pauses and crashes.** A worker's answer changes vitals, so earlier rule results become stale. We rebuild state by replaying the trace in order and mark the rules fresh only if `check_danger_signs` ran after the last change to findings or vitals. The checker also refuses `finish` if a new vital pushed the protocol level above the existing plan.
- **Stopping the model from arguing with safety.** Early runs showed the model occasionally proposing a lower triage. Rejections with explicit reasons ("protocol requires at least RED: SpO₂ 88% < 90%") let it self-correct within one step, and repeated rejections switch to the rules planner.
- **Small models repeat themselves.** Gemma 4 E4B initially called `extract_findings` 12 times in a row. We did not fix this by prompt-tweaking alone. The checker now rejects a repeated tool whose inputs haven't changed and returns the remaining checklist, and the state view carries an explicit `progress` / `still_open` checklist. The model still chooses what to ask, the triage level, the advice, and when to escalate.
- **Model download on venue Wi-Fi.** We switched to the QAT builds (6.2 GB instead of 9.6 GB for E4B), which also suits the on-device goal.

## Results (M5 MacBook, 16 GB, Wi-Fi off)

All runs below used real Gemma 4 E4B QAT through Ollama, with no network. Their unedited traces are in the live demo.

| Case | Outcome | Steps | Wall time |
|---|---|---|---|
| 2-year-old with cough, "breathing fast" | Asked for breathing rate (55), RED, Telugu advice, referral | 7 | 48 s |
| Pregnant, severe headache, blurred vision | Instant RED referral, asked for BP (166), RED | 7 | 53 s |
| Hinglish note: "behosh jaisi hai, kuch pee nahi rahi" | Gemma mapped "behosh" to lethargic/unconscious, RED, Hindi advice | 5 | 32 s |
| Adult with mild fever, normal vitals | GREEN, home care in Hindi, follow-up in 2 days | 5 | 41 s |
| Same flow with the model killed | RECOVER step, rules-only planner, RED, visit completed | 5 | 1 s |
| Server `kill -9` mid-visit | Resumed at step 3 on restart, YELLOW plus referral | 6 | – |

A DECIDE step takes about 4 to 6 s on an M5 laptop, and extraction about 5 s. In early runs the model repeated finished steps. After we added the no-progress guard and the `still_open` checklist, every case finished on Gemma with at most one checker rejection.

## Limitations and next steps

The danger-sign protocol is a hackathon demo and is not clinically validated. The next steps are clinician-reviewed IMCI/maternal rule sets, on-device voice input, packaging for Android with LiteRT, encryption at rest, and authenticated sync to the state HMIS.

**Code:** https://github.com/SolomonLipson/Hackathon-strong  
**Live demo (replay of real on-device runs):** https://solomonlipson.github.io/Hackathon-strong/
