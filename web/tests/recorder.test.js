// The microphone's state machine, against a fake MediaRecorder and a fake
// microphone. What it sounds like, and how the buttons feel on a phone, is
// still checked by hand against the checklist in docs/08.

import { test } from "node:test";
import assert from "node:assert/strict";

import { createRecorder } from "../js/recorder.js";

const flush = () => new Promise((resolve) => setImmediate(resolve));

function fakeMic() {
  const opened = [];
  const prompts = [];
  return {
    opened,
    prompts,
    // Each call waits until the test answers the prompt.
    getUserMedia() {
      return new Promise((resolve, reject) => {
        prompts.push({
          allow() {
            const track = Object.assign(new EventTarget(), { stopped: false, stop() { this.stopped = true; } });
            const stream = { getTracks: () => [track], track };
            opened.push(stream);
            resolve(stream);
          },
          refuse: () => reject(Object.assign(new Error("denied"), { name: "NotAllowedError" })),
        });
      });
    },
  };
}

// Hands over its audio, then says it has stopped, a moment after stop(), the
// way browsers do. `says` is what it hands over.
function fakeRecorder({ says = "audio", stops = true } = {}) {
  return class FakeRecorder extends EventTarget {
    static made = [];
    static isTypeSupported = (type) => type === "audio/webm";
    constructor(stream, { mimeType }) {
      super();
      this.stream = stream;
      this.mimeType = mimeType;
      this.state = "inactive";
      FakeRecorder.made.push(this);
    }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      this.micLiveAtStop = !this.stream.track.stopped;
      if (!stops) return;
      queueMicrotask(() => {
        this.micLiveAtData = !this.stream.track.stopped;
        if (says) this.dispatchEvent(Object.assign(new Event("dataavailable"), { data: new Blob([says]) }));
        this.dispatchEvent(new Event("stop"));
      });
    }
  };
}

function setup(options = {}) {
  const mic = fakeMic();
  const sent = [];
  const errors = [];
  const states = [];
  const Recorder = fakeRecorder(options);
  const recorder = createRecorder({
    media: mic,
    Recorder,
    maxSeconds: 60,
    onState: (state) => states.push(state),
    onSend: (take) => sent.push(take),
    onError: (err) => errors.push(err),
  });
  return { mic, sent, errors, states, recorder, made: Recorder.made };
}

async function recording(world) {
  world.recorder.start();
  world.mic.prompts.at(-1).allow();
  await flush();
  assert.equal(world.recorder.state(), "recording");
  return world.mic.opened.at(-1);
}

test("Send while recording stops and sends what was said, then lets go of the mic", async () => {
  const world = setup();
  const stream = await recording(world);
  world.recorder.send();
  assert.equal(world.recorder.state(), "stopping");
  await flush();
  assert.equal(world.sent.length, 1);
  assert.equal(await world.sent[0].blob.text(), "audio");
  assert.equal(world.sent[0].mimeType, "audio/webm");
  assert.equal(world.recorder.state(), "idle");
  assert.equal(stream.track.stopped, true, "the mic is released");
});

test("the mic stays open until the recorder has handed over its audio", async () => {
  // Safari can drop the whole take if the tracks stop first.
  const world = setup();
  const stream = await recording(world);
  world.recorder.send();
  await flush();
  const [made] = world.made;
  assert.equal(made.micLiveAtStop, true);
  assert.equal(made.micLiveAtData, true);
  assert.equal(stream.track.stopped, true, "and released after");
});

test("Stop keeps the take without sending it; Send then sends it", async () => {
  const world = setup();
  const stream = await recording(world);
  world.recorder.stop();
  await flush();
  assert.equal(world.recorder.state(), "stopped");
  assert.equal(world.sent.length, 0, "nothing sent yet");
  assert.equal(stream.track.stopped, true, "the mic is off while it waits");
  world.recorder.send();
  assert.equal(world.sent.length, 1);
  assert.equal(await world.sent[0].blob.text(), "audio");
  assert.equal(world.recorder.state(), "idle");
});

test("Cancel while recording sends nothing and lets go of the mic", async () => {
  const world = setup();
  const stream = await recording(world);
  world.recorder.cancel();
  await flush();
  assert.equal(world.sent.length, 0);
  assert.equal(world.errors.length, 0);
  assert.equal(world.recorder.state(), "idle");
  assert.equal(stream.track.stopped, true);
});

