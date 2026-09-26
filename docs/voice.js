/*
 * Voice input and read-aloud, both offline.
 *
 * Input: the browser records the microphone (MediaRecorder), decodes the
 * clip, resamples it to 16 kHz mono and encodes a PCM WAV, then POSTs it to
 * /api/transcribe, where Gemma 4 transcribes it locally with its native audio
 * encoder (no cloud speech API, no separate ASR model). Used for the visit
 * note and for answering the agent's questions, so a worker can keep their
 * hands on the patient.
 *
 * Output: speak() reads the care advice to the family with the browser's
 * speech synthesis, preferring on-device voices (localService) so it also
 * works offline.
 */

const Voice = (() => {
  let recorder = null;
  let chunks = [];

  /** Resample an AudioBuffer to 16 kHz mono and encode it as a base64 PCM16 WAV. */
  async function toWavBase64(blob) {
    const raw = await blob.arrayBuffer();
    const decoded = await new AudioContext().decodeAudioData(raw);
    const rate = 16000;
    const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const src = offline.createBufferSource();
    src.buffer = decoded;
    src.connect(offline.destination);
    src.start();
    const pcm = (await offline.startRendering()).getChannelData(0);
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const v = new DataView(buf);
    const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, pcm.length * 2, true);
    pcm.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 0x7fff, true));
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }

  /**
   * Toggle recording on a mic button. On stop, transcribe with Gemma and hand
   * the text to onText. The button shows recording / transcribing states.
   */
  async function toggle(button, onText) {
    if (window.SAHAYAK_REPLAY) { alert("Voice input runs on-device with Gemma 4. Clone the repo and run it locally to try it."); return; }
    if (recorder && recorder.state === "recording") { recorder.stop(); return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      alert("Microphone not available: " + err.message);
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      button.classList.remove("rec");
      button.classList.add("busy");
      button.textContent = "⋯ Gemma is transcribing";
      try {
        const audio = await toWavBase64(new Blob(chunks, { type: recorder.mimeType }));
        const r = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audio }) });
        const out = await r.json();
        if (out.error) alert(out.error); else onText(out.text);
      } catch (err) {
        alert("Transcription failed: " + err.message);
      } finally {
        button.classList.remove("busy");
        button.textContent = button.dataset.label;
      }
    };
    recorder.start();
    button.dataset.label = button.dataset.label || button.textContent;
    button.classList.add("rec");
    button.textContent = "■ Stop & transcribe";
  }

  const LANG = { Hindi: "hi-IN", Telugu: "te-IN", English: "en-IN" };

  /** Read text aloud in the family's language, preferring on-device voices. */
  function speak(text, language) {
    const lang = LANG[language] || "en-IN";
    const voices = speechSynthesis.getVoices().filter((v) => v.lang.replace("_", "-").startsWith(lang.slice(0, 2)));
    const voice = voices.find((v) => v.localService) || voices[0];
    if (!voice) { alert(`No ${language} voice installed on this device (System Settings → Accessibility → Spoken Content).`); return; }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = 0.9;
    speechSynthesis.speak(u);
  }

  return { toggle, speak };
})();
