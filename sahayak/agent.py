"""
The agent loop: SENSE -> DECIDE -> ACT -> CHECK, repeated until done.

Each iteration:
  1. DECIDE  Gemma 4 (local, via Ollama) sees the current state (note,
             vitals, findings, rule flags, history, answers, plan, and the
             checker's last feedback) and picks ONE tool as JSON.
  2. CHECK   checker.precheck() may reject the proposal; the rejection is fed
             back to the model next turn, so the agent corrects itself.
  3. ACT     the tool runs against local state (tools.py).
  4. CHECK   checker.postcheck() may force a human handoff (RED danger sign,
             low confidence).
  5. PERSIST state + a trace step are written to SQLite before continuing.

Stops when: `finish` is approved (status done / handoff), the agent asks the
health worker a question (status needs_input; resume with answer()), or the
step / rejection budget runs out (forced handoff: never a silent guess).

Offline error recovery:
  * If Gemma is unreachable or returns garbage (even after the E4B -> E2B
    fallback) the loop switches to `rules_planner`, a deterministic policy,
    and marks the encounter `degraded`. The visit still completes safely.
  * Every step is persisted; `resume_interrupted()` restarts encounters that
    were 'running' when the process died (power cut, OOM kill).
"""

import json
import re
import threading
import time

from . import checker, config, llm, protocols, store, sync
from .log import get_logger, logged
from .tools import TOOL_NAMES, TOOLS, VITAL_FIELDS, AgentState

log = get_logger("agent")

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "tool": {"type": "string", "enum": TOOL_NAMES},
        "args": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "field": {"type": "string", "enum": VITAL_FIELDS + ["none"]},
                "triage": {"type": "string", "enum": protocols.LEVELS},
                "advice_steps": {"type": "array", "items": {"type": "string"}},
                "advice_local_language": {"type": "string"},
                "followup_days": {"type": "integer"},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "urgency": {"type": "string", "enum": protocols.LEVELS},
                "reason": {"type": "string"},
                "summary": {"type": "string"},
            },
        },
    },
    "required": ["thought", "tool", "args"],
}

DECIDE_SYSTEM = """You are Sahayak, an offline triage assistant for a community health worker (ASHA) in rural India.
You run fully on-device. You never diagnose; you triage, advise safe first steps, and hand off to clinicians.

Each turn, choose exactly ONE tool:
(The note is already read into "findings", history is loaded and the protocol rules are applied automatically in the SENSE phase.
 If "history" shows earlier visits, use them: a returning patient who is not improving, or was YELLOW/RED before, deserves more caution.
 findings.unclear_signs are danger signs the note only hints at: consider asking about the most important one.)
- extract_findings / check_danger_signs / get_patient_history: re-run only if something changed.
- ask_health_worker {question, field}: ask for ONE missing measurement, a re-measurement of an unreliable reading, or a clarifying fact.
  field = the vital it fills (rr, temp_c, sbp, dbp, hr, spo2) or "none" for a free-text clarification (e.g. a vague note: ask since when and what other symptoms).
- recommend_care {triage, advice_steps, advice_local_language, followup_days, confidence, rationale}:
  triage GREEN (home care), YELLOW (clinic within 24h), RED (refer now). Never lower than the protocol level.
  advice_steps: 2-5 short practical steps for the health worker. advice_local_language: the same advice for the family in the
  requested advice_language, written in that language's OWN SCRIPT (Telugu in తెలుగు script, Hindi in देवनागरी), simple words, never romanized.
  confidence: 0-1, how sure you are the triage is right.
- refer_to_clinician {urgency, reason}: human handoff. Required for YELLOW or RED.
- finish {summary}: close the visit when everything is done.

Rules: look at "progress" and "still_open" in the state: never repeat a finished step. Follow checker_feedback exactly.
You decide HOW to do each open item (what to ask, the triage level, advice, confidence); you may also escalate triage above the protocol level when your judgment says so. Keep "thought" to one sentence."""


def _state_view(s: AgentState) -> str:
    """Compact JSON view of the state that Gemma sees in DECIDE."""
    view = {
        "patient": {k: s.patient.get(k) for k in ("age_years", "sex", "pregnant")},
        "advice_language": s.language,
        "note": s.note,
        "vitals": s.vitals,
        "findings": s.findings or "NOT EXTRACTED YET",
        "protocol": s.rules or "NOT CHECKED YET",
        "history": s.history if s.history is not None else "NOT LOADED",
        "worker_answers": s.answers,
        "plan": s.plan or "NONE",
        "referral": s.referral or "NONE",
        "progress": {
            "findings_extracted": bool(s.findings), "danger_signs_checked": s.rules is not None,
            "history_loaded": s.history is not None, "plan_drafted": bool(s.plan), "referral_opened": bool(s.referral),
        },
        "still_open": checker.open_items(s),
        "steps_used": f"{s.step}/{config.MAX_STEPS}",
        "checker_feedback": s.last_feedback or "none",
    }
    return json.dumps(view, ensure_ascii=False, default=str)


