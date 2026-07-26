"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Anchor,
  HoverCard,
  Loader,
  Paper,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { IconBook2, IconBulb, IconMessage2, IconWorld } from "@tabler/icons-react";

import { apiGet } from "@/lib/api/client";

type Sel = { text: string; x: number; top: number; bottom: number };

type DictionaryResponse = {
  word: string;
  part_of_speech: string | null;
  definition: string;
  example: string | null;
};

/**
 * Wrap any text region (e.g. the MCQ stem + options) to get ChatGPT-style
 * "quote what you selected into the chat". Selecting text reveals a small
 * floating popover whose actions hand the selected passage to the tutor
 * composer so the learner can ask about exactly what they highlighted.
 *
 * Uses `display: contents` so it never perturbs the host layout; the popover is
 * `position: fixed`, measured from the live selection range.
 */
export function SelectionQuote({
  children,
  onAsk,
  onExplain,
  onWikipedia,
  disabled = false,
}: {
  children: ReactNode;
  /** Quote the passage into the composer (does not send). */
  onAsk: (text: string) => void;
  /** Send an "explain this" prompt to the tutor (does not prefill the composer). */
  onExplain?: (text: string) => void;
  /** Ask the tutor using Wikipedia as a source for the selected term. */
  onWikipedia?: (text: string) => void;
  disabled?: boolean;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [sel, setSel] = useState<Sel | null>(null);

  const showSelection = useCallback(() => {
    if (disabled) return;
    const s = window.getSelection();
    const text = s?.toString().trim().replace(/\s+/g, " ") ?? "";
    if (!text || text.length < 3) {
      setSel(null);
      return;
    }
    const root = rootRef.current;
    if (!root || !s || s.rangeCount === 0) return;
    // Only react to selections inside this region.
    if (!root.contains(s.anchorNode)) return;
    const rect = s.getRangeAt(0).getBoundingClientRect();
    setSel({ text, x: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom });
  }, [disabled]);

  const onMouseUp = useCallback(() => showSelection(), [showSelection]);
  // Let the browser settle the selection handles before measuring (mobile long-press).
  const onTouchEnd = useCallback(() => window.setTimeout(showSelection, 16), [showSelection]);

  useEffect(() => {
    const onDown = (e: MouseEvent | TouchEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest?.("[data-quote-popover]")) return; // keep it open when clicking the popover
      setSel(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown, { passive: true });
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, []);

  const act = (fn: () => void) => {
    fn();
    setSel(null);
    window.getSelection()?.removeAllRanges();
  };

  return (
    <div
      ref={rootRef}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
      style={{ display: "contents" }}
    >
      {children}
      {sel ? (
        (() => {
          const popH = 44;
          const above = sel.top - popH - 8;
          const below = sel.bottom + 8;
          const top = above >= 12 ? above : below;
          const buttonCount =
            1 + (onExplain ? 1 : 0) + 1 + (onWikipedia ? 1 : 0);
          const popW = buttonCount * 118;
          return (
            <Paper
              data-quote-popover
              withBorder
              radius="xl"
              shadow="md"
              p={4}
              style={{
                position: "fixed",
                left: Math.max(12, Math.min(sel.x - popW / 2, window.innerWidth - popW - 12)),
                top: Math.min(top, window.innerHeight - popH - 12),
                zIndex: 400,
                display: "flex",
                gap: 2,
                background: "var(--mantine-color-body)",
              }}
            >
              <QuoteBtn
                icon={<IconMessage2 size={16} />}
                label="Ask Zivo"
                onClick={() => act(() => onAsk(sel.text))}
              />
              {onExplain ? (
                <QuoteBtn
                  icon={<IconBulb size={16} />}
                  label="Explain"
                  onClick={() => act(() => onExplain(sel.text))}
                />
              ) : null}
              <DictionaryBtn key={sel.text} text={sel.text} />
              {onWikipedia ? (
                <QuoteBtn
                  icon={<IconWorld size={16} />}
                  label="Wikipedia"
                  onClick={() => act(() => onWikipedia(sel.text))}
                />
              ) : null}
            </Paper>
          );
        })()
      ) : null}
    </div>
  );
}

export function DictionaryBtn({ text }: { text: string }) {
  const [opened, setOpened] = useState(false);
  const [loading, setLoading] = useState(false);
  const [entry, setEntry] = useState<DictionaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (loading) return;
    setLoading(true);
    setError(null);
    void apiGet<DictionaryResponse>(
      `/api/reference/dictionary?word=${encodeURIComponent(text)}`,
    )
      .then((data) => {
        setEntry(data);
        setError(null);
      })
      .catch((exc: unknown) => {
        setEntry(null);
        setError(exc instanceof Error ? exc.message : "No definition found");
      })
      .finally(() => setLoading(false));
  }, [loading, text]);

  return (
    <HoverCard
      width={280}
      shadow="md"
      radius="lg"
      withArrow
      openDelay={200}
      closeDelay={120}
      position="top"
      opened={opened}
      onChange={(next) => {
        setOpened(next);
        if (next && !entry && !error && !loading) load();
      }}
    >
      <HoverCard.Target>
        <Tooltip label="Dictionary" withArrow openDelay={300}>
          <UnstyledButton
            aria-label="Dictionary"
            onMouseDown={(e) => e.preventDefault()}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 12px",
              borderRadius: "var(--mantine-radius-xl)",
              fontSize: "var(--mantine-font-size-sm)",
              fontWeight: 600,
              color: "var(--mantine-color-text)",
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.background = "var(--mantine-color-lavender-0)")
            }
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <IconBook2 size={16} />
            Dictionary
          </UnstyledButton>
        </Tooltip>
      </HoverCard.Target>
      <HoverCard.Dropdown p="sm">
        {loading ? (
          <Loader size="sm" color="lavender" />
        ) : entry ? (
          <Stack gap={4}>
            <Text fw={600} size="sm">
              {entry.word}
              {entry.part_of_speech ? (
                <Text component="span" c="dimmed" fw={500}>
                  {" "}
                  · {entry.part_of_speech}
                </Text>
              ) : null}
            </Text>
            <Text size="sm" lh={1.55}>{entry.definition}</Text>
            {entry.example ? (
              <Text size="xs" c="dimmed" fs="italic" lh={1.5}>
                e.g. {entry.example}
              </Text>
            ) : null}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">{error ?? "Hover for a definition"}</Text>
        )}
      </HoverCard.Dropdown>
    </HoverCard>
  );
}

function QuoteBtn({
  icon,
  label,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <Tooltip label={label} withArrow openDelay={300}>
      <UnstyledButton
        onClick={onClick}
        aria-label={label}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          borderRadius: "var(--mantine-radius-xl)",
          fontSize: "var(--mantine-font-size-sm)",
          fontWeight: 600,
          color: "var(--mantine-color-text)",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--mantine-color-lavender-0)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        {icon}
        {label}
      </UnstyledButton>
    </Tooltip>
  );
}
