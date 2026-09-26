/*
 * Voice input and read-aloud, both fully offline.
 *
 * Input: listen() records the microphone, stops by itself ~1.3 s after the
 * speaker goes quiet (energy-based voice-activity detection), resamples the
 * clip to 16 kHz mono PCM WAV and POSTs it to /api/transcribe, where Gemma 4
 * transcribes it locally with its native audio encoder and returns a CLEANED
 * transcript: fillers (um, hmm, aah), false starts and repetitions removed,
 * every clinical fact, negation and number kept. No cloud speech API.
 *
 * Output: say() speaks text with the operating system's offline voices via
 * /api/speak (Hindi, Telugu, Indian English) and resolves when playback
 * ends; only if that is unavailable does it fall back to the browser's own
 * speech synthesis.
 *
 * toggle() wires a mic button for one-shot dictation; the conversation mode
 * (conversation.js) chains say() and listen() into a spoken dialogue.
 */

const Voice = (() => {
  let active = null;   // current recording: { stop() }
  let playing = null;  // current audio element

  /** Encode an audio Blob as a base64 16 kHz mono PCM WAV. */
  async function toWavBase64(blob) {
    const decoded = await new AudioContext().decodeAudioData(await blob.arrayBuffer());
    const rate = 16000;
    const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const src = offline.createBufferSource();
    src.buffer = decoded;
    src.connect(offline.destination);
    src.start();
    let pcm = (await offline.startRendering()).getChannelData(0);
    // Trim leading/trailing silence and normalise loudness: quiet, padded mic
    // clips are the main cause of mis-recognition.
    let peak = 0;
    for (const x of pcm) peak = Math.max(peak, Math.abs(x));
    const thr = Math.max(0.01, peak * 0.08);
    let a = pcm.findIndex((x) => Math.abs(x) > thr), b = pcm.length - 1;
    while (b > a && Math.abs(pcm[b]) <= thr) b--;
    if (a < 0) a = 0;
    pcm = pcm.slice(Math.max(0, a - rate * 0.25), Math.min(pcm.length, b + rate * 0.35));
    const gain = peak > 0 ? Math.min(8, 0.9 / peak) : 1;
    pcm = pcm.map((x) => x * gain);
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
   * Record until the speaker pauses, then return Gemma's cleaned transcript.
   * onState("listening" | "transcribing") lets the UI show progress.
   * Resolves "" if nothing was said within `maxWaitMs`.
   */
  async function listen(onState = () => {}, maxWaitMs = 12000, language = null) {
    if (window.SAHAYAK_REPLAY) throw new Error("Voice runs on-device with Gemma 4. Clone the repo and run it locally.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true } });
    const recorder = new MediaRecorder(stream);
    const chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let spoke = false, quietSince = null;
    const started = Date.now();
    const done = new Promise((resolve) => (recorder.onstop = resolve));
    const vad = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, x) => a + x * x, 0) / buf.length);
      if (rms > 0.02) { spoke = true; quietSince = null; } else if (spoke && !quietSince) quietSince = Date.now();
      const elapsed = Date.now() - started;
      if ((spoke && quietSince && Date.now() - quietSince > 1300) || elapsed > 45000 || (!spoke && elapsed > maxWaitMs)) {
        if (recorder.state === "recording") recorder.stop();
      }
    }, 80);
    active = { stop: () => recorder.state === "recording" && recorder.stop() };
    recorder.start();
    onState("listening");
    await done;
    clearInterval(vad);
    ctx.close();
    stream.getTracks().forEach((t) => t.stop());
    active = null;
    if (!spoke) return "";
    onState("transcribing");
    const audio = await toWavBase64(new Blob(chunks, { type: recorder.mimeType }));
    const r = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audio, language }) });
    const out = await r.json();
    if (out.error) throw new Error(out.error);
    return out.text;
  }

  /** Stop an in-progress recording early (it will still be transcribed). */
  function stopListening() { if (active) active.stop(); }

  /** One-shot dictation on a mic button: click to talk, stops when you pause. */
  async function toggle(button, onText) {
    if (active) { stopListening(); return; }
    const label = button.dataset.label || (button.dataset.label = button.textContent);
    try {
      const text = await listen((state) => {
        button.classList.toggle("rec", state === "listening");
        button.classList.toggle("busy", state === "transcribing");
        button.textContent = state === "listening" ? "● Listening… (stops when you pause)" : "⋯ Gemma is cleaning up what you said";
      });
      if (text) onText(text);
    } catch (err) {
      alert(err.message);
    } finally {
      button.classList.remove("rec", "busy");
      button.textContent = label;
    }
  }

  const LANG = { Hindi: "hi-IN", Telugu: "te-IN", English: "en-IN" };

  /** Browser speech synthesis fallback; resolves when finished. */
  function browserSay(text, language) {
    return new Promise((resolve) => {
      const lang = LANG[language] || "en-IN";
      const voices = speechSynthesis.getVoices().filter((v) => v.lang.replace("_", "-").startsWith(lang.slice(0, 2)));
      const voice = voices.find((v) => v.localService) || voices[0];
      if (!voice) { resolve(); return; }
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.voice = voice;
      u.lang = voice.lang;
      u.onend = resolve;
      u.onerror = resolve;
      speechSynthesis.speak(u);
    });
  }

  /** Speak text with the device's offline voices; resolves when playback ends. */
  async function say(text, language = "English") {
    stopSpeaking();
    if (!text) return;
    if (window.SAHAYAK_REPLAY) return browserSay(text, language);
    try {
      const r = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }) });
      if (!r.ok) throw new Error((await r.json()).error);
      const audio = new Audio(URL.createObjectURL(await r.blob()));
      playing = audio;
      await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; audio.onpause = resolve; audio.play().catch(resolve); });
    } catch (err) {
      console.warn("on-device TTS unavailable, using browser voices:", err);
      await browserSay(text, language);
    } finally {
      playing = null;
    }
  }

  function stopSpeaking() {
    if (playing) { playing.pause(); playing = null; }
    if (window.speechSynthesis) speechSynthesis.cancel();
  }

  /** Read-aloud button: click to play, click again to stop. */
  async function speak(text, language, button) {
    if (playing) { stopSpeaking(); return; }
    if (button) button.textContent = "■ Stop";
    await say(text, language);
    if (button) button.textContent = "🔊 Read aloud";
  }

  return { listen, stopListening, toggle, say, speak, stopSpeaking };
})();
