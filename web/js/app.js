// Starts the app: session first, then the shell, then the log.
//
// Opening the app (docs/00-architecture.md, "Request flows"):
// 1. The service worker paints the shell from cache, online or not.
// 2. POST /v1/auth/refresh turns the cookie into tokens. 401 -> sign-in.
//    No reply -> the saved copy of the log, read-only.
// 3. GET /v1/days with the last ETag, then every 60 s while visible.

import { $, h, replace, toast } from "./dom.js";
import { createApi } from "./api.js";
import { createSession } from "./session.js";
import { createStore } from "./store.js";
import { createSync, POLL_MS } from "./sync.js";
import { renderSignIn } from "./views/signin.js";

const config = globalThis.WORKOUT_LOG_CONFIG;
const store = createStore();
const session = createSession({ fetch: (...args) => fetch(...args), onSignedOut: sessionEnded });
const api = createApi({ fetch: (...args) => fetch(...args), session });
const touch = matchMedia("(pointer: coarse)").matches;

let sync = null;
let user = null; // {sub, email, groups}
let pollTimer = null;
let swRegistration = null;

const STATUS = {
  connecting: "Connecting…",
  live: "Live · updates as you log",
  offline: "Offline · showing saved data",
  error: touch ? "Couldn't load the log · pull to refresh" : "Couldn't load the log · press Refresh",
};

boot();

async function boot() {
  registerServiceWorker();
  wireTabs();
  wireAccountMenu();
  wireRefresh();
  if (!config) {
    showSignedOut();
    replace($("#signin"), h("p", { class: "notice bad", role: "alert" },
      "This copy of the app has no config.js. Run make web-config, then reload (docs/06)."));
    return;
  }
  setStatus("connecting");
  $("#status").hidden = false;
  try {
    await session.refresh();
    await enter();
  } catch (err) {
    if (err.status === 401) return; // sessionEnded() has shown sign-in
    // No reply, or the server is having trouble: open what's saved, if anything.
    const last = store.lastUser();
    if (last && (err.status === 0 || err.status >= 500)) return enterSaved(last, err.status === 0 ? "offline" : "error");
    showSignIn("", err.status === 0 ? "You're offline. Connect to the internet to sign in." : err.message);
  }
}

// ------------------------------------------------------------ signed in

async function enter() {
  const claims = session.claims() || {};
  // Groups decide what the app offers (AI from phase 6). The API enforces
  // them on every call; this copy is only for display.
  const me = await api.me().catch(() => null);
  if (!session.signedIn()) return; // the session ended while we waited
  user = { sub: claims.sub, email: claims.email || "", groups: me ? me.groups : [] };
  store.setUser(user);
  showApp();
  startSync(user.sub);
  await sync.refresh();
}

// Opened with no connection to the server: the saved log, read-only, until
// the session can be restored.
function enterSaved(last, status) {
  user = { sub: last.sub, email: last.email, groups: [] };
  showApp();
  startSync(user.sub);
  setStatus(status);
}

function startSync(sub) {
  sync = createSync({ api, store: store.forUser(sub), onData: renderLog, onStatus: setStatus });
  renderLog(sync.days());
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (document.visibilityState === "visible") tick(); }, POLL_MS);
}

// One check for news: a sync when signed in, a reconnect when opened offline.
async function tick() {
  if (!user) return;
  if (session.signedIn()) return sync.refresh();
  try {
    await session.refresh();
    await enter();
  } catch (err) {
    if (err.status !== 401) setStatus(err.status === 0 ? "offline" : "error");
  }
}

// The server said the session is over (expired, revoked, or signed out from
// another device). Its saved data goes too: a phone signed out remotely
// shouldn't keep showing the log offline.
function sessionEnded(err) {
  const wasIn = user !== null;
  stop();
  store.clear();
  showSignIn(wasIn && err.code === "session_expired" ? "Your session ended. Sign in again." : "");
}

async function signOut(everywhere) {
  closeMenu();
  try {
    await session.signOut({ everywhere });
  } catch (err) {
    toast(err.status === 0 ? "You're offline. Signing out needs a connection." : err.message);
    return;
  }
  stop();
  store.clear();
  showSignIn(everywhere ? "Signed out on every device." : "Signed out.");
}

function stop() {
  clearInterval(pollTimer);
  pollTimer = null;
  sync = null;
  user = null;
  replace($("#log-summary"));
}

// --------------------------------------------------------------- screens

