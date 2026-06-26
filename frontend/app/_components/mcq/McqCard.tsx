"use client";

/**
 * Shared MCQ rendering for the workspace and the public practice library.
 *
 * `McqFeedbackCard` is extracted verbatim from the workspace study page so both
 * surfaces share one feedback design. `McqCard` is a lean, self-contained
 * question card for the practice run page: it does NOT carry the workspace's
 * per-document generation/wait/queue state, only the essentials needed to
 * render a question, capture a selection, show graded feedback, and advance.
 */

import { Box, Button, Group, Radio, Stack, Text, Title, useMantineColorScheme } from "@mantine/core";
import { normalizeMcqOptions } from "@/lib/types";

export type GradeState = { correct: boolean; correctIndex: number } | null;

export type McqOptionVisualState = {
  isSelected: boolean;
  isCorrectOption: boolean;
  isWrongSelected: boolean;
};

/**
 * Option chrome shared by workspace McqHeroPanel and practice McqCard.
 *
 * Filled accent chips (selected / correct / wrong) use bright shade-6 fills;
 * content stays paper-white in both schemes for readable contrast.
 */
export function mcqOptionChrome(isDark: boolean, state: McqOptionVisualState) {
  const { isSelected, isCorrectOption, isWrongSelected } = state;
  // Filled accent chips (shade-6) read dark in light mode and light in dark mode,
  // so the letter on them must flip: paper-white on light scheme, ink on dark.
  // Using literals avoids the inverted gray scale (where gray-9 is white in dark).
  const chipInk = isDark ? "#1A1917" : "#FFFFFF";

  let border = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-default-border)";
  let background = isDark ? "var(--mantine-color-dark-7)" : "var(--mantine-color-gray-0)";
  // Idle chip: a quiet tinted square. Keep the letter readable on it in both schemes.
  let chipBg = isDark ? "var(--mantine-color-dark-5)" : "var(--mantine-color-gray-2)";
  let chipColor = isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-7)";
  let borderWidth = 1;

  if (isCorrectOption) {
    border = isDark ? "var(--mantine-color-sage-4)" : "var(--mantine-color-sage-5)";
    background = isDark ? "var(--mantine-color-sage-1)" : "var(--mantine-color-sage-0)";
    chipBg = "var(--mantine-color-sage-6)";
    chipColor = chipInk;
    borderWidth = 2;
  } else if (isWrongSelected) {
    border = isDark ? "var(--mantine-color-terracotta-4)" : "var(--mantine-color-terracotta-5)";
    background = isDark ? "var(--mantine-color-terracotta-1)" : "var(--mantine-color-terracotta-0)";
    chipBg = "var(--mantine-color-terracotta-6)";
    chipColor = chipInk;
    borderWidth = 2;
  } else if (isSelected) {
    // A decisive, on-brand selection — a present lavender tint and a saturated
    // brand-primary outline, not the near-white wash it had before.
    border = isDark ? "var(--mantine-color-lavender-5)" : "var(--mantine-color-lavender-6)";
    background = isDark ? "var(--mantine-color-lavender-1)" : "var(--mantine-color-lavender-1)";
    chipBg = "var(--mantine-color-lavender-6)";
    chipColor = chipInk;
    borderWidth = 2;
  }

  return { border, background, chipBg, chipColor, borderWidth };
}

export function McqFeedbackCard({
  feedback,
  isCorrect,
  compact,
  isDark,
}: {
  feedback: string;
  isCorrect: boolean;
  compact?: boolean;
  isDark: boolean;
}) {
  // The selected/correct option chips already signal right vs wrong in colour, so
  // the explanation reads as a calm, serif-italic footnote — premium and small, so
  // revealing it barely changes the card height (no jarring jump).
  const text = feedback
    .split("\n\n")
    .map((p) => p.trim())
    .filter(Boolean)
    .join("  ");

  return (
    <Box
      className="mcq-feedback"
      style={{
        flexShrink: 0,
        textAlign: "center",
        padding: compact ? "2px 12px 0" : "6px 28px 0",
      }}
    >
      <style>{`
        @keyframes mcq-fb { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
        .mcq-feedback { animation: mcq-fb 360ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .mcq-feedback { animation: none !important; } }
      `}</style>
      <Text
        c="dimmed"
        style={{
          fontFamily: "var(--font-serif), Georgia, serif",
          fontStyle: "italic",
          lineHeight: 1.6,
          fontSize: compact ? "0.8rem" : "0.95rem",
          maxWidth: 560,
          marginInline: "auto",
        }}
      >
        {text}
      </Text>
    </Box>
  );
}

