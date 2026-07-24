"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Box,
  Center,
  Group,
  Loader,
  Paper,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCpu } from "@tabler/icons-react";
import { apiGet, apiPatch, isApiAccessDenied } from "@/lib/api/client";
import { useSessionQuery } from "@/lib/api/queries";

type AdminModel = {
  id: string;
  display_name: string;
  litellm_model: string;
  provider_name: string;
  provider_slug: string;
  is_enabled: boolean;
  is_default: boolean;
  has_api_key: boolean;
  provider_enabled: boolean;
};

type AdminModelList = {
  models: AdminModel[];
  pool_enabled: boolean;
};

export default function WorkspaceModelsPage() {
  const queryClient = useQueryClient();
  const session = useSessionQuery();
  const isAdmin = Boolean(session.data?.is_admin);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [poolBusy, setPoolBusy] = useState(false);

  const modelsQuery = useQuery({
    queryKey: ["models", "admin"],
    queryFn: () => apiGet<AdminModelList>("/api/models/admin"),
    enabled: isAdmin,
    retry: false,
  });

  const models = modelsQuery.data?.models ?? [];
  const poolEnabled = modelsQuery.data?.pool_enabled ?? false;
  const loading = session.isLoading || modelsQuery.isLoading;
  const forbidden =
    !isAdmin || (modelsQuery.isError && isApiAccessDenied(modelsQuery.error));

  useEffect(() => {
    if (!isAdmin || !modelsQuery.isError || forbidden) return;
    notifications.show({
      title: "Models",
      message: modelsQuery.error instanceof Error ? modelsQuery.error.message : "Unknown error",
      color: "terracotta",
    });
  }, [modelsQuery.isError, modelsQuery.error, forbidden, isAdmin]);

  async function toggleModel(model: AdminModel, enabled: boolean) {
    setBusyId(model.id);
    try {
      const updated = await apiPatch<AdminModel>(`/api/models/${model.id}`, { is_enabled: enabled });
      queryClient.setQueryData<AdminModelList>(["models", "admin"], (prev) =>
        prev
          ? { ...prev, models: prev.models.map((m) => (m.id === updated.id ? { ...m, ...updated } : m)) }
          : prev,
      );
    } catch (e) {
      notifications.show({
        title: model.display_name,
        message: e instanceof Error ? e.message : "Update failed",
        color: "terracotta",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function togglePool(enabled: boolean) {
    setPoolBusy(true);
    try {
      const data = await apiPatch<{ pool_enabled: boolean }>("/api/models/pool", { pool_enabled: enabled });
      queryClient.setQueryData<AdminModelList>(["models", "admin"], (prev) =>
        prev ? { ...prev, pool_enabled: data.pool_enabled } : prev,
      );
    } catch (e) {
      notifications.show({
        title: "Model pool",
        message: e instanceof Error ? e.message : "Update failed",
        color: "terracotta",
      });
    } finally {
      setPoolBusy(false);
    }
  }

  if (loading) {
    return (
      <Center mih="50vh">
        <Loader />
      </Center>
    );
  }

  if (forbidden) {
    return (
      <Center mih="50vh" px="lg">
        <Stack align="center" gap="sm" maw={420} ta="center">
          <Text fw={600} size="lg" style={{ fontFamily: "var(--font-serif), Georgia, serif" }}>
            Admin only
          </Text>
          <Text size="sm" c="dimmed" ta="center" lh={1.6}>
            Sign in as an admin user to enable or disable chat models.
          </Text>
        </Stack>
      </Center>
    );
  }

  return (
    <Box p={{ base: "md", md: "lg" }} maw={720} mx="auto" w="100%">
      <Stack gap="lg">
        <Stack gap={4}>
          <Group gap="sm" align="center">
            <IconCpu size={22} stroke={1.5} />
            <Title
              order={3}
              style={{ letterSpacing: "-0.01em", fontFamily: "var(--font-serif), Georgia, serif", fontWeight: 500 }}
            >
              LLM models
            </Title>
          </Group>
          <Text size="sm" c="dimmed" lh={1.55}>
            Turn models on or off for chat and question generation. Disabled models are skipped by the router.
          </Text>
        </Stack>

        <Paper withBorder p="md" radius="lg">
          <Group justify="space-between" align="center" wrap="nowrap">
            <Stack gap={2}>
              <Text fw={600} size="sm">
                Round-robin pool
              </Text>
              <Text size="xs" c="dimmed">
                Rotate across all enabled models with API keys. Off = default model only.
              </Text>
            </Stack>
            <Switch
              checked={poolEnabled}
              disabled={poolBusy}
              onChange={(e) => void togglePool(e.currentTarget.checked)}
              aria-label="Enable model pool"
            />
          </Group>
        </Paper>

        <Stack gap="sm">
          {models.map((model) => {
            const ready = model.has_api_key && model.provider_enabled;
            return (
              <Paper key={model.id} withBorder p="md" radius="lg">
                <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
                  <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
                    <Group gap="xs" wrap="wrap">
                      <Text fw={600} size="sm">
                        {model.display_name}
                      </Text>
                      {model.is_default && (
                        <Badge size="sm" variant="light" color="lavender">
                          Default
                        </Badge>
                      )}
                      {!ready && (
                        <Badge size="sm" variant="light" color="gray">
                          No API key
                        </Badge>
                      )}
                    </Group>
                    <Text size="xs" c="dimmed" lineClamp={1}>
                      {model.provider_name} · {model.litellm_model}
                    </Text>
                  </Stack>
                  <Switch
                    checked={model.is_enabled}
                    disabled={busyId === model.id || !ready}
                    onChange={(e) => void toggleModel(model, e.currentTarget.checked)}
                    aria-label={`Enable ${model.display_name}`}
                  />
                </Group>
              </Paper>
            );
          })}
        </Stack>
      </Stack>
    </Box>
  );
}
