// Caches the app shell so the app opens instantly, with or without a network.
//
// It never touches /v1/* or /health: API replies carry one person's data and
// must always come from the server. The offline copy of the log lives in
// localStorage (js/store.js), not here.
//
// deploy-web.sh replaces VERSION with the commit, so every deploy is a new
// cache and the old one is deleted when the new worker takes over.

const VERSION = "dev";
const CACHE = `wl-shell-${VERSION}`;

// Everything the app needs to start. web/tests/shell.test.js fails if a file
// in web/ is missing from this list, or if the list names a file that isn't
// there.
const SHELL = [
  "/",
  "/index.html",
  "/config.js",
  "/manifest.webmanifest",
  "/exercise_catalog.json",
  "/css/app.css",
  "/js/app.js",
  "/js/api.js",
  "/js/catalog.js",
  "/js/cognito.js",
  "/js/dom.js",
  "/js/logic.js",
  "/js/parse.js",
  "/js/session.js",
  "/js/store.js",
  "/js/sync.js",
  "/js/views/day.js",
  "/js/views/editor.js",
  "/js/views/signin.js",
  "/vendor/amazon-cognito-identity-6.3.20.min.js",
  "/fonts/barlow-latin-400-normal.woff2",
  "/fonts/barlow-latin-500-normal.woff2",
  "/fonts/barlow-latin-600-normal.woff2",
  "/fonts/barlow-condensed-latin-500-normal.woff2",
  "/fonts/barlow-condensed-latin-600-normal.woff2",
  "/fonts/barlow-condensed-latin-700-normal.woff2",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  // cache: "reload" skips the browser's HTTP cache, so a new version never
  // gets stored with an old file in it.
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL.map((path) => new Request(path, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("wl-shell-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // Other origins (Cognito), the API, and anything but GET go to the network
  // untouched.
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/v1/") || url.pathname === "/health") return;

  if (request.mode === "navigate") {
    event.respondWith(caches.match("/index.html").then((hit) => hit || fetch(request)));
    return;
  }
  event.respondWith(caches.match(request, { ignoreSearch: true }).then((hit) => hit || fetch(request)));
});
