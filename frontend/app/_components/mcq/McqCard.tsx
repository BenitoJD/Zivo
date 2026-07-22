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

import { Box, Button, Checkbox, Group, Radio, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconBulb, IconCheck } from "@tabler/icons-react";
import { normalizeMcqOptions } from "@/lib/types";
import { useIsDark } from "@/lib/useIsDark";

export type GradeState = { correct: boolean; correctIndex: number; correctIndices?: number[] } | null;

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
    // A decisive, on-brand selection - a present lavender tint and a saturated
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
  // The explanation is the payoff after answering. Premium treatment: a self-
  // contained card with a tinted status header (icon + verdict) and a clean,
  // readable body - left-aligned prose, generous line-height.
  const paragraphs = feedback
    .split("\n\n")
    .map((p) => p.trim())
    .filter(Boolean);

  const tone = isCorrect ? "sage" : "lavender";
  const verdict = isCorrect ? "Correct" : "Here's why";

  return (
    <Box
      className="mcq-feedback"
      style={{ flexShrink: 0, padding: compact ? "6px 2px 0" : "10px 2px 0" }}
    >
      <style>{`
        @keyframes mcq-fb { from { opacity: 0; transform: translateY(6px) scale(0.995); } to { opacity: 1; transform: none; } }
        .mcq-feedback { animation: mcq-fb 420ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .mcq-feedback { animation: none !important; } }
      `}</style>
      <Box
        style={{
          maxWidth: 640,
          marginInline: "auto",
          textAlign: "left",
          borderRadius: 18,
          overflow: "hidden",
          background: isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-body)",
          border: `1px solid ${isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-2)"}`,
          boxShadow: isDark ? "none" : "0 6px 24px -16px rgba(20,18,40,0.35)",
        }}
      >
        <Group
          gap={10}
          wrap="nowrap"
          align="center"
          px={compact ? 14 : 18}
          py={compact ? 9 : 11}
          style={{
            background: isDark ? `var(--mantine-color-${tone}-2)` : `var(--mantine-color-${tone}-0)`,
            borderBottom: `1px solid var(--mantine-color-${tone}-${isDark ? 4 : 2})`,
          }}
        >
          <ThemeIcon
            radius="xl"
            size={compact ? 24 : 28}
            variant="filled"
            color={tone}
            style={{ flexShrink: 0 }}
          >
            {isCorrect ? <IconCheck size={15} stroke={2.6} /> : <IconBulb size={15} stroke={2.2} />}
          </ThemeIcon>
          <Text
            fw={700}
            fz={compact ? "sm" : "md"}
            c={isDark ? `var(--mantine-color-${tone}-9)` : `var(--mantine-color-${tone}-8)`}
            style={{ letterSpacing: "-0.01em" }}
          >
            {verdict}
          </Text>
        </Group>
        <Box px={compact ? 14 : 18} py={compact ? 12 : 16}>
          {paragraphs.map((p, i) => (
            <Text
              key={i}
              c="var(--mantine-color-text)"
              mt={i === 0 ? 0 : "sm"}
              style={{ lineHeight: 1.7, fontSize: compact ? "0.92rem" : "1.02rem" }}
            >
              {p}
            </Text>
          ))}
        </Box>
      </Box>
    </Box>
  );
}

export function McqCard({
  stem,
  options,
  selected,
  onSelect,
  multiSelect = false,
  selectedIndices,
  onToggle,
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
  /** Multi-select ("select all that apply") mode. */
  multiSelect?: boolean;
  selectedIndices?: number[];
  onToggle?: (index: number) => void;
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
  const isDark = useIsDark();
  const safeOptions = normalizeMcqOptions(options);
  const graded = gradeState !== null;
  const optionsLocked = graded && (mode === "test" || gradeState.correct);
  const multiChosen = selectedIndices ?? [];
  const hasSelection = multiSelect ? multiChosen.length > 0 : selected !== null;
  const correctSet = gradeState?.correctIndices;

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
          whiteSpace: "pre-line", // statement/matching/code stems arrive with \n line breaks
        }}
      >
        {stem}
      </Title>

      {multiSelect && !graded ? (
        <Text fz="xs" fw={600} c="dimmed" ta="center" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
          Select all that apply
        </Text>
      ) : null}
      <OptionGroup
        multiSelect={multiSelect}
        selected={selected}
        multiChosen={multiChosen}
        optionsLocked={optionsLocked}
        onSelect={onSelect}
        onToggle={onToggle}
      >
        <Stack gap={compact ? 6 : 8} mih={0}>
          {safeOptions.map((opt, i) => {
            const value = String(i);
            const isSelected = multiSelect ? multiChosen.includes(i) : selected === value;
            const isCorrectOption =
              graded && (multiSelect && correctSet ? correctSet.includes(i) : gradeState.correctIndex === i);
            const isWrongSelected = graded && !gradeState.correct && isSelected && !isCorrectOption;
            const { border: borderColor, background, borderWidth } = mcqOptionChrome(
              isDark,
              { isSelected, isCorrectOption, isWrongSelected },
            );
            const ItemComp = multiSelect ? Checkbox : Radio;
            return (
              <ItemComp
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
                  input: { marginTop: 4 },
                }}
              />
            );
          })}
        </Stack>
      </OptionGroup>

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
            disabled={!hasSelection}
          >
            Check answer
          </Button>
        )}
      </Stack>
    </Stack>
  );
}

/** Wraps the option list in a Radio.Group (single) or Checkbox.Group (multi),
 *  keeping the shared option-row rendering identical for both. */
function OptionGroup({
  multiSelect,
  selected,
  multiChosen,
  optionsLocked,
  onSelect,
  onToggle,
  children,
}: {
  multiSelect: boolean;
  selected: string | null;
  multiChosen: number[];
  optionsLocked: boolean;
  onSelect: (value: string) => void;
  onToggle?: (index: number) => void;
  children: React.ReactNode;
}) {
  if (multiSelect) {
    return (
      <Checkbox.Group
        value={multiChosen.map(String)}
        onChange={(values) => {
          if (optionsLocked || !onToggle) return;
          // Checkbox.Group hands back the full set; diff against current to fire
          // one toggle for the option that changed.
          const next = new Set(values.map(Number));
          const prev = new Set(multiChosen);
          for (const i of next) if (!prev.has(i)) onToggle(i);
          for (const i of prev) if (!next.has(i)) onToggle(i);
        }}
      >
        {children}
      </Checkbox.Group>
    );
  }
  return (
    <Radio.Group
      value={selected}
      onChange={(value) => {
        if (optionsLocked) return;
        onSelect(value);
      }}
      name="mcq-options"
    >
      {children}
    </Radio.Group>
  );
}
