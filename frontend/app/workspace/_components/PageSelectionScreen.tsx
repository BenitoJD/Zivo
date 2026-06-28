"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Box,
  Button,
  Center,
  Group,
  Loader,
  NumberInput,
  RangeSlider,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconArrowRight, IconCheck, IconFileText, IconPoint } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { bucketPdfThumbWidth, pdfPageAspectRatio, renderPdfThumbToCanvas } from "@/lib/pdf";
import {
  SELECTION_PAD_X,
  SELECTION_PAD_Y,
  SELECTION_PAD_X_COMPACT,
  SELECTION_PAD_Y_COMPACT,
  THUMB_GAP,
  THUMB_GAP_COMPACT,
  THUMB_MIN_WIDTH,
  THUMB_MIN_WIDTH_COMPACT,
  THUMB_MAX_COLS,
  THUMB_MAX_COLS_COMPACT,
  THUMB_FRAME_ASPECT,
  THUMB_FRAME_ASPECT_COMPACT,
  STUDY_COMPACT_BP,
  computeGridLayout,
  shellBleedPx,
  formatSelectionSummary,
} from "@/app/workspace/_components/studyLayout";

/**
 * Page-selection screen (extracted from the workspace page monolith): the
 * pre-study picker where the learner chooses which pages to study. The redesign
 * is a clean three-band layout — header, scrollable thumbnail grid, and a solid
 * in-flow action bar — that adapts from phones to desktops via flex-wrap and
 * container-measured columns (no fragile breakpoint math). Shared as
 * PageSelectionBody by the in-session "change study range" overlay, which
 * supplies its own header.
 */
export function PageSelectionScreen({
  subtitle,
  pageCount,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  pdfLoading,
  pdfError,
  thumbCanvasRefs,
  confirming,
  setupError,
  confirmLabel,
  isCompact,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  subtitle: string;
  pageCount: number;
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  pdfLoading: boolean;
  pdfError: string | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  isCompact: boolean;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const bleed = shellBleedPx(isCompact);
  const padX = isCompact ? SELECTION_PAD_X_COMPACT : SELECTION_PAD_X;

  return (
    <Box
      flex={1}
      mih={0}
      h="100%"
      bg="var(--mantine-color-body)"
      my={isCompact ? "calc(-1 * var(--mantine-spacing-xs))" : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
        ...(bleed > 0
          ? {
              margin: -bleed,
              width: `calc(100% + ${bleed * 2}px)`,
              height: `calc(100% + ${bleed * 2}px)`,
            }
          : {}),
        boxSizing: "border-box",
      }}
    >
      <SelectionHeader
        subtitle={subtitle}
        pageCount={pageCount}
        pdfLoading={pdfLoading}
        isCompact={isCompact}
        padX={padX}
      />

      {pdfError && (
        <Text c="terracotta.7" size="sm" px={padX} pt={4}>
          {pdfError}
        </Text>
      )}
      {!isPdf && !pdfLoading && (
        <Text c="dimmed" size="sm" px={padX} pt={4}>
          PDF thumbnails load automatically. Use the slider below ({pageCount} pages).
        </Text>
      )}

      <Box flex={1} mih={0} style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageSelectionBody
          padX={padX}
          pageCount={pageCount}
          sliderFrom={sliderFrom}
          sliderTo={sliderTo}
          sliderMarks={sliderMarks}
          selectedPages={selectedPages}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbCanvasRefs={thumbCanvasRefs}
          confirming={confirming}
          setupError={setupError}
          confirmLabel={confirmLabel}
          onRangeChange={onRangeChange}
          onPageToggle={onPageToggle}
          onSelectAll={onSelectAll}
          onClearAll={onClearAll}
          onConfirm={onConfirm}
        />
      </Box>
    </Box>
  );
}

