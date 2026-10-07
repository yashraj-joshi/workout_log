import { test } from "node:test";
import assert from "node:assert/strict";

import { ApiError, createApi, errorFrom, send } from "../js/api.js";
import { fakeFetch } from "./helpers.js";

// A session stub: hands out "t1", and "t2" after a refresh.
function fakeSession() {
  let token = "t1";
  const s = {
    refreshes: 0,
    accessToken: async () => token,
    refresh: async () => { s.refreshes++; token = "t2"; },
  };
  return s;
}

const noSleep = async () => {};
let keys = 0;
const newKey = () => `key-${++keys}`;

test("sends the bearer token and returns status, ETag and body", async () => {
  const fetch = fakeFetch({ status: 200, body: { days: [] }, headers: { ETag: '"7"' } });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  const res = await api.listDays({ etag: '"6"' });
  assert.equal(res.status, 200);
  assert.equal(res.etag, '"7"');
  assert.deepEqual(res.data, { days: [] });
  assert.equal(fetch.calls[0].headers.Authorization, "Bearer t1");
  assert.equal(fetch.calls[0].headers["If-None-Match"], '"6"');
});

test("a 304 comes back with no body", async () => {
  const fetch = fakeFetch({ status: 304, headers: { ETag: '"6"' } });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  const res = await api.listDays({ etag: '"6"' });
  assert.equal(res.status, 304);
  assert.equal(res.data, null);
});

test("a 401 refreshes once and retries with the new token", async () => {
  const fetch = fakeFetch({ status: 401, body: { message: "Unauthorized" } }, { status: 200, body: { email: "a@b.c" } });
  const session = fakeSession();
  const api = createApi({ fetch, session, sleep: noSleep, newKey });
  assert.deepEqual(await api.me(), { email: "a@b.c" });
  assert.equal(session.refreshes, 1);
  assert.equal(fetch.calls[1].headers.Authorization, "Bearer t2");
});

test("a second 401 is an error, not a loop", async () => {
  const fetch = fakeFetch({ status: 401 }, { status: 401 });
  const session = fakeSession();
  const api = createApi({ fetch, session, sleep: noSleep, newKey });
  await assert.rejects(api.me(), (err) => err instanceof ApiError && err.status === 401);
  assert.equal(session.refreshes, 1);
  assert.equal(fetch.calls.length, 2);
});

test("an idempotent write retries twice on 503 with the same key, then gives up", async () => {
  const fetch = fakeFetch({ status: 503 }, { status: 503 }, { status: 503 });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  await assert.rejects(api.request("POST", "/v1/days/2026-09-21/exercises", { body: { exercise: "Plank" }, idempotent: true }),
    (err) => err.status === 503);
  assert.equal(fetch.calls.length, 3);
  const sent = fetch.calls.map((c) => c.headers["Idempotency-Key"]);
  assert.ok(sent[0]);
  assert.deepEqual(sent, [sent[0], sent[0], sent[0]]);
});

test("each new action gets a new key", async () => {
  const fetch = fakeFetch({ status: 201, body: {} }, { status: 201, body: {} });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  await api.request("POST", "/v1/days/2026-09-21/exercises", { body: {}, idempotent: true });
  await api.request("POST", "/v1/days/2026-09-21/exercises", { body: {}, idempotent: true });
  assert.notEqual(fetch.calls[0].headers["Idempotency-Key"], fetch.calls[1].headers["Idempotency-Key"]);
});

test("a network error on a keyed write is retried and can succeed", async () => {
  const fetch = fakeFetch(new TypeError("Failed to fetch"), { status: 201, body: { ok: 1 } });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  const res = await api.request("POST", "/v1/days/2026-09-21/exercises", { body: {}, idempotent: true });
  assert.equal(res.status, 201);
  assert.equal(fetch.calls.length, 2);
});

test("a POST without a key is never retried", async () => {
  const fetch = fakeFetch({ status: 503 });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  await assert.rejects(api.request("POST", "/v1/days/2026-09-21/finish"), (err) => err.status === 503);
  assert.equal(fetch.calls.length, 1);
});

