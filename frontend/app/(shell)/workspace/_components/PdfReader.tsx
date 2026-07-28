"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Loader,
  NumberInput,
  Paper,
  Popover,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import {
  IconBulb,
  IconChevronLeft,
  IconChevronRight,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMessage2,
  IconNotebook,
  IconPlus,
  IconMinus,
  IconWorld,
  IconWand,
} from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  pdfPageAspectRatio,
  renderPdfPageToCanvas,
  renderPdfTextLayer,
  renderPdfThumbToCanvas,
} from "@/lib/pdf";
import { apiGet } from "@/lib/api/client";
import { DictionaryBtn } from "@/app/workspace/_components/SelectionQuote";

// Below this width the rail would crowd the page; collapse to reclaim space (mobile).
const THUMB_RAIL_HIDE_BP = "(max-width: 47.99em)";
const THUMB_TILE_WIDTH = 116; // css px of the thumbnail canvas
const THUMB_RAIL_WIDTH = THUMB_TILE_WIDTH + 32; // tile + horizontal padding/gutter

const ZOOM_MIN = 1;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.25;
const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));

/**
 * Read mode - a focused reader for the source with a real, selectable text layer.
 * Select any passage and a popover lets you quote it to the study buddy, ask it to
 * explain/simplify, or save it as a note. PDFs render visually (canvas + pdf.js text
 * layer); non-PDF sources render their extracted text (also selectable).
 *
 * For PDFs we also show a Chrome-style thumbnail rail on the left: a scrollable column
 * of page tiles. Clicking a tile jumps the reader to that page; scrolling the reader
 * keeps the matching tile highlighted and in view.
 */
