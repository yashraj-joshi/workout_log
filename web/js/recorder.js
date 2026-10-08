// The microphone as a small state machine with no screen of its own, so the
// awkward moments are tested without a browser: a second tap while the
// permission prompt is up, Cancel, the app going to the background mid-take.
//
//   idle -> starting -> recording -> stopping -> stopped -> send -> idle
//             |                        |           \-> cancel -> idle
//             \-> idle                 \-> idle (sent at once, or discarded)
//
// The mic is released once the recorder has handed over its audio, not
// before: Safari can drop the whole take if the tracks stop first.

const STOP_GRACE_MS = 3000;
// Safari records mp4, Chrome and Firefox webm. The server accepts both and
// passes the type through to the transcriber.
const TYPES = ["audio/mp4", "audio/webm"];

export function pickMimeType(recorder = globalThis.MediaRecorder) {
  if (!recorder || !recorder.isTypeSupported) return TYPES[1];
  return TYPES.find((type) => recorder.isTypeSupported(type)) || TYPES[1];
}

export function createRecorder({
  media = globalThis.navigator && globalThis.navigator.mediaDevices,
  Recorder = globalThis.MediaRecorder,
  maxSeconds = 60,
  onState = () => {}, // (state) on every change
  onTick = () => {}, // (seconds) once a second while recording
  onSend = () => {}, // ({ blob, mimeType, seconds }) a finished take to send
  onError = () => {}, // (err) the mic was refused, the take was empty, or the recorder broke
} = {}) {
  let state = "idle";
  // Bumped by every start and cancel, so a permission prompt answered after
  // Cancel can tell it is stale and hand the mic straight back.
  let attempt = 0;
  let stream = null;
  let recorder = null;
  let mimeType = null;
  let chunks = [];
  let seconds = 0;
  let ticker = null;
  let grace = null;
  let after = null; // once the recorder stops: "send", "keep" or "discard"
  let kept = null; // a stopped take waiting for Send

  const set = (next) => { state = next; onState(next); };

  async function start() {
    if (state !== "idle" && state !== "stopped") return;
    kept = null;
    if (!media || !media.getUserMedia || !Recorder) {
      set("idle");
      return onError(Object.assign(new Error("unsupported"), { unsupported: true }));
    }
    const mine = ++attempt;
    set("starting");
    let got;
    try {
      got = await media.getUserMedia({ audio: true });
    } catch (err) {
      if (mine !== attempt) return;
      set("idle");
      return onError(err);
    }
    if (mine !== attempt) return release(got);

    stream = got;
    mimeType = pickMimeType(Recorder);
    chunks = [];
    seconds = 0;
    try {
      const rec = new Recorder(stream, { mimeType });
      recorder = rec;
      rec.addEventListener("dataavailable", (event) => {
        if (rec === recorder && event.data && event.data.size) chunks.push(event.data);
      });
      rec.addEventListener("stop", () => { if (rec === recorder) stopped(); });
      rec.addEventListener("error", (event) => { if (rec === recorder) broke(event.error || event); });
      rec.start();
    } catch (err) {
      return broke(err);
    }
    // A phone call, or iOS taking the mic back, ends the track. Keep what
    // was said rather than losing it.
    for (const track of stream.getTracks()) track.addEventListener("ended", () => stop());
    ticker = setInterval(tick, 1000);
    set("recording");
  }

  function tick() {
    seconds += 1;
    onTick(seconds);
    if (seconds >= maxSeconds) send();
  }

  function finish(next) {
    after = next;
    clearInterval(ticker);
    ticker = null;
    set("stopping");
    // If the recorder never says it has stopped, go on with what it has.
    grace = setTimeout(stopped, STOP_GRACE_MS);
    try {
      if (recorder.state === "inactive") stopped();
      else recorder.stop();
    } catch {
      stopped();
    }
  }

  function stopped() {
    if (state !== "stopping") return;
    const take = { blob: new Blob(chunks, { type: mimeType }), mimeType, seconds };
    reset();
    if (after === "discard") return set("idle");
    if (!take.blob.size) {
      set("idle");
      return onError(Object.assign(new Error("empty"), { empty: true }));
    }
    if (after === "send") {
      set("idle");
      return onSend(take);
    }
    kept = take;
    set("stopped");
  }

  function broke(err) {
    reset();
    kept = null;
    set("idle");
    onError(err);
  }

  function reset() {
    clearInterval(ticker);
    clearTimeout(grace);
    ticker = null;
    grace = null;
    release(stream);
    stream = null;
    recorder = null;
    chunks = [];
  }

  function release(source) {
    if (source) source.getTracks().forEach((track) => track.stop());
  }

  // Send: while recording, stop and send at once; once stopped, send the take.
  function send() {
    if (state === "recording") return finish("send");
    if (state === "stopped") {
      const take = kept;
      kept = null;
      set("idle");
      return onSend(take);
    }
    if (state === "starting") return cancel();
  }

  // Stop: end the take but hold it, so it can be sent, redone or discarded.
  function stop() {
    if (state === "recording") return finish("keep");
    if (state === "starting") return cancel();
  }

  // Cancel throws the audio away, whatever stage it has reached.
  function cancel() {
    if (state === "starting") {
      attempt += 1;
      return set("idle");
    }
    if (state === "recording") return finish("discard");
    if (state === "stopping") after = "discard";
    if (state === "stopped") {
      kept = null;
      set("idle");
    }
  }

  return {
    start,
    stop,
    send,
    cancel,
    state: () => state,
    seconds: () => (kept ? kept.seconds : seconds),
    stream: () => stream,
  };
}
