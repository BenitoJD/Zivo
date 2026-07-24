"use client";

/**
 * Workspace coding bank hub — browse published problems; admins curate/seed.
 * Distinct route from per-source Coding study mode (`CodingView`).
 */

import { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Group,
  Paper,
  ScrollArea,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
  Switch,
} from "@mantine/core";
import { IconCheck, IconCode, IconPlus, IconSeedling } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useRouter } from "next/navigation";
import {
  useCodingAdminQuery,
  useCodingCurateActions,
  useCodingPublicQuery,
  useSessionQuery,
  type CodingProblemListItem,
} from "@/lib/api/queries";

export default function WorkspaceCodingBankPage() {
  const router = useRouter();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);
  const [showDrafts, setShowDrafts] = useState(false);

  const publicQuery = useCodingPublicQuery({});
  const adminQuery = useCodingAdminQuery(isAdmin);
  const actions = useCodingCurateActions();
  const [seeding, setSeeding] = useState(false);

  const items: CodingProblemListItem[] = isAdmin
    ? (adminQuery.data?.items ?? []).filter((i) => showDrafts || i.published !== false)
    : (publicQuery.data?.items ?? []);

  const loading = isAdmin ? adminQuery.isLoading : publicQuery.isLoading;
  const forbidden =
    isAdmin &&
    adminQuery.isError &&
    (adminQuery.error instanceof Error
      ? adminQuery.error.message.toLowerCase().includes("admin")
      : false);

  async function handleSeed() {
    setSeeding(true);
    try {
      const res = await actions.seed();
      actions.invalidate();
      notifications.show({
        title: "Starter bank seeded",
        message: `Created ${res.created}, updated ${res.updated} (${res.total} total).`,
        color: "sage",
      });
    } catch (e) {
      notifications.show({
        title: "Seed failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "terracotta",
      });
    } finally {
      setSeeding(false);
    }
  }

  return (
    <ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={880} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <Stack gap="md">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
            <Group gap="sm">
              <ThemeIcon variant="light" color="lavender" size={40} radius="xl">
                <IconCode size={20} />
              </ThemeIcon>
              <Box>
                <Title order={3} ff="var(--font-serif)" fw={500}>
                  Coding bank
                </Title>
                <Text c="dimmed" fz="sm">
                  LeetCode-style problem bank. Practice here or from /practice/coding.
                </Text>
              </Box>
            </Group>
            {isAdmin ? (
              <Group gap="xs">
                <Button
                  size="xs"
                  variant="light"
                  radius="xl"
                  leftSection={<IconSeedling size={14} />}
                  loading={seeding}
                  onClick={() => void handleSeed()}
                >
                  Seed starters
                </Button>
                <Button
                  size="xs"
                  color="lavender"
                  radius="xl"
                  leftSection={<IconPlus size={14} />}
                  onClick={() => router.push("/workspace/coding/new")}
                >
                  New problem
                </Button>
              </Group>
            ) : null}
          </Group>

          {isAdmin ? (
            <Switch
              size="xs"
              label="Show unpublished drafts"
              checked={showDrafts}
              onChange={(e) => setShowDrafts(e.currentTarget.checked)}
            />
          ) : null}

          {forbidden ? (
            <Paper radius="lg" p="xl" withBorder ta="center">
              <Text>Admin required to curate.</Text>
            </Paper>
          ) : loading ? (
            <Text c="dimmed">Loading…</Text>
          ) : items.length === 0 ? (
            <Paper radius="lg" p="xl" withBorder ta="center">
              <Text ff="var(--font-serif)" fz={22} fw={500} mb={4}>
                Empty bank
              </Text>
              <Text c="dimmed" fz="sm" maw={420} mx="auto" mb="md">
                {isAdmin
                  ? "Seed the classic starter set, or author a new problem."
                  : "No published problems yet."}
              </Text>
              {isAdmin ? (
                <Button radius="xl" color="lavender" loading={seeding} onClick={() => void handleSeed()}>
                  Seed starter problems
                </Button>
              ) : null}
            </Paper>
          ) : (
            <Stack gap="xs">
              {items.map((item) => (
                <BankRow
                  key={item.id}
                  item={item}
                  isAdmin={isAdmin}
                  onSolve={() => router.push(`/practice/coding/${item.id}`)}
                  onEdit={() => router.push(`/workspace/coding/${item.id}/edit`)}
                />
              ))}
            </Stack>
          )}
        </Stack>
      </Box>
    </ScrollArea>
  );
}

function BankRow({
  item,
  isAdmin,
  onSolve,
  onEdit,
}: {
  item: CodingProblemListItem;
  isAdmin: boolean;
  onSolve: () => void;
  onEdit: () => void;
}) {
  const color = item.difficulty === "easy" ? "sage" : item.difficulty === "hard" ? "terracotta" : "lavender";
  return (
    <Paper radius="lg" p="md" withBorder>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <UnstyledButton onClick={onSolve} style={{ textAlign: "left", flex: 1, minWidth: 0 }}>
          <Group gap={8} wrap="nowrap">
            {item.status === "solved" ? (
              <ThemeIcon size={18} radius="xl" color="sage" variant="light">
                <IconCheck size={12} />
              </ThemeIcon>
            ) : null}
            <Box style={{ minWidth: 0 }}>
              <Text fw={600} ff="var(--font-serif)" truncate>
                {(item.title?.trim().length ?? 0) >= 2
                  ? item.title
                  : (item.concept?.trim().length ?? 0) >= 2
                    ? item.concept
                    : "Untitled problem"}
              </Text>
              <Group gap={6} mt={4}>
                <Badge size="sm" variant="light" color={color} tt="capitalize">
                  {item.difficulty}
                </Badge>
                {item.origin ? (
                  <Badge size="sm" variant="outline" color="gray">
                    {item.origin}
                  </Badge>
                ) : null}
                {item.published === false ? (
                  <Badge size="sm" variant="light" color="terracotta">
                    draft
                  </Badge>
                ) : null}
                {(item.tags ?? []).slice(0, 3).map((t) => (
                  <Badge key={t} size="sm" variant="light" color="gray">
                    {t}
                  </Badge>
                ))}
              </Group>
            </Box>
          </Group>
        </UnstyledButton>
        <Group gap={6}>
          <Button size="xs" variant="light" radius="xl" onClick={onSolve}>
            Solve
          </Button>
          {isAdmin ? (
            <Button size="xs" variant="subtle" radius="xl" onClick={onEdit}>
              Edit
            </Button>
          ) : null}
        </Group>
      </Group>
    </Paper>
  );
}
