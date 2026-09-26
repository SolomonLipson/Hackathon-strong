"""
The agent's tools (the ACT step).

Gemma chooses one tool per iteration; this module executes it against local
state. Every tool is offline: it reads/writes SQLite, runs the deterministic
protocol rules, or makes a local Gemma call. Tools:

  extract_findings     SENSE: turn the free-text note into structured findings
                       (Gemma 4 with a JSON schema; keyword fallback offline)
  check_danger_signs   run the deterministic danger-sign rules
  get_patient_history  read this patient's previous visits from local storage
  ask_health_worker    pause the loop and ask the human a question
  recommend_care       draft triage level + advice (+ local-language version)
  refer_to_clinician   HUMAN HANDOFF: open a referral and queue it for sync
  finish               close the encounter (checker must approve)

`AgentState` is the working memory for one encounter. It is rebuilt from the
database on resume, so a crash never loses a visit.
"""

import time
from dataclasses import dataclass, field

from . import llm, protocols, store
from .log import get_logger, logged

log = get_logger("tools")

TOOL_NAMES = [
    "extract_findings", "check_danger_signs", "get_patient_history",
    "ask_health_worker", "recommend_care", "refer_to_clinician", "finish",
]
VITAL_FIELDS = ["temp_c", "hr", "rr", "spo2", "sbp", "dbp"]


@dataclass
class AgentState:
    """Working memory of one encounter; persisted after every step."""
    encounter_id: str
    patient: dict
    note: str
    vitals: dict
    language: str
    findings: dict = field(default_factory=dict)
    rules: dict | None = None
    history: list | None = None
    answers: list = field(default_factory=list)       # [{question, field, answer}]
    pending_question: dict | None = None
    plan: dict = field(default_factory=dict)
    referral: dict | None = None
    done: bool = False
    degraded: bool = False
    step: int = 0
    rejections: int = 0
    last_feedback: str = ""


# --- SENSE: extraction ----------------------------------------------------------

# Checklist extraction: Gemma must rule each danger sign in or out, with a
# quote from the note as evidence. Forcing a verdict per sign (instead of
# "list the danger signs") markedly improves recall on small on-device models.
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "danger_sign_checklist": {
            "type": "object",
            "properties": {code: {"type": "string", "enum": ["yes", "no", "unclear"]} for code in protocols.DANGER_SIGNS},
            "required": list(protocols.DANGER_SIGNS),
        },
        "evidence": {
            "type": "array",
            "items": {"type": "object", "properties": {"code": {"type": "string"}, "quote": {"type": "string"}},
                      "required": ["code", "quote"]},
        },
        "symptoms": {"type": "array", "items": {"type": "string"}},
        "negated": {"type": "array", "items": {"type": "string"}},
        "duration_days": {"type": ["number", "null"]},
        "vitals_in_note": {
            "type": "object",
            "properties": {k: {"type": ["number", "null"]} for k in VITAL_FIELDS},
        },
        "summary": {"type": "string"},
    },
    "required": ["danger_sign_checklist", "evidence", "symptoms", "negated", "duration_days", "vitals_in_note", "summary"],
}

EXTRACT_SYSTEM = (
    "You are the clinical reader for a community health worker in India. The note may be English, Hindi, Telugu, "
    "Hinglish or a mix, in any script, and may come from speech transcription. Read it carefully and literally; "
    "never invent facts.\n"
    "1. danger_sign_checklist: for EVERY sign answer yes (clearly described, even in everyday words), no (absent or "
    "explicitly denied) or unclear (hinted but not certain). Definitions:\n"
    + "\n".join(f"   - {code}: {desc}" for code, desc in protocols.DANGER_SIGN_DEFS.items())
    + "\n2. evidence: for each 'yes' or 'unclear' sign, the exact words from the note.\n"
    "3. symptoms: short lowercase English words for symptoms that ARE present (fever, cough, diarrhoea, vomiting, "
    "headache, abdominal pain, rash, weakness, ...). Put denied symptoms ('no vomiting', 'bukhar nahi') in negated.\n"
    "4. duration_days: how long the illness has lasted in days (yesterday=1, since morning=0), else null.\n"
    "5. vitals_in_note: numbers stated in the note (temp_c in Celsius; convert Fahrenheit), else null.\n"
    "6. summary: one clear English sentence."
)


def _merge_note_vitals(state: AgentState, found: dict) -> list[str]:
    """Copy vitals mentioned in the note into empty vitals fields."""
    added = []
    for k, v in (found or {}).items():
        if k in VITAL_FIELDS and v is not None and state.vitals.get(k) is None:
            if k == "temp_c" and v > 50:  # Fahrenheit slipped through
                v = round((v - 32) * 5 / 9, 1)
            state.vitals[k] = v
            added.append(k)
    return added


