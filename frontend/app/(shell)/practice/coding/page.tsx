"use client";

/**
 * Public coding practice bank — browse curated + generated LeetCode-style problems.
 * Filters: difficulty / tag / solved status. Solve at /practice/coding/[id].
 */

import { useEffect, useState } from "react";
import {
  Badge,
  Box,
  Container,
  Group,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
  Chip,
  ScrollArea,
  Anchor,
} from "@mantine/core";
import { IconCheck, IconCode, IconPlayerPlay } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import {
  useCodingPublicQuery,
  useSessionQuery,
  type CodingProblemListItem,
  type CodingPublicFilters,
} from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";

export default function CodingPracticePage() {
  const router = useRouter();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);

  const [filters, setFilters] = useState<CodingPublicFilters>({});
  const { data, isLoading, isError } = useCodingPublicQuery(filters);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const items = data?.items ?? [];
  const tags = data?.tags ?? [];

  return (
    <Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
            <Group gap="sm" align="center">
              <ThemeIcon variant="light" color="lavender" size={44} radius="xl">
                <IconCode size={22} />
              </ThemeIcon>
              <Box>
                <Title order={2} ff="var(--font-serif)" fw={500}>
                  Coding practice
                </Title>
                <Text c="dimmed" fz="sm">
                  Curated problems + challenges from study material. Run tests, submit, earn the check.
                </Text>
              </Box>
            </Group>
            {isAdmin ? (
              <Anchor href="/workspace/coding" fz="sm" c="lavender.7">
                Curate bank →
              </Anchor>
            ) : null}
          </Group>

          <Stack gap="sm">
            <SegmentedControl
              value={filters.difficulty ?? "all"}
              onChange={(v) =>
                setFilters((f) => ({
                  ...f,
                  difficulty: v === "all" ? undefined : (v as CodingPublicFilters["difficulty"]),
                }))
              }
              data={[
                { label: "All", value: "all" },
                { label: "Easy", value: "easy" },
                { label: "Medium", value: "medium" },
                { label: "Hard", value: "hard" },
              ]}
              radius="xl"
              color="lavender"
            />
            <Group gap="xs" wrap="wrap">
              <SegmentedControl
                size="xs"
                value={filters.status ?? "all"}
                onChange={(v) =>
                  setFilters((f) => ({
                    ...f,
                    status: v === "all" ? undefined : (v as CodingPublicFilters["status"]),
                  }))
                }
                data={[
                  { label: "Any status", value: "all" },
                  { label: "Unsolved", value: "new" },
                  { label: "Solved", value: "solved" },
                ]}
                radius="xl"
              />
              <SegmentedControl
                size="xs"
                value={filters.origin ?? "all"}
                onChange={(v) =>
                  setFilters((f) => ({
                    ...f,
                    origin: v === "all" ? undefined : (v as CodingPublicFilters["origin"]),
                  }))
                }
                data={[
                  { label: "All sources", value: "all" },
                  { label: "Curated", value: "curated" },
                  { label: "From material", value: "generated" },
                ]}
                radius="xl"
              />
            </Group>
            {tags.length > 0 ? (
              <ScrollArea type="hover" offsetScrollbars>
                <Chip.Group
                  multiple={false}
                  value={filters.tag ?? ""}
                  onChange={(v) =>
                    setFilters((f) => ({
                      ...f,
                      tag: !v || v === filters.tag ? undefined : String(v),
                    }))
                  }
                >
                  <Group gap={6} wrap="nowrap">
                    {tags.map((t) => (
                      <Chip key={t} value={t} size="xs" radius="xl" variant="light" color="lavender">
                        {t}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
              </ScrollArea>
            ) : null}
          </Stack>

          {isLoading ? (
            <Text c="dimmed">Loading problems…</Text>
          ) : isError ? (
            <Text c="terracotta">Couldn&rsquo;t load problems right now.</Text>
          ) : items.length === 0 ? (
            <Paper radius="lg" p="xl" withBorder ta="center">
              <Text ff="var(--font-serif)" fz={22} fw={500} mb={4}>
                No problems yet
              </Text>
              <Text c="dimmed" fz="sm" maw={440} mx="auto">
                {isAdmin
                  ? "Seed the starter bank from Curate bank, or upload programming material to generate problems."
                  : "Coding challenges appear once the bank is seeded or someone uploads programming material."}
              </Text>
            </Paper>
          ) : (
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              {items.map((item) => (
                <ProblemRow
                  key={item.id}
                  item={item}
                  onOpen={() => router.push(`/practice/coding/${item.id}`)}
                />
              ))}
            </SimpleGrid>
          )}
        </Stack>
      </Container>
    </Shell>
  );
}

function ProblemRow({ item, onOpen }: { item: CodingProblemListItem; onOpen: () => void }) {
  const color = item.difficulty === "easy" ? "sage" : item.difficulty === "hard" ? "terracotta" : "lavender";
  const solved = item.status === "solved";
  return (
    <UnstyledButton onClick={onOpen} w="100%" style={{ textAlign: "left" }}>
      <Paper radius="lg" p="md" withBorder style={{ borderColor: "var(--mantine-color-gray-2)" }}>
        <Group justify="space-between" wrap="wrap" gap="xs" mb={6}>
          <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
            {solved ? (
              <ThemeIcon size={18} radius="xl" color="sage" variant="light">
                <IconCheck size={12} />
              </ThemeIcon>
            ) : null}
            <Text fw={600} fz="md" ff="var(--font-serif)" truncate>
              {(item.title?.trim().length ?? 0) >= 2
                ? item.title
                : (item.concept?.trim().length ?? 0) >= 2
                  ? item.concept
                  : "Untitled problem"}
            </Text>
          </Group>
          <Badge variant="light" color={color} radius="sm" tt="capitalize">
            {item.difficulty}
          </Badge>
        </Group>
        <Group gap={6} wrap="wrap">
          {item.origin === "curated" ? (
            <Badge variant="outline" color="gray" radius="sm">
              curated
            </Badge>
          ) : null}
          {(item.tags ?? []).slice(0, 3).map((t) => (
            <Badge key={t} variant="light" color="gray" radius="sm">
              {t}
            </Badge>
          ))}
          <Badge variant="light" color="gray" radius="sm">
            {item.hidden_test_count} tests
          </Badge>
          <Group gap={4}>
            <IconPlayerPlay size={12} color="var(--mantine-color-dimmed)" />
            <Text fz="xs" c="dimmed">
              {solved ? "Solved" : "Solve"}
            </Text>
          </Group>
        </Group>
      </Paper>
    </UnstyledButton>
  );
}
