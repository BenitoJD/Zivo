"use client";

import { useEffect, useRef, useState } from "react";
import { Box, Button, Group, Modal, Progress, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconHeadphones, IconLoader2 } from "@tabler/icons-react";
import { apiGet, apiPost } from "@/lib/api/client";

type AudiobookStatus = {
  state: "none" | "building" | "ready" | "failed" | "disabled";
  progress: number;
  voice?: string;
  chunks?: { index: number; url: string }[];
};

/**
 * "Listen" affordance for a source row (Audiobook Engine).
 *
 * Background-first: the first click fires the TTS build (an ETA job, off the
 * request path) and closes with a toast. The learner keeps studying while the
 * icon shows building/ready state. Reopen the modal anytime to check progress
 * or play the playlist.
 */
export function ListenAudiobookButton({ documentId }: { documentId: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<AudiobookStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  async function refreshStatus() {
    try {
      const s = await apiGet<AudiobookStatus>(`/api/audiobook/${documentId}/status`);
      setStatus(s);
      return s;
    } catch {
      return null;
    }
  }

  // Seed status on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await apiGet<AudiobookStatus>(`/api/audiobook/${documentId}/status`);
        if (!cancelled) setStatus(s);
      } catch {
        /* icon stays quiet until first open */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // Poll in the background while building, even when the modal is closed.
  useEffect(() => {
    if (status?.state !== "building") return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const s = await refreshStatus();
      if (!s || s.state === "ready" || s.state === "failed" || s.state === "disabled") {
        setBusy(false);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.state, documentId]);

  // Poll while the modal is open for snappier progress updates.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      for (let i = 0; i < 240; i += 1) {
        if (cancelled) return;
        const s = await refreshStatus();
        if (!s) break;
        if (s.state === "ready" || s.state === "failed" || s.state === "disabled") {
          setBusy(false);
          return;
        }
        await new Promise((r) => setTimeout(r, 2500));
      }
      setBusy(false);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, documentId]);

  async function handleStart() {
    if (startedRef.current) return;
    startedRef.current = true;
    setError(null);
    try {
      await apiPost(`/api/audiobook/${documentId}/build`, {});
      setStatus((s) =>
        s && s.state === "ready" ? s : { state: "building", progress: Math.max(s?.progress ?? 0, 1) },
      );
      setBusy(true);
      notifications.show({
        title: "Preparing audiobook",
        message:
          "Your narration is building in the background. Hover the headphones icon for progress, or reopen Listen when it is ready.",
        color: "lavender",
        icon: <IconLoader2 size={18} />,
      });
    } catch (e) {
      startedRef.current = false;
      setError(e instanceof Error ? e.message : "Could not start audio");
    }
  }

  async function handleOpen() {
    const s = await refreshStatus();
    if (!s || s.state === "none") {
      setBusy(true);
      void handleStart();
    } else if (s.state === "building") {
      setBusy(true);
    } else {
      setBusy(false);
    }
    setOpen(true);
  }

  const ready = status?.state === "ready" && (status.chunks?.length ?? 0) > 0;
  const building = status?.state === "building";
  const tooltipLabel = building
    ? `Preparing audiobook${status.progress > 0 ? ` · ${status.progress}%` : "…"}`
    : ready
      ? "Audiobook ready"
      : "Listen to this source";

  return (
    <>
      <Tooltip label={tooltipLabel} position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label={tooltipLabel}
          className="zivo-source-action"
          data-audio={building ? "building" : ready ? "ready" : undefined}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            void handleOpen();
          }}
        >
          <IconHeadphones size={15} stroke={1.7} />
        </span>
      </Tooltip>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Listen"
        size="md"
        centered
        overlayProps={{ backgroundOpacity: 0.45, blur: 8 }}
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Your source, narrated as an audiobook. Rendered locally with an
            open-source voice. No internet needed after generation.
          </Text>
          {busy && status?.state === "building" ? (
            <Box>
              <Text size="xs" c="dimmed" mb={6}>
                Building your narration… {status.progress}% — you can close this and keep studying.
              </Text>
              <Progress value={status.progress} size="sm" radius="xl" color="lavender" />
            </Box>
          ) : null}
          {busy && (!status || status.state === "none") ? (
            <Box>
              <Text size="xs" c="dimmed" mb={6}>
                Starting… this runs in the background — you can close this and keep studying.
              </Text>
              <Progress value={10} size="sm" radius="xl" color="lavender" />
            </Box>
          ) : null}
          {error ? (
            <Text size="sm" c="terracotta">
              {error}
            </Text>
          ) : null}
          {status?.state === "failed" ? (
            <Text size="sm" c="terracotta">
              Could not render this source to audio. Try again in a moment.
            </Text>
          ) : null}
          {status?.state === "disabled" ? (
            <Text size="sm" c="dimmed">
              Audio is not enabled yet.
            </Text>
          ) : null}
          {ready && status.chunks ? (
            <Stack gap="sm">
              {status.chunks.map((c) => (
                <Group key={c.index} gap="sm" wrap="nowrap">
                  <Text size="xs" c="dimmed" fw={600} ff="monospace" w={28}>
                    {String(c.index + 1).padStart(2, "0")}
                  </Text>
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <audio controls preload="none" src={c.url} style={{ width: "100%" }} />
                  </Box>
                </Group>
              ))}
            </Stack>
          ) : null}
          {ready && status.voice ? (
            <Text size="xs" c="dimmed" fs="italic">
              Voice: {status.voice}
            </Text>
          ) : null}
          {ready ? (
            <Group justify="flex-end">
              <Button
                variant="light"
                color="lavender"
                radius="xl"
                size="compact-sm"
                onClick={() => setOpen(false)}
              >
                Done
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Modal>
    </>
  );
}
