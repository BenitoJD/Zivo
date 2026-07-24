"use client";

/**
 * Admin: swap the Telegram channel that feeds Newspaper ingest.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  Center,
  Loader,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { apiPatch } from "@/lib/api/client";
import {
  queryKeys,
  useNewspaperChannelQuery,
  type NewspaperChannel,
} from "@/lib/api/queries";

export default function NewspaperAdminPage() {
  const qc = useQueryClient();
  const channelQ = useNewspaperChannelQuery();
  // undefined = show server value; string = user edited
  const [refEdit, setRefEdit] = useState<string | undefined>(undefined);
  const [labelEdit, setLabelEdit] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  const forbidden =
    channelQ.isError &&
    (channelQ.error instanceof Error
      ? channelQ.error.message.toLowerCase().includes("admin")
      : false);

  const ref = refEdit ?? channelQ.data?.channel_ref ?? "";
  const label = labelEdit ?? channelQ.data?.channel_label ?? "";

  async function save() {
    setBusy(true);
    try {
      const updated = await apiPatch<NewspaperChannel>("/api/newspaper/admin/channel", {
        channel_ref: ref.trim(),
        channel_label: label.trim(),
      });
      qc.setQueryData(queryKeys.newspaperChannel(), updated);
      setRefEdit(undefined);
      setLabelEdit(undefined);
      notifications.show({
        title: "Channel updated",
        message: "Ingest will follow the new channel. Cursor reset.",
        color: "sage",
      });
    } catch (e) {
      notifications.show({
        title: "Channel",
        message: e instanceof Error ? e.message : "Update failed",
        color: "terracotta",
      });
    } finally {
      setBusy(false);
    }
  }

  if (channelQ.isLoading) {
    return (
      <Center mih={240}>
        <Loader color="lavender" />
      </Center>
    );
  }

  if (forbidden) {
    return (
      <Center mih={240}>
        <Text c="dimmed">Admin only.</Text>
      </Center>
    );
  }

  return (
    <Box p={{ base: "md", md: "xl" }} maw={560}>
      <Stack gap="lg">
        <Stack gap={4}>
          <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8">
            Newspaper
          </Text>
          <Title order={2} ff="var(--font-serif)" fw={500}>
            Source channel
          </Title>
        </Stack>
        <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
          <Stack gap="md">
            <Text size="sm" c="dimmed">
              Paste a channel @username, invite link, or numeric id. No redeploy needed —
              the ingest worker re-reads this on the next loop.
            </Text>
            <TextInput
              label="Channel"
              placeholder="@mychannel or -100…"
              value={ref}
              onChange={(e) => setRefEdit(e.currentTarget.value)}
              radius="md"
            />
            <TextInput
              label="Label (optional)"
              placeholder="My newspaper drop"
              value={label}
              onChange={(e) => setLabelEdit(e.currentTarget.value)}
              radius="md"
            />
            {channelQ.data?.updated_at ? (
              <Text size="xs" c="dimmed">
                Last updated {channelQ.data.updated_at}
                {channelQ.data.sync_cursor != null
                  ? ` · cursor ${channelQ.data.sync_cursor}`
                  : ""}
              </Text>
            ) : null}
            <Button
              radius="xl"
              loading={busy}
              disabled={!ref.trim()}
              onClick={() => void save()}
            >
              Save channel
            </Button>
          </Stack>
        </Paper>
      </Stack>
    </Box>
  );
}
