"use client";

/**
 * Public coding practice sampler - browse LeetCode-style problems across all
 * sources (no login required). Anonymous visitors get a guest session so their
 * submits still record a measurement (against a guest entity).
 *
 * This is a thin discovery surface over the same problem bank the workspace
 * Coding study mode reads. Per-learner status (solved/attempted) only resolves
 * when logged in; for anonymous users every problem shows as "new".
 */

import { useEffect } from "react";
import {
  Badge,
  Box,
  Container,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { IconCode, IconPlayerPlay } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useCodingPublicQuery, type CodingProblemListItem } from "@/lib/api/queries";

export default function CodingPracticePage() {
  const router = useRouter();
  const { data, isLoading, isError } = useCodingPublicQuery();

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const items = data?.items ?? [];

  return (
    <Container size="md" py={{ base: 32, md: 56 }}>
      <Stack gap="md">
        <Group gap="sm" align="center">
          <ThemeIcon variant="light" color="lavender" size={44} radius="xl">
            <IconCode size={22} />
          </ThemeIcon>
          <Box>
            <Title order={2} ff="var(--font-serif)" fw={500}>Coding practice</Title>
            <Text c="dimmed" fz="sm">LeetCode-style problems generated from real study material. No login needed.</Text>
          </Box>
        </Group>

        {isLoading ? (
          <Text c="dimmed">Loading problems…</Text>
        ) : isError ? (
          <Text c="terracotta">Couldn&rsquo;t load problems right now.</Text>
        ) : items.length === 0 ? (
          <Paper radius="lg" p="xl" withBorder ta="center">
            <Text ff="var(--font-serif)" fz={22} fw={500} mb={4}>No problems yet</Text>
            <Text c="dimmed" fz="sm" maw={440} mx="auto">
              Coding challenges appear here once a user uploads programming material and Zivo
              generates verified problems from it. Check back soon.
            </Text>
          </Paper>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {items.map((item) => (
              <ProblemRow key={item.id} item={item} onOpen={() => router.push(`/practice/coding/${item.id}`)} />
            ))}
          </SimpleGrid>
        )}
      </Stack>
    </Container>
  );
}

function ProblemRow({ item, onOpen }: { item: CodingProblemListItem; onOpen: () => void }) {
  const color = item.difficulty === "easy" ? "sage" : item.difficulty === "hard" ? "terracotta" : "lavender";
  return (
    <UnstyledButton onClick={onOpen} w="100%" style={{ textAlign: "left" }}>
      <Paper radius="lg" p="md" withBorder style={{ borderColor: "var(--mantine-color-gray-2)" }}>
        <Group justify="space-between" wrap="wrap" gap="xs" mb={6}>
          <Text fw={600} fz="md" ff="var(--font-serif)">{item.title}</Text>
          <Badge variant="light" color={color} radius="sm" tt="capitalize">{item.difficulty}</Badge>
        </Group>
        <Group gap={6}>
          <Badge variant="light" color="gray" radius="sm">{item.hidden_test_count} hidden tests</Badge>
          <Group gap={4}>
            <IconPlayerPlay size={12} color="var(--mantine-color-dimmed)" />
            <Text fz="xs" c="dimmed">Solve</Text>
          </Group>
        </Group>
      </Paper>
    </UnstyledButton>
  );
}
