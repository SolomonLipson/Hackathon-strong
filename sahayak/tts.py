"""
Offline read-aloud: turn the care advice into speech for the family.

Many families cannot read, so the advice Gemma writes in Hindi/Telugu is also
spoken. Browsers do not reliably expose Indian-language voices (Chrome on
macOS often lists none), so speech is synthesized on the device by the
operating system instead and streamed to the page as a WAV file:

  * Piper neural voices (natural-sounding Telugu "padmavathi" and Hindi
    "priyamvada"), run locally from models/piper (scripts/setup_voices.sh).
  * macOS: the built-in `say` voices (Rishi = Indian English, and Lekha /
    Geeta as fallbacks), configured in config.TTS_VOICES.
  * Linux: espeak-ng, if installed.

No network is used. If no engine is available, synthesize() raises
TTSUnavailable and the UI falls back to the browser's own voices.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config
from .log import get_logger, logged

log = get_logger("tts")

ESPEAK_LANG = {"Hindi": "hi", "Telugu": "te", "English": "en-in"}


class TTSUnavailable(RuntimeError):
    """Raised when no offline speech engine can speak the requested language."""


_voices_cache: list[str] | None = None


def best_voice(base: str) -> str:
    """Pick the most natural installed variant of a macOS voice: Premium > Enhanced > compact.

    Enhanced/Premium voices are a one-time download (System Settings > Accessibility >
    Spoken Content > System voice > Manage Voices) and then work fully offline.
    """
    global _voices_cache
    if _voices_cache is None:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        _voices_cache = [line.split("  ")[0].strip() for line in out.splitlines()]
    for quality in ("(Premium)", "(Enhanced)"):
        name = f"{base} {quality}"
        if name in _voices_cache:
            return name
    return base


@logged(log)
def synthesize(text: str, language: str) -> bytes:
    """Return WAV audio of `text` spoken in `language`, generated on-device."""
    text = (text or "").strip()[:2000]
    if not text:
        raise TTSUnavailable("nothing to say")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "speech.wav"
        piper_voice = config.PIPER_DIR / config.PIPER_VOICES.get(language, "-")
        if config.PIPER_BIN.exists() and piper_voice.exists():
            proc = subprocess.run([str(config.PIPER_BIN), "-m", str(piper_voice), "-f", str(out)],
                                  input=text.encode(), capture_output=True, timeout=60)
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 100:
                return out.read_bytes()
            log.warning("piper failed, falling back: %s", proc.stderr.decode(errors="ignore")[-200:])
        if shutil.which("say"):
            voice = best_voice(config.TTS_VOICES.get(language, config.TTS_VOICES["English"]))
            cmd = ["say", "-v", voice, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@24000", text]
        elif shutil.which("espeak-ng"):
            cmd = ["espeak-ng", "-v", ESPEAK_LANG.get(language, "en"), "-s", "150", "-w", str(out), text]
        else:
            raise TTSUnavailable("no offline speech engine (say / espeak-ng) found")
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size < 100:
            raise TTSUnavailable(f"speech engine failed: {proc.stderr.decode(errors='ignore')[:200]}")
        return out.read_bytes()
