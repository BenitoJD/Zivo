"use client";

import type { ReactNode } from "react";
import { Center, Stack, Text, ThemeIcon } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";

/**
 * Shared loading / empty / error placeholder for the study-mode views
 * (Explain, Notes, Flashcards, Memory Palace, Quiz Builder). Centered icon +
 * serif title + dimmed body + optional action, in Calm Paper styling. Each view
 * passes its own `icon` (a Loader while generating, an alert on error); the
 * sparkles fallback only shows if a caller omits one.
 */
export function WaitState({
  icon,
  title,
  body,
  action,
}: {
  icon?: ReactNode;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <Center mih={280}>
      <Stack align="center" gap="sm" ta="center" maw={440}>
        <ThemeIcon variant="light" color="lavender" radius="xl" size={54}>
          {icon ?? <IconSparkles size={26} />}
        </ThemeIcon>
        <Text ff="var(--font-serif)" fz={24} fw={500} c="var(--mantine-color-text)">
          {title}
        </Text>
        <Text c="dimmed">{body}</Text>
        {action}
      </Stack>
    </Center>
  );
}
