import { test } from "node:test";
import assert from "node:assert/strict";

import { createStore } from "../js/store.js";
import { memoryStorage } from "./helpers.js";

test("a different person signing in wipes the last person's log", () => {
  const storage = memoryStorage();
  const store = createStore(storage);
  store.setUser({ sub: "u1", email: "a@example.com" });
  store.forUser("u1").save({ etag: '"1"', days: [] });
  store.setUser({ sub: "u2", email: "b@example.com" });
  assert.equal(store.forUser("u1").load(), null);
  assert.equal(store.lastUser().sub, "u2");
});

test("the same person signing in again keeps their log", () => {
  const store = createStore(memoryStorage());
  store.setUser({ sub: "u1", email: "a@example.com" });
  store.forUser("u1").save({ etag: '"1"', days: [] });
  store.setUser({ sub: "u1", email: "a@example.com" });
  assert.deepEqual(store.forUser("u1").load(), { etag: '"1"', days: [] });
});

test("clear removes only the app's own keys", () => {
  const storage = memoryStorage();
  storage.setItem("someone-else", "x");
  const store = createStore(storage);
  store.setUser({ sub: "u1", email: "a@example.com" });
  store.clear();
  assert.equal(store.lastUser(), null);
  assert.equal(storage.getItem("someone-else"), "x");
});

test("only sub and email are kept about the user", () => {
  const storage = memoryStorage();
  createStore(storage).setUser({ sub: "u1", email: "a@example.com", groups: ["admins"], token: "nope" });
  assert.deepEqual(JSON.parse(storage.getItem("wl:user")), { sub: "u1", email: "a@example.com" });
});

test("storage that throws doesn't break the app", () => {
  const broken = {
    length: 0,
    key: () => { throw new Error("blocked"); },
    getItem: () => { throw new Error("blocked"); },
    setItem: () => { throw new Error("quota"); },
    removeItem: () => { throw new Error("blocked"); },
  };
  const store = createStore(broken);
  assert.equal(store.lastUser(), null);
  store.setUser({ sub: "u1", email: "a" });
  store.forUser("u1").save({ days: [] });
  store.clear();
});

test("preferences survive a reload and go when the store is cleared", () => {
  const storage = memoryStorage();
  const store = createStore(storage);
  assert.equal(store.pref("trendsRange", "30"), "30", "the fallback until something is set");
  store.setPref("trendsRange", "90");
  store.setPref("progressExercise", "Seated row");
  assert.equal(createStore(storage).pref("trendsRange"), "90", "read back by a fresh store");
  assert.equal(createStore(storage).pref("progressExercise"), "Seated row");
  store.clear();
  assert.equal(createStore(storage).pref("trendsRange", "30"), "30");
});

test("a preference set to a falsy value is still remembered", () => {
  const store = createStore(memoryStorage());
  store.setPref("showAll", false);
  assert.equal(store.pref("showAll", true), false);
});
