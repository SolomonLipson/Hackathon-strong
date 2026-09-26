"""
Deterministic clinical safety rules: the agent's "check" step and its safety net.

Gemma decides and explains; these rules verify. They encode danger-sign logic
loosely inspired by WHO IMCI / ASHA-worker referral guidance (DEMO ONLY, not
validated clinical advice). They are deliberately simple, auditable and
offline, and they are never overruled by the model:

  * evaluate(): turns vitals + demographics + extracted danger signs into a
    list of red/yellow flags. The highest flag sets the minimum triage level.
  * missing_critical(): information the agent must ask the worker for before
    it is allowed to finish (e.g. breathing rate for a coughing child).
  * keyword_extract(): a regex/keyword extractor (English + common Hinglish)
    used when no local model is available, so the agent still works degraded.

Triage levels, most to least urgent: RED (refer now), YELLOW (see a clinician
within 24 h), GREEN (home care + follow-up).
"""

import re

LEVELS = ["GREEN", "YELLOW", "RED"]

# Danger signs the SENSE step may extract from free text -> (level, label).
DANGER_SIGNS = {
    "unconscious_or_lethargic": ("RED", "Unconscious or unusually sleepy"),
    "convulsions": ("RED", "Convulsions / fits"),
    "unable_to_drink_or_feed": ("RED", "Unable to drink or breastfeed"),
    "vomits_everything": ("RED", "Vomits everything"),
    "chest_indrawing": ("RED", "Chest indrawing"),
    "severe_bleeding": ("RED", "Severe bleeding"),
    "pregnancy_bleeding": ("RED", "Bleeding in pregnancy"),
    "severe_headache_blurred_vision": ("RED", "Severe headache / blurred vision"),
    "stiff_neck": ("RED", "Stiff neck"),
    "snake_or_animal_bite": ("RED", "Snake or animal bite"),
    "suicidal_thoughts": ("RED", "Thoughts of self-harm"),
    "chest_pain": ("YELLOW", "Chest pain"),
    "difficulty_breathing": ("YELLOW", "Difficulty breathing"),
    "severe_dehydration": ("YELLOW", "Signs of dehydration"),
    "blood_in_stool": ("YELLOW", "Blood in stool"),
}

# Keyword fallback: pattern -> danger sign or symptom. Covers English and a
# few common Hinglish words a health worker might type.
_SIGN_WORDS = {
    r"unconscious|lethargic|not waking|behosh|drowsy": "unconscious_or_lethargic",
    r"convuls|seizure|fits?\b|jhatke|daura": "convulsions",
    r"not (able to )?(drink|feed|breastfeed)|unable to (drink|feed)": "unable_to_drink_or_feed",
    r"vomit(s|ing)? everything": "vomits_everything",
    r"chest indrawing|indrawing": "chest_indrawing",
    r"heavy bleeding|severe bleeding|bleeding heavily": "severe_bleeding",
    r"blurred vision|severe headache": "severe_headache_blurred_vision",
    r"stiff neck": "stiff_neck",
    r"snake|dog bite|animal bite": "snake_or_animal_bite",
    r"suicid|kill (him|her|my)self": "suicidal_thoughts",
    r"chest pain|seene me dard": "chest_pain",
    r"breathless|difficulty breathing|short(ness)? of breath|saans": "difficulty_breathing",
    r"sunken eyes|very thirsty|dehydrat": "severe_dehydration",
    r"blood in stool|bloody stool": "blood_in_stool",
}
_SYMPTOM_WORDS = {
    r"fever|bukhar|temperature": "fever",
    r"cough|khansi": "cough",
    r"diarrh|loose motion|dast": "diarrhoea",
    r"vomit|ulti": "vomiting",
    r"headache|sir dard": "headache",
    r"rash": "rash",
    r"pain": "pain",
    r"bleed": "bleeding",
}


def _raise(current: str, new: str) -> str:
    """Return the more urgent of two triage levels."""
    return max(current, new, key=LEVELS.index)


