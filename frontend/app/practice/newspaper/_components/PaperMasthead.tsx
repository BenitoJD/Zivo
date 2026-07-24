"use client";

import { Box, Group, Stack, Text, Title } from "@mantine/core";
import { IconNews } from "@tabler/icons-react";

/**
 * Light paper identity for the newspaper catalog (pick paper / pick day).
 * Serif masthead only — practice itself lives in Learn/Test.
 */
export function PaperMasthead({
  title,
  subtitle,
  compact = false,
}: {
  title: string;
  subtitle?: string;
  compact?: boolean;
}) {
  return (
    <Stack gap={compact ? 4 : "xs"} align="center" ta="center">
      <Group gap={8} justify="center" align="center">
        <Box
          c="lavender.7"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: compact ? 28 : 34,
            height: compact ? 28 : 34,
            borderRadius: 999,
            background: "var(--mantine-color-lavender-0)",
          }}
        >
          <IconNews size={compact ? 14 : 18} stroke={1.75} />
        </Box>
        <Text
          fz={10}
          fw={700}
          tt="uppercase"
          c="dimmed"
          style={{ letterSpacing: "0.14em" }}
        >
          Newspaper
        </Text>
      </Group>
      <Title
        order={compact ? 3 : 2}
        ff="var(--font-serif)"
        fw={500}
        style={{ letterSpacing: "-0.02em", lineHeight: 1.15 }}
      >
        {title}
      </Title>
      {subtitle ? (
        <Text c="dimmed" fz="sm" maw={420}>
          {subtitle}
        </Text>
      ) : null}
      <Box
        w={compact ? 48 : 72}
        h={2}
        mt={4}
        bg="lavender.3"
        style={{ borderRadius: 999 }}
        aria-hidden
      />
    </Stack>
  );
}
