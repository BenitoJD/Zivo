"use client";

import { useState } from "react";
import {
  Badge,
  Box,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  UnstyledButton,
} from "@mantine/core";
import {
  IconCode,
  IconCheck,
  IconPlayerPlay,
} from "@tabler/icons-react";
import {
  useCodingWorkspaceQuery,
  useCodingProblemQuery,
  type CodingProblemListItem,
} from "@/lib/api/queries";
import { CodeEditor } from "@/app/_components/coding/CodeEditor";
import { WaitState } from "@/app/workspace/_components/WaitState";

/**
 * Coding study mode - LeetCode-style practice problems generated from this
 * source. Lists every coding assertion for the artifact with per-problem
 * solved/attempted status; selecting one opens the shared CodeEditor.
 *
 * Empty state is the *indexing* wait: coding problems appear only after page
 * triage flags a page as programmable and the generate.coding job runs the
 * verify gate (the LLM's reference solution must pass every hidden test). On a
 * non-programming source this stays empty permanently - that's correct.
 */
export function CodingView({ artifactId, compact = false }: { artifactId: string; compact?: boolean }) {
  const { data, isError } = useCodingWorkspaceQuery(artifactId);
  const items = data?.items ?? [];

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const problemQuery = useCodingProblemQuery(selectedId, Boolean(selectedId));

  if (isError) {
    return (
      <Stack align="center" gap="sm" py="xl" ta="center">
        <Text ff="var(--font-serif)" fz={20} fw={500}>Couldn&rsquo;t load coding problems</Text>
        <Text c="dimmed">Try again in a moment.</Text>
      </Stack>
    );
  }

  if (!data) {
    return <WaitState pet title="Scanning for programmable pages" body="Looking for content a coding challenge could come from…" />;
  }

  if (items.length === 0) {
    return (
      <Stack align="center" gap="sm" py="xl" ta="center" maw={520}>
        <ThemeIcon variant="light" color="lavender" size={48} radius="xl">
          <IconCode size={24} />
        </ThemeIcon>
        <Text ff="var(--font-serif)" fz={compact ? 22 : 28} fw={500}>No coding problems yet</Text>
        <Text c="dimmed">
          Zivo writes coding challenges from pages about programming, algorithms, or data
          structures. If this source covers those topics, problems will appear here shortly -
          otherwise this material is better suited to MCQs.
        </Text>
      </Stack>
    );
  }

  // Problem detail view
  if (selectedId && problemQuery.data) {
    return (
      <Stack gap="md">
        <UnstyledButton onClick={() => setSelectedId(null)} mb="xs">
          <Group gap={4}>
            <Text fz="sm" c="lavender.7">← Back to all problems</Text>
          </Group>
        </UnstyledButton>
        <CodeEditor
          problem={problemQuery.data}
          compact={compact}
          onSubmitted={() => {
            // The list's status badges refresh via the invalidate in useCodingActions.
          }}
        />
      </Stack>
    );
  }

  if (selectedId && problemQuery.isLoading) {
    return <WaitState pet title="Loading problem" body="Cracking open the statement…" />;
  }

  // Problem list view
  return (
    <Stack gap="md" pb="xl">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Box>
          <Text ff="var(--font-serif)" fz={compact ? 20 : 24} fw={500}>Coding practice</Text>
          <Text fz="sm" c="dimmed">Solve, run against sample cases, then submit against hidden tests.</Text>
        </Box>
        <Badge variant="light" color="gray" radius="sm">{items.length} problem{items.length === 1 ? "" : "s"}</Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
        {items.map((item) => (
          <ProblemCard key={item.id} item={item} onSelect={() => setSelectedId(item.id)} />
        ))}
      </SimpleGrid>
    </Stack>
  );
}

function ProblemCard({ item, onSelect }: { item: CodingProblemListItem; onSelect: () => void }) {
  const color = item.difficulty === "easy" ? "sage" : item.difficulty === "hard" ? "terracotta" : "lavender";
  return (
    <UnstyledButton onClick={onSelect} w="100%" style={{ textAlign: "left" }}>
      <Paper
        radius="lg"
        p="md"
        withBorder
        style={{
          borderColor: "var(--app-border, var(--mantine-color-gray-2))",
          transition: "border-color 160ms ease, transform 160ms ease",
        }}
      >
        <Group justify="space-between" wrap="wrap" gap="xs" mb={6}>
          <Text fw={600} fz="md" ff="var(--font-serif)">{item.title}</Text>
          <StatusChip status={item.status ?? "new"} />
        </Group>
        <Group gap={6}>
          <Badge variant="light" color={color} radius="sm" tt="capitalize">{item.difficulty}</Badge>
          <Badge variant="light" color="gray" radius="sm">{item.hidden_test_count} hidden tests</Badge>
        </Group>
        <Group gap={4} mt="sm">
          <IconPlayerPlay size={12} color="var(--mantine-color-dimmed)" />
          <Text fz="xs" c="dimmed">Open editor</Text>
        </Group>
      </Paper>
    </UnstyledButton>
  );
}

function StatusChip({ status }: { status: "new" | "solved" }) {
  if (status === "solved") {
    return (
      <Group gap={3}>
        <IconCheck size={13} color="var(--mantine-color-sage-6)" />
        <Text fz="xs" c="sage.7" fw={500}>Solved</Text>
      </Group>
    );
  }
  return <Text fz="xs" c="dimmed">New</Text>;
}
