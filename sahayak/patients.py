"""
Returning patients and follow-up visits.

Two workflows a health worker needs across days, all on local SQLite:

1. Returning patients. Instead of creating a new patient for every visit,
   the worker searches by name (or picks from the follow-up list). The new
   visit is attached to the existing patient id, so the agent's SENSE phase
   loads that patient's previous visits (triage, findings, plan) and Gemma
   sees them in its state, e.g. "second visit, fever not improving".

2. Follow-ups. When a visit closes with a plan, the agent schedules a
   follow-up (followups table). This module lists what is overdue, due
   today and upcoming, lets the worker mark one done, and automatically
   completes a patient's open follow-ups when they are seen again.

Nothing here needs the network.
"""

import time

from . import store
from .log import get_logger, logged

log = get_logger("patients")
DAY = 86400


def migrate() -> None:
    """Add follow-up completion columns to databases created before this feature."""
    cols = {r["name"] for r in store.query("PRAGMA table_info(followups)")}
    if "done_ts" not in cols:
        store.execute("ALTER TABLE followups ADD COLUMN done_ts REAL")
    if "done_by_encounter" not in cols:
        store.execute("ALTER TABLE followups ADD COLUMN done_by_encounter TEXT")


@logged(log)
def search(q: str = "", limit: int = 8) -> list[dict]:
    """Patients whose name contains `q` (case-insensitive), most recently seen first, with visit summary."""
    rows = store.query(
        "SELECT p.id, p.name, p.age_years, p.sex, p.pregnant, p.village, "
        "COUNT(e.id) AS visits, MAX(e.created_at) AS last_visit, "
        "(SELECT triage FROM encounters x WHERE x.patient_id=p.id ORDER BY x.created_at DESC LIMIT 1) AS last_triage "
        "FROM patients p LEFT JOIN encounters e ON e.patient_id=p.id "
        "WHERE p.name LIKE ? GROUP BY p.id ORDER BY last_visit DESC LIMIT ?",
        (f"%{q.strip()}%", limit),
    )
    return rows


@logged(log)
def followups(horizon_days: int = 7) -> dict:
    """Open follow-ups split into overdue, today and upcoming (within horizon_days)."""
    now = time.time()
    lt = time.localtime(now)
    start_today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))  # local midnight
    end_today = start_today + DAY
    rows = store.query(
        "SELECT f.id, f.patient_id, f.encounter_id, f.due_ts, f.reason, p.name, p.age_years, p.sex, "
        "e.triage, e.findings FROM followups f JOIN patients p ON p.id=f.patient_id "
        "JOIN encounters e ON e.id=f.encounter_id WHERE f.done_ts IS NULL AND f.due_ts < ? ORDER BY f.due_ts",
        (end_today + horizon_days * DAY,),
    )
    out = {"overdue": [], "today": [], "upcoming": []}
    for r in rows:
        r["summary"] = (r.pop("findings") or {}).get("summary", "")
        key = "overdue" if r["due_ts"] < start_today else "today" if r["due_ts"] < end_today else "upcoming"
        out[key].append(r)
    return out


@logged(log)
def mark_done(followup_id: str, encounter_id: str | None = None) -> None:
    """Close one follow-up (manually, or because the patient was seen again)."""
    store.execute("UPDATE followups SET done_ts=?, done_by_encounter=? WHERE id=? AND done_ts IS NULL",
                  (time.time(), encounter_id, followup_id))


@logged(log)
def complete_for_revisit(patient_id: str, encounter_id: str) -> int:
    """A new visit for this patient closes their open follow-ups. Returns how many were closed."""
    open_ids = [r["id"] for r in store.query(
        "SELECT id FROM followups WHERE patient_id=? AND done_ts IS NULL AND encounter_id != ?", (patient_id, encounter_id))]
    for fid in open_ids:
        mark_done(fid, encounter_id)
    return len(open_ids)
