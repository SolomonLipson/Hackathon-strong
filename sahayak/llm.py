"""
Local Gemma 4 client (Ollama) with a fallback chain.

The agent never talks to the cloud. Every call goes to Ollama on localhost and
asks for JSON constrained by a schema (Ollama structured outputs), so the
agent loop receives machine-checkable decisions rather than prose.

Failure handling, from best to worst:
  1. Gemma 4 E4B (PRIMARY_MODEL), retried once on timeout / invalid JSON.
  2. Gemma 4 E2B (FALLBACK_MODEL), for low-memory devices or an E4B crash.
  3. LLMUnavailable is raised; the agent then switches to its deterministic
     rules-only planner, so a patient visit is never lost because the model died.

Use cases: the agent's DECIDE step (choose next tool) and the SENSE step's
free-text extraction (turn a health worker's note into structured findings).
"""

import json
import time
import urllib.error
import urllib.request

from . import config
from .log import get_logger

log = get_logger("llm")


class LLMUnavailable(RuntimeError):
    """Raised when no local model could produce a valid response."""


# Demo switch: simulate the local model crashing (OOM, runtime killed).
_enabled = True


def set_enabled(value: bool) -> None:
    """Enable/disable the local model to demonstrate degraded-mode recovery."""
    global _enabled
    _enabled = value
    log.info("local model %s", "enabled" if value else "DISABLED (simulated crash)")


def _post(path: str, body: dict, timeout: float) -> dict:
    """POST JSON to the local Ollama server and return the decoded reply."""
    req = urllib.request.Request(
        config.OLLAMA_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def status() -> dict:
    """Report whether Ollama is reachable and which Gemma models are installed."""
    try:
        with urllib.request.urlopen(config.OLLAMA_URL + "/api/tags", timeout=2) as resp:
            names = [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return {"runtime": False, "enabled": _enabled, "models": [], "primary": config.PRIMARY_MODEL}
    return {
        "runtime": True,
        "enabled": _enabled,
        "models": names,
        "primary": config.PRIMARY_MODEL,
        "primary_ready": config.PRIMARY_MODEL in names,
        "fallback_ready": config.FALLBACK_MODEL in names,
    }


def chat_json(system: str, user: str, schema: dict, models: list[str] | None = None) -> tuple[dict, str]:
    """
    Ask a local Gemma model for a JSON object matching `schema`.

    Returns (parsed_object, model_name_used). Walks the fallback chain and
    raises LLMUnavailable if every model fails.
    """
    if not _enabled:
        raise LLMUnavailable("local model disabled (simulated crash)")
    chain = models or [config.PRIMARY_MODEL, config.FALLBACK_MODEL]
    last_err: Exception | None = None
    for model in chain:
        for attempt in range(1 + config.LLM_RETRIES):
            body = {
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {"temperature": config.LLM_TEMPERATURE, "num_ctx": config.LLM_NUM_CTX},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            log.info("gemma call model=%s attempt=%d system=%r user=%r", model, attempt, system[:400], user)
            t0 = time.monotonic()
            try:
                reply = _post("/api/chat", body, config.LLM_TIMEOUT_S)
                text = reply["message"]["content"]
                log.info("gemma output model=%s %.1fs: %s", model, time.monotonic() - t0, text)
                return json.loads(text), model
            except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
                last_err = err
                log.warning("gemma failure model=%s attempt=%d: %s", model, attempt, err)
    raise LLMUnavailable(f"no local model answered: {last_err}")
