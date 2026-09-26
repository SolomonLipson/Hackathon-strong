"""
Local web server: the health worker's UI and a small JSON API.

Standard-library only (no pip install, no CDN assets), bound to localhost by
default, so the whole app runs on an offline laptop. Routes:

  GET  /                          the single-page UI (static/)
  GET  /api/status                model runtime + sync status
  GET  /api/encounters            recent visits
  POST /api/encounters            start a visit {patient, note, vitals, language}
  GET  /api/encounters/<id>       full visit incl. agent trace
  POST /api/encounters/<id>/answer  answer the agent's question {answer}
  GET  /api/patients              known patients (for repeat visits)
  GET  /api/handoffs              open clinician handoffs
  POST /api/handoffs/<id>/ack     clinician acknowledges a handoff
  POST /api/transcribe {audio}    voice input: base64 16 kHz WAV -> text (Gemma 4 audio)
  POST /api/network {online}      simulate connectivity
  POST /api/model {enabled}       simulate the local model crashing
"""

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, config, llm, store, sync
from .log import get_logger

log = get_logger("server")


class Handler(BaseHTTPRequestHandler):
    """Routes API calls and serves static files."""

    def log_message(self, fmt, *args):
        """Silence default per-request stderr logging (polling is noisy)."""

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self._send(200, {"model": llm.status(), "sync": sync.stats()})
        if path == "/api/encounters":
            return self._send(200, store.query(
                "SELECT e.id, e.status, e.triage, e.degraded, e.created_at, p.name, p.age_years "
                "FROM encounters e JOIN patients p ON p.id=e.patient_id ORDER BY e.created_at DESC LIMIT 30"))
        m = re.fullmatch(r"/api/encounters/(\w+)", path)
        if m:
            enc = store.get_encounter(m.group(1))
            return self._send(200 if enc else 404, enc or {"error": "not found"})
        if path == "/api/patients":
            return self._send(200, store.query("SELECT * FROM patients ORDER BY created_at DESC"))
        if path == "/api/handoffs":
            return self._send(200, store.query(
                "SELECT h.*, p.name, p.age_years FROM handoffs h JOIN encounters e ON e.id=h.encounter_id "
                "JOIN patients p ON p.id=e.patient_id WHERE h.status='open' ORDER BY h.created_at DESC"))
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._json()
        if path == "/api/encounters":
            pid = store.upsert_patient(body.get("patient") or {})
            vitals = {k: float(v) for k, v in (body.get("vitals") or {}).items() if v not in (None, "")}
            eid = store.create_encounter(pid, body.get("note", ""), vitals, body.get("language") or "English")
            agent.start_async(eid)
            return self._send(201, {"id": eid})
        m = re.fullmatch(r"/api/encounters/(\w+)/answer", path)
        if m:
            agent.answer(m.group(1), str(body.get("answer", "")))
            return self._send(200, {"ok": True})
        m = re.fullmatch(r"/api/handoffs/(\w+)/ack", path)
        if m:
            store.execute("UPDATE handoffs SET status='acknowledged' WHERE id=?", (m.group(1),))
            return self._send(200, {"ok": True})
        if path == "/api/transcribe":
            try:
                text, model = llm.transcribe(body.get("audio", ""))
                return self._send(200, {"text": text, "model": model})
            except llm.LLMUnavailable as err:
                return self._send(503, {"error": f"Voice needs the local model: {err}. Please type instead."})
        if path == "/api/network":
            sync.set_online(bool(body.get("online")))
            return self._send(200, sync.stats())
        if path == "/api/model":
            llm.set_enabled(bool(body.get("enabled")))
            return self._send(200, llm.status())
        return self._send(404, {"error": "not found"})

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (config.STATIC_DIR / rel).resolve()
        if config.STATIC_DIR.resolve() not in f.parents or not f.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self._send(200, f.read_bytes(), ctype)


def main() -> None:
    """Start sync worker, resume interrupted visits, serve forever."""
    store.conn()
    sync.start_worker()
    resumed = agent.resume_interrupted()
    if resumed:
        log.info("resumed interrupted encounters: %s", resumed)
    log.info("model status: %s", llm.status())
    log.info("Sahayak running on http://%s:%d", config.HOST, config.PORT)
    ThreadingHTTPServer((config.HOST, config.PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
