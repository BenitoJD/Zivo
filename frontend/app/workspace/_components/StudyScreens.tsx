"use client";

import type { ReactNode } from "react";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Center,
  Group,
  Loader,
  Paper,
  Progress,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { IconCheck, IconClipboardList, IconHistory, IconX } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  SELECTION_PAD_X,
  SELECTION_PAD_X_COMPACT,
  type AnsweredCard,
} from "@/app/workspace/_components/studyLayout";
import { PageSelectionBody } from "@/app/workspace/_components/PageSelectionScreen";

/**
 * End-of-study report card: first-try correct vs. to-revisit, plus per-topic
 * strengths and the topics to focus on next. Driven entirely by the answered
 * history (first-attempt correctness, so Learn-mode retries don't hide weak spots).
 */
export function StudyReportCard({
  answered,
  showSummary = true,
  compact,
}: {
  answered: AnsweredCard[];
  showSummary?: boolean;
  compact?: boolean;
}) {
  const total = answered.length;
  if (total === 0) return null;
  const correct = answered.filter((a) => a.firstTryCorrect).length;
  const wrong = total - correct;

  const byConcept = new Map<string, { name: string; correct: number; total: number }>();
  for (const a of answered) {
    const name = (a.concept || "").trim() || "General";
    const t = byConcept.get(name) ?? { name, correct: 0, total: 0 };
    t.total += 1;
    if (a.firstTryCorrect) t.correct += 1;
    byConcept.set(name, t);
  }
  const topics = [...byConcept.values()];
  const weak = topics
    .filter((t) => t.correct < t.total)
    .sort((a, b) => a.correct / a.total - b.correct / b.total);
  const strong = topics.filter((t) => t.correct === t.total);

  return (
    <Paper withBorder radius="lg" p={compact ? "md" : "lg"} w="100%" bg="var(--mantine-color-body)">
      <Stack gap={compact ? "sm" : "md"}>
        <Text size="xs" tt="uppercase" fw={700} c="dimmed" style={{ letterSpacing: "0.1em" }}>
          Report card
        </Text>

        {showSummary ? (
          <Group gap="lg" wrap="nowrap" align="center">
            <Group gap={8} wrap="nowrap">
              <ThemeIcon size={34} radius="xl" variant="light" color="sage">
                <IconCheck size={18} stroke={2.4} />
              </ThemeIcon>
              <Box>
                <Text fz={compact ? 22 : 26} fw={700} lh={1} c="var(--mantine-color-text)">
                  {correct}
                </Text>
                <Text fz="xs" c="dimmed">
                  correct first try
                </Text>
              </Box>
            </Group>
            <Group gap={8} wrap="nowrap">
              <ThemeIcon size={34} radius="xl" variant="light" color="terracotta">
                <IconX size={18} stroke={2.4} />
              </ThemeIcon>
              <Box>
                <Text fz={compact ? 22 : 26} fw={700} lh={1} c="var(--mantine-color-text)">
                  {wrong}
                </Text>
                <Text fz="xs" c="dimmed">
                  to revisit
                </Text>
              </Box>
            </Group>
            <Box style={{ marginLeft: "auto", textAlign: "right" }}>
              <Text
                fz={compact ? 22 : 26}
                fw={700}
                lh={1}
                c="var(--mantine-color-text)"
                style={{ fontFamily: "var(--font-serif), Georgia, serif" }}
              >
                {Math.round((correct / total) * 100)}%
              </Text>
              <Text fz="xs" c="dimmed" style={{ fontVariantNumeric: "tabular-nums" }}>
                {correct} / {total}
              </Text>
            </Box>
          </Group>
        ) : null}

        {weak.length > 0 ? (
          <Stack gap={8}>
            <Text fz="sm" fw={600} c="var(--mantine-color-text)">
              Topics to focus on
            </Text>
            {weak.map((t) => (
              <Box key={t.name}>
                <Group justify="space-between" gap="sm" wrap="nowrap" mb={3} align="flex-start">
                  <Text fz="sm" c="var(--mantine-color-text)" style={{ minWidth: 0 }}>
                    {t.name}
                  </Text>
                  <Text
                    fz="xs"
                    c="dimmed"
                    style={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
                  >
                    {t.correct}/{t.total} first try
                  </Text>
                </Group>
                <Progress
                  value={Math.round((t.correct / Math.max(t.total, 1)) * 100)}
                  color="terracotta"
                  size="sm"
                  radius="xl"
                />
              </Box>
            ))}
          </Stack>
        ) : null}

        {strong.length > 0 ? (
          <Stack gap={6}>
            <Text fz="sm" fw={600} c="var(--mantine-color-text)">
              Strong topics
            </Text>
            <Group gap={6}>
              {strong.map((t) => (
                <Badge key={t.name} variant="light" color="sage" radius="sm" tt="none">
                  {t.name}
                </Badge>
              ))}
            </Group>
          </Stack>
        ) : null}
      </Stack>
    </Paper>
  );
}

/**
 * End-of-study screens (extracted from the workspace page monolith): the Test
 * results scorecard, the document-complete celebration, the page-complete
 * interstitial, and the in-session "change study range" overlay, plus the
 * suggestNextPageRange helper that powers "study the next pages".
 */
export function suggestNextPageRange(
  completed: { from: number; to: number },
  pageCount: number,
): { from: number; to: number; bookFinished: boolean } {
  const span = Math.max(0, completed.to - completed.from);
  const nextFrom = completed.to + 1;
  if (nextFrom > pageCount) {
    return {
      from: completed.from,
      to: Math.min(completed.from + span, pageCount),
      bookFinished: true,
    };
  }
  return {
    from: nextFrom,
    to: Math.min(nextFrom + span, pageCount),
    bookFinished: false,
  };
}

/** Summative score screen shown at the end of a Test — the payoff Learn never shows. */
export function TestResultsScreen({
  correct,
  total,
  answered = [],
  compact,
  canChoosePages,
  onReview,
  onChoosePages,
}: {
  correct: number;
  total: number;
  answered?: AnsweredCard[];
  compact?: boolean;
  canChoosePages?: boolean;
  onReview: () => void;
  onChoosePages: () => void;
}) {
  const pct = total > 0 ? Math.round((correct / total) * 100) : 0;
  const tone = pct >= 80 ? "sage" : pct >= 50 ? "forest" : "terracotta";
  const verdict = pct >= 80 ? "Excellent" : pct >= 50 ? "Solid work" : "Keep practicing";
  const RING = compact ? 150 : 184;
  const R = RING / 2 - 12;
  const C = 2 * Math.PI * R;
  const center = RING / 2;
  return (
    <Center h="100%" py={compact ? "md" : "lg"}>
      <Stack align="center" gap={compact ? "md" : "lg"} maw={440} px="md">
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
            <circle cx={center} cy={center} r={R} fill="none" stroke="var(--mantine-color-gray-3)" strokeWidth={10} />
            <circle
              className="zv-ring-fill"
              cx={center}
              cy={center}
              r={R}
              fill="none"
              stroke={`var(--mantine-color-${tone}-6)`}
              strokeWidth={10}
              strokeLinecap="round"
              strokeDasharray={C}
              strokeDashoffset={C * (1 - pct / 100)}
              transform={`rotate(-90 ${center} ${center})`}
            />
          </svg>
          <Stack pos="absolute" gap={0} align="center">
            <Text fz={compact ? 34 : 42} fw={600} c="var(--mantine-color-text)" style={{ fontFamily: "var(--font-serif), Georgia, serif", lineHeight: 1, letterSpacing: "-0.02em" }}>
              {pct}%
            </Text>
            <Text fz="sm" c="dimmed" fw={600} mt={4} style={{ fontVariantNumeric: "tabular-nums" }}>
              {correct} / {total} correct
            </Text>
          </Stack>
        </Box>
        <Stack gap={4} align="center">
          <Text ff="var(--font-serif)" fz={compact ? 22 : 26} fw={500} c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {verdict}
          </Text>
          <Text c="dimmed" fz="sm" ta="center" maw={320} lh={1.55}>
            Review every question to see the correct answers and the reasoning behind them.
          </Text>
        </Stack>
        <StudyReportCard answered={answered} showSummary={false} compact={compact} />
        <Stack gap={8} w="100%" maw={300} mt="xs">
          <Button radius="xl" size="md" color="forest" onClick={onReview} leftSection={<IconHistory size={16} stroke={2} />}>
            Review answers
          </Button>
          {canChoosePages ? (
            <Button radius="xl" size="sm" variant="subtle" color="gray" onClick={onChoosePages}>
              Study new pages
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Center>
  );
}

export function DocumentCompleteScreen({
  completedFrom,
  completedTo,
  pageCount,
  bookFinished,
  nextFrom,
  nextTo,
  answered = [],
  compact,
  onChoosePages,
}: {
  completedFrom: number;
  completedTo: number;
  pageCount: number;
  bookFinished: boolean;
  nextFrom?: number;
  nextTo?: number;
  answered?: AnsweredCard[];
  compact?: boolean;
  onChoosePages: () => void;
}) {
  const pageLabel =
    completedFrom === completedTo
      ? `Page ${completedFrom}`
      : `Pages ${completedFrom}–${completedTo}`;

  return (
    <Center py={compact ? "lg" : "xl"} px="md" h="100%">
      <Stack align="center" gap={compact ? "lg" : "xl"} maw={440}>
        <Stack align="center" gap="xs">
          <ThemeIcon size={52} radius="xl" variant="light" color="sage">
            <IconClipboardList size={26} stroke={1.5} />
          </ThemeIcon>
          <Title
            order={2}
            ta="center"
            fw={500}
            style={{ letterSpacing: "-0.01em", lineHeight: 1.2, fontFamily: "var(--font-serif), Georgia, serif" }}
          >
            {pageLabel} complete
          </Title>
          <Text size="sm" c="dimmed" ta="center" lh={1.6} maw={360}>
            {bookFinished
              ? `You've worked through every page in this ${pageCount}-page book. Pick any range to study again, or continue elsewhere in your library.`
              : "Every question in this range is done. When you're ready, choose the next pages from the same source."}
          </Text>
        </Stack>

        <StudyReportCard answered={answered} compact={compact} />

        {!bookFinished && nextFrom !== undefined && nextTo !== undefined && (
          <Paper withBorder radius="lg" p="md" w="100%" bg="var(--mantine-color-body)">
            <Text size="xs" tt="uppercase" fw={600} c="dimmed" mb={6}>
              Suggested next
            </Text>
            <Text size="lg" fw={600} style={{ letterSpacing: "-0.02em" }}>
              Pages {nextFrom}–{nextTo}
            </Text>
          </Paper>
        )}

        <Stack gap="sm" w="100%" maw={320}>
          <Button size="md" radius="xl" fullWidth onClick={onChoosePages}>
            {bookFinished ? "Choose pages to study" : "Choose next pages"}
          </Button>
        </Stack>
      </Stack>
    </Center>
  );
}

export function StudyRangeReselectOverlay({
  filename,
  pageCount,
  completedRange,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isCompact,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  confirming,
  setupError,
  bookFinished,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onClose,
  onConfirm,
}: {
  filename: string;
  pageCount: number;
  completedRange?: { from: number; to: number };
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isCompact: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  bookFinished: boolean;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const completedLabel =
    completedRange && completedRange.from === completedRange.to
      ? `page ${completedRange.from}`
      : completedRange
        ? `pages ${completedRange.from}–${completedRange.to}`
        : "your last selection";

  return (
    <Box
      pos="fixed"
      inset={0}
      style={{
        zIndex: 300,
        display: "flex",
        alignItems: isCompact ? "flex-end" : "center",
        justifyContent: "center",
        padding: isCompact ? 0 : "var(--mantine-spacing-md)",
        background: "rgba(0, 0, 0, 0.45)",
        backdropFilter: "blur(6px)",
      }}
      onClick={onClose}
    >
      <Paper
        shadow="xl"
        radius={isCompact ? 0 : "lg"}
        p={isCompact ? "md" : "xl"}
        w="100%"
        maw={isCompact ? "100%" : 900}
        mih={isCompact ? "85vh" : "min(88vh, 760px)"}
        onClick={(e) => e.stopPropagation()}
        style={{
          display: "flex",
          flexDirection: "column",
          borderBottomLeftRadius: isCompact ? 0 : undefined,
          borderBottomRightRadius: isCompact ? 0 : undefined,
          paddingBottom: isCompact ? "max(20px, env(safe-area-inset-bottom))" : undefined,
        }}
      >
        <Group justify="space-between" align="flex-start" mb="md" wrap="nowrap">
          <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
            <Text size="xs" tt="uppercase" fw={600} c="dimmed">
              {filename}
            </Text>
            <Title order={4} style={{ letterSpacing: "-0.03em" }}>
              Pick your next pages
            </Title>
            <Text size="sm" c="dimmed" lh={1.5}>
              {bookFinished
                ? `You finished ${completedLabel}. Tap any pages in this ${pageCount}-page book.`
                : `You finished ${completedLabel}. Choose the pages you want to study next.`}
            </Text>
          </Stack>
          <ActionIcon variant="subtle" color="gray" onClick={onClose} aria-label="Close">
            <IconX size={18} />
          </ActionIcon>
        </Group>
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column" }}>
          <PageSelectionBody
            padX={isCompact ? SELECTION_PAD_X_COMPACT : SELECTION_PAD_X}
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
            confirmLabel="Start studying"
            onRangeChange={onRangeChange}
            onPageToggle={onPageToggle}
            onSelectAll={onSelectAll}
            onClearAll={onClearAll}
            onConfirm={onConfirm}
          />
        </Box>
      </Paper>
    </Box>
  );
}

export function PageCompleteInterstitial({
  compact,
  generating,
}: {
  page: number;
  compact?: boolean;
  generating?: boolean;
}) {
  return (
    <Center py={compact ? "md" : "xl"}>
      <Stack align="center" gap="md" maw={320}>
        <Loader type="oval" size="sm" />
        <Text size="lg" fw={500} ta="center" style={{ letterSpacing: "-0.02em" }}>
          Well done
        </Text>
        <Text size="sm" c="dimmed" ta="center" lh={1.55}>
          {generating ? "Preparing what's next…" : "Continuing…"}
        </Text>
      </Stack>
    </Center>
  );
}
