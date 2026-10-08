// Starts the app: session first, then the shell, then the log.
//
// Opening the app (docs/00-architecture.md, "Request flows"):
// 1. The service worker paints the shell from cache, online or not.
// 2. POST /v1/auth/refresh turns the cookie into tokens. 401 -> sign-in.
//    No reply -> the saved copy of the log, read-only.
// 3. GET /v1/days with the last ETag, then every 60 s while visible.

import { $, h, replace, toast } from "./dom.js";
import { createApi } from "./api.js";
import { loadCatalog } from "./catalog.js";
import { createSession } from "./session.js";
import { createStore } from "./store.js";
import { createSync, POLL_MS } from "./sync.js";
import { createVoice } from "./voice.js";
import { exportCsv } from "./csv.js";
import { createDayTab } from "./views/day.js";
import { openEditor } from "./views/editor.js";
import { createProgressTab } from "./views/progress.js";
import { renderSignIn } from "./views/signin.js";
import { createTrendsTab } from "./views/trends.js";

const config = globalThis.WORKOUT_LOG_CONFIG;
const store = createStore();
const session = createSession({ fetch: (...args) => fetch(...args), onSignedOut: sessionEnded });
const api = createApi({ fetch: (...args) => fetch(...args), session });
const touch = matchMedia("(pointer: coarse)").matches;

let sync = null;
let user = null; // {sub, email, groups}
let dayTab = null;
let trendsTab = null;
let progressTab = null;
let voice = null;
let pollTimer = null;
let swRegistration = null;
let selectTab = () => {};

const canUseAI = () => Boolean(user && user.groups.some((g) => g === "ai-users" || g === "admins"));

// The device's own calendar date. A day is local: logging at 11pm belongs to
// that evening, not to tomorrow in UTC.
function todayISO() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

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
  wireExport();
  wireRefresh();
  if (!config) {
    showSignedOut();
    replace($("#signin"), h("p", { class: "notice bad", role: "alert" },
      "This copy of the app has no config.js. Run make web-config, then reload (docs/06)."));
    return;
  }
  try {
    await loadCatalog();
  } catch {
    showSignedOut();
    replace($("#signin"), h("p", { class: "notice bad", role: "alert" },
      "This copy of the app is missing exercise_catalog.json. Run make sync-shared and deploy again (docs/06)."));
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
  const today = todayISO();
  dayTab = createDayTab({ root: $("#day-tab"), today, actions: dayActions(), canUseAI: canUseAI() });
  trendsTab = createTrendsTab({ root: $("#trends-tab"), store, today, actions: { openDay: openDayOn } });
  progressTab = createProgressTab({ root: $("#progress-tab"), store, today, actions: { openDay: openDayOn } });
  startVoice();
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
  dayTab = null;
  trendsTab = null;
  progressTab = null;
  if (voice) voice.destroy();
  voice = null;
  replace($("#day-tab"));
  replace($("#trends-tab"));
  replace($("#progress-tab"));
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

function renderLog(days) {
  if (!user || !dayTab) return; // a sync that finished after sign-out
  // null means nothing has loaded and nothing was saved: not an empty log.
  if (!days) return replace($("#day-tab"), h("p", { class: "meta" }, "Loading your log…"));
  const today = todayISO();
  dayTab.update(days, today);
  trendsTab.update(days, today);
  progressTab.update(days, today);
}

// Voice is for accounts in ai-users or admins. The API enforces that on every
// call; this only decides whether to offer the button.
function startVoice() {
  if (voice) voice.destroy();
  voice = null;
  if (!canUseAI()) return replace($("#voice"));
  voice = createVoice({
    host: $("#voice"),
    api,
    // The day on screen only if I chose it. On load the Day tab opens on the
    // last workout when today is empty, and "bench, 3 sets of 8" then is
    // about today, not that day.
    getDate: () => (dayTab ? dayTab.chosen() : null),
    getToday: todayISO,
    actions: {
      // A turn can touch more than one day, and can delete one.
      applied: (result) => {
        if (!sync) return;
        for (const day of result.days || []) {
          sync.applyDay(day.date, day.deleted ? null : day);
        }
        const [first] = result.changedDates || [];
        if (first && dayTab) {
          dayTab.select(first);
          selectTab("day");
        }
        sync.refresh();
      },
    },
  });
}

// The calendar heatmap and the Progress history both open a day this way.
function openDayOn(date) {
  if (!dayTab) return;
  dayTab.select(date);
  selectTab("day");
}

// ------------------------------------------------------------- the day tab

function dayActions() {
  return {
    api,
    refresh: () => sync && sync.refresh(),
    applyDay: (date, day) => sync && sync.applyDay(date, day),
    add: (date) => openDialog(date, null),
    edit: (date, key, exercise) => openDialog(date, { date, key, exercise }),
    openProgress: (name) => {
      if (!progressTab) return;
      progressTab.select(name);
      selectTab("progress");
    },
  };
}

function openDialog(date, existing) {
  // navigator.onLine is only trustworthy when it says no, which is the case
  // worth catching: filling the dialog in and losing it at Save.
  if (navigator.onLine === false) return toast("You're offline. Logging needs a connection.");
  openEditor({
    host: $("#dialogs"),
    days: (sync && sync.days()) || [],
    today: todayISO(),
    date: date || todayISO(),
    existing,
    api,
    actions: {
      applied: (on, body) => { if (sync && body && "day" in body) sync.applyDay(on, body.day); },
      // A move changes two days, so every path ends with a refresh.
      done: (on) => {
        if (dayTab) dayTab.select(on);
        selectTab("day");
        if (sync) sync.refresh();
      },
    },
  });
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
  selectTab = (name) => {
    const tab = tabs.find((t) => t.dataset.tab === name);
    if (tab) select(tab);
  };
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

function wireExport() {
  $("#export").addEventListener("click", async () => {
    const days = (sync && sync.days()) || [];
    try {
      const result = await exportCsv(days, todayISO());
      if (result.empty) toast("Nothing logged yet, so there is nothing to export.");
      else if (result.downloaded) toast("Exported your log as CSV.");
    } catch {
      toast("Couldn't build the export. Try again.");
    }
  });
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
