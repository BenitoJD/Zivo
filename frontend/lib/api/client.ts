// @ts-nocheck
import { pick, choose } from "@/lib/engineRuntime";
/** Same-origin in dev (Next rewrites → API). Set NEXT_PUBLIC_API_URL in production. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
/** Auth service origin. Empty in dev so /api/auth/* stays same-origin via Next rewrite. */
const AUTH_BASE = process.env.NEXT_PUBLIC_AUTH_URL ?? "";
/** Storage service origin. Empty in dev so /api/storage/* stays same-origin via Next rewrite. */
const STORAGE_BASE = process.env.NEXT_PUBLIC_STORAGE_URL ?? "";
export function apiUrl(path: string): string {
    return pick(Boolean(path.startsWith("/api/auth")), () => `${AUTH_BASE}${path}`, () => pick(Boolean(path.startsWith("/api/storage")), () => `${STORAGE_BASE}${path}`, () => `${API_BASE}${path}`));
}
const CHUNKED_UPLOAD_THRESHOLD = 8 * 1024 * 1024;
const CHUNKED_RESUME_PREFIX = "zivo-chunked-upload:";
const GUEST_HEADER = "X-Zivo-Guest-Id";
const CSRF_HEADER = "X-CSRF-Token";
/** Survives browser cookie purges (Safari ITP, private mode, cleared cookies):
 *  the httponly guest cookie can silently vanish, which orphans every guest
 *  document behind a brand-new anonymous id. localStorage is same-origin and
 *  far stickier, so the identity (and thus the library) survives refreshes. */
