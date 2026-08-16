// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Box, Button, Center, Group, Loader, NumberInput, RangeSlider, Stack, Text, ThemeIcon, Title, UnstyledButton, } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconArrowRight, IconCheck, IconFileText, IconPoint } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { bucketPdfThumbWidth, pdfPageAspectRatio, renderPdfThumbToCanvas } from "@/lib/pdf";
import { SELECTION_PAD_X, SELECTION_PAD_Y, SELECTION_PAD_X_COMPACT, SELECTION_PAD_Y_COMPACT, THUMB_GAP, THUMB_GAP_COMPACT, THUMB_MIN_WIDTH, THUMB_MIN_WIDTH_COMPACT, THUMB_MAX_COLS, THUMB_MAX_COLS_COMPACT, THUMB_FRAME_ASPECT, THUMB_FRAME_ASPECT_COMPACT, STUDY_COMPACT_BP, computeGridLayout, shellBleedPx, formatSelectionSummary, } from "@/app/workspace/_components/studyLayout";
/**
 * Page-selection screen (extracted from the workspace page monolith): the
 * pre-study picker where the learner chooses which pages to study. The redesign
 * is a clean three-band layout - header, scrollable thumbnail grid, and a solid
 * in-flow action bar - that adapts from phones to desktops via flex-wrap and
 * container-measured columns (no fragile breakpoint math). Shared as
 * PageSelectionBody by the in-session "change study range" overlay, which
 * supplies its own header.
 */
