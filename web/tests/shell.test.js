// Guards on the static files themselves: the service worker's list, the CSP
// rules the page has to live within, and where data may be stored.

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const read = (path) => readFileSync(join(WEB, path), "utf8");

function files(dir = WEB) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [`/${relative(WEB, path).split("\\").join("/")}`];
  });
}

function shellList() {
  const block = read("sw.js").match(/const SHELL = \[([\s\S]*?)\];/)[1];
  return [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

// Not part of the running app: tests, docs, licenses, and the template for
// the generated config.js.
const NOT_SHIPPED_TO_CACHE = (path) =>
  path.startsWith("/tests/") || path.endsWith(".txt") || path.endsWith(".md") ||
  ["/sw.js", "/config.example.js", "/config.js", "/.DS_Store"].includes(path) || path.endsWith("/.DS_Store");

test("the service worker caches every file the app uses", () => {
  const shell = new Set(shellList());
  const missing = files().filter((p) => !NOT_SHIPPED_TO_CACHE(p) && !shell.has(p));
  assert.deepEqual(missing, []);
});

test("every file the service worker caches exists (config.js is generated)", () => {
  const absent = shellList().filter((p) => p !== "/" && p !== "/config.js" && !existsSync(join(WEB, p)));
  assert.deepEqual(absent, []);
});

test("deploy-web.sh can stamp the version", () => {
  assert.match(read("sw.js"), /^const VERSION = "dev";$/m);
});

test("index.html has no inline script, inline style or inline handler (the CSP blocks them)", () => {
  const html = read("index.html");
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i, "inline <script>");
  assert.doesNotMatch(html, /<style/i, "<style> block");
  assert.doesNotMatch(html, /\sstyle=/i, "style attribute");
  assert.doesNotMatch(html, /\son[a-z]+=/i, "inline event handler");
});

test("the app's code never builds markup from strings or evaluates code", () => {
  for (const path of files().filter((p) => p.startsWith("/js/"))) {
    const code = read(path).replace(/\/\/.*$/gm, "");
    for (const banned of [/\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\beval\(/, /new Function\(/]) {
      assert.ok(!banned.test(code), `${path} uses ${banned}`);
    }
  }
});

test("only store.js touches browser storage, so no token can end up there", () => {
  for (const path of files().filter((p) => p.startsWith("/js/") && p !== "/js/store.js")) {
    const code = read(path).replace(/\/\/.*$/gm, ""); // comments may say why
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(code), path);
  }
});

test("the manifest makes an installable standalone app with real icons", () => {
  const manifest = JSON.parse(read("manifest.webmanifest"));
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.start_url, "/");
  for (const icon of manifest.icons) assert.ok(existsSync(join(WEB, icon.src)), icon.src);
  assert.ok(manifest.icons.some((i) => i.sizes === "512x512" && i.purpose === "maskable"));
});

test("every script and stylesheet index.html loads exists", () => {
  const html = read("index.html");
  const refs = [...html.matchAll(/(?:src|href)="(\/[^"]+)"/g)].map((m) => m[1]);
  const absent = refs.filter((p) => p !== "/config.js" && !existsSync(join(WEB, p)));
  assert.deepEqual(absent, []);
});
