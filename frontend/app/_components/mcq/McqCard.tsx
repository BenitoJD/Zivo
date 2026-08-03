"use client";

/**
 * Shared MCQ chrome primitives used by Learn/Test `McqHeroPanel`.
 *
 * Interactive MCQ surfaces must render `McqHeroPanel` (or `McqReviewView`) —
 * do not fork a second option/feedback UI. This module only exports the
 * visual tokens + feedback card those panels share.
 */

import { Box, Group, Text, ThemeIcon } from "@mantine/core";
import { IconBulb, IconCheck } from "@tabler/icons-react";

/**
 * Shared reading measure for McqHeroPanel / McqReviewView / feedback.
 * Wide enough for long newspaper stems + option cards; not full-bleed.
 * Mobile stays fluid via `min(MCQ_CONTENT_MAX, 100%)`.
 */
export const MCQ_CONTENT_MAX = 800;

/** Fixed stem size — no vw/clamp so every question reads at the same measure. */
export const MCQ_STEM_FONT_SIZE = {
  compact: "1.125rem",
  default: "1.3125rem",
} as const;

/** Option body text — paired with stem tokens for consistent MCQ chrome. */
export const MCQ_OPTION_FONT_SIZE = {
  compact: "0.9375rem",
  default: "1.0625rem",
} as const;

/** Lets learners highlight stem/option copy for Ask / Explain / Dictionary / Wikipedia. */
export const MCQ_SELECTABLE_TEXT_STYLE = {
  userSelect: "text",
  WebkitUserSelect: "text",
} as const;

export function mcqHasTextSelection(): boolean {
  return Boolean(window.getSelection()?.toString().trim());
}

export type GradeState = { correct: boolean; correctIndex: number; correctIndices?: number[] } | null;

export type McqOptionVisualState = {
  isSelected: boolean;
  isCorrectOption: boolean;
  isWrongSelected: boolean;
};

/**
 * Option chrome shared by McqHeroPanel / McqReviewView.
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
  let chipColor = isDark ? "var(--mantine-color-gray-6)" : "var(--mantine-color-gray-7)";
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
          maxWidth: MCQ_CONTENT_MAX,
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
              style={{
                lineHeight: 1.7,
                fontSize: compact ? "0.92rem" : "1.02rem",
                ...MCQ_SELECTABLE_TEXT_STYLE,
              }}
            >
              {p}
            </Text>
          ))}
        </Box>
      </Box>
    </Box>
  );
}
