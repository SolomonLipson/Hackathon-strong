"""
Accuracy evaluation: run labelled clinical vignettes through the full agent.

Each case has a patient, a worker's note (English / Hinglish / Hindi / Telugu),
the vitals the worker measured, scripted answers for any question the agent
asks (keyed by the vital field, or "none" for a clarification), and the
expected triage level under the project's protocol plus common clinical sense.

Two configurations are compared on the same cases:
  * gemma  - the real agent: Gemma 4 E4B via Ollama plans, extracts and advises
  * rules  - the model switched off: keyword extraction + deterministic planner

Reported per configuration: exact triage accuracy, UNDER-triage (the unsafe
error: predicted less urgent than expected), over-triage, questions asked,
and wall time. Results go to eval/results.md and eval/results.json.

Run: python3 eval/run_eval.py            (needs Ollama + gemma4 for the gemma column)
     python3 eval/run_eval.py --rules    (rules-only, no model)

Labels are the authors' judgement for a demo protocol, not a clinical gold standard.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SAHAYAK_DATA_DIR"] = tempfile.mkdtemp(prefix="sahayak-eval-")
sys.path.insert(0, str(ROOT))

from sahayak import agent, llm, protocols, store  # noqa: E402

CASES = [
    # (id, patient, note, vitals, answers, expected, why)
    ("child-cough-normal-rr", {"age_years": 2, "sex": "M"}, "Cough and runny nose for 2 days, mild fever, playing and feeding well.",
     {"temp_c": 38.2}, {"rr": "32"}, "GREEN", "cough, RR normal for age"),
    ("child-chest-indrawing", {"age_years": 3, "sex": "F"}, "Cough for 4 days, breathing fast, I can see the lower chest pulling in when she breathes in.",
     {"temp_c": 38.4}, {"rr": "48"}, "RED", "chest indrawing"),
    ("infant-not-breastfeeding", {"age_years": 0.7, "sex": "M"}, "Baby has not taken breast milk since morning and is very sleepy, hard to wake up.",
     {"temp_c": 38.9}, {}, "RED", "unable to feed + lethargic"),
    ("young-infant-fever", {"age_years": 0.12, "sex": "F"}, "Six week old baby feels warm, feeding okay since yesterday.",
     {"temp_c": 38.0}, {}, "RED", "fever in infant < 2 months"),
    ("pregnancy-mild-htn", {"age_years": 30, "sex": "F", "pregnant": True}, "8 months pregnant, mild headache since yesterday, no problem with vision, baby moving well.",
     {"sbp": 150, "dbp": 95}, {}, "YELLOW", "hypertension in pregnancy, no severe features"),
    ("pregnancy-bleeding", {"age_years": 26, "sex": "F", "pregnant": True}, "6 months pregnant, bleeding from below since one hour, soaking cloth.",
     {"sbp": 110, "dbp": 70}, {}, "RED", "bleeding in pregnancy"),
    ("adult-chest-pain-arm", {"age_years": 45, "sex": "M"}, "Chest pain for the last hour spreading to the left arm, sweating a lot.",
     {"hr": 100, "sbp": 130, "dbp": 85}, {}, "RED", "possible heart attack"),
    ("adult-mild-fever", {"age_years": 30, "sex": "M"}, "Mild fever and body ache since yesterday, eating and drinking normally.",
     {"temp_c": 38.0, "hr": 88, "rr": 16, "spo2": 98}, {}, "GREEN", "uncomplicated fever"),
    ("adult-mild-diarrhoea", {"age_years": 25, "sex": "F"}, "Loose motions 3 times today, drinking well, no blood, no vomiting.",
     {"temp_c": 37.2, "hr": 84}, {}, "GREEN", "mild diarrhoea"),
    ("child-dehydration", {"age_years": 5, "sex": "M"}, "Loose motions 8 times since yesterday, eyes look sunken, drinks eagerly, very thirsty.",
     {"temp_c": 37.6, "hr": 120}, {}, "YELLOW", "some dehydration"),
    ("prolonged-fever", {"age_years": 60, "sex": "M"}, "Fever for 9 days with weakness, not improving with paracetamol.",
     {"temp_c": 38.5, "hr": 96}, {}, "YELLOW", "fever ≥ 7 days"),
    ("telugu-fever-headache", {"age_years": 20, "sex": "F"}, "రెండు రోజులుగా జ్వరం, తలనొప్పి. తింటోంది, తాగుతోంది.",
     {"temp_c": 38.3, "hr": 90}, {}, "GREEN", "Telugu: fever + headache 2 days, eating and drinking"),
    ("hinglish-convulsions", {"age_years": 4, "sex": "M"}, "Subah se 2 baar jhatke aaye, abhi so raha hai, uthane par bhi nahi uth raha.",
     {"temp_c": 39.2}, {}, "RED", "Hinglish: convulsions, not waking"),
    ("snake-bite", {"age_years": 35, "sex": "M"}, "Bitten by a snake on the leg one hour ago while working in the field, leg swelling.",
     {"hr": 96}, {}, "RED", "snake bite"),
    ("implausible-temp", {"age_years": 22, "sex": "M"}, "Not sure, but low energy.",
     {"temp_c": 30.0, "hr": 80, "spo2": 98}, {"temp_c": "36.9", "none": "Since 2 days, sleeping less because of exams, no fever, eating fine, no other problems."},
     "GREEN", "bad reading re-measured normal; vague note clarified"),
    ("self-harm", {"age_years": 28, "sex": "F"}, "She feels hopeless for weeks and told her sister she wants to end her life.",
     {}, {}, "RED", "suicidal thoughts"),
    ("chronic-cough-haemoptysis", {"age_years": 40, "sex": "F"}, "Cough for 3 weeks, sometimes blood in the sputum, losing weight, night sweats.",
     {"temp_c": 37.8, "spo2": 96}, {}, "YELLOW", "suspected TB: needs clinic, not home care"),
    ("child-poor-appetite", {"age_years": 3, "sex": "F"}, "Fever for 2 days, eating less than usual, but drinking water and playing.",
     {"temp_c": 38.4, "rr": 30, "hr": 110}, {}, "GREEN", "reduced appetite is not 'unable to feed'"),
    ("adult-hypoxia", {"age_years": 50, "sex": "M"}, "Breathless since 2 days, worse on walking, cough.",
     {"spo2": 88, "rr": 28, "hr": 108}, {}, "RED", "SpO2 < 90%"),
]


def run_case(case) -> dict:
    """Run one vignette through the agent, auto-answering its questions."""
    cid, patient, note, vitals, answers, expected, why = case
    pid = store.upsert_patient({"name": cid, **patient})
    eid = store.create_encounter(pid, note, {k: float(v) for k, v in vitals.items()}, "Hindi")
    t0 = time.time()
    status = agent.run(agent.load_state(eid))
    asked = []
    for _ in range(4):
        if status != "needs_input":
            break
        enc = store.get_encounter(eid)
        field = next((st["detail"].get("field") for st in reversed(enc["steps"])
                      if st["phase"] == "act" and st["tool"] == "ask_health_worker"), None) or "none"
        reply = answers.get(field, "not known")
        asked.append({"field": field, "question": enc["question"], "answer": reply})
        agent.answer(eid, reply, resume=False)
        status = agent.run(agent.load_state(eid))
    enc = store.get_encounter(eid)
    got = enc["triage"] or "NONE"
    diff = protocols.LEVELS.index(got) - protocols.LEVELS.index(expected) if got in protocols.LEVELS else -9
    return {"id": cid, "expected": expected, "got": got, "match": diff == 0, "under": diff < 0, "over": diff > 0,
            "status": status, "degraded": bool(enc["degraded"]), "questions": asked, "seconds": round(time.time() - t0, 1),
            "steps": max((st["n"] for st in enc["steps"]), default=0), "why": why,
            "rejections": sum(1 for st in enc["steps"] if st["phase"] == "check" and st["detail"].get("ok") is False)}


def summarize(rows: list[dict]) -> dict:
    """Aggregate metrics for one configuration."""
    n = len(rows)
    return {"cases": n, "accuracy": sum(r["match"] for r in rows) / n, "under_triage": sum(r["under"] for r in rows),
            "over_triage": sum(r["over"] for r in rows), "questions": sum(len(r["questions"]) for r in rows),
            "degraded": sum(r["degraded"] for r in rows), "median_s": sorted(r["seconds"] for r in rows)[n // 2]}


def main() -> None:
    """Run both configurations and write the report."""
    configs = [("rules", False)] if "--rules" in sys.argv else [("rules", False), ("gemma", True)]
    results = {}
    for name, enabled in configs:
        llm.set_enabled(enabled)
        rows = []
        for case in CASES:
            r = run_case(case)
            rows.append(r)
            print(f"[{name}] {r['id']:<28} expected {r['expected']:<6} got {r['got']:<6} "
                  f"{'OK ' if r['match'] else 'UNDER' if r['under'] else 'over'} q={len(r['questions'])} {r['seconds']}s", flush=True)
        results[name] = {"summary": summarize(rows), "rows": rows}
    out = ROOT / "eval"
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    lines = ["# Evaluation results", "", f"{len(CASES)} labelled vignettes (see `eval/run_eval.py`). Under-triage is the unsafe error.", "",
             "| Config | Exact accuracy | Under-triage | Over-triage | Questions asked | Median time |", "|---|---|---|---|---|---|"]
    for name, res in results.items():
        s = res["summary"]
        lines.append(f"| {name} | {s['accuracy']:.0%} ({round(s['accuracy'] * s['cases'])}/{s['cases']}) | {s['under_triage']} | "
                     f"{s['over_triage']} | {s['questions']} | {s['median_s']} s |")
    lines += ["", "| Case | Expected | " + " | ".join(results) + " |", "|---|---|" + "---|" * len(results)]
    for i, case in enumerate(CASES):
        cells = []
        for name in results:
            r = results[name]["rows"][i]
            cells.append(f"{r['got']}{' ✓' if r['match'] else ' ⚠ under' if r['under'] else ' ↑ over'}")
        lines.append(f"| {case[0]} ({case[6]}) | {case[5]} | " + " | ".join(cells) + " |")
    (out / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    main()
