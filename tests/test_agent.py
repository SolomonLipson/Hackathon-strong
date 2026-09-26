"""
Tests for the agent loop's safety boundaries, run without any model.

A scripted fake Gemma lets us check that the loop is more than a straight
arrow: the checker rejects unsafe plans, RED signs force a handoff, the agent
pauses for missing vitals and resumes, a model crash falls back to rules, and
an interrupted visit can be rebuilt from the database.

Run: python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["SAHAYAK_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sahayak import agent, llm, store  # noqa: E402


def new_visit(note, vitals=None, **patient):
    """Create a patient + encounter and return the loaded agent state."""
    pid = store.upsert_patient({"name": "T", "age_years": 30, "sex": "F", **patient})
    eid = store.create_encounter(pid, note, vitals or {}, "Hindi")
    return agent.load_state(eid)


class FakeGemma:
    """Replays a scripted list of DECIDE outputs; extraction returns fixed findings."""

    def __init__(self, decisions, findings):
        self.decisions = list(decisions)
        self.findings = findings

    def __call__(self, system, user, schema, models=None):
        if "clinical reader" in system:
            f = dict(self.findings)
            f["danger_sign_checklist"] = {s: "yes" for s in f.pop("danger_signs", [])}
            f.setdefault("evidence", [])
            f.setdefault("negated", [])
            return f, "fake-gemma"
        tool, args = self.decisions.pop(0)
        return {"thought": "t", "tool": tool, "args": args}, "fake-gemma"


class AgentTests(unittest.TestCase):
    def setUp(self):
        self._orig = llm.chat_json
        llm.set_enabled(True)

    def tearDown(self):
        llm.chat_json = self._orig
        llm.set_enabled(True)

    def test_checker_rejects_downgrade_and_forces_referral(self):
        findings = {"symptoms": ["headache"], "danger_signs": ["severe_headache_blurred_vision"],
                    "duration_days": 1, "vitals_in_note": {}, "summary": "s"}
        llm.chat_json = FakeGemma([
            ("extract_findings", {}),  # repeat of the auto-sensed step: rejected as no progress
            ("recommend_care", {"triage": "GREEN", "advice_steps": ["rest"], "confidence": 0.9}),  # unsafe
            ("recommend_care", {"triage": "RED", "advice_steps": ["go to hospital"], "confidence": 0.9}),
            ("finish", {"summary": "done"}),
        ], findings)
        s = new_visit("severe headache blurred vision", {"sbp": 150, "dbp": 100}, pregnant=True)
        status = agent.run(s)
        enc = store.get_encounter(s.encounter_id)
        self.assertEqual(status, "handoff")
        self.assertEqual(enc["plan"]["triage"], "RED")
        self.assertTrue(any(st["phase"] == "check" and st["detail"].get("ok") is False for st in enc["steps"]))
        self.assertEqual(len(enc["handoffs"]), 1)  # forced by RED rule, not duplicated

    def test_pauses_for_missing_breathing_rate_then_resumes(self):
        llm.set_enabled(False)  # rules planner drives this one deterministically
        s = new_visit("child with cough and fever for 3 days", {"temp_c": 38.5}, age_years=2)
        self.assertEqual(agent.run(s), "needs_input")
        agent.answer(s.encounter_id, "52", resume=False)
        agent.run(agent.load_state(s.encounter_id))
        enc = store.get_encounter(s.encounter_id)
        self.assertEqual(enc["vitals"]["rr"], 52.0)
        self.assertEqual(enc["plan"]["triage"], "YELLOW")
        self.assertEqual(enc["status"], "handoff")

    def test_model_crash_falls_back_to_rules(self):
        def boom(*a, **k):
            raise llm.LLMUnavailable("down")
        llm.chat_json = boom
        s = new_visit("mild fever and body ache since yesterday", {"temp_c": 38.0, "hr": 90, "rr": 18}, age_years=30, sex="M")
        status = agent.run(s)
        enc = store.get_encounter(s.encounter_id)
        self.assertEqual(status, "done")
        self.assertEqual(enc["plan"]["triage"], "GREEN")
        self.assertTrue(enc["degraded"])
        self.assertTrue(any(st["phase"] == "recover" for st in enc["steps"]))

    def test_keyword_safety_net_catches_missed_danger_sign(self):
        findings = {"symptoms": ["fever"], "danger_signs": [], "duration_days": 2, "vitals_in_note": {}, "summary": "s"}
        llm.chat_json = FakeGemma([("extract_findings", {}), ("check_danger_signs", {})], findings)
        s = new_visit("bachchi behosh hai, bukhar", {"temp_c": 39}, age_years=4)
        agent.TOOLS["extract_findings"](s, {})
        self.assertIn("unconscious_or_lethargic", s.findings["danger_signs"])

    def test_resume_rebuilds_state(self):
        llm.set_enabled(False)
        s = new_visit("mild cough and runny nose for 2 days", {"temp_c": 37.2}, age_years=40)
        s.step = 1
        agent.TOOLS["extract_findings"](s, {})
        store.add_step(s.encounter_id, 1, "act", "extract_findings", {})
        store.update_encounter(s.encounter_id, findings=s.findings)
        resumed = agent.load_state(s.encounter_id)
        self.assertTrue(resumed.findings)
        self.assertIsNone(resumed.rules)
        self.assertEqual(agent.run(resumed), "done")

    def test_vague_note_triggers_clarifying_question(self):
        llm.set_enabled(False)
        s = new_visit("not sure, but low energy", {"temp_c": 36.8, "hr": 80}, age_years=22, sex="M")
        self.assertEqual(agent.run(s), "needs_input")
        enc = store.get_encounter(s.encounter_id)
        self.assertIn("other problems", enc["question"])
        agent.answer(s.encounter_id, "since 3 days, vomiting and not eating", resume=False)
        agent.run(agent.load_state(s.encounter_id))
        enc = store.get_encounter(s.encounter_id)
        self.assertIn("vomiting", enc["findings"]["symptoms"])
        self.assertIn(enc["status"], ("done", "handoff"))

    def test_implausible_vital_is_remeasured_not_trusted(self):
        llm.set_enabled(False)
        s = new_visit("low energy and weakness since yesterday", {"temp_c": 30.0, "hr": 80}, age_years=22, sex="M")
        self.assertEqual(agent.run(s), "needs_input")
        enc = store.get_encounter(s.encounter_id)
        self.assertIn("looks wrong", enc["question"])
        self.assertEqual(enc["handoffs"], [])  # no emergency referral from a bad reading
        agent.answer(s.encounter_id, "98.2 F", resume=False)
        agent.run(agent.load_state(s.encounter_id))
        enc = store.get_encounter(s.encounter_id)
        self.assertEqual(enc["vitals"]["temp_c"], 36.8)
        self.assertEqual(enc["triage"], "GREEN")

    def test_temperature_thresholds(self):
        from sahayak import protocols
        lvl = lambda t: protocols.evaluate({"temp_c": t}, {"age_years": 30}, {})["level"]
        self.assertEqual([lvl(34.5), lvl(35.0), lvl(36.5), lvl(39.6), lvl(41.2)], ["RED", "YELLOW", "GREEN", "YELLOW", "RED"])


if __name__ == "__main__":
    unittest.main()
