"use client";

/**
 * Coding assistant tab — assertion-scoped SSE chat (no PDF / document required).
 * Chrome is TutorPanel (same as Learn / workspace), not a one-off layout.
 */

import { useEffect, useState } from "react";
import { TutorPanel } from "@/app/workspace/_components/TutorPanel";
import { apiGet, apiPost, apiPostSSE } from "@/lib/api/client";

type ChatMsg = { role: string; content: string };

const CODING_SUGGESTIONS = [
  "What's wrong with my approach?",
  "Hint for the failing case — no full solution",
  "Explain the constraints in plain language",
];

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
    <TutorPanel
      messages={messages}
      input={input}
      busy={busy}
      onInputChange={setInput}
      onSend={() => void send()}
      onClear={() => void clearThread()}
      suggestions={CODING_SUGGESTIONS}
      emptyHint="Ask about the statement, errors, or approach — hints first, not spoilers."
      disclaimer="Hints first — Zivo can make mistakes. Verify against the problem and your tests."
    />
  );
}