ADVICE = {
    "RED": ["Arrange transport to the nearest PHC / hospital now (call 108).",
            "Keep the patient warm and lying on their side if drowsy.",
            "Do not give anything by mouth if unconscious or convulsing."],
    "YELLOW": ["Take the patient to the PHC within 24 hours.",
               "Give plenty of fluids; continue feeding.",
               "Return immediately if any danger sign appears."],
    "GREEN": ["Home care: fluids, rest, continue feeding.",
              "Re-check in 2-3 days or sooner if worse.",
              "Return immediately if any danger sign appears."],
}


def rules_planner(s: AgentState) -> tuple[str, dict, str]:
    """Deterministic fallback policy used when no local model is available."""
    if not s.findings:
        return "extract_findings", {}, "Extract findings (degraded mode)."
    if s.rules is None:
        return "check_danger_signs", {}, "Check protocol rules."
    if s.history is None:
        return "get_patient_history", {}, "Load history."
    asks = checker.unanswered(s)
    if asks and len(s.answers) < checker.MAX_QUESTIONS:
        return "ask_health_worker", asks[0], "Ask for missing vital."
    if not s.plan:
        level = s.rules["level"]
        return "recommend_care", {
            "triage": level, "advice_steps": ADVICE[level], "confidence": 0.6,
            "followup_days": {"RED": 1, "YELLOW": 1, "GREEN": 3}[level],
            "rationale": "Rules-only triage (local model unavailable).",
        }, "Recommend care from protocol level."
    if s.plan["triage"] != "GREEN" and not s.referral:
        return "refer_to_clinician", {"urgency": s.plan["triage"], "reason": "Protocol triage " + s.plan["triage"]}, "Refer."
    return "finish", {"summary": "Completed in degraded (rules-only) mode."}, "Finish."


def _decide(s: AgentState) -> tuple[str, dict, str, str]:
    """DECIDE: ask Gemma for the next tool; fall back to rules on failure."""
    if not s.degraded:
        try:
            out, model = llm.chat_json(DECIDE_SYSTEM, _state_view(s), DECIDE_SCHEMA)
            args = out.get("args") or {}
            if args.get("field") == "none":
                args.pop("field")
            return out.get("tool", ""), args, out.get("thought", ""), model
        except llm.LLMUnavailable as err:
            log.warning("switching to rules-only planner: %s", err)
            s.degraded = True
            store.add_step(s.encounter_id, s.step, "recover", None,
                           {"message": "Local model unavailable: switched to deterministic rules planner.", "error": str(err)})
    tool, args, thought = rules_planner(s)
    return tool, args, thought, "rules"


def _persist(s: AgentState, status: str) -> None:
    """Write the state back to the encounter row."""
    store.update_encounter(
        s.encounter_id, status=status, vitals=s.vitals, findings=s.findings, plan=s.plan,
        answers=s.answers, triage=s.plan.get("triage") or (s.rules or {}).get("level"),
        question=(s.pending_question or {}).get("question"), degraded=int(s.degraded),
    )


def _finalize(s: AgentState) -> str:
    """Final status, follow-up scheduling and sync queueing for a closed visit."""
    status = "handoff" if s.referral else "done"
    days = s.plan.get("followup_days")
    if days:
        store.execute(
            "INSERT INTO followups (id,patient_id,encounter_id,due_ts,reason) VALUES (?,?,?,?,?)",
            (store.new_id(), s.patient["id"], s.encounter_id, time.time() + int(days) * 86400, f"Follow-up after {s.plan['triage']} triage"),
        )
    store.enqueue("encounter", f"encounter:{s.encounter_id}", {
        "encounter_id": s.encounter_id, "patient_id": s.patient["id"], "triage": s.plan.get("triage"),
        "findings": s.findings, "plan": s.plan, "degraded": s.degraded,
    })
    return status