export function PdfReader({
  artifactId,
  pdfDoc,
  isPdf,
  pageCount,
  onQuote,
  onAsk,
  onWikipedia,
  onSaveQuote,
}: {
  artifactId: string;
  pdfDoc: PDFDocumentProxy | null;
  isPdf: boolean;
  pageCount: number;
  onQuote: (text: string) => void;
  onAsk: (message: string) => void;
  onWikipedia?: (text: string) => void;
  onSaveQuote: (text: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pagesWrapRef = useRef<HTMLDivElement>(null);
  const [fitWidth, setFitWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [sel, setSel] = useState<{ text: string; x: number; top: number; bottom: number } | null>(null);
  const [activePage, setActivePage] = useState(1);

  // The crisp render width: the fit-to-pane width scaled by the zoom factor. Pages
  // re-render to canvas at this width, so zoomed text stays sharp (not a blurry
  // upscale). When zoomed past the pane, the reader scrolls horizontally too.
  const renderWidth = Math.round((fitWidth || 0) * zoom);
  const zoomable = isPdf && Boolean(pdfDoc);

  const showRail = Boolean(isPdf && pdfDoc && pageCount > 0);
  const isNarrow = useMediaQuery(THUMB_RAIL_HIDE_BP, false, { getInitialValueInEffect: true });
  // User-driven collapse; on narrow screens the rail is forced closed.
  const [railOpen, setRailOpen] = useState(true);
  const railVisible = showRail && railOpen && !isNarrow;

  // PDFs fill the pane (the user wants the page to use the whole space); the readable
  // column for extracted text stays narrower for line length.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setFitWidth(Math.min(el.clientWidth - 24, 1100));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // --- Touch zoom: pinch + double-tap -------------------------------------
  // Live pinch scales the pages wrapper with a CSS transform (cheap, smooth),
  // then commits to `zoom` on release so the canvas re-renders crisp.
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchStart = useRef<{ dist: number; zoom: number } | null>(null);
  const pinchHappened = useRef(false);
  const liveTargetZoom = useRef(1);
  const lastTap = useRef<{ t: number; x: number; y: number } | null>(null);

  const applyLiveScale = useCallback((scale: number) => {
    const el = pagesWrapRef.current;
    if (!el) return;
    el.style.transformOrigin = "top center";
    el.style.transform = scale === 1 ? "" : `scale(${scale})`;
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!zoomable || e.pointerType !== "touch") return;
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.current.size === 2) {
        const [a, b] = [...pointers.current.values()];
        pinchStart.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom };
        liveTargetZoom.current = zoom;
        pinchHappened.current = true;
      }
    },
    [zoomable, zoom],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!pointers.current.has(e.pointerId)) return;
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const start = pinchStart.current;
      if (pointers.current.size === 2 && start && start.dist > 0) {
        const [a, b] = [...pointers.current.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const target = clampZoom(start.zoom * (dist / start.dist));
        liveTargetZoom.current = target;
        applyLiveScale(target / zoom);
        e.preventDefault();
      }
    },
    [applyLiveScale, zoom],
  );

  const endPointer = useCallback(
    (e: React.PointerEvent) => {
      const hadTwo = pointers.current.size === 2;
      pointers.current.delete(e.pointerId);
      // Commit the pinch when the gesture drops below two fingers.
      if (pinchStart.current && hadTwo) {
        const target = liveTargetZoom.current;
        pinchStart.current = null;
        applyLiveScale(1);
        if (Math.abs(target - zoom) > 0.01) setZoom(Number(target.toFixed(2)));
        return;
      }
      if (pointers.current.size > 0) return;
      // All fingers up. A pinch consumes the gesture - never treat its final
      // lift as a tap.
      const wasPinch = pinchHappened.current;
      pinchHappened.current = false;
      if (e.pointerType === "touch" && !wasPinch) {
        const now = Date.now();
        const prev = lastTap.current;
        if (prev && now - prev.t < 300 && Math.hypot(e.clientX - prev.x, e.clientY - prev.y) < 30) {
          setZoom((z) => (z > 1 ? 1 : 2));
          lastTap.current = null;
        } else {
          lastTap.current = { t: now, x: e.clientX, y: e.clientY };
        }
      }
    },
    [applyLiveScale, zoom],
  );

  const zoomBy = useCallback((delta: number) => setZoom((z) => clampZoom(Number((z + delta).toFixed(2)))), []);

  // Show the quote popover for the current selection. Shared by mouse-up (desktop)
  // and touch-end (mobile long-press selection), so the Ask/Explain/Save actions
  // are reachable with a finger, not just a cursor.
  const showSelection = useCallback(() => {
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
    setSel({ text, x: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom });
  }, []);

  const onMouseUp = useCallback(() => showSelection(), [showSelection]);
  const onTouchEnd = useCallback(() => {
    if (pinchStart.current) return; // don't pop during a pinch gesture
    // Let the browser settle the selection handles before measuring it.
    window.setTimeout(showSelection, 16);
  }, [showSelection]);

  useEffect(() => {
    const onDown = (e: MouseEvent | TouchEvent) => {
      // Keep the popover if the interaction is on it.
      const t = e.target as HTMLElement;
      if (t.closest?.("[data-reader-popover]")) return;
      setSel(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown, { passive: true });
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, []);

  const act = (fn: () => void) => {
    fn();
    setSel(null);
    window.getSelection()?.removeAllRanges();
  };

  // Track which page is currently centered in the reader so the rail can highlight it.
  // An observer per visible page sets activePage to the one most in view.
  useEffect(() => {
    if (!showRail) return;
    const root = scrollRef.current;
    if (!root) return;
    const ratios = new Map<number, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const n = Number((e.target as HTMLElement).dataset.pdfPage);
          if (!n) continue;
          ratios.set(n, e.isIntersecting ? e.intersectionRatio : 0);
        }
        let best = 0;
        let bestRatio = 0;
        for (const [n, r] of ratios) {
          if (r > bestRatio) {
            bestRatio = r;
            best = n;
          }
        }
        if (best > 0) setActivePage(best);
      },
      { root, threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    const tiles = root.querySelectorAll<HTMLElement>("[data-pdf-page]");
    tiles.forEach((t) => io.observe(t));
    return () => io.disconnect();
  }, [showRail, pageCount, renderWidth, pdfDoc]);

  const jumpToPage = useCallback((n: number) => {
    const root = scrollRef.current;
    if (!root) return;
    const target = root.querySelector<HTMLElement>(`#pdf-page-${n}`);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    setActivePage(n);
  }, []);

  return (
    <Box pos="relative" h="100%" style={{ display: "flex", minHeight: 0 }}>
      <style>{TEXT_LAYER_CSS}</style>
      <style>{THUMB_RAIL_CSS}</style>

      {railVisible && pdfDoc ? (
        <ThumbnailRail
          pdfDoc={pdfDoc}
          pageCount={pageCount}
          activePage={activePage}
          onJump={jumpToPage}
          onCollapse={() => setRailOpen(false)}
        />
      ) : null}

      {/* Floating expand handle when the rail is collapsed (desktop/tablet only). */}
      {showRail && !railVisible && !isNarrow ? (
        <Tooltip label="Show page thumbnails" position="right" withArrow>
          <ActionIcon
            variant="default"
            radius="md"
            size="lg"
            onClick={() => setRailOpen(true)}
            aria-label="Show page thumbnails"
            style={{ position: "absolute", top: 12, left: 12, zIndex: 5 }}
          >
            <IconLayoutSidebarLeftExpand size={18} stroke={1.6} />
          </ActionIcon>
        </Tooltip>
      ) : null}

      <Box
        ref={scrollRef}
        data-reader-scroll
        onMouseUp={onMouseUp}
        onTouchEnd={onTouchEnd}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        flex={1}
        mih={0}
        px="sm"
        py="md"
        style={{
          overflowY: "auto",
          // When zoomed past the pane, allow horizontal panning to reach the edges.
          overflowX: zoom > 1 ? "auto" : "hidden",
          overscrollBehavior: "contain",
          // Let one finger pan/scroll while we handle two-finger pinch in JS
          // (so pinch zooms the page, not the whole app chrome).
          touchAction: zoomable ? "pan-x pan-y" : undefined,
        }}
      >
        <Box
          ref={pagesWrapRef}
          w={isPdf ? renderWidth || "100%" : undefined}
          maw={isPdf ? undefined : 760}
          mx="auto"
        >
          {isPdf && pdfDoc ? (
            Array.from({ length: pageCount }).map((_, i) => (
              <PdfPage key={i + 1} pdfDoc={pdfDoc} pageNumber={i + 1} width={renderWidth} />
            ))
          ) : (
            <TextReader artifactId={artifactId} />
          )}
        </Box>
      </Box>

      {/* Zoom controls - handy on desktop, essential on touch (pair with pinch /
          double-tap). Sit out of the way at the bottom-right of the reader. */}
      {zoomable && fitWidth > 0 ? (
        <Paper
          withBorder
          radius="xl"
          shadow="sm"
          p={4}
          style={{
            position: "absolute",
            bottom: 14,
            right: 14,
            zIndex: 6,
            display: "flex",
            alignItems: "center",
            gap: 2,
            // gray-0 is the "lifted paper / card" surface in BOTH schemes (#FFFFFF
            // light, #22211F dark). It was `--mantine-color-body`, which in dark mode
            // is #1A1917 - the exact page background - so the control disappeared.
            background: "var(--mantine-color-gray-0)",
          }}
        >
          <Tooltip label="Zoom out" withArrow openDelay={400}>
            <ActionIcon
              variant="subtle"
              color="gray"
              radius="xl"
              size="lg"
              onClick={() => zoomBy(-ZOOM_STEP)}
              disabled={zoom <= ZOOM_MIN}
              aria-label="Zoom out"
              // The gray scale is inverted for dark mode (high = light text), and
              // `variant="subtle"` resolves to a LOW shade - i.e. dark glyphs on ink.
              // gray.8 is the readable text tone in both schemes.
              c="gray.8"
            >
              <IconMinus size={18} stroke={2} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Reset zoom" withArrow openDelay={400}>
            <Button
              variant="subtle"
              color="gray"
              size="compact-sm"
              radius="xl"
              onClick={() => setZoom(1)}
              disabled={zoom === 1}
              aria-label="Reset zoom to 100%"
              c="gray.8"
              style={{ minWidth: 52, fontVariantNumeric: "tabular-nums" }}
            >
              {Math.round(zoom * 100)}%
            </Button>
          </Tooltip>
          <Tooltip label="Zoom in" withArrow openDelay={400}>
            <ActionIcon
              variant="subtle"
              color="gray"
              radius="xl"
              size="lg"
              onClick={() => zoomBy(ZOOM_STEP)}
              disabled={zoom >= ZOOM_MAX}
              aria-label="Zoom in"
              c="gray.8"
            >
              <IconPlus size={18} stroke={2} />
            </ActionIcon>
          </Tooltip>
        </Paper>
      ) : null}

      {sel ? (
        // Place above the selection, but flip below when it's too near the top
        // edge to fit (common on phones when selecting the first lines).
        (() => {
          const popH = 52;
          const above = sel.top - popH - 8;
          const below = sel.bottom + 8;
          const top = above >= 12 ? above : below;
          const popW = isNarrow ? Math.min(window.innerWidth - 24, 360) : onWikipedia ? 520 : 420;
          return (
            <Paper
              data-reader-popover
              withBorder
              radius="xl"
              shadow="md"
              p={4}
              style={{
                position: "fixed",
                left: Math.max(12, Math.min(sel.x - popW / 2, window.innerWidth - popW - 12)),
                top: Math.min(top, window.innerHeight - popH - 12),
                zIndex: 400,
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "center",
                maxWidth: "calc(100vw - 24px)",
                gap: 2,
                background: "var(--mantine-color-body)",
              }}
            >
              <PopBtn icon={<IconMessage2 size={16} />} label="Ask" large={isNarrow} onClick={() => act(() => onQuote(sel.text))} />
              <PopBtn icon={<IconBulb size={16} />} label="Explain" large={isNarrow} onClick={() => act(() => onAsk(`Explain this passage in simple terms:\n\n"${sel.text}"`))} />
              <DictionaryBtn key={sel.text} text={sel.text} />
              {onWikipedia ? (
                <PopBtn
                  icon={<IconWorld size={16} />}
                  label="Wikipedia"
                  large={isNarrow}
                  onClick={() => act(() => onWikipedia(sel.text))}
                />
              ) : null}
              <PopBtn icon={<IconWand size={16} />} label="Simplify" large={isNarrow} onClick={() => act(() => onAsk(`Simplify this so it's easy to understand:\n\n"${sel.text}"`))} />
              <PopBtn icon={<IconNotebook size={16} />} label="Save" large={isNarrow} onClick={() => act(() => onSaveQuote(sel.text))} />
            </Paper>
          );
        })()
      ) : null}

      {/* Mobile page nav: the thumbnail rail is hidden on phones, so a compact
          floating pill restores "where am I / jump to page N". */}
      {showRail && isNarrow ? (
        <MobilePageNav activePage={activePage} pageCount={pageCount} onJump={jumpToPage} />
      ) : null}
    </Box>
  );
}

