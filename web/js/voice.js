// Voice logging: the mic button, the recorder, and the card that comes back.
//
// The recording never touches disk and is kept in memory only until the turn
// succeeds, so Retry after a dropped connection does not need you to say it
// all again.
//
// Nothing here knows about OpenAI. It posts audio or text to /v1/assistant and
// renders what comes back.

import { $, h, replace, toast } from "./dom.js";

const MAX_SECONDS = 60;
const DISMISS_MS = 8000;
// Safari records mp4, Chrome and Firefox webm. The server accepts both and
// passes the type through to the transcriber.
const TYPES = ["audio/mp4", "audio/webm"];

export function pickMimeType(recorder = globalThis.MediaRecorder) {
  if (!recorder || !recorder.isTypeSupported) return TYPES[1];
  return TYPES.find((type) => recorder.isTypeSupported(type)) || TYPES[1];
}

// What to say when a turn fails. The server's own message is used where it
// has one worth showing; these cover the cases it never gets to answer.
export function messageFor(err) {
  if (!err) return "That didn't work. Try again.";
  switch (err.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "The microphone is blocked. Allow it for this app in Settings, then try again.";
    case "NotFoundError":
      return "No microphone was found on this device.";
    default: break;
  }
  if (err.unsupported) return "This browser can't record audio. Use Type instead.";
  switch (err.status) {
    case 0: return "You're offline. Voice needs a connection.";
    case 402: return err.message || "OpenAI credit ran out.";
    case 403: return "Voice isn't enabled for this account.";
    case 413: return "That recording was too long. Keep it under 60 seconds.";
    case 429: return err.message || "That's today's AI limit.";
    case 502:
    case 504: return err.message || "Couldn't reach the AI. Nothing was logged.";
    default: return err.message || "That didn't work. Try again.";
  }
}

