"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Button, Center, Loader, Paper, Stack, Text } from "@mantine/core";
import {
  IconBulb,
  IconMessage2,
  IconNotebook,
  IconWand,
} from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  pdfPageAspectRatio,
  renderPdfPageToCanvas,
  renderPdfTextLayer,
} from "@/lib/pdf";
import { apiGet } from "@/lib/api/client";

/**
 * Read mode — a focused reader for the source with a real, selectable text layer.
 * Select any passage and a popover lets you quote it to the study buddy, ask it to
 * explain/simplify, or save it as a note. PDFs render visually (canvas + pdf.js text
 * layer); non-PDF sources render their extracted text (also selectable).
 */
export function PdfReader({
  artifactId,
  pdfDoc,
  isPdf,
  pageCount,
  onQuote,
  onAsk,
  onSaveQuote,
}: {
  artifactId: string;
  pdfDoc: PDFDocumentProxy | null;
  isPdf: boolean;
  pageCount: number;
  onQuote: (text: string) => void;
  onAsk: (message: string) => void;
  onSaveQuote: (text: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [sel, setSel] = useState<{ text: string; x: number; y: number } | null>(null);

  // Track the readable column width (capped for comfortable line length).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setWidth(Math.min(el.clientWidth - 32, 820));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onMouseUp = useCallback(() => {
    const s = window.getSelection();
    const text = s?.toString().trim() ?? "";
    if (!text || text.length < 3) {
      setSel(null);
      return;
    }
    const root = scrollRef.current;
    if (!root || !s || s.rangeCount === 0) return;
    // Only react to selections inside the reader.
    if (!root.contains(s.anchorNode)) return;
    const rect = s.getRangeAt(0).getBoundingClientRect();
    setSel({ text, x: rect.left + rect.width / 2, y: rect.top });
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      // Keep the popover if the click is on it.
      const t = e.target as HTMLElement;
      if (t.closest?.("[data-reader-popover]")) return;
      setSel(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const act = (fn: () => void) => {
    fn();
    setSel(null);
    window.getSelection()?.removeAllRanges();
  };

  return (
    <Box pos="relative" h="100%" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <style>{TEXT_LAYER_CSS}</style>
      <Box
        ref={scrollRef}
        data-reader-scroll
        onMouseUp={onMouseUp}
        flex={1}
        mih={0}
        px="md"
        py="md"
        style={{ overflowY: "auto", overflowX: "hidden", overscrollBehavior: "contain" }}
      >
        <Box maw={width || 820} mx="auto">
          {isPdf && pdfDoc ? (
            Array.from({ length: pageCount }).map((_, i) => (
              <PdfPage key={i + 1} pdfDoc={pdfDoc} pageNumber={i + 1} width={width} />
            ))
          ) : (
            <TextReader artifactId={artifactId} />
          )}
        </Box>
      </Box>

      {sel ? (
        <Paper
          data-reader-popover
          withBorder
          radius="xl"
          shadow="md"
          p={4}
          style={{
            position: "fixed",
            left: Math.max(12, Math.min(sel.x - 150, window.innerWidth - 312)),
            top: Math.max(12, sel.y - 52),
            zIndex: 400,
            display: "flex",
            gap: 2,
            background: "var(--mantine-color-body)",
          }}
        >
          <PopBtn icon={<IconMessage2 size={14} />} label="Ask" onClick={() => act(() => onQuote(sel.text))} />
          <PopBtn icon={<IconBulb size={14} />} label="Explain" onClick={() => act(() => onAsk(`Explain this passage in simple terms:\n\n"${sel.text}"`))} />
          <PopBtn icon={<IconWand size={14} />} label="Simplify" onClick={() => act(() => onAsk(`Simplify this so it's easy to understand:\n\n"${sel.text}"`))} />
          <PopBtn icon={<IconNotebook size={14} />} label="Save" onClick={() => act(() => onSaveQuote(sel.text))} />
        </Paper>
      ) : null}
    </Box>
  );
}

function PopBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <Button size="compact-xs" variant="subtle" color="lavender" leftSection={icon} radius="xl" onClick={onClick}>
      {label}
    </Button>
  );
}

function PdfPage({
  pdfDoc,
  pageNumber,
  width,
}: {
  pdfDoc: PDFDocumentProxy;
  pageNumber: number;
  width: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState(1.414);
  const [visible, setVisible] = useState(false);
  const renderedAt = useRef(0);

  useEffect(() => {
    let alive = true;
    void pdfPageAspectRatio(pdfDoc, pageNumber).then((a) => alive && a > 0 && setAspect(a));
    return () => { alive = false; };
  }, [pdfDoc, pageNumber]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && setVisible(true)),
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || width < 1) return;
    if (renderedAt.current === width) return;
    renderedAt.current = width;
    const canvas = canvasRef.current;
    const textDiv = textRef.current;
    void (async () => {
      try {
        if (canvas) {
          const cssScale = width / (await baseWidth(pdfDoc, pageNumber));
          await renderPdfPageToCanvas(pdfDoc, pageNumber, canvas, cssScale, width);
        }
        if (textDiv) await renderPdfTextLayer(pdfDoc, pageNumber, textDiv, width);
      } catch {
        /* render race — ignore */
      }
    })();
  }, [visible, width, pdfDoc, pageNumber]);

  const height = width > 0 ? width * aspect : 0;
  return (
    <Box
      ref={wrapRef}
      mb="md"
      style={{
        position: "relative",
        width: width || "100%",
        height: height || undefined,
        minHeight: height ? undefined : 200,
        margin: "0 auto 16px",
        background: "var(--mantine-color-gray-0)",
        borderRadius: 8,
        boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
        overflow: "hidden",
      }}
    >
      <canvas ref={canvasRef} style={{ display: "block", width: "100%" }} />
      <div ref={textRef} className="zv-textlayer" style={{ position: "absolute", inset: 0 }} />
      {!visible ? (
        <Center style={{ position: "absolute", inset: 0 }}>
          <Text c="dimmed" fz="xs">Page {pageNumber}</Text>
        </Center>
      ) : null}
    </Box>
  );
}

