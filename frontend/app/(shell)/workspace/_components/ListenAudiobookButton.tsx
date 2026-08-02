"use client";

import { useEffect, useState } from "react";
import { Box, Button, Group, Modal, Progress, Stack, Text, Tooltip } from "@mantine/core";
import { IconHeadphones } from "@tabler/icons-react";
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
 * Opens a frosted modal, kicks off TTS rendering (idempotent), polls until the
 * manifest is ready, then presents a playlist of per-chunk <audio> players.
 */
export function ListenAudiobookButton({ documentId }: { documentId: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<AudiobookStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function poll() {
    try {
      const s = await apiGet<AudiobookStatus>(`/api/audiobook/${documentId}/status`);
      setStatus(s);
      if (s.state === "building" && !open) return;
      return s;
    } catch {
      return null;
    }
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      setError(null);
      setBusy(true);
      try {
        await apiPost(`/api/audiobook/${documentId}/build`, {});
        for (let i = 0; i < 120; i += 1) {
          if (cancelled) return;
          const s = await poll();
          if (!s) break;
          if (s.state === "ready" || s.state === "failed" || s.state === "disabled") {
            setBusy(false);
            return;
          }
          await new Promise((r) => setTimeout(r, 2500));
        }
        setBusy(false);
      } catch (e) {
        setBusy(false);
        setError(e instanceof Error ? e.message : "Could not start audio");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll is stable per open
  }, [open, documentId]);

  return (
    <>
      <Tooltip label="Listen to this source" position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label="Listen to this source"
          className="zivo-source-action"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen(true);
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
            open-source voice — no internet needed after generation.
          </Text>
          {busy && (
            <Box>
              <Text size="xs" c="dimmed" mb={6}>
                {status?.state === "building"
                  ? `Building your narration… ${status.progress}%`
                  : "Starting…"}
              </Text>
              <Progress value={status?.progress ?? 10} size="sm" radius="xl" color="lavender" />
            </Box>
          )}
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
          {status?.state === "ready" && status.chunks ? (
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
          {status?.state === "ready" && status.voice ? (
            <Text size="xs" c="dimmed" fs="italic">
              Voice: {status.voice}
            </Text>
          ) : null}
          {status?.state === "ready" ? (
            <Group justify="flex-end">
              <Button
                variant="light"
                color="lavender"
                radius="xl"
                size="compact-sm"
                onClick={() => {
                  setOpen(false);
                  setStatus(null);
                }}
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
