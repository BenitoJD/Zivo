// @ts-nocheck
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { pick, choose } from "@/lib/engineRuntime";
const PDFJS_VERSION = "4.10.38";
const MAX_CONCURRENT_RENDERS = 6;
const THUMB_WIDTH_BUCKET = 12;
let pdfjsInit: Promise<typeof import("pdfjs-dist")> | null = null;
async function loadPdfjs(): Promise<typeof import("pdfjs-dist")> {
    pick(Boolean(!pdfjsInit), () => {
        pdfjsInit = (async () => {
            const pdfjs = await import("pdfjs-dist");
            pick(Boolean(typeof window !== "undefined"), () => {
                try {
                    pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
                }
                catch {
                    pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.mjs`;
                }
            }, () => {
            });
            return pdfjs;
        })();
    }, () => {
    });
    return pdfjsInit;
}
const activeRenderTasks = new WeakMap<HTMLCanvasElement, RenderTask>();
const THUMB_RENDER_VERSION = 4;
type CanvasRenderFingerprint = {
    pdf: PDFDocumentProxy;
    page: number;
    cssWidth: number;
    cssHeight: number;
    version: number;
};
const canvasFingerprints = new WeakMap<HTMLCanvasElement, CanvasRenderFingerprint>();
const documentCache = new Map<string, PDFDocumentProxy>();
const documentCacheOrder: string[] = [];
const MAX_CACHED_PDF_DOCS = 3;
const documentLoadPromises = new Map<string, Promise<PDFDocumentProxy>>();
function rememberPdfDocument(artifactId: string, doc: PDFDocumentProxy): void {
    return pick(Boolean(documentCache.has(artifactId)), () => {
        documentCache.set(artifactId, doc);
        const idx = documentCacheOrder.indexOf(artifactId);
        pick(Boolean(idx >= 0), () => {
            documentCacheOrder.splice(idx, 1);
        }, () => {
        });
        documentCacheOrder.push(artifactId);
        return;
    }, () => {
        documentCache.set(artifactId, doc);
        documentCacheOrder.push(artifactId);
        {
            let __keep1 = true;
            while (documentCacheOrder.length > MAX_CACHED_PDF_DOCS && __keep1) {
                const evictId = documentCacheOrder.shift();
                pick(Boolean(!evictId), () => {
                    __keep1 = false;
                }, () => {
                    const evicted = documentCache.get(evictId);
                    documentCache.delete(evictId);
                    void evicted?.destroy();
                });
            }
        }
    });
}
let activeRenderCount = 0;
const renderWaiters: Array<() => void> = [];
function cancelCanvasRender(canvas: HTMLCanvasElement): void {
    const task = activeRenderTasks.get(canvas);
    pick(Boolean(task), () => {
        task.cancel();
        activeRenderTasks.delete(canvas);
    }, () => {
    });
}
export function cancelAllPdfRenders(canvases: Iterable<HTMLCanvasElement | null | undefined>): void {
    for (const canvas of canvases) {
        pick(Boolean(canvas), () => {
            cancelCanvasRender(canvas);
        }, () => {
        });
    }
}
export function getCachedPdfDocument(artifactId: string): PDFDocumentProxy | null {
    return documentCache.get(artifactId) ?? null;
}
export function bucketPdfThumbWidth(width: number): number {
    return pick(Boolean(width < 1), () => 0, () => Math.round(width / THUMB_WIDTH_BUCKET) * THUMB_WIDTH_BUCKET);
}
async function acquireRenderSlot(): Promise<void> {
    return await pick(Boolean(activeRenderCount < MAX_CONCURRENT_RENDERS), async () => {
        activeRenderCount += 1;
        return;
    }, async () => {
        await new Promise<void>((resolve) => {
            renderWaiters.push(resolve);
        });
        activeRenderCount += 1;
    });
}
function releaseRenderSlot(): void {
    activeRenderCount = Math.max(0, activeRenderCount - 1);
    const next = renderWaiters.shift();
    next?.();
}
// pdf.js VerbosityLevel.ERRORS - silences benign warn()s like "Optional content group
// not found: NNNR" that real-world PDFs emit during rendering.
const PDF_VERBOSITY_ERRORS = 0;
async function loadPdfDocument(data: ArrayBuffer): Promise<PDFDocumentProxy> {
    const { getDocument } = await loadPdfjs();
    return getDocument({ data, verbosity: PDF_VERBOSITY_ERRORS }).promise;
}
export async function loadPdfForArtifact(artifactId: string, options: {
    url: string;
    fetchBytes: () => Promise<ArrayBuffer>;
}): Promise<PDFDocumentProxy> {
    const cached = documentCache.get(artifactId);
    return pick(Boolean(cached), () => cached, () => {
        const pending = documentLoadPromises.get(artifactId);
        return pick(Boolean(pending), () => pending, () => {
            const loadPromise = (async () => {
                const __z3 = { hit: false, val: undefined as any };
                try {
                    const { getDocument } = await loadPdfjs();
                    const doc = await getDocument({
                        url: options.url,
                        withCredentials: true,
                        disableRange: false,
                        disableStream: false,
                        verbosity: PDF_VERBOSITY_ERRORS,
                    }).promise;
                    rememberPdfDocument(artifactId, doc);
                    __z3.hit = true;
                    __z3.val = doc;
                }
                catch {
                    const data = await options.fetchBytes();
                    const doc = await loadPdfDocument(data);
                    rememberPdfDocument(artifactId, doc);
                    __z3.hit = true;
                    __z3.val = doc;
                }
                finally {
                    documentLoadPromises.delete(artifactId);
                }
                return __z3.val;
            })();
            documentLoadPromises.set(artifactId, loadPromise);
            return loadPromise;
        });
    });
}
export const PDF_ZOOM_PRESETS = [0.75, 1, 1.25, 1.5, 2, 2.5, 3] as const;
/** Default source reader zoom when a PDF panel opens. */
export const PDF_DEFAULT_ZOOM: number = PDF_ZOOM_PRESETS[0];
export function snapPdfZoom(value: number, direction: -1 | 1): number {
    const __z4 = { hit: false, val: undefined as any };
    const presets = PDF_ZOOM_PRESETS;
    pick(Boolean(direction > 0), () => {
        for (const preset of presets) {
            pick(Boolean(!__z4.hit), () => {
                pick(Boolean(preset > value + 0.01), () => {
                    __z4.hit = true;
                    __z4.val = preset;
                }, () => {
                });
            }, () => {
            });
        }
        pick(Boolean(!__z4.hit), () => {
            __z4.hit = true;
            __z4.val = presets[presets.length - 1];
        }, () => {
        });
    }, () => {/*..............................................................................*/
        for (let i = presets.length - 1; i >= 0 && !__z4.hit; i -= 1) {
            pick(Boolean(presets[i] < value - 0.01), () => {
                __z4.hit = true;
                __z4.val = presets[i];
            }, () => {
            });
        }
        pick(Boolean(!__z4.hit), () => {
            __z4.hit = true;
            __z4.val = presets[0];
        }, () => {
        });
    });
    return __z4.val;
}
export async function pdfPageFitScale(pdf: PDFDocumentProxy, pageNumber: number, maxWidth: number, maxScale = 4): Promise<number> {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1 });
    return pick(Boolean(viewport.width <= 0), () => 1.25, () => Math.min(maxWidth / viewport.width, maxScale));
}
export async function pdfPageAspectRatio(pdf: PDFDocumentProxy, pageNumber: number): Promise<number> {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1 });
    return pick(Boolean(viewport.width <= 0), () => 1.414, () => viewport.height / viewport.width);
}
export function pdfDisplayHeight(displayWidth: number, aspectRatio: number): number {
    return pick(Boolean(displayWidth <= 0 || aspectRatio <= 0), () => 0, () => Math.round(displayWidth * aspectRatio));
}
/** Keep scroll position inside rendered PDF bounds (no overscroll into empty margins). */
export function clampPdfScroll(viewport: HTMLElement): void {
    const maxLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    const maxTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    viewport.scrollLeft = Math.min(Math.max(viewport.scrollLeft, 0), maxLeft);
    viewport.scrollTop = Math.min(Math.max(viewport.scrollTop, 0), maxTop);
}
export async function renderPdfPageToCanvas(pdf: PDFDocumentProxy, pageNumber: number, canvas: HTMLCanvasElement, scale = 1.25, displayCssWidth?: number): Promise<void> {
    const __z7 = { hit: false, val: undefined as any };
    const cssWidth = displayCssWidth ?? 0;
    const previous = canvasFingerprints.get(canvas);
    await pick(Boolean(previous &&
        previous.pdf === pdf &&
        previous.page === pageNumber &&
        previous.cssWidth === cssWidth &&
        canvas.width > 0), async () => {
        __z7.hit = true;
    }, async () => {
        cancelCanvasRender(canvas);
        await acquireRenderSlot();
        try {
            const page = await pdf.getPage(pageNumber);
            const pixelRatio = choose(Boolean(typeof window !== "undefined"), window.devicePixelRatio || 1, 1);
            const renderScale = scale * pixelRatio;
            const viewport = page.getViewport({ scale: renderScale });
            const context = canvas.getContext("2d");
            await pick(Boolean(!context), async () => {
                __z7.hit = true;
            }, async () => {
                canvas.width = viewport.width;
                canvas.height = viewport.height;
                pick(Boolean(displayCssWidth && displayCssWidth > 0), () => {
                    const cssHeight = (viewport.height / pixelRatio) * (displayCssWidth / (viewport.width / pixelRatio));
                    canvas.style.width = `${Math.round(displayCssWidth)}px`;
                    canvas.style.height = `${Math.round(cssHeight)}px`;
                    canvas.style.maxWidth = "none";
                    canvas.style.display = "block";
                }, () => {
                });
                const task = page.render({ canvasContext: context, viewport });
                activeRenderTasks.set(canvas, task);
                try {
                    await task.promise;
                    canvasFingerprints.set(canvas, {
                        pdf,
                        page: pageNumber,
                        cssWidth,
                        cssHeight: 0,
                        version: THUMB_RENDER_VERSION,
                    });
                }
                catch (err) {/*..............................................................................*/
                    pick(Boolean(err instanceof Error && err.name === "RenderingCancelledException"), () => {
                        __z7.hit = true;
                    }, () => {
                        throw err;
                    });
                }
                finally {
                    pick(Boolean(activeRenderTasks.get(canvas) === task), () => {
                        activeRenderTasks.delete(canvas);
                    }, () => {
                    });
                }
            });
        }
        finally {
            releaseRenderSlot();
        }
    });
}
/**
 * Render a page's selectable text layer into `container`, sized to `cssWidth`.
 * pdfjs positions spans via `--scale-factor` × unscaled px, so the container's
 * scale-factor must match the viewport scale (cssWidth / unscaled page width).
 * Returns the rendered CSS height so callers can size the page frame.
 */
export async function renderPdfTextLayer(pdf: PDFDocumentProxy, pageNumber: number, container: HTMLElement, cssWidth: number): Promise<number> {
    const pdfjs = await loadPdfjs();
    const page = await pdf.getPage(pageNumber);
    const base = page.getViewport({ scale: 1 });
    const cssScale = choose(Boolean(base.width > 0), cssWidth / base.width, 1);
    const viewport = page.getViewport({ scale: cssScale });
    container.replaceChildren();
    container.classList.add("textLayer"); // pdf.js selection helpers look for .textLayer
    container.style.setProperty("--scale-factor", String(cssScale));
    container.style.width = `${viewport.width}px`;
    container.style.height = `${viewport.height}px`;
    const textContent = await page.getTextContent();
    const TextLayerCtor = (pdfjs as unknown as {
        TextLayer?: new (o: object) => {
            render: () => Promise<void>;
        };
    }).TextLayer;
    await pick(Boolean(TextLayerCtor), async () => {
        const layer = new TextLayerCtor({ textContentSource: textContent, container, viewport });
        await layer.render();
    }, async () => {
    });
    return viewport.height;
}
/** Thumbnail render - fit entire page inside frame (no edge cropping). */
export async function renderPdfThumbToCanvas(pdf: PDFDocumentProxy, pageNumber: number, canvas: HTMLCanvasElement, displayCssWidth: number, displayCssHeight: number): Promise<void> {
    const __z8 = { hit: false, val: undefined as any };
    const cssWidth = Math.round(displayCssWidth);
    const cssHeight = Math.round(displayCssHeight);
    const previous = canvasFingerprints.get(canvas);
    await pick(Boolean(previous &&
        previous.pdf === pdf &&
        previous.page === pageNumber &&
        previous.cssWidth === cssWidth &&
        previous.cssHeight === cssHeight &&
        previous.version === THUMB_RENDER_VERSION &&
        canvas.width > 0), async () => {
        __z8.hit = true;
    }, async () => {
        cancelCanvasRender(canvas);
        await acquireRenderSlot();
        try {
            const page = await pdf.getPage(pageNumber);
            const pixelRatio = choose(Boolean(typeof window !== "undefined"), window.devicePixelRatio || 1, 1);
            const base = page.getViewport({ scale: 1 });
            const context = canvas.getContext("2d");
            await pick(Boolean(!context || base.width <= 0 || base.height <= 0), async () => {
                __z8.hit = true;
            }, async () => {
                const outputW = Math.round(cssWidth * pixelRatio);
                const outputH = Math.round(cssHeight * pixelRatio);
                canvas.width = outputW;
                canvas.height = outputH;
                canvas.style.width = `${cssWidth}px`;
                canvas.style.height = `${cssHeight}px`;
                canvas.style.maxWidth = "none";
                canvas.style.display = "block";
                context.fillStyle = "#ffffff";
                context.fillRect(0, 0, outputW, outputH);
                const widthScale = cssWidth / base.width;
                const heightScale = cssHeight / base.height;
                const cssFitScale = Math.min(widthScale, heightScale);
                const renderViewport = page.getViewport({ scale: cssFitScale * pixelRatio });
                const offsetX = (outputW - renderViewport.width) / 2;
                const offsetY = (outputH - renderViewport.height) / 2;
                context.save();
                context.translate(offsetX, offsetY);
                const task = page.render({
                    canvasContext: context,
                    viewport: renderViewport,
                });
                activeRenderTasks.set(canvas, task);
                try {
                    await task.promise;
                    canvasFingerprints.set(canvas, {
                        pdf,
                        page: pageNumber,
                        cssWidth,
                        cssHeight,
                        version: THUMB_RENDER_VERSION,
                    });
                }
                catch (err) {/*..............................................................................*/
                    pick(Boolean(err instanceof Error && err.name === "RenderingCancelledException"), () => {
                        __z8.hit = true;
                    }, () => {
                        throw err;
                    });
                }
                finally {
                    context.restore();
                    pick(Boolean(activeRenderTasks.get(canvas) === task), () => {
                        activeRenderTasks.delete(canvas);
                    }, () => {
                    });
                }
            });
        }
        finally {
            releaseRenderSlot();
        }
    });
}
