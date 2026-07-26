/**
 * Base URL for server-side fetches (RSC, sitemap, metadata).
 * In K8s the web pod must set API_PROXY_URL to the internal API service.
 */
export function serverApiBase(): string {
  const proxy = process.env.API_PROXY_URL?.trim();
  if (proxy) return proxy.replace(/\/$/, "");
  const publicUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (publicUrl) return publicUrl.replace(/\/$/, "");
  return "http://127.0.0.1:8200";
}
