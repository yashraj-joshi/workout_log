// The offline copy of the log, in localStorage. It holds workout data and who
// it belongs to, never a token. Each user's copy is keyed by their Cognito
// sub, and a different person signing in on this device wipes the last one's.

const PREFIX = "wl:";

export function createStore(storage = globalThis.localStorage) {
  // Storage can be missing or throw (private mode, quota, blocked site data).
  // The app still works without it; it just can't open offline.
  function read(key) {
    try {
      const text = storage.getItem(PREFIX + key);
      return text ? JSON.parse(text) : null;
    } catch {
      return null;
    }
  }

  function write(key, value) {
    try {
      storage.setItem(PREFIX + key, JSON.stringify(value));
    } catch {
      // Full or unavailable: the next successful load writes it again.
    }
  }

  function clear() {
    try {
      const ours = [];
      for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i);
        if (key && key.startsWith(PREFIX)) ours.push(key);
      }
      ours.forEach((key) => storage.removeItem(key));
    } catch {
      // Nothing stored, or nothing we can reach.
    }
  }

  return {
    clear,
    // {sub, email} of whoever last signed in here, for opening offline.
    lastUser: () => read("user"),
    setUser(user) {
      const previous = read("user");
      if (previous && previous.sub !== user.sub) clear();
      write("user", { sub: user.sub, email: user.email });
    },
    forUser(sub) {
      return {
        load: () => read(`days:${sub}`),
        save: (state) => write(`days:${sub}`, state),
      };
    },
  };
}
