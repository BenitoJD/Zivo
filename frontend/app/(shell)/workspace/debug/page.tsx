"use client";

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
import { IconBug, IconPlus, IconSeedling } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useRouter } from "next/navigation";
import {
  useDebugAdminQuery,
  useDebugCurateActions,
  useDebugPublicQuery,
  useSessionQuery,
  type DebugScenarioListItem,
} from "@/lib/api/queries";

export default function WorkspaceDebugBankPage() {
  const router = useRouter();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);
  const [showDrafts, setShowDrafts] = useState(false);

  const publicQuery = useDebugPublicQuery({});
  const adminQuery = useDebugAdminQuery(isAdmin);
  const actions = useDebugCurateActions();
  const [seeding, setSeeding] = useState(false);

  const items: DebugScenarioListItem[] = isAdmin
    ? (adminQuery.data?.items ?? []).filter((i) => showDrafts || i.published !== false)
    : (publicQuery.data?.items ?? []);

  const loading = isAdmin ? adminQuery.isLoading : publicQuery.isLoading;

  async function handleSeed() {
    setSeeding(true);
    try {
      const res = await actions.seed();
      actions.invalidate();
      notifications.show({
        title: "Starter scenarios seeded",
        message: `Created ${res.created}, updated ${res.updated}.`,
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
                <IconBug size={20} />
              </ThemeIcon>
              <Box>
                <Title order={3} ff="var(--font-serif)" fw={500}>
                  Debug diagnostics
                </Title>
                <Text c="dimmed" fz="sm">
                  Cook and curate diagnostic scenarios. Learners read cases and answer what is wrong.
                </Text>
              </Box>
            </Group>
            <Group gap="xs">
              <Button
                size="xs"
                variant="light"
                radius="xl"
                onClick={() => router.push("/workspace/debug/material")}
              >
                Cook material
              </Button>
              {isAdmin ? (
                <>
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
                    onClick={() => router.push("/workspace/debug/new")}
                  >
                    New scenario
                  </Button>
                </>
              ) : null}
            </Group>
          </Group>

          {isAdmin ? (
            <Switch
              label="Show drafts and unpublished"
              checked={showDrafts}
              onChange={(e) => setShowDrafts(e.currentTarget.checked)}
            />
          ) : null}

          {loading ? (
            <Text c="dimmed">Loading…</Text>
          ) : items.length === 0 ? (
            <Text c="dimmed">No scenarios yet.</Text>
          ) : (
            <Stack gap="sm">
              {items.map((item) => (
                <UnstyledButton
                  key={item.id}
                  onClick={() =>
                    isAdmin
                      ? router.push(`/workspace/debug/${item.id}/edit`)
                      : router.push(`/practice/debug/${item.id}`)
                  }
                >
                  <Paper p="md" radius="xl" withBorder bg="gray.0">
                    <Group justify="space-between">
                      <Box>
                        <Text fw={600}>{item.title}</Text>
                        <Group gap={6} mt={4}>
                          <Badge size="xs">{item.difficulty}</Badge>
                          {isAdmin && item.review_status ? (
                            <Badge size="xs" variant="outline">
                              {item.review_status}
                            </Badge>
                          ) : null}
                          {!item.published && isAdmin ? (
                            <Badge size="xs" color="terracotta" variant="light">
                              draft
                            </Badge>
                          ) : null}
                        </Group>
                      </Box>
                      <Text size="xs" c="dimmed">
                        {item.step_count} steps
                      </Text>
                    </Group>
                  </Paper>
                </UnstyledButton>
              ))}
            </Stack>
          )}
        </Stack>
      </Box>
    </ScrollArea>
  );
}
