"use client";

/**
 * Shared download + print-to-PDF helpers.
 *
 * Both patterns were already inlined in NotesView, QuizBuilderView and ResumeView
 * before this file existed; Brainstorm is the fourth caller, so they live here now
 * instead of being copied again. The three older call sites still hold their own
 * copies — they can migrate here whenever someone is in them anyway.
 */

/** Save a Blob to disk under `filename`. */
export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/**
 * Print a live DOM node via the browser's own "Save as PDF" — no PDF library.
 *
 * Clones the app's stylesheets into a new window so the printed sheet looks like
 * the app. The node must be mounted and styled: this prints what is on screen, so
 * it cannot export content that was never rendered.
 */
export function printNodeToPdf(node: HTMLElement | null, title: string) {
  if (!node) return;
  const win = window.open("", "_blank", "width=820,height=1000");
  if (!win) return;
  const styles = Array.from(document.querySelectorAll('style, link[rel="stylesheet"]'))
    .map((el) => el.outerHTML)
    .join("\n");
  win.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>${styles}` +
      `<style>body{background:#fff;margin:0;padding:32px;}` +
      `.zivo-print-sheet{max-width:760px;margin:0 auto;}@page{margin:16mm;}</style></head>` +
      `<body><div class="zivo-print-sheet">${node.innerHTML}</div></body></html>`,
  );
  win.document.close();
  win.focus();
  // Give the cloned stylesheets a beat to apply before printing.
  setTimeout(() => win.print(), 500);
}
