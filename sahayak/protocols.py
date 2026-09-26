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

# Plain-language definitions shown to Gemma so it can map everyday phrasing
# (in any language) onto each code. Small models miss signs given bare codes.
DANGER_SIGN_DEFS = {
    "unconscious_or_lethargic": "unconscious, fainted, very sleepy, hard to wake, not responding, floppy (behosh, uth nahi raha, స్పృహ లేదు)",
    "convulsions": "fits, seizures, jerking of body, eyes rolling (jhatke, daura, మూర్ఛ)",
    "unable_to_drink_or_feed": "cannot or will not drink or breastfeed AT ALL, refusing every feed or all fluids (pee nahi raha, doodh nahi pi raha, పాలు తాగడం లేదు). Eating less, poor appetite or 'not eating well' is NOT this sign: answer no (or unclear if drinking is not mentioned)",
    "vomits_everything": "vomits after every feed or drink, cannot keep anything down",
    "chest_indrawing": "lower chest pulls in when breathing in, ribs sucking in",
    "severe_bleeding": "heavy bleeding that soaks cloth, bleeding that does not stop",
    "pregnancy_bleeding": "any bleeding from the vagina during pregnancy",
    "severe_headache_blurred_vision": "a headache explicitly described as severe/worst ever/unbearable, OR blurred/double vision or seeing spots. An ordinary headache (headache, sir dard, తలనొప్పి) is only a symptom: answer no",
    "stiff_neck": "cannot bend neck forward, neck stiffness with fever",
    "snake_or_animal_bite": "snake bite, dog bite, scorpion sting, any animal bite",
    "suicidal_thoughts": "talks of ending life, self-harm, wanting to die, hopelessness with such talk",
    "chest_pain": "pain or pressure in the chest, may spread to arm, jaw or back",
    "difficulty_breathing": "breathless, struggling or fast breathing, gasping, wheeze (saans phoolna, ఆయాసం)",
    "severe_dehydration": "sunken eyes, very thirsty or too weak to drink, skin pinch goes back slowly, very little urine",
    "blood_in_stool": "blood in stool or black stool",
}

# Keyword fallback: pattern -> danger sign or symptom. Covers English and a
# few common Hinglish words a health worker might type.
_SIGN_WORDS = {
    r"unconscious|lethargic|not waking|hard to wake|difficult to wake|not responding|unresponsive|fainted|behosh|drowsy|very sleepy|floppy|uth nahi": "unconscious_or_lethargic",
    r"convuls|seizure|fits?\b|jhatke|daura": "convulsions",
    r"not (able to )?(drink|feed|breastfeed|eat)|unable to (drink|feed|eat)|(not|n't) (taken|taking|take) (any )?(breast ?milk|milk|feeds?|fluids?|water)|refus\w* (to )?(feed|drink|milk|breast)|pee nahi|pi nahi|doodh nahi": "unable_to_drink_or_feed",
    r"vomit(s|ing)? everything|vomits after every|cannot keep (anything|food|water) down": "vomits_everything",
    r"chest indrawing|indrawing": "chest_indrawing",
    r"heavy bleeding|severe bleeding|bleeding heavily|soaking|won'?t stop bleeding": "severe_bleeding",
    r"blurred vision|severe headache": "severe_headache_blurred_vision",
    r"stiff neck|neck stiff": "stiff_neck",
    r"snake|dog bite|animal bite": "snake_or_animal_bite",
    r"suicid|kill (him|her|my)self|end (his|her|my) life|wants? to die|self[- ]harm": "suicidal_thoughts",
    r"chest pain|seene me dard": "chest_pain",
    r"breathless|difficulty breathing|short(ness)? of breath|saans": "difficulty_breathing",
    r"sunken eyes|very thirsty|dehydrat": "severe_dehydration",
    r"blood in stool|bloody stool": "blood_in_stool",
    # Devanagari (Hindi) and Telugu script, e.g. from voice transcripts
    r"बेहोश|होश नहीं|స్పృహ లేదు|స్పృహ తప్పి": "unconscious_or_lethargic",
    r"दौरा|झटके|ఫిట్స్|మూర్ఛ": "convulsions",
    r"पी नहीं|दूध नहीं पी|తాగడం లేదు|పాలు తాగడం లేదు": "unable_to_drink_or_feed",
    r"सांस लेने में|साँस फूल|ఆయాసం|శ్వాస తీసుకోవడం కష్టం": "difficulty_breathing",
    r"सीने में दर्द|छाती में दर्द|ఛాతీ నొప్పి": "chest_pain",
    r"सांप|పాము కాటు|పాము కరిచ": "snake_or_animal_bite",
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
    r"बुखार|జ్వరం": "fever",
    r"खांसी|खाँसी|దగ్గు": "cough",
    r"दस्त|విరేచనాలు": "diarrhoea",
    r"उल्टी|వాంతు": "vomiting",
}


# Readings outside these ranges are almost certainly measurement errors.
PLAUSIBLE = {"temp_c": (32, 43), "hr": (30, 250), "rr": (5, 100), "spo2": (50, 100), "sbp": (50, 260), "dbp": (30, 160)}
VITAL_LABELS = {"temp_c": "temperature", "hr": "heart rate", "rr": "breathing rate", "spo2": "SpO2", "sbp": "systolic BP", "dbp": "diastolic BP"}


