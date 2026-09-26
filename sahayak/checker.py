"""
The CHECK step: a deterministic verifier that gates every tool call.

Gemma proposes; the checker disposes. Before a proposed action runs, `precheck`
may reject it with feedback the model sees on its next turn (so the loop
self-corrects). After `recommend_care`, `postcheck` enforces hard safety
boundaries the model cannot argue with:

  * Triage can only go UP: if the rules say RED, a GREEN/YELLOW plan is rejected.
  * `finish` is refused until findings were extracted, danger signs checked,
    critical missing information asked for, a plan drafted, and (for
    RED/YELLOW) a referral opened.
  * Low model confidence, or too many rejected proposals, forces a human
    handoff instead of letting the agent guess.

Returned verdicts: {"ok": bool, "feedback": str, "force": optional action}.
"""

from . import config, protocols
from .tools import TOOL_NAMES, AgentState

MAX_QUESTIONS = 3

# Unicode blocks the family's advice must be written in (not romanized "Tenglish"/"Hinglish").
SCRIPTS = {"Telugu": ((0x0C00, 0x0C7F), "Telugu (తెలుగు)"), "Hindi": ((0x0900, 0x097F), "Devanagari (देवनागरी)")}


def _script_share(text: str, block: tuple[int, int]) -> float:
    """Fraction of the letters in `text` that belong to the given Unicode block."""
    letters = [c for c in text if c.isalpha()]
    return sum(block[0] <= ord(c) <= block[1] for c in letters) / len(letters) if letters else 0.0


def unanswered(state: AgentState) -> list[dict]:
    """Critical missing vitals that have not already been asked about."""
    asked = {a.get("field") or "none" for a in state.answers}
    return [a for a in protocols.missing_critical(state.vitals, state.patient, state.findings)
            if a["field"] not in asked]


def open_items(state: AgentState) -> list[str]:
    """Checklist items not yet done, in the order the protocol expects them."""
    items = []
    if not state.findings:
        items.append("extract_findings")
    if state.rules is None:
        items.append("check_danger_signs")
    if state.history is None:
        items.append("get_patient_history")
    asks = unanswered(state)
    if asks and len(state.answers) < MAX_QUESTIONS:
        items.append(f"ask_health_worker(field={asks[0]['field']})")
    if not state.plan or (state.rules and not protocols.level_at_least(state.plan["triage"], state.rules["level"])):
        items.append("recommend_care")
    elif state.plan["triage"] in ("RED", "YELLOW") and not state.referral:
        items.append("refer_to_clinician")
    return items or ["finish"]


def precheck(state: AgentState, tool: str, args: dict) -> dict:
    """Validate a proposed tool call before it runs."""
    if tool not in TOOL_NAMES:
        return {"ok": False, "feedback": f"Unknown tool '{tool}'. Use one of {TOOL_NAMES}."}

    # No-progress guard: small models tend to repeat a finished step. Reject
    # repeats whose inputs have not changed, and say what is still open.
    if tool == "extract_findings" and state.findings:
        return {"ok": False, "feedback": f"Findings are already extracted and unchanged. Still open: {', '.join(open_items(state))}."}
    if tool == "check_danger_signs" and state.rules is not None:
        return {"ok": False, "feedback": f"Danger signs already checked (level {state.rules['level']}) and nothing changed. Still open: {', '.join(open_items(state))}."}
    if tool == "get_patient_history" and state.history is not None:
        return {"ok": False, "feedback": f"History already loaded. Still open: {', '.join(open_items(state))}."}
    if tool == "refer_to_clinician" and state.referral:
        return {"ok": False, "feedback": f"Referral already open. Still open: {', '.join(open_items(state))}."}
    if tool == "recommend_care" and state.plan and state.rules and protocols.level_at_least(state.plan["triage"], state.rules["level"]):
        return {"ok": False, "feedback": f"A valid plan already exists. Still open: {', '.join(open_items(state))}."}

    if tool == "ask_health_worker":
        if not (args.get("question") or "").strip():
            return {"ok": False, "feedback": "ask_health_worker needs a non-empty 'question'."}
        if len(state.answers) >= MAX_QUESTIONS:
            return {"ok": False, "feedback": "Question budget used up. Decide with what you have or refer."}

    if tool == "recommend_care":
        triage = args.get("triage")
        if triage not in protocols.LEVELS:
            return {"ok": False, "feedback": "recommend_care needs triage in GREEN/YELLOW/RED."}
        if state.rules is None:
            return {"ok": False, "feedback": "Run check_danger_signs before recommending care."}
        floor = state.rules["level"]
        if not protocols.level_at_least(triage, floor):
            reasons = "; ".join(f["reason"] for f in state.rules["flags"] if f["level"] == floor)
            return {"ok": False, "feedback":
                    f"REJECTED: protocol requires at least {floor} ({reasons}). You proposed {triage}. Triage can never be lowered below the rules."}
        if not args.get("advice_steps"):
            return {"ok": False, "feedback": "recommend_care needs concrete 'advice_steps'."}
        script = SCRIPTS.get(state.language)
        local = args.get("advice_local_language") or ""
        if script and not state.degraded and _script_share(local, script[0]) < 0.5:
            return {"ok": False, "feedback":
                    f"REJECTED: advice_local_language must be written in {state.language} using {script[1]} script "
                    f"(not English letters / transliteration). Rewrite it in {script[1]} script."}

    if tool == "finish":
        missing = []
        if not state.findings:
            missing.append("extract_findings")
        if state.rules is None:
            missing.append("check_danger_signs")
        asks = unanswered(state)
        if asks and len(state.answers) < MAX_QUESTIONS:
            missing.append(f"ask_health_worker with field={asks[0]['field']} (\"{asks[0]['question']}\")")
        if not state.plan:
            missing.append("recommend_care")
        elif state.rules and not protocols.level_at_least(state.plan["triage"], state.rules["level"]):
            missing.append(f"recommend_care again (plan {state.plan['triage']} is below new protocol level {state.rules['level']})")
        elif state.plan["triage"] in ("RED", "YELLOW") and not state.referral:
            missing.append("refer_to_clinician (triage is " + state.plan["triage"] + ")")
        if missing:
            return {"ok": False, "feedback": "Cannot finish yet. Still required: " + ", ".join(missing)}

    return {"ok": True, "feedback": ""}


def postcheck(state: AgentState, tool: str, result: dict) -> dict:
    """Enforce handoff boundaries after a tool ran."""
    if tool == "recommend_care":
        conf = state.plan.get("confidence")
        if conf is not None and conf < config.HANDOFF_CONFIDENCE and not state.referral:
            return {"ok": True, "force": "refer_to_clinician",
                    "reason": f"Low model confidence ({conf:.2f}) on {state.plan.get('triage')} triage: clinician review. {state.plan.get('rationale', '')}",
                    "feedback": f"Model confidence {conf:.2f} < {config.HANDOFF_CONFIDENCE}: handing off to a clinician."}
    if tool == "check_danger_signs" and result["level"] == "RED" and not state.referral:
        reasons = "; ".join(f["reason"] for f in result["flags"] if f["level"] == "RED")
        return {"ok": True, "force": "refer_to_clinician", "reason": f"Emergency: {reasons}",
                "feedback": f"RED danger sign ({reasons}): emergency referral opened immediately, before any further reasoning."}
    return {"ok": True, "feedback": ""}