/** Title band for the full-screen picker (the overlay brings its own header). */
function SelectionHeader({
  subtitle,
  pageCount,
  pdfLoading,
  isCompact,
  padX,
}: {
  subtitle: string;
  pageCount: number;
  pdfLoading?: boolean;
  isCompact: boolean;
  padX: number;
}) {
  return (
    <Box px={padX} pt={isCompact ? "sm" : "lg"} pb={isCompact ? 4 : "xs"} style={{ flexShrink: 0 }}>
      {(subtitle || pdfLoading) && (
        <Group gap={8} wrap="nowrap" mb={4}>
          {subtitle && (
            <Text size="xs" tt="uppercase" fw={700} c="dimmed" lineClamp={1} style={{ letterSpacing: "0.08em" }}>
              {subtitle}
            </Text>
          )}
          {pdfLoading && <Loader size="xs" color="lavender" />}
        </Group>
      )}
      <Title
        order={2}
        fz={isCompact ? 22 : 28}
        fw={500}
        style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em", lineHeight: 1.15 }}
      >
        Choose pages to study
      </Title>
      <Text size="sm" c="dimmed" mt={2} lh={1.5}>
        Tap a page or drag the range — {pageCount} {pageCount === 1 ? "page" : "pages"} in this source.
      </Text>
    </Box>
  );
}

export function PageSelectionBody({
  padX,
  pageCount,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  confirming,
  setupError,
  confirmLabel,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  padX: number;
  pageCount: number;
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);

  return (
    <Box
      pos="relative"
      flex={1}
      mih={0}
      h="100%"
      style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      <QuickPresets
        pageCount={pageCount}
        selectedPages={selectedPages}
        padX={padX}
        isCompact={Boolean(isCompact)}
        onSelectAll={onSelectAll}
        onClearAll={onClearAll}
        onRangeChange={onRangeChange}
      />

      <Box
        flex={1}
        mih={0}
        px={padX}
        style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
      >
        <PageThumbnailGrid
          pageCount={pageCount}
          selectedPages={selectedPages}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbCanvasRefs={thumbCanvasRefs}
          onPageToggle={onPageToggle}
        />
      </Box>

      <SelectionActionBar
        padX={padX}
        isCompact={Boolean(isCompact)}
        sliderFrom={sliderFrom}
        sliderTo={sliderTo}
        pageCount={pageCount}
        sliderMarks={sliderMarks}
        selectedPages={selectedPages}
        isDark={isDark}
        confirming={confirming}
        setupError={setupError}
        confirmLabel={confirmLabel}
        onRangeChange={onRangeChange}
        onConfirm={onConfirm}
      />
    </Box>
  );
}

/** Fast common selections — far quicker than nudging the slider, especially on phones. */
function QuickPresets({
  pageCount,
  selectedPages,
  padX,
  isCompact,
  onSelectAll,
  onClearAll,
  onRangeChange,
}: {
  pageCount: number;
  selectedPages: number[];
  padX: number;
  isCompact: boolean;
  onSelectAll: () => void;
  onClearAll: () => void;
  onRangeChange: (from: number, to: number) => void;
}) {
  const hasSelection = selectedPages.length > 0;
  const allSelected = selectedPages.length === pageCount && pageCount > 0;
  const isRange = (from: number, to: number) =>
    selectedPages.length === to - from + 1 && selectedPages[0] === from && selectedPages[selectedPages.length - 1] === to;

  const chip = (label: string, onClick: () => void, active: boolean, disabled = false) => (
    <Button
      variant={active ? "light" : "default"}
      color="lavender"
      size="compact-sm"
      radius="xl"
      onClick={onClick}
      disabled={disabled}
      styles={{ root: { fontWeight: 600, flexShrink: 0 } }}
    >
      {label}
    </Button>
  );

  return (
    <Group gap={8} px={padX} pt={isCompact ? 6 : 10} pb={isCompact ? 2 : 4} wrap="wrap" style={{ flexShrink: 0 }}>
      {chip("All pages", onSelectAll, allSelected)}
      {pageCount > 10 && chip("First 10", () => onRangeChange(1, Math.min(10, pageCount)), isRange(1, Math.min(10, pageCount)))}
      {pageCount > 10 &&
        chip(
          "Last 10",
          () => onRangeChange(Math.max(1, pageCount - 9), pageCount),
          isRange(Math.max(1, pageCount - 9), pageCount),
        )}
      {chip("Clear", onClearAll, false, !hasSelection)}
    </Group>
  );
}

