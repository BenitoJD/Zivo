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

export async function renderPdfPageToCanvas(
  pdf: PDFDocumentProxy,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  scale = 1.25,
): Promise<void> {
  cancelCanvasRender(canvas);

  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale });
  const context = canvas.getContext("2d");
  if (!context) return;

  canvas.width = viewport.width;
  canvas.height = viewport.height;

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
