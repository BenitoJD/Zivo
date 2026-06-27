"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Paper,
  Stack,
  Text,
  Textarea,
  Title,
  Tooltip,
  useMantineColorScheme,
} from "@mantine/core";
import { useHover } from "@mantine/hooks";
import {
  IconArrowDown,
  IconArrowUp,
  IconNotebook,
  IconPencil,
  IconPlayerStop,
  IconRefresh,
} from "@tabler/icons-react";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { BrandMark } from "@/app/_components/BrandMark";
import { AssistantMarkdown, MessageCopyAction } from "@/lib/chatMarkdown";

/**
 * Tutor chat panel + its message UI (extracted from the workspace page monolith).
 * The branded assistant chat used across Learn/Test/Read surfaces: streaming
 * messages, thinking heart, per-message actions (copy / edit / save-note /
 * regenerate), and the composer. Stateless — the page owns messages + handlers.
 */
function AssistantLogo({ size }: { size: number }) {
  return <BrandMark showWord={false} height={size} />;
}

export function TutorPanel({
  messages,
  input,
  busy,
  contextReady = true,
  onInputChange,
  onSend,
  onStop,
  onRegenerate,
  onEditUser,
  onSaveNote,
  suggestions = CHAT_SUGGESTIONS,
  emptyHint = "Questions about this page, the source, or how to think through the answer.",
  showHeader = false,
}: {
  messages: { role: string; content: string }[];
  input: string;
  busy: boolean;
  contextReady?: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
  onRegenerate?: () => void;
  onEditUser?: (index: number) => void;
  onSaveNote?: (content: string) => void;
  suggestions?: string[];
  emptyHint?: string;
  /** Show a branded top bar — for surfaces (Read, mobile) that have no rail header. */
  showHeader?: boolean;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showJumpLatest, setShowJumpLatest] = useState(false);
  const canSend = Boolean(input.trim()) && !busy && contextReady;

  const scrollToBottom = useCallback((force = false) => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (!force && !nearBottom) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
      setShowJumpLatest(false);
    });
  }, []);

  const prevBusyRef = useRef(false);
  useEffect(() => {
    if (messages.length === 0) return;
    // When a send starts (busy goes false→true) force the view down so the user
    // immediately sees their question + the thinking indicator; during streaming
    // just follow if they're already near the bottom.
    const justStarted = busy && !prevBusyRef.current;
    prevBusyRef.current = busy;
    scrollToBottom(justStarted);
  }, [messages, busy, scrollToBottom]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setShowJumpLatest(!nearBottom && messages.length > 0);
  }

  function submitMessage() {
    if (!canSend) return;
    onSend();
  }

  return (
    <Stack gap={0} h="100%" mih={0} bg={isDark ? "dark.8" : "white"}>
      <style>{`
        @keyframes chat-msg-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
        .chat-msg { animation: chat-msg-in 320ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .chat-msg { animation: none; } }
      `}</style>
      {showHeader ? (
        <Group
          gap={8}
          wrap="nowrap"
          align="center"
          px="md"
          py="xs"
          style={{ flexShrink: 0, borderBottom: "1px solid var(--app-border, var(--mantine-color-default-border))", background: isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-body)" }}
        >
          <BrandMark showWord={false} height={18} />
          <Text fz="sm" fw={600} c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {ZIVO_ASSISTANT_NAME}
          </Text>
        </Group>
      ) : null}
      <Box flex={1} mih={0} pos="relative">
        <Box
          ref={scrollRef}
          h="100%"
          onScroll={handleScroll}
          style={{ overflow: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable" }}
        >
          {messages.length === 0 ? (
            <Center mih="100%" px="sm" py="md">
              <Stack gap="sm" align="center" maw={280} w="100%">
                <AssistantLogo size={44} />
                <Stack gap={4} align="center">
                  <Title order={5} fw={600} ta="center" style={{ letterSpacing: "-0.02em" }}>
                    Ask {ZIVO_ASSISTANT_NAME}
                  </Title>
                  <Text size="xs" c="dimmed" ta="center" lh={1.5}>
                    {emptyHint}
                  </Text>
                </Stack>
                <Stack gap={6} w="100%">
                  {suggestions.map((suggestion) => (
                    <Button
                      key={suggestion}
                      variant="light"
                      color="gray"
                      radius="xl"
                      size="compact-xs"
                      fullWidth
                      styles={{ label: { whiteSpace: "normal", lineHeight: 1.35, fontSize: 12 } }}
                      onClick={() => onInputChange(suggestion)}
                    >
                      {suggestion}
                    </Button>
                  ))}
                </Stack>
              </Stack>
            </Center>
          ) : (
            <Box
              mih="100%"
              style={{
                display: "flex",
                flexDirection: "column",
                justifyContent: "flex-end",
              }}
            >
              <Stack gap="md" py="sm" px="sm" pb="md">
                {messages.map((m, i) => (
                  <ChatMessage
                    key={i}
                    message={m}
                    isUser={m.role === "user"}
                    streaming={!busy ? false : m.role === "assistant" && i === messages.length - 1}
                    isDark={isDark}
                    canRegenerate={
                      !busy && m.role === "assistant" && i === messages.length - 1 && Boolean(onRegenerate)
                    }
                    onRegenerate={onRegenerate}
                    onEdit={onEditUser && !busy ? () => onEditUser(i) : undefined}
                    onSaveNote={
                      onSaveNote && m.role === "assistant" && Boolean(m.content.trim())
                        ? () => onSaveNote(m.content)
                        : undefined
                    }
                  />
                ))}
              </Stack>
            </Box>
          )}
        </Box>
        {showJumpLatest ? (
          <Tooltip label="Jump to latest" position="top" withArrow>
            <ActionIcon
              pos="absolute"
              bottom={12}
              right={12}
              size={36}
              radius="xl"
              variant="default"
              aria-label="Jump to latest"
              onClick={() => scrollToBottom(true)}
              bg={isDark ? "dark.6" : "gray.0"}
              style={{ zIndex: 5, boxShadow: "var(--mantine-shadow-paper)" }}
            >
              <IconArrowDown size={18} stroke={2.25} />
            </ActionIcon>
          </Tooltip>
        ) : null}
      </Box>

      <Box
        px="xs"
        py={6}
        style={{
          flexShrink: 0,
          borderTop: `1px solid var(--mantine-color-default-border)`,
          background: isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-white)",
          paddingBottom: "max(6px, env(safe-area-inset-bottom))",
        }}
      >
        <Paper
          withBorder
          radius="xl"
          py={2}
          px={6}
          shadow="none"
          component="form"
          onSubmit={(e) => {
            e.preventDefault();
            submitMessage();
          }}
          bg={isDark ? "dark.7" : "white"}
          styles={{
            root: {
              borderColor: isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-4)",
            },
          }}
        >
          <Group align="center" wrap="nowrap" gap={6}>
            {busy && onStop ? (
              <ActionIcon
                type="button"
                radius="xl"
                size={32}
                variant="light"
                color="terracotta"
                onClick={onStop}
                aria-label="Stop response"
              >
                <IconPlayerStop size={16} />
              </ActionIcon>
            ) : null}
            <Textarea
              flex={1}
              variant="unstyled"
              autosize
              minRows={1}
              maxRows={5}
              placeholder={
                contextReady ? `Message ${ZIVO_ASSISTANT_NAME}` : "Preparing chat context…"
              }
              value={input}
              onChange={(e) => onInputChange(e.currentTarget.value)}
              disabled={busy || !contextReady}
              styles={{
                input: {
                  paddingTop: 6,
                  paddingBottom: 6,
                  paddingLeft: 6,
                  paddingRight: 0,
                  fontSize: 14,
                  lineHeight: 1.4,
                  minHeight: 22,
                },
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submitMessage();
                }
              }}
            />
            <ActionIcon
              type="button"
              radius="xl"
              size={32}
              variant="filled"
              onClick={submitMessage}
              disabled={!canSend}
              aria-label={`Send message to ${ZIVO_ASSISTANT_NAME}`}
              styles={{
                root: {
                  flexShrink: 0,
                  border: "none",
                  transition: "background-color 160ms ease, transform 120ms ease",
                  background: canSend
                    ? isDark
                      ? "var(--mantine-color-white)"
                      : "var(--mantine-color-dark-9)"
                    : isDark
                      ? "var(--mantine-color-dark-5)"
                      : "var(--mantine-color-gray-3)",
                  color: canSend
                    ? isDark
                      ? "var(--mantine-color-dark-9)"
                      : "var(--mantine-color-white)"
                    : isDark
                      ? "var(--mantine-color-dark-2)"
                      : "var(--mantine-color-gray-5)",
                  "&:hover": canSend
                    ? {
                        background: isDark
                          ? "var(--mantine-color-gray-1)"
                          : "var(--mantine-color-dark-7)",
                      }
                    : undefined,
                  "&:active": canSend ? { transform: "scale(0.96)" } : undefined,
                },
              }}
            >
              <IconArrowUp size={17} stroke={2.5} />
            </ActionIcon>
          </Group>
        </Paper>
        <Text size="10px" c="dimmed" ta="center" mt={6} lh={1.3} opacity={0.85}>
          {ZIVO_ASSISTANT_NAME} can make mistakes. Check important details in your source.
        </Text>
      </Box>
    </Stack>
  );
}

