/** Same-origin in dev (Next rewrites → API). Set NEXT_PUBLIC_API_URL in production. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const GUEST_HEADER = "X-Zivo-Guest-Id";
const CSRF_HEADER = "X-CSRF-Token";

let csrfToken: string | null = null;
let guestId: string | null = null;

const ARTIFACT_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isArtifactId(value: string | undefined | null): value is string {
  return typeof value === "string" && value !== "undefined" && ARTIFACT_ID_RE.test(value);
}

async function readApiError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => {
          if (typeof item === "object" && item !== null && "msg" in item) {
            return String((item as { msg?: string }).msg ?? "");
          }
          return "";
        })
        .filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
  } catch {
    /* keep raw text */
  }
  return text;
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs = 120_000): Promise<Response> {
  return fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

function captureResponseMeta(res: Response) {
  const headerGuest = res.headers.get(GUEST_HEADER);
  if (headerGuest) guestId = headerGuest;
}

function buildHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (csrfToken) headers.set(CSRF_HEADER, csrfToken);
  if (guestId) headers.set(GUEST_HEADER, guestId);
  return headers;
}

/** Bootstrap anonymous guest session (sets httponly cookie + header). */
export async function ensureGuestSession(): Promise<void> {
  if (guestId) return;
  const res = await fetch(`${API_BASE}/api/sources`, {
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  // 200 = listed docs; either way optional_guest_session ran on the backend.
  if (!res.ok && res.status !== 401) {
    await res.text();
  }
}

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export async function apiFetchBytes(path: string): Promise<ArrayBuffer> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.arrayBuffer();
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: "POST",
      credentials: "include",
      headers: buildHeaders(),
      body: form,
    },
    30 * 60 * 1000,
  );
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
}

export async function apiPostSSE(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: buildHeaders({
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  if (!res.ok || !res.body) throw new Error(await readApiError(res).catch(() => "SSE failed"));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const lines = part.split("\n");
      const event = lines.find((l) => l.startsWith("event:"))?.replace(/^event:\s*/, "");
      const dataLine = lines.find((l) => l.startsWith("data:"))?.replace(/^data:\s*/, "");
      if (!dataLine) continue;
      if (event === "token") {
        try {
          const payload = JSON.parse(dataLine) as { text?: string };
          if (payload.text) onChunk(payload.text);
        } catch {
          onChunk(dataLine);
        }
      } else if (event === "error") {
        try {
          const payload = JSON.parse(dataLine) as { message?: string };
          throw new Error(payload.message ?? "Chat error");
        } catch (e) {
          if (e instanceof Error && e.message !== "Chat error") throw e;
          throw new Error("Chat error");
        }
      }
    }
  }
}