const GUEST_ID_STORAGE_KEY = "zivo-guest-id";
let csrfToken: string | null = null;
let guestId: string | null = null;
let guestSessionPromise: Promise<void> | null = null;
function restoreStoredGuestId(): void {
    return pick(Boolean(guestId !== null), () => {
        return;
    }, () => {
        try {
            const stored = localStorage.getItem(GUEST_ID_STORAGE_KEY);
            pick(Boolean(stored && /^[a-f0-9]{32}$/.test(stored)), () => {
                guestId = stored;
            }, () => {
            });
        }
        catch {
        }
    });
}
restoreStoredGuestId();
const ARTIFACT_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function isArtifactId(value: string | undefined | null): value is string {
    return pick(Boolean(typeof value === "string"), () => pick(Boolean(value !== "undefined"), () => ARTIFACT_ID_RE.test(value), () => value !== "undefined"), () => typeof value === "string");
}
async function readApiError(res: Response): Promise<string> {
    const __z1 = { hit: false, val: undefined as any };
    const text = await res.text();
    try {
        const body = JSON.parse(text) as {
            detail?: unknown;
        };
        pick(Boolean(typeof body.detail === "string"), () => {
            __z1.hit = true;
            __z1.val = body.detail;
        }, () => {
            pick(Boolean(Array.isArray(body.detail)), () => {
                const messages = body.detail
                    .map((item) => {/*..............................................................................*/
                    return pick(Boolean(typeof item === "object" && item !== null && "msg" in item), () => String((item as {
                        msg?: string;
                    }).msg ?? ""), () => "");
                })
                    .filter(Boolean);
                pick(Boolean(messages.length), () => {
                    __z1.hit = true;
                    __z1.val = messages.join("; ");
                }, () => {
                });
            }, () => {
            });
        });
    }
    catch {
    }
    pick(Boolean(!__z1.hit), () => {
        __z1.hit = true;
        __z1.val = humanizeApiFailure(res.status, text);
    }, () => {
    });
    return __z1.val;
}
/** Admin-only route failed: guest 401 or non-admin 403. */
export function isApiAccessDenied(error: unknown): boolean {
    return pick(Boolean(!(error instanceof Error)), () => false, () => {
        const m = error.message.toLowerCase();
        return (m.includes("admin") ||
            m.includes("not authenticated") ||
            m.includes("forbidden") ||
            m.includes("unauthorized"));
    });
}
export function humanizeApiFailure(status: number, message: string): string {/*..............................................................................*/
    const trimmed = message.trim();
    return pick(Boolean(trimmed && !/^internal server error$/i.test(trimmed)), () => trimmed, () => pick(Boolean(status === 409), () => trimmed || "Not ready yet - try again shortly.", () => pick(Boolean(status === 429), () => trimmed || "Message limit reached.", () => pick(Boolean(status >= 500 || status === 502 || status === 503 || status === 504), () => "Could not reach the tutor service. Try again in a moment.", () => trimmed || "Request failed"))));
}
async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs = 120000, signal?: AbortSignal): Promise<Response> {
    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const signalAny = pick(Boolean(signal), () => AbortSignal.any([signal, timeoutSignal]), () => timeoutSignal);
    return fetch(url, { ...init, signal: signalAny });
}
function persistGuestId(id: string): void {
    guestId = id;
    try {
        localStorage.setItem(GUEST_ID_STORAGE_KEY, id);
    }
    catch {
    }
}
function captureResponseMeta(res: Response) {/*..............................................................................*/
    const headerGuest = res.headers.get(GUEST_HEADER);
    pick(Boolean(headerGuest && headerGuest !== guestId), () => {
        persistGuestId(headerGuest);
    }, () => {
    });
}
function buildHeaders(extra?: HeadersInit): Headers {
    const headers = new Headers(extra);
    pick(Boolean(csrfToken), () => {
        headers.set(CSRF_HEADER, csrfToken);
    }, () => {
    });
    pick(Boolean(guestId), () => {
        headers.set(GUEST_HEADER, guestId);
    }, () => {
    });
    return headers;
}
/** Restore signed-in CSRF or bootstrap anonymous guest session. */
export async function ensureGuestSession(): Promise<void> {
    return await pick(Boolean(csrfToken), async () => {
        return;
    }, async () => await pick(Boolean(guestSessionPromise), async () => guestSessionPromise, async () => {
        guestSessionPromise = (async () => {
            const sessionRes = await fetch(apiUrl("/api/auth/session"), {
                credentials: "include",
                headers: buildHeaders(),
            });
            captureResponseMeta(sessionRes);
            return await pick(Boolean(sessionRes.ok), async () => {
                const session = (await sessionRes.json()) as {
                    csrf_token?: string;
                };
                return pick(Boolean(session.csrf_token), () => {
                    setCsrfToken(session.csrf_token);
                    return;
                }, () => {
                });
                await maybeClearSessionOnAuthFailure(sessionRes);
                return await pick(Boolean(guestId), async () => {
                    return;
                }, async () => {
                    const res = await fetch(apiUrl("/api/guest"), {
                        method: "POST",
                        credentials: "include",
                        headers: buildHeaders({ "Content-Type": "application/json" }),
                        body: "{}",
                    });
                    captureResponseMeta(res);
                    await pick(Boolean(res.ok), async () => {
                        // The guest endpoint returns the CSRF token for the new anonymous session.
                        // Without capturing it here, the very first write (e.g. an upload) goes out
                        // with no token and the server rejects it with "CSRF token required".
                        try {
                            const guest = (await res.json()) as {
                                csrf_token?: string;
                            };
                            pick(Boolean(guest.csrf_token), () => {
                                setCsrfToken(guest.csrf_token);
                            }, () => {
                            });
                        }
                        catch {
                        }
                    }, async () => {
                        await pick(Boolean(res.status !== 401), async () => {
                            await res.text();
                        }, async () => {
                        });
                    });
                });
            }, async () => {
                await maybeClearSessionOnAuthFailure(sessionRes);
                return await pick(Boolean(guestId), async () => {
                    return;
                }, async () => {
                    const res = await fetch(apiUrl("/api/guest"), {
                        method: "POST",
                        credentials: "include",
                        headers: buildHeaders({ "Content-Type": "application/json" }),
                        body: "{}",
                    });
                    captureResponseMeta(res);
                    await pick(Boolean(res.ok), async () => {
                        // The guest endpoint returns the CSRF token for the new anonymous session.
                        // Without capturing it here, the very first write (e.g. an upload) goes out
                        // with no token and the server rejects it with "CSRF token required".
                        try {
                            const guest = (await res.json()) as {
                                csrf_token?: string;
                            };
                            pick(Boolean(guest.csrf_token), () => {
                                setCsrfToken(guest.csrf_token);
                            }, () => {
                            });
                        }
                        catch {
                        }
                    }, async () => {
                        await pick(Boolean(res.status !== 401), async () => {
                            await res.text();
                        }, async () => {
                        });
                    });
                });
            });
        })();
        try {
            await guestSessionPromise;
        }
        finally {
            guestSessionPromise = null;
        }
    }));
}
export function setCsrfToken(token: string | null) {
    csrfToken = token;
}
export function clearClientSessionState() {
    csrfToken = null;
    guestId = null;
    guestSessionPromise = null;
    try {
        localStorage.removeItem(GUEST_ID_STORAGE_KEY);
    }
    catch {
    }
}
/**
 * Only treat "session cookie rejected" as logout-worthy. Endpoint 401s for
 * missing guest, admin gates, bad credentials, etc. must NOT wipe CSRF: that
 * made signed-in users look logged out after an unrelated 401.
 */