def _sense(s: AgentState) -> bool:
    """
    SENSE phase, run automatically whenever the agent's picture is stale:
    Gemma reads the note (+ answers) into findings, local history is loaded,
    and the deterministic danger-sign rules are applied. These steps are
    always needed, so they don't cost a model decision; a RED sign therefore
    opens the emergency referral within seconds. Returns True if it did work.
    """
    did = False
    if not s.findings:
        was_degraded = s.degraded
        res = TOOLS["extract_findings"](s, {})
        if s.degraded and not was_degraded:
            store.add_step(s.encounter_id, s.step, "recover", None,
                           {"message": "Local model unavailable: read the note with the keyword extractor and switched to the deterministic rules planner."})
        store.add_step(s.encounter_id, s.step, "sense", "extract_findings", res, res.get("model") or "keywords")
        did = True
    if s.history is None:
        store.add_step(s.encounter_id, s.step, "sense", "get_patient_history", TOOLS["get_patient_history"](s, {}))
        did = True
    if s.rules is None:
        res = TOOLS["check_danger_signs"](s, {})
        store.add_step(s.encounter_id, s.step, "check", "check_danger_signs", {"ok": True, **res})
        post = checker.postcheck(s, "check_danger_signs", res)
        if post.get("force"):
            forced = TOOLS["refer_to_clinician"](s, {"urgency": "RED", "reason": post.get("reason") or post["feedback"]})
            store.add_step(s.encounter_id, s.step, "check", "refer_to_clinician",
                           {"ok": True, "forced": True, "feedback": post["feedback"], **forced})
            s.last_feedback = (post["feedback"] + " The referral is DONE (do not call refer_to_clinician again). "
                               f"Still open: {', '.join(checker.open_items(s))}.")
        did = True
    return did


@logged(log)
def run(s: AgentState) -> str:
    """Drive the loop until done, paused for input, or out of budget. Returns status."""
    _persist(s, "running")
    while s.step < config.MAX_STEPS:
        s.step += 1
        if _sense(s):
            _persist(s, "running")
            continue
        tool, args, thought, model = _decide(s)
        store.add_step(s.encounter_id, s.step, "decide", tool, {"thought": thought, "args": args}, model)

        verdict = checker.precheck(s, tool, args)
        if not verdict["ok"]:
            s.rejections += 1
            s.last_feedback = verdict["feedback"]
            store.add_step(s.encounter_id, s.step, "check", tool, {"ok": False, "feedback": verdict["feedback"]})
            if s.rejections >= config.MAX_CHECK_REJECTIONS and not s.degraded:
                s.degraded = True  # the model keeps proposing unsafe/invalid actions: stop trusting it
                store.add_step(s.encounter_id, s.step, "recover", None,
                               {"message": "Too many rejected proposals: switching to rules planner."})
            _persist(s, "running")
            continue

        result = TOOLS[tool](s, args)
        s.last_feedback = ""
        s.rejections = 0  # only consecutive rejections count toward the rules-planner switch
        store.add_step(s.encounter_id, s.step, "act", tool, result)

        post = checker.postcheck(s, tool, result)
        if post.get("force"):
            urgency = max("YELLOW", (s.rules or {}).get("level", "YELLOW"), s.plan.get("triage") or "YELLOW",
                          key=protocols.LEVELS.index)  # a handoff is never GREEN
            forced = TOOLS[post["force"]](s, {"urgency": urgency, "reason": post.get("reason") or post["feedback"]})
            store.add_step(s.encounter_id, s.step, "check", post["force"], {"ok": True, "forced": True,
                                                                               "feedback": post["feedback"], **forced})
            s.last_feedback = (post["feedback"] + " The referral is DONE (do not call refer_to_clinician again). "
                               f"Still open: {', '.join(checker.open_items(s))}.")

        if tool == "recommend_care" and s.referral and s.plan.get("rationale"):
            # The clinician gets Gemma's own reasoning, not just the rule that fired.
            note = f"{s.referral['reason']} | Gemma: {s.plan['rationale']}"
            store.execute("UPDATE handoffs SET reason=? WHERE id=?", (note, s.referral["id"]))
            s.referral["reason"] = note
        if tool in ("recommend_care", "refer_to_clinician") and checker.open_items(s) == ["finish"] and \
                checker.precheck(s, "finish", {})["ok"]:
            # Nothing left to decide: close the visit without spending another model call.
            summary = s.plan.get("rationale") or "Visit complete."
            TOOLS["finish"](s, {"summary": summary})
            store.add_step(s.encounter_id, s.step, "check", "finish",
                           {"ok": True, "feedback": "All checks passed: visit closed automatically.", "summary": summary})

        if s.pending_question:
            _persist(s, "needs_input")
            return "needs_input"
        if s.done:
            status = _finalize(s)
            _persist(s, status)
            sync.kick()
            return status
        _persist(s, "running")

    # Budget exhausted: never guess, hand off.
    if not s.referral:
        TOOLS["refer_to_clinician"](s, {"urgency": (s.rules or {}).get("level", "YELLOW"),
                                         "reason": "Agent step budget exhausted without a safe conclusion."})
    store.add_step(s.encounter_id, s.step, "check", "refer_to_clinician",
                   {"ok": True, "forced": True, "feedback": "Step budget exhausted: handed off to clinician."})
    s.done = True
    status = _finalize(s)
    _persist(s, "handoff")
    return status


