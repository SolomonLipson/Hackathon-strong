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
 * Recording stops by itself ~1.5 s after the speaker goes quiet (a simple
 * energy-based voice-activity detector), so the worker just talks. Gemma
 * returns a CLEANED transcript: fillers (um, hmm, aah), false starts and
 * repetitions removed, every clinical fact and number kept.
 *
 * Output: speak() reads the care advice aloud with the operating system's
 * offline voices via /api/speak (Hindi, Telugu, English); only if that is
 * unavailable does it fall back to the browser's own speech synthesis.
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
    // Voice-activity detection: stop ~1.5 s after speech ends (or after 45 s).
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let spoke = false, quietSince = null;
    const started = Date.now();
    const vad = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, x) => a + x * x, 0) / buf.length);
      if (rms > 0.02) { spoke = true; quietSince = null; } else if (spoke && !quietSince) quietSince = Date.now();
      if ((spoke && quietSince && Date.now() - quietSince > 1500) || Date.now() - started > 45000) {
        if (recorder.state === "recording") recorder.stop();
      }
    }, 100);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      clearInterval(vad);
      ctx.close();
      stream.getTracks().forEach((t) => t.stop());
      button.classList.remove("rec");
      button.classList.add("busy");
      button.textContent = "⋯ Gemma is cleaning up what you said";
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
    button.textContent = "● Listening… (stops when you pause)";
  }

  const LANG = { Hindi: "hi-IN", Telugu: "te-IN", English: "en-IN" };
  let playing = null;

  /** Browser speech synthesis fallback (only used if on-device TTS is unavailable). */
  function browserSpeak(text, language) {
    const lang = LANG[language] || "en-IN";
    const voices = speechSynthesis.getVoices().filter((v) => v.lang.replace("_", "-").startsWith(lang.slice(0, 2)));
    const voice = voices.find((v) => v.localService) || voices[0];
    if (!voice) { alert(`No ${language} voice available on this device.`); return; }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = 0.9;
    speechSynthesis.speak(u);
  }

  /** Read text aloud in the family's language with the device's offline voices. Click again to stop. */
  async function speak(text, language, button) {
    if (playing) { playing.pause(); playing = null; if (button) button.textContent = "🔊 Read aloud"; return; }
    if (window.SAHAYAK_REPLAY) { browserSpeak(text, language); return; }
    if (button) button.textContent = "⋯ preparing voice";
    try {
      const r = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }) });
      if (!r.ok) throw new Error((await r.json()).error);
      const audio = new Audio(URL.createObjectURL(await r.blob()));
      playing = audio;
      if (button) button.textContent = "■ Stop";
      audio.onended = () => { playing = null; if (button) button.textContent = "🔊 Read aloud"; };
      await audio.play();
    } catch (err) {
      console.warn("on-device TTS unavailable, using browser voices:", err);
      if (button) button.textContent = "🔊 Read aloud";
      browserSpeak(text, language);
    }
  }

  return { toggle, speak };
})();
