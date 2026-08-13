/**
 * Base URL for server-side fetches (RSC, sitemap, metadata).
 * In K8s the web pod must set *_PROXY_URL to the in-cluster service.
 * Keep NEXT_PUBLIC_* empty until public DNS exists so the browser stays on apex rewrites.
 */
function trimSlash(url: string): string {
  return url.replace(/\/$/, "");
}

function envOr(name: string, fallback: string): string {
  const raw = process.env[name]?.trim();
  if (raw) return trimSlash(raw);
  return fallback;
}

export function serverProxyFor(path: string): string {
  if (path.startsWith("/api/learn") || path.startsWith("/api/newspaper/admin")) {
    return envOr("CONTENT_PROXY_URL", "http://127.0.0.1:8204");
  }
  if (
    path.startsWith("/api/coding") ||
    path.startsWith("/api/system-design") ||
    path.startsWith("/api/practice") ||
    path.startsWith("/api/newspaper")
  ) {
    return envOr("PRACTICE_PROXY_URL", "http://127.0.0.1:8203");
  }
  if (
    path.startsWith("/api/artifacts") ||
    path.startsWith("/api/assertions") ||
    path.startsWith("/api/chat") ||
    path.startsWith("/api/mcq") ||
    path.startsWith("/api/progress") ||
    path.startsWith("/api/guest") ||
    path.startsWith("/api/offline") ||
    path.startsWith("/api/reference")
  ) {
    return envOr("STUDY_PROXY_URL", "http://127.0.0.1:8205");
  }
  if (
    path.startsWith("/api/sources") ||
    path.startsWith("/api/documents") ||
    path.startsWith("/api/activities") ||
    path.startsWith("/api/audiobook")
  ) {
    return envOr("LIBRARY_PROXY_URL", "http://127.0.0.1:8206");
  }
  if (path.startsWith("/api/models") || path.startsWith("/api/debug")) {
    return envOr("ADMIN_PROXY_URL", "http://127.0.0.1:8207");
  }
  if (path.startsWith("/api/auth")) {
    return envOr("AUTH_PROXY_URL", "http://127.0.0.1:8201");
  }
  if (path.startsWith("/api/storage")) {
    return envOr("STORAGE_PROXY_URL", "http://127.0.0.1:8202");
  }
  const proxy = process.env.API_PROXY_URL?.trim();
  if (proxy) return trimSlash(proxy);
  const publicUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (publicUrl) return trimSlash(publicUrl);
  return "http://127.0.0.1:8200";
}

/** @deprecated Prefer serverApiUrl(path) so learn/practice/study hit the right process. */
export function serverApiBase(): string {
  return serverProxyFor("/api/");
}

export function serverApiUrl(path: string): string {
  return `${serverProxyFor(path)}${path}`;
}