/** Solid, always-reachable footer: range controls + selection summary + the primary CTA. */
function SelectionActionBar({
  padX,
  isCompact,
  sliderFrom,
  sliderTo,
  pageCount,
  sliderMarks,
  selectedPages,
  isDark,
  confirming,
  setupError,
  confirmLabel,
  onRangeChange,
  onConfirm,
}: {
  padX: number;
  isCompact: boolean;
  sliderFrom: number;
  sliderTo: number;
  pageCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onRangeChange: (from: number, to: number) => void;
  onConfirm: () => void;
}) {
  const hasSelection = selectedPages.length > 0;
  const summary = formatSelectionSummary(selectedPages, pageCount);
  const hairline = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const barBg = isDark ? "var(--mantine-color-dark-7)" : "var(--mantine-color-gray-0)";
  const trackBg = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-3)";

  const dockMarks = useMemo(
    () =>
      isCompact
        ? [{ value: 1, label: "1" }, ...(pageCount > 1 ? [{ value: pageCount, label: String(pageCount) }] : [])]
        : sliderMarks,
    [isCompact, sliderMarks, pageCount],
  );

  const numberInputStyles = {
    input: {
      // Pin to the theme-aware text token (NOT a hardcoded palette stop): the
      // app remaps both `dark` and `gray` to one warm NEUTRAL ramp, so e.g.
      // gray-1 is near-white in BOTH schemes — hardcoding it left the page
      // number invisible on the dark action bar. Let the default themed input
      // supply the background; --mantine-color-text always contrasts it.
      color: "var(--mantine-color-text)",
      minHeight: 30,
      height: 30,
      width: 52,
      fontSize: 13,
      fontWeight: 600,
      paddingInline: 6,
      textAlign: "center" as const,
    },
  } as const;

  const sliderStyles = {
    root: { paddingTop: 0, paddingBottom: isCompact ? 16 : 20, overflow: "visible" },
    track: { backgroundColor: trackBg, height: 4, borderRadius: 4 },
    bar: {
      backgroundColor: isDark ? "var(--mantine-color-lavender-5)" : "var(--mantine-color-lavender-6)",
      borderRadius: 4,
    },
    thumb: {
      backgroundColor: isDark ? "var(--mantine-color-gray-0)" : "var(--mantine-color-white)",
      borderColor: isDark ? "var(--mantine-color-lavender-4)" : "var(--mantine-color-lavender-6)",
      borderWidth: 2,
      boxShadow: isDark
        ? "0 1px 4px rgba(0, 0, 0, 0.45)"
        : "0 1px 4px rgba(0, 0, 0, 0.18)",
    },
    mark: { width: 3, height: 3, borderWidth: 0, backgroundColor: isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-4)", opacity: 0.7 },
    markLabel: {
      color: isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-6)",
      marginTop: 4,
      fontSize: isCompact ? 9 : 10,
      fontWeight: 500,
    },
  } as const;

  return (
    <Box
      style={{
        flexShrink: 0,
        borderTop: `1px solid ${hairline}`,
        background: barBg,
        paddingBottom: isCompact ? "max(10px, env(safe-area-inset-bottom))" : undefined,
      }}
    >
      <Stack gap={isCompact ? 8 : 10} px={padX} pt={isCompact ? 10 : 14} pb={isCompact ? 8 : 14}>
        <Group gap="sm" wrap="nowrap" align="center">
          <NumberInput
            hideControls
            size="xs"
            radius="md"
            min={1}
            max={pageCount}
            value={sliderFrom}
            aria-label="Start page"
            onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
            styles={numberInputStyles}
          />
          <Box flex={1} miw={80} style={{ minWidth: 0 }}>
            <RangeSlider
              color="lavender"
              min={1}
              max={pageCount}
              minRange={1}
              step={1}
              value={[sliderFrom, sliderTo]}
              onChange={([from, to]) => onRangeChange(from, to)}
              marks={dockMarks}
              label={(v) => `Page ${v}`}
              thumbSize={isCompact ? 16 : 20}
              thumbFromLabel="Start page"
              thumbToLabel="End page"
              thumbValueText={(v) => `Page ${v}`}
              restrictToMarks={!isCompact && pageCount <= 12}
              styles={sliderStyles}
            />
          </Box>
          <NumberInput
            hideControls
            size="xs"
            radius="md"
            min={sliderFrom}
            max={pageCount}
            value={sliderTo}
            aria-label="End page"
            onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
            styles={numberInputStyles}
          />
        </Group>

        <Group justify="space-between" gap="sm" wrap="wrap" align="center">
          <Text
            fw={600}
            size="sm"
            c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
            style={{ flex: "1 1 auto", minWidth: 0 }}
            lineClamp={1}
          >
            {summary}
          </Text>
          <Button
            size={isCompact ? "sm" : "md"}
            radius="xl"
            variant="filled"
            color="lavender"
            px={isCompact ? "lg" : "xl"}
            fw={600}
            fullWidth={isCompact}
            rightSection={<IconArrowRight size={isCompact ? 15 : 18} stroke={2.25} />}
            onClick={onConfirm}
            loading={confirming}
            disabled={!hasSelection}
            style={{ flex: isCompact ? "1 1 100%" : "0 0 auto", whiteSpace: "nowrap" }}
          >
            {confirmLabel}
          </Button>
        </Group>

        {setupError && (
          <Text size="xs" c="terracotta.7" ta={isCompact ? "center" : undefined}>
            {setupError}
          </Text>
        )}
      </Stack>
    </Box>
  );
}

