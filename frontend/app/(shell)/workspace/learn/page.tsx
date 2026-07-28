"use client";

/**
 * Admin: SEO /learn cook kill-switch + unpublish recent posts.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  Center,
  Group,
  Loader,
  NumberInput,
  Paper,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { apiPatch, isApiAccessDenied } from "@/lib/api/client";
import {
  queryKeys,
  useSeoLearnAdminPostsQuery,
  useSeoLearnSettingsQuery,
  useSessionQuery,
  type SeoLearnAdminPost,
  type SeoLearnSettings,
} from "@/lib/api/queries";

export default function SeoLearnAdminPage() {
  const qc = useQueryClient();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);
  const settingsQ = useSeoLearnSettingsQuery(isAdmin);
  const postsQ = useSeoLearnAdminPostsQuery(isAdmin);
  const [busy, setBusy] = useState(false);
  const [postBusy, setPostBusy] = useState<string | null>(null);
  const [softMaxEdit, setSoftMaxEdit] = useState<number | undefined>(undefined);

  const forbidden =
    !isAdmin || (settingsQ.isError && isApiAccessDenied(settingsQ.error));

  const cookEnabled = settingsQ.data?.cook_enabled ?? false;
  const softMax = softMaxEdit ?? settingsQ.data?.soft_max_per_day ?? 20;
  const posts = postsQ.data?.items ?? [];

  async function patchSettings(patch: Partial<SeoLearnSettings>) {
    setBusy(true);
    try {
      const updated = await apiPatch<SeoLearnSettings>("/api/learn/admin/settings", patch);
      qc.setQueryData(queryKeys.seoLearnSettings(), updated);
      setSoftMaxEdit(undefined);
      notifications.show({
        title: "Learn cook",
        message: "Settings saved",
        color: "sage",
      });
    } catch (e) {
      notifications.show({
        title: "Learn cook",
        message: e instanceof Error ? e.message : "Update failed",
        color: "terracotta",
      });
    } finally {
      setBusy(false);
    }
  }

  async function setPostStatus(post: SeoLearnAdminPost, status: "published" | "unpublished") {
    setPostBusy(post.id);
    try {
      await apiPatch(`/api/learn/admin/posts/${post.id}`, { status });
      void qc.invalidateQueries({ queryKey: queryKeys.seoLearnAdminPosts() });
      notifications.show({
        title: status === "unpublished" ? "Unpublished" : "Published",
        message: post.title,
        color: "sage",
      });
    } catch (e) {
      notifications.show({
        title: "Post",
        message: e instanceof Error ? e.message : "Update failed",
        color: "terracotta",
      });
    } finally {
      setPostBusy(null);
    }
  }

  if (session.isLoading || (isAdmin && settingsQ.isLoading)) {
    return (
      <Center mih={320}>
        <Loader color="lavender" />
      </Center>
    );
  }

  if (forbidden) {
    return (
      <Center mih={320}>
        <Text c="dimmed">Admin only.</Text>
      </Center>
    );
  }

  return (
    <ScrollViewport>
      <Box p={{ base: "md", md: "xl" }} maw={720} mx="auto" w="100%">
      <Stack gap="xl">
        <Stack gap="xs">
          <Title order={2} ff="var(--font-serif)" fw={500}>
            Learn content
          </Title>
          <Text size="sm" c="dimmed">
            Auto-cook public /learn posts. Kill-switch off by default until smoke-tested.
          </Text>
        </Stack>

        <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
          <Stack gap="md">
            <Switch
              label="Cook enabled"
              description="When on, ETA schedules cook candidates under the soft daily max."
              checked={cookEnabled}
              disabled={busy}
              onChange={(e) => void patchSettings({ cook_enabled: e.currentTarget.checked })}
            />
            <Group align="flex-end">
              <NumberInput
                label="Soft max per day"
                value={softMax}
                min={1}
                max={100}
                onChange={(v) => setSoftMaxEdit(typeof v === "number" ? v : undefined)}
                w={160}
              />
              <Button
                radius="xl"
                variant="light"
                color="lavender"
                loading={busy}
                onClick={() => void patchSettings({ soft_max_per_day: softMax })}
              >
                Save max
              </Button>
            </Group>
          </Stack>
        </Paper>

        <Stack gap="sm">
          <Title order={3} ff="var(--font-serif)" fw={500} size="h4">
            Recent posts
          </Title>
          {postsQ.isLoading ? (
            <Loader size="sm" color="lavender" />
          ) : posts.length === 0 ? (
            <Text size="sm" c="dimmed">
              No posts yet.
            </Text>
          ) : (
            posts.map((p) => (
              <Paper key={p.id} radius="md" p="md" withBorder bg="gray.0">
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <Stack gap={4} style={{ minWidth: 0 }}>
                    <Text fw={600} lineClamp={1}>
                      {p.title}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {p.status} · {p.stream} · {p.source_kind} · {p.author_name}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace">
                      /learn/{p.slug}
                    </Text>
                  </Stack>
                  <Group gap="xs" style={{ flexShrink: 0 }}>
                    {p.status === "published" ? (
                      <Button
                        size="xs"
                        radius="xl"
                        variant="light"
                        color="terracotta"
                        loading={postBusy === p.id}
                        onClick={() => void setPostStatus(p, "unpublished")}
                      >
                        Unpublish
                      </Button>
                    ) : (
                      <Button
                        size="xs"
                        radius="xl"
                        variant="light"
                        color="sage"
                        loading={postBusy === p.id}
                        onClick={() => void setPostStatus(p, "published")}
                      >
                        Publish
                      </Button>
                    )}
                  </Group>
                </Group>
              </Paper>
            ))
          )}
        </Stack>
      </Stack>
      </Box>
    </ScrollViewport>
  );
}
