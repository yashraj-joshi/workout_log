// Every call to our own API goes through here. Three rules:
// - Nothing waits forever: each call has a 10 s deadline.
// - A 401 means the 15-minute access token ran out. Refresh once, retry once.
// - Writes retry at most twice, and only when the request may never have
//   arrived (network error, timeout, 503, 504). POST exercises and move carry
//   an Idempotency-Key, so a retry after a lost reply can't log a set twice.

export const TIMEOUT_MS = 10_000;
// The assistant transcribes and then runs the model, and gives itself 25 s to
// do it. Waiting less here would abandon a turn that is about to succeed.
export const ASSISTANT_TIMEOUT_MS = 30_000;
const RETRY_DELAYS_MS = [600, 1800];
const RETRYABLE = new Set([0, 503, 504]);

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status; // 0 when no reply arrived at all
    this.code = code;
  }
}

const FALLBACK = {
  400: "That request wasn't valid.",
  401: "Sign in to continue.",
  403: "This account can't do that.",
  404: "That wasn't found.",
  409: "Something changed meanwhile. Refresh and try again.",
  413: "That's too large to send.",
  422: "Some of that didn't look right.",
  429: "Too many requests. Wait a moment and try again.",
};

// Our routes answer {"error": {"code", "message"}}. API Gateway's own 401
// answers {"message": "Unauthorized"}, so both shapes end up here.
export function errorFrom(status, data) {
  const error = data && typeof data === "object" ? data.error : null;
  if (error && error.code) return new ApiError(status, error.code, error.message || FALLBACK[status] || "");
  const code = status === 401 ? "unauthorized" : status >= 500 ? "server_error" : "error";
  return new ApiError(status, code, FALLBACK[status] || "The server had a problem. Try again in a moment.");
}

// One HTTP exchange, body included, inside the deadline. Returns
// {status, etag, data}; throws ApiError(0, ...) when nothing came back.
export async function send(fetchImpl, path, { method = "GET", headers = {}, body, timeoutMs = TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    const text = res.status === 204 || res.status === 304 ? "" : await res.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = null; }
    }
    return { status: res.status, etag: res.headers.get("etag"), data };
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (controller.signal.aborted) throw new ApiError(0, "timeout", "The server took too long to answer.");
    throw new ApiError(0, "offline", "You're offline, or the server can't be reached.");
  } finally {
    clearTimeout(timer);
  }
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function createApi({ fetch: fetchImpl, session, sleep = wait, newKey = () => crypto.randomUUID() }) {
  async function once(method, path, { body, etag, key, timeoutMs }) {
    const token = await session.accessToken();
    const headers = { Authorization: `Bearer ${token}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (etag) headers["If-None-Match"] = etag;
    if (key) headers["Idempotency-Key"] = key;
    let res = await send(fetchImpl, path, { method, headers, body, timeoutMs });
    if (res.status === 401) {
      // `stale` lets ten calls that all hit 401 share one refresh.
      await session.refresh({ stale: token });
      headers.Authorization = `Bearer ${await session.accessToken()}`;
      res = await send(fetchImpl, path, { method, headers, body, timeoutMs });
    }
    if (res.status >= 400) throw errorFrom(res.status, res.data);
    return res;
  }

  // idempotent: true makes one key for this action and sends the same key on
  // every retry of it. A new call is a new action and gets a new key.
  async function request(method, path, { body, etag, idempotent = false, timeoutMs } = {}) {
    const key = idempotent ? newKey() : null;
    const safeToRepeat = method === "GET" || method === "PUT" || method === "PATCH" || method === "DELETE";
    const retries = method !== "GET" && (key || safeToRepeat) ? RETRY_DELAYS_MS.length : 0;
    for (let attempt = 0; ; attempt++) {
      try {
        return await once(method, path, { body, etag, key, timeoutMs });
      } catch (err) {
        // The first DELETE may have landed before its reply was lost.
        if (attempt > 0 && method === "DELETE" && err.status === 404) return { status: 204, etag: null, data: null };
        if (attempt >= retries || !RETRYABLE.has(err.status)) throw err;
        await sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
  }

  // The day routes answer {"day": {...}}, or {"day": null} when the write
  // emptied the day and the server deleted it. A write whose reply was lost
  // and then retried can come back with no body at all, so callers refresh
  // after every write rather than trusting what they get here.
  const body = async (promise) => (await promise).data;
  const day = (date) => `/v1/days/${encodeURIComponent(date)}`;
  const exercise = (date, key) => `${day(date)}/exercises/${encodeURIComponent(key)}`;

  return {
    request,
    me: async () => (await request("GET", "/v1/me")).data,
    // 304 when the log hasn't changed since `etag`; then data is null.
    listDays: ({ etag, cursor } = {}) =>
      request("GET", cursor ? `/v1/days?cursor=${encodeURIComponent(cursor)}` : "/v1/days", { etag }),
    readDay: (date) => body(request("GET", day(date))),
    // place, notes, bodyweight and summary. null removes a field.
    patchDay: (date, patch) => body(request("PATCH", day(date), { body: patch })),
    // The server assigns the order and loggedAt, so a repeat of this one would
    // add a second copy: it carries an Idempotency-Key.
    addExercise: (date, item) => body(request("POST", `${day(date)}/exercises`, { body: item, idempotent: true })),
    putExercise: (date, key, item) => body(request("PUT", exercise(date, key), { body: item })),
    removeExercise: (date, key) => body(request("DELETE", exercise(date, key))),
    moveExercise: (date, key, toDate) =>
      body(request("POST", `${exercise(date, key)}/move`, { body: { toDate }, idempotent: true })),

    // The AI routes. None of them is retried: a repeat could log a second copy
    // of the same set, and the assistant is not cheap enough to guess with.
    assistant: (turn) =>
      body(request("POST", "/v1/assistant", { body: turn, timeoutMs: ASSISTANT_TIMEOUT_MS })),
    undoAssistant: (undoToken) =>
      body(request("POST", "/v1/assistant/undo", { body: { undoToken } })),
    // Conditional on the server, so a second press gets 409 rather than a
    // second summary.
    finishDay: (date, notes) =>
      body(request("POST", `${day(date)}/finish`, { body: notes ? { notes } : {},
                                                    timeoutMs: ASSISTANT_TIMEOUT_MS })),
  };
}
