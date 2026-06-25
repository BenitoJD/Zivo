"use client";

import { Box, Button, Center, Paper, Stack, Text, Title } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <Center mih="100dvh" p="md" bg="var(--mantine-color-body)">
      <Paper
        radius="xl"
        p={{ base: "xl", md: 48 }}
        shadow="paper"
        bg="gray.0"
        maw={460}
        w="100%"
        style={{
          position: "relative",
          overflow: "hidden",
          border: "1px solid var(--mantine-color-default-border)",
          boxShadow: "var(--mantine-shadow-paper-lg)",
        }}
      >
        <Box
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            zIndex: 0,
            background: "radial-gradient(circle 350px at 50% 120%, rgba(220, 100, 80, 0.04), transparent 75%)",
          }}
        />
        <Stack align="center" gap={14} ta="center" style={{ position: "relative", zIndex: 1 }}>
          <Text size="xs" fw={600} tt="uppercase" lts={2} c="terracotta.7">
            Something broke
          </Text>
          <Title
            order={2}
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontWeight: 500,
              letterSpacing: "-0.01em",
            }}
          >
            That wasn&apos;t supposed to happen.
          </Title>
          <Text size="sm" c="gray.6" ta="center" lh={1.6}>
            {error.message || "An unexpected error occurred."}
          </Text>
          <Button onClick={reset} leftSection={<IconRefresh size={16} stroke={1.75} />}>
            Try again
          </Button>
        </Stack>
      </Paper>
    </Center>
  );
}