@logged(log)
def extract_findings(state: AgentState, args: dict) -> dict:
    """SENSE: structured findings from the note via Gemma, keyword fallback."""
    patient = {k: state.patient.get(k) for k in ("age_years", "sex", "pregnant")}
    user = f"Patient: {patient}\nNote: {state.note}"
    if state.answers:
        user += f"\nFollow-up answers: {state.answers}"
    model = None
    text = state.note + " " + " ".join(a["answer"] for a in state.answers)
    try:
        raw, model = llm.chat_json(EXTRACT_SYSTEM, user, EXTRACT_SCHEMA)
        checklist = dict(raw.get("danger_sign_checklist") or {})
        # Grounding check: a "yes" whose quoted evidence is not in the note is
        # treated as unclear (the keyword net still backs up real mentions).
        quotes = {e.get("code"): (e.get("quote") or "").strip().lower() for e in raw.get("evidence") or []}
        for code, verdict in checklist.items():
            q = quotes.get(code, "")
            if verdict == "yes" and q and q.strip('"“”. ') not in text.lower():
                checklist[code] = "unclear"
        found = {
            "symptoms": raw.get("symptoms") or [],
            "danger_signs": [c for c, v in checklist.items() if v == "yes" and c in protocols.DANGER_SIGNS],
            "unclear_signs": [c for c, v in checklist.items() if v == "unclear" and c in protocols.DANGER_SIGNS],
            "evidence": raw.get("evidence") or [],
            "negated": raw.get("negated") or [],
            "duration_days": raw.get("duration_days"),
            "vitals_in_note": raw.get("vitals_in_note") or {},
            "summary": raw.get("summary", ""),
            "source": model,
        }
    except llm.LLMUnavailable:
        found = protocols.keyword_extract(text)
        state.degraded = True
    # Safety net: keyword-detected danger signs are always kept, even if the model missed them.
    kw = protocols.keyword_extract(text)
    missed = sorted(set(kw["danger_signs"]) - set(found.get("danger_signs", [])))
    found["danger_signs"] = sorted(set(found.get("danger_signs", [])) | set(kw["danger_signs"]))
    found["symptoms"] = sorted((set(found.get("symptoms", [])) | set(kw["symptoms"])) - set(found.get("negated", [])))
    if found.get("duration_days") is None:
        found["duration_days"] = kw["duration_days"]
    added = _merge_note_vitals(state, found.pop("vitals_in_note", {}))
    state.findings = found
    state.rules = None  # findings changed: rules must be re-run
    return {"findings": found, "vitals_added_from_note": added, "keyword_safety_net_added": missed, "model": model}


@logged(log)
def check_danger_signs(state: AgentState, args: dict) -> dict:
    """Run the deterministic danger-sign rules on current vitals + findings."""
    state.rules = protocols.evaluate(state.vitals, state.patient, state.findings)
    return state.rules


@logged(log)
def get_patient_history(state: AgentState, args: dict) -> dict:
    """Load previous visits of this patient from the local store."""
    rows = store.patient_history(state.patient["id"], exclude=state.encounter_id)
    state.history = [
        {"when": r["created_at"], "triage": r["triage"],
         "summary": (r.get("findings") or {}).get("summary"),
         "follow_up": (r.get("plan") or {}).get("followup_days")}
        for r in rows
    ]
    return {"previous_visits": state.history}


@logged(log)
def ask_health_worker(state: AgentState, args: dict) -> dict:
    """Pause the loop and ask the health worker a question (human in the loop)."""
    q = (args.get("question") or "").strip()
    fld = args.get("field") if args.get("field") in VITAL_FIELDS else None
    state.pending_question = {"question": q, "field": fld}
    return {"asked": q, "field": fld, "status": "waiting_for_worker"}


@logged(log)
def recommend_care(state: AgentState, args: dict) -> dict:
    """Draft the care plan: triage level, advice steps, follow-up, confidence."""
    state.plan = {
        "triage": args.get("triage"),
        "advice_steps": args.get("advice_steps") or [],
        "advice_local_language": args.get("advice_local_language") or "",
        "followup_days": args.get("followup_days"),
        "confidence": args.get("confidence"),
        "rationale": args.get("rationale") or "",
    }
    return {"plan": state.plan}


@logged(log)
def refer_to_clinician(state: AgentState, args: dict) -> dict:
    """HUMAN HANDOFF: open a referral in the local queue and the sync outbox."""
    if state.referral:
        return {"referral": state.referral, "note": "already referred"}
    hid = store.new_id()
    urgency = args.get("urgency") or state.plan.get("triage") or "YELLOW"
    reason = args.get("reason") or "Agent requested clinician review"
    store.execute(
        "INSERT INTO handoffs (id,encounter_id,urgency,reason,status,created_at) VALUES (?,?,?,?,'open',?)",
        (hid, state.encounter_id, urgency, reason, time.time()),
    )
    state.referral = {"id": hid, "urgency": urgency, "reason": reason}
    store.enqueue("referral", f"referral:{hid}", {
        "encounter_id": state.encounter_id, "patient": state.patient, "vitals": state.vitals,
        "findings": state.findings, "urgency": urgency, "reason": reason,
    })
    return {"referral": state.referral, "queued_for_sync": True}


@logged(log)
def finish(state: AgentState, args: dict) -> dict:
    """Close the encounter; the checker has already approved this call."""
    state.done = True
    return {"summary": args.get("summary", "")}


TOOLS = {
    "extract_findings": extract_findings,
    "check_danger_signs": check_danger_signs,
    "get_patient_history": get_patient_history,
    "ask_health_worker": ask_health_worker,
    "recommend_care": recommend_care,
    "refer_to_clinician": refer_to_clinician,
    "finish": finish,
}
