// Voice logging: the mic button, the recorder, and the card that comes back.
//
// The recording never touches disk and is kept in memory only until the turn
// succeeds, so Retry after a dropped connection does not need you to say it
// all again.
//
// Nothing here knows about OpenAI. It posts audio or text to /v1/assistant and
// renders what comes back.

import { h, replace, toast } from "./dom.js";
import { createRecorder } from "./recorder.js";

export { pickMimeType } from "./recorder.js";

const MAX_SECONDS = 60;
const DISMISS_MS = 8000;

export const clock = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

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
  if (err.empty) return "Nothing was recorded. Try again.";
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
  // The turn waiting to be sent, and the key that identifies it. The key
  // stays the same across every retry of the same recording, so a reply lost
  // on a weak connection cannot turn into a second set in the log.
  let pending = null; // {audioBase64, audioMimeType} or {text}
  let pendingKey = null;
  let dismissTimer = null;
  let busy = false;
  let meter = null;
  // True while the panel shows the recording itself, so it closes when the
  // recording ends without anything to show (Cancel, the app backgrounded
  // before the mic started).
  let recordingPanel = false;

  const rec = createRecorder({
    maxSeconds: MAX_SECONDS,
    onState: changed,
    onTick: showTime,
    onSend: sendTake,
    onError: fail,
  });

  const panel = h("div", { class: "voice-panel", hidden: true });
  const glyph = h("span", { class: "mic-glyph", "aria-hidden": "true" }, "●");
  // Idle, it starts a recording. Recording, or stopped, it sends.
  const button = h("button", {
    class: "mic", type: "button", "aria-label": "Log by voice",
    onclick: () => (rec.state() === "idle" ? rec.start() : rec.send()),
  }, glyph);
  const stopButton = h("button", {
    class: "mic-stop", type: "button", "aria-label": "Stop recording", hidden: true,
    onclick: () => rec.stop(),
  }, h("span", { class: "mic-stop-glyph", "aria-hidden": "true" }));

  const typeButton = h("button", {
    class: "btn type-instead", type: "button", onclick: () => showTyping(),
  }, "Type instead");

  replace(host, panel, h("div", { class: "voice-bar" }, typeButton, stopButton, button));

  // Recording in the background is cut off by iOS anyway. Stop and keep it,
  // so it is there to send on the way back.
  const onVisibility = () => { if (document.hidden) rec.stop(); };
  document.addEventListener("visibilitychange", onVisibility);

  // ------------------------------------------------------------- recording

  function changed(state) {
    renderBar();
    if (state === "recording") listen();
    else quiet();
    if (state === "starting") showStarting();
    else if (state === "recording") showRecording();
    else if (state === "stopped") showStopped();
    else if (state === "idle" && recordingPanel) hide();
    if (state === "recording") buzz(12);
  }

  function renderBar() {
    const state = rec.state();
    const idle = state === "idle";
    typeButton.hidden = !idle;
    typeButton.disabled = busy;
    stopButton.hidden = state !== "recording";
    button.disabled = idle ? busy : state === "starting" || state === "stopping";
    button.classList.toggle("send", !idle);
    glyph.textContent = idle ? "●" : "↑";
    button.setAttribute("aria-label", idle ? "Log by voice"
      : state === "stopped" ? "Send recording" : "Stop and send");
  }

  // A level meter, so it is obvious the mic is actually hearing something.
  function listen() {
    try {
      const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
      const stream = rec.stream();
      if (!Context || !stream) return;
      const context = new Context();
      // Made after the permission prompt, outside the tap, so Safari can
      // start it suspended.
      if (context.state === "suspended") context.resume().catch(() => {});
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const frame = () => {
        if (meter !== context) return;
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const value of data) peak = Math.max(peak, Math.abs(value - 128));
        panel.style.setProperty("--level", String(Math.min(1, peak / 70)));
        requestAnimationFrame(frame);
      };
      meter = context;
      frame();
    } catch {
      // No Web Audio: the timer and the ring still say it is recording.
    }
  }

  function quiet() {
    try {
      if (meter) Promise.resolve(meter.close()).catch(() => {});
    } catch {
      // Already closed.
    }
    meter = null;
    panel.style.removeProperty("--level");
  }

  const buzz = (ms) => { try { navigator.vibrate && navigator.vibrate(ms); } catch { /* not supported */ } };

  async function sendTake(take) {
    showWorking("Sending…");
    try {
      pending = { audioBase64: await toBase64(take.blob), audioMimeType: take.mimeType };
    } catch (err) {
      return fail(err);
    }
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
    renderBar();
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
      renderBar();
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
    recordingPanel = false;
    panel.hidden = false;
    replace(panel, ...children);
  }

  function hide() {
    clearTimeout(dismissTimer);
    recordingPanel = false;
    panel.hidden = true;
    replace(panel);
  }

  // The recording panels. Built once per stage and then only the timer text
  // changes, so a tap on Cancel is never lost to a redraw.
  let timeText = null;
  let hintText = null;

  function showStage(row, ...buttons) {
    show(row, h("div", { class: "voice-actions" }, ...buttons));
    recordingPanel = true;
  }

  const cancelButton = (label) =>
    h("button", { class: "btn", type: "button", onclick: () => rec.cancel() }, label);

  function showStarting() {
    showStage(h("p", { class: "meta" }, "Starting the mic…"), cancelButton("× Cancel"));
  }

  function showRecording() {
    timeText = document.createTextNode("");
    hintText = document.createTextNode("");
    showStage(
      h("div", { class: "rec" },
        h("span", { class: "rec-ring", "aria-hidden": "true" }),
        h("span", { class: "rec-time", role: "timer" }, timeText),
        h("span", { class: "rec-hint" }, hintText)),
      cancelButton("× Cancel"));
    showTime(rec.seconds());
  }

  function showTime(seconds) {
    if (!timeText) return;
    timeText.data = clock(seconds);
    hintText.data = seconds >= MAX_SECONDS - 10
      ? `Sends itself at ${clock(MAX_SECONDS)}`
      : "↑ sends · ■ stops";
  }

  function showStopped() {
    timeText = null;
    hintText = null;
    showStage(
      h("div", { class: "rec" },
        h("span", { class: "rec-time" }, clock(rec.seconds())),
        h("span", { class: "rec-hint" }, "Recorded. ↑ sends it.")),
      h("button", { class: "btn", type: "button", onclick: () => rec.start() }, "Record again"),
      cancelButton("× Discard"));
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
          ? h("button", { class: "btn primary", type: "button", onclick: () => rec.start() }, "Answer")
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
      rec.cancel();
      quiet();
      document.removeEventListener("visibilitychange", onVisibility);
      replace(host);
    },
  };
}
