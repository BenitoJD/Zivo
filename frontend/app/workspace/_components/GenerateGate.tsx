"use client";

import { Button, Center, Stack, Text, ThemeIcon } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";

/**
 * Per-source+mode "already generated" flag. Once a learner generates a tool for a
 * source we remember it so re-opening the mode auto-loads instead of re-prompting.
 * Key scheme: `zivo-gen-started:{artifactId}:{mode}` (e.g. `:notes`, `:cheatsheet`,
 * `:explain`, `:cards`, `:palace`).
 */
export function genStartedKey(artifactId: string, mode: string): string {
  return `zivo-gen-started:${artifactId}:${mode}`;
}

export function readGenStarted(artifactId: string, mode: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(genStartedKey(artifactId, mode)) === "1";
  } catch {
    return false;
  }
}

export function markGenStarted(artifactId: string, mode: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(genStartedKey(artifactId, mode), "1");
  } catch {
    /* ignore */
  }
}

/**
 * GenerateGate — a calm confirmation step shown before a study tool generates.
 *
 * The AI study tools (Explain, Notes, Flashcards, Memory Palace) are expensive to
 * produce, so instead of auto-generating the moment a mode opens, we show this short
 * intro card. Generation only starts when the learner clicks the button. Calm Paper
 * styling: tinted lavender icon chip, serif title, plain-language description, and a
 * single primary action.
 */
export function GenerateGate({
  icon,
  title,
  description,
  actionLabel = "Generate",
  onStart,
  compact = false,
}: {
  icon?: React.ReactNode;
  title: string;
  description: string;
  actionLabel?: string;
  onStart: () => void;
  compact?: boolean;
}) {
  return (
    <Center mih={compact ? 240 : 320}>
      <Stack align="center" gap="md" ta="center" maw={compact ? 360 : 440} px="md">
        <ThemeIcon variant="light" color="lavender" radius="xl" size={compact ? 52 : 64}>
          {icon ?? <IconSparkles size={compact ? 24 : 28} />}
        </ThemeIcon>
        <Stack gap={6}>
          <Text
            ff="var(--font-serif)"
            fz={compact ? 22 : 26}
            fw={500}
            c="var(--mantine-color-text)"
            lh={1.2}
          >
            {title}
          </Text>
          <Text c="dimmed" fz={compact ? "sm" : "md"} lh={1.5}>
            {description}
          </Text>
        </Stack>
        <Button
          color="lavender"
          radius="xl"
          size={compact ? "sm" : "md"}
          mt={4}
          leftSection={<IconSparkles size={compact ? 15 : 17} />}
          onClick={onStart}
        >
          {actionLabel}
        </Button>
      </Stack>
    </Center>
  );
}
