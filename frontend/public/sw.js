/**
 * Zivo service worker — app-shell caching for Offline Mode (ADR 0006).
 *
 * Hand-rolled (Turbopack-compatible; serwist/workbox require Webpack which
 * Next.js 16 no longer uses by default). Its ONLY job: cache the app shell so
 * a reload with no signal doesn't white-screen. Offline CONTENT (the pack)
 * lives in IndexedDB and does not depend on this SW.
 *
 * Strategy:
 *  - navigations (HTML): network-first, fall back to the cached shell offline.
 *  - static assets (/_next/static, fonts): stale-while-revalidate.
 *  - API (/api/*): NEVER cached here — the trust model keeps the pack in
 *    IndexedDB, not the HTTP cache; caching API responses would be wrong.
 */

const SHELL_CACHE = "zivo-shell-v1";
const ASSET_CACHE = "zivo-assets-v1";
const SHELL_URLS = ["/", "/offline"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS)).catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== ASSET_CACHE)
            .map((k) => caches.delete(k)),
        ),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never intercept API requests — see header comment (trust model).
  if (url.pathname.startsWith("/api/")) return;

  // Static assets: stale-while-revalidate.
  if (
    url.pathname.startsWith("/_next/") ||
    url.pathname.includes("/fonts/") ||
    req.destination === "style" ||
    req.destination === "script" ||
    req.destination === "font"
  ) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  // Navigations: network-first, fall back to cached shell.
  if (req.mode === "navigate") {
    event.respondWith(networkFirstNavigation(req));
  }
});

async function networkFirstNavigation(req) {
  try {
    const fresh = await fetch(req);
    const cache = await caches.open(SHELL_CACHE);
    cache.put(req, fresh.clone()).catch(() => undefined);
    return fresh;
  } catch {
    const cached = await caches.match(req);
    return cached || caches.match("/") || Response.error();
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((res) => {
      if (res && res.status === 200) cache.put(req, res.clone()).catch(() => undefined);
      return res;
    })
    .catch(() => cached);
  return cached || network;
}