/** Floating page indicator + jumper for phones (no thumbnail rail there). */
function MobilePageNav({
  activePage,
  pageCount,
  onJump,
}: {
  activePage: number;
  pageCount: number;
  onJump: (n: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<number | string>(activePage);

  const toggle = () => {
    setValue(activePage); // seed the input with the current page each time it opens
    setOpen((o) => !o);
  };

  const go = (n: number) => {
    const clamped = Math.min(pageCount, Math.max(1, Math.round(n)));
    onJump(clamped);
    setOpen(false);
  };

  return (
    <Paper
      withBorder
      radius="xl"
      shadow="sm"
      p={2}
      style={{
        position: "absolute",
        bottom: 14,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 6,
        display: "flex",
        alignItems: "center",
        gap: 2,
        background: "var(--mantine-color-body)",
      }}
    >
      <ActionIcon
        variant="subtle"
        color="gray"
        radius="xl"
        size="lg"
        onClick={() => go(activePage - 1)}
        disabled={activePage <= 1}
        aria-label="Previous page"
      >
        <IconChevronLeft size={18} stroke={2} />
      </ActionIcon>
      <Popover opened={open} onChange={setOpen} position="top" withArrow shadow="md" radius="md" trapFocus>
        <Popover.Target>
          <Button
            variant="subtle"
            color="gray"
            size="compact-sm"
            radius="xl"
            onClick={toggle}
            aria-label={`Page ${activePage} of ${pageCount}. Tap to jump.`}
            style={{ fontVariantNumeric: "tabular-nums", minWidth: 64 }}
          >
            {activePage} / {pageCount}
          </Button>
        </Popover.Target>
        <Popover.Dropdown p="xs">
          <Group gap={6} wrap="nowrap" align="flex-end">
            <NumberInput
              label="Go to page"
              size="xs"
              min={1}
              max={pageCount}
              value={value}
              onChange={setValue}
              onKeyDown={(e) => e.key === "Enter" && go(Number(value))}
              hideControls
              w={92}
              autoFocus
            />
            <Button size="xs" radius="md" color="lavender" onClick={() => go(Number(value))}>
              Go
            </Button>
          </Group>
        </Popover.Dropdown>
      </Popover>
      <ActionIcon
        variant="subtle"
        color="gray"
        radius="xl"
        size="lg"
        onClick={() => go(activePage + 1)}
        disabled={activePage >= pageCount}
        aria-label="Next page"
      >
        <IconChevronRight size={18} stroke={2} />
      </ActionIcon>
    </Paper>
  );
}

/** Chrome-style left rail of page thumbnails. Lazy-renders tiles as they scroll in. */
function ThumbnailRail({
  pdfDoc,
  pageCount,
  activePage,
  onJump,
  onCollapse,
}: {
  pdfDoc: PDFDocumentProxy;
  pageCount: number;
  activePage: number;
  onJump: (n: number) => void;
  onCollapse: () => void;
}) {
  const activeTileRef = useRef<HTMLButtonElement>(null);
  const pages = useMemo(() => Array.from({ length: pageCount }, (_, i) => i + 1), [pageCount]);

  // Keep the active tile visible as the reader scrolls (but don't fight the user's
  // own scrolling of the rail - nearest avoids large jumps).
  useEffect(() => {
    const tile = activeTileRef.current;
    if (!tile) return;
    tile.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activePage]);

  return (
    <Box
      className="zv-thumb-rail"
      style={{
        width: THUMB_RAIL_WIDTH,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        borderRight: "1px solid var(--mantine-color-default-border)",
        background: "var(--mantine-color-body)",
      }}
    >
      <Box
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 10px 6px 12px",
          flexShrink: 0,
        }}
      >
        <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.2} style={{ fontSize: 10 }}>
          Pages
        </Text>
        <Tooltip label="Hide thumbnails" position="right" withArrow openDelay={300}>
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            onClick={onCollapse}
            aria-label="Hide thumbnails"
          >
            <IconLayoutSidebarLeftCollapse size={16} stroke={1.6} />
          </ActionIcon>
        </Tooltip>
      </Box>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          overscrollBehavior: "contain",
          padding: "4px 8px 16px",
        }}
      >
        <Stack gap={10} align="center">
          {pages.map((n) => (
            <ThumbTile
              key={n}
              ref={n === activePage ? activeTileRef : undefined}
              pdfDoc={pdfDoc}
              pageNumber={n}
              active={n === activePage}
              onClick={() => onJump(n)}
            />
          ))}
        </Stack>
      </Box>
    </Box>
  );
}