# --- Public API used by the server ---------------------------------------------

_running: set[str] = set()
_running_lock = threading.Lock()


def load_state(eid: str) -> AgentState:
    """Rebuild working memory for an encounter from SQLite (crash-safe resume)."""
    enc = store.get_encounter(eid)
    s = AgentState(
        encounter_id=eid, patient=enc["patient"], note=enc["note"], vitals=enc["vitals"] or {},
        language=enc["language"] or "English", findings=enc["findings"] or {}, answers=enc["answers"] or [],
        plan=enc["plan"] or {}, degraded=bool(enc["degraded"]),
    )
    s.step = max((st["n"] for st in enc["steps"] if st["n"] < 999), default=0)
    # Replay the trace in order: rules are fresh only if checked after the last
    # change to findings or vitals (a new extraction or a worker answer).
    rules_fresh, history_loaded = False, False
    for st in enc["steps"]:
        if st["phase"] == "act" and st["tool"] == "check_danger_signs":
            rules_fresh = True
        elif (st["phase"] == "act" and st["tool"] == "extract_findings") or st["tool"] == "worker_answer":
            rules_fresh = False
        elif st["phase"] == "act" and st["tool"] == "get_patient_history":
            history_loaded = True
    if rules_fresh:
        s.rules = protocols.evaluate(s.vitals, s.patient, s.findings)
    if history_loaded:
        TOOLS["get_patient_history"](s, {})
    if enc["handoffs"]:
        h = enc["handoffs"][0]
        s.referral = {"id": h["id"], "urgency": h["urgency"], "reason": h["reason"]}
    return s


def start_async(eid: str) -> None:
    """Run (or resume) an encounter's loop in a background thread."""
    with _running_lock:
        if eid in _running:
            return
        _running.add(eid)

    def target():
        try:
            run(load_state(eid))
        except Exception as err:  # never leave an encounter stuck in 'running'
            log.exception("agent crashed on %s", eid)
            store.add_step(eid, 999, "recover", None, {"message": f"Agent crashed: {err}. Visit handed to clinician."})
            store.update_encounter(eid, status="failed")
        finally:
            with _running_lock:
                _running.discard(eid)

    threading.Thread(target=target, daemon=True).start()


def answer(eid: str, text: str, resume: bool = True) -> None:
    """Record the health worker's answer to the pending question and (by default) resume."""
    s = load_state(eid)
    enc = store.get_encounter(eid)
    q = enc["question"] or ""
    field = next((st["detail"].get("field") for st in reversed(enc["steps"])
                  if st["phase"] == "act" and st["tool"] == "ask_health_worker"), None)
    field = field if field in VITAL_FIELDS else None
    s.answers.append({"question": q, "field": field, "answer": text})
    m = re.search(r"-?\d+(?:\.\d+)?", text) if field else None
    if m:
        value = float(m.group())
        if field == "temp_c" and value > 50:  # given in Fahrenheit
            value = round((value - 32) * 5 / 9, 1)
        s.vitals[field] = value
    else:
        s.findings = {}  # new free-text information: re-extract with the answer included
    store.add_step(eid, s.step, "sense", "worker_answer", {"question": q, "answer": text, "field": field})
    s.last_feedback = f"The health worker answered '{q}' with '{text}'. Re-check danger signs before deciding."
    store.update_encounter(eid, answers=s.answers, vitals=s.vitals, findings=s.findings, question=None, status="running")
    if resume:
        start_async(eid)


def resume_interrupted() -> list[str]:
    """Restart encounters left 'running' by a crash or power cut."""
    ids = [r["id"] for r in store.query("SELECT id FROM encounters WHERE status='running'")]
    for eid in ids:
        store.add_step(eid, 0, "recover", None, {"message": "Process restarted: resuming this visit from its last saved step."})
        start_async(eid)
    return ids