async function baseWidth(pdf: PDFDocumentProxy, pageNumber: number): Promise<number> {
  const page = await pdf.getPage(pageNumber);
  return page.getViewport({ scale: 1 }).width || 1;
}

function TextReader({ artifactId }: { artifactId: string }) {
  const [pages, setPages] = useState<{ page: number; text: string }[] | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let alive = true;
    apiGet<{ pages: { page: number; text: string }[] }>(`/api/documents/${artifactId}/pages`)
      .then((d) => alive && setPages(d.pages || []))
      .catch(() => alive && setErr(true));
    return () => { alive = false; };
  }, [artifactId]);

  if (err) return <Text c="dimmed" ta="center" py="xl">Couldn’t load the text.</Text>;
  if (!pages) return <Center py="xl"><Loader color="lavender" size="sm" /></Center>;
  return (
    <Stack gap="lg" py="xs">
      {pages.map((p) => (
        <Stack key={p.page} gap="sm">
          {p.text.split(/\n{2,}/).map((para, j) =>
            para.trim() ? (
              <Text key={j} fz="md" lh={1.7} c="var(--mantine-color-text)" style={{ whiteSpace: "pre-wrap" }}>
                {para.trim()}
              </Text>
            ) : null,
          )}
        </Stack>
      ))}
    </Stack>
  );
}

const TEXT_LAYER_CSS = `
.zv-textlayer { overflow: hidden; opacity: 1; line-height: 1; text-size-adjust: none; forced-color-adjust: none; transform-origin: 0 0; z-index: 2; }
.zv-textlayer :is(span, br) { color: transparent; position: absolute; white-space: pre; cursor: text; transform-origin: 0 0; }
.zv-textlayer span.markedContent { top: 0; height: 0; }
.zv-textlayer ::selection { background: rgba(124, 109, 242, 0.35); }
`;
