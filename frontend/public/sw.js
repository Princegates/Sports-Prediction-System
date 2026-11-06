/* Socca Intelligence service worker.
 *
 * Deliberately small. Its jobs are (1) make the app installable, (2) show a
 * friendly page instead of the browser's dinosaur when there is no signal,
 * and (3) make repeat visits faster by keeping the build's static files.
 *
 * What it must NOT do: cache predictions, odds, or anything from the API.
 * That data is time-sensitive and tied to the signed-in account, and a stale
 * "pick" shown as if it were current is worse than an offline notice. API
 * calls (a different origin, or any /api path) are never touched here.
 *
 * Bump VERSION to drop every old cache on the next visit.
 */

const VERSION = "v1";
const SHELL_CACHE = `socca-shell-${VERSION}`;
const ASSET_CACHE = `socca-assets-${VERSION}`;
const OFFLINE_URL = "/offline.html";
const MAX_ASSET_ENTRIES = 120;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll([OFFLINE_URL, "/brand/icon-192.png"]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== ASSET_CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  // Oldest first (insertion order); old builds' hashed files are what we drop.
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}

async function cacheFirst(request) {
  const cache = await caches.open(ASSET_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) {
    cache.put(request, response.clone());
    trim(ASSET_CACHE, MAX_ASSET_ENTRIES);
  }
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(ASSET_CACHE);
  // Any cache, not just this one: the offline page's own icon is precached in the shell cache.
  const hit = (await cache.match(request)) || (await caches.match(request));
  const refresh = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => hit);
  return hit || refresh;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // the API, fonts, crests: leave to the browser
  if (url.pathname.startsWith("/api/")) return;

  // Page loads: always the live site; only a failed network gets the offline page.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => (await caches.match(OFFLINE_URL)) || Response.error()),
    );
    return;
  }

  // Build output is content-hashed, so a cached copy can never be stale.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(request).catch(() => Response.error()));
    return;
  }

  // Brand images and the mascot keep their names between builds.
  if (url.pathname.startsWith("/brand/") || url.pathname.startsWith("/mascot/")) {
    event.respondWith(staleWhileRevalidate(request));
  }
  // Everything else (videos, sitemap, the worker itself): straight to the network.
});
