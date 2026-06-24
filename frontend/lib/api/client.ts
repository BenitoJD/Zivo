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
  return humanizeApiFailure(res.status, text);
}

export function humanizeApiFailure(status: number, message: string): string {
  const trimmed = message.trim();
  if (trimmed && !/^internal server error$/i.test(trimmed)) {
    return trimmed;
  }
  if (status === 409) return trimmed || "Not ready yet — try again shortly.";
  if (status === 429) return trimmed || "Message limit reached.";
  if (status >= 500 || status === 502 || status === 503 || status === 504) {
    return "Could not reach the tutor service. Try again in a moment.";
  }
  return trimmed || "Request failed";
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs = 120_000,
  signal?: AbortSignal,
): Promise<Response> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const signalAny = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  return fetch(url, { ...init, signal: signalAny });
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

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

export type UploadProgressHandler = (loaded: number, total: number) => void;

export async function apiPostForm<T>(
  path: string,
  form: FormData,
  options?: { onProgress?: UploadProgressHandler; timeoutMs?: number },
): Promise<T> {
  const timeoutMs = options?.timeoutMs ?? 30 * 60 * 1000;
  const onProgress = options?.onProgress;

  if (onProgress && typeof XMLHttpRequest !== "undefined") {
    return new Promise<T>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}${path}`);
      xhr.withCredentials = true;
      xhr.timeout = timeoutMs;
      const headers = buildHeaders();
      headers.forEach((value, key) => xhr.setRequestHeader(key, value));
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded, event.total);
      };
      xhr.onload = () => {
        const headerGuest = xhr.getResponseHeader(GUEST_HEADER);
        if (headerGuest) guestId = headerGuest;
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as T);
          } catch {
            reject(new Error("Invalid response"));
          }
          return;
        }
        void (async () => {
          try {
            const body = JSON.parse(xhr.responseText) as { detail?: unknown };
            if (typeof body.detail === "string") {
              reject(new Error(body.detail));
              return;
            }
          } catch {
            /* fall through */
          }
          reject(new Error(humanizeApiFailure(xhr.status, xhr.responseText)));
        })();
      };
      xhr.onerror = () => reject(new Error("Upload failed"));
      xhr.ontimeout = () => reject(new Error("Upload timed out"));
      xhr.send(form);
    });
  }

  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: "POST",
      credentials: "include",
      headers: buildHeaders(),
      body: form,
    },
    timeoutMs,
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

function stripCr(value: string): string {
  return value.replace(/\r$/, "");
}

function parseSseEventBlock(part: string): { event: string; data: string } | null {
  const lines = part.split(/\n/).map(stripCr);
  const dataLine = lines.find((l) => l.startsWith("data:"))?.replace(/^data:\s*/, "");
  if (!dataLine) return null;
  const event = stripCr(lines.find((l) => l.startsWith("event:"))?.replace(/^event:\s*/, "") ?? "message");
  return { event, data: dataLine };
}

export type ChatSseHandlers = {
  onChunk: (text: string) => void;
  /** Backend emits `status` before retrieval / first token (e.g. phase: "thinking"). */
  onStatus?: (phase: string) => void;
};

export type ApiPostSSEOptions = {
  signal?: AbortSignal;
};

export async function apiPostSSE(
  path: string,
  body: unknown,
  onChunkOrHandlers: ((text: string) => void) | ChatSseHandlers,
  options?: ApiPostSSEOptions,
): Promise<void> {
  const handlers: ChatSseHandlers =
    typeof onChunkOrHandlers === "function"
      ? { onChunk: onChunkOrHandlers }
      : onChunkOrHandlers;
  const { onChunk, onStatus } = handlers;
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: "POST",
      credentials: "include",
      headers: buildHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify(body),
    },
    120_000,
    options?.signal,
  );
  captureResponseMeta(res);
  if (!res.ok || !res.body) {
    const detail = await readApiError(res).catch(() => humanizeApiFailure(res.status, "SSE failed"));
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    if (options?.signal?.aborted) {
      await reader.cancel();
      throw new DOMException("Aborted", "AbortError");
    }
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const parsed = parseSseEventBlock(part);
      if (!parsed) continue;
      const { event, data } = parsed;
      if (event === "status") {
        try {
          const payload = JSON.parse(data) as { phase?: string };
          if (payload.phase) onStatus?.(payload.phase);
        } catch {
          /* ignore malformed status */
        }
      } else if (event === "token") {
        try {
          const payload = JSON.parse(data) as { text?: string };
          if (payload.text) onChunk(payload.text);
        } catch {
          onChunk(data);
        }
      } else if (event === "error") {
        try {
          const payload = JSON.parse(data) as { message?: string };
          throw new Error(payload.message ?? "Chat error");
        } catch (e) {
          if (e instanceof Error && e.message !== "Chat error") throw e;
          throw new Error("Chat error");
        }
      }
    }
  }
}
