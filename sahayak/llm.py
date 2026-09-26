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

Use cases: the agent's DECIDE step (choose next tool), the SENSE step's
free-text extraction (turn a health worker's note into structured findings),
and voice input: `transcribe()` sends recorded speech to Gemma 4's native
audio encoder (no separate ASR model, no cloud).
"""

import json
import re
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


def _options(temperature: float) -> dict:
    """Identical runtime options for every call (a different num_ctx forces a model reload)."""
    return {"temperature": temperature, "num_ctx": config.LLM_NUM_CTX, "num_predict": config.LLM_MAX_TOKENS}


def warm_up() -> None:
    """Load the Gemma models into memory at startup so the first visit is not slow."""
    for model in dict.fromkeys((config.PRIMARY_MODEL, config.FAST_MODEL)):
        try:
            _post("/api/chat", {"model": model, "stream": False, "think": False,
                                "keep_alive": config.LLM_KEEP_ALIVE, "options": _options(0) | {"num_predict": 1},
                                "messages": [{"role": "user", "content": "hi"}]}, config.LLM_TIMEOUT_S)
            log.info("model %s warmed up", model)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
            log.warning("warm-up of %s failed (agent will use fallbacks): %s", model, err)


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
                "keep_alive": config.LLM_KEEP_ALIVE,
                "format": schema,
                "options": _options(config.LLM_TEMPERATURE),
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


TRANSCRIBE_PROMPT = (
    "Listen to this {secs:.1f}-second recording and write down exactly what the speaker says.{lang_hint} "
    "Drop filler sounds (um, uh, hmm, aah) and repeated words. Write numbers as digits. Do not add anything that was "
    "not said, do not describe the audio, and do not answer or comment. If no clear speech is heard, output nothing."
)
LANG_HINT = {
    "Telugu": " The speaker is most likely speaking Telugu (maybe mixed with English): write Telugu words in Telugu script (తెలుగు), never in English letters.",
    "Hindi": " The speaker is most likely speaking Hindi (maybe mixed with English): write Hindi words in Devanagari (हिन्दी), never in English letters.",
    "English": " The speaker is most likely speaking English (Indian accent).",
}
# Signs that the model talked ABOUT the audio instead of transcribing it.
_META = re.compile(r"(transcri|audio|recording|clip|write only|i'?m sorry|i cannot|i can'?t|no speech|not provided|speaker)", re.I)


def transcribe(wav_b64: str, language: str | None = None) -> tuple[str, str]:
    """Speech-to-text with Gemma 4 audio input. Returns (text, model). Raises LLMUnavailable."""
    if not _enabled:
        raise LLMUnavailable("local model disabled (simulated crash)")
    last_err: Exception | None = None
    secs = max(0.5, (len(wav_b64) * 3 // 4 - 44) / 32000)  # 16 kHz mono 16-bit PCM
    for model in (config.PRIMARY_MODEL, config.FALLBACK_MODEL):
        body = {
            "model": model, "stream": False, "think": False,
            "keep_alive": config.LLM_KEEP_ALIVE, "options": _options(0),
            # Ollama passes audio clips to Gemma 4 through the multimodal "images" field.
            "messages": [{"role": "user", "content": TRANSCRIBE_PROMPT.format(secs=secs, lang_hint=LANG_HINT.get(language or "", "")),
                          "images": [wav_b64]}],
        }
        log.info("gemma transcribe model=%s audio_bytes=%d (inline audio stripped from log)", model, len(wav_b64) * 3 // 4)
        t0 = time.monotonic()
        try:
            text = _post("/api/chat", body, config.LLM_TIMEOUT_S)["message"]["content"].strip()
            if _META.search(text):  # the model described/refused instead of transcribing: treat as not heard
                log.warning("discarding meta transcript: %s", text)
                return "", model
            words = text.split()
            if len(words) > secs * 5 + 4:  # physically impossible speaking rate: the model made things up
                log.warning("transcript too long for %.1fs clip (%d words); truncating", secs, len(words))
                text = " ".join(words[: int(secs * 4) + 3])
            log.info("gemma transcript model=%s %.1fs: %s", model, time.monotonic() - t0, text)
            return text, model
        except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
            last_err = err
            log.warning("transcribe failure model=%s: %s", model, err)
    raise LLMUnavailable(f"no local model could transcribe: {last_err}")


def translate(text: str, language: str) -> str:
    """Translate a short spoken line into the family's language, in its native script (on-device)."""
    if language == "English" or not text:
        return text
    out, _ = chat_json(
        f"Translate what a health assistant says into simple spoken {language}, written in {language} script "
        f"(never in English letters). Keep numbers as digits. Output JSON only.",
        text, {"type": "object", "properties": {"translation": {"type": "string"}}, "required": ["translation"]},
        models=[config.FAST_MODEL, config.PRIMARY_MODEL])
    return out.get("translation") or text