def implausible(vitals: dict) -> list[str]:
    """Vital fields whose value is outside the physiologically plausible range."""
    return [k for k, (lo, hi) in PLAUSIBLE.items() if vitals.get(k) is not None and not lo <= vitals[k] <= hi]


def is_vague(findings: dict) -> bool:
    """True if the note gives too little to triage on (≤1 symptom, no danger sign, no duration)."""
    return (bool(findings) and len(findings.get("symptoms", [])) <= 1
            and not findings.get("danger_signs") and findings.get("duration_days") is None)


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

    # Physiologically implausible readings are not trusted: they raise a
    # YELLOW "unreliable reading" flag and the agent must ask for a re-measure.
    bad = implausible(vitals)
    for k in bad:
        flag("unreliable_" + k, "YELLOW", f"Unreliable reading {VITAL_LABELS[k]}={vitals[k]} (outside {PLAUSIBLE[k][0]}–{PLAUSIBLE[k][1]}): re-measure")
    t, hr, rr, spo2, sbp, dbp = (None if k in bad else vitals.get(k) for k in ("temp_c", "hr", "rr", "spo2", "sbp", "dbp"))

    if spo2 is not None:
        if spo2 < 90:
            flag("low_spo2", "RED", f"SpO2 {spo2}% < 90%")
        elif spo2 < 94:
            flag("low_spo2", "YELLOW", f"SpO2 {spo2}% < 94%")
    if t is not None:
        if age is not None and age < 2 / 12 and t >= 37.5:
            flag("infant_fever", "RED", f"Fever {t}°C in infant under 2 months")
        elif t >= 41:
            flag("hyperpyrexia", "RED", f"Temperature {t}°C ≥ 41°C")
        elif t >= 39.5:
            flag("high_fever", "YELLOW", f"Temperature {t}°C ≥ 39.5°C")
        if t < 35.0:
            flag("hypothermia", "RED", f"Temperature {t}°C < 35.0°C (hypothermia)")
        elif t < 36.0:
            flag("low_temperature", "YELLOW", f"Temperature {t}°C below normal (35.0–35.9°C)")
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
            if sign == "severe_headache_blurred_vision" and not pregnant:
                level = "YELLOW"  # RED in pregnancy (pre-eclampsia); otherwise needs a clinician, not an ambulance
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
    asks = [{"field": k, "kind": "remeasure",
             "question": f"The {VITAL_LABELS[k]} reading {vitals[k]} looks wrong. Please measure it again and enter the new value."}
            for k in implausible(vitals)]
    age = patient.get("age_years")
    symptoms = set(findings.get("symptoms", []))
    breathing = "cough" in symptoms or "difficulty_breathing" in findings.get("danger_signs", [])
    if age is not None and age < 5 and breathing and vitals.get("rr") is None:
        asks.append({"field": "rr", "question": "Count the child's breaths for one full minute. What is the breathing rate?"})
    if patient.get("pregnant") and vitals.get("sbp") is None:
        asks.append({"field": "sbp", "question": "Pregnant patient: please measure the blood pressure. What is the systolic (upper) value?"})
    if "fever" in symptoms and vitals.get("temp_c") is None:
        asks.append({"field": "temp_c", "question": "Please measure the temperature. What is it in °C?"})
    if is_vague(findings):
        asks.append({"field": "none", "kind": "clarify",
                     "question": "Since when has this been going on, and are there other problems (fever, cough, vomiting, diarrhoea, pain, not eating or drinking, bleeding)?"})
    return asks


# Signs whose patterns already express a negation ("not drinking", "uth nahi")
# must not be cancelled by the negation check below.
_SELF_NEGATING = {"unable_to_drink_or_feed", "unconscious_or_lethargic"}
_NEG_BEFORE = re.compile(r"\b(no|not|denies|denied|without|never|nil|absent)\b[^.;,]{0,25}$")
_NEG_AFTER = re.compile(r"^[^.;,]{0,12}\b(nahi|nahin|nai)\b|^[^.;,]{0,12}(नहीं|లేదు)")


def _mentioned(pattern: str, text: str, self_negating: bool = False) -> bool:
    """True if the pattern occurs in the text at least once without being negated.

    Handles English pre-negation ("no chest pain", "denies vomiting") and
    Hindi/Telugu post-negation ("saans nahi phool rahi", "జ్వరం లేదు").
    """
    for m in re.finditer(pattern, text):
        if self_negating:
            return True
        if _NEG_BEFORE.search(text[max(0, m.start() - 30):m.start()]) or _NEG_AFTER.search(text[m.end():m.end() + 20]):
            continue
        return True
    return False


def keyword_extract(note: str) -> dict:
    """Model-free, negation-aware extraction of symptoms, danger signs and duration."""
    text = note.lower()
    signs = sorted({v for k, v in _SIGN_WORDS.items() if _mentioned(k, text, v in _SELF_NEGATING)})
    symptoms = sorted({v for k, v in _SYMPTOM_WORDS.items() if _mentioned(k, text)})
    dur = None
    m = re.search(r"(\d+)\s*(day|din|दिन|రోజు)", text)
    if m:
        dur = int(m.group(1))
    elif re.search(r"(\d+)\s*week", text):
        dur = 7 * int(re.search(r"(\d+)\s*week", text).group(1))
    elif re.search(r"yesterday|last night|kal se|kal raat", text):
        dur = 1
    elif re.search(r"today|this morning|since morning|aaj|subah", text):
        dur = 0
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
