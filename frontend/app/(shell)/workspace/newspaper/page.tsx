// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Admin: Telegram channel + which newspaper brands to cook.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Box, Button, Center, Group, Loader, Paper, Stack, Switch, Text, TextInput, Title, } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { apiPatch, isApiAccessDenied } from "@/lib/api/client";
import { queryKeys, useNewspaperBrandsQuery, useNewspaperChannelQuery, useSessionQuery, type NewspaperBrand, type NewspaperBrands, type NewspaperChannel, } from "@/lib/api/queries";
export default function NewspaperAdminPage() {
    const qc = useQueryClient();
    const session = useSessionQuery();
    const isAdmin = Boolean(session.data?.is_admin);
    const channelQ = useNewspaperChannelQuery(isAdmin);
    const brandsQ = useNewspaperBrandsQuery(isAdmin);
    // undefined = show server value; string = user edited
    const [refEdit, setRefEdit] = useState<string | undefined>(undefined);
    const [labelEdit, setLabelEdit] = useState<string | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    const [brandBusy, setBrandBusy] = useState<string | null>(null);
    const [modeBusy, setModeBusy] = useState(false);
    const forbidden = !isAdmin || (pick(Boolean(channelQ.isError), () => isApiAccessDenied(channelQ.error), () => channelQ.isError));
    const ref = refEdit ?? channelQ.data?.channel_ref ?? "";
    const label = labelEdit ?? channelQ.data?.channel_label ?? "";
    const allowlistOnly = brandsQ.data?.allowlist_only ?? channelQ.data?.allowlist_only ?? false;
    const brands = brandsQ.data?.brands ?? [];
    async function saveChannel() {
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
        }
        catch (e) {
            notifications.show({
                title: "Channel",
                message: choose(Boolean(e instanceof Error), e.message, "Update failed"),
                color: "terracotta",
            });
        }
        finally {
            setBusy(false);
        }
    }
    async function toggleAllowlist(next: boolean) {
        setModeBusy(true);
        try {
            const updated = await apiPatch<NewspaperChannel>("/api/newspaper/admin/allowlist", {
                allowlist_only: next,
            });
            qc.setQueryData(queryKeys.newspaperChannel(), (prev) => choose(Boolean(prev), { ...prev, ...updated }, updated));
            qc.setQueryData(queryKeys.newspaperBrands(), (prev: NewspaperBrands | undefined) => choose(Boolean(prev), { ...prev, allowlist_only: next }, prev));
            void qc.invalidateQueries({ queryKey: queryKeys.newspaperCatalog() });
            notifications.show({
                title: "Paper filter",
                message: choose(Boolean(next), "Only enabled papers will ingest and show.", "All discovered papers will ingest and show."),
                color: "sage",
            });
        }
        catch (e) {
            notifications.show({
                title: "Paper filter",
                message: choose(Boolean(e instanceof Error), e.message, "Update failed"),
                color: "terracotta",
            });
        }
        finally {
            setModeBusy(false);
        }
    }
    async function toggleBrand(brand: NewspaperBrand, enabled: boolean) {
        setBrandBusy(brand.slug);
        try {
            const updated = await apiPatch<NewspaperBrand>(`/api/newspaper/admin/brands/${encodeURIComponent(brand.slug)}`, { enabled });
            qc.setQueryData(queryKeys.newspaperBrands(), (prev: NewspaperBrands | undefined) => pick(Boolean(prev), () => ({
                ...prev,
                brands: prev.brands.map((b) => choose(Boolean(b.slug === updated.slug), { ...b, enabled: updated.enabled }, b)),
            }), () => prev));
            void qc.invalidateQueries({ queryKey: queryKeys.newspaperCatalog() });
        }
        catch (e) {
            notifications.show({
                title: brand.title,
                message: choose(Boolean(e instanceof Error), e.message, "Update failed"),
                color: "terracotta",
            });
        }
        finally {
            setBrandBusy(null);
        }
    }
    return pick(Boolean(session.isLoading || channelQ.isLoading || brandsQ.isLoading), () => (<Center flex={1} mih={240}>
        <Loader color="lavender"/>
      </Center>), () => pick(Boolean(forbidden), () => (<Center flex={1} mih={240}>
        <Text c="dimmed">Admin only.</Text>
      </Center>), () => (<ScrollViewport>
      <Box p={{ base: "md", md: "xl" }} maw={640} mx="auto" w="100%">
        <Stack gap="xl">
          <Stack gap={4}>
            <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8">
              Newspaper
            </Text>
            <Title order={2} ff="var(--font-serif)" fw={500}>
              Source &amp; papers
            </Title>
          </Stack>

          <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
            <Stack gap="md">
              <Text fw={600} ff="var(--font-serif)">
                Telegram channel
              </Text>
              <Text size="sm" c="dimmed">
                Paste numeric peer id (preferred), @username, or invite link. Example label:
                MyBookZon ENGLISH (PREMIUM). No redeploy needed.
              </Text>
              <TextInput label="Channel" placeholder="@mychannel or -100…" value={ref} onChange={(e) => setRefEdit(e.currentTarget.value)} radius="md"/>
              <TextInput label="Label (optional)" placeholder="MyBookZon ENGLISH (PREMIUM)" value={label} onChange={(e) => setLabelEdit(e.currentTarget.value)} radius="md"/>
              {choose(Boolean(channelQ.data?.updated_at), (<Text size="xs" c="dimmed">
                  Last updated {channelQ.data.updated_at}
                  {choose(Boolean(channelQ.data.sync_cursor != null), ` · cursor ${channelQ.data.sync_cursor}`, "")}
                </Text>), null)}
              <Button radius="xl" loading={busy} disabled={!ref.trim()} onClick={() => void saveChannel()}>
                Save channel
              </Button>
            </Stack>
          </Paper>

          <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
            <Stack gap="md">
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <Box>
                  <Text fw={600} ff="var(--font-serif)">
                    Papers you want
                  </Text>
                  <Text size="sm" c="dimmed">
                    Turn on “only selected” then enable papers. Filenames change — ingest uses LLM
                    to map TH/Mint/… then remembers aliases. New brands appear after a post (off by
                    default).
                  </Text>
                </Box>
                <Switch checked={allowlistOnly} onChange={(e) => void toggleAllowlist(e.currentTarget.checked)} disabled={modeBusy} label="Only selected" labelPosition="left"/>
              </Group>

              {pick(Boolean(brands.length === 0), () => (<Text size="sm" c="dimmed">
                  No papers discovered yet. After ingest sees a PDF, it appears here.
                </Text>), () => (<Stack gap="xs">
                  {brands.map((b) => (<Paper key={b.slug} radius="md" p="sm" withBorder bg="var(--mantine-color-body)">
                      <Group justify="space-between" wrap="nowrap">
                        <Box style={{ minWidth: 0 }}>
                          <Text fw={500} truncate>
                            {b.title}
                          </Text>
                          <Text size="xs" c="dimmed" truncate>
                            {b.slug}
                          </Text>
                        </Box>
                        <Switch checked={b.enabled} disabled={brandBusy === b.slug || !allowlistOnly} onChange={(e) => void toggleBrand(b, e.currentTarget.checked)} aria-label={`Enable ${b.title}`}/>
                      </Group>
                    </Paper>))}
                  {choose(Boolean(!allowlistOnly), (<Text size="xs" c="dimmed">
                      Switches apply when “Only selected” is on. Right now every paper is allowed.
                    </Text>), null)}
                </Stack>))}
            </Stack>
          </Paper>
        </Stack>
      </Box>
    </ScrollViewport>)));
}
