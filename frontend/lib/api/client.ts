/** Same-origin in dev (Next rewrites → API). Set NEXT_PUBLIC_API_URL in production. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

const CHUNKED_UPLOAD_THRESHOLD = 8 * 1024 * 1024;
const CHUNKED_RESUME_PREFIX = "zivo-chunked-upload:";

const GUEST_HEADER = "X-Zivo-Guest-Id";
const CSRF_HEADER = "X-CSRF-Token";

let csrfToken: string | null = null;
let guestId: string | null = null;
let guestSessionPromise: Promise<void> | null = null;

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

/** Admin-only route failed: guest 401 or non-admin 403. */
export function isApiAccessDenied(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const m = error.message.toLowerCase();
  return (
    m.includes("admin") ||
    m.includes("not authenticated") ||
    m.includes("forbidden") ||
    m.includes("unauthorized")
  );
}

export function humanizeApiFailure(status: number, message: string): string {
  const trimmed = message.trim();
  if (trimmed && !/^internal server error$/i.test(trimmed)) {
    return trimmed;
  }
  if (status === 409) return trimmed || "Not ready yet - try again shortly.";
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

/** Restore signed-in CSRF or bootstrap anonymous guest session. */
export async function ensureGuestSession(): Promise<void> {
  if (csrfToken) return;
  if (guestSessionPromise) return guestSessionPromise;
  guestSessionPromise = (async () => {
    const sessionRes = await fetch(`${API_BASE}/api/auth/session`, {
      credentials: "include",
      headers: buildHeaders(),
    });
    captureResponseMeta(sessionRes);
    if (sessionRes.ok) {
      const session = (await sessionRes.json()) as { csrf_token?: string };
      if (session.csrf_token) {
        setCsrfToken(session.csrf_token);
        return;
      }
    }
    await maybeClearSessionOnAuthFailure(sessionRes);

    if (guestId) return;

    const res = await fetch(`${API_BASE}/api/auth/guest`, {
      method: "POST",
      credentials: "include",
      headers: buildHeaders({ "Content-Type": "application/json" }),
      body: "{}",
    });
    captureResponseMeta(res);
    if (res.ok) {
      // The guest endpoint returns the CSRF token for the new anonymous session.
      // Without capturing it here, the very first write (e.g. an upload) goes out
      // with no token and the server rejects it with "CSRF token required".
      try {
        const guest = (await res.json()) as { csrf_token?: string };
        if (guest.csrf_token) setCsrfToken(guest.csrf_token);
      } catch {
        /* non-JSON body - nothing to capture */
      }
    } else if (res.status !== 401) {
      await res.text();
    }
  })();
  try {
    await guestSessionPromise;
  } finally {
    guestSessionPromise = null;
  }
}

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function clearClientSessionState() {
  csrfToken = null;
  guestId = null;
  guestSessionPromise = null;
}

/**
 * Only treat "session cookie rejected" as logout-worthy. Endpoint 401s for
 * missing guest, admin gates, bad credentials, etc. must NOT wipe CSRF: that
 * made signed-in users look logged out after an unrelated 401.
 */
export function isSessionAuthFailure(status: number, detail: string): boolean {
  if (status !== 401) return false;
  const d = detail.toLowerCase();
  return (
    d.includes("invalid session") ||
    d.includes("session revoked") ||
    d.includes("user not found") ||
    d === "not authenticated"
  );
}

async function maybeClearSessionOnAuthFailure(res: Response): Promise<void> {
  if (res.status !== 401) return;
  // Clone so callers can still read the body.
  const detail = await res.clone().text().catch(() => "");
  let message = detail;
  try {
    const body = JSON.parse(detail) as { detail?: unknown };
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    /* keep raw */
  }
  if (isSessionAuthFailure(res.status, message)) {
    clearClientSessionState();
  }
}

export async function apiFetchBytes(path: string): Promise<ArrayBuffer> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.arrayBuffer();
}

