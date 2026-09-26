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

from . import llm
from .log import get_logger, logged
from .tools import VITAL_FIELDS

log = get_logger("intake")

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"]},
        "age_years": {"type": ["number", "null"]},
        "sex": {"type": ["string", "null"], "enum": ["F", "M", None]},
        "pregnant": {"type": "boolean"},
        "vitals": {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in VITAL_FIELDS}},
        "clinical_note": {"type": "string"},
        "missing": {"type": "array", "items": {"type": "string", "enum": ["age", "complaint"]}},
        "next_question": {"type": "string"},
    },
    "required": ["name", "age_years", "sex", "pregnant", "vitals", "clinical_note", "missing", "next_question"],
}

SYSTEM = (
    "You are the intake step of an offline voice assistant for a community health worker in India. "
    "From everything the worker has said (English, Hindi, Telugu or mixed), fill the visit form. Never invent.\n"
    "- name: patient's name if said ('This is Lakshmi', 'patient Ravi', 'Riya naam hai' -> the name), else null. age_years: number ('she is 24' = 24, '2 saal ka' = 2, '8 months' = 0.67), else null.\n"
    "- sex: F or M if said or obvious (pregnant = F), else null. pregnant: true only if said.\n"
    "- vitals: temp_c (convert Fahrenheit), hr (pulse), rr (breaths per minute), spo2, sbp/dbp (BP '150 over 100'), else null.\n"
    "- clinical_note: a clean, complete description of the illness in the worker's words (symptoms, duration, what was "
    "seen), without the name/age/vitals already captured.\n"
    "- missing: 'age' if age unknown, 'complaint' if no illness described yet.\n"
    "- next_question: ONE short friendly spoken question (English) about THE PATIENT (third person, e.g. 'How old is the "
    "patient?') asking for the FIRST item in `missing`, else ''."
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
    form, model = llm.chat_json(SYSTEM, f"What the worker said so far:\n{transcript}", SCHEMA)
    form["vitals"] = {k: v for k, v in (form.get("vitals") or {}).items() if v is not None and k in VITAL_FIELDS}
    if form.get("age_years") is None:  # backup for plainly spoken ages the model skipped
        age = _spoken_age(transcript)
        if age is not None:
            form["age_years"] = age
            form["missing"] = [m for m in form.get("missing", []) if m != "age"]
            if not form["missing"]:
                form["next_question"] = ""
    form["model"] = model
    return form
