import { test } from "node:test";
import assert from "node:assert/strict";

import { createSession, decodeJwt } from "../js/session.js";
import { fakeFetch, jwt } from "./helpers.js";

const ID = jwt({ sub: "u1", email: "ana@example.com", name: "Ana Ñ" });

test("adopt sends the refresh token once, with the access token, then keeps only access and ID", async () => {
  const fetch = fakeFetch({ status: 200, body: { ok: true } });
  const session = createSession({ fetch });
  await session.adopt({ accessToken: "a1", idToken: ID, refreshToken: "r1", expiresIn: 900 });
  const call = fetch.calls[0];
  assert.equal(call.path, "/v1/auth/session");
  assert.equal(call.method, "POST");
  assert.equal(call.headers.Authorization, "Bearer a1");
  assert.deepEqual(call.body, { refreshToken: "r1" });
  assert.equal(await session.accessToken(), "a1");
  assert.equal(session.claims().email, "ana@example.com");
});

test("a failed adopt leaves the app signed out", async () => {
  const fetch = fakeFetch({ status: 403, body: { error: { code: "bad_origin", message: "no" } } });
  const session = createSession({ fetch });
  await assert.rejects(session.adopt({ accessToken: "a1", idToken: ID, refreshToken: "r1", expiresIn: 900 }),
    (err) => err.code === "bad_origin");
  assert.equal(session.signedIn(), false);
});

test("parallel refreshes share one call, because rotation would burn the second", async () => {
  const fetch = fakeFetch({ status: 200, body: { accessToken: "a2", idToken: ID, expiresIn: 900 } });
  const session = createSession({ fetch });
  await Promise.all([session.refresh(), session.refresh(), session.accessToken()]);
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].path, "/v1/auth/refresh");
  assert.equal(await session.accessToken(), "a2");
});

test("a refresh for a token that's already been replaced does nothing", async () => {
  const fetch = fakeFetch({ status: 200, body: { accessToken: "a2", idToken: ID, expiresIn: 900 } });
  const session = createSession({ fetch });
  await session.refresh();
  await session.refresh({ stale: "a1" });
  assert.equal(fetch.calls.length, 1);
});

test("a 401 from refresh signs out and tells the app", async () => {
  const fetch = fakeFetch({ status: 401, body: { error: { code: "session_expired", message: "Sign in again." } } });
  const ended = [];
  const session = createSession({ fetch, onSignedOut: (err) => ended.push(err.code) });
  await assert.rejects(session.refresh(), (err) => err.status === 401);
  assert.deepEqual(ended, ["session_expired"]);
  assert.equal(session.signedIn(), false);
});

test("no reply from refresh isn't a sign-out", async () => {
  const fetch = fakeFetch(new TypeError("Failed to fetch"));
  const ended = [];
  const session = createSession({ fetch, onSignedOut: () => ended.push(1) });
  await assert.rejects(session.refresh(), (err) => err.status === 0);
  assert.deepEqual(ended, []);
});

test("a token close to expiry is refreshed before use", async () => {
  let clock = 0;
  const fetch = fakeFetch(
    { status: 200, body: { accessToken: "a1", idToken: ID, expiresIn: 900 } },
    { status: 200, body: { accessToken: "a2", idToken: ID, expiresIn: 900 } },
  );
  const session = createSession({ fetch, now: () => clock });
  assert.equal(await session.accessToken(), "a1");
  clock = 800_000; // 100 s left: under the 60 s margin? No, so it's kept.
  assert.equal(await session.accessToken(), "a1");
  clock = 850_000; // 50 s left
  assert.equal(await session.accessToken(), "a2");
});

test("sign-out everywhere sends the access token; a failure keeps the session", async () => {
  const fetch = fakeFetch(
    { status: 200, body: { accessToken: "a1", idToken: ID, expiresIn: 900 } },
    new TypeError("Failed to fetch"),
    { status: 200, body: { ok: true } },
  );
  const session = createSession({ fetch });
  await session.refresh();
  await assert.rejects(session.signOut({ everywhere: true }), (err) => err.status === 0);
  assert.equal(session.signedIn(), true);
  await session.signOut({ everywhere: true });
  assert.equal(fetch.calls[2].headers.Authorization, "Bearer a1");
  assert.deepEqual(fetch.calls[2].body, { everywhere: true });
  assert.equal(session.signedIn(), false);
});

test("plain sign-out sends no token", async () => {
  const fetch = fakeFetch(
    { status: 200, body: { accessToken: "a1", idToken: ID, expiresIn: 900 } },
    { status: 200, body: { ok: true } },
  );
  const session = createSession({ fetch });
  await session.refresh();
  await session.signOut();
  assert.equal(fetch.calls[1].headers.Authorization, undefined);
});

test("decodeJwt reads base64url and UTF-8, and survives junk", () => {
  assert.equal(decodeJwt(ID).name, "Ana Ñ");
  assert.equal(decodeJwt("not a token"), null);
});
