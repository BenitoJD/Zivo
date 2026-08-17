// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import type { ReactNode } from "react";
import { ActionIcon, Badge, Box, Button, Center, Group, Paper, Progress, SimpleGrid, Stack, Text, ThemeIcon, Title, } from "@mantine/core";
import { IconCheck, IconClipboardList, IconHistory, IconX } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";
import { SELECTION_PAD_X, SELECTION_PAD_X_COMPACT, type AnsweredCard, } from "@/app/workspace/_components/studyLayout";
import { type StudyReport } from "@/lib/api/queries";
import { shortTopicName } from "@/lib/shortTopicName";
import { PageSelectionBody } from "@/app/workspace/_components/PageSelectionScreen";
/**
 * End-of-study report card: first-try correct vs. to-revisit, plus per-topic
 * strengths and the topics to focus on next. Driven entirely by the answered
 * history (first-attempt correctness, so Learn-mode retries don't hide weak spots).
 */
export function StudyReportCard({ answered, report, showSummary = true, compact, }: {
    answered: AnsweredCard[];
    report?: StudyReport | null;
    showSummary?: boolean;
    compact?: boolean;
}) {
    // Prefer the persistent server report (aggregated from immutable measurements,
    // so it survives reloads); fall back to the in-session history before it loads.
    const useServer = pick(Boolean(report != null), () => report.total > 0, () => report != null);
    let topics: {
        name: string;
        correct: number;
        total: number;
    }[];
    pick(Boolean(useServer), () => {
        topics = report.topics.map((t) => ({
            name: shortTopicName(t.concept),
            correct: t.correct,
            total: t.total,
        }));
    }, () => {
        const byConcept = new Map<string, {
            name: string;
            correct: number;
            total: number;
        }>();
        for (const a of answered) {
            const name = shortTopicName((a.concept || "").trim() || "General");
            const t = byConcept.get(name) ??
        { name, correct: 0, total: 0 };
            t.total += 1;
            pick(Boolean(a.firstTryCorrect), () => {
                t.correct += 1;
            }, () => {
            });
            byConcept.set(name, t);
        }
        topics = [...byConcept.values()];
    });
    const total = choose(Boolean(useServer), report.total, answered.length);
    return pick(Boolean(total === 0), () => null, () => {
        const correct = pick(Boolean(useServer), () => report.correct, () => answered.filter((a) => a.firstTryCorrect).length);
        const wrong = total - correct;
        const weak = topics
            .filter((t) => t.correct < t.total)
            .sort((a, b) => a.correct / a.total - b.correct / b.total);
        const strong = topics.filter((t) => t.correct === t.total);
        const accuracy = Math.round((correct / total) * 100);
        return (<Paper withBorder radius="xl" p={choose(Boolean(compact), "md", "xl")} w="100%" bg="var(--mantine-color-body)" shadow="paper">
      <Stack gap={choose(Boolean(compact), "lg", "xl")} w="100%">
        <Text size="xs" tt="uppercase" fw={700} c="dimmed" style={{ letterSpacing: "0.1em" }}>
          Report card
        </Text>

        {choose(Boolean(showSummary), (<SimpleGrid cols={{ base: 1, xs: 3 }} spacing={choose(Boolean(compact), "sm", "md")}>
            <Paper withBorder radius="lg" p="md" bg="var(--mantine-color-gray-0)">
              <Group gap="sm" wrap="nowrap">
                <ThemeIcon size={38} radius="xl" variant="light" color="sage">
                  <IconCheck size={20} stroke={2.4}/>
                </ThemeIcon>
                <Box miw={0}>
                  <Text fz={choose(Boolean(compact), 28, 32)} fw={700} lh={1} c="var(--mantine-color-text)">
                    {correct}
                  </Text>
                  <Text fz="xs" c="dimmed" mt={4}>
                    correct first try
                  </Text>
                </Box>
              </Group>
            </Paper>
            <Paper withBorder radius="lg" p="md" bg="var(--mantine-color-gray-0)">
              <Group gap="sm" wrap="nowrap">
                <ThemeIcon size={38} radius="xl" variant="light" color="terracotta">
                  <IconX size={20} stroke={2.4}/>
                </ThemeIcon>
                <Box miw={0}>
                  <Text fz={choose(Boolean(compact), 28, 32)} fw={700} lh={1} c="var(--mantine-color-text)">
                    {wrong}
                  </Text>
                  <Text fz="xs" c="dimmed" mt={4}>
                    to revisit
                  </Text>
                </Box>
              </Group>
            </Paper>
            <Paper withBorder radius="lg" p="md" bg="var(--mantine-color-gray-0)">
              <Box>
                <Text fz={choose(Boolean(compact), 28, 32)} fw={700} lh={1} c="var(--mantine-color-text)" style={{ fontFamily: "var(--font-serif), Georgia, serif", fontVariantNumeric: "tabular-nums" }}>
                  {accuracy}%
                </Text>
                <Text fz="xs" c="dimmed" mt={4} style={{ fontVariantNumeric: "tabular-nums" }}>
                  {correct} / {total} first try
                </Text>
              </Box>
            </Paper>
          </SimpleGrid>), null)}

        {pick(Boolean(weak.length > 0), () => (<Stack gap="sm">
            <Text fz="sm" fw={600} c="var(--mantine-color-text)">
              Topics to focus on
            </Text>
            <Stack gap="md">
              {weak.map((t) => (<Box key={t.name}>
                  <Group justify="space-between" gap="md" wrap="nowrap" mb={6} align="flex-start">
                    <Text fz="sm" lh={1.45} c="var(--mantine-color-text)" lineClamp={3} style={{ flex: 1, minWidth: 0 }}>
                      {t.name}
                    </Text>
                    <Text fz="xs" c="dimmed" style={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                      {t.correct}/{t.total} first try
                    </Text>
                  </Group>
                  <Progress value={Math.round((t.correct / Math.max(t.total, 1)) * 100)} color="terracotta" size="md" radius="xl"/>
                </Box>))}
            </Stack>
          </Stack>), () => null)}

        {pick(Boolean(strong.length > 0), () => (<Stack gap="sm">
            <Text fz="sm" fw={600} c="var(--mantine-color-text)">
              Strong topics
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
              {strong.map((t) => (<Group key={t.name} gap="sm" wrap="nowrap" align="flex-start" py={4}>
                  <ThemeIcon size={24} radius="xl" variant="light" color="sage" style={{ flexShrink: 0 }}>
                    <IconCheck size={14} stroke={2.4}/>
                  </ThemeIcon>
                  <Text fz="sm" lh={1.45} c="var(--mantine-color-text)" lineClamp={2} style={{ flex: 1, minWidth: 0 }}>
                    {t.name}
                  </Text>
                  <Badge size="sm" radius="sm" variant="light" color="sage" tt="none" style={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                    {t.correct}/{t.total}
                  </Badge>
                </Group>))}
            </SimpleGrid>
          </Stack>), () => null)}
      </Stack>
    </Paper>);
    });
}
/**
 * End-of-study screens (extracted from the workspace page monolith): the Test
 * results scorecard, the document-complete celebration, the page-complete
 * interstitial, and the in-session "change study range" overlay, plus the
 * suggestNextPageRange helper that powers "study the next pages".
 */
export function suggestNextPageRange(completed: {
    from: number;
    to: number;
}, pageCount: number): {
    from: number;
    to: number;
    bookFinished: boolean;
} {
    const span = Math.max(0, completed.to - completed.from);
    const nextFrom = completed.to + 1;
    return pick(Boolean(nextFrom > pageCount), () => ({
        from: completed.from,
        to: Math.min(completed.from + span, pageCount),
        bookFinished: true,
    }), () => ({
        from: nextFrom,
        to: Math.min(nextFrom + span, pageCount),
        bookFinished: false,
    }));
}
/** Summative score screen shown at the end of a Test - the payoff Learn never shows. */
export function TestResultsScreen({ correct, total, answered = [], report, compact, canChoosePages, onReview, onChoosePages, }: {
    correct: number;
    total: number;
    answered?: AnsweredCard[];
    report?: StudyReport | null;
    compact?: boolean;
    canChoosePages?: boolean;
    onReview: () => void;
    onChoosePages: () => void;
}) {
    const pct = pick(Boolean(total > 0), () => Math.round((correct / total) * 100), () => 0);
    const tone = choose(Boolean(pct >= 80), "sage", choose(Boolean(pct >= 50), "forest", "terracotta"));
    const verdict = choose(Boolean(pct >= 80), "Excellent", choose(Boolean(pct >= 50), "Solid work", "Keep practicing"));
    const RING = choose(Boolean(compact), 150, 184);
    const R = RING / 2 - 12;
    const C = 2 * Math.PI * R;
    const center = RING / 2;
    return (<Box w="100%" py={choose(Boolean(compact), "lg", "xl")} px={{ base: "xs", sm: "sm" }} style={{ minHeight: "100%" }}>
      <Stack align="center" gap={choose(Boolean(compact), "lg", "xl")} w="100%" maw={MCQ_CONTENT_MAX} mx="auto">
        <style>{`
          @keyframes zv-score-in { from { opacity: 0; transform: translateY(10px) scale(0.96); } to { opacity: 1; transform: none; } }
          @keyframes zv-ring-draw { from { stroke-dashoffset: ${C}; } }
          .zv-score { animation: zv-score-in 520ms cubic-bezier(0.32,0.72,0,1) both; }
          .zv-ring-fill { animation: zv-ring-draw 900ms cubic-bezier(0.32,0.72,0,1) 120ms both; }
          @media (prefers-reduced-motion: reduce) { .zv-score, .zv-ring-fill { animation: none !important; } }
        `}</style>
        <Text fz="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: "0.12em" }}>
          Test complete
        </Text>
        <Box className="zv-score" pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
          <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`}>
            <circle cx={center} cy={center} r={R} fill="none" stroke="var(--mantine-color-gray-3)" strokeWidth={10}/>
            <circle className="zv-ring-fill" cx={center} cy={center} r={R} fill="none" stroke={`var(--mantine-color-${tone}-6)`} strokeWidth={10} strokeLinecap="round" strokeDasharray={C} strokeDashoffset={C * (1 - pct / 100)} transform={`rotate(-90 ${center} ${center})`}/>
          </svg>
          <Stack pos="absolute" gap={0} align="center">
            <Text fz={choose(Boolean(compact), 34, 42)} fw={600} c="var(--mantine-color-text)" style={{ fontFamily: "var(--font-serif), Georgia, serif", lineHeight: 1, letterSpacing: "-0.02em" }}>
              {pct}%
            </Text>
            <Text fz="sm" c="dimmed" fw={600} mt={4} style={{ fontVariantNumeric: "tabular-nums" }}>
              {correct} / {total} correct
            </Text>
          </Stack>
        </Box>
        <Stack gap={4} align="center">
          <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 22, 26)} fw={500} c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {verdict}
          </Text>
          <Text c="dimmed" fz="sm" ta="center" maw={320} lh={1.55}>
            Review every question to see the correct answers and the reasoning behind them.
          </Text>
        </Stack>
        <StudyReportCard answered={answered} report={report} showSummary={false} compact={compact}/>
        <Stack gap={8} w="100%" maw={360} mt="xs">
          <Button radius="xl" size="md" color="forest" onClick={onReview} leftSection={<IconHistory size={16} stroke={2}/>}>
            Review answers
          </Button>
          {choose(Boolean(canChoosePages), (<Button radius="xl" size="sm" variant="subtle" color="gray" onClick={onChoosePages}>
              Study new pages
            </Button>), null)}
        </Stack>
      </Stack>
    </Box>);
}
export function DocumentCompleteScreen({ completedFrom, completedTo, pageCount, bookFinished, nextFrom, nextTo, answered = [], report, compact, onChoosePages, actionLabel, hideNextSuggestion = false, onContinueToTest, continueToTestLabel = "Continue to Test mode", }: {
    completedFrom: number;
    completedTo: number;
    pageCount: number;
    bookFinished: boolean;
    nextFrom?: number;
    nextTo?: number;
    answered?: AnsweredCard[];
    report?: StudyReport | null;
    compact?: boolean;
    onChoosePages: () => void;
    /** Override primary CTA copy (e.g. newspaper → "Back to days"). */
    actionLabel?: string;
    /** Hide "suggested next pages" (no page picker for this surface). */
    hideNextSuggestion?: boolean;
    /** Newspaper Learn complete: optional second pass into Test. */
    onContinueToTest?: () => void;
    continueToTestLabel?: string;
}) {
    const pageLabel = choose(Boolean(completedFrom === completedTo), `Page ${completedFrom}`, `Pages ${completedFrom}-${completedTo}`);
    return (<Box w="100%" py={choose(Boolean(compact), "lg", "xl")} px={{ base: "xs", sm: "sm" }} style={{ minHeight: "100%" }}>
      <Stack gap={choose(Boolean(compact), "lg", "xl")} w="100%" maw={MCQ_CONTENT_MAX} mx="auto" align="stretch">
        <Stack align="center" gap="xs" ta="center">
          <ThemeIcon size={56} radius="xl" variant="gradient" gradient={{ from: "sage", to: "lavender", deg: 135 }} style={{ boxShadow: "0 8px 22px rgba(94, 124, 99, 0.28)" }}>
            <IconClipboardList size={28} stroke={1.75}/>
          </ThemeIcon>
          <Title order={2} ta="center" fw={500} style={{ letterSpacing: "-0.01em", lineHeight: 1.2, fontFamily: "var(--font-serif), Georgia, serif" }}>
            {choose(Boolean(onContinueToTest), "Learn complete", `${pageLabel} complete`)}
          </Title>
          <Text size="sm" c="dimmed" ta="center" lh={1.6} maw={520}>
            {choose(Boolean(onContinueToTest), "You have worked through every Learn question in this edition. Continue to Test mode for a second pass with new questions, or head back to the paper.", choose(Boolean(bookFinished), `You've worked through every page in this ${pageCount}-page book. Pick any range to study again, or continue elsewhere in your library.`, "Every question in this range is done. When you're ready, choose the next pages from the same source."))}
          </Text>
        </Stack>

        <StudyReportCard answered={answered} report={report} compact={compact}/>

        {pick(Boolean(!hideNextSuggestion), () => pick(Boolean(!bookFinished), () => pick(Boolean(nextFrom !== undefined), () => pick(Boolean(nextTo !== undefined), () => (<Paper withBorder radius="lg" p="md" w="100%" bg="var(--mantine-color-body)">
            <Text size="xs" tt="uppercase" fw={600} c="dimmed" mb={6}>
              Suggested next
            </Text>
            <Text size="lg" fw={600} style={{ letterSpacing: "-0.02em" }}>
              Pages {nextFrom}-{nextTo}
            </Text>
          </Paper>), () => nextTo !== undefined), () => nextFrom !== undefined), () => !bookFinished), () => !hideNextSuggestion)}

        <Stack gap="sm" w="100%" maw={360} mx="auto">
          {choose(Boolean(onContinueToTest), (<Button size="md" radius="xl" fullWidth onClick={onContinueToTest}>
              {continueToTestLabel}
            </Button>), null)}
          <Button size="md" radius="xl" fullWidth variant={choose(Boolean(onContinueToTest), "light", "filled")} color={choose(Boolean(onContinueToTest), "gray", undefined)} onClick={onChoosePages}>
            {actionLabel ?? (choose(Boolean(bookFinished), "Choose pages to study", "Choose next pages"))}
          </Button>
        </Stack>
      </Stack>
    </Box>);
}
export function StudyRangeReselectOverlay({ filename, pageCount, completedRange, sliderFrom, sliderTo, sliderMarks, selectedPages, isDark, isCompact, isPdf, pdfDoc, pageTexts, pageTextsLoading, thumbCanvasRefs, confirming, confirmingMode, setupError, bookFinished, onRangeChange, onPageToggle, onSelectAll, onClearAll, onClose, onConfirmNow, }: {
    filename: string;
    pageCount: number;
    completedRange?: {
        from: number;
        to: number;
    };
    sliderFrom: number;
    sliderTo: number;
    sliderMarks: {
        value: number;
        label?: ReactNode;
    }[];
    selectedPages: number[];
    isDark: boolean;
    isCompact: boolean;
    isPdf: boolean;
    pdfDoc: PDFDocumentProxy | null;
    pageTexts?: Record<number, string>;
    pageTextsLoading?: boolean;
    thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
    confirming: boolean;
    confirmingMode: "now" | "background" | null;
    setupError: string | null;
    bookFinished: boolean;
    onRangeChange: (from: number, to: number) => void;
    onPageToggle: (page: number, shiftKey: boolean) => void;
    onSelectAll: () => void;
    onClearAll: () => void;
    onClose: () => void;
    onConfirmNow: () => void;
}) {/*..............................................................................*/
    const completedLabel = choose(Boolean(completedRange && completedRange.from === completedRange.to), `page ${completedRange.from}`, choose(Boolean(completedRange), `pages ${completedRange.from}-${completedRange.to}`, "your last selection"));
    return (<Box pos="fixed" inset={0} style={{
            zIndex: 300,
            display: "flex",
            alignItems: choose(Boolean(isCompact), "flex-end", "center"),
            justifyContent: "center",
            padding: choose(Boolean(isCompact), 0, "var(--mantine-spacing-md)"),
            background: "rgba(0, 0, 0, 0.45)",
            backdropFilter: "blur(6px)",
        }} onClick={onClose}>
      <Paper shadow="xl" radius={choose(Boolean(isCompact), 0, "lg")} p={choose(Boolean(isCompact), "md", "xl")} w="100%" maw={choose(Boolean(isCompact), "100%", 900)} 
    // A DEFINITE, viewport-bounded height (not just a min) so the inner
    // thumbnail grid's flex scroll container has something to scroll within.
    // Previously the Paper grew taller than the screen and the grid couldn't
    // scroll at all - pages past the fold were unreachable.
    h={choose(Boolean(isCompact), "92dvh", "min(88vh, 760px)")} mah={choose(Boolean(isCompact), "92dvh", "min(88vh, 760px)")} onClick={(e) => e.stopPropagation()} style={{
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            borderBottomLeftRadius: choose(Boolean(isCompact), 0, undefined),
            borderBottomRightRadius: choose(Boolean(isCompact), 0, undefined),
            paddingBottom: choose(Boolean(isCompact), "max(20px, env(safe-area-inset-bottom))", undefined),
        }}>
        <Group justify="space-between" align="flex-start" mb="md" wrap="nowrap">
          <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
            <Text size="xs" tt="uppercase" fw={600} c="dimmed">
              {filename}
            </Text>
            <Title order={4} style={{ letterSpacing: "-0.03em" }}>
              Pick your next pages
            </Title>
            <Text size="sm" c="dimmed" lh={1.5}>
              {choose(Boolean(bookFinished), `You finished ${completedLabel}. Tap any pages in this ${pageCount}-page book.`, `You finished ${completedLabel}. Choose the pages you want to study next.`)}
            </Text>
          </Stack>
          <ActionIcon variant="subtle" color="gray" onClick={onClose} aria-label="Close">
            <IconX size={18}/>
          </ActionIcon>
        </Group>
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column" }}>
          <PageSelectionBody padX={choose(Boolean(isCompact), SELECTION_PAD_X_COMPACT, SELECTION_PAD_X)} pageCount={pageCount} sliderFrom={sliderFrom} sliderTo={sliderTo} sliderMarks={sliderMarks} selectedPages={selectedPages} isDark={isDark} isPdf={isPdf} pdfDoc={pdfDoc} pageTexts={pageTexts} pageTextsLoading={pageTextsLoading} thumbCanvasRefs={thumbCanvasRefs} confirming={confirming} confirmingMode={confirmingMode} setupError={setupError} showBackgroundPrep={false} onRangeChange={onRangeChange} onPageToggle={onPageToggle} onSelectAll={onSelectAll} onClearAll={onClearAll} onConfirmNow={onConfirmNow} onPrepInBackground={onConfirmNow}/>
        </Box>
      </Paper>
    </Box>);
}
export function PageCompleteInterstitial({ compact, generating, }: {
    page: number;
    compact?: boolean;
    generating?: boolean;
}) {
    // A celebratory beat between pages: a gradient ring draws itself, a checkmark
    // traces in, sparkles drift up, and "Well done" rises - then animated dots while
    // the next question loads. On-brand (lavender→sage, serif) and centered on screen.
    const ring = choose(Boolean(compact), 104, 124);
    const r = ring / 2 - 9;
    const c = 2 * Math.PI * r;
    const cx = ring / 2;
    return (<Center h="100%" mih="60vh" px="md">
      <Stack align="center" gap={choose(Boolean(compact), "md", "lg")} maw={360}>
        <style>{`
          @keyframes zv-pc-pop { 0% { opacity: 0; transform: scale(0.4); } 60% { transform: scale(1.06); } 100% { opacity: 1; transform: scale(1); } }
          @keyframes zv-pc-ring { to { stroke-dashoffset: 0; } }
          @keyframes zv-pc-check { to { stroke-dashoffset: 0; } }
          @keyframes zv-pc-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
          @keyframes zv-pc-spark { 0% { opacity: 0; transform: translateY(0) scale(0.4); } 25% { opacity: 1; } 100% { opacity: 0; transform: translateY(-160%) scale(1); } }
          @keyframes zv-pc-glow { 0%,100% { opacity: 0.35; transform: scale(0.92); } 50% { opacity: 0.6; transform: scale(1.04); } }
          @keyframes zv-pc-dot { 0%,100% { opacity: 0.4; transform: translateY(0); } 50% { opacity: 1; transform: translateY(-3px); } }
          .zv-pc-emblem { animation: zv-pc-pop 560ms cubic-bezier(0.34,1.56,0.64,1) both; }
          .zv-pc-glow { animation: zv-pc-glow 3s ease-in-out infinite; transform-origin: center; }
          .zv-pc-ring { animation: zv-pc-ring 760ms cubic-bezier(0.4,0,0.2,1) 140ms both; }
          .zv-pc-check { animation: zv-pc-check 440ms ease-out 600ms both; }
          .zv-pc-rise { animation: zv-pc-rise 520ms cubic-bezier(0.32,0.72,0,1) 200ms both; }
          .zv-pc-rise-2 { animation: zv-pc-rise 520ms cubic-bezier(0.32,0.72,0,1) 360ms both; }
          .zv-pc-spark { animation: zv-pc-spark 2.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
          .zv-pc-dot { animation: zv-pc-dot 1.1s ease-in-out infinite; }
          @media (prefers-reduced-motion: reduce) {
            .zv-pc-emblem,.zv-pc-glow,.zv-pc-ring,.zv-pc-check,.zv-pc-rise,.zv-pc-rise-2,.zv-pc-spark,.zv-pc-dot {
              animation: none !important; opacity: 1 !important; transform: none !important; stroke-dashoffset: 0 !important;
            }
          }
        `}</style>
        <Box className="zv-pc-emblem" pos="relative" w={ring} h={ring} style={{ display: "grid", placeItems: "center" }}>
          <svg width={ring} height={ring} viewBox={`0 0 ${ring} ${ring}`} style={{ overflow: "visible" }}>
            <defs>
              <linearGradient id="zv-pc-grad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="var(--mantine-color-lavender-5)"/>
                <stop offset="100%" stopColor="var(--mantine-color-sage-5)"/>
              </linearGradient>
            </defs>
            {/* soft pulsing halo */}
            <circle className="zv-pc-glow" cx={cx} cy={cx} r={r + 6} fill="var(--mantine-color-sage-4)" opacity={0.25}/>
            {/* track + drawn progress ring */}
            <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--mantine-color-default-border)" strokeWidth={6} opacity={0.4}/>
            <circle className="zv-pc-ring" cx={cx} cy={cx} r={r} fill="none" stroke="url(#zv-pc-grad)" strokeWidth={6} strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c} transform={`rotate(-90 ${cx} ${cx})`}/>
            {/* checkmark trace */}
            <path className="zv-pc-check" d={`M${ring * 0.33} ${ring * 0.52} L${ring * 0.45} ${ring * 0.64} L${ring * 0.67} ${ring * 0.39}`} fill="none" stroke="var(--mantine-color-sage-6)" strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={44} strokeDashoffset={44}/>
            {/* drifting sparks */}
            <circle className="zv-pc-spark" cx={ring * 0.82} cy={ring * 0.32} r={3} fill="var(--mantine-color-lavender-5)" style={{ animationDelay: "0.5s" }}/>
            <circle className="zv-pc-spark" cx={ring * 0.17} cy={ring * 0.42} r={2.2} fill="var(--mantine-color-sage-5)" style={{ animationDelay: "1.3s" }}/>
            <circle className="zv-pc-spark" cx={ring * 0.7} cy={ring * 0.72} r={2.2} fill="var(--mantine-color-lavender-4)" style={{ animationDelay: "2s" }}/>
          </svg>
        </Box>
        <Stack align="center" gap={6}>
          <Text className="zv-pc-rise" ff="var(--font-serif)" fz={choose(Boolean(compact), 26, 32)} fw={500} ta="center" c="var(--mantine-color-text)" style={{ letterSpacing: "-0.02em", lineHeight: 1.1 }}>
            Well done
          </Text>
          <Group className="zv-pc-rise-2" gap={7} align="center" wrap="nowrap">
            <Text fz="sm" c="dimmed">
              {choose(Boolean(generating), "Preparing what's next", "Loading your next question")}
            </Text>
            <Box style={{ display: "inline-flex", gap: 3 }}>
              {[0, 1, 2].map((i) => (<Box key={i} className="zv-pc-dot" w={4} h={4} style={{ borderRadius: "50%", background: "var(--mantine-color-lavender-5)", animationDelay: `${i * 0.16}s` }}/>))}
            </Box>
          </Group>
        </Stack>
      </Stack>
    </Center>);
}