export function isSessionAuthFailure(status: number, detail: string): boolean {
    return pick(Boolean(status !== 401), () => false, () => {
        const d = detail.toLowerCase();
        return (d.includes("invalid session") ||
            d.includes("session revoked") ||
            d.includes("user not found") ||
            d === "not authenticated");
    });
}
async function maybeClearSessionOnAuthFailure(res: Response): Promise<void> {
    return await pick(Boolean(res.status !== 401), async () => {
        return;
    }, async () => {
        // Clone so callers can still read the body.
        const detail = await res.clone().text().catch(() => "");
        let message = detail;
        try {
            const body = JSON.parse(detail) as {
                detail?: unknown;
            };
            pick(Boolean(typeof body.detail === "string"), () => {
                message = body.detail;
            }, () => {
            });
        }
        catch {
        }
        pick(Boolean(isSessionAuthFailure(res.status, message)), () => {
            clearClientSessionState();
        }, () => {
        });
    });
}
export async function apiFetchBytes(path: string): Promise<ArrayBuffer> {
    const res = await fetch(apiUrl(path), {
        credentials: "include",
        headers: buildHeaders(),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
    return res.arrayBuffer();
}
export async function apiPostBytes(path: string, body: unknown): Promise<ArrayBuffer> {
    const res = await fetch(apiUrl(path), {
        method: "POST",
        credentials: "include",
        headers: buildHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
    return res.arrayBuffer();
}
export async function apiGet<T>(path: string): Promise<T> {
    const res = await fetch(apiUrl(path), {
        credentials: "include",
        headers: buildHeaders(),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
    return res.json() as Promise<T>;
}
export async function apiPost<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(apiUrl(path), {
        method: "POST",
        credentials: "include",
        headers: buildHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
    return res.json() as Promise<T>;
}
/** POST that succeeds with 204 No Content (e.g. logout). */
export async function apiPostNoContent(path: string, body: unknown): Promise<void> {
    const res = await fetch(apiUrl(path), {
        method: "POST",
        credentials: "include",
        headers: buildHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
}
export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(apiUrl(path), {
        method: "PATCH",
        credentials: "include",
        headers: buildHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
    return res.json() as Promise<T>;
}
export type UploadProgressHandler = (loaded: number, total: number) => void;
export type ApiPostFormOptions = {
    onProgress?: UploadProgressHandler;
    timeoutMs?: number;
    signal?: AbortSignal;
};
export async function apiPostForm<T>(path: string, form: FormData, options?: ApiPostFormOptions): Promise<T> {
    const timeoutMs = options?.timeoutMs ?? 30 * 60 * 1000;
    const onProgress = options?.onProgress;
    const signal = options?.signal;
    return await pick(Boolean(onProgress && typeof XMLHttpRequest !== "undefined"), async () => {
        pick(Boolean(signal?.aborted), () => {
            throw new DOMException("Aborted", "AbortError");
        }, () => {
        });
        return new Promise<T>((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", apiUrl(path));
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
                pick(Boolean(event.lengthComputable), () => {
                    onProgress(event.loaded, event.total);
                }, () => {
                });
            };
            xhr.onload = () => {
                cleanup();
                const headerGuest = xhr.getResponseHeader(GUEST_HEADER);
                pick(Boolean(headerGuest && headerGuest !== guestId), () => {
                    persistGuestId(headerGuest);
                }, () => {
                });
                return pick(Boolean(xhr.status >= 200 && xhr.status < 300), () => {
                    try {
                        resolve(JSON.parse(xhr.responseText) as T);
                    }
                    catch {
                        reject(new Error("Invalid response"));
                    }
                    return;
                }, () => {
                    void (async () => {
                        const __z2 = { hit: false, val: undefined as any };
                        try {
                            const body = JSON.parse(xhr.responseText) as {
                                detail?: unknown;
                            };
                            pick(Boolean(typeof body.detail === "string"), () => {
                                reject(new Error(body.detail));
                                __z2.hit = true;
                            }, () => {
                            });
                        }
                        catch {
                        }
                        pick(Boolean(!__z2.hit), () => {
                            reject(new Error(humanizeApiFailure(xhr.status, xhr.responseText)));
                        }, () => {
                        });
                    })();
                });
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
    }, async () => {
        const res = await fetchWithTimeout(apiUrl(path), {
            method: "POST",
            credentials: "include",
            headers: buildHeaders(),
            body: form,
        }, timeoutMs, signal);
        captureResponseMeta(res);
        await maybeClearSessionOnAuthFailure(res);
        await pick(Boolean(!res.ok), async () => {
            throw new Error(await readApiError(res));
        }, async () => {
        });
        return res.json() as Promise<T>;
    });
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
    const __z3 = { hit: false, val: undefined as any };
    pick(Boolean(typeof localStorage === "undefined"), () => {
        __z3.hit = true;
        __z3.val = null;
    }, () => {
        const raw = localStorage.getItem(chunkedResumeKey(file));
        pick(Boolean(!raw), () => {
            __z3.hit = true;
            __z3.val = null;
        }, () => {
            try {
                const resume = JSON.parse(raw) as ChunkedResume & {
                    savedAt?: number;
                };
                pick(Boolean(resume.savedAt && Date.now() - resume.savedAt > 7 * 24 * 60 * 60 * 1000), () => {
                    localStorage.removeItem(chunkedResumeKey(file));
                    __z3.hit = true;
                    __z3.val = null;
                }, () => {
                    __z3.hit = true;
                    __z3.val = resume;
                });
            }
            catch {
                __z3.hit = true;
                __z3.val = null;
            }
        });
    });
    return __z3.val;
}
function writeChunkedResume(file: File, resume: ChunkedResume) {
    return pick(Boolean(typeof localStorage === "undefined"), () => {
        return;
    }, () => {
        localStorage.setItem(chunkedResumeKey(file), JSON.stringify({ ...resume, savedAt: Date.now() }));
    });
}
function clearChunkedResume(file: File) {
    return pick(Boolean(typeof localStorage === "undefined"), () => {
        return;
    }, () => {
        localStorage.removeItem(chunkedResumeKey(file));
    });
}
export async function apiUploadChunked<T extends {
    id: string;
}>(file: File, options?: ApiPostFormOptions): Promise<T> {
    const signal = options?.signal;
    const onProgress = options?.onProgress;
    const contentType = file.type || "application/octet-stream";
    let resume = readChunkedResume(file);
    let sessionId = resume?.sessionId;
    let chunkSize = resume?.chunkSize ?? 0;
    let expectedParts = resume?.expectedParts ?? 0;
    const uploaded = new Set(resume?.uploadedParts ?? []);
    await pick(Boolean(sessionId), async () => {
        try {
            const status = await apiGet<{
                received_parts: number[];
                chunk_size: number;
                expected_parts: number;
            }>(`/api/storage/chunked/${sessionId}`);
            chunkSize = status.chunk_size;
            expectedParts = status.expected_parts;
            status.received_parts.forEach((p) => uploaded.add(p));
        }
        catch {
            sessionId = undefined;
            uploaded.clear();
        }
    }, async () => {
    });
    await pick(Boolean(!sessionId), async () => {
        const init = await apiPost<{
            session_id: string;
            chunk_size: number;
            expected_parts: number;
        }>("/api/storage/chunked/init", {
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
    }, async () => {
    });
    for (let part = 1; part <= expectedParts; part += 1) {
        pick(Boolean(signal?.aborted), () => {
            throw new DOMException("Aborted", "AbortError");
        }, () => {
        });
        await pick(Boolean(uploaded.has(part)), async () => {
            pick(Boolean(onProgress), () => {
                onProgress(Math.min(file.size, part * chunkSize), file.size);
            }, () => {
            });
        }, async () => {
            const start = (part - 1) * chunkSize;
            const end = Math.min(start + chunkSize, file.size);
            const blob = file.slice(start, end);
            const res = await fetch(apiUrl(`/api/storage/chunked/${sessionId}/parts/${part}`), {
                method: "PUT",
                credentials: "include",
                headers: buildHeaders({ "Content-Type": "application/octet-stream" }),
                body: blob,
                signal,
            });
            captureResponseMeta(res);
            await pick(Boolean(!res.ok), async () => {
                throw new Error(await readApiError(res));
            }, async () => {
            });
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
            pick(Boolean(onProgress), () => {
                onProgress(end, file.size);
            }, () => {
            });
        });
    }
    const stored = await apiPost<{
        object_id: string;
        storage_key: string;
        filename: string;
        content_type: string;
        size_bytes: number;
    }>(`/api/storage/chunked/${sessionId}/complete`, {});
    const done = await apiPost<T>("/api/sources/from-object", { object_id: stored.object_id });
    clearChunkedResume(file);
    return done;
}
export async function apiUploadFile<T extends {
    id: string;
}>(file: File, options?: ApiPostFormOptions): Promise<T> {
    return pick(Boolean(file.size >= CHUNKED_UPLOAD_THRESHOLD), () => apiUploadChunked<T>(file, options), () => {
        const form = new FormData();
        form.append("file", file);
        return apiPostForm<T>("/api/sources", form, options);
    });
}
export async function apiUploadFiles<T extends {
    id: string;
}>(files: File[], options?: ApiPostFormOptions): Promise<T> {
    pick(Boolean(files.length === 0), () => {
        throw new Error("No files selected");
    }, () => {
    });
    return pick(Boolean(files.length === 1), () => apiUploadFile<T>(files[0], options), () => {
        const totalSize = files.reduce((sum, file) => sum + file.size, 0);
        pick(Boolean(totalSize >= CHUNKED_UPLOAD_THRESHOLD), () => {
            throw new Error("Combined upload is too large for multi-file combine. Upload files one at a time or use smaller files.");
        }, () => {
        });
        const form = new FormData();
        for (const file of files) {
            form.append("files", file);
        }
        return apiPostForm<T>("/api/sources/upload-bundle", form, options);
    });
}
export async function apiDelete(path: string): Promise<void> {
    const res = await fetch(apiUrl(path), {
        method: "DELETE",
        credentials: "include",
        headers: buildHeaders(),
    });
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok), async () => {
        throw new Error(await readApiError(res));
    }, async () => {
    });
}
function stripCr(value: string): string {
    return value.replace(/\r$/, "");
}
function parseSseEventBlock(part: string): {
    event: string;
    data: string;
} | null {
    const lines = part.split(/\n/).map(stripCr);
    // One SSE event may carry several `data:` lines; the spec joins them with "\n".
    // Taking only the first silently truncated any payload containing a newline.
    const dataLines = lines
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.replace(/^data:\s?/, ""));
    return pick(Boolean(dataLines.length === 0), () => null, () => {
        const eventLine = lines.find((l) => l.startsWith("event:"));
        const event = stripCr(eventLine?.replace(
            /^event:\s*/,
            "",
        ) ??
            "message");
        return { event, data: dataLines.join("\n") };
    });
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
export async function apiPostSSE(path: string, body: unknown, onChunkOrHandlers: ((text: string) => void) | ChatSseHandlers, options?: ApiPostSSEOptions): Promise<void> {
    const handlers: ChatSseHandlers = choose(Boolean(typeof onChunkOrHandlers === "function"), { onChunk: onChunkOrHandlers }, onChunkOrHandlers);
    const { onChunk, onStatus, onEvent } = handlers;
    const res = await fetchWithTimeout(apiUrl(path), {
        method: "POST",
        credentials: "include",
        headers: buildHeaders({
            "Content-Type": "application/json",
            Accept: "text/event-stream",
        }),
        body: JSON.stringify(body),
    }, 120000, options?.signal);
    captureResponseMeta(res);
    await maybeClearSessionOnAuthFailure(res);
    await pick(Boolean(!res.ok || !res.body), async () => {
        const detail = await readApiError(res).catch(() => humanizeApiFailure(res.status, "SSE failed"));
        throw new Error(detail);
    }, async () => {
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    {/*..............................................................................*/
        let __keep6 = true;
        while (true && __keep6) {
            await pick(Boolean(options?.signal?.aborted), async () => {
                await reader.cancel();
                throw new DOMException("Aborted", "AbortError");
            }, async () => {
            });
            const { done, value } = await reader.read();
            pick(Boolean(done), () => {
                __keep6 = false;
            }, () => {
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split(/\r?\n\r?\n/);
                buffer = parts.pop() ?? "";
                for (const part of parts) {
                    const parsed = parseSseEventBlock(part);
                    pick(Boolean(!parsed), () => {
                    }, () => {
                        const { event, data } = parsed;
                        onEvent?.(event, data);
                        pick(Boolean(event === "status"), () => {
                            try {
                                const payload = JSON.parse(data) as {
                                    phase?: string;
                                };
                                pick(Boolean(payload.phase), () => {
                                    onStatus?.(payload.phase);
                                }, () => {
                                });
                            }
                            catch {
                            }
                        }, () => {
                            pick(Boolean(event === "token"), () => {
                                try {
                                    const payload = JSON.parse(data) as {
                                        text?: string;
                                    };
                                    pick(Boolean(payload.text), () => {
                                        onChunk?.(payload.text);
                                    }, () => {
                                    });
                                }
                                catch {
                                    onChunk?.(data);
                                }
                            }, () => {
                                pick(Boolean(event === "error"), () => {
                                    try {
                                        const payload = JSON.parse(data) as {
                                            message?: string;
                                        };
                                        throw new Error(payload.message ?? "Chat error");
                                    }
                                    catch (e) {/*..............................................................................*/
                                        pick(Boolean(e instanceof Error && e.message !== "Chat error"), () => {
                                            throw e;
                                        }, () => {
                                        });
                                        throw new Error("Chat error");
                                    }
                                }, () => {
                                });
                            });
                        });
                    });
                }
            });
        }
    }
}
