"use client";

import { Button, Center, Group, Paper, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconFileText, IconUpload } from "@tabler/icons-react";
import { useWorkspaceShell } from "@/app/workspace/layout";

export default function WorkspaceIndexPage() {
  const { openAddSource } = useWorkspaceShell();

  return (
    <Center mih="calc(100dvh - 2rem)">
      <Paper withBorder p={{ base: "xl", sm: 48 }} radius="lg" maw={520} w="100%">
        <Stack align="center" gap="lg">
          <ThemeIcon size={56} radius="md" variant="light" color="gray">
            <IconFileText size={28} stroke={1.25} />
          </ThemeIcon>
          <Stack gap="xs" align="center">
            <Title order={2} fw={600} ta="center">
              Add a source
            </Title>
            <Text c="dimmed" ta="center" size="sm" lh={1.7} maw={380}>
              Upload a PDF, article, or notes. Practice questions stay scoped to the material you
              choose.
            </Text>
          </Stack>
          <Button
            variant="white"
            c="dark.9"
            size="md"
            radius="md"
            leftSection={<IconUpload size={16} stroke={1.75} />}
            onClick={openAddSource}
          >
            Add source
          </Button>
          <Group gap="xs" justify="center">
            <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
              PDF
            </Text>
            <Text size="xs" c="dimmed">
              ·
            </Text>
            <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
              Word
            </Text>
            <Text size="xs" c="dimmed">
              ·
            </Text>
            <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
              URL
            </Text>
            <Text size="xs" c="dimmed">
              ·
            </Text>
            <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
              Paste
            </Text>
          </Group>
        </Stack>
      </Paper>
    </Center>
  );
}
