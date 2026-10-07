// A fake fetch for the tests: each call takes the next scripted reply and
// records what was sent.

export function fakeFetch(...replies) {
  const calls = [];
  const fn = async (path, init = {}) => {
    calls.push({ path, method: init.method || "GET", headers: { ...init.headers }, body: init.body ? JSON.parse(init.body) : undefined });
    const next = replies.shift();
    if (!next) throw new Error(`unexpected fetch ${init.method || "GET"} ${path}`);
    if (next instanceof Error) throw next;
    const { status = 200, body, headers = {} } = typeof next === "function" ? next(path, init) : next;
    const text = body === undefined ? null : JSON.stringify(body);
    return new Response(status === 204 || status === 304 ? null : text, { status, headers });
  };
  fn.calls = calls;
  return fn;
}

// A JWT-shaped token. Unsigned: the app only ever reads its claims for display.
export function jwt(claims) {
  const part = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${part({ alg: "none" })}.${part(claims)}.sig`;
}

export function memoryStorage() {
  const items = new Map();
  return {
    get length() { return items.size; },
    key: (i) => [...items.keys()][i] ?? null,
    getItem: (k) => (items.has(k) ? items.get(k) : null),
    setItem: (k, v) => items.set(k, String(v)),
    removeItem: (k) => items.delete(k),
    items,
  };
}
