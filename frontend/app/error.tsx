"use client";

import { Button, Center, Stack, Text, Title } from "@mantine/core";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <Center mih="100dvh" p="md">
      <Stack align="center" gap="md" maw={420}>
        <Title order={3}>Something went wrong</Title>
        <Text size="sm" c="dimmed" ta="center">
          {error.message || "An unexpected error occurred."}
        </Text>
        <Button onClick={reset}>Try again</Button>
      </Stack>
    </Center>
  );
}