test("Discard after Stop throws the take away", async () => {
  const world = setup();
  await recording(world);
  world.recorder.stop();
  await flush();
  world.recorder.cancel();
  world.recorder.send();
  assert.equal(world.sent.length, 0);
  assert.equal(world.recorder.state(), "idle");
});

test("Record again after Stop starts a new take and drops the old one", async () => {
  const world = setup({ says: "first" });
  await recording(world);
  world.recorder.stop();
  await flush();
  await recording(world);
  world.recorder.send();
  await flush();
  assert.equal(world.sent.length, 1, "only the new take is sent");
  assert.equal(world.mic.opened.length, 2);
});

test("a second tap while the permission prompt is up does not open a second mic", async () => {
  const world = setup();
  world.recorder.start();
  world.recorder.start();
  assert.equal(world.mic.prompts.length, 1);
});

test("Cancel while the permission prompt is up hands the mic back when it arrives", async () => {
  const world = setup();
  world.recorder.start();
  assert.equal(world.recorder.state(), "starting");
  world.recorder.cancel();
  assert.equal(world.recorder.state(), "idle");
  world.mic.prompts[0].allow();
  await flush();
  assert.equal(world.mic.opened[0].track.stopped, true, "not left recording in the background");
  assert.equal(world.recorder.state(), "idle");
});

test("a late answer to an old prompt does not disturb a newer recording", async () => {
  const world = setup();
  world.recorder.start();
  world.recorder.cancel();
  const fresh = await recording(world);
  world.mic.prompts[0].allow();
  await flush();
  assert.equal(world.recorder.state(), "recording");
  assert.equal(fresh.track.stopped, false);
  assert.equal(world.mic.opened[1].track.stopped, true, "the stale stream is released");
});

test("a refused mic reports why and goes back to idle", async () => {
  const world = setup();
  world.recorder.start();
  world.mic.prompts[0].refuse();
  await flush();
  assert.equal(world.recorder.state(), "idle");
  assert.equal(world.errors[0].name, "NotAllowedError");
});

test("a browser with no recorder says so instead of doing nothing", () => {
  const errors = [];
  const recorder = createRecorder({ media: fakeMic(), Recorder: undefined, onError: (e) => errors.push(e) });
  recorder.start();
  assert.equal(errors[0].unsupported, true);
  assert.equal(recorder.state(), "idle");
});

test("an empty take says so instead of disappearing", async () => {
  const world = setup({ says: "" });
  await recording(world);
  world.recorder.send();
  await flush();
  assert.equal(world.sent.length, 0);
  assert.equal(world.errors[0].empty, true);
});

test("the mic ending on its own (a call, iOS taking it back) keeps what was said", async () => {
  const world = setup();
  const stream = await recording(world);
  stream.track.dispatchEvent(new Event("ended"));
  await flush();
  assert.equal(world.recorder.state(), "stopped");
  world.recorder.send();
  assert.equal(world.sent.length, 1);
});

test("at the time limit it stops and sends by itself", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  const world = setup();
  await recording(world);
  t.mock.timers.tick(59_000);
  assert.equal(world.recorder.state(), "recording");
  assert.equal(world.recorder.seconds(), 59);
  t.mock.timers.tick(1_000);
  await flush();
  assert.equal(world.sent.length, 1);
  assert.equal(world.sent[0].seconds, 60);
});

test("a recorder that never says it stopped is not left hanging", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  const world = setup({ stops: false });
  const stream = await recording(world);
  world.recorder.send();
  assert.equal(world.recorder.state(), "stopping");
  t.mock.timers.tick(3_000);
  assert.equal(world.recorder.state(), "idle");
  assert.equal(stream.track.stopped, true);
  assert.equal(world.errors[0].empty, true, "with no audio, it says nothing was recorded");
});

test("each state change is reported once, in order", async () => {
  const world = setup();
  await recording(world);
  world.recorder.stop();
  await flush();
  world.recorder.send();
  assert.deepEqual(world.states, ["starting", "recording", "stopping", "stopped", "idle"]);
});
