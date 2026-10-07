import { test } from "node:test";
import assert from "node:assert/strict";

import { createStore } from "../js/store.js";
import { createSync } from "../js/sync.js";
import { memoryStorage } from "./helpers.js";

// An api stub that answers listDays from a script and records the arguments.
function fakeApi(...replies) {
  const calls = [];
  return {
    calls,
    listDays: async (args) => {
      calls.push(args);
      const next = replies.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  };
}

const day = (date) => ({ date, exercises: {} });
const offline = Object.assign(new Error("offline"), { status: 0 });

test("the first load saves the log and its ETag", async () => {
  const storage = memoryStorage();
  const api = fakeApi({ status: 200, etag: '"3"', data: { days: [day("2026-09-21")], nextCursor: null } });
  const seen = [];
  const statuses = [];
  const sync = createSync({ api, store: createStore(storage).forUser("u1"), onData: (d) => seen.push(d), onStatus: (s) => statuses.push(s) });
  await sync.refresh();
  assert.equal(api.calls[0].etag, null);
  assert.equal(seen[0].length, 1);
  assert.deepEqual(statuses, ["live"]);
  assert.equal(JSON.parse(storage.getItem("wl:days:u1")).etag, '"3"');
});

test("an unchanged log is a 304 and redraws nothing", async () => {
  const storage = memoryStorage();
  const store = createStore(storage).forUser("u1");
  store.save({ etag: '"3"', days: [day("2026-09-21")] });
  const api = fakeApi({ status: 304, etag: '"3"', data: null });
  const seen = [];
  const sync = createSync({ api, store, onData: (d) => seen.push(d) });
  await sync.refresh();
  assert.equal(api.calls[0].etag, '"3"');
  assert.deepEqual(seen, []);
  assert.equal(sync.days().length, 1);
});

test("pages are followed until there's no cursor", async () => {
  const api = fakeApi(
    { status: 200, etag: '"9"', data: { days: [day("2026-09-22")], nextCursor: "c1" } },
    { status: 200, etag: '"9"', data: { days: [day("2026-09-21")], nextCursor: null } },
  );
  const sync = createSync({ api, store: createStore(memoryStorage()).forUser("u1") });
  await sync.refresh();
  assert.equal(api.calls[1].cursor, "c1");
  assert.deepEqual(sync.days().map((d) => d.date), ["2026-09-22", "2026-09-21"]);
});

test("offline keeps the saved copy and says so", async () => {
  const store = createStore(memoryStorage()).forUser("u1");
  store.save({ etag: '"3"', days: [day("2026-09-21")] });
  const statuses = [];
  const sync = createSync({ api: fakeApi(offline), store, onStatus: (s) => statuses.push(s) });
  await sync.refresh();
  assert.deepEqual(statuses, ["offline"]);
  assert.equal(sync.days().length, 1);
});

test("a server error shows as an error, not offline", async () => {
  const statuses = [];
  const err = Object.assign(new Error("boom"), { status: 502 });
  const sync = createSync({ api: fakeApi(err), store: createStore(memoryStorage()).forUser("u1"), onStatus: (s) => statuses.push(s) });
  await sync.refresh();
  assert.deepEqual(statuses, ["error"]);
});

test("refreshes that overlap share one request", async () => {
  const api = fakeApi({ status: 200, etag: '"1"', data: { days: [], nextCursor: null } });
  const sync = createSync({ api, store: createStore(memoryStorage()).forUser("u1") });
  await Promise.all([sync.refresh(), sync.refresh()]);
  assert.equal(api.calls.length, 1);
});
