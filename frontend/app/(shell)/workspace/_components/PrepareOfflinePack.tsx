"use client";

import { useState } from "react";
import { Badge, Box, Group, Modal, Progress, Stack, Text, Tooltip } from "@mantine/core";
import { IconCloudDownload, IconCheck, IconAlertCircle } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { preparePack, removePack, type PrepareState } from "@/lib/offline/prepare";
import { useHasOfflinePack } from "@/lib/offline/mode";

/**
 * The "Prepare offline pack" affordance for a source row (ADR 0006).
 *
 * A small hover action icon (matching the Download/Delete cluster) that opens a
 * frosted modal with build progress, then confirms "Ready · N questions ·
 * expires in 7 days." When a pack is already downloaded, the icon toggles to a
 * remove action and a lavender badge shows the offline state.
 */
export function PrepareOfflinePackButton({ documentId }: { documentId: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<PrepareState | null>(null);
  const { pack, loading } = useHasOfflinePack(documentId);

  async function handlePrepare() {
    setState({ phase: "creating" });
    try {
      await preparePack(documentId, setState);
      notifications.show({
        title: "Ready offline",
        message: "Your questions are downloaded. Study anywhere, no signal needed.",
        color: "lavender",
        icon: <IconCheck size={18} />,
      });
      setOpen(false);
    } catch (err) {
      setState({ phase: "failed", error: err instanceof Error ? err.message : "Build failed" });
    }
  }

  async function handleRemove() {
    if (!pack) return;
    await removePack(pack.id);
    notifications.show({
      title: "Offline pack removed",
      message: "This source will study online again.",
      color: "gray",
    });
  }

  if (loading) {
    return (
      <span className="zivo-source-action" style={{ opacity: 0.4 }}>
        <IconCloudDownload size={15} stroke={1.7} />
      </span>
    );
  }

  if (pack) {
    // Already downloaded → remove affordance.
    return (
      <Tooltip label="Remove offline pack" position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label="Remove offline pack"
          className="zivo-source-action"
          data-offline="ready"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            void handleRemove();
          }}
        >
          <IconCloudDownload size={15} stroke={1.7} />
        </span>
      </Tooltip>
    );
  }

  return (
    <>
      <Tooltip label="Prepare offline pack" position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label="Prepare offline pack"
          className="zivo-source-action"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setState(null);
            setOpen(true);
          }}
        >
          <IconCloudDownload size={15} stroke={1.7} />
        </span>
      </Tooltip>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Prepare offline pack"
        size="sm"
        centered
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Download all questions for this source so you can study with no internet.
            Answer keys and feedback are bundled; grades sync automatically when
            you reconnect.
          </Text>
          {state?.phase === "building" && (
            <Box>
              <Text size="xs" c="dimmed" mb={6}>
                Building your questions… {Math.round(state.progress)}%
              </Text>
              <Progress value={state.progress} size="sm" radius="xl" color="lavender" />
            </Box>
          )}
          {state?.phase === "failed" && (
            <Group gap="xs">
              <IconAlertCircle size={16} color="var(--mantine-color-terracotta-6)" />
              <Text size="sm" c="terracotta">
                {state.error}
              </Text>
            </Group>
          )}
          {state?.phase === "ready" && (
            <Badge color="lavender" variant="light" size="lg" radius="xl">
              Ready · {state.questionCount} questions
            </Badge>
          )}
          {(!state || state.phase === "creating") && (
            <Group justify="flex-end">
              <PrepareButton onPrepare={handlePrepare} busy={state?.phase === "creating"} />
            </Group>
          )}
        </Stack>
      </Modal>
    </>
  );
}

function PrepareButton({ onPrepare, busy }: { onPrepare: () => void; busy: boolean }) {
  return (
    <Box
      role="button"
      tabIndex={0}
      onClick={onPrepare}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPrepare();
        }
      }}
      style={{
        cursor: busy ? "wait" : "pointer",
        fontWeight: 600,
        color: "var(--mantine-color-lavender-7)",
      }}
    >
      {busy ? "Preparing…" : "Prepare pack"}
    </Box>
  );
}
