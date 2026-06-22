"use client";

import { Stack, Text, Title } from "@mantine/core";

export default function WorkspaceIndexPage() {
  return (
    <Stack align="center" justify="center" mih="70vh" gap="lg">
      <Title order={2} ta="center">
        Add a source
      </Title>
      <Text c="dimmed" ta="center" maw={360}>
        PDF, article, or notes — use{" "}
        <Text span fw={700} inherit>
          Add source
        </Text>{" "}
        in the sidebar to start.
      </Text>
    </Stack>
  );
}