export function PageSelectionScreen({ subtitle, pageCount, sliderFrom, sliderTo, sliderMarks, selectedPages, isDark, isPdf, pdfDoc, pdfLoading, pdfError, pageTexts, pageTextsLoading, thumbCanvasRefs, confirming, confirmingMode, setupError, isCompact, onRangeChange, onPageToggle, onSelectAll, onClearAll, onConfirmNow, onPrepInBackground, showBackgroundPrep = true, }: {
    subtitle: string;
    pageCount: number;
    sliderFrom: number;
    sliderTo: number;
    sliderMarks: {
        value: number;
        label?: ReactNode;
    }[];
    selectedPages: number[];
    isDark: boolean;
    isPdf: boolean;
    pdfDoc: PDFDocumentProxy | null;
    pdfLoading: boolean;
    pdfError: string | null;
    /** Non-PDF study-page text (DOCX/PPTX/paste) for thumbnail previews. */
    pageTexts?: Record<number, string>;
    pageTextsLoading?: boolean;
    thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
    confirming: boolean;
    confirmingMode: "now" | "background" | null;
    setupError: string | null;
    isCompact: boolean;
    onRangeChange: (from: number, to: number) => void;
    onPageToggle: (page: number, shiftKey: boolean) => void;
    onSelectAll: () => void;
    onClearAll: () => void;
    onConfirmNow: () => void;
    onPrepInBackground: () => void;
    showBackgroundPrep?: boolean;
}) {
    const bleed = shellBleedPx(isCompact);
    const padX = choose(Boolean(isCompact), SELECTION_PAD_X_COMPACT, SELECTION_PAD_X);
    return (<Box flex={1} mih={0} h="100%" bg="var(--mantine-color-body)" my={choose(Boolean(isCompact), "calc(-1 * var(--mantine-spacing-xs))", undefined)} style={{
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
            ...(choose(Boolean(bleed > 0), {
                margin: -bleed,
                width: `calc(100% + ${bleed * 2}px)`,
                height: `calc(100% + ${bleed * 2}px)`,
            }, {})),
            boxSizing: "border-box",
        }}>
      <SelectionHeader subtitle={subtitle} pageCount={pageCount} pdfLoading={pdfLoading} isCompact={isCompact} padX={padX}/>

      {pick(Boolean(pdfError), () => (<Text c="terracotta.7" size="sm" px={padX} pt={4}>
          {pdfError}
        </Text>), () => pdfError)}
      {pick(Boolean(!isPdf), () => pick(Boolean(!pdfLoading), () => (<Text c="dimmed" size="sm" px={padX} pt={4}>
          {choose(Boolean(pageCount === 1), "1 study page in this source. Tap to select.", `${pageCount} study pages — tap a page or drag the range.`)}
        </Text>), () => !pdfLoading), () => !isPdf)}

      <Box flex={1} mih={0} style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageSelectionBody padX={padX} pageCount={pageCount} sliderFrom={sliderFrom} sliderTo={sliderTo} sliderMarks={sliderMarks} selectedPages={selectedPages} isDark={isDark} isPdf={isPdf} pdfDoc={pdfDoc} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} thumbCanvasRefs={thumbCanvasRefs} confirming={confirming} confirmingMode={confirmingMode} setupError={setupError} onRangeChange={onRangeChange} onPageToggle={onPageToggle} onSelectAll={onSelectAll} onClearAll={onClearAll} onConfirmNow={onConfirmNow} onPrepInBackground={onPrepInBackground} showBackgroundPrep={showBackgroundPrep}/>
      </Box>
    </Box>);
}
/** Title band for the full-screen picker (the overlay brings its own header). */
function SelectionHeader({ subtitle, pageCount, pdfLoading, isCompact, padX, }: {
    subtitle: string;
    pageCount: number;
    pdfLoading?: boolean;
    isCompact: boolean;
    padX: number;
}) {
    return (<Box px={padX} pt={choose(Boolean(isCompact), "sm", "lg")} pb={choose(Boolean(isCompact), 4, "xs")} style={{ flexShrink: 0 }}>
      {pick(Boolean((subtitle || pdfLoading)), () => (<Group gap={8} wrap="nowrap" mb={4}>
          {pick(Boolean(subtitle), () => (<Text size="xs" tt="uppercase" fw={700} c="dimmed" lineClamp={1} style={{ letterSpacing: "0.08em" }}>
              {subtitle}
            </Text>), () => subtitle)}
          {pick(Boolean(pdfLoading), () => <Loader size="xs" color="lavender"/>, () => pdfLoading)}
        </Group>), () => (subtitle || pdfLoading))}
      <Title order={2} fz={choose(Boolean(isCompact), 22, 28)} fw={500} style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em", lineHeight: 1.15 }}>
        Choose pages to study
      </Title>
      <Text size="sm" c="dimmed" mt={2} lh={1.5}>
        Tap a page or drag the range - {pageCount} {choose(Boolean(pageCount === 1), "page", "pages")} in this source.
      </Text>
    </Box>);
}
export function PageSelectionBody({ padX, pageCount, sliderFrom, sliderTo, sliderMarks, selectedPages, isDark, isPdf, pdfDoc, pageTexts, pageTextsLoading, thumbCanvasRefs, confirming, confirmingMode, setupError, onRangeChange, onPageToggle, onSelectAll, onClearAll, onConfirmNow, onPrepInBackground, showBackgroundPrep = true, }: {
    padX: number;
    pageCount: number;
    sliderFrom: number;
    sliderTo: number;
    sliderMarks: {
        value: number;
        label?: ReactNode;
    }[];
    selectedPages: number[];
    isDark: boolean;
    isPdf: boolean;
    pdfDoc: PDFDocumentProxy | null;
    pageTexts?: Record<number, string>;
    pageTextsLoading?: boolean;
    thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
    confirming: boolean;
    confirmingMode: "now" | "background" | null;
    setupError: string | null;
    onRangeChange: (from: number, to: number) => void;
    onPageToggle: (page: number, shiftKey: boolean) => void;
    onSelectAll: () => void;
    onClearAll: () => void;
    onConfirmNow: () => void;
    onPrepInBackground: () => void;
    showBackgroundPrep?: boolean;
}) {
    const isCompact = useMediaQuery(STUDY_COMPACT_BP);
    return (<Box pos="relative" flex={1} mih={0} h="100%" style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <QuickPresets pageCount={pageCount} selectedPages={selectedPages} padX={padX} isCompact={Boolean(isCompact)} onSelectAll={onSelectAll} onClearAll={onClearAll} onRangeChange={onRangeChange}/>

      <Box flex={1} mih={0} px={padX} style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageThumbnailGrid pageCount={pageCount} selectedPages={selectedPages} isDark={isDark} isPdf={isPdf} pdfDoc={pdfDoc} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} thumbCanvasRefs={thumbCanvasRefs} onPageToggle={onPageToggle}/>
      </Box>

      <SelectionActionBar padX={padX} isCompact={Boolean(isCompact)} sliderFrom={sliderFrom} sliderTo={sliderTo} pageCount={pageCount} sliderMarks={sliderMarks} selectedPages={selectedPages} isDark={isDark} confirming={confirming} confirmingMode={confirmingMode} setupError={setupError} onRangeChange={onRangeChange} onConfirmNow={onConfirmNow} onPrepInBackground={onPrepInBackground} showBackgroundPrep={showBackgroundPrep}/>
    </Box>);
}
/** Fast common selections - far quicker than nudging the slider, especially on phones. */
function QuickPresets({ pageCount, selectedPages, padX, isCompact, onSelectAll, onClearAll, onRangeChange, }: {
    pageCount: number;
    selectedPages: number[];
    padX: number;
    isCompact: boolean;
    onSelectAll: () => void;
    onClearAll: () => void;
    onRangeChange: (from: number, to: number) => void;
}) {
    const hasSelection = selectedPages.length > 0;
    const allSelected = pick(Boolean(selectedPages.length === pageCount), () => pageCount > 0, () => selectedPages.length === pageCount);
    const isRange = (from: number, to: number) => pick(Boolean(selectedPages.length === to - from + 1), () => pick(Boolean(selectedPages[0] === from), () => selectedPages[selectedPages.length - 1] === to, () => selectedPages[0] === from), () => selectedPages.length === to - from + 1);
    const chip = (label: string, onClick: () => void, active: boolean, disabled = false) => (<Button variant={choose(Boolean(active), "light", "default")} color="lavender" size="compact-sm" radius="xl" onClick={onClick} disabled={disabled} styles={{ root: { fontWeight: 600, flexShrink: 0 } }}>
      {label}
    </Button>);
    return (<Group gap={8} px={padX} pt={choose(Boolean(isCompact), 6, 10)} pb={choose(Boolean(isCompact), 2, 4)} wrap="wrap" style={{ flexShrink: 0 }}>
      {chip("All pages", onSelectAll, allSelected)}
      {pick(Boolean(pageCount > 10), () => chip("First 10", () => onRangeChange(1, Math.min(10, pageCount)), isRange(1, Math.min(10, pageCount))), () => pageCount > 10)}
      {pick(Boolean(pageCount > 10), () => chip("Last 10", () => onRangeChange(Math.max(1, pageCount - 9), pageCount), isRange(Math.max(1, pageCount - 9), pageCount)), () => pageCount > 10)}
      {chip("Clear", onClearAll, false, !hasSelection)}
    </Group>);
}
/** Solid, always-reachable footer: range controls + selection summary + the primary CTA. */
function SelectionActionBar({ padX, isCompact, sliderFrom, sliderTo, pageCount, sliderMarks, selectedPages, isDark, confirming, confirmingMode, setupError, onRangeChange, onConfirmNow, onPrepInBackground, showBackgroundPrep = true, }: {
    padX: number;
    isCompact: boolean;
    sliderFrom: number;
    sliderTo: number;
    pageCount: number;
    sliderMarks: {
        value: number;
        label?: ReactNode;
    }[];
    selectedPages: number[];
    isDark: boolean;
    confirming: boolean;
    confirmingMode: "now" | "background" | null;
    setupError: string | null;
    onRangeChange: (from: number, to: number) => void;
    onConfirmNow: () => void;
    onPrepInBackground: () => void;
    showBackgroundPrep?: boolean;
}) {
    const hasSelection = selectedPages.length > 0;
    const summary = formatSelectionSummary(selectedPages, pageCount);
    const hairline = choose(Boolean(isDark), "var(--mantine-color-dark-4)", "var(--mantine-color-gray-3)");
    const barBg = choose(Boolean(isDark), "var(--mantine-color-dark-7)", "var(--mantine-color-gray-0)");
    const trackBg = choose(Boolean(isDark), "var(--mantine-color-dark-3)", "var(--mantine-color-gray-3)");
    const dockMarks = useMemo(() => pick(Boolean(isCompact), () => [{ value: 1, label: "1" }, ...(pick(Boolean(pageCount > 1), () => [{ value: pageCount, label: String(pageCount) }], () => []))], () => sliderMarks), [isCompact, sliderMarks, pageCount]);
    const numberInputStyles = {
        input: {
            // Pin to the theme-aware text token (NOT a hardcoded palette stop): the
            // app remaps both `dark` and `gray` to one warm NEUTRAL ramp, so e.g.
            // gray-1 is near-white in BOTH schemes - hardcoding it left the page
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
        root: { paddingTop: 0, paddingBottom: choose(Boolean(isCompact), 16, 20), overflow: "visible" },
        track: { backgroundColor: trackBg, height: 4, borderRadius: 4 },
        bar: {
            backgroundColor: choose(Boolean(isDark), "var(--mantine-color-lavender-5)", "var(--mantine-color-lavender-6)"),
            borderRadius: 4,
        },
        thumb: {
            backgroundColor: choose(Boolean(isDark), "var(--mantine-color-gray-0)", "var(--mantine-color-white)"),
            borderColor: choose(Boolean(isDark), "var(--mantine-color-lavender-4)", "var(--mantine-color-lavender-6)"),
            borderWidth: 2,
            boxShadow: choose(Boolean(isDark), "0 1px 4px rgba(0, 0, 0, 0.45)", "0 1px 4px rgba(0, 0, 0, 0.18)"),
        },
        mark: { width: 3, height: 3, borderWidth: 0, backgroundColor: choose(Boolean(isDark), "var(--mantine-color-gray-5)", "var(--mantine-color-gray-4)"), opacity: 0.7 },
        markLabel: {
            color: choose(Boolean(isDark), "var(--mantine-color-gray-5)", "var(--mantine-color-gray-6)"),
            marginTop: 4,
            fontSize: choose(Boolean(isCompact), 9, 10),
            fontWeight: 500,
        },
    } as const;
    return (<Box style={{
            flexShrink: 0,
            borderTop: `1px solid ${hairline}`,
            background: barBg,
            paddingBottom: choose(Boolean(isCompact), "max(10px, env(safe-area-inset-bottom))", undefined),
        }}>
      <Stack gap={choose(Boolean(isCompact), 8, 10)} px={padX} pt={choose(Boolean(isCompact), 10, 14)} pb={choose(Boolean(isCompact), 8, 14)}>
        <Group gap="sm" wrap="nowrap" align="center">
          <NumberInput hideControls size="xs" radius="md" min={1} max={pageCount} value={sliderFrom} aria-label="Start page" onChange={(v) => onRangeChange(choose(Boolean(typeof v === "number"), v, 1), sliderTo)} styles={numberInputStyles}/>
          <Box flex={1} miw={80} style={{ minWidth: 0 }}>
            <RangeSlider color="lavender" min={1} max={pageCount} minRange={1} step={1} value={[sliderFrom, sliderTo]} onChange={([from, to]) => onRangeChange(from, to)} marks={dockMarks} label={(v) => `Page ${v}`} thumbSize={choose(Boolean(isCompact), 16, 20)} thumbFromLabel="Start page" thumbToLabel="End page" thumbValueText={(v) => `Page ${v}`} restrictToMarks={pick(Boolean(!isCompact), () => pageCount <= 12, () => !isCompact)} styles={sliderStyles}/>
          </Box>
          <NumberInput hideControls size="xs" radius="md" min={sliderFrom} max={pageCount} value={sliderTo} aria-label="End page" onChange={(v) => onRangeChange(sliderFrom, choose(Boolean(typeof v === "number"), v, sliderFrom))} styles={numberInputStyles}/>
        </Group>

        <Group justify="space-between" gap="sm" wrap="wrap" align="center">
          <Text fw={600} size="sm" c={choose(Boolean(hasSelection), "var(--mantine-color-text)", "dimmed")} style={{ flex: "1 1 auto", minWidth: 0 }} lineClamp={choose(Boolean(isCompact), 2, 1)} title={summary}>
            {summary}
          </Text>
        <Group gap="sm" wrap="wrap" justify={choose(Boolean(isCompact), "stretch", "flex-end")} align="center" style={{ flex: choose(Boolean(isCompact), "1 1 100%", "0 0 auto") }}>
          <Button size={choose(Boolean(isCompact), "sm", "md")} radius="xl" variant="default" px={choose(Boolean(isCompact), "md", "lg")} fw={600} fullWidth={isCompact} onClick={onPrepInBackground} loading={pick(Boolean(confirming), () => confirmingMode === "background", () => confirming)} disabled={!hasSelection || (pick(Boolean(confirming), () => confirmingMode === "now", () => confirming))} style={{ whiteSpace: "nowrap", display: choose(Boolean(showBackgroundPrep), undefined, "none") }}>
            Prep in background
          </Button>
          <Button size={choose(Boolean(isCompact), "sm", "md")} radius="xl" variant="filled" color="lavender" px={choose(Boolean(isCompact), "lg", "xl")} fw={600} fullWidth={isCompact} rightSection={<IconArrowRight size={choose(Boolean(isCompact), 15, 18)} stroke={2.25}/>} onClick={onConfirmNow} loading={pick(Boolean(confirming), () => confirmingMode === "now", () => confirming)} disabled={!hasSelection || (pick(Boolean(confirming), () => confirmingMode === "background", () => confirming))} style={{ whiteSpace: "nowrap" }}>
            Start learning now
          </Button>
        </Group>
        </Group>

        {pick(Boolean(setupError), () => (<Text size="xs" c="terracotta.7" ta={choose(Boolean(isCompact), "center", undefined)}>
            {setupError}
          </Text>), () => setupError)}
      </Stack>
    </Box>);
}
function PageThumbnailGrid({ pageCount, selectedPages, isDark, isPdf, pdfDoc, pageTexts, pageTextsLoading, thumbCanvasRefs, dockReserve = 0, onPageToggle, }: {
    pageCount: number;
    selectedPages: number[];
    isDark: boolean;
    isPdf: boolean;
    pdfDoc: PDFDocumentProxy | null;
    pageTexts?: Record<number, string>;
    pageTextsLoading?: boolean;
    thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
    dockReserve?: number;
    onPageToggle: (page: number, shiftKey: boolean) => void;
}) {
    const selectedSet = useMemo(() => new Set(selectedPages), [selectedPages]);
    const outerRef = useRef<HTMLDivElement | null>(null);
    const [gridWidth, setGridWidth] = useState(0);
    const isCompact = useMediaQuery(STUDY_COMPACT_BP);
    const compactGrid = isCompact || (pick(Boolean(gridWidth > 0), () => gridWidth < 520, () => gridWidth > 0));
    const gap = choose(Boolean(compactGrid), THUMB_GAP_COMPACT, THUMB_GAP);
    const thumbMinWidth = choose(Boolean(compactGrid), THUMB_MIN_WIDTH_COMPACT, THUMB_MIN_WIDTH);
    const maxCols = choose(Boolean(compactGrid), THUMB_MAX_COLS_COMPACT, THUMB_MAX_COLS);
    const { cols, thumbWidth } = computeGridLayout(gridWidth, thumbMinWidth, gap, maxCols);
    const renderThumbWidth = pick(Boolean(compactGrid && gridWidth > 0), () => Math.floor((gridWidth - gap * Math.max(0, cols - 1)) / cols), () => thumbWidth);
    const stableThumbWidth = bucketPdfThumbWidth(renderThumbWidth);
    const cellThumbWidth = choose(Boolean(compactGrid && renderThumbWidth > 0), renderThumbWidth, stableThumbWidth);
    const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);
    useEffect(() => {
        const id = "zivo-page-scroll-style";
        return pick(Boolean(document.getElementById(id)), () => {
            return;
        }, () => {
            const el = document.createElement("style");
            el.id = id;
            el.textContent = "[data-zivo-page-scroll]::-webkit-scrollbar{display:none;width:0;height:0}";
            document.head.appendChild(el);
        });
    }, []);
    useEffect(() => {
        const el = outerRef.current;
        return pick(Boolean(!el), () => {
            return;
        }, () => {
            const update = () => setGridWidth(el.clientWidth);
            update();
            const ro = new ResizeObserver(() => update());
            ro.observe(el);
            return () => ro.disconnect();
        });
    }, []);
    const pageNumbers = useMemo(() => Array.from({ length: pageCount }, (_, index) => index + 1), [pageCount]);
    const gridContent = (<Box w="100%" pb={(choose(Boolean(compactGrid), SELECTION_PAD_Y_COMPACT, SELECTION_PAD_Y)) + dockReserve} style={{
            display: "grid",
            gridTemplateColumns: choose(Boolean(compactGrid), `repeat(${cols}, minmax(0, 1fr))`, `repeat(${cols}, ${thumbWidth}px)`),
            justifyContent: choose(Boolean(compactGrid), "stretch", "center"),
            gap,
            boxSizing: "border-box",
        }}>
      {pageNumbers.map((page) => (<PageThumbnailCell key={page} page={page} selected={selectedSet.has(page)} isDark={isDark} isPdf={isPdf} pdfDoc={pdfDoc} previewText={pageTexts?.[page]} pageTextsLoading={pageTextsLoading} thumbWidth={cellThumbWidth} compact={compactGrid} scrollRoot={scrollRoot} thumbCanvasRefs={thumbCanvasRefs} onToggle={onPageToggle}/>))}
    </Box>);
    const bindScrollContainer = useCallback((node: HTMLDivElement | null) => {
        setScrollRoot(node);
    }, []);
    return (<Box ref={outerRef} flex={1} mih={0} w="100%" style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <Box ref={bindScrollContainer} data-zivo-page-scroll flex={1} mih={0} pt={choose(Boolean(compactGrid), 6, 8)} style={{
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
            WebkitOverflowScrolling: "touch",
            overscrollBehavior: "contain",
            scrollbarWidth: "none",
            msOverflowStyle: "none",
        }}>
        {gridContent}
      </Box>
    </Box>);
}
function PageThumbnailCell({ page, selected, isDark, isPdf, pdfDoc, previewText, pageTextsLoading, thumbWidth, compact, scrollRoot, thumbCanvasRefs, onToggle, }: {
    page: number;
    selected: boolean;
    isDark: boolean;
    isPdf: boolean;
    pdfDoc: PDFDocumentProxy | null;
    previewText?: string;
    pageTextsLoading?: boolean;
    thumbWidth: number;
    compact?: boolean;
    scrollRoot: HTMLDivElement | null;
    thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
    onToggle: (page: number, shiftKey: boolean) => void;
}) {
    const [hovered, setHovered] = useState(false);
    const [nearViewport, setNearViewport] = useState(false);
    const [renderedSize, setRenderedSize] = useState({ width: 0, height: 0 });
    const [pageAspect, setPageAspect] = useState(choose(Boolean(compact), THUMB_FRAME_ASPECT_COMPACT, THUMB_FRAME_ASPECT));
    const cellRef = useRef<HTMLButtonElement>(null);
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const frameRef = useRef<HTMLDivElement | null>(null);
    const [frameWidth, setFrameWidth] = useState(choose(Boolean(compact), 0, thumbWidth));
    const ringColor = "var(--mantine-color-lavender-6)";
    const idleRing = "var(--mantine-color-default-border)";
    const ringWidth = choose(Boolean(selected), 3, 1);
    const innerRadius = choose(Boolean(compact), 12, 14);
    const outerRadius = innerRadius + ringWidth;
    const renderWidth = choose(Boolean(compact), (choose(Boolean(frameWidth > 0), frameWidth, thumbWidth)), thumbWidth);
    const frameHeight = Math.round(renderWidth * pageAspect);
    const rendered = pick(Boolean(renderedSize.width === renderWidth), () => pick(Boolean(renderedSize.height === frameHeight), () => renderWidth > 0, () => renderedSize.height === frameHeight), () => renderedSize.width === renderWidth);
    useEffect(() => {
        return pick(Boolean(!compact), () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- async PDF page-aspect load - inherently an effect
            setFrameWidth(thumbWidth);
            return;
        }, () => {
            const el = frameRef.current;
            return pick(Boolean(!el), () => {
                return;
            }, () => {
                const update = () => setFrameWidth(Math.round(el.clientWidth));
                update();
                const ro = new ResizeObserver(() => update());
                ro.observe(el);
                return () => ro.disconnect();
            });
        });
    }, [compact, thumbWidth]);
    useEffect(() => {
        return pick(Boolean(!pdfDoc || !isPdf), () => {
            return;
        }, () => {
            let cancelled = false;
            void (async () => {
                const aspect = await pdfPageAspectRatio(pdfDoc, page);
                pick(Boolean(!cancelled), () => {
                    setPageAspect(aspect);
                }, () => {
                });
            })();
            return () => {
                cancelled = true;
            };
        });
    }, [pdfDoc, isPdf, page]);
    // eslint-disable-next-line react-hooks/immutability -- register into a ref-held canvas registry (not React-owned state) inside an effect
    useEffect(() => {
        const canvas = canvasRef.current;
        const registry = thumbCanvasRefs.current;
        // eslint-disable-next-line react-hooks/immutability -- clean up the ref-held canvas registry on unmount
        registry[page] = canvas;
        return () => {
            pick(Boolean(registry[page] === canvas), () => {
                delete registry[page];
            }, () => {
            });
        };
    }, [page, thumbCanvasRefs]);
    useEffect(() => {
        const target = cellRef.current;
        return pick(Boolean(!target || !scrollRoot), () => {
            return;
        }, () => {
            const observer = new IntersectionObserver(([entry]) => {
                setNearViewport(entry.isIntersecting);
            }, {
                root: scrollRoot,
                rootMargin: choose(Boolean(compact), "360px 0px", "280px 0px"),
                threshold: 0.01,
            });
            observer.observe(target);
            return () => observer.disconnect();
        });
    }, [scrollRoot, compact, page]);
    useEffect(() => {
        return pick(Boolean(!nearViewport || !pdfDoc || !isPdf || renderWidth < 1), () => {
            return;
        }, () => {
            let cancelled = false;
            void (async () => {
                const __z1 = { hit: false, val: undefined as any };
                const canvas = canvasRef.current;
                await pick(Boolean(!canvas || cancelled), async () => {
                    __z1.hit = true;
                }, async () => {
                    try {
                        await renderPdfThumbToCanvas(pdfDoc, page, canvas, renderWidth, frameHeight);
                        pick(Boolean(!cancelled), () => {
                            setRenderedSize({ width: renderWidth, height: frameHeight });
                        }, () => {
                        });
                    }
                    catch {
                        pick(Boolean(!cancelled), () => {
                            __z1.hit = true;
                        }, () => {
                        });
                    }
                });
            })();
            return () => {
                cancelled = true;
            };
        });
    }, [nearViewport, pdfDoc, isPdf, page, renderWidth, frameHeight]);
    return (<UnstyledButton ref={cellRef} onClick={(event) => onToggle(page, event.shiftKey)} aria-label={`Page ${page}${choose(Boolean(selected), ", selected", "")}`} aria-pressed={selected} w="100%" pb={choose(Boolean(compact), 4, 8)} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} style={{
            transition: "transform 140ms ease",
            transform: choose(Boolean(hovered && !selected), "translateY(-2px)", undefined),
        }}>
      <Box pos="relative" w="100%">
        <Box p={ringWidth} bg={choose(Boolean(selected), ringColor, idleRing)} style={{
            borderRadius: outerRadius,
            overflow: "hidden",
            lineHeight: 0,
            transition: "background-color 160ms ease",
        }}>
          <Box bg={choose(Boolean(isDark), "dark.7", "gray.0")} w="100%" style={{
            borderRadius: innerRadius,
            overflow: "hidden",
            lineHeight: 0,
            opacity: choose(Boolean(selected), 1, 0.94),
            transition: "opacity 160ms ease",
        }}>
            {pick(Boolean(isPdf), () => (<Box ref={frameRef} pos="relative" w="100%" h={frameHeight} style={{
                borderRadius: innerRadius,
                overflow: "hidden",
            }}>
                {pick(Boolean(!rendered), () => (<Center pos="absolute" inset={0} bg={choose(Boolean(isDark), "dark.6", "gray.1")}>
                    <Loader size="xs" color="gray"/>
                  </Center>), () => !rendered)}
                <canvas ref={canvasRef} style={{
                display: "block",
                verticalAlign: "top",
                opacity: choose(Boolean(rendered), 1, 0),
                transition: "opacity 180ms ease",
            }}/>
              </Box>), () => (<Box ref={frameRef} w="100%" h={frameHeight} px={choose(Boolean(compact), 8, 10)} py={choose(Boolean(compact), 8, 10)} bg={choose(Boolean(isDark), "dark.6", "gray.0")} style={{
                borderRadius: innerRadius,
                overflow: "hidden",
                boxSizing: "border-box",
            }}>
                {pick(Boolean(previewText?.trim()), () => (<Text c={choose(Boolean(isDark), "gray.4", "dark.4")} lh={1.35} style={{
                    fontFamily: "var(--font-serif), Georgia, serif",
                    fontSize: choose(Boolean(compact), 8.5, 9.5),
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    overflow: "hidden",
                    display: "-webkit-box",
                    WebkitLineClamp: choose(Boolean(compact), 14, 18),
                    WebkitBoxOrient: "vertical",
                }}>
                    {previewText.trim().slice(0, 520)}
                  </Text>), () => (<Center h="100%">
                    {choose(Boolean(pageTextsLoading), (<Loader size="xs" color="gray"/>), (<IconFileText size={40} stroke={1.25} color="var(--mantine-color-dimmed)"/>))}
                  </Center>))}
              </Box>))}
          </Box>
        </Box>
        <Text size={choose(Boolean(compact), "sm", "md")} ta="center" mt={choose(Boolean(compact), 8, 12)} fw={choose(Boolean(selected), 700, 500)} c={choose(Boolean(selected), "lavender.7", "dimmed")} lh={1}>
          {page}
        </Text>
        {pick(Boolean(selected), () => (<ThemeIcon pos="absolute" top={ringWidth + 6} right={ringWidth + 6} size={32} radius="xl" color="lavender" variant="filled" style={{ boxShadow: "0 3px 12px rgba(0,0,0,0.18)" }}>
            <IconCheck size={18} stroke={3}/>
          </ThemeIcon>), () => selected)}
      </Box>
    </UnstyledButton>);
}
function pageSliderPoint(): ReactNode {
    return (<Box mt={6}>
      <IconPoint size={10} stroke={1.5}/>
    </Box>);
}
export function buildPageSliderMarks(pageCount: number): {
    value: number;
    label?: ReactNode;
}[] {
    return pick(Boolean(pageCount <= 1), () => [{ value: 1, label: "1" }], () => pick(Boolean(pageCount <= 12), () => Array.from({ length: pageCount }, (_, i) => ({
        value: i + 1,
        label: String(i + 1),
    })), () => {
        const segments = 8;
        const ticks: number[] = [1];
        for (let i = 1; i < segments; i += 1) {
            const v = Math.round(1 + (i / segments) * (pageCount - 1));
            pick(Boolean(ticks[ticks.length - 1] !== v), () => {
                ticks.push(v);
            }, () => {
            });
        }
        pick(Boolean(ticks[ticks.length - 1] !== pageCount), () => {
            ticks.push(pageCount);
        }, () => {
        });
        const marks: {
            value: number;
            label?: ReactNode;
        }[] = [];
        for (let i = 0; i < ticks.length; i += 1) {
            marks.push({ value: ticks[i], label: String(ticks[i]) });
            pick(Boolean(i < ticks.length - 1), () => {
                marks.push({ value: (ticks[i] + ticks[i + 1]) / 2, label: pageSliderPoint() });
            }, () => {
            });
        }
        return marks;
    }));
}