export async function apiPostBytes(path: string, body: unknown): Promise<ArrayBuffer> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.arrayBuffer();
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
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
  await maybeClearSessionOnAuthFailure(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

/** POST that succeeds with 204 No Content (e.g. logout). */
export async function apiPostNoContent(path: string, body: unknown): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
  if (!res.ok) throw new Error(await readApiError(res));
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

export type UploadProgressHandler = (loaded: number, total: number) => void;

export type ApiPostFormOptions = {
  onProgress?: UploadProgressHandler;
  timeoutMs?: number;
  signal?: AbortSignal;
};

export async function apiPostForm<T>(
  path: string,
  form: FormData,
  options?: ApiPostFormOptions,
): Promise<T> {
  const timeoutMs = options?.timeoutMs ?? 30 * 60 * 1000;
  const onProgress = options?.onProgress;
  const signal = options?.signal;

  if (onProgress && typeof XMLHttpRequest !== "undefined") {
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }

    return new Promise<T>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}${path}`);
      xhr.withCredentials = true;
      xhr.timeout = timeoutMs;
      const headers = buildHeaders();
      headers.forEach((value, key) => xhr.setRequestHeader(key, value));

      const onAbort = () => {
        xhr.abort();
      };
      signal?.addEventListener("abort", onAbort);

      const cleanup = () => {
        signal?.removeEventListener("abort", onAbort);
      };

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded, event.total);
      };
      xhr.onload = () => {
        cleanup();
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
      xhr.onerror = () => {
        cleanup();
        reject(new Error("Upload failed"));
      };
      xhr.ontimeout = () => {
        cleanup();
        reject(new Error("Upload timed out"));
      };
      xhr.onabort = () => {
        cleanup();
        reject(new DOMException("Aborted", "AbortError"));
      };
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
    signal,
  );
  captureResponseMeta(res);
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json() as Promise<T>;
}

type ChunkedResume = {
  sessionId: string;
  filename: string;
  contentType: string;
  totalSize: number;
  chunkSize: number;
  expectedParts: number;
  uploadedParts: number[];
};

function chunkedResumeKey(file: File): string {
  return `${CHUNKED_RESUME_PREFIX}${file.name}:${file.size}:${file.lastModified}`;
}

function readChunkedResume(file: File): ChunkedResume | null {
  if (typeof localStorage === "undefined") return null;
  const raw = localStorage.getItem(chunkedResumeKey(file));
  if (!raw) return null;
  try {
    const resume = JSON.parse(raw) as ChunkedResume & { savedAt?: number };
    if (resume.savedAt && Date.now() - resume.savedAt > 7 * 24 * 60 * 60 * 1000) {
      localStorage.removeItem(chunkedResumeKey(file));
      return null;
    }
    return resume;
  } catch {
    return null;
  }
}

function writeChunkedResume(file: File, resume: ChunkedResume) {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(
    chunkedResumeKey(file),
    JSON.stringify({ ...resume, savedAt: Date.now() }),
  );
}

function clearChunkedResume(file: File) {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(chunkedResumeKey(file));
}

export async function apiUploadChunked<T extends { id: string }>(
  file: File,
  options?: ApiPostFormOptions,
): Promise<T> {
  const signal = options?.signal;
  const onProgress = options?.onProgress;
  const contentType = file.type || "application/octet-stream";

  let resume = readChunkedResume(file);
  let sessionId = resume?.sessionId;
  let chunkSize = resume?.chunkSize ?? 0;
  let expectedParts = resume?.expectedParts ?? 0;
  const uploaded = new Set(resume?.uploadedParts ?? []);

  if (sessionId) {
    try {
      const status = await apiGet<{
        received_parts: number[];
        chunk_size: number;
        expected_parts: number;
      }>(`/api/sources/chunked/${sessionId}`);
      chunkSize = status.chunk_size;
      expectedParts = status.expected_parts;
      status.received_parts.forEach((p) => uploaded.add(p));
    } catch {
      sessionId = undefined;
      uploaded.clear();
    }
  }

  if (!sessionId) {
    const init = await apiPost<{
      session_id: string;
      chunk_size: number;
      expected_parts: number;
    }>("/api/sources/chunked/init", {
      filename: file.name,
      content_type: contentType,
      total_size: file.size,
    });
    sessionId = init.session_id;
    chunkSize = init.chunk_size;
    expectedParts = init.expected_parts;
    writeChunkedResume(file, {
      sessionId,
      filename: file.name,
      contentType,
      totalSize: file.size,
      chunkSize,
      expectedParts,
      uploadedParts: [],
    });
  }

  for (let part = 1; part <= expectedParts; part += 1) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (uploaded.has(part)) {
      if (onProgress) onProgress(Math.min(file.size, part * chunkSize), file.size);
      continue;
    }
    const start = (part - 1) * chunkSize;
    const end = Math.min(start + chunkSize, file.size);
    const blob = file.slice(start, end);
    const res = await fetch(`${API_BASE}/api/sources/chunked/${sessionId}/parts/${part}`, {
      method: "PUT",
      credentials: "include",
      headers: buildHeaders({ "Content-Type": "application/octet-stream" }),
      body: blob,
      signal,
    });
    captureResponseMeta(res);
    if (!res.ok) throw new Error(await readApiError(res));
    uploaded.add(part);
    writeChunkedResume(file, {
      sessionId,
      filename: file.name,
      contentType,
      totalSize: file.size,
      chunkSize,
      expectedParts,
      uploadedParts: [...uploaded].sort((a, b) => a - b),
    });
    if (onProgress) onProgress(end, file.size);
  }

  const done = await apiPost<T>(`/api/sources/chunked/${sessionId}/complete`, {});
  clearChunkedResume(file);
  return done;
}

export async function apiUploadFile<T extends { id: string }>(
  file: File,
  options?: ApiPostFormOptions,
): Promise<T> {
  if (file.size >= CHUNKED_UPLOAD_THRESHOLD) {
    return apiUploadChunked<T>(file, options);
  }
  const form = new FormData();
  form.append("file", file);
  return apiPostForm<T>("/api/sources", form, options);
}

export async function apiUploadFiles<T extends { id: string }>(
  files: File[],
  options?: ApiPostFormOptions,
): Promise<T> {
  if (files.length === 0) {
    throw new Error("No files selected");
  }
  if (files.length === 1) {
    return apiUploadFile<T>(files[0], options);
  }
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  if (totalSize >= CHUNKED_UPLOAD_THRESHOLD) {
    throw new Error(
      "Combined upload is too large for multi-file combine. Upload files one at a time or use smaller files.",
    );
  }
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiPostForm<T>("/api/sources/upload-bundle", form, options);
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    credentials: "include",
    headers: buildHeaders(),
  });
  captureResponseMeta(res);
  await maybeClearSessionOnAuthFailure(res);
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
  onChunk?: (text: string) => void;
  /** Backend emits `status` before retrieval / first token (e.g. phase: "thinking"). */
  onStatus?: (phase: string) => void;
  /** Raw per-event hook - fires for EVERY SSE event (used for non-chat streams
   *  like verdict-first grading: "verdict" / "feedback" / "done"). */
  onEvent?: (event: string, data: string) => void;
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
  const { onChunk, onStatus, onEvent } = handlers;
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
  await maybeClearSessionOnAuthFailure(res);
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
      onEvent?.(event, data);
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
          if (payload.text) onChunk?.(payload.text);
        } catch {
          onChunk?.(data);
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