function PageThumbnailGrid({
  pageCount,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  dockReserve = 0,
  onPageToggle,
}: {
  pageCount: number;
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  dockReserve?: number;
  onPageToggle: (page: number, shiftKey: boolean) => void;
}) {
  const selectedSet = useMemo(() => new Set(selectedPages), [selectedPages]);
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [gridWidth, setGridWidth] = useState(0);
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);

  const compactGrid = isCompact || (gridWidth > 0 && gridWidth < 520);
  const gap = compactGrid ? THUMB_GAP_COMPACT : THUMB_GAP;
  const thumbMinWidth = compactGrid ? THUMB_MIN_WIDTH_COMPACT : THUMB_MIN_WIDTH;
  const maxCols = compactGrid ? THUMB_MAX_COLS_COMPACT : THUMB_MAX_COLS;
  const { cols, thumbWidth } = computeGridLayout(gridWidth, thumbMinWidth, gap, maxCols);
  const renderThumbWidth =
    compactGrid && gridWidth > 0
      ? Math.floor((gridWidth - gap * Math.max(0, cols - 1)) / cols)
      : thumbWidth;
  const stableThumbWidth = bucketPdfThumbWidth(renderThumbWidth);
  const cellThumbWidth =
    compactGrid && renderThumbWidth > 0 ? renderThumbWidth : stableThumbWidth;
  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    const id = "zivo-page-scroll-style";
    if (document.getElementById(id)) return;
    const el = document.createElement("style");
    el.id = id;
    el.textContent = "[data-zivo-page-scroll]::-webkit-scrollbar{display:none;width:0;height:0}";
    document.head.appendChild(el);
  }, []);

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const update = () => setGridWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index + 1),
    [pageCount],
  );

  const gridContent = (
    <Box
      w="100%"
      pb={(compactGrid ? SELECTION_PAD_Y_COMPACT : SELECTION_PAD_Y) + dockReserve}
      style={{
        display: "grid",
        gridTemplateColumns: compactGrid
          ? `repeat(${cols}, minmax(0, 1fr))`
          : `repeat(${cols}, ${thumbWidth}px)`,
        justifyContent: compactGrid ? "stretch" : "center",
        gap,
        boxSizing: "border-box",
      }}
    >
      {pageNumbers.map((page) => (
        <PageThumbnailCell
          key={page}
          page={page}
          selected={selectedSet.has(page)}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbWidth={cellThumbWidth}
          compact={compactGrid}
          scrollRoot={scrollRoot}
          thumbCanvasRefs={thumbCanvasRefs}
          onToggle={onPageToggle}
        />
      ))}
    </Box>
  );

  const bindScrollContainer = useCallback((node: HTMLDivElement | null) => {
    setScrollRoot(node);
  }, []);

  return (
    <Box
      ref={outerRef}
      flex={1}
      mih={0}
      w="100%"
      style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      <Box
        ref={bindScrollContainer}
        data-zivo-page-scroll
        flex={1}
        mih={0}
        pt={compactGrid ? 6 : 8}
        style={{
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          WebkitOverflowScrolling: "touch",
          overscrollBehavior: "contain",
          scrollbarWidth: "none",
          msOverflowStyle: "none",
        }}
      >
        {gridContent}
      </Box>
    </Box>
  );
}