def evaluate(vitals: dict, patient: dict, findings: dict) -> dict:
    """
    Apply danger-sign rules. Returns {"level": minimum triage level,
    "flags": [{"code","level","reason"}]}.
    """
    flags = []
    age = patient.get("age_years")
    pregnant = bool(patient.get("pregnant"))

    def flag(code, level, reason):
        flags.append({"code": code, "level": level, "reason": reason})

    t, hr, rr = vitals.get("temp_c"), vitals.get("hr"), vitals.get("rr")
    spo2, sbp, dbp = vitals.get("spo2"), vitals.get("sbp"), vitals.get("dbp")

    if spo2 is not None:
        if spo2 < 90:
            flag("low_spo2", "RED", f"SpO2 {spo2}% < 90%")
        elif spo2 < 94:
            flag("low_spo2", "YELLOW", f"SpO2 {spo2}% < 94%")
    if t is not None:
        if age is not None and age < 2 / 12 and t >= 37.5:
            flag("infant_fever", "RED", f"Fever {t}°C in infant under 2 months")
        elif t >= 39.5:
            flag("high_fever", "YELLOW", f"Temperature {t}°C ≥ 39.5°C")
        if t < 35.5:
            flag("hypothermia", "RED", f"Temperature {t}°C < 35.5°C")
    if rr is not None and age is not None:
        if age < 1 and rr >= 50:
            flag("fast_breathing", "YELLOW", f"RR {rr}/min ≥ 50 (age < 1 y)")
        elif 1 <= age < 5 and rr >= 40:
            flag("fast_breathing", "YELLOW", f"RR {rr}/min ≥ 40 (age 1–5 y)")
        elif age >= 12 and rr >= 30:
            flag("fast_breathing", "RED", f"RR {rr}/min ≥ 30")
        elif age >= 12 and rr >= 22:
            flag("fast_breathing", "YELLOW", f"RR {rr}/min ≥ 22")
    if hr is not None and (age is None or age >= 12):
        if hr > 130:
            flag("tachycardia", "RED", f"Heart rate {hr} > 130")
        elif hr > 110:
            flag("tachycardia", "YELLOW", f"Heart rate {hr} > 110")
    if sbp is not None and sbp < 90 and (age is None or age >= 12):
        flag("hypotension", "RED", f"Systolic BP {sbp} < 90")
    if sbp is not None or dbp is not None:
        s, d = sbp or 0, dbp or 0
        bp = f"{sbp if sbp is not None else '?'}/{dbp if dbp is not None else '?'}"
        if pregnant and (s >= 160 or d >= 110):
            flag("severe_pre_eclampsia_bp", "RED", f"BP {bp} in pregnancy")
        elif pregnant and (s >= 140 or d >= 90):
            flag("pregnancy_hypertension", "YELLOW", f"BP {bp} in pregnancy")
        elif s >= 180 or d >= 120:
            flag("hypertensive_crisis", "RED", f"BP {bp}")

    for sign in findings.get("danger_signs", []):
        if sign in DANGER_SIGNS:
            level, label = DANGER_SIGNS[sign]
            if sign == "chest_pain" and (age or 0) >= 35:
                level = "RED"
            if sign == "difficulty_breathing" and age is not None and age < 5:
                level = "RED"
            flag(sign, level, label)

    dur = findings.get("duration_days")
    if dur and dur >= 7 and "fever" in findings.get("symptoms", []):
        flag("prolonged_fever", "YELLOW", f"Fever for {dur} days")

    level = "GREEN"
    for f in flags:
        level = _raise(level, f["level"])
    return {"level": level, "flags": flags}


def missing_critical(vitals: dict, patient: dict, findings: dict) -> list[dict]:
    """Missing vitals the worker must provide before the agent may finish: [{field, question}]."""
    asks = []
    age = patient.get("age_years")
    symptoms = set(findings.get("symptoms", []))
    breathing = "cough" in symptoms or "difficulty_breathing" in findings.get("danger_signs", [])
    if age is not None and age < 5 and breathing and vitals.get("rr") is None:
        asks.append({"field": "rr", "question": "Count the child's breaths for one full minute. What is the breathing rate?"})
    if patient.get("pregnant") and vitals.get("sbp") is None:
        asks.append({"field": "sbp", "question": "Pregnant patient: please measure the blood pressure. What is the systolic (upper) value?"})
    if "fever" in symptoms and vitals.get("temp_c") is None:
        asks.append({"field": "temp_c", "question": "Please measure the temperature. What is it in °C?"})
    return asks


def keyword_extract(note: str) -> dict:
    """Model-free extraction of symptoms, danger signs and duration from text."""
    text = note.lower()
    signs = sorted({v for k, v in _SIGN_WORDS.items() if re.search(k, text)})
    symptoms = sorted({v for k, v in _SYMPTOM_WORDS.items() if re.search(k, text)})
    dur = None
    m = re.search(r"(\d+)\s*(day|din)", text)
    if m:
        dur = int(m.group(1))
    elif re.search(r"(\d+)\s*week", text):
        dur = 7 * int(re.search(r"(\d+)\s*week", text).group(1))
    return {
        "symptoms": symptoms,
        "danger_signs": signs,
        "duration_days": dur,
        "summary": note[:200],
        "source": "keyword-fallback",
    }


def level_at_least(level: str, minimum: str) -> bool:
    """True if `level` is as urgent as `minimum` or more."""
    return LEVELS.index(level) >= LEVELS.index(minimum)
