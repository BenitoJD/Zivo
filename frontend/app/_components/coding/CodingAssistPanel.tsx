"use client";

/**
 * Coding assistant tab — assertion-scoped SSE chat (no PDF / document required).
 */

import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Group,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  UnstyledButton,
} from "@mantine/core";
import { IconEraser, IconSend } from "@tabler/icons-react";
import { apiGet, apiPost, apiPostSSE } from "@/lib/api/client";

type ChatMsg = { role: string; content: string };

export function CodingAssistPanel({
  assertionId,
  code,
  languageId,
  stdin,
  lastStatus,
}: {
  assertionId: string;
  code: string;
  languageId: number;
  stdin: string;
  lastStatus: string | null;
}) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const msgs = await apiGet<{ role: string; content: string }[]>(
          `/api/coding/${assertionId}/assist/messages`,
        );
        if (!alive) return;
        setMessages(
          (msgs || [])
            .filter((m) => (m.content || "").trim())
            .map((m) => ({ role: m.role, content: m.content })),
        );
      } catch {
        /* empty thread ok */
      }
    })();
    return () => {
      alive = false;
    };
  }, [assertionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    try {
      await apiPostSSE(`/api/coding/${assertionId}/assist`, {
        message: text,
        code,
        language_id: languageId,
        stdin,
        last_status: lastStatus,
      }, {
        onChunk: (chunk) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + chunk };
            }
            return next;
          });
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Assistant failed.";
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant" && !last.content.trim()) {
          next[next.length - 1] = { role: "assistant", content: msg };
        } else {
          next.push({ role: "assistant", content: msg });
        }
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  async function clearThread() {
    if (busy) return;
    try {
      await apiPost(`/api/coding/${assertionId}/assist/clear`, {});
      setMessages([]);
    } catch {
      /* ignore */
    }
  }

  return (
    <Stack gap="sm" h="100%" style={{ minHeight: 0 }}>
      <Group justify="space-between" px="md" pt="sm" wrap="nowrap">
        <Text fz="xs" c="dimmed">
          Ask about the statement, errors, or approach — hints first, not spoilers.
        </Text>
        <UnstyledButton onClick={() => void clearThread()} aria-label="Clear chat">
          <Group gap={4}>
            <IconEraser size={14} color="var(--mantine-color-dimmed)" />
            <Text fz="xs" c="dimmed">
              Clear
            </Text>
          </Group>
        </UnstyledButton>
      </Group>
      <ScrollArea flex={1} px="md" offsetScrollbars style={{ minHeight: 0 }}>
        <Stack gap="sm" pb="sm">
          {messages.length === 0 ? (
            <Text size="sm" c="dimmed">
              Stuck on constraints or a failing case? Ask here.
            </Text>
          ) : (
            messages.map((m, i) => (
              <Box key={`${i}-${m.role}`}>
                <Text size="xs" c="dimmed" tt="uppercase" lts={0.5} mb={2}>
                  {m.role === "user" ? "You" : "Assistant"}
                </Text>
                <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                  {m.content || (busy && i === messages.length - 1 ? "…" : "")}
                </Text>
              </Box>
            ))
          )}
          <div ref={bottomRef} />
        </Stack>
      </ScrollArea>
      <Box px="md" pb="md">
        <Textarea
          placeholder="Ask a question…"
          value={input}
          onChange={(e) => setInput(e.currentTarget.value)}
          minRows={2}
          maxRows={5}
          autosize
          radius="md"
          disabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <Group justify="flex-end" mt="xs">
          <Button
            size="xs"
            radius="xl"
            color="lavender"
            leftSection={<IconSend size={14} />}
            loading={busy}
            disabled={!input.trim()}
            onClick={() => void send()}
          >
            Send
          </Button>
        </Group>
      </Box>
    </Stack>
  );
}
