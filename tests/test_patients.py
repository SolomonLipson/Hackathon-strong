"""
Tests for returning patients and follow-ups (sahayak/patients.py).

Checks that a patient can be found by partial name with a visit summary,
that a second visit sees the first one as history, and that follow-ups are
bucketed (overdue / today / upcoming), can be marked done, and are closed
automatically when the patient is seen again.

Run: python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("SAHAYAK_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sahayak import agent, llm, patients, store  # noqa: E402

DAY = 86400


def visit(pid, note="mild fever and body ache since yesterday", vitals=None):
    """Run a full rules-only visit for patient `pid` and return its encounter id."""
    eid = store.create_encounter(pid, note, vitals or {"temp_c": 38.0, "hr": 88, "rr": 16}, "English")
    agent.run(agent.load_state(eid))
    return eid


def add_followup(pid, eid, due_ts):
    fid = store.new_id()
    store.execute("INSERT INTO followups (id,patient_id,encounter_id,due_ts,reason) VALUES (?,?,?,?,?)",
                  (fid, pid, eid, due_ts, "test follow-up"))
    return fid


class PatientTests(unittest.TestCase):
    def setUp(self):
        llm.set_enabled(False)  # deterministic rules-only runs; no model needed
        patients.migrate()

    def tearDown(self):
        llm.set_enabled(True)

    def test_migrate_is_idempotent(self):
        patients.migrate()
        cols = {r["name"] for r in store.query("PRAGMA table_info(followups)")}
        self.assertTrue({"done_ts", "done_by_encounter"} <= cols)

    def test_search_by_partial_name_with_visit_summary(self):
        pid = store.upsert_patient({"name": "Sahithya Gande", "age_years": 22, "sex": "F"})
        visit(pid)
        visit(pid)
        found = [p for p in patients.search("sahi") if p["id"] == pid]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["visits"], 2)
        self.assertEqual(found[0]["last_triage"], "GREEN")
        self.assertEqual(patients.search("zzz-nobody"), [])

    def test_second_visit_sees_first_as_history(self):
        pid = store.upsert_patient({"name": "Ravi Kumar", "age_years": 30, "sex": "M"})
        first = visit(pid)
        second = visit(pid)
        hist = next(s for s in store.get_encounter(second)["steps"] if s["tool"] == "get_patient_history")
        prev = hist["detail"]["previous_visits"]
        self.assertEqual(len(prev), 1)
        self.assertEqual(prev[0]["triage"], "GREEN")
        self.assertLess(prev[0]["days_ago"], 0.1)
        self.assertNotEqual(first, second)

    def test_followups_bucketed_marked_done_and_closed_on_revisit(self):
        pid = store.upsert_patient({"name": "Meena Rao", "age_years": 6, "sex": "F"})
        eid = visit(pid)
        store.execute("DELETE FROM followups WHERE patient_id=?", (pid,))  # control the due dates exactly
        now = time.time()
        overdue = add_followup(pid, eid, now - 2 * DAY)
        lt = time.localtime(now)
        today = add_followup(pid, eid, time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1)))
        upcoming = add_followup(pid, eid, now + 3 * DAY)
        later = add_followup(pid, eid, now + 30 * DAY)  # beyond the 7-day horizon

        fu = patients.followups()
        ids = lambda k: {r["id"] for r in fu[k]}
        self.assertIn(overdue, ids("overdue"))
        self.assertIn(today, ids("today"))
        self.assertIn(upcoming, ids("upcoming"))
        self.assertNotIn(later, ids("overdue") | ids("today") | ids("upcoming"))

        patients.mark_done(overdue)
        self.assertNotIn(overdue, {r["id"] for r in patients.followups()["overdue"]})

        new_eid = store.create_encounter(pid, "follow-up visit, better now", {"temp_c": 37.0}, "English")
        closed = patients.complete_for_revisit(pid, new_eid)
        self.assertEqual(closed, 3)  # today, upcoming and later; overdue was already done
        open_left = store.query("SELECT id FROM followups WHERE patient_id=? AND done_ts IS NULL", (pid,))
        self.assertEqual(open_left, [])
        row = store.one("SELECT done_by_encounter FROM followups WHERE id=?", (today,))
        self.assertEqual(row["done_by_encounter"], new_eid)


if __name__ == "__main__":
    unittest.main()
