"""
Conversational intake: turn what the health worker SAID into a visit form.

Used by the "Talk to Sahayak" voice mode. The worker describes the patient in
free speech ("This is Lakshmi, 24, seven months pregnant, bad headache since
morning, BP was 150 over 100"); Gemma 4 (local) extracts the structured
fields the agent needs and says what is still missing, so the voice agent can
ask for it out loud. Everything the worker has said so far is sent each turn,
so later sentences can correct earlier ones.
"""

import re

from . import config, llm
from .log import get_logger, logged
from .tools import VITAL_FIELDS

log = get_logger("intake")

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"]},
        "age_years": {"type": ["number", "null"]},
        "sex": {"type": ["string", "null"], "enum": ["F", "M", "O", None]},
        "pregnant": {"type": "boolean"},
        "vitals": {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in VITAL_FIELDS}},
        "clinical_note": {"type": "string"},

    },
    "required": ["name", "age_years", "sex", "pregnant", "vitals", "clinical_note"],
}

SYSTEM = (
    "You are the intake step of an offline voice assistant for a community health worker in India. "
    "The transcript is a spoken dialogue: 'Sahayak:' lines are the assistant's questions, 'Worker:' lines are the answers "
    "(English, Hindi, Telugu or mixed). A short answer refers to the question just before it (Sahayak: 'What is the "
    "patient's name?' Worker: 'Solomon' -> name Solomon). Fill the visit form from everything said. Never invent.\n"
    "- name: patient's name if said ('This is Lakshmi', 'patient Ravi', 'Riya naam hai' -> the name), else null. age_years: number ('she is 24' = 24, '2 saal ka' = 2, '8 months' = 0.67), else null.\n"
    "- sex: F (female/woman/girl), M (male/man/boy), O (transgender, non-binary or any other answer), else null. pregnant: true only if said.\n"
    "- vitals: temp_c (convert Fahrenheit), hr (pulse), rr (breaths per minute), spo2, sbp/dbp (BP '150 over 100'), else null.\n"
    "- clinical_note: a clean, complete description of the illness in the worker's words (symptoms, duration, what was "
    "seen), without the name/age/vitals. Empty string if no illness has been described yet. Never copy these "
    "instructions or invent symptoms."
)


def _spoken_age(text: str) -> float | None:
    """Find an age stated as '24 years', '2 saal', '8 months', 'she is 24', 'aged 30'."""
    t = text.lower()
    m = re.search(r"(\d{1,3}(?:\.\d)?)\s*(?:-|\s)?(years?|yrs?|year[- ]old|saal|sal|ఏళ్ల|సంవత్సరాల)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{1,2})\s*(?:-|\s)?(months?\b|mahine|నెలల)(?!\s*(?:of\s+)?(?:pregnan|garbh))", t)
    if m:
        return round(int(m.group(1)) / 12, 2)
    m = re.search(r"\b(?:is|aged|age)\s+(\d{1,3})\b(?!\s*(?:°|degree|over|/|bpm|%|months?))", t)
    return float(m.group(1)) if m and 0 < int(m.group(1)) < 110 else None


@logged(log)
def extract(transcript: str) -> dict:
    """Structured visit form from the worker's spoken description. Raises llm.LLMUnavailable."""
    form, model = llm.chat_json(SYSTEM, f"What the worker said so far:\n{transcript}", SCHEMA,
                                models=[config.FAST_MODEL, config.PRIMARY_MODEL])
    form["vitals"] = {k: v for k, v in (form.get("vitals") or {}).items() if v is not None and k in VITAL_FIELDS}
    if form.get("age_years") is None:  # backup for plainly spoken ages the model skipped
        form["age_years"] = _spoken_age(transcript)
    if form.get("pregnant"):
        form["sex"] = "F"
    # What is still missing is decided by code, not by the model, so the
    # conversation never starts a visit without the essentials.
    note = (form.get("clinical_note") or "").strip()
    form["missing"] = [field for field, absent in (
        ("name", not form.get("name")),
        ("age", form.get("age_years") is None),
        ("sex", form.get("sex") not in ("F", "M", "O")),
        ("complaint", len(note) < 8),
    ) if absent]
    form["model"] = model
    return form