export function McqCard({
  stem,
  options,
  selected,
  onSelect,
  feedback,
  mode = "test",
  gradeState,
  submitting,
  loading = false,
  loadingLabel = "Preparing your questions…",
  compact = false,
  onSubmit,
  onNext,
}: {
  stem: string;
  options: string[];
  selected: string | null;
  onSelect: (value: string) => void;
  feedback: string | null;
  mode?: "learn" | "test";
  gradeState: GradeState;
  submitting: boolean;
  loading?: boolean;
  loadingLabel?: string;
  compact?: boolean;
  onSubmit: () => void;
  onNext: () => void;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const safeOptions = normalizeMcqOptions(options);
  const graded = gradeState !== null;
  const optionsLocked = graded && (mode === "test" || gradeState.correct);

  if (loading || safeOptions.length === 0) {
    return (
      <Stack align="center" justify="center" gap="sm" mih={220}>
        <Text size="lg" fw={500} ta="center" c="var(--mantine-color-text)" style={{ letterSpacing: "-0.025em" }}>
          {loadingLabel}
        </Text>
        <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={300}>
          Generating fresh practice questions from the concept&apos;s source material.
        </Text>
      </Stack>
    );
  }

  return (
    <Stack gap={compact ? 12 : 16} align="stretch" mih={0}>
      <Title
        order={2}
        fw={500}
        lh={1.35}
        ta="center"
        c="var(--mantine-color-text)"
        style={{
          flexShrink: 0,
          fontFamily: "var(--font-serif), Georgia, serif",
          fontSize: compact ? "clamp(0.95rem, 4.2vw, 1.25rem)" : "clamp(1.05rem, 2vw, 1.6rem)",
          letterSpacing: "-0.005em",
        }}
      >
        {stem}
      </Title>

      <Radio.Group
        value={selected}
        onChange={(value) => {
          if (optionsLocked) return;
          onSelect(value);
        }}
        name="mcq-options"
      >
        <Stack gap={compact ? 6 : 8} mih={0}>
          {safeOptions.map((opt, i) => {
            const value = String(i);
            const isSelected = selected === value;
            const isCorrectOption = graded && gradeState.correctIndex === i;
            const isWrongSelected = graded && !gradeState.correct && isSelected;
            const { border: borderColor, background, borderWidth } = mcqOptionChrome(
              isDark,
              { isSelected, isCorrectOption, isWrongSelected },
            );
            return (
              <Radio
                key={value}
                value={value}
                disabled={optionsLocked}
                label={
                  <Group wrap="nowrap" align="flex-start" gap="sm">
                    <Text size="sm" c="dimmed" w={20} ta="center" ff="monospace" fw={600}>
                      {String.fromCharCode(65 + i)}
                    </Text>
                    <Text
                      size={compact ? "sm" : "md"}
                      lh={1.5}
                      c="var(--mantine-color-text)"
                      style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem" }}
                    >
                      {opt}
                    </Text>
                  </Group>
                }
                styles={{
                  root: {
                    width: "100%",
                    borderRadius: 12,
                    padding: compact ? "14px 12px" : "12px 14px",
                    minHeight: 44,
                    border: `${borderWidth}px solid ${borderColor}`,
                    background,
                    opacity: optionsLocked && !isCorrectOption && !isWrongSelected ? 0.65 : 1,
                    transition: "border-color 120ms ease, background 120ms ease",
                    cursor: optionsLocked ? "default" : "pointer",
                  },
                  body: { alignItems: "flex-start" },
                  label: { width: "100%", paddingInlineStart: 8 },
                  radio: { marginTop: 4 },
                }}
              />
            );
          })}
        </Stack>
      </Radio.Group>

      {feedback && (
        <McqFeedbackCard
          feedback={feedback}
          isCorrect={gradeState?.correct === true}
          compact={compact}
          isDark={isDark}
        />
      )}

      <Stack align="center" gap="xs" style={{ flexShrink: 0 }}>
        {graded ? (
          <Button
            radius="xl"
            size="md"
            color="sage"
            maw={compact ? "100%" : 280}
            w="100%"
            loading={submitting}
            onClick={onNext}
          >
            Next question
          </Button>
        ) : (
          <Button
            radius="xl"
            size="md"
            color="lavender"
            maw={compact ? "100%" : 280}
            w="100%"
            onClick={onSubmit}
            loading={submitting}
            disabled={selected === null}
          >
            Check answer
          </Button>
        )}
      </Stack>
    </Stack>
  );
}