const CHAT_SUGGESTIONS = [
  "Explain this question in simple terms",
  "What concept is being tested here?",
  "Give me a hint without the answer",
];

// Reading-oriented prompts for Read / Study-Buddy mode (no question on screen).
export const READ_CHAT_SUGGESTIONS = [
  "Summarize this page",
  "Explain the part I highlighted",
  "What are the key takeaways?",
  "Give me an example",
];

/**
 * The brand heart, alive while the assistant thinks — a gentle beat with a soft glow
 * ring breathing outward. Replaces the old "..." dots for a more premium wait state.
 */
function ThinkingHeart({ size = 28 }: { size?: number }) {
  return (
    <Box
      pos="relative"
      w={size}
      h={size}
      style={{ display: "grid", placeItems: "center", flexShrink: 0 }}
      aria-live="polite"
      aria-label="Thinking"
    >
      <style>{`
        @keyframes zivo-heart-beat {
          0%, 100% { transform: scale(1); }
          30% { transform: scale(1.16); }
          45% { transform: scale(1.02); }
          60% { transform: scale(1.1); }
        }
        @keyframes zivo-heart-ring {
          0% { transform: scale(0.7); opacity: 0.55; }
          100% { transform: scale(2.1); opacity: 0; }
        }
        .zivo-heart-beat { animation: zivo-heart-beat 1.5s cubic-bezier(0.4,0,0.2,1) infinite; transform-origin: center; }
        .zivo-heart-ring { animation: zivo-heart-ring 1.8s cubic-bezier(0.32,0.72,0,1) infinite; transform-origin: center; }
        @media (prefers-reduced-motion: reduce) {
          .zivo-heart-beat, .zivo-heart-ring { animation: none !important; }
          .zivo-heart-ring { opacity: 0 !important; }
        }
      `}</style>
      <Box
        className="zivo-heart-ring"
        pos="absolute"
        style={{
          inset: 0,
          borderRadius: "50%",
          background: "radial-gradient(circle, var(--mantine-color-lavender-4) 0%, transparent 68%)",
        }}
      />
      <Box className="zivo-heart-beat" style={{ position: "relative", lineHeight: 0 }}>
        <BrandMark showWord={false} height={size} />
      </Box>
    </Box>
  );
}

