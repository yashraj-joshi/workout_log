// What voice.js decides without a microphone: which container to record in,
// how the timer reads, and what to say when a turn fails. The recorder's own
// states are in recorder.test.js; how it feels on a phone is checked by hand
// against the checklist in docs/08.

import { test } from "node:test";
import assert from "node:assert/strict";

import { clock, messageFor, pickMimeType } from "../js/voice.js";

test("Safari gets mp4, Chrome gets webm, and an unknown browser still records", () => {
  const supports = (...types) => ({ isTypeSupported: (type) => types.includes(type) });
  assert.equal(pickMimeType(supports("audio/mp4", "audio/webm")), "audio/mp4", "Safari's container wins");
  assert.equal(pickMimeType(supports("audio/webm")), "audio/webm");
  assert.equal(pickMimeType(supports()), "audio/webm", "fall back rather than refuse to record");
  assert.equal(pickMimeType(undefined), "audio/webm");
});

test("a blocked microphone says how to unblock it", () => {
  const message = messageFor({ name: "NotAllowedError" });
  assert.match(message, /microphone is blocked/);
  assert.match(message, /Settings/, "on iOS the permission lives there, not in the page");
  assert.match(messageFor({ name: "SecurityError" }), /microphone is blocked/);
  assert.match(messageFor({ name: "NotFoundError" }), /No microphone/);
});

test("a browser that cannot record points at Type instead", () => {
  assert.match(messageFor({ unsupported: true }), /Type instead/);
});

test("an empty recording says so", () => {
  assert.equal(messageFor({ empty: true }), "Nothing was recorded. Try again.");
});

test("the timer reads in minutes and seconds, up to the one-minute limit", () => {
  assert.equal(clock(0), "0:00");
  assert.equal(clock(9), "0:09");
  assert.equal(clock(59), "0:59");
  assert.equal(clock(60), "1:00", "not 0:60");
});

test("each failure the server can return has its own wording", () => {
  assert.match(messageFor({ status: 0 }), /offline/);
  assert.match(messageFor({ status: 403 }), /isn't enabled/);
  assert.match(messageFor({ status: 413 }), /under 60 seconds/);
  // These two carry the server's own message, which names the limit or the
  // account, so it is shown as-is rather than replaced.
  assert.equal(messageFor({ status: 429, message: "Daily AI limit of 100 reached." }),
    "Daily AI limit of 100 reached.");
  assert.equal(messageFor({ status: 402, message: "OpenAI credit ran out. Top up the account." }),
    "OpenAI credit ran out. Top up the account.");
  assert.match(messageFor({ status: 429 }), /limit/, "and a sensible default when it doesn't");
  assert.match(messageFor({ status: 502 }), /Couldn't reach the AI/);
  assert.match(messageFor({ status: 504, message: "That took too long." }), /took too long/);
});

test("anything unrecognised still says something useful", () => {
  assert.equal(messageFor(null), "That didn't work. Try again.");
  assert.equal(messageFor({}), "That didn't work. Try again.");
  assert.equal(messageFor({ status: 500, message: "The server had a problem." }),
    "The server had a problem.");
});
