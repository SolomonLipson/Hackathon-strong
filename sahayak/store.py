"""
Local state: a single SQLite file that is the agent's memory.

Everything the agent knows or does is persisted on the device, never in RAM
only, because a rural clinic laptop can lose power mid-visit:

  patients    - demographics used by the danger-sign rules (age, pregnancy)
  encounters  - one visit: the worker's note, vitals, status, findings, plan
  steps       - append-only trace of every sense/decide/act/check step; the
                agent resumes an interrupted encounter by replaying this
  handoffs    - the human-handoff queue (clinician must review)
  followups   - scheduled revisits
  outbox      - records waiting to sync to the district server when a network
                appears (idempotency key per record so re-sends are harmless)

A module-level lock serializes writes; the web server is multi-threaded.
"""

import json
import sqlite3
import threading
import time
import uuid

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
  id TEXT PRIMARY KEY, name TEXT, age_years REAL, sex TEXT, pregnant INTEGER DEFAULT 0,
  village TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS encounters (
  id TEXT PRIMARY KEY, patient_id TEXT, note TEXT, vitals TEXT, language TEXT,
  status TEXT, triage TEXT, findings TEXT, plan TEXT, question TEXT, answers TEXT DEFAULT '[]',
  degraded INTEGER DEFAULT 0, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, encounter_id TEXT, n INTEGER, phase TEXT,
  tool TEXT, detail TEXT, model TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS handoffs (
  id TEXT PRIMARY KEY, encounter_id TEXT, urgency TEXT, reason TEXT, status TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS followups (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, due_ts REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, idem_key TEXT UNIQUE, payload TEXT,
  attempts INTEGER DEFAULT 0, next_try_at REAL DEFAULT 0, synced_at REAL, last_error TEXT);
"""

JSON_COLS = {"vitals", "findings", "plan", "answers", "detail", "payload"}


def conn() -> sqlite3.Connection:
    """Open (once) and return the shared SQLite connection."""
    global _conn
    with _lock:
        if _conn is None:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
        return _conn


def _row(r: sqlite3.Row | None) -> dict | None:
    """Convert a row to a dict, decoding JSON columns."""
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def new_id() -> str:
    """Short random id for patients, encounters, handoffs."""
    return uuid.uuid4().hex[:12]


def execute(sql: str, params: tuple = ()) -> None:
    """Run a write statement and commit."""
    with _lock:
        c = conn()
        c.execute(sql, params)
        c.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read statement and return rows as dicts."""
    with _lock:
        return [_row(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    """Run a read statement and return the first row or None."""
    rows = query(sql, params)
    return rows[0] if rows else None


# --- Patients & encounters ----------------------------------------------------

def upsert_patient(p: dict) -> str:
    """Insert or update a patient; returns its id."""
    pid = p.get("id") or new_id()
    execute(
        "INSERT INTO patients (id,name,age_years,sex,pregnant,village,created_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, age_years=excluded.age_years, sex=excluded.sex, "
        "pregnant=excluded.pregnant, village=excluded.village",
        (pid, p.get("name"), p.get("age_years"), p.get("sex"), int(bool(p.get("pregnant"))),
         p.get("village"), time.time()),
    )
    return pid


def create_encounter(patient_id: str, note: str, vitals: dict, language: str) -> str:
    """Create a new encounter in 'running' state; returns its id."""
    eid = new_id()
    now = time.time()
    execute(
        "INSERT INTO encounters (id,patient_id,note,vitals,language,status,findings,plan,created_at,updated_at) "
        "VALUES (?,?,?,?,?,'running','{}','{}',?,?)",
        (eid, patient_id, note, json.dumps(vitals), language, now, now),
    )
    return eid


def update_encounter(eid: str, **fields) -> None:
    """Patch encounter columns (JSON-encoding dict/list values)."""
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = tuple(json.dumps(v) if k in JSON_COLS else v for k, v in fields.items())
    execute(f"UPDATE encounters SET {cols} WHERE id=?", vals + (eid,))


def get_encounter(eid: str) -> dict | None:
    """Return an encounter joined with its patient, steps and handoffs."""
    enc = one("SELECT * FROM encounters WHERE id=?", (eid,))
    if not enc:
        return None
    enc["patient"] = one("SELECT * FROM patients WHERE id=?", (enc["patient_id"],))
    enc["steps"] = query("SELECT * FROM steps WHERE encounter_id=? ORDER BY id", (eid,))
    enc["handoffs"] = query("SELECT * FROM handoffs WHERE encounter_id=?", (eid,))
    enc["followups"] = query("SELECT * FROM followups WHERE encounter_id=?", (eid,))
    return enc


def patient_history(patient_id: str, exclude: str | None = None, limit: int = 5) -> list[dict]:
    """Previous encounters for a patient, most recent first."""
    return query(
        "SELECT id, created_at, triage, status, findings, plan FROM encounters "
        "WHERE patient_id=? AND id!=? ORDER BY created_at DESC LIMIT ?",
        (patient_id, exclude or "", limit),
    )


def add_step(eid: str, n: int, phase: str, tool: str | None, detail: dict, model: str | None = None) -> None:
    """Append one step to the encounter's trace."""
    execute(
        "INSERT INTO steps (encounter_id,n,phase,tool,detail,model,ts) VALUES (?,?,?,?,?,?,?)",
        (eid, n, phase, tool, json.dumps(detail), model, time.time()),
    )


def enqueue(kind: str, idem_key: str, payload: dict) -> None:
    """Put a record in the sync outbox (no-op if the key was already queued)."""
    execute(
        "INSERT OR IGNORE INTO outbox (kind,idem_key,payload) VALUES (?,?,?)",
        (kind, idem_key, json.dumps(payload)),
    )