test("client errors and 500s are not retried", async () => {
  for (const status of [400, 409, 422, 500]) {
    const fetch = fakeFetch({ status, body: { error: { code: "x", message: "no" } } });
    const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
    await assert.rejects(api.request("PUT", "/v1/days/2026-09-21/exercises/01", { body: {} }), (err) => err.status === status);
    assert.equal(fetch.calls.length, 1, `status ${status}`);
  }
});

test("GET is not retried; the next poll is the retry", async () => {
  const fetch = fakeFetch({ status: 503 });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  await assert.rejects(api.listDays(), (err) => err.status === 503);
  assert.equal(fetch.calls.length, 1);
});

test("a retried DELETE that finds nothing counts as done", async () => {
  const fetch = fakeFetch({ status: 504 }, { status: 404, body: { error: { code: "not_found", message: "gone" } } });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  const res = await api.request("DELETE", "/v1/days/2026-09-21/exercises/01");
  assert.equal(res.status, 204);
});

test("a first-try DELETE 404 is still an error", async () => {
  const fetch = fakeFetch({ status: 404, body: { error: { code: "not_found", message: "gone" } } });
  const api = createApi({ fetch, session: fakeSession(), sleep: noSleep, newKey });
  await assert.rejects(api.request("DELETE", "/v1/days/2026-09-21/exercises/01"), (err) => err.code === "not_found");
});

test("a reply that never comes ends at the deadline", async () => {
  const hang = (path, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
  await assert.rejects(send(hang, "/v1/me", { timeoutMs: 20 }), (err) => err.status === 0 && err.code === "timeout");
});

test("no connection is status 0, code offline", async () => {
  const fail = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(send(fail, "/v1/me"), (err) => err.status === 0 && err.code === "offline");
});

test("errors read our envelope, and API Gateway's own shape", () => {
  const ours = errorFrom(409, { error: { code: "version_conflict", message: "Changed." } });
  assert.equal(ours.code, "version_conflict");
  assert.equal(ours.message, "Changed.");
  const gateway = errorFrom(401, { message: "Unauthorized" });
  assert.equal(gateway.code, "unauthorized");
  assert.equal(errorFrom(502, null).code, "server_error");
});

test("the assistant carries the key it was given, unchanged, on every try", async () => {
  const fetchImpl = fakeFetch(
    { status: 200, body: { reply: "Logged it.", changedDates: [], days: [] } },
    { status: 200, body: { reply: "Logged it.", changedDates: [], days: [] } },
  );
  const api = createApi({ fetch: fetchImpl, session: fakeSession() });
  const key = "11111111-2222-3333-4444-555555555555";

  await api.assistant({ text: "seated row", date: "2026-10-07", today: "2026-10-07" }, key);
  await api.assistant({ text: "seated row", date: "2026-10-07", today: "2026-10-07" }, key);

  assert.equal(fetchImpl.calls[0].headers["Idempotency-Key"], key);
  assert.equal(fetchImpl.calls[1].headers["Idempotency-Key"], key,
    "the same recording keeps its key, so the server replays instead of logging twice");
});

test("a lost assistant reply is not retried behind your back", async () => {
  // One network failure, and that is the end of it: a silent retry could pay
  // for a second model call and log a second set.
  const fetchImpl = fakeFetch(new TypeError("network"));
  const api = createApi({ fetch: fetchImpl, session: fakeSession(), sleep: async () => {} });

  await assert.rejects(() => api.assistant({ text: "x", date: "2026-10-07", today: "2026-10-07" }, "k"),
    (err) => err.status === 0);
  assert.equal(fetchImpl.calls.length, 1);
});

test("asking about a turn says whether the server already has it", async () => {
  const api = (...replies) => createApi({ fetch: fakeFetch(...replies), session: fakeSession() });

  assert.equal(await api({ status: 200, body: { state: "done" } }).requestState("k"), "done");
  assert.equal(await api({ status: 200, body: { state: "in_progress" } }).requestState("k"), "in_progress");
  // Never sent, so it is safe to send now.
  assert.equal(await api({ status: 404, body: { error: { code: "not_found" } } }).requestState("k"), null);
  // Could not tell: treated as unknown rather than as "safe to resend".
  assert.equal(await api(new TypeError("network")).requestState("k"), "unknown");
});
