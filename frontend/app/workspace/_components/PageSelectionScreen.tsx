"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Box,
  Button,
  Center,
  Group,
  Loader,
  NumberInput,
  Paper,
  RangeSlider,
  Stack,
  Text,
  ThemeIcon,
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
  SELECTION_DOCK_WIDTH,
  SELECTION_DOCK_RESERVE,
  SELECTION_DOCK_RESERVE_COMPACT,
  THUMB_GAP,
  THUMB_GAP_COMPACT,
  THUMB_MIN_WIDTH,
  THUMB_MIN_WIDTH_COMPACT,
  THUMB_MAX_COLS,
  THUMB_MAX_COLS_COMPACT,
  THUMB_FRAME_ASPECT,
  THUMB_FRAME_ASPECT_COMPACT,
  STUDY_COMPACT_BP,
  STUDY_OVERLAY_BP,
  computeGridLayout,
  shellBleedPx,
  formatSelectionSummary,
} from "@/app/workspace/_components/studyLayout";

/**
 * Page-selection screen (extracted from the workspace page monolith): the
 * pre-study picker where the learner chooses which pages to study — thumbnail
 * grid, range slider, and the floating action dock. Also reused (PageSelectionBody)
 * by the in-session "change study range" overlay.
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
      {pdfError && (
        <Text c="terracotta.7" size="sm" px="md" pt="xs">
          {pdfError}
        </Text>
      )}
      {!isPdf && !pdfLoading && (
        <Text c="dimmed" size="sm" px="md" pt="xs">
          PDF thumbnails load automatically. Use the slider below ({pageCount} pages).
        </Text>
      )}

      <Box flex={1} mih={0} style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageSelectionBody
          padX={isCompact ? SELECTION_PAD_X_COMPACT : SELECTION_PAD_X}
          subtitle={subtitle}
          pdfLoading={pdfLoading}
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

export function PageSelectionBody({
  padX,
  subtitle,
  pdfLoading,
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
  subtitle?: string;
  pdfLoading?: boolean;
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
  const sliderColor = isDark ? "blue.4" : "blue.6";
  const trackBg = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-3)";

  return (
    <Box
      pos="relative"
      flex={1}
      mih={0}
      h="100%"
      style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
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
          dockReserve={isCompact ? SELECTION_DOCK_RESERVE_COMPACT : SELECTION_DOCK_RESERVE}
          onPageToggle={onPageToggle}
        />
      </Box>
      <PageSelectionDock
        subtitle={subtitle}
        pdfLoading={pdfLoading}
        sliderFrom={sliderFrom}
        sliderTo={sliderTo}
        pageCount={pageCount}
        sliderMarks={sliderMarks}
        selectedPages={selectedPages}
        isDark={isDark}
        sliderColor={sliderColor}
        trackBg={trackBg}
        confirming={confirming}
        setupError={setupError}
        confirmLabel={confirmLabel}
        onRangeChange={onRangeChange}
        onSelectAll={onSelectAll}
        onClearAll={onClearAll}
        onConfirm={onConfirm}
      />
    </Box>
  );
}

function PageSelectionDock({
  subtitle,
  pdfLoading,
  sliderFrom,
  sliderTo,
  pageCount,
  sliderMarks,
  selectedPages,
  isDark,
  sliderColor,
  trackBg,
  confirming,
  setupError,
  confirmLabel,
  onRangeChange,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  subtitle?: string;
  pdfLoading?: boolean;
  sliderFrom: number;
  sliderTo: number;
  pageCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  sliderColor: string;
  trackBg: string;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onRangeChange: (from: number, to: number) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);
  const dockStacked = useMediaQuery(STUDY_OVERLAY_BP, false, { getInitialValueInEffect: true });
  const hasSelection = selectedPages.length > 0;
  const panelBorder = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const hairline = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const secondaryBorder = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-4)";
  const inputBorder = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-4)";
  const inputBg = isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-white)";
  const markColor = isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-5)";
  const markLabelColor = isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-6)";
  const summary = formatSelectionSummary(selectedPages, pageCount);
  const dockMarks = useMemo(
    () =>
      isCompact
        ? [
            { value: 1, label: "1" },
            ...(pageCount > 1 ? [{ value: pageCount, label: String(pageCount) }] : []),
          ]
        : sliderMarks,
    [isCompact, sliderMarks, pageCount],
  );

  const numberInputStyles = {
    input: {
      border: `1px solid ${inputBorder}`,
      backgroundColor: inputBg,
      color: isDark ? "var(--mantine-color-gray-1)" : undefined,
      minHeight: 28,
      height: 28,
      fontSize: 13,
      fontWeight: 500,
      paddingInline: 8,
      textAlign: "center" as const,
    },
  } as const;

  const secondaryButtonStyles = {
    root: {
      border: `1px solid ${secondaryBorder}`,
      backgroundColor: isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-white)",
      color: isDark ? "var(--mantine-color-gray-2)" : undefined,
      fontWeight: 600,
      flexShrink: 0,
      whiteSpace: "nowrap" as const,
    },
  } as const;

  const sliderStyles = {
    root: { paddingTop: 0, paddingBottom: isCompact ? 2 : 16, overflow: "visible" },
    track: { backgroundColor: trackBg, height: isCompact ? 3 : 4, borderRadius: 4 },
    bar: {
      backgroundColor: isDark ? "var(--mantine-color-blue-5)" : "var(--mantine-color-blue-6)",
      borderRadius: 4,
    },
    thumb: {
      backgroundColor: isDark ? "var(--mantine-color-gray-0)" : "var(--mantine-color-white)",
      borderColor: isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-6)",
      borderWidth: 2,
      boxShadow: isDark
        ? "0 1px 4px rgba(0, 0, 0, 0.45), 0 0 0 0.5px rgba(255, 255, 255, 0.08)"
        : "0 1px 4px rgba(0, 0, 0, 0.18), 0 0 0 0.5px rgba(0, 0, 0, 0.04)",
    },
    mark: {
      width: 3,
      height: 3,
      borderWidth: 0,
      backgroundColor: markColor,
      opacity: 0.7,
    },
    markLabel: {
      color: markLabelColor,
      marginTop: 4,
      fontSize: isCompact ? 9 : 10,
      fontWeight: 500,
      letterSpacing: "-0.01em",
    },
  } as const;

  const rangeSlider = (
    <RangeSlider
      color={sliderColor}
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
  );

  const confirmButton = (
    <Button
      size={isCompact ? "sm" : "md"}
      radius="xl"
      variant="filled"
      color="lavender"
      px={isCompact ? "lg" : "xl"}
      fw={600}
      fullWidth={isCompact}
      h={isCompact ? 40 : undefined}
      rightSection={<IconArrowRight size={isCompact ? 15 : 18} stroke={2.25} />}
      onClick={onConfirm}
      loading={confirming}
      disabled={!hasSelection}
      style={{ flexShrink: 0, whiteSpace: "nowrap" }}
    >
      {confirmLabel}
    </Button>
  );

  const selectionActions = (
    <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
      <Button variant="default" size="sm" radius="xl" onClick={onSelectAll} styles={secondaryButtonStyles}>
        All
      </Button>
      <Button
        variant="default"
        size="sm"
        radius="xl"
        onClick={onClearAll}
        disabled={!hasSelection}
        styles={secondaryButtonStyles}
      >
        Clear
      </Button>
    </Group>
  );

  if (isCompact) {
    return (
      <Box
        pos="absolute"
        left={0}
        right={0}
        bottom={0}
        bg={isDark ? "dark.7" : "gray.0"}
        style={{
          zIndex: 2,
          pointerEvents: "none",
          borderTop: `1px solid ${hairline}`,
          paddingBottom: "calc(12px + env(safe-area-inset-bottom))",
          boxShadow: isDark
            ? "0 -10px 32px rgba(0, 0, 0, 0.4)"
            : "0 -6px 20px rgba(0, 0, 0, 0.08)",
        }}
      >
        <Stack gap={8} px="md" pt={10} pb={4} style={{ pointerEvents: "auto" }}>
          {rangeSlider}
          <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
            <Box miw={0} style={{ flex: 1 }}>
              {subtitle && (
                <Group gap={6} wrap="nowrap" mb={2}>
                  <Text
                    size="xs"
                    c="dimmed"
                    lineClamp={1}
                    tt="uppercase"
                    fw={600}
                    style={{ letterSpacing: "0.05em", fontSize: 10 }}
                  >
                    {subtitle}
                  </Text>
                  {pdfLoading && <Loader size="xs" />}
                </Group>
              )}
              <Text
                fw={600}
                size="sm"
                lineClamp={1}
                c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
              >
                {summary}
              </Text>
            </Box>
            <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
              <Button variant="subtle" size="compact-sm" radius="xl" onClick={onSelectAll} px="sm">
                All
              </Button>
              <Button
                variant="subtle"
                size="compact-sm"
                radius="xl"
                onClick={onClearAll}
                disabled={!hasSelection}
                px="sm"
                style={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                Clear
              </Button>
            </Group>
          </Group>
          {confirmButton}
          {setupError && (
            <Text size="xs" c="terracotta.7" ta="center">
              {setupError}
            </Text>
          )}
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      pos="absolute"
      left={0}
      right={0}
      bottom={0}
      w={dockStacked ? "92%" : SELECTION_DOCK_WIDTH}
      mx="auto"
      pb="md"
      pt="xs"
      style={{ zIndex: 2, pointerEvents: "none" }}
    >
      <Paper
        withBorder
        radius="lg"
        w="100%"
        bg={isDark ? "dark.7" : "gray.0"}
        style={{
          pointerEvents: "auto",
          borderColor: panelBorder,
          boxShadow: isDark
            ? "0 -12px 40px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255, 255, 255, 0.04) inset"
            : "0 -8px 32px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(255, 255, 255, 0.6) inset",
        }}
      >
      <Stack gap={0}>
        <Box px={isCompact ? "sm" : "md"} pt="sm" pb={isCompact ? "xs" : 4}>
          {isCompact ? (
            <Stack gap="sm">
              <Group justify="center" gap={8} wrap="nowrap">
                <Text size="xs" c="dimmed" fw={500}>
                  From
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={56}
                  radius="md"
                  min={1}
                  max={pageCount}
                  value={sliderFrom}
                  onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
                  styles={numberInputStyles}
                />
                <Text size="xs" c="dimmed" fw={500}>
                  To
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={56}
                  radius="md"
                  min={sliderFrom}
                  max={pageCount}
                  value={sliderTo}
                  onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
                  styles={numberInputStyles}
                />
              </Group>
              {rangeSlider}
            </Stack>
          ) : (
            <Group align="center" gap="md" wrap="nowrap" w="100%">
              <Group gap={6} align="center" wrap="nowrap" style={{ flexShrink: 0 }}>
                <Text size="xs" c="dimmed" fw={500}>
                  From
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={52}
                  radius="md"
                  min={1}
                  max={pageCount}
                  value={sliderFrom}
                  onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
                  styles={numberInputStyles}
                />
                <Text size="xs" c="dimmed" fw={500}>
                  To
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={52}
                  radius="md"
                  min={sliderFrom}
                  max={pageCount}
                  value={sliderTo}
                  onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
                  styles={numberInputStyles}
                />
              </Group>
              <Box flex={1} miw={120} style={{ minWidth: 0 }}>
                {rangeSlider}
              </Box>
            </Group>
          )}
        </Box>

        <Box h={1} bg={hairline} />

        <Box px={isCompact ? "sm" : "md"} py={isCompact ? "xs" : "sm"}>
          <Stack gap={isCompact ? "sm" : "xs"}>
            {isCompact ? (
              <Stack gap={6} align="center">
                {subtitle && (
                  <Group gap="xs" wrap="nowrap" justify="center">
                    <Text
                      size="xs"
                      c="dimmed"
                      ta="center"
                      lineClamp={2}
                      tt="uppercase"
                      fw={600}
                      style={{ letterSpacing: "0.04em" }}
                    >
                      {subtitle}
                    </Text>
                    {pdfLoading && <Loader size="xs" />}
                  </Group>
                )}
                <Text
                  fw={600}
                  size="sm"
                  ta="center"
                  lineClamp={2}
                  c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                >
                  {summary}
                </Text>
                <Text size="xs" c="dimmed" ta="center">
                  Tap a page · Shift+tap to extend
                </Text>
                <Group grow gap="xs" w="100%">
                  <Button variant="default" size="sm" radius="xl" onClick={onSelectAll} styles={secondaryButtonStyles}>
                    All
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    radius="xl"
                    onClick={onClearAll}
                    disabled={!hasSelection}
                    styles={secondaryButtonStyles}
                  >
                    Clear
                  </Button>
                </Group>
                {confirmButton}
              </Stack>
            ) : dockStacked ? (
              <Stack gap="sm">
                <Group align="flex-start" justify="space-between" gap="md" wrap="nowrap" w="100%">
                  <Box miw={0} style={{ flex: 1 }}>
                    {subtitle && (
                      <Group gap="xs" wrap="nowrap" align="center">
                        <Text
                          size="xs"
                          c="dimmed"
                          lineClamp={2}
                          tt="uppercase"
                          fw={600}
                          style={{ letterSpacing: "0.04em" }}
                        >
                          {subtitle}
                        </Text>
                        {pdfLoading && <Loader size="xs" />}
                      </Group>
                    )}
                  </Box>
                  <Stack gap={2} align="flex-end" miw={0} style={{ flex: 1.2 }}>
                    <Text
                      fw={600}
                      size="sm"
                      ta="right"
                      lineClamp={2}
                      c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                    >
                      {summary}
                    </Text>
                    <Text size="xs" c="dimmed" ta="right" lineClamp={1}>
                      Tap a page · Shift+tap to extend
                    </Text>
                  </Stack>
                </Group>
                <Group gap="xs" wrap="nowrap" justify="flex-end" w="100%">
                  {selectionActions}
                  {confirmButton}
                </Group>
              </Stack>
            ) : (
              <Group align="center" wrap="nowrap" gap="lg" w="100%" justify="space-between">
                <Box miw={0} style={{ flex: 1, overflow: "hidden" }}>
                  {subtitle && (
                    <Group gap="xs" wrap="nowrap" align="center">
                      <Text
                        size="xs"
                        c="dimmed"
                        lineClamp={1}
                        tt="uppercase"
                        fw={600}
                        style={{ letterSpacing: "0.04em" }}
                      >
                        {subtitle}
                      </Text>
                      {pdfLoading && <Loader size="xs" />}
                    </Group>
                  )}
                </Box>

                <Stack gap={2} align="center" miw={0} style={{ flex: 1.2, overflow: "hidden" }}>
                  <Text
                    fw={600}
                    size="sm"
                    ta="center"
                    lineClamp={1}
                    c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                  >
                    {summary}
                  </Text>
                  <Text size="xs" c="dimmed" ta="center" lineClamp={1}>
                    Tap a page · Shift+tap to extend
                  </Text>
                </Stack>

                <Group gap="xs" wrap="nowrap" justify="flex-end" style={{ flexShrink: 0 }}>
                  {selectionActions}
                  {confirmButton}
                </Group>
              </Group>
            )}
            {setupError && (
              <Text size="xs" c="terracotta.7" ta={isCompact ? "center" : undefined}>
                {setupError}
              </Text>
            )}
          </Stack>
        </Box>
      </Stack>
    </Paper>
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
