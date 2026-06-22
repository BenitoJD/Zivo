import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";

const PDFJS_VERSION = "4.10.38";

if (typeof window !== "undefined") {
  GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.mjs`;
}

const activeRenderTasks = new WeakMap<HTMLCanvasElement, RenderTask>();

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

export async function loadPdfDocument(data: ArrayBuffer): Promise<PDFDocumentProxy> {
  return getDocument({ data }).promise;
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
  cancelCanvasRender(canvas);

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
  } catch (err) {
    if (err instanceof Error && err.name === "RenderingCancelledException") return;
    throw err;
  } finally {
    if (activeRenderTasks.get(canvas) === task) {
      activeRenderTasks.delete(canvas);
    }
  }
}
