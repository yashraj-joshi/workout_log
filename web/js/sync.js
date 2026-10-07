// Keeps the whole log loaded, with a saved copy for opening offline.
//
// GET /v1/days carries the last ETag (the server's dataVersion). An unchanged
// log costs the server one GetItem and comes back 304, which is why polling
// every 60 seconds is cheap.

export const POLL_MS = 60_000;

export function createSync({ api, store, onData = () => {}, onStatus = () => {} }) {
  let state = store.load(); // {etag, days, savedAt}
  let running = null;

  // Returns null when nothing changed.
  async function fetchLog() {
    const first = await api.listDays({ etag: state && state.etag });
    if (first.status === 304) return null;
    let days = first.data.days || [];
    let cursor = first.data.nextCursor;
    // Past 500 days the log comes in pages. If it changes between pages, the
    // next poll's ETag won't match and loads it again.
    while (cursor) {
      const page = await api.listDays({ cursor });
      days = days.concat(page.data.days || []);
      cursor = page.data.nextCursor;
    }
    return { etag: first.etag, days };
  }

  // Calls that arrive while one is running share it.
  function refresh() {
    if (!running) {
      running = (async () => {
        try {
          const fresh = await fetchLog();
          if (fresh) {
            state = { ...fresh, savedAt: new Date().toISOString() };
            store.save(state);
            onData(state.days);
          }
          onStatus("live");
        } catch (err) {
          // A 401 here means the session ended; the session's own handler
          // has already sent the app back to sign-in.
          if (err.status !== 401) onStatus(err.status === 0 ? "offline" : "error");
        } finally {
          running = null;
        }
      })();
    }
    return running;
  }

  // A write already told us what that day now looks like, so the screen can
  // update before the refresh lands. `day` of null means the write emptied the
  // day and the server deleted it. The stored ETag is deliberately left alone:
  // the server's dataVersion has moved on, so the next refresh fetches and has
  // the last word.
  function applyDay(date, day) {
    if (!state) state = { etag: null, days: [], savedAt: null };
    const days = state.days.filter((d) => d.date !== date);
    if (day) days.push(day);
    days.sort((a, b) => (a.date > b.date ? -1 : a.date < b.date ? 1 : 0));
    state = { ...state, days, savedAt: new Date().toISOString() };
    store.save(state);
    onData(state.days);
  }

  return {
    refresh,
    applyDay,
    days: () => (state ? state.days : null),
    savedAt: () => (state ? state.savedAt : null),
  };
}