export function createVoice({ host, api, actions, getDate, getToday }) {
  // The turn being worked on, and the last recording, kept for Retry.
  let recorder = null;
  let chunks = [];
  let stream = null;
  let meter = null;
  let ticker = null;
  let seconds = 0;
  // The turn waiting to be sent, and the key that identifies it. The key
  // stays the same across every retry of the same recording, so a reply lost
  // on a weak connection cannot turn into a second set in the log.
  let pending = null; // {audioBase64, audioMimeType} or {text}
  let pendingKey = null;
  let dismissTimer = null;
  let busy = false;

  const panel = h("div", { class: "voice-panel", hidden: true });
  const button = h("button", {
    class: "mic", type: "button", "aria-label": "Log by voice",
    onclick: () => (recorder ? stop() : start()),
  }, h("span", { class: "mic-glyph", "aria-hidden": "true" }, "●"));

  const typeButton = h("button", {
    class: "btn type-instead", type: "button", onclick: () => showTyping(),
  }, "Type instead");

  replace(host, panel, h("div", { class: "voice-bar" }, typeButton, button));

  // ------------------------------------------------------------- recording

  async function start() {
    clearTimeout(dismissTimer);
    if (!navigator.mediaDevices || !globalThis.MediaRecorder) {
      return fail(Object.assign(new Error("unsupported"), { unsupported: true }));
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      return fail(err);
    }
    const mimeType = pickMimeType();
    chunks = [];
    seconds = 0;
    recorder = new MediaRecorder(stream, { mimeType });
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => send(mimeType));
    recorder.start();
    buzz(12);
    listen();
    ticker = setInterval(() => {
      seconds += 1;
      showRecording();
      if (seconds >= MAX_SECONDS) stop();
    }, 1000);
    showRecording();
    button.classList.add("live");
    button.setAttribute("aria-label", "Stop recording and send");
  }

  function stop() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    teardown();
  }

  // Cancel throws the audio away rather than sending it.
  function cancel() {
    chunks = [];
    if (recorder && recorder.state !== "inactive") {
      recorder.removeEventListener("stop", send);
      recorder.onstop = null;
      recorder.stop();
    }
    recorder = null;
    teardown();
    hide();
  }

  function teardown() {
    clearInterval(ticker);
    ticker = null;
    if (meter) { meter.close(); meter = null; }
    if (stream) { stream.getTracks().forEach((track) => track.stop()); stream = null; }
    button.classList.remove("live");
    button.setAttribute("aria-label", "Log by voice");
  }

  // A level meter, so it is obvious the mic is actually hearing something.
  function listen() {
    try {
      const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (!Context) return;
      const context = new Context();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const frame = () => {
        if (!meter) return;
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const value of data) peak = Math.max(peak, Math.abs(value - 128));
        const level = Math.min(1, peak / 70);
        panel.style.setProperty("--level", String(level));
        requestAnimationFrame(frame);
      };
      meter = context;
      frame();
    } catch {
      // No Web Audio: the timer and the ring still say it is recording.
    }
  }

  const buzz = (ms) => { try { navigator.vibrate && navigator.vibrate(ms); } catch { /* not supported */ } };

  async function send(mimeType) {
    recorder = null;
    const blob = new Blob(chunks, { type: mimeType });
    chunks = [];
    if (!blob.size) return hide();
    pending = { audioBase64: await toBase64(blob), audioMimeType: mimeType };
    pendingKey = newKey();
    deliver();
  }

  function toBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(reader.error);
      reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
      reader.readAsDataURL(blob);
    });
  }

  // --------------------------------------------------------------- sending

  async function deliver() {
    if (!pending || busy) return;
    busy = true;
    showWorking(pending.text ? "Logging…" : "Transcribing…");
    try {
      const result = await api.assistant({
        ...pending,
        date: getDate() || getToday(),
        today: getToday(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      }, pendingKey);
      pending = null; // it landed; the recording is no longer needed
      pendingKey = null;
      buzz(20);
      actions.applied(result);
      showResult(result);
    } catch (err) {
      fail(err);
    } finally {
      busy = false;
    }
  }

  function typeInstead(text) {
    pending = { text };
    pendingKey = newKey();
    deliver();
  }

  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));

  // ------------------------------------------------------------- the panel

  function show(...children) {
    clearTimeout(dismissTimer);
    panel.hidden = false;
    replace(panel, ...children);
  }

  function hide() {
    clearTimeout(dismissTimer);
    panel.hidden = true;
    replace(panel);
  }

  function showRecording() {
    show(
      h("div", { class: "rec" },
        h("span", { class: "rec-ring", "aria-hidden": "true" }),
        h("span", { class: "rec-time", role: "timer" },
          `0:${String(seconds).padStart(2, "0")}`),
        h("span", { class: "rec-hint" }, seconds >= MAX_SECONDS - 10
          ? `Stops at ${MAX_SECONDS}s` : "Tap the mic again to send")),
      h("div", { class: "voice-actions" },
        h("button", { class: "btn", type: "button", onclick: () => cancel() }, "× Cancel")));
  }

  // The same endpoint, without the microphone: useful in a quiet gym, and the
  // way in when recording is not available at all.
  function showTyping(text = "") {
    const field = h("input", {
      class: "box", type: "text", value: text, maxlength: 500,
      "aria-label": "Tell the log what you did",
      placeholder: "Seated row, 3 sets of 10 to 12 at 40 pounds",
      enterkeyhint: "send",
    });
    const submit = () => {
      const said = field.value.trim();
      if (said) typeInstead(said);
    };
    field.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
    show(
      h("div", { class: "voice-type" }, field),
      h("div", { class: "voice-actions" },
        h("button", { class: "btn primary", type: "button", onclick: submit }, "Send"),
        h("button", { class: "btn", type: "button", onclick: () => hide() }, "Cancel")));
    field.focus();
  }

  function showWorking(label) {
    show(h("p", { class: "meta" }, label));
  }

  function showResult(result) {
    const children = [
      result.transcript ? h("p", { class: "meta transcript" }, `"${result.transcript}"`) : null,
      h("p", { class: "reply" }, result.reply),
      (result.assumptions || []).length
        ? h("ul", { class: "assumptions" }, result.assumptions.map((a) => h("li", {}, a)))
        : null,
      result.question ? h("p", { class: "question" }, result.question) : null,
      h("div", { class: "voice-actions" },
        result.question
          ? h("button", { class: "btn primary", type: "button", onclick: () => start() }, "Answer")
          : null,
        result.question
          ? h("button", { class: "btn", type: "button", onclick: () => showTyping() }, "Type")
          : null,
        result.undoToken
          ? h("button", { class: "btn", type: "button", onclick: () => undo(result.undoToken) }, "Undo")
          : null,
        h("button", { class: "btn", type: "button", onclick: () => hide() }, "Done")),
    ];
    show(...children);
    // A question stays up until it is answered or dismissed.
    if (!result.question) dismissTimer = setTimeout(hide, DISMISS_MS);
  }

  async function undo(token) {
    try {
      const result = await api.undoAssistant(token);
      actions.applied(result);
      hide();
      toast("Undone.");
    } catch (err) {
      toast(err.status === 409 ? err.message : messageFor(err));
    }
  }

  // Before re-sending a recording, ask whether the server already has it.
  // A turn that landed and lost its reply is replayed by the key anyway, but
  // asking first means not paying for the upload again.
  async function retry() {
    if (!pending || !pendingKey) return;
    showWorking("Checking…");
    const state = await api.requestState(pendingKey);
    if (state === "in_progress") {
      return fail({ message: "That one is still going. Give it a moment and try again." });
    }
    deliver();
  }

  function fail(err) {
    teardown();
    const retryable = Boolean(pending);
    show(
      h("p", { class: "notice bad", role: "alert" }, messageFor(err)),
      h("div", { class: "voice-actions" },
        // The recording is still here, so Retry sends it rather than asking
        // for it again.
        retryable ? h("button", { class: "btn primary", type: "button", onclick: () => retry() }, "Retry") : null,
        h("button", { class: "btn", type: "button", onclick: () => { pending = null; pendingKey = null; hide(); } }, "Dismiss")));
  }

  return {
    typeInstead,
    showTyping,
    busy: () => busy,
    // Signed out, or AI turned off: the button and anything open go away.
    destroy() {
      cancel();
      replace(host);
    },
  };
}