function PageThumbnailCell({
  page,
  selected,
  isDark,
  isPdf,
  pdfDoc,
  thumbWidth,
  compact,
  scrollRoot,
  thumbCanvasRefs,
  onToggle,
}: {
  page: number;
  selected: boolean;
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbWidth: number;
  compact?: boolean;
  scrollRoot: HTMLDivElement | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  onToggle: (page: number, shiftKey: boolean) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const [nearViewport, setNearViewport] = useState(false);
  const [renderedSize, setRenderedSize] = useState({ width: 0, height: 0 });
  const [pageAspect, setPageAspect] = useState(
    compact ? THUMB_FRAME_ASPECT_COMPACT : THUMB_FRAME_ASPECT,
  );
  const cellRef = useRef<HTMLButtonElement>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frameWidth, setFrameWidth] = useState(compact ? 0 : thumbWidth);
  const ringColor = "var(--mantine-color-lavender-6)";
  const idleRing = "var(--mantine-color-default-border)";
  const ringWidth = selected ? 3 : 1;
  const innerRadius = compact ? 12 : 14;
  const outerRadius = innerRadius + ringWidth;
  const renderWidth = compact ? (frameWidth > 0 ? frameWidth : thumbWidth) : thumbWidth;
  const frameHeight = Math.round(renderWidth * pageAspect);
  const rendered =
    renderedSize.width === renderWidth &&
    renderedSize.height === frameHeight &&
    renderWidth > 0;

  useEffect(() => {
    if (!compact) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- async PDF page-aspect load — inherently an effect
      setFrameWidth(thumbWidth);
      return;
    }
    const el = frameRef.current;
    if (!el) return;
    const update = () => setFrameWidth(Math.round(el.clientWidth));
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, [compact, thumbWidth]);

  useEffect(() => {
    if (!pdfDoc || !isPdf) return;
    let cancelled = false;
    void (async () => {
      const aspect = await pdfPageAspectRatio(pdfDoc, page);
      if (!cancelled) setPageAspect(aspect);
    })();
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, isPdf, page]);

  // eslint-disable-next-line react-hooks/immutability -- register into a ref-held canvas registry (not React-owned state) inside an effect
  useEffect(() => {
    const canvas = canvasRef.current;
    const registry = thumbCanvasRefs.current;
    // eslint-disable-next-line react-hooks/immutability -- clean up the ref-held canvas registry on unmount
    registry[page] = canvas;
    return () => {
      if (registry[page] === canvas) {
        delete registry[page];
      }
    };
  }, [page, thumbCanvasRefs]);

  useEffect(() => {
    const target = cellRef.current;
    if (!target || !scrollRoot) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        setNearViewport(entry.isIntersecting);
      },
      {
        root: scrollRoot,
        rootMargin: compact ? "360px 0px" : "280px 0px",
        threshold: 0.01,
      },
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, [scrollRoot, compact, page]);

  useEffect(() => {
    if (!nearViewport || !pdfDoc || !isPdf || renderWidth < 1) return;

    let cancelled = false;

    void (async () => {
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      try {
        await renderPdfThumbToCanvas(pdfDoc, page, canvas, renderWidth, frameHeight);
        if (!cancelled) setRenderedSize({ width: renderWidth, height: frameHeight });
      } catch {
        if (!cancelled) return;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nearViewport, pdfDoc, isPdf, page, renderWidth, frameHeight]);

  return (
    <UnstyledButton
      ref={cellRef}
      onClick={(event) => onToggle(page, event.shiftKey)}
      aria-label={`Page ${page}${selected ? ", selected" : ""}`}
      aria-pressed={selected}
      w="100%"
      pb={compact ? 4 : 8}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        transition: "transform 140ms ease",
        transform: hovered && !selected ? "translateY(-2px)" : undefined,
      }}
    >
      <Box pos="relative" w="100%">
        <Box
          p={ringWidth}
          bg={selected ? ringColor : idleRing}
          style={{
            borderRadius: outerRadius,
            overflow: "hidden",
            lineHeight: 0,
            transition: "background-color 160ms ease",
          }}
        >
          <Box
            bg={isDark ? "dark.7" : "gray.0"}
            w="100%"
            style={{
              borderRadius: innerRadius,
              overflow: "hidden",
              lineHeight: 0,
              opacity: selected ? 1 : 0.94,
              transition: "opacity 160ms ease",
            }}
          >
            {isPdf ? (
              <Box
                ref={frameRef}
                pos="relative"
                w="100%"
                h={frameHeight}
                style={{
                  borderRadius: innerRadius,
                  overflow: "hidden",
                }}
              >
                {!rendered && (
                  <Center
                    pos="absolute"
                    inset={0}
                    bg={isDark ? "dark.6" : "gray.1"}
                  >
                    <Loader size="xs" color="gray" />
                  </Center>
                )}
                <canvas
                  ref={canvasRef}
                  style={{
                    display: "block",
                    verticalAlign: "top",
                    opacity: rendered ? 1 : 0,
                    transition: "opacity 180ms ease",
                  }}
                />
              </Box>
            ) : (
              <Center h={frameHeight} w="100%">
                <IconFileText size={40} stroke={1.25} color="var(--mantine-color-dimmed)" />
              </Center>
            )}
          </Box>
        </Box>
        <Text
          size={compact ? "sm" : "md"}
          ta="center"
          mt={compact ? 8 : 12}
          fw={selected ? 700 : 500}
          c={selected ? "lavender.7" : "dimmed"}
          lh={1}
        >
          {page}
        </Text>
        {selected && (
          <ThemeIcon
            pos="absolute"
            top={ringWidth + 6}
            right={ringWidth + 6}
            size={32}
            radius="xl"
            color="lavender"
            variant="filled"
            style={{ boxShadow: "0 3px 12px rgba(0,0,0,0.18)" }}
          >
            <IconCheck size={18} stroke={3} />
          </ThemeIcon>
        )}
      </Box>
    </UnstyledButton>
  );
}

function pageSliderPoint(): ReactNode {
  return (
    <Box mt={6}>
      <IconPoint size={10} stroke={1.5} />
    </Box>
  );
}

export function buildPageSliderMarks(pageCount: number): { value: number; label?: ReactNode }[] {
  if (pageCount <= 1) {
    return [{ value: 1, label: "1" }];
  }

  if (pageCount <= 12) {
    return Array.from({ length: pageCount }, (_, i) => ({
      value: i + 1,
      label: String(i + 1),
    }));
  }

  const segments = 8;
  const ticks: number[] = [1];
  for (let i = 1; i < segments; i += 1) {
    const v = Math.round(1 + (i / segments) * (pageCount - 1));
    if (ticks[ticks.length - 1] !== v) {
      ticks.push(v);
    }
  }
  if (ticks[ticks.length - 1] !== pageCount) {
    ticks.push(pageCount);
  }

  const marks: { value: number; label?: ReactNode }[] = [];
  for (let i = 0; i < ticks.length; i += 1) {
    marks.push({ value: ticks[i], label: String(ticks[i]) });
    if (i < ticks.length - 1) {
      marks.push({ value: (ticks[i] + ticks[i + 1]) / 2, label: pageSliderPoint() });
    }
  }

  return marks;
}
