"use client";

import { useEffect, useState } from "react";
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
import { apiGet, apiPatch } from "@/lib/api/client";

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
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [models, setModels] = useState<AdminModel[]>([]);
  const [poolEnabled, setPoolEnabled] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [poolBusy, setPoolBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const data = await apiGet<AdminModelList>("/api/models/admin");
        if (cancelled) return;
        setModels(data.models);
        setPoolEnabled(data.pool_enabled);
        setForbidden(false);
      } catch (e) {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : "Could not load models";
        if (message.toLowerCase().includes("admin")) {
          setForbidden(true);
        } else {
          notifications.show({ title: "Models", message, color: "red" });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggleModel(model: AdminModel, enabled: boolean) {
    setBusyId(model.id);
    try {
      const updated = await apiPatch<AdminModel>(`/api/models/${model.id}`, { is_enabled: enabled });
      setModels((prev) => prev.map((m) => (m.id === updated.id ? { ...m, ...updated } : m)));
    } catch (e) {
      notifications.show({
        title: model.display_name,
        message: e instanceof Error ? e.message : "Update failed",
        color: "red",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function togglePool(enabled: boolean) {
    setPoolBusy(true);
    try {
      const data = await apiPatch<{ pool_enabled: boolean }>("/api/models/pool", { pool_enabled: enabled });
      setPoolEnabled(data.pool_enabled);
    } catch (e) {
      notifications.show({
        title: "Model pool",
        message: e instanceof Error ? e.message : "Update failed",
        color: "red",
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
        <Stack align="center" gap="sm" maw={420}>
          <Text fw={600}>Admin only</Text>
          <Text size="sm" c="dimmed" ta="center">
            Sign in as an admin user to enable or disable chat models.
          </Text>
        </Stack>
      </Center>
    );
  }

  return (
    <Box p="lg" maw={720} mx="auto" w="100%">
      <Stack gap="lg">
        <Stack gap={4}>
          <Group gap="sm">
            <IconCpu size={22} stroke={1.5} />
            <Title order={3} style={{ letterSpacing: "-0.03em" }}>
              LLM models
            </Title>
          </Group>
          <Text size="sm" c="dimmed">
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
                        <Badge size="sm" variant="light" color="blue">
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
