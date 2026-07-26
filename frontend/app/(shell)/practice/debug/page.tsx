"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Box,
  Container,
  Group,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  ThemeIcon,
  UnstyledButton,
  Anchor,
} from "@mantine/core";
import { IconBug, IconPlayerPlay } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import {
  useDebugPublicQuery,
  useSessionQuery,
  type DebugPublicFilters,
  type DebugScenarioListItem,
} from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";

export default function DebugPracticePage() {
  const router = useRouter();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);
  const [filters, setFilters] = useState<DebugPublicFilters>({});
  const { data, isLoading } = useDebugPublicQuery(filters);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const items = data?.items ?? [];

  return (
    <Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
            <LearnerPageHeader
              align="left"
              compact
              eyebrow="Debug diagnostics"
              title="Find what's wrong"
              subtitle="Read real failure cases. Diagnose root cause, pick the fix approach, reflect on process. No coding required."
            />
            {isAdmin ? (
              <Anchor href="/workspace/debug" fz="sm" c="lavender.7">
                Curate bank →
              </Anchor>
            ) : null}
          </Group>

          <SegmentedControl
            value={filters.difficulty ?? "all"}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                difficulty: v === "all" ? undefined : (v as DebugPublicFilters["difficulty"]),
              }))
            }
            data={[
              { label: "All", value: "all" },
              { label: "Easy", value: "easy" },
              { label: "Medium", value: "medium" },
              { label: "Hard", value: "hard" },
            ]}
            radius="xl"
          />

          {isLoading ? (
            <Text c="dimmed">Loading scenarios…</Text>
          ) : items.length === 0 ? (
            <Text c="dimmed">No published scenarios yet. Check back soon.</Text>
          ) : (
            <Stack gap="sm">
              {items.map((item) => (
                <ScenarioRow key={item.id} item={item} onOpen={() => router.push(`/practice/debug/${item.id}`)} />
              ))}
            </Stack>
          )}
        </Stack>
      </Container>
    </Shell>
  );
}

function ScenarioRow({ item, onOpen }: { item: DebugScenarioListItem; onOpen: () => void }) {
  return (
    <UnstyledButton onClick={onOpen}>
      <Paper p="md" radius="xl" withBorder bg="gray.0" shadow="paper">
        <Group justify="space-between" wrap="nowrap" gap="sm">
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0, flex: 1 }}>
            <ThemeIcon variant="light" color="lavender" radius="xl" size={36}>
              <IconBug size={18} />
            </ThemeIcon>
            <Box style={{ minWidth: 0 }}>
              <Text fw={600} ff="var(--font-serif)" truncate>
                {item.title}
              </Text>
              <Group gap={6} mt={4}>
                <Badge size="xs" variant="light">
                  {item.difficulty}
                </Badge>
                <Text size="xs" c="dimmed">
                  {item.step_count} steps · {item.scenario_type.replace(/_/g, " ")}
                </Text>
              </Group>
            </Box>
          </Group>
          <ThemeIcon variant="transparent" color="lavender">
            <IconPlayerPlay size={18} />
          </ThemeIcon>
        </Group>
      </Paper>
    </UnstyledButton>
  );
}