function MessageActionRail({
  visible,
  enabled,
  children,
  align = "flex-start",
}: {
  visible: boolean;
  enabled: boolean;
  children: ReactNode;
  align?: "flex-start" | "flex-end";
}) {
  return (
    <Group
      gap={4}
      mt={align === "flex-end" ? 4 : 6}
      justify={align}
      h={enabled ? 28 : 0}
      style={{
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? "auto" : "none",
        overflow: "hidden",
        transition: "opacity 150ms ease",
      }}
    >
      {enabled ? children : null}
    </Group>
  );
}
function ChatIconAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip label={label} position="top" withArrow openDelay={250}>
      <ActionIcon variant="subtle" color="gray" size="sm" radius="md" aria-label={label} onClick={onClick}>
        {children}
      </ActionIcon>
    </Tooltip>
  );
}

function ChatMessage({
  message,
  isUser,
  streaming,
  thinking = false,
  isDark,
  canRegenerate = false,
  onRegenerate,
  onEdit,
  onSaveNote,
}: {
  message: { role: string; content: string };
  isUser: boolean;
  streaming: boolean;
  thinking?: boolean;
  isDark: boolean;
  canRegenerate?: boolean;
  onRegenerate?: () => void;
  onEdit?: () => void;
  onSaveNote?: () => void;
}) {
  const { hovered, ref } = useHover();
  const actionsEnabled =
    Boolean(message.content.trim()) && !streaming && !thinking;
  // The last assistant reply keeps its actions visible (ChatGPT-style); others
  // reveal on hover.
  const showActions = (hovered || canRegenerate) && actionsEnabled;

  if (isUser) {
    return (
      <Box
        ref={ref}
        className="chat-msg"
        style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}
      >
        <Paper
          px="md"
          py="sm"
          radius="lg"
          maw="88%"
          bg={isDark ? "dark.5" : "gray.1"}
        >
          <Text size="sm" lh={1.65} style={{ whiteSpace: "pre-wrap" }}>
            {message.content}
          </Text>
        </Paper>
        <MessageActionRail visible={showActions} enabled={actionsEnabled} align="flex-end">
          {onEdit && (
            <ChatIconAction label="Edit & resend" onClick={onEdit}>
              <IconPencil size={15} stroke={1.8} />
            </ChatIconAction>
          )}
          <MessageCopyAction value={message.content} label="Copy message" />
        </MessageActionRail>
      </Box>
    );
  }

  const isThinking = thinking || (streaming && !message.content);
  return (
    <Group ref={ref} className="chat-msg" align="flex-start" gap="sm" wrap="nowrap" maw="100%">
      {isThinking ? <ThinkingHeart size={28} /> : <AssistantLogo size={28} />}
      <Box pt={4} style={{ flex: 1, minWidth: 0 }}>
        {isThinking ? (
          <Text size="sm" c="dimmed" pt={5} style={{ fontStyle: "italic" }}>
            Thinking…
          </Text>
        ) : (
          <AssistantMarkdown
            content={message.content}
            isDark={isDark}
            streaming={streaming}
          />
        )}
        <MessageActionRail visible={showActions} enabled={actionsEnabled}>
          <MessageCopyAction value={message.content} label="Copy message" />
          {onSaveNote && (
            <ChatIconAction label="Save to notes" onClick={onSaveNote}>
              <IconNotebook size={15} stroke={1.8} />
            </ChatIconAction>
          )}
          {canRegenerate && onRegenerate && (
            <ChatIconAction label="Regenerate" onClick={onRegenerate}>
              <IconRefresh size={15} stroke={1.8} />
            </ChatIconAction>
          )}
        </MessageActionRail>
      </Box>
    </Group>
  );
}
