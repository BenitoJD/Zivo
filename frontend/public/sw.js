/**
 * Zivo service worker — app-shell caching for Offline Mode (ADR 0006).
 *
 * Hand-rolled (Turbopack-compatible; serwist/workbox require Webpack which
 * Next.js 16 no longer uses by default). Its ONLY job: cache the app shell so
 * a reload with no signal doesn't white-screen. Offline CONTENT (the pack)
 * lives in IndexedDB and does not depend on this SW.
 *
 * Strategy (rule table, same seam as engine_runtime):
 *  - navigations (HTML): network-first, fall back to the cached shell offline.
 *  - static assets (/_next/static, fonts): stale-while-revalidate.
 *  - API (/api/*): NEVER cached here — the trust model keeps the pack in
 *    IndexedDB, not the HTTP cache; caching API responses would be wrong.
 */

const SHELL_CACHE = "zivo-shell-v1";
const ASSET_CACHE = "zivo-assets-v1";
const SHELL_URLS = ["/", "/offline"];

const OPS = {
  eq: (a, b) => a === b,
  neq: (a, b) => a !== b,
  truthy: (a) => Boolean(a),
  falsey: (a) => !a,
};

function predOk(pred, signals) {
  return OPS[pred.op || "truthy"](signals[pred.key], pred.value);
}

function firstMatch(rules, signals) {
  return (
    rules.find((rule) => rule.when.every((pred) => predOk(pred, signals))) || {
      when: [],
      action: "unmatched",
    }
  );
}

function apply(action, handlers) {
  return handlers[action]();
}

function pick(flag, whenTrue, whenFalse) {
  return { true: whenTrue, false: whenFalse }[String(Boolean(flag))]();
}

const FETCH_RULES = [
  { when: [{ key: "isGet", op: "falsey" }], action: "ignore" },
  { when: [{ key: "isApi", op: "truthy" }], action: "ignore" },
  { when: [{ key: "isAsset", op: "truthy" }], action: "asset" },
  { when: [{ key: "isNav", op: "truthy" }], action: "navigate" },
  { when: [], action: "ignore" },
];

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
  const url = new URL(req.url);
  const hit = firstMatch(FETCH_RULES, {
    isGet: req.method === "GET",
    isApi: url.pathname.startsWith("/api/"),
    isAsset:
      url.pathname.startsWith("/_next/") ||
      url.pathname.includes("/fonts/") ||
      req.destination === "style" ||
      req.destination === "script" ||
      req.destination === "font",
    isNav: req.mode === "navigate",
  });
  apply(hit.action, {
    ignore: () => undefined,
    asset: () => event.respondWith(staleWhileRevalidate(req)),
    navigate: () => event.respondWith(networkFirstNavigation(req)),
    unmatched: () => undefined,
  });
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
    .then((res) =>
      pick(
        Boolean(res && res.status === 200),
        () => {
          cache.put(req, res.clone()).catch(() => undefined);
          return res;
        },
        () => res,
      ),
    )
    .catch(() => cached);
  return cached || network;
}
