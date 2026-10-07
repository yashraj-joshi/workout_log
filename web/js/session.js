// The signed-in session, as the app sees it.
//
// Access and ID tokens (15 minutes) live in this closure and nowhere else: not
// localStorage, not sessionStorage, not IndexedDB. The 90-day refresh token
// lives in an HttpOnly cookie on /v1/auth that no script can read, including
// this one. docs/00-architecture.md, "Tokens: where each one lives".

import { ApiError, errorFrom, send } from "./api.js";

// Refresh this long before expiry, so a call never leaves with a token that
// dies on the way.
const EARLY_MS = 60_000;

export function decodeJwt(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(part + "=".repeat(-part.length & 3)), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

export function createSession({ fetch: fetchImpl, now = Date.now, onSignedOut = () => {} }) {
  let tokens = null; // {accessToken, idToken, expiresAt}
  let inflight = null;

  function keep(accessToken, idToken, expiresIn) {
    tokens = { accessToken, idToken, expiresAt: now() + (Number(expiresIn) || 900) * 1000 };
  }

  async function post(path, { body, bearer } = {}) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    const res = await send(fetchImpl, path, { method: "POST", headers, body });
    if (res.status >= 400) throw errorFrom(res.status, res.data);
    return res.data;
  }

  // Right after SRP sign-in. The server swaps the refresh token for a new one
  // that only the cookie ever sees; this side forgets the old one by returning.
  async function adopt({ accessToken, idToken, refreshToken, expiresIn }) {
    await post("/v1/auth/session", { body: { refreshToken }, bearer: accessToken });
    keep(accessToken, idToken, expiresIn);
  }

  // Cookie in, fresh tokens out. One refresh at a time: every caller that
  // arrives meanwhile waits for the same answer. Rotation means two parallel
  // refreshes would each burn the other's token.
  function refresh({ stale } = {}) {
    if (stale && tokens && tokens.accessToken !== stale) return Promise.resolve();
    if (!inflight) {
      inflight = post("/v1/auth/refresh")
        .then((data) => keep(data.accessToken, data.idToken, data.expiresIn))
        .catch((err) => {
          if (err.status === 401) {
            tokens = null;
            onSignedOut(err);
          }
          throw err;
        })
        .finally(() => { inflight = null; });
    }
    return inflight;
  }

  async function accessToken() {
    if (!tokens || tokens.expiresAt - now() < EARLY_MS) await refresh();
    if (!tokens) throw new ApiError(401, "signed_out", "Sign in to continue.");
    return tokens.accessToken;
  }

  // Only a server that answered can end the session: the cookie is HttpOnly,
  // so this side can't delete it. If the call fails, the session is kept and
  // the caller says so, rather than claiming a sign-out that didn't happen.
  async function signOut({ everywhere = false } = {}) {
    const bearer = everywhere ? await accessToken() : undefined;
    await post("/v1/auth/signout", { body: { everywhere }, bearer });
    tokens = null;
  }

  return {
    adopt,
    refresh,
    accessToken,
    signOut,
    signedIn: () => tokens !== null,
    claims: () => (tokens ? decodeJwt(tokens.idToken) : null),
  };
}
