import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

const PDFJS_VERSION = "4.10.38";
const MAX_CONCURRENT_RENDERS = 6;
const THUMB_WIDTH_BUCKET = 12;

let pdfjsInit: Promise<typeof import("pdfjs-dist")> | null = null;

async function loadPdfjs(): Promise<typeof import("pdfjs-dist")> {
  if (!pdfjsInit) {
    pdfjsInit = (async () => {
      const pdfjs = await import("pdfjs-dist");
      if (typeof window !== "undefined") {
        try {
          pdfjs.GlobalWorkerOptions.workerSrc = new URL(
            "pdfjs-dist/build/pdf.worker.min.mjs",
            import.meta.url,
          ).toString();
        } catch {
          pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.mjs`;
        }
      }
      return pdfjs;
    })();
  }
  return pdfjsInit;
}

const activeRenderTasks = new WeakMap<HTMLCanvasElement, RenderTask>();

const THUMB_RENDER_VERSION = 3;

type CanvasRenderFingerprint = {
  pdf: PDFDocumentProxy;
  page: number;
  cssWidth: number;
  cssHeight: number;
  version: number;
};

const canvasFingerprints = new WeakMap<HTMLCanvasElement, CanvasRenderFingerprint>();

const documentCache = new Map<string, PDFDocumentProxy>();
const documentLoadPromises = new Map<string, Promise<PDFDocumentProxy>>();

let activeRenderCount = 0;
const renderWaiters: Array<() => void> = [];

function cancelCanvasRender(canvas: HTMLCanvasElement): void {
  const task = activeRenderTasks.get(canvas);
  if (task) {
    task.cancel();
    activeRenderTasks.delete(canvas);
  }
}

export function cancelAllPdfRenders(canvases: Iterable<HTMLCanvasElement | null | undefined>): void {
  for (const canvas of canvases) {
    if (canvas) cancelCanvasRender(canvas);
  }
}

export function getCachedPdfDocument(artifactId: string): PDFDocumentProxy | null {
  return documentCache.get(artifactId) ?? null;
}

export function bucketPdfThumbWidth(width: number): number {
  if (width < 1) return 0;
  return Math.round(width / THUMB_WIDTH_BUCKET) * THUMB_WIDTH_BUCKET;
}

async function acquireRenderSlot(): Promise<void> {
  if (activeRenderCount < MAX_CONCURRENT_RENDERS) {
    activeRenderCount += 1;
    return;
  }
  await new Promise<void>((resolve) => {
    renderWaiters.push(resolve);
  });
  activeRenderCount += 1;
}

function releaseRenderSlot(): void {
  activeRenderCount = Math.max(0, activeRenderCount - 1);
  const next = renderWaiters.shift();
  next?.();
}

async function loadPdfDocument(data: ArrayBuffer): Promise<PDFDocumentProxy> {
  const { getDocument } = await loadPdfjs();
  return getDocument({ data }).promise;
}

export async function loadPdfForArtifact(
  artifactId: string,
  options: { url: string; fetchBytes: () => Promise<ArrayBuffer> },
): Promise<PDFDocumentProxy> {
  const cached = documentCache.get(artifactId);
  if (cached) return cached;

  const pending = documentLoadPromises.get(artifactId);
  if (pending) return pending;

  const loadPromise = (async () => {
    try {
      const { getDocument } = await loadPdfjs();
      const doc = await getDocument({
        url: options.url,
        withCredentials: true,
        disableRange: false,
        disableStream: false,
      }).promise;
      documentCache.set(artifactId, doc);
      return doc;
    } catch {
      const data = await options.fetchBytes();
      const doc = await loadPdfDocument(data);
      documentCache.set(artifactId, doc);
      return doc;
    } finally {
      documentLoadPromises.delete(artifactId);
    }
  })();

  documentLoadPromises.set(artifactId, loadPromise);
  return loadPromise;
}

export const PDF_ZOOM_PRESETS = [0.75, 1, 1.25, 1.5, 2, 2.5, 3] as const;

export function snapPdfZoom(value: number, direction: -1 | 1): number {
  const presets = PDF_ZOOM_PRESETS;
  if (direction > 0) {
    for (const preset of presets) {
      if (preset > value + 0.01) return preset;
    }
    return presets[presets.length - 1];
  }
  for (let i = presets.length - 1; i >= 0; i -= 1) {
    if (presets[i] < value - 0.01) return presets[i];
  }
  return presets[0];
}

export async function pdfPageFitScale(
  pdf: PDFDocumentProxy,
  pageNumber: number,
  maxWidth: number,
  maxScale = 4,
): Promise<number> {
  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale: 1 });
  if (viewport.width <= 0) return 1.25;
  return Math.min(maxWidth / viewport.width, maxScale);
}

export async function pdfPageAspectRatio(
  pdf: PDFDocumentProxy,
  pageNumber: number,
): Promise<number> {
  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale: 1 });
  if (viewport.width <= 0) return 1.414;
  return viewport.height / viewport.width;
}

export function pdfDisplayHeight(displayWidth: number, aspectRatio: number): number {
  if (displayWidth <= 0 || aspectRatio <= 0) return 0;
  return Math.round(displayWidth * aspectRatio);
}

/** Keep scroll position inside rendered PDF bounds (no overscroll into empty margins). */
export function clampPdfScroll(viewport: HTMLElement): void {
  const maxLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
  const maxTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
  viewport.scrollLeft = Math.min(Math.max(viewport.scrollLeft, 0), maxLeft);
  viewport.scrollTop = Math.min(Math.max(viewport.scrollTop, 0), maxTop);
}

export async function renderPdfPageToCanvas(
  pdf: PDFDocumentProxy,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  scale = 1.25,
  displayCssWidth?: number,
): Promise<void> {
  const cssWidth = displayCssWidth ?? 0;
  const previous = canvasFingerprints.get(canvas);
  if (
    previous &&
    previous.pdf === pdf &&
    previous.page === pageNumber &&
    previous.cssWidth === cssWidth &&
    canvas.width > 0
  ) {
    return;
  }

  cancelCanvasRender(canvas);
  await acquireRenderSlot();

  try {
    const page = await pdf.getPage(pageNumber);
    const pixelRatio = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const renderScale = scale * pixelRatio;
    const viewport = page.getViewport({ scale: renderScale });
    const context = canvas.getContext("2d");
    if (!context) return;

    canvas.width = viewport.width;
    canvas.height = viewport.height;

    if (displayCssWidth && displayCssWidth > 0) {
      const cssHeight = (viewport.height / pixelRatio) * (displayCssWidth / (viewport.width / pixelRatio));
      canvas.style.width = `${Math.round(displayCssWidth)}px`;
      canvas.style.height = `${Math.round(cssHeight)}px`;
      canvas.style.maxWidth = "none";
      canvas.style.display = "block";
    }

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
    } catch (err) {
      if (err instanceof Error && err.name === "RenderingCancelledException") return;
      throw err;
    } finally {
      if (activeRenderTasks.get(canvas) === task) {
        activeRenderTasks.delete(canvas);
      }
    }
  } finally {
    releaseRenderSlot();
  }
}

/** Thumbnail render — cover-fills frame with slight zoom so no dead space at edges. */
export async function renderPdfThumbToCanvas(
  pdf: PDFDocumentProxy,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  displayCssWidth: number,
  displayCssHeight: number,
  zoom = 1.08,
): Promise<void> {
  const cssWidth = Math.round(displayCssWidth);
  const cssHeight = Math.round(displayCssHeight);
  const previous = canvasFingerprints.get(canvas);
  if (
    previous &&
    previous.pdf === pdf &&
    previous.page === pageNumber &&
    previous.cssWidth === cssWidth &&
    previous.cssHeight === cssHeight &&
    previous.version === THUMB_RENDER_VERSION &&
    canvas.width > 0
  ) {
    return;
  }

  cancelCanvasRender(canvas);
  await acquireRenderSlot();

  try {
    const page = await pdf.getPage(pageNumber);
    const pixelRatio = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const base = page.getViewport({ scale: 1 });
    const context = canvas.getContext("2d");
    if (!context || base.width <= 0 || base.height <= 0) return;

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

    const widthScale = (cssWidth / base.width) * zoom;
    const heightScale = (cssHeight / base.height) * zoom;
    const cssCoverScale = Math.max(widthScale, heightScale);
    const renderViewport = page.getViewport({ scale: cssCoverScale * pixelRatio });
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
    } catch (err) {
      if (err instanceof Error && err.name === "RenderingCancelledException") return;
      throw err;
    } finally {
      context.restore();
      if (activeRenderTasks.get(canvas) === task) {
        activeRenderTasks.delete(canvas);
      }
    }
  } finally {
    releaseRenderSlot();
  }
}