function ThumbTile({
  ref,
  pdfDoc,
  pageNumber,
  active,
  onClick,
}: {
  ref?: React.Ref<HTMLButtonElement>;
  pdfDoc: PDFDocumentProxy;
  pageNumber: number;
  active: boolean;
  onClick: () => void;
}) {
  const wrapRef = useRef<HTMLButtonElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [aspect, setAspect] = useState(1.414);
  const [visible, setVisible] = useState(false);
  const rendered = useRef(false);

  // Merge the internal ref (IntersectionObserver) with the optional forwarded ref
  // (active scroll-into-view), so the rail can keep the active tile in view.
  const setNode = useCallback(
    (node: HTMLButtonElement | null) => {
      wrapRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) (ref as React.RefObject<HTMLButtonElement | null>).current = node;
    },
    [ref],
  );

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
      { rootMargin: "400px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const tileH = Math.round(THUMB_TILE_WIDTH * aspect);

  useEffect(() => {
    if (!visible || rendered.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    rendered.current = true;
    void renderPdfThumbToCanvas(pdfDoc, pageNumber, canvas, THUMB_TILE_WIDTH, tileH).catch(() => {
      rendered.current = false; // allow a retry on a later pass
    });
  }, [visible, pdfDoc, pageNumber, tileH]);

  return (
    <button
      ref={setNode}
      type="button"
      onClick={onClick}
      className="zv-thumb-tile"
      data-active={active || undefined}
      aria-label={`Go to page ${pageNumber}`}
      aria-current={active ? "true" : undefined}
    >
      <span
        className="zv-thumb-frame"
        style={{ width: THUMB_TILE_WIDTH, height: tileH }}
      >
        <canvas ref={canvasRef} style={{ display: "block" }} />
        {!visible ? <span className="zv-thumb-skeleton" /> : null}
      </span>
      <span className="zv-thumb-num">{pageNumber}</span>
    </button>
  );
}

function PopBtn({
  icon,
  label,
  large = false,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  large?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      size={large ? "sm" : "compact-xs"}
      variant="subtle"
      color="lavender"
      leftSection={icon}
      radius="xl"
      onClick={onClick}
      // Comfortable thumb target on touch (~44px) without bloating the cursor UI.
      styles={large ? { root: { height: 44, paddingInline: 12 } } : undefined}
    >
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
        /* render race - ignore */
      }
    })();
  }, [visible, width, pdfDoc, pageNumber]);

  const height = width > 0 ? width * aspect : 0;
  return (
    <Box
      ref={wrapRef}
      id={`pdf-page-${pageNumber}`}
      data-pdf-page={pageNumber}
      mb="md"
      style={{
        position: "relative",
        width: width || "100%",
        height: height || undefined,
        minHeight: height ? undefined : 200,
        margin: "0 auto 16px",
        scrollMarginTop: 8,
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
/* Force every glyph fully transparent so only the crisp canvas shows - selecting
   then highlights cleanly instead of revealing a second, blurry copy of the text. */
.zv-textlayer, .zv-textlayer * { color: transparent !important; }
.zv-textlayer :is(span, br) { position: absolute; white-space: pre; cursor: text; transform-origin: 0 0; }
.zv-textlayer span.markedContent { top: 0; height: 0; }
.zv-textlayer ::selection { background: rgba(124, 109, 242, 0.38); color: transparent; }
`;

const THUMB_RAIL_CSS = `
.zv-thumb-tile {
  appearance: none;
  border: none;
  background: transparent;
  padding: 4px;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  border-radius: 12px;
  transition: background 140ms ease, transform 140ms ease;
}
.zv-thumb-tile:hover { background: var(--mantine-color-default-hover); }
.zv-thumb-tile:focus-visible {
  outline: 2px solid var(--mantine-color-lavender-5);
  outline-offset: 1px;
}
.zv-thumb-tile:hover .zv-thumb-frame { border-color: var(--mantine-color-lavender-3); }
.zv-thumb-tile[data-active] { transform: translateY(-1px); }
.zv-thumb-frame {
  position: relative;
  display: block;
  overflow: hidden;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid var(--mantine-color-default-border);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  transition: border-color 140ms ease, box-shadow 140ms ease;
}
.zv-thumb-tile[data-active] .zv-thumb-frame {
  border-color: var(--mantine-color-lavender-5);
  box-shadow: 0 0 0 2px var(--mantine-color-lavender-3), 0 4px 12px rgba(124, 109, 242, 0.18);
}
.zv-thumb-skeleton {
  position: absolute;
  inset: 0;
  background: linear-gradient(110deg, var(--mantine-color-gray-1) 30%, var(--mantine-color-gray-0) 50%, var(--mantine-color-gray-1) 70%);
  background-size: 200% 100%;
  animation: zv-thumb-shimmer 1.4s ease-in-out infinite;
}
@keyframes zv-thumb-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.zv-thumb-num {
  font-size: 11px;
  font-weight: 600;
  color: var(--mantine-color-dimmed);
  font-variant-numeric: tabular-nums;
  line-height: 1;
  transition: color 140ms ease;
}
.zv-thumb-tile[data-active] .zv-thumb-num { color: var(--mantine-color-lavender-7); }
[data-mantine-color-scheme="dark"] .zv-thumb-frame { background: var(--mantine-color-dark-6); }
@media (prefers-reduced-motion: reduce) {
  .zv-thumb-tile, .zv-thumb-frame, .zv-thumb-num { transition: none !important; }
  .zv-thumb-skeleton { animation: none !important; }
}
`;
