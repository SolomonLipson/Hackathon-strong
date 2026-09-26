"""
Record the demo visits for the GitHub Pages replay.

Starts nothing itself: point it at a running Sahayak server (default
http://127.0.0.1:8766, e.g. one started with SAHAYAK_DATA_DIR=data-demo
SAHAYAK_PORT=8766) and it drives each demo case through the real agent,
answering the agent's questions like a health worker would. One case is run
with the model switched off to record the degraded-mode recovery.

Then export: SAHAYAK_DATA_DIR=data-demo python3 scripts/export_demo.py
"""

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"

CASES = [
    ({"name": "Aarav", "age_years": 2, "sex": "M"}, "2 year old boy, cough and fever for 3 days, not eating well.",
     {"temp_c": 38.6}, "Telugu", ["46"], True),
    ({"name": "Lakshmi", "age_years": 24, "sex": "F", "pregnant": True},
     "7 months pregnant. Severe headache since morning and says her vision is blurred. Feet swollen.",
     {}, "Telugu", ["166"], True),
    ({"name": "Riya", "age_years": 4, "sex": "F"},
     "Bachchi ko 2 din se tez bukhar hai, aaj subah se behosh jaisi hai, kuch pee nahi rahi.",
     {"temp_c": 39.8}, "Hindi", [], True),
    ({"name": "Baby Anu", "age_years": 0.7, "sex": "F"},
     "Baby has not taken breast milk since morning and is very sleepy, hard to wake up.",
     {"temp_c": 38.9}, "Telugu", [], True),
    ({"name": "Ramesh", "age_years": 32, "sex": "M"},
     "Mild fever and body ache since yesterday, eating and drinking normally, no vomiting, no breathing problem.",
     {"temp_c": 38.1, "hr": 92, "rr": 18, "spo2": 98}, "Hindi", [], True),
    ({"name": "Ravi", "age_years": 22, "sex": "M"}, "Not sure, but low energy.",
     {"temp_c": 30, "hr": 90, "rr": 18, "spo2": 98}, "English",
     ["36.8", "Since 3 days, sleeping badly because of exams, eating and drinking fine, no fever, no other problems."], True),
    ({"name": "Sunita", "age_years": 58, "sex": "F"}, "Cough for 10 days, feels breathless when walking, mild chest pain.",
     {"temp_c": 37.9, "hr": 104, "spo2": 93}, "Hindi", [], False),
]


def call(path: str, body: dict | None = None) -> dict:
    """GET or POST JSON against the Sahayak API."""
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main() -> None:
    """Drive every demo case to completion, answering questions in order."""
    for patient, note, vitals, lang, answers, model_on in CASES:
        call("/api/model", {"enabled": model_on})
        eid = call("/api/encounters", {"patient": patient, "note": note, "vitals": vitals, "language": lang})["id"]
        answers = list(answers)
        while True:
            time.sleep(1)
            e = call(f"/api/encounters/{eid}")
            if e["status"] == "needs_input":
                reply = answers.pop(0) if answers else "not known"
                print(f"  Q: {e['question']}  A: {reply}")
                call(f"/api/encounters/{eid}/answer", {"answer": reply})
                time.sleep(1)
            elif e["status"] != "running":
                break
        print(f"{patient['name']:<9} {e['status']:<8} {e['triage']:<7} degraded={e['degraded']} steps={len(e['steps'])}")
    call("/api/model", {"enabled": True})


if __name__ == "__main__":
    main()