function showSignIn(message, problem = "") {
  showSignedOut();
  renderSignIn({
    root: $("#signin"),
    config,
    message,
    problem,
    adopt: (tokens) => session.adopt(tokens),
    onDone: () => {
      setStatus("connecting");
      enter().catch((err) => setStatus(err.status === 0 ? "offline" : "error"));
    },
  });
}

function showSignedOut() {
  $("#status").hidden = true;
  $("#top-actions").hidden = true;
  $("#tabs").hidden = true;
  $("#app-body").hidden = true;
  $("#signin").hidden = false;
}

function showApp() {
  replace($("#signin"));
  $("#signin").hidden = true;
  $("#status").hidden = false;
  $("#top-actions").hidden = false;
  $("#tabs").hidden = false;
  $("#app-body").hidden = false;
  $("#account-email").textContent = user.email || "Signed in";
  $("#account-ai").textContent = user.groups.some((g) => g === "ai-users" || g === "admins")
    ? "Voice and summaries: on" : "Voice and summaries: off";
}

function setStatus(state) {
  const el = $("#status");
  el.dataset.state = state;
  $("#status-text").textContent = STATUS[state];
}

// Until the Day tab lands (phase 4), the panel proves the sync works.
function renderLog(days) {
  const el = $("#log-summary");
  if (!user) return; // a sync that finished after sign-out
  if (!days) return replace(el, h("p", { class: "meta" }, "Loading your log…"));
  if (days.length === 0) return replace(el, h("p", { class: "meta" }, "No workouts logged yet."));
  const latest = days.reduce((a, b) => (a.date > b.date ? a : b));
  const when = new Date(`${latest.date}T00:00:00`).toLocaleDateString("en-US",
    { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  replace(el,
    h("p", { class: "stat-value" }, days.length.toLocaleString("en-US")),
    h("p", { class: "label" }, days.length === 1 ? "Day logged" : "Days logged"),
    h("p", { class: "meta" }, `Latest: ${when}`));
}

// ------------------------------------------------------------- chrome

function wireTabs() {
  const tabs = [...document.querySelectorAll("#tabs [role=tab]")];
  const select = (tab, focus = false) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(`#${t.getAttribute("aria-controls")}`).hidden = !on;
    }
    if (focus) tab.focus();
    history.replaceState(null, "", `#${tab.dataset.tab}`);
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    // Arrow keys move between tabs, as screen-reader users expect of a tablist.
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) select(tabs[(i + step + tabs.length) % tabs.length], true);
    });
  });
  select(tabs.find((t) => `#${t.dataset.tab}` === location.hash) || tabs[0]);
}

function closeMenu() {
  $("#account-menu").hidden = true;
  $("#account-btn").setAttribute("aria-expanded", "false");
}

function wireAccountMenu() {
  const button = $("#account-btn");
  const menu = $("#account-menu");
  button.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      closeMenu();
      button.focus();
    }
  });
  $("#signout").addEventListener("click", () => signOut(false));
  $("#signout-all").addEventListener("click", () => signOut(true));
}

function wireRefresh() {
  $("#refresh").addEventListener("click", () => tick());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    tick();
    // A home-screen app resumes rather than reloads, so look for updates here.
    swRegistration?.update().catch(() => {});
  });
  addEventListener("online", () => tick());
  addEventListener("offline", () => { if (user) setStatus("offline"); });
  if (touch) wirePullToRefresh();
}

// A home-screen app has no browser pull-to-refresh, so this is a small one:
// pull down from the top of the page past the threshold, then let go.
function wirePullToRefresh() {
  const THRESHOLD = 70;
  const hint = $("#ptr");
  let startY = null;
  let pulled = 0;
  addEventListener("touchstart", (event) => {
    startY = user && scrollY <= 0 && event.touches.length === 1 ? event.touches[0].clientY : null;
    pulled = 0;
  }, { passive: true });
  addEventListener("touchmove", (event) => {
    if (startY === null) return;
    pulled = Math.max(0, event.touches[0].clientY - startY);
    hint.hidden = pulled < 20;
    hint.textContent = pulled >= THRESHOLD ? "Release to refresh" : "Pull to refresh";
  }, { passive: true });
  addEventListener("touchend", () => {
    hint.hidden = true;
    if (startY !== null && pulled >= THRESHOLD) tick();
    startY = null;
  });
}

// ------------------------------------------------------- service worker

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  // Locally (make serve), a cached shell would hide every edit you make.
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") return;
  // A new version takes over as soon as it's installed. Updates are found at
  // launch and on resume, so the reload lands when the app has just opened.
  const hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController) location.reload(); });
  navigator.serviceWorker.register("/sw.js").then((reg) => { swRegistration = reg; }).catch(() => {});
}
